<!-- SPDX-License-Identifier: EUPL-1.2 -->
# Poller et Lambda « manifeste » : les deux fonctions manquantes de la chaîne (18 octobre 2026)

Suite de [lambda.md](lambda.md) (le worker `rw-lambda`, une échéance par message SQS) et mise en œuvre de l'orchestration de [../architecture-aws.md](../architecture-aws.md) (§3.1 : poller par sondage, manifeste reconstruit par une Lambda à concurrence réservée 1 ; §10 : variante de lancement à moins de 1 $/mois, 2 tirages par jour, état dans S3 ; le §10 y supprimait la file « manifeste », ici la Lambda manifeste est déclenchée par S3 via SQS comme au §3.1). **Rien n'est déployé, aucune ressource AWS n'a été créée ni appelée, aucun identifiant AWS réel n'a été lu.** Tout a été compilé et testé en local avec un faux S3, un faux SQS et une fausse source Météo-France ; la seule requête réseau réelle est la lecture publique (listage) du S3 d'OVH.

Étiquettes : **[M]** mesuré ici, **[T]** vérifié par un test automatique, **[N]** non testé / à vérifier au déploiement, **[C]** calculé, **[S]** supposé.

## 1. Ce qui existe

| Élément | Fichier |
|---|---|
| Crate partagé `rw-aws` : SigV4, client S3 (PUT, GET, HEAD, **LIST**, **DELETE**), client **SQS** (`SendMessageBatch`), listage public non signé, dates, trait `Stockage` | `rust/crates/rw-aws/` |
| Crate `rw-poller` (bibliothèque + binaire `rw-poller`, déployé sous le nom `bootstrap`) | `rust/crates/rw-poller/` |
| Crate `rw-manifeste` (bibliothèque + binaire `rw-manifeste`, déployé sous le nom `bootstrap`) | `rust/crates/rw-manifeste/` |
| Faux S3 (listage, DELETE, SigV4 vérifiée) + faux SQS (JSON, SigV4 vérifiée) + fausse source OVH | `rust/tools/serveur_aws_local.py` |
| Vérification sur données réelles contre le générateur Python | `rust/tools/verifier_manifeste.sh` |
| Emballage des trois zips (sans envoi) | `rust/tools/package-lambda.sh` (étendu : `arm64 poller`, `manifeste`, `tout`) |

Le code Python du dépôt n'est pas modifié. `rw-lambda` n'a plus de client S3 ni de SigV4 en propre : `sigv4`, `s3`, `stockage`, `http` et `temps` ont été **déplacés** dans `rw-aws` et réexportés (`pub use rw_aws::{http, s3, sigv4, stockage, temps}`), donc `rw_lambda::s3::StockageS3` etc. existent toujours avec le même comportement. Les 19 tests d'origine passent toujours : 5 tests unitaires et 9 tests d'intégration dans `rw-lambda`, 5 tests unitaires (SigV4 ×2, dates, stockage ×2) maintenant dans `rw-aws`. Le worker pèse 2,97 Mo en arm64 (zip 1,58 Mo) contre 2,82 Mo (1,50 Mo) avant, soit +0,15 Mo, dû au code de listage et de suppression ajouté au client S3 partagé et aux changements parallèles de l'agent isobares **[M]**.

**Nom du binaire.** Les trois crates ont chacun un exécutable `bootstrap` dans le zip Lambda (obligatoire pour `provided.al2023`). Dans Cargo, le worker garde `bootstrap` (inchangé) mais le poller et le manifeste s'appellent `rw-poller` et `rw-manifeste` : trois cibles de même nom dans l'espace de travail s'écraseraient dans `target/` (et `CARGO_BIN_EXE_bootstrap` des tests désignerait un binaire au hasard). `package-lambda.sh` les range sous le nom `bootstrap`.

## 2. Vue d'ensemble

```text
EventBridge Scheduler (5 min)
        │ {}      (ou {"simulation": true, "maintenant": "…"} pour les essais)
        ▼
Lambda « poller » (rw-poller, concurrence réservée 1, 128 à 256 Mo, délai 60 s)
   ├─ ListObjectsV2 public sur OVH : pnt/<tirage>/arome/<res>/<SP1|SP2>/   (2 listages par modèle et tirage)
   ├─ ListObjectsV2 + GetObject sur le bucket de données : <v1>/<modèle>/<tirage>/_etat/   (états du worker)
   ├─ GetObject / PutObject : <v1>/_poller/<modèle>/<tirage>.json   (marqueur d'envoi)
   └─ SQS SendMessageBatch (≤ 10) : {"modele","tirage","echeance"}
        ▼
SQS « jobs » ──► rw-lambda (worker) ──► PMTiles, …, <v1>/<modèle>/<tirage>/_etat/NN.json (en dernier)
                                                     │ ObjectCreated (préfixes v1/arome/ et v1/arome-hd/, suffixe .json)
                                                     ▼
                                          SQS « manifeste » (fenêtre de lot 20 s)
                                                     ▼
                      Lambda « manifeste » (rw-manifeste, concurrence réservée 1, 512 Mo, délai 60 s)
                      liste les états, lit ceux qui ont changé, calcule le polygone, écrit <v1>/manifest.json EN DERNIER
```

Le poller est **à déclenchement par niveau** : à chaque passage il compare « publié chez Météo-France », « déjà produit » (états) et « déjà envoyé récemment » (marqueur). Un message perdu, une file purgée ou une panne ne perdent donc rien : le passage suivant renvoie ce qui manque. Le manifeste est reconstruit **entièrement** à chaque invocation, jamais mis à jour incrémentalement : l'ordre ou le nombre des notifications S3 n'a pas d'importance.

### 2.1 Clés S3 ajoutées

| Clé | Écrit par | Contenu | `Cache-Control` |
|---|---|---|---|
| `<v1>/manifest.json` | manifeste | manifeste (§5) | `public, max-age=30` (`RW_MANIFESTE_CACHE_S`) |
| `<v1>/_poller/<modèle>/<tirage>.json` | poller | `{"schema":1,"envoyes":{"07":<s Unix>},"tailles":{"<fichier GRIB>":<octets>},"termine":true}` | `no-cache` |

Le manifeste est à la **racine du préfixe**, en dehors de `v1/arome/` et `v1/arome-hd/` : la notification S3 sur les états ne le voit donc pas (pas de boucle). Les clés `_poller/` commencent par `_` comme les autres dossiers internes : **ne pas les exposer par CloudFront** (comportement dédié ou politique de chemin), comme pour `_etat`, `_domaine`, `_isobares`.

## 3. Poller

### 3.1 Algorithme d'un passage

1. **Tirages candidats** (par modèle) : les heures de `RW_TIRAGES_HEURES` (défaut `00,12` = lancement à 2 tirages par jour), du plus récent au plus ancien, ni plus jeunes que `RW_DELAI_MIN_S` (1,5 h : le premier fichier apparaît à +1,8 h chez OVH, aucun listage avant), ni plus vieux que `min(RW_AGE_MAX_H, RW_RETENTION_OVH_H)` (36 h et 336 h = 14 jours, la rétention d'OVH étant d'environ 15 jours **[M]**), au plus `RW_TIRAGES_MAX` (défaut 2). `RW_AGE_MAX_H` doit rester **inférieur à la durée de vie des objets de nos données** (cycle de vie 2 jours) : sinon, un tirage dont les états ont expiré mais qui existe encore chez OVH serait retraité en boucle. [T]
2. Marqueur du tirage lu ; si `termine`, **une seule requête** (cette lecture) et le tirage est laissé de côté. [T]
3. **Listage de la source** : `GET https://meteofrance-pnt.s3.rbx.io.cloud.ovh.net/?list-type=2&prefix=pnt/<tirage>/arome/<001|0025>/<SP1|SP2>/` (pagination suivie), sans signature, avec le même User-Agent identifiable que le worker. Deux listages par (modèle, tirage). [T pagination ; M sur la vraie source, §7]
4. **États produits** : listage de `<v1>/<modèle>/<tirage>/_etat/`. Un état de moins de 1 024 octets (seuil `etat_petit_octets` de la configuration, non exposé en variable d'environnement) est relu pour distinguer `complete` d'`echec_definitif` ; un état plus gros est un état complet : les états réels du worker pèsent 2,0 à 3,3 Ko (échéance 0 : 2,1 à 2,2 Ko ; les autres : 3,2 à 3,3 Ko), un état d'échec ≈ 250 octets **[M]**.
5. **Décision par échéance** `n` de 0 à 51 :
   - état complet → *faite* ;
   - état `echec_definitif` écrit depuis moins de `RW_RETRY_ECHEC_S` (6 h) → *en échec*, non renvoyée (le worker a acquitté une erreur définitive ; elle est retentée après le délai) ;
   - sinon, fichiers GRIB requis (mêmes noms que `rw_cli::canon::noms_requis` : SP1 et SP2 de l'étiquette de `n`, et de celle de `n−1` dès `n ≥ 2` ; un test compare les deux pour les 52 échéances des deux modèles) : tous prêts → à envoyer ; un absent → *incomplète* ; tous présents mais pas tous prêts → *instable*.
   - Un fichier est **prêt** si sa date de modification chez OVH a plus de `RW_AGE_FICHIER_MIN_S` (120 s) **et** que sa taille est égale à celle notée au passage précédent (dans le marqueur), ou que sa date dépasse `RW_AGE_FICHIER_SUR_S` (600 s) (critère « taille stable à deux passages et âge > 2 min » de l'architecture §3.1, rendu possible dès le premier passage pour les fichiers anciens). [T]
6. **Déduplication par fenêtre** : une échéance prête déjà envoyée depuis moins de `RW_FENETRE_S` (900 s, comme le `queued_at` de l'architecture) n'est pas renvoyée. Au-delà, elle est renvoyée tant qu'aucun état n'existe (message perdu ou worker en échec transitoire). Le worker est de toute façon idempotent (état `complete` et fichiers présents : `deja_fait`, sans téléchargement). Pour une file **FIFO** (URL en `.fifo`), le message porte `MessageGroupId = <modèle>-<tirage>` et `MessageDeduplicationId = <modèle>-<tirage>-<NN>-<instant/fenêtre>`. [T pour la forme ; N contre un vrai SQS FIFO]
7. **Envoi** par lots de 10 (`SendMessageBatch`), au plus `RW_ENVOI_MAX` (300) messages par passage, du tirage le plus récent au plus ancien. Un refus d'entrée (`Failed`) est journalisé en `ERROR` et l'échéance n'est pas marquée envoyée (renvoyée au passage suivant). Une erreur HTTP SQS (après `essais` tentatives) interrompt l'envoi de ce tirage et le passage se termine en erreur (`Errors` de Lambda), après avoir écrit le marqueur des échéances réellement envoyées. [T]
8. **Marqueur** réécrit seulement s'il a changé (5 $ le million de PUT) ; `termine` posé quand les 52 états sont complets. En simulation, ni envoi ni écriture. [T]

Message envoyé (contrat de `rw-lambda`) : `{"modele":"arome-hd","tirage":"2026-10-17T12:00:00Z","echeance":7}`.

### 3.2 Publication incomplète (R11, SP1 HD manquant)

Le cas du 17/10/2026 ([../donnees-arome.md](../donnees-arome.md) §3 : tirage HD de 06 h avec 19 échéances de SP1 seulement, 09 h sans aucune, SP2 complet) est géré par construction : une échéance n'est envoyée que si **SP1 et SP2** (et ceux de `n−1`) sont publiés. Pour ce tirage, le poller envoie 0 à 18, puis, quand le SP1 manquant apparaît, 19 à 51 (déclenchement par niveau). [T : scénario `publication_incomplete_sp1_hd_manquant`] Le bilan dit quel paquet manque (`paquet_absent: {"SP1": "19-51"}`, `fichiers: {"SP1": [19, 52], "SP2": [52, 52]}`) et un tirage encore incomplet **après `RW_ALERTE_INCOMPLET_H` (7 h)** émet une ligne `WARN` `"evenement":"publication_incomplete"` (à transformer en métrique d'alarme par un filtre de journal). Le client reste servi par le tirage précédent grâce au manifeste (D8) ; **aucune bascule de `latest_complete`** tant que le tirage n'est pas complet.

Le poller ne distingue pas un retard de publication d'un trou définitif : tant que le tirage est dans sa fenêtre d'âge (36 h) et pas `termine`, il interroge OVH à chaque passage (2 listages par modèle et par tirage, donc au plus 8 par passage avec 2 modèles et 2 tirages). Un tirage dont un paquet n'est jamais publié reste donc interrogé jusqu'à 36 h : coût négligeable, mais bruit dans les journaux (une ligne `WARN` par passage après 7 h).

### 3.3 Variables d'environnement

| Variable | Défaut | Rôle |
|---|---|---|
| `RW_BUCKET` | (obligatoire) | bucket de données (états, marqueurs) |
| `AWS_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` | fournies par Lambda | région et identifiants du rôle (jamais journalisés) |
| `RW_SQS_URL` | (obligatoire hors simulation) | URL de la file de travaux |
| `RW_PREFIXE` | `v1` | préfixe de clés (révision de pipeline : `v1r2`) |
| `RW_MODELES` | `arome,arome-hd` | modèles suivis |
| `RW_TIRAGES_HEURES` | `00,12` | heures de tirage (UTC) à traiter ; `00,03,06,09,12,15,18,21` pour 8 tirages par jour |
| `RW_TIRAGES_MAX` | 2 | plafond de tirages examinés par modèle et par passage (les plus récents) |
| `RW_AGE_MAX_H` | 36 | âge maximal d'un tirage traité (à garder sous la durée de vie des objets) |
| `RW_RETENTION_OVH_H` | 336 | rétention de la source (borne dure) |
| `RW_DELAI_MIN_S` | 5400 | pas de listage avant ce délai après l'heure nominale |
| `RW_FENETRE_S` | 900 | fenêtre de déduplication des envois |
| `RW_AGE_FICHIER_MIN_S`, `RW_AGE_FICHIER_SUR_S` | 120, 600 | âge minimal d'un fichier GRIB ; âge au-delà duquel la stabilité de taille n'est plus exigée |
| `RW_ENVOI_MAX` | 300 | plafond de messages par passage |
| `RW_RETRY_ECHEC_S` | 21600 | délai avant de retenter un état `echec_definitif` |
| `RW_ALERTE_INCOMPLET_H` | 7 | âge du tirage au-delà duquel une publication incomplète est signalée en `WARN` |
| `RW_SOURCE_LISTE`, `RW_SOURCE_PREFIXE` | `https://meteofrance-pnt.s3.rbx.io.cloud.ovh.net`, `pnt` | racine du bucket de la source et préfixe |
| `RW_SQS_ENDPOINT`, `RW_S3_ENDPOINT` | (absents) | points de terminaison explicites (tests) |
| `RW_SORTIE` | `s3` | `local:/chemin` : bucket de données sur disque (essais) |
| `RW_SIMULATION` | (absent) | `1` : liste et décide sans rien envoyer ni écrire (aussi par l'événement : `{"simulation": true}`) |
| `RW_PAUSE_MS` | 1000 | pause de base entre essais réseau |

L'événement d'entrée accepte aussi `{"maintenant": "2026-10-17T15:10:00Z"}` (horloge fixée, pour les essais).

### 3.4 Politique IAM du poller

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Sid": "EnvoyerLesTravaux", "Effect": "Allow", "Action": "sqs:SendMessage", "Resource": "arn:aws:sqs:REGION:COMPTE:FILE_JOBS" },
    { "Sid": "ListerLesEtats", "Effect": "Allow", "Action": "s3:ListBucket", "Resource": "arn:aws:s3:::BUCKET",
      "Condition": { "StringLike": { "s3:prefix": ["v1/arome/*", "v1/arome-hd/*"] } } },
    { "Sid": "LireLesEtats", "Effect": "Allow", "Action": "s3:GetObject",
      "Resource": ["arn:aws:s3:::BUCKET/v1/arome/*", "arn:aws:s3:::BUCKET/v1/arome-hd/*"] },
    { "Sid": "Marqueurs", "Effect": "Allow", "Action": ["s3:GetObject", "s3:PutObject"], "Resource": "arn:aws:s3:::BUCKET/v1/_poller/*" },
    { "Sid": "Journaux", "Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
      "Resource": "arn:aws:logs:REGION:COMPTE:log-group:/aws/lambda/FONCTION:*" }
  ]
}
```

`SendMessageBatch` est autorisé par `sqs:SendMessage`. La source OVH est publique : aucun droit AWS. Le marqueur est lu avant d'être écrit : `s3:ListBucket` rend la réponse « absent » en 404 et non en 403. Le marqueur est lu directement (jamais listé) : la condition `s3:prefix` ne porte que sur les listages d'états. **[N]** : la condition `s3:prefix` sur `ListObjectsV2`, et le fait que S3 réponde 404 plutôt que 403 à la lecture d'un marqueur absent quand `s3:ListBucket` est assorti d'une condition de préfixe, n'ont pas été exécutés contre un vrai S3 ; à défaut, retirer la condition. Pas de `s3:DeleteObject` (les marqueurs expirent avec la règle de cycle de vie).

### 3.5 Réglages recommandés

| Réglage | Valeur | Raison |
|---|---|---|
| Runtime / architecture | `provided.al2023`, arm64, zip de `package-lambda.sh arm64 poller` | |
| Mémoire / délai | 128 à 256 Mo, 60 s | pic mesuré 7,6 Mo de RSS ; 12 listages OVH en 0,33 à 0,37 s depuis un poste **[M]** |
| Concurrence réservée | **1** | le poller est le seul écrivain des marqueurs ; deux passages simultanés enverraient des doublons (sans gravité pour le worker, mais inutiles) |
| Scheduler | `rate(5 minutes)`, entrée `{}`, `RetryPolicy.MaximumRetryAttempts = 0`, pas de DLQ | un passage raté est repris 5 minutes plus tard ; une relance immédiate n'apporte rien |
| Alarmes | `Errors > 0` sur 15 min ; filtre de journal `"evenement":"publication_incomplete"` ; filtre `"niveau":"ERROR"` | |
| Coût [C] | 8 640 invocations par mois × 0,4 s × 0,125 Go ≈ 430 Go-s ; ≈ 7 000 listages S3 (≈ 0,04 $) et ≈ 35 000 lectures de marqueur (≈ 0,01 $) | les tirages `termine` ne coûtent qu'une lecture ; à comparer aux 400 000 Go-s du palier gratuit |

## 4. Client SQS minimal

`rw_aws::sqs` : `SendMessageBatch` en protocole JSON (`POST https://sqs.<région>.amazonaws.com/`, `X-Amz-Target: AmazonSQS.SendMessageBatch`, `Content-Type: application/x-amz-json-1.0`), signature SigV4 service `sqs` avec le code de `sigv4` (en-têtes signés : `content-type`, `host`, `x-amz-date`, `x-amz-target`, `x-amz-security-token`), nouvelles tentatives sur erreur réseau, 408, 429 et 5xx, ≤ 10 entrées et ≤ 262 144 octets par lot, identifiants d'entrée `eNN`. Réponse : `Successful` / `Failed`. Aucune dépendance de plus (la pile HTTP et crypto est celle du client S3). [T contre le faux SQS qui recalcule la signature indépendamment ; N contre le vrai service : le protocole JSON de SQS est documenté mais n'a pas été appelé, et la pile TLS en Rust pur n'a jamais parlé à un point de terminaison AWS.]

Le client S3 gagne `lister` (ListObjectsV2, `prefix`, `delimiter`, pagination par `continuation-token`, requête canonique triée et encodée) et `supprimer` (DeleteObject, 204 ou 404 = succès). Le trait `Stockage` reçoit ces deux méthodes (avec une implémentation par défaut qui échoue, donc sans casser d'implémentation tierce) ; `StockageLocal` les implémente aussi pour les essais sur disque.

## 5. Lambda manifeste

### 5.1 Déclenchement

S3 → SQS : deux configurations de notification `s3:ObjectCreated:*`, préfixes `v1/arome/` et `v1/arome-hd/`, suffixe `.json` (un événement par échéance, émis après l'écriture des PMTiles puisque l'état est écrit en dernier). Un seul préfixe et un seul suffixe sont permis par configuration, d'où deux configurations ; `v1/manifest.json` et `v1/_poller/` ne sont dans aucun préfixe, et le code ignore de toute façon toute clé qui n'est pas `<v1>/<modèle>/<tirage>/_etat/NN.json` (défense en profondeur contre une boucle). Mapping SQS → Lambda : `BatchSize` 100, `MaximumBatchingWindowInSeconds` 20, `FunctionResponseTypes: ["ReportBatchItemFailures"]`, concurrence réservée de la fonction **1**. Une invocation fait **une seule reconstruction**, quel que soit le nombre de messages ; les messages sans état d'échéance (notification de test S3, autres clés) sont acquittés sans aucune requête S3. En cas d'échec, tous les messages du lot sont rendus (`batchItemFailures`). Une invocation directe (sans enregistrement SQS) force une reconstruction : utile pour un Scheduler de secours ou une relance manuelle. [T]

### 5.2 Algorithme

1. Pour chaque modèle (`arome`, `arome-hd`) : listage des tirages (`<v1>/<modèle>/` avec délimiteur), âge calculé depuis l'heure nominale.
2. **Rétention du manifeste** : un tirage n'est cité que s'il a au plus `RW_AGE_REF_H` heures (défaut **42**) et fait partie des `RW_TIRAGES_MANIFESTE` (6) plus récents. La règle de cycle de vie du bucket expire chaque objet 2 jours après **sa propre** création ; les objets d'un tirage sont écrits de +2 à +6 h après l'heure nominale et expirent donc de +50 à +54 h (plus la latence asynchrone de l'expiration). Citer jusqu'à 42 h garde une marge d'au moins 8 h : le manifeste ne référence jamais un fichier susceptible d'avoir expiré, sans toucher aux objets (« suppression logique »). Un modèle sans tirage cité disparaît du manifeste, comme dans le Python. [T]
3. États : listage de `<v1>/<modèle>/<tirage>/_etat/` ; chaque état est lu **une fois** (cache en mémoire clé + ETag, conservé entre invocations d'un même environnement chaud, purgé des objets disparus) : à régime établi, une reconstruction ne coûte que des listages. États `echec_definitif`, de schéma inconnu, illisibles, ou dont le modèle ou le tirage ne correspond pas à la clé : ignorés et comptés (`WARN` pour les trois derniers). Un trou dû à un échec reste un trou dans `steps`. [T]
4. **Polygone du domaine** (§5.4).
5. `construire` (§5.3), puis comparaison avec le manifeste actuel **hors `generated`** : inchangé → aucun PUT, `ETag` stable pour les clients. Sinon `PutObject` de `<v1>/manifest.json` (`Content-Type: application/json`, `Cache-Control: public, max-age=30`) **en dernier** : S3 est fortement cohérent, donc quand le manifeste cite une échéance, son fichier est déjà lisible. Le PUT d'un objet est atomique : la bascule de `latest_complete` est un seul remplacement. [T : le manifeste est le dernier `PUT` du journal du faux S3]
6. **Purge physique, facultative** (`RW_PURGE_AGE_H`, absente par défaut : c'est le cycle de vie du bucket qui supprime) : après l'écriture du manifeste, suppression des objets des tirages plus vieux que ce seuil (qui doit dépasser `RW_AGE_REF_H`, sinon refus de démarrer), de leur marqueur `_poller/` et de leurs GeoJSON `<v1>/isobares/<modèle>/<tirage>/`, au plus `RW_PURGE_MAX` (2 000) objets par invocation. [T]

### 5.3 Schéma du manifeste

Identique à celui du générateur Python ([../poc-j3/manifest.md](../poc-j3/manifest.md) §1) : mêmes clés, **même ordre**, mêmes valeurs. Sur les données réelles (HD 03Z et AROME 06Z du 17/10/2026, échéances 0, 12, 24, 36, 48 produites par le worker), le manifeste de `rw-manifeste` est **identique** à celui de `python3 -m pipeline.manifest` sur les mêmes tuiles, polygone compris, hors `generated`, `runs[].traite` (date de fichier côté Python, date de l'état côté Rust), `domain.source` (texte) et le nouveau champ `latest_complete` **[M, `verifier_manifeste.sh`]**. Correspondance avec les états : voir [lambda.md](lambda.md) §7 (zooms, `min`, `max` et `quant_step` viennent de l'état de la plus petite échéance du tirage le plus récent qui contient la variable, comme la lecture du premier fichier PMTiles en Python).

**Ajout au schéma** (décision ouverte n° 7 de l'architecture) : `models.<modèle>.latest_complete`, identifiant (`2026-10-17T12Z`) du plus récent tirage dont **toutes** les variables du modèle ont **toutes** leurs échéances (`runs[].complete`), ou `null`. Il est calculé dans la même reconstruction que `runs`, donc cohérent avec elle ; il bascule atomiquement avec le PUT du manifeste. Aucun changement pour le client existant : `viewer/js/modele-auto.js` ne lit que `models.<m>.runs[].{run,id,path,variables.<v>.steps,complete}`, `domain.polygon`, `couches`, `variables`, `file_template`, `zooms` ; un champ ajouté est ignoré. Le choix du tirage reste celui de D8 (dernier tirage dont l'échéance existe), `latest_complete` n'est qu'une information supplémentaire (par exemple pour afficher « données complètes du tirage 12 h »). Le champ n'est pas encore lu par le viewer.

Tailles [C, non mesurées sur un tirage complet] : un tirage complet ajoute ≈ 1,8 Ko compact par modèle ; 3 tirages × 2 modèles ≈ 11 Ko, ≈ 20 Ko avec 6 tirages par modèle, ≈ 3 Ko compressé. Mesuré : 5,3 Ko (1,4 Ko gzip) pour 2 tirages partiels. Compact (sans indentation), contrairement au Python (`indent=1`).

### 5.4 Polygone du domaine

Le schéma l'exige (`domain.polygon`, utilisé par `choisirModele`). Il est calculé **depuis `_domaine/NN.npz`** (clé donnée par le champ `domaine.cle` de l'état de l'échéance 3 par défaut, `RW_ECHEANCE_DOMAINE` du worker) du tirage cité le plus récent qui en possède un. `rw_manifeste::domaine` porte `pipeline/manifest/domaine.py` (`polygone_depuis_masque`) : lecture du `.npz` (zip + `.npy` `<f4`/`<f8`, tableaux `lat`, `lon`, `t2m`, valide = non NaN), 2 880 rayons depuis le barycentre (somme à la manière de NumPy, par paires), point valide le plus éloigné par rayon, Douglas-Peucker 0,04°, arrondi décimal exact à 4 décimales. Le résultat est **identique** à celui du Python pour l'AROME (20 sommets, 0,05 s) et l'AROME HD (20 sommets, 0,13 s, grille 2 801 × 1 791) [T, comparaison valeur à valeur avec `out/manifest/manifest.json`, et sur le dossier produit par le worker dans `verifier_manifeste.sh`]. Résultat mis en cache par (clé, taille) pour la vie de l'environnement ; pic de mémoire mesuré 116 Mo pour l'AROME HD (`.npz` canonique complet, plus gros que le masque seul) **[M]**. Sans fichier `_domaine`, repli sur le rectangle de la grille avec la source `grille (rectangle, champs canoniques absents)` (comportement du Python). `domain.source` vaut `<modèle>/<tirage>/_domaine/03` (le Python écrit `canon/<modèle>/<tirage>/03`) ; le viewer n'utilise pas ce champ.

Écart connu : les sinus et cosinus viennent de la bibliothèque mathématique de Rust, ceux de NumPy peuvent différer d'un ulp. Aucun écart n'est observé sur les deux domaines réels, mais un domaine futur pourrait choisir une maille voisine sur un rayon : l'effet serait d'une maille sur un sommet, absorbé par la tolérance de vue du client (0,05 à 0,25°).

### 5.5 Variables d'environnement

| Variable | Défaut | Rôle |
|---|---|---|
| `RW_BUCKET`, `AWS_REGION`, identifiants | (obligatoires / fournies par Lambda) | bucket de données |
| `RW_PREFIXE` | `v1` | préfixe de clés ; le manifeste est `<préfixe>/manifest.json` |
| `RW_AGE_REF_H` | 42 | âge maximal d'un tirage cité (à garder sous la durée de vie des objets, §5.2) |
| `RW_TIRAGES_MANIFESTE` | 6 | nombre maximal de tirages cités par modèle |
| `RW_MANIFESTE_CACHE_S` | 30 | `max-age` du manifeste |
| `RW_PURGE_AGE_H` | (absente) | active la purge physique au-delà de cet âge (> `RW_AGE_REF_H`) |
| `RW_PURGE_MAX` | 2000 | objets supprimés au plus par invocation |
| `RW_S3_ENDPOINT`, `RW_SORTIE=local:/chemin`, `RW_PAUSE_MS` | | essais |

### 5.6 Politique IAM du manifeste

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Sid": "LireLaFile", "Effect": "Allow", "Action": ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"],
      "Resource": "arn:aws:sqs:REGION:COMPTE:FILE_MANIFESTE" },
    { "Sid": "Lister", "Effect": "Allow", "Action": "s3:ListBucket", "Resource": "arn:aws:s3:::BUCKET",
      "Condition": { "StringLike": { "s3:prefix": ["v1/arome/*", "v1/arome-hd/*", "v1/isobares/*"] } } },
    { "Sid": "LireEtatsDomaineEtManifeste", "Effect": "Allow", "Action": "s3:GetObject",
      "Resource": ["arn:aws:s3:::BUCKET/v1/arome/*", "arn:aws:s3:::BUCKET/v1/arome-hd/*", "arn:aws:s3:::BUCKET/v1/manifest.json"] },
    { "Sid": "EcrireLeManifeste", "Effect": "Allow", "Action": "s3:PutObject", "Resource": "arn:aws:s3:::BUCKET/v1/manifest.json" },
    { "Sid": "Journaux", "Effect": "Allow", "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
      "Resource": "arn:aws:logs:REGION:COMPTE:log-group:/aws/lambda/FONCTION:*" }
  ]
}
```

Pour la purge physique (désactivée par défaut), ajouter `s3:DeleteObject` sur `v1/arome/*`, `v1/arome-hd/*`, `v1/isobares/*` et `v1/_poller/*`. Le listage du dossier des tirages (`v1/arome/`) est fait avec le délimiteur `/` : la condition `s3:prefix` doit l'accepter (`v1/arome/` correspond à `v1/arome/*`) **[N]**. Sans le droit de suppression, le manifeste fonctionne.

La file « manifeste » doit autoriser l'envoi depuis le bucket : politique de file avec `aws:SourceArn` du bucket et `aws:SourceAccount`.

### 5.7 Réglages recommandés

| Réglage | Valeur | Raison |
|---|---|---|
| Mémoire / délai | 512 Mo, 60 s | pic ≈ 116 Mo pour le polygone HD ; un cache chaud ne relit presque rien ; à froid ≈ 250 lectures d'états (≈ 10 à 20 ms chacune [E]) |
| Concurrence réservée | **1** | jamais deux écritures simultanées du manifeste |
| Mapping SQS | `BatchSize` 100, fenêtre 20 s, `ReportBatchItemFailures` | regrouper les dizaines d'événements d'un tirage |
| File | délai de visibilité ≥ 6 × 60 s = 360 s ; DLQ avec `maxReceiveCount` ≥ 10 | avec une concurrence réservée de 1, des invocations limitées (*throttled*) renvoient les messages à la file et consomment des réceptions **[N]** |
| Alarmes | `Errors > 0` ; profondeur de la DLQ ≥ 1 ; âge du manifeste (métrique à dériver de `generated`) | |
| Cycle de vie du bucket | expiration à 2 jours sur les préfixes `v1/arome/`, `v1/arome-hd/`, `v1/_poller/` et `v1/isobares/` (une règle par préfixe), sans versionnage | `v1/manifest.json` doit rester **hors** des règles : il n'est réécrit qu'en cas de changement et un pipeline arrêté pendant 2 jours le ferait expirer (risque 1 du §8) |

## 6. Ce qui est testé

Commande : `cd rust && cargo test --release -p rw-aws -p rw-lambda -p rw-poller -p rw-manifeste` (serveur Python lancé et arrêté par PID par chaque test, jamais par nom).

| Crate | Tests | Contenu principal |
|---|---|---|
| `rw-aws` | 13 unitaires | SigV4 contre l'exemple officiel, encodage, dates (`depuis_iso`), XML de listage (entités, jetons, erreurs), requête canonique triée, corps et réponses SQS (standard et FIFO), limites de lot, stockage local (liste avec/sans délimiteur, préfixe partiel, suppression, clés dangereuses) |
| `rw-lambda` | 5 unitaires + 9 d'intégration (inchangés) | |
| `rw-poller` | 15 unitaires + 13 d'intégration | **unitaires** : noms et groupes 0,025°, accord avec `rw_cli::canon::noms_requis` pour 2 × 52 échéances, configuration, tirages candidats (heures, plafond, âge, rétention), taille stable, `n−1`, **SP1 HD manquant**, déduplication par fenêtre, états en échec, groupes AROME, marqueur. **Intégration** (faux OVH + faux S3 + faux SQS) : envoi nominal, déduplication sur 3 passages, états du worker, **publication incomplète puis complétée**, fichiers jeunes et taille changeante, **lots de 10 et refus partiel**, plafond et heures, **rétention**, **pagination**, groupes 0,025°, panne SQS 500 puis reprise, **signature fausse (403, sans nouvel essai)**, file FIFO, échec définitif retenté après délai, **simulation**, **tirage terminé** (une seule requête), binaire en mode `--evenement` (succès, configuration invalide, secret faux) |
| `rw-manifeste` | 10 unitaires + 6 d'intégration + 2 de comparaison au Python | **unitaires** : états, notifications S3 (clés, `%XX`, `+`, événement de test), configuration, polygone (somme NumPy, Douglas-Peucker, rectangle, masque vide). **Intégration** : agrégation, `latest_complete`, bascule au moment où un tirage se complète, **manifeste dernier PUT**, `Cache-Control`, absence de réécriture si inchangé, cache des états (aucune relecture), **rétention et purge** (manifeste écrit avant toute suppression, plafond de suppressions), **polygone depuis le `.npz` égal à celui du Python**, états en échec ou illisibles, bucket vide, **binaire** (événements de test et clés étrangères ignorés sans requête, une reconstruction pour un lot, tous les messages rendus en cas d'échec, invocation directe). **Comparaison** : polygones AROME et HD égaux à `out/manifest/manifest.json` |
| bout en bout | `verifier_manifeste.sh` | worker réel sur 10 échéances → manifeste Rust égal au manifeste Python [M] |

Commande de la vérification sur données réelles (≈ 33 s mesurées, GRIB de `out/rust/src`, `PYLIB_PMTILES` : dossier du paquet Python `pmtiles` ; la roue `pmtiles-3.8.1-py3-none-any.whl` du dépôt se décompresse avec `python3 -m zipfile -e`) :

```sh
PYLIB_PMTILES=<dossier> rust/tools/verifier_manifeste.sh      # depuis la racine du dépôt
```

## 7. Mesures

| Mesure | Valeur |
|---|---|
| `rw-poller` arm64 (`aarch64-unknown-linux-musl`, statique, strippé, LTO) | binaire **2,44 Mo** ; zip **1,28 Mo** (x86_64 musl : 3,00 Mo, zip 1,40 Mo) **[M]** |
| `rw-manifeste` arm64 | binaire **2,62 Mo** ; zip **1,40 Mo** (x86_64 musl : 3,23 Mo, zip 1,53 Mo) **[M]** |
| `rw-lambda` (worker) arm64 | binaire 2,97 Mo ; zip 1,58 Mo (avant : 2,82 et 1,50 Mo) **[M]** |
| Compilation arm64 | `-p` par fonction, sans compilateur C croisé (`rust-lld`), 23 s pour chacune à partir des dépendances compilées **[M]** |
| Source OVH **réelle**, simulation sans écriture, 3 tirages × 2 modèles × 2 paquets = 12 listages, tirages 06Z, 09Z, 12Z du 18/10/2026 (observé à 14:43 UTC) | 0,33 à 0,37 s, 7,6 Mo de RSS ; 06Z et 09Z complets (52 et 9 fichiers par paquet), 12Z (2,7 h) pas encore publié, comme prévu par le retard de 2,6 à 5,3 h **[M]** |
| Polygone du domaine | AROME 0,05 s, AROME HD 0,13 s (hors lecture réseau), 116 Mo de pic **[M]** |

Aucune exécution arm64 (pas d'émulateur) ni sur Lambda : seuls les binaires x86_64 glibc sont exercés par les tests. L'arm64 est compilé et lié, c'est tout.

## 8. Limites, risques et ce qui n'est pas testé

**Non testé [N]**

- Aucun appel à un vrai AWS : la pile TLS en Rust pur (`rustls-rustcrypto` 0.0.2-alpha) face aux points de terminaison S3 et SQS d'AWS (chaîne Amazon, suites de chiffrement), le protocole JSON de SQS réel (les noms de champs suivent la documentation ; le faux serveur est écrit par la même main que le client), le comportement `ListObjectsV2` réel (ordre, `KeyCount`, accès « path-style » `/bucket?list-type=2` sans barre finale), les conditions IAM `s3:prefix` sur le listage, la limitation des invocations à concurrence réservée 1 avec le mapping SQS, la livraison des notifications S3 (un événement par PUT d'état, y compris en cas de réécriture de l'état), l'éventuel doublon de notification.
- Les files FIFO (seulement la forme des messages) ; les files avec chiffrement géré.
- L'exécution sur arm64.
- La charge réelle : un tirage complet (104 états, 52 PMTiles × 8 variables × 2 modèles) n'a pas été traité de bout en bout avec le worker réel (seulement 10 échéances par `verifier_manifeste.sh`) ; les 52 × 2 états d'un tirage sont simulés par des états synthétiques.
- La publication d'OVH pendant un incident : les scénarios incomplets sont simulés, la source réelle n'a été que lue.
- Les tirages à 8 par jour (configuration `RW_TIRAGES_HEURES` testée par les tests unitaires, pas en charge).
- Le délai réel de visibilité et de reprise des messages par SQS (les tests unitaires de reprise sont faits par repassage du poller, pas par SQS).

**Risques et décisions ouvertes**

1. **Expiration du manifeste.** Une règle de cycle de vie générale à 2 jours supprimerait `v1/manifest.json` s'il n'a pas été réécrit pendant 2 jours (pipeline arrêté : il serait *vide* ou absent, au lieu de rester à une valeur périmée). À traiter avec des règles par préfixes ou une écriture périodique ; non traité ici. Un manifeste périmé mais présent est le comportement voulu en cas de panne : le client affiche le dernier tirage.
2. **Seuils d'âge à aligner** : `RW_AGE_MAX_H` du poller (36 h) < `RW_AGE_REF_H` du manifeste (42 h) < durée de vie des objets (≥ 50 h). Si la règle de cycle de vie change (par exemple 3 jours), les deux seuils peuvent être élargis ; ne jamais les inverser.
3. **Heuristique de taille de l'état** (1 024 octets) pour reconnaître un état `echec_definitif` sans le lire : à rendre exacte si le worker écrit un jour un état complet de moins de 1 Ko, ou en faisant écrire les échecs sous une autre clé.
4. **Citation partielle** : un tirage qui n'a que quelques échéances est cité tout de suite (D8, publication progressive) ; le client choisit tirage par échéance via `choisirTirage`. `latest_complete` n'a donc de sens que pour l'information.
5. **Pas de DynamoDB** : l'état est dans S3 (marqueurs et états), donc sans transaction. Deux passages simultanés du poller (si la concurrence réservée est oubliée) enverraient des doublons, rattrapés par l'idempotence du worker.
6. **Tirage terminé sans retour** : un tirage dont les 52 états sont complets n'est plus jamais réexaminé. Si un état est supprimé à la main pour retraiter une échéance, il faut aussi supprimer le marqueur (ou relancer le worker directement).
7. **Pas de métrique propre** (EMF) : seulement des lignes de journal JSON (`passage`, `envoi`, `publication_incomplete`, `manifeste`) ; l'« âge du dernier tirage complet » de l'architecture §7 reste à dériver de `latest_complete` et de `generated`.
8. **`sante.json`** (architecture §3, §4.3) n'est pas écrit.
9. **Isobares** : le worker écrit désormais des GeoJSON sous `<v1>/isobares/<modèle>/<tirage>/` (travail en cours d'un autre agent, hors du dossier du tirage) ; le manifeste ne les cite pas, seule la purge facultative les supprime. Le cycle de vie doit les couvrir.
10. **Charge sur OVH** : au plus 4 listages par passage et par tirage non terminé, sans téléchargement ; `MaximumConcurrency × RW_CONNEXIONS` du worker reste le vrai poste de courtoisie.
11. **Version alpha de `rustls-rustcrypto`** : risque porté de `rw-lambda`, étendu à SQS et au listage S3 ; repli documenté dans [lambda.md](lambda.md) §14.

## 9. Emballage

```sh
rust/tools/package-lambda.sh arm64 tout        # rw-lambda-arm64.zip, rw-poller-arm64.zip, rw-manifeste-arm64.zip dans rust/target/lambda/
rust/tools/package-lambda.sh arm64 poller      # un seul
rust/tools/package-lambda.sh x86_64 manifeste  # pour comparer
```

Chaque zip contient un unique `bootstrap` (0755), reproductible à l'octet (somme SHA-256 identique d'une exécution à l'autre, vérifiée pour le poller). Compiler toujours avec `-p` (le script le fait) : un `cargo build` à la racine de l'espace de travail active libwebp (C) par unification des fonctionnalités et casserait l'arm64. Rien n'est envoyé.

## 10. Reste à faire avant de déployer

1. Infrastructure : bucket (notifications S3 → file « manifeste », cycle de vie par préfixes), deux files SQS et leurs DLQ, trois fonctions, mappings, Scheduler, rôles de §3.4 et §5.6, journaux à 14 jours, comportements CloudFront excluant `_*`.
2. Essai sur un compte réel avec un bucket jetable : TLS contre S3 et SQS, listage, notifications, concurrence réservée 1, un tirage réel de bout en bout.
3. Décider du cycle de vie du manifeste (§8, risque 1) et des seuils d'âge.
4. Lire `latest_complete` dans le viewer si l'on veut l'afficher (hors de ce travail).
5. Métriques EMF et alarmes de [../architecture-aws.md](../architecture-aws.md) §7.
