<!-- SPDX-License-Identifier: EUPL-1.2 -->
# Architecture de production sur AWS (évaluation et recommandation)

Statut : **étude du 18 octobre 2026, rien n'est déployé.** Elle précise le jalon J5 et la décision D1 de [specifications.md](specifications.md) (§4, §8) à partir des mesures de [poc-j1/synthese.md](poc-j1/synthese.md), [poc-j1/tuilage.md](poc-j1/tuilage.md), [poc-j1/visualisation.md](poc-j1/visualisation.md) et [poc-j3/manifest.md](poc-j3/manifest.md). Elle est **indépendante du langage** (Rust ou Python).

Étiquettes de fiabilité utilisées partout :

- **[V]** vérifié ce jour dans la documentation AWS ou l'API de prix AWS (`pricing:GetProducts`, région `eu-north-1`, lecture seule) ;
- **[M]** mesuré sur les vraies données, repris des documents du projet ;
- **[C]** calculé à partir de [V] et [M] (hypothèses données) ;
- **[E]** estimé (ordre de grandeur, à mesurer) ;
- **[S]** supposé ou connaissance générale **non revérifiée** ce jour.

Prix en USD, région `eu-north-1` (Stockholm), sauf CloudFront (tarification par zone « Europe », indépendante de la région du bucket).

## 0. Résumé

1. **Le traitement par Lambda (une invocation par échéance et par modèle) est adapté.** Une invocation dure ≈ 17 à 30 s et utilise < 2 Go **[C]** ; les limites Lambda (15 min, 10 240 Mo, 10 Go de `/tmp`) sont très au-dessus **[V]**. Coût de calcul ≈ 8 à 18 $/mois **[C]**, du même ordre qu'une tâche Fargate (≈ 10 $/mois **[C]**), sans NAT si la Lambda reste hors VPC.
2. **Le nombre de requêtes S3 n'est pas un sujet de coût** : ≈ 6 600 PUT/jour ≈ **1 $/mois** **[C]**. Le vrai poste variable est le **nombre de requêtes CloudFront par session** (1,20 $ le million en Europe **[V]**), donc la politique de préchargement du client et la taille des tuiles, pas le découpage des fichiers. On garde D2 (un PMTiles par modèle, tirage, variable, échéance).
3. **L'affirmation « CloudFront permet depuis peu d'appeler directement des lambdas » est imprécise** (§4.2). Ce qui est nouveau (12/10/2026) : une origine **Lambda Web Function** derrière CloudFront avec OAC. Une origine **Function URL** avec OAC existe depuis plus longtemps. Dans les deux cas CloudFront envoie une requête HTTPS signée ; il n'« invoque » pas la Lambda, et cela **n'a aucun rôle dans le pipeline de traitement**. Utile seulement pour une future API (point, itinéraire).
4. **Orchestration recommandée** : EventBridge Scheduler → Lambda « poller » (listage du S3 de Météo-France) → file SQS → Lambda « worker » à concurrence plafonnée, état dans DynamoDB, manifeste reconstruit par une Lambda à concurrence réservée 1 déclenchée par des événements S3 via SQS. Step Functions n'apporte rien ici et coûterait cher si on y faisait le sondage par échéance (≈ 170 $/mois **[C]**).
5. **Coût total** : ≈ 14 à 25 $/mois de socle, plus la diffusion : ≈ 0 $ (100 utilisateurs actifs/jour), ≈ 7 $ (1 000), ≈ 350 $ en PAYG ou 200 $ en forfait (10 000) avec le client actuel (frise entière préchargée) ; ≈ 60 $ (10 000) avec un préchargement sobre **[C]**, hypothèses §5.

## 1. Données de dimensionnement

| Grandeur | Valeur | Source |
|---|---|---|
| Tirages par jour | 8 (00, 03 … 21 UTC) | [M] spécifications §3.1 |
| Échéances | 52 (0 à 51 h) par modèle, 2 modèles | [M] |
| Fichiers PMTiles par tirage | **412 par modèle, 824 au total** (8 variables × 52, moins l'échéance 0 de `gust10`, `precip_rate`, `snow_fraction`, `cloud_total`) | [M] 412 fichiers AROME comptés |
| Fichiers par jour / par mois | 6 592 / ≈ 198 000 | [C] |
| Poids d'un tirage (`reduit2`) | ≈ 0,99 Go (HD 0,545 + AROME 0,444) ; ≈ 1,3 Mo par fichier HD, ≈ 1,1 Mo par fichier AROME | [M] (HD en partie extrapolé) |
| Poids par jour | ≈ 7,9 Go | [C] |
| CPU par tirage | ≈ 41 min (2 455 s) ; HD par échéance : ingestion 8 s (0,89 Go) + tuilage 21 s (0,23 Go) ; AROME : ≈ 1 s + 16 s (≈ 1,1 Go de pic à l'ingestion) | [M]/[C] |
| GRIB à télécharger | ≈ 2,9 Go par tirage, ≈ 42 Mo par échéance HD (SP1 + SP2) ; source : stockage public OVH (`meteofrance-pnt.s3.rbx.io.cloud.ovh.net`), **hors AWS** | [M] donnees-arome.md |
| Retard de publication | dernier fichier 2,6 à 5,3 h après l'heure nominale, étalé sur ≈ 1 h ; un tirage peut être incomplet paquet par paquet (R11) | [M] |
| Ouverture de la carte (vue France, `reduit`) | bureau 1,08 Mo / 57 requêtes ; mobile 0,35 Mo / 15 requêtes | [M] visualisation §8.4 |
| Préchargement de toute la frise, une couche | bureau 1 568 à 1 722 requêtes, 9,6 à 29,4 Mo ; mobile 329 à 336 requêtes, 3,1 à 8,4 Mo | [M] visualisation §8.4 |

Écart à signaler : la spécification D9 retient **3 tirages** (≈ 3 Go) ; le propriétaire parle de **≈ 15 jours** de rétention (120 tirages ≈ 119 Go). Les deux sont chiffrés ci-dessous ; c'est une décision ouverte (§8).

## 2. Requêtes S3 : minimiser et maîtriser

### 2.1 Prix S3 `eu-north-1` [V]

| Poste | Prix |
|---|---|
| PUT, COPY, POST, LIST | 0,005 $ / 1 000 (5 $ le million) |
| GET et autres | 0,0004 $ / 1 000 (0,40 $ le million) |
| Stockage Standard | 0,023 $ / Go-mois (50 premiers To) |
| S3 Express One Zone : PUT / GET / stockage | 0,00113 $ / 1 000 ; 0,00003 $ / 1 000 ; 0,11 $ / Go-mois (+ 0,0032 $/Go à l'envoi, 0,0006 $/Go à la lecture) |
| Transfert S3 vers CloudFront | gratuit (origines AWS) [V] doc CloudFront « What is Amazon CloudFront ? » |
| Expiration par règle de cycle de vie | pas de ligne de prix dans la liste S3 ; facturation nulle de l'expiration [S] |

### 2.2 Trois variantes

| | A. Actuelle (D2) | B. Une archive par (modèle, tirage, échéance), variables multiplexées | C. A + archives « empilées dans le temps » reconstruites à la fin du tirage |
|---|---|---|---|
| Fichiers par tirage | 824 | 104 | 824 + 16 |
| PUT par jour | 6 592 | 832 | ≈ 6 600 + ≈ 1 000 (multipart) |
| Coût PUT par mois | **≈ 1,0 $** | ≈ 0,1 $ | ≈ 1,5 $ |
| Stockage, 3 tirages | 3,0 Go ≈ 0,07 $ | idem | ≈ 6 Go ≈ 0,14 $ |
| Stockage, 2 jours (16 tirages) | 16 Go ≈ 0,36 $ | idem | ≈ 0,7 $ |
| Stockage, 15 jours (120 tirages) | 119 Go ≈ **2,7 $** | idem | ≈ 238 Go ≈ 5,5 $ |
| Publication progressive (D8) | oui, un fichier = une échéance utilisable | oui, mais ajouter une variable à une échéance déjà publiée oblige à réécrire le fichier | oui pendant le tirage (A), puis consolidé |
| Cache CDN | fichiers immuables, ranges stables | l'ajout d'une variable change le fichier : perte de l'immuabilité | A reste immuable ; C écrit une seule fois |
| Requêtes de préchargement de frise | 330 (mobile) à 1 700 (bureau) par couche [M] | peu de différence (la requête par tuile domine) | ≈ 30 à 60 par couche [E] |
| Complexité | aucune (PMTiles standard) | adressage de tuiles non standard (variable codée dans l'identifiant de tuile), lecteur sur mesure | lecteur sur mesure (axe temps) |

**Constat [C]** : passer de 824 à 104 fichiers économise ≈ 0,9 $/mois de PUT. Ce n'est pas un levier. Le coût des requêtes est à la **lecture** : une requête CloudFront en HTTPS coûte 0,012 $ / 10 000 en Europe, soit **1,20 $ le million [V]**, trois fois plus que le GET S3 qui en découle (0,40 $ le million).

Une archive unique par (modèle, tirage, variable) avec toutes les échéances, **réécrite à chaque échéance publiée**, est à écarter : 52 réécritures d'un fichier qui grossit, plus d'immuabilité, caches invalidés. Elle n'est acceptable que construite **une fois à la fin du tirage** (variante C), et seulement si l'audience le justifie (voir §5, levier sur les requêtes).

### 2.3 Autres points sur les requêtes

- **Multipart** : à éviter. Les fichiers (≈ 1 à 12 Mo) tiennent en un `PutObject` (limite 5 Go **[S]**). Un envoi multipart coûte 1 requête d'initialisation + une par partie + 1 de finalisation ; avec le seuil par défaut de 8 Mo de la plupart des SDK, un fichier de 18 Mo (HD `contrat`) coûterait 5 requêtes au lieu de 1. **Régler le seuil multipart à 64 Mo ou plus** dans le SDK du pipeline.
- **LIST** : le générateur de manifeste liste chaque préfixe de tirage (1 requête / 1 000 objets). 2 à 120 listages par reconstruction ; < 0,5 $/mois même reconstruit toutes les 20 s en période active **[C]**. Pour limiter, ne lister que les tirages récents et garder les anciens dans l'état (§3).
- **Pas de S3 Express One Zone** : disponible à Stockholm (zones `eun1-az1`, `eun1-az2`, `eun1-az3`) **[V]**, PUT 4,4 fois moins cher et GET 13 fois moins cher, mais stockage ≈ 4,8 fois plus cher (0,11 contre 0,023 $/Go-mois), une seule zone de disponibilité, et je n'ai **pas trouvé** de documentation confirmant qu'un bucket « directory » soit utilisable comme origine CloudFront avec OAC **[S]**. Économie maximale ≈ 1 à 5 $/mois, perte potentielle supérieure sur le stockage de 15 jours (13 $ contre 2,7 $). Rejeté.
- **Origin Shield : non.** Requêtes Origin Shield 0,009 $ / 10 000 (0,90 $ le million) **[V]**, plus cher que le GET S3 qu'il épargnerait (0,40 $ le million) ; l'origine S3 n'a pas de limite à protéger. Les caches régionaux (REC) sont déjà inclus par défaut **[V]** (« tiered caching » des forfaits).
- **Taux de succès du cache [E]** : les fichiers d'un nouveau tirage sont des URL nouvelles toutes les 3 h, donc froides. Hypothèse : 50 % de succès (100 utilisateurs/jour), 80 % (1 000), 92 % (10 000). Requêtes S3 GET correspondantes : ≈ 0,8 / 3,2 / 12,8 millions par mois, soit **0,3 / 1,3 / 5,1 $** **[C]**. Les requêtes simultanées sur le même objet sont fusionnées par CloudFront [S].
- **Range et cache** : CloudFront met en cache la plage demandée ou l'objet entier, et peut demander une plage plus large à l'origine **[V]** (page « range GETs »). Un `Transfer-Encoding: chunked` à l'origine ferait renvoyer l'objet entier : sans objet avec S3. Garder le `Content-Type: application/octet-stream` des `.pmtiles` (non compressible par CloudFront, pour ne pas fausser les décalages d'octets) **[S]**.
- **Réduire le nombre de requêtes côté client** (levier réel, voir §5) : préchargement ±3 h de l'échéance courante plutôt que la frise entière (spécification §6.5) ; tuiles de 512 px au lieu de 256 px (≈ 3 à 4 fois moins de requêtes à pixels égaux, **non mesuré** [E]) ; ne charger l'échéance h+1 qu'après h (déjà noté dans visualisation §3).
- **Rétention et cycle de vie** : une règle S3 expire par **âge de l'objet en jours** (granularité 1 jour [S]) et par préfixe ou étiquette ; elle ne sait pas « garder les 3 derniers tirages ». Conséquence : retenir **N jours** (2 jours = 16 tirages, 0,36 $), et laisser le **manifeste** décider des 3 tirages affichés. Prévoir un préfixe stable par modèle (`arome/…`, `arome-hd/…`). Désactiver le versionnage (sinon l'expiration ne supprime pas définitivement). L'expiration est asynchrone (quelques heures de latence [S]).
- **Taille du manifeste** : avec 15 jours (120 tirages × 2 modèles × 8 variables × ≈ 52 échéances), le manifeste actuel (liste complète des échéances) pèse ≈ 300 Ko brut, ≈ 20 Ko compressé **[E]**. Le limiter aux tirages affichables (3 à 6) ; l'historique long, s'il existe, va dans un second fichier.

## 3. Orchestration

### 3.1 Schéma retenu

```
Météo-France (OVH S3 public, listage ouvert)
        ▲  ListObjectsV2 (HTTPS, hors AWS)
        │
EventBridge Scheduler (toutes les 2 à 5 min)
        │
        ▼
Lambda « poller »  ──lit/écrit──►  DynamoDB (état par modèle/tirage/échéance/paquet)
        │  un message par échéance prête (SP1+SP2 présents, taille stable)
        ▼
SQS « jobs » (standard) ──► DLQ (après 3 à 5 réceptions)
        │  mapping de source d'événements, concurrence maximale 10 à 20,
        │  taille de lot 1, ReportBatchItemFailures activé
        ▼
Lambda « worker » (arm64, 1 769 à 3 008 Mo, /tmp 2 Go, hors VPC)
   télécharge les GRIB → décode → dérive → quantifie → tuile → PutObject S3
        │  (clé déterministe ; écriture dans DynamoDB : échéance « done »)
        ▼
S3 « weather-data » ──ObjectCreated (préfixe v1/, suffixe .pmtiles)──► SQS « manifeste »
                                                                          │ fenêtre de lot 20 s
                                                                          ▼
                                             Lambda « manifeste » (concurrence réservée 1)
                                             liste S3 + en-têtes → manifest.json (PUT en dernier)
```

Pourquoi ce découpage :

- **Détection** : Météo-France n'offre pas de notification (S3 OVH tiers). Le seul moyen est le sondage de listage **[S]** (listage ouvert avec dates de modification : donnees-arome.md). Le poller est **à déclenchement par niveau** : à chaque passage il compare « présent chez Météo-France » et « fait dans DynamoDB » et met en file ce qui manque et n'est pas déjà en vol (champ `queued_at` périmé après 15 min). Un message perdu, une DLQ vidée ou un redémarrage ne perdent donc rien. EventBridge Scheduler : 14 millions d'invocations gratuites par mois **[V]** ; le poller coûte < 0,10 $/mois (≈ 9 000 invocations de 1 à 2 s, 256 Mo) **[C]**.
- **Fichier complet** : pour éviter les fichiers tronqués (R11), n'enfiler que si la taille est identique à deux passages successifs et l'âge de modification > 2 min **[S]** (à valider sur les fichiers réels). Le worker vérifie en plus le décodage eccodes ; en cas d'échec, le message revient en file.
- **Dépendance entre échéances** : le dé-cumul des précipitations (spécifications §3.3) a besoin de l'échéance n−1. Soit le worker retélécharge aussi n−1 (≈ +50 % de téléchargement sur SP2), soit on relit le champ canonique n−1 conservé sur S3 (plus de PUT/GET internes). **À trancher au prototype ; non couvert par les mesures actuelles.**
- **Concurrence** : 104 tâches par tirage (52 × 2), chacune ≈ 17 à 30 s. La fenêtre de publication étant déjà étalée sur ≈ 1 h, une concurrence maximale de **10 à 20** suffit (104 tâches ≈ 2,6 min au pire à 20) et limite la charge sur l'hébergeur OVH (R2). Réglage : `ScalingConfig.MaximumConcurrency` du mapping SQS (valeur minimale 2) **[V]**, avec concurrence réservée de la fonction supérieure à cette valeur **[V]** (blog AWS).
- **Délai de visibilité de la file** : au moins 6 fois le délai d'expiration de la fonction **[S]** (recommandation AWS pour SQS + Lambda) ; avec un délai de fonction de 120 s, 720 s.
- **Lot de 1** : un message est une échéance (≈ 30 s). La réponse partielle de lot (`ReportBatchItemFailures`) **[V]** est activée par précaution si on passe à des lots > 1.
- **Idempotence** : SQS standard livre au moins une fois **[S]**. Les clés S3 sont **déterministes** (`{modèle}/{tirage}/{variable}/{échéance}.pmtiles`) et les sorties reproductibles à l'octet ; une ré-exécution réécrit le même contenu. Le worker consulte DynamoDB pour sauter les échéances `done`. Écriture conditionnelle S3 (`If-None-Match`) non nécessaire [S].
- **Échec et relance** : après 3 à 5 réceptions, DLQ + alarme (§7) ; la relance est soit une redirection depuis la DLQ, soit simplement le prochain passage du poller (déclenchement par niveau, donc automatique). Alarme « aucun nouveau tirage depuis 6 h » (spécifications §8).
- **État : DynamoDB à la demande** : 0,67 $ le million d'écritures et 0,1345 $ le million de lectures **[V]** ; ≈ 100 000 écritures/mois ≈ 0,07 $ **[C]**. Variante sans nouveau service : objets marqueurs S3 (mais plus de LIST/PUT). **Décision ouverte.**
- **Manifeste** : le générateur existant (`pipeline/manifest/`, 0,5 s sur 3 tirages [M]) peut être appelé tel quel par la Lambda « manifeste ». **Concurrence réservée 1** pour qu'il n'y ait jamais deux écritures simultanées ; la fenêtre de lot SQS (20 s) regroupe les dizaines d'événements d'un tirage. Le manifeste est **le dernier PUT** : S3 garantit la lecture après écriture (cohérence forte [S]), donc quand le manifeste cite une échéance, son fichier est lisible.
- **Bascule atomique du « dernier tirage complet »** : S3 remplace un objet atomiquement. Ajouter au schéma un champ `latest_complete` (par modèle), calculé par la Lambda « manifeste » **seulement si** toutes les variables ont toutes leurs échéances ; le client lit tirage complet et tirage en cours dans un seul fichier. **Ajout au schéma à valider** (le manifeste actuel a seulement `runs[].complete`).
- **En-têtes** : voir §4.3.

### 3.2 Alternatives

| Option | Verdict | Chiffres |
|---|---|---|
| **Step Functions, Map distribué** | Écartée en v1. Les tâches arrivent progressivement (retard étalé sur ≈ 1 h), alors qu'une Map distribuée suppose une liste connue au départ. Faire attendre chaque échéance dans la machine d'état coûte cher. | 0,000025 $ par transition **[V]**. Une exécution par (modèle, tirage), 52 branches qui sondent toutes les 2 min pendant 3 h (≈ 270 transitions chacune) : 28 000 transitions/tirage ≈ **0,70 $/tirage ≈ 168 $/mois** [C]. Une seule boucle de supervision par tirage (Wait + Lambda + Choice) : ≈ 0,007 $/tirage, ≈ 1,7 $/mois [C], utile uniquement pour l'historique d'exécution, pas indispensable. |
| **AWS Fargate / Batch** | Bon repli, pas nécessaire. Sans limite de 15 min ni de /tmp, adapté à de gros pas (HP1 de 74 Mo par échéance, ARPEGE 0,1° du jalon J9) ; la détection et la publication par échéance restent à construire. Démarrage à froid de tâche plus long que Lambda [S]. Disponibilité de AWS Batch en `eu-north-1` : non vérifiée. | Fargate ARM `eu-north-1` : 0,0356 $/vCPU-h et 0,00392 $/Go-h **[V]** ; 4 vCPU + 8 Go ≈ 0,174 $/h ; ≈ 15 min par tirage (11 min mesurées sur 4 vCPU + démarrage) ≈ 0,043 $ ≈ **10,4 $/mois** [C]. Taille de tâche de la synthèse (4 vCPU, 8 Go, 20 Go). |
| **Lambda par échéance (retenue)** | Fraîcheur « dans les 15 min » naturelle (une échéance est publiée dès qu'elle est prête), mise à l'échelle et facturation à la seconde. | Voir §3.3. |
| Lambda Managed Instances | Hors sujet (facturation à l'instance). | Tarif présent à `eu-north-1` [V]. |

### 3.3 Coût de calcul Lambda [C]

Hypothèses : arm64, 1 769 Mo (≈ 1 vCPU **[V]**), durée **mesurée sur 20 cœurs x86, à ± 30 % sur Graviton** (synthèse « Non couvert ») : HD ≈ 29 s par échéance (8 s ingestion + 21 s tuilage), AROME ≈ 17 s, isobares 42 s par tirage, téléchargement non compté (≈ 1 à 2 s à 47 Mo/s mesuré en HD).

- GB-s par tirage : 52 × 29 × 1,727 + 52 × 17 × 1,727 + 42 × 1,727 ≈ **4 200 GB-s**.
- Prix arm64 `eu-north-1` : **0,0000133334 $/GB-s** (premiers 7,5 milliards) et 0,20 $ le million de requêtes **[V]**.
- Par tirage : 0,056 $ (0,073 $ avec +30 %). Par mois (240 tirages) : **13,5 $ (17,5 $)**. Le niveau gratuit Lambda (400 000 GB-s, 1 million de requêtes par mois, **[V]** page tarifs) l'abaisse à 8 à 12 $ s'il s'applique au compte **[S]**.
- Parallélisme interne : à 3 008 Mo (≈ 1,7 vCPU), le tuilage à 2 processus divise la durée sans changer les GB-s (le coût reste proportionnel au CPU consommé).
- Requêtes Lambda : ≈ 25 000 par mois pour les workers + ≈ 20 000 pour poller et manifeste : négligeables.
- **Quota des comptes neufs** : concurrence et mémoire réduites **[V]** (erreur `MemorySize … ≤ 3008` documentée). Le compte interrogé en lecture seule (`GetAccountSettings`, `eu-north-1`) affichait **400 exécutions simultanées** et 5 fonctions ; je n'ai pas vérifié que ce compte soit celui du profil `bike360`. Notre besoin (≤ 20 workers + poller + manifeste) tient largement.

## 4. Limites Lambda pour ce cas, et diffusion

### 4.1 Limites Lambda vérifiées [V] (page « Lambda quotas »)

| Limite | Valeur | Pour nous |
|---|---|---|
| Durée maximale | 900 s | 17 à 30 s : marge ×30 |
| Mémoire | 128 à 10 240 Mo (1 769 Mo = 1 vCPU) ; comptes neufs possiblement limités à 3 008 Mo | 1 769 à 3 008 Mo suffisent (pic 1,1 Go) |
| `/tmp` | 512 à 10 240 Mo, facturé 0,0000000323 $/Go-s au-delà du palier de base **[V]** prix | un GRIB HD ≈ 42 à 120 Mo : 1 à 2 Go suffisent |
| Paquet ZIP | 50 Mo zippé / 250 Mo décompressé | juste pour numpy + eccodes + Pillow (Python) ; plutôt **image conteneur** |
| Image conteneur | 10 Go non compressée | recommandée (eccodes est une bibliothèque C) ; stockage ECR ≈ 0,10 $/Go-mois **[V]** (≈ 0,15 $/mois) |
| Bande passante par environnement | 625 Mbit/s (≈ 78 Mo/s) | 40 Mo en ≈ 1 s (non mesuré depuis AWS) |
| Démarrage à froid | non documenté en valeur | **[E]** 1 à 3 s en Python avec eccodes en conteneur ; sans conséquence sur 30 s. Rust : plus court. À mesurer. |
| Concurrence | 1 000 par région (comptes neufs : réduit) ; montée de 1 000 environnements / 10 s par fonction | sans objet |
| Architecture | arm64 : prix 20 % inférieur à x86 (0,0000133334 contre 0,0000166667 $/GB-s) **[V]** | vérifier que eccodes, numpy, Pillow-WebP ont des binaires arm64 |

**Réseau et NAT** : les GRIB viennent d'OVH (hors AWS). Une Lambda **hors VPC** accède directement à Internet ; l'entrée de données depuis Internet est gratuite **[S]**. Une Lambda en VPC exigerait une NAT : 0,046 $/h (≈ 33,6 $/mois) + 0,046 $/Go **[V]** ; pour ≈ 2,9 Go × 240 = 696 Go/mois : ≈ **66 $/mois [C]**, soit plus que tout le calcul. **Ne pas mettre les workers en VPC** (aucune ressource privée à joindre). Le jeton AROME-PI (J8) sera lu dans Secrets Manager via l'API publique de ce service.

### 4.2 « CloudFront appelle directement des lambdas » : vérification

Trois fonctionnalités distinctes existent ; l'affirmation du propriétaire les confond :

| Fonction | Ce que c'est | Statut vérifié | Limites et coûts | Pertinence ici |
|---|---|---|---|---|
| **Origine Lambda Function URL + OAC** | CloudFront envoie des requêtes HTTPS signées (SigV4) vers l'URL de la fonction (`AuthType AWS_IAM`), avec permission `lambda:InvokeFunctionUrl` pour le principal `cloudfront.amazonaws.com` | [V] doc CloudFront « Restrict access to an AWS Lambda function URL origin » ; la date de lancement (2024) est [S] | Pour `PUT`/`POST`, **le client doit calculer le SHA-256 du corps** et l'envoyer dans `x-amz-content-sha256` (Lambda n'accepte pas les charges non signées) **[V]**. Réponse bufferisée 6 Mo, flux jusqu'à 200 Mo **[V]**. Prix : Lambda ordinaire + CloudFront | **Oui** pour une future API (`/v1/point`, `/v1/route`), tout langage |
| **Origine Lambda Web Function + OAC** | Nouveau type de fonction Lambda (serveur HTTP en écoute sur un port, plusieurs requêtes par environnement), annoncé comme origine CloudFront le **12/10/2026**, sans frais supplémentaires pour la fonctionnalité **[V]** | [V] annonce du 12/10/2026 et doc « Restrict access to an AWS Lambda Web Function origin » | **Node.js 24 seulement au lancement**, arm64, 2 vCPU / 2 Go fixes, délai de requête 30 s (3 à 900) **[V]** blogs et quotas. Prix `eu-north-1` : 0,30 $ le million de requêtes + 0,0000277088 $/vCPU-s + 0,0000036686 $/Go-s **[V]**. Disponibilité à Stockholm : tarifs présents, **non confirmée** par une page de régions. Politique d'origine : exclure l'en-tête `Host` | Pas adapté tant que le projet écrit en Rust ou Python |
| **Lambda@Edge** | Code exécuté aux points de présence, déclenché par des événements de requête/réponse | [V] tarification : 0,60 $ le million de requêtes + 0,00005001 $/Go-s | Fonction à déployer dans `us-east-1`, pas de variables d'environnement ni de VPC [S] ; facturée à l'usage même avec un forfait CloudFront **[V]** | Non : rien à calculer sur le chemin des tuiles |
| **CloudFront Functions** | JavaScript léger au bord, < 1 ms | [V] annonce 2025 et tarifs : 0,10 $ le million, 2 millions gratuits | Pas de réseau | Non nécessaire : les en-têtes passent par une politique d'en-têtes de réponse |

Conclusion factuelle : **CloudFront n'appelle pas Lambda par l'API `Invoke`** ; il envoie du HTTPS signé vers un point de terminaison HTTP de Lambda. La nouveauté récente (12/10/2026) est l'origine « Web Function ». Rien de tout cela n'intervient dans le traitement (SQS → Lambda, qui reste du ressort de Lambda « Event Function »).

### 4.3 Diffusion : CloudFront + S3

- **Domaines** : `weather.runeride.fr` reste sur o2switch (site statique, pas derrière CloudFront). **Un seul domaine CloudFront : `weather-data.runeride.fr`**, certificat **ACM en `us-east-1`** (exigence de CloudFront), validation DNS et alias par **CNAME dans cPanel** (un sous-domaine accepte un CNAME). Évite un second certificat et une seconde distribution. Réponse à l'« OUVERT » de spécifications §4 : deux noms, un seul derrière CloudFront.
- **Accès S3** : bucket privé, Block Public Access, **OAC** (jamais d'OAI : les forfaits forfaitaires ne le supportent pas **[V]**), politique de bucket restreinte à `aws:SourceArn` de la distribution.
- **Comportements de cache** :
  - `*.pmtiles` : `Cache-Control: public, max-age=31536000, immutable` posé à l'écriture S3 ; politique de cache gérée « CachingOptimized » (pas de chaîne de requête) ;
  - `manifest.json` : `Cache-Control: public, max-age=30` (ou 60), `ETag`, compression activée ; pas d'invalidation (1 000 chemins gratuits/mois, 0,005 $ au-delà **[V]**) ;
  - `sante.json` : fichier statique mis à jour par le poller (TTL 60 s) au lieu d'une Lambda.
- **Immuabilité et correction** : si une échéance est retraitée avec un contenu différent, l'ancien reste 1 an en cache. Prévoir une révision de pipeline dans le chemin (`…/{tirage}/r2/…`, donc dans `file_template` du manifeste) ou une invalidation par chemin. **Décision ouverte.**
- **CORS** : politique d'en-têtes de réponse (autorise `https://weather.runeride.fr`, méthodes `GET, HEAD, OPTIONS`, en-têtes demandés `Range, If-Match`, exposés `Content-Range, Accept-Ranges, ETag, Content-Length`). `Range` simple est déjà autorisé sans pré-requête par la spécification Fetch [S] ; prévoir quand même les `OPTIONS` en cache.
- **Compression** : CloudFront compresse le JSON (manifeste) ; ne pas compresser les `.pmtiles` (WebP, déjà compressé ; répertoires internes en gzip).
- **Classe de prix** : `PriceClass_100` (Amérique du Nord et Europe) [S] pour une audience française.
- **Protection du coût** : un budget AWS prévient mais **ne plafonne pas** [S]. Avec CloudFront à l'usage, la seule limitation réelle est une règle WAF de limitation de débit par IP (spécifications §8). Prix WAF : 0,60 $ le million de requêtes inspectées et 1 $ par règle et par mois **[V]** (+ 5 $ par ACL [S]) : 6 $ fixes, ≈ 10 $ à 1 000 utilisateurs/jour, ≈ 100 $ à 10 000 avec le client actuel. Les forfaits CloudFront (§5.2) incluent WAF et suppriment le risque de dépassement.

### 4.4 Ce que « Lambda derrière CloudFront » apporterait réellement

- **Rien pour les tuiles ni le manifeste** : S3 + OAC fait mieux et moins cher.
- **Point (`/v1/point`, J4)** : le choix de D6 reste valide en deux temps. (1) Le pipeline écrit des **blocs de séries ponctuelles** statiques (par cellule géographique) lus en `Range` par le navigateur via CloudFront : zéro Lambda, zéro coût de calcul. Taille non chiffrée ici : HD complet = 5 millions de points × 8 variables × 52 échéances × 1 octet ≈ 2,1 Go par tirage avant compression **[C]**, soit doubler le stockage. (2) Si la latence de 500 ms n'est pas tenue, une Lambda « Event Function » avec Function URL + OAC (256 Mo arm64, 100 ms) coûte ≈ 0,5 $ le million d'appels **[C]**.
- **Itinéraire (`POST /v1/route`, J6)** : seul cas où du calcul à la demande est justifié. Lambda Function URL + OAC, concurrence réservée plafonnée (par exemple 10) pour **borner le coût** ; attention à `x-amz-content-sha256` pour les `POST` **[V]**.

## 5. Coût mensuel par scénario d'audience

### 5.1 Hypothèses explicites (toutes [E], à remplacer par des mesures)

- Une session par utilisateur actif et par jour, 30 jours.
- Session « actuelle » : 70 % mobile (≈ 250 requêtes, 6 Mo) et 30 % bureau (≈ 1 200 requêtes, 20 Mo), fond de carte compris, soit en moyenne **535 requêtes et 10,2 Mo** (les mesures de visualisation §8.4 donnent 329 à 1 722 requêtes et 3 à 29 Mo pour la frise d'une couche).
- Session « sobre » : préchargement de ±3 h et tuiles inchangées : **150 requêtes, 4 Mo**.
- Palier gratuit CloudFront : 1 To et 10 millions de requêtes HTTP/HTTPS par mois **[V]** (il ne s'applique pas aux forfaits, et le compte ne doit pas être sous plan gratuit pour les forfaits **[V]**).
- Tarif CloudFront Europe : 0,085 $/Go (10 premiers To), 0,012 $ pour 10 000 requêtes HTTPS **[V]**.

### 5.2 Diffusion

| Audience (actifs/jour) | Sessions/mois | Volume | Requêtes | CloudFront PAYG, palier gratuit déduit | Sans palier gratuit | Forfait CloudFront **[V]** |
|---|---|---|---|---|---|---|
| 100 | 3 000 | 31 Go | 1,6 M | **0 $** | 4,5 $ | Free 0 $ (allocation 1 M req, 100 Go : dépassée de 60 %) |
| 1 000 | 30 000 | 306 Go | 16 M | **7 $** | 45 $ | Pro 15 $ (10 M req, 50 To : requêtes dépassées ×1,6) |
| 10 000 | 300 000 | 3,1 To | 160 M | **≈ 354 $** (173 $ de volume + 181 $ de requêtes) | 453 $ | Business 200 $ (125 M req : dépassée ×1,3 ; WAF et cache personnalisé inclus) |
| 10 000, session sobre | 300 000 | 1,2 To | 45 M | **≈ 57 $** (15 $ de volume + 42 $ de requêtes) | | Pro 15 $ (10 M req : dépassée ×4,5, donc Business 200 $ exagéré) |

Les dépassements d'allocation d'un forfait n'entraînent **aucun surcoût** mais, s'ils sont durables, une dégradation des performances (distribution depuis des points de présence plus éloignés) ; un premier pic jusqu'à 3 fois l'allocation est toléré **[V]**. La phrase « jusqu'à 3 fois » vaut une fois, pas en régime permanent.

Requêtes S3 GET induites (§2.3) : 0,3 / 1,3 / 5,1 $ ; moins avec la session sobre.

### 5.3 Total mensuel

| Poste | Montant | Fiabilité |
|---|---|---|
| Lambda (tirages) | 8 à 18 $ | [C] ±30 % |
| S3 stockage, 15 jours (119 Go) / 2 jours / 3 tirages | 2,7 $ / 0,4 $ / 0,07 $ | [C] |
| S3 PUT + LIST + manifeste | ≈ 1,3 $ | [C] |
| SQS, DynamoDB, Scheduler, ECR, journaux (rétention 14 j), ≈ 8 alarmes à 0,10 $ | 1,5 à 3 $ | [E] |
| **Socle sans diffusion** | **≈ 14 à 25 $** (15 jours) ; ≈ 11 à 22 $ (2 jours) | |

| Audience | Diffusion CloudFront + S3 GET | Total (socle 15 j) | Avec WAF à l'usage |
|---|---|---|---|
| 100 | ≈ 0,3 $ | **≈ 15 à 25 $** | +6 $ |
| 1 000 | ≈ 8 $ | **≈ 22 à 33 $** | +16 $ |
| 10 000, client actuel | ≈ 360 $ (PAYG) ou 200 $ (Business, WAF inclus) | **≈ 375 à 385 $ (PAYG)** ou **≈ 215 à 225 $ (Business)** | +100 $ en PAYG |
| 10 000, préchargement sobre | ≈ 62 $ | **≈ 75 à 85 $** | +33 $ |

Les spécifications §8 annoncent « quelques euros à quelques dizaines d'euros par mois, dominé par la diffusion » : **vrai jusqu'à ≈ 1 000 utilisateurs/jour**, faux à 10 000 si le client précharge la frise entière.

**Incertitudes** : la part diffusion varie de ×0,5 à ×2 selon le nombre de sessions par utilisateur et la fraction de la frise préchargée ; la part calcul de ±30 % (Graviton contre x86 de mesure) ; HP1 (3,9 Go par tirage en HD) ou l'extension à ARPEGE ajouteraient du calcul et du téléchargement, **non couverts** ici. Les montants en euros dépendent du change (non appliqué).

## 6. Sécurité : rôles minimaux

- **Worker** : `sqs:ReceiveMessage/DeleteMessage/GetQueueAttributes` sur sa file ; `s3:PutObject` sur `…/v1/arome*/*` seulement ; `dynamodb:UpdateItem` sur la table d'état ; journaux. Aucun `s3:Delete*`, aucune lecture S3.
- **Poller** : `sqs:SendMessage` sur la file des jobs ; `dynamodb:Query/PutItem/UpdateItem` ; `s3:PutObject` sur `v1/sante.json` ; jeton AROME-PI : `secretsmanager:GetSecretValue` sur ce secret seulement (J8).
- **Manifeste** : `s3:ListBucket` (préfixe `v1/`), `s3:GetObject` (plage d'en-têtes), `s3:PutObject` sur `v1/manifest.json` seulement ; concurrence réservée 1.
- **Bucket** : privé, Block Public Access, politique limitée à la distribution CloudFront (OAC), refus du HTTP, chiffrement par défaut SSE-S3, pas de versionnage.
- **File SQS** : chiffrement géré ; politique n'autorisant l'envoi des événements S3 que depuis ce bucket (`aws:SourceArn`).
- **CloudFront** : en-têtes de sécurité (HSTS, `nosniff`) par politique ; aucune clé AWS dans le client ; si API : OAC et concurrence réservée.
- **Compte** : MFA sur la racine, rôle d'administration séparé, AWS Budgets avec alertes (80 %, 100 %, prévision).

## 7. Observabilité minimale

| Signal | Source | Alarme |
|---|---|---|
| Âge du dernier tirage complet | métrique personnalisée (format EMF) émise par le poller | > 6 h (spécifications §8) |
| Retard de publication par échéance (instant d'apparition chez Météo-France → PutObject) | EMF du worker | médiane > 15 min (objectif §8) |
| Erreurs, limitations, durée, mémoire maximale des workers | `AWS/Lambda` | `Errors` > 0 sur 15 min ; durée > 80 % du délai |
| Âge du plus vieux message / profondeur DLQ | `AWS/SQS` | DLQ ≥ 1 ; âge > 30 min |
| Manifeste : échec ou absence de mise à jour pendant un tirage | `AWS/Lambda` | `Errors` > 0 |
| CloudFront : taux 5xx / 4xx, taux de succès du cache | `AWS/CloudFront` (métriques de base) | 5xx > 1 % |
| Coût | AWS Budgets + détection d'anomalies | 80 % du budget mensuel |

Alertes par SNS vers courriel ; journaux conservés 14 jours (ingestion 0,54 $/Go à Stockholm **[V]**, négligeable). Vérifier la saturation de la concurrence Lambda (`ConcurrentExecutions`) pendant les premiers tirages.

## 8. Architecture recommandée

```
                Météo-France (OVH S3 public, GRIB2)
                         ▲   ListObjectsV2 / GET (hors AWS)
                         │
   EventBridge Scheduler ┴─► Lambda poller ◄──► DynamoDB (état)
        (2 à 5 min)                │ SendMessage (échéance prête)
                                   ▼
                         SQS jobs ──► DLQ ──► alarme
                                   │ concurrence max 10–20, lot 1
                                   ▼
                  Lambda worker (arm64, conteneur, hors VPC)
              GRIB → décode → dérive → quantifie → tuile PMTiles
                                   │ PutObject (clé déterministe)
                                   ▼
                      S3 weather-data (privé, OAC)
                         │                      ▲
                         │ ObjectCreated         │ PUT manifest.json (en dernier)
                         ▼                      │
                   SQS manifeste ──► Lambda manifeste (concurrence réservée 1)
                                                │ + sante.json (poller)
   S3 ──► CloudFront (weather-data.runeride.fr, ACM us-east-1, CNAME cPanel)
              ├─ *.pmtiles : immutable 1 an, Range
              ├─ manifest.json : 30 s, ETag, compression
              └─ /v1/point, /v1/route (plus tard) ──► Lambda Function URL + OAC
   o2switch : weather.runeride.fr (statique) ──► navigateur (MapLibre + PMTiles)
```

**Décisions prises par cette étude** : Lambda par échéance ; D2 conservé ; pas de Step Functions ni d'Origin Shield ni de S3 Express ; hors VPC donc sans NAT ; un seul domaine derrière CloudFront ; tuiles immuables et manifeste à TTL court ; CloudFront à l'usage (PAYG) tant que l'audience est sous ≈ 3 000 à 5 000 actifs/jour.

**Décisions ouvertes** :

1. Rétention : 3 tirages (D9), 2 jours ou 15 jours (propriétaire) ? Coût 0,07 / 0,4 / 2,7 $/mois, mais le manifeste doit rester limité aux tirages affichables.
2. Politique de préchargement du client et taille de tuile (256 ou 512 px) : c'est le premier levier de coût à 10 000 utilisateurs (×6 de différence).
3. Forfait CloudFront (Pro 15 $, Business 200 $) contre PAYG : seuil de bascule à mesurer sur l'audience réelle ; le forfait nécessite un compte hors palier gratuit AWS et inclut WAF.
4. État du pipeline : DynamoDB ou marqueurs S3.
5. Dé-cumul : retélécharger l'échéance n−1 ou conserver les champs canoniques.
6. Révision de pipeline dans le chemin des fichiers (retraitement d'un tirage).
7. Ajout de `latest_complete` au schéma du manifeste.
8. Image conteneur ou ZIP ; Python ou Rust (taille, démarrage à froid, arm64).
9. HP1 (74 Mo par échéance HD) : dedans ou dehors ; impact sur durée, `/tmp` et téléchargement.
10. API point : blocs statiques d'abord ou Lambda tout de suite ; et rejet de « Web Function » tant que seul Node.js est supporté.
11. Compte AWS : type de plan (palier gratuit/forfait), quotas Lambda réels du profil `bike360`.

**Points de vigilance** : quotas des comptes neufs (concurrence, mémoire) ; durée de calcul à confirmer sur Graviton ; tirages d'été ou de tempête non mesurés (plage de quantification D14) ; fichier Météo-France tronqué (R11) ; absence de plafond dur de dépense en PAYG ; hébergeur OVH comme point de défaillance unique (R1, R2).

## 9. Plan d'implémentation

1. **Mesures sur AWS** (une demi-journée, sans pipeline complet) : une Lambda arm64 qui télécharge un tirage réel depuis OVH vers `eu-north-1`, ingère et tuile une échéance HD, pour mesurer durée, mémoire, démarrage à froid, débit, comparées aux 29 s et 0,89 Go ; refaire en x86 pour confirmer l'écart de prix.
2. **Socle de diffusion** : bucket privé + CloudFront + OAC + domaine `weather-data` (certificat ACM en `us-east-1`, CNAME cPanel) ; publier à la main un tirage de test ; vérifier avec le viewer les `Range`, le CORS, le taux de succès du cache (`X-Cache`), le nombre de requêtes à l'ouverture.
3. **Worker + file** : SQS, DLQ, mapping à concurrence plafonnée, état DynamoDB, écriture déterministe, test d'idempotence (message rejoué) et d'échec (fichier tronqué).
4. **Poller** : sondage de listage, critère de fichier complet, déclenchement par niveau, métrique d'âge du tirage ; test sur un tirage réel en direct.
5. **Manifeste** : événements S3 → SQS → Lambda (concurrence 1), `latest_complete`, ordre « tuiles puis manifeste », TTL court.
6. **Rétention et coûts** : règle de cycle de vie (préfixes par modèle), budget et alarmes de coût, règle WAF de limitation de débit si besoin.
7. **Observabilité** : alarmes du §7, tableau de bord minimal, test d'une panne volontaire (DLQ, source coupée).
8. **Recette J5** : 7 jours sans intervention, fraîcheur ≤ 15 min mesurée, un tirage incomplet géré par repli (D8).
9. **Ensuite** : points/itinéraires (Function URL + OAC) ; si l'audience dépasse ≈ 5 000 actifs/jour, mesurer les requêtes par session et envisager tuiles de 512 px, préchargement sobre ou archives empilées (variante C), et comparer au forfait Business.

## 10. Variante lancement < 1 $/mois (≈ 20 utilisateurs actifs/jour)

Contrainte ajoutée le 18/10/2026 : coût total inférieur à 1 $/mois au lancement. Même étiquettes que plus haut. **Conclusion d'avance : c'est faisable, à ≈ 0,5 à 0,8 $/mois en régime établi, à condition de rester dans le palier gratuit Lambda (2 tirages par jour sur tout le domaine, ou 4 avec le HD limité à la France) et de ne mettre ni WAF payant, ni NAT, ni 8 tirages par jour.**

### 10.1 Paliers gratuits : ce qui est vérifié

| Service | Palier | Statut |
|---|---|---|
| Lambda | 1 M de requêtes et **400 000 Go-s par mois** (page de tarifs Lambda, FAQ AWS) ; offre classée « toujours gratuite » pour les formules Free et Paid | [V]. Application à `eu-north-1` et à arm64 : la documentation ne distingue pas région ni architecture pour ce palier (les Go-s sont agrégés par architecture seulement pour les tranches de prix) ; **non confirmé explicitement** [S] |
| SQS | 1 M de requêtes par mois, mensuel, sans report | [V] |
| EventBridge Scheduler | 14 M d'invocations par mois | [V] |
| DynamoDB | 25 unités d'écriture et 25 de lecture **provisionnées**, 25 Go (classe Standard). **Ne couvre pas le mode à la demande.** | [V] |
| CloudWatch | 5 Go de journaux (ingestion, archivage, requêtes Insights), 10 métriques personnalisées, 10 alarmes standard, 3 tableaux de bord, 1 M de requêtes d'API | [V] |
| CloudFront | Palier à l'usage : 1 To et 10 M de requêtes par mois, 2 M d'exécutions de Functions. **Ou** forfait Free 0 $ : 1 M de requêtes, 100 Go, WAF inclus (5 règles), **5 Go de crédit de stockage S3 Standard**, aucun dépassement facturé | [V] |
| S3 | Ancien palier de 12 mois : 5 Go, 20 000 GET, 2 000 PUT. Les comptes récents ont des crédits à la place (100 à 200 $, valables 12 mois ; le plan Free expire à 6 mois) | [V] ; **tous les chiffres S3 ci-dessous sont après expiration**, sans aucun palier |
| ACM (certificat public), DNS chez o2switch, SSE-S3, budgets sans action | gratuits | [S] |

Prix hors palier pour borner le risque : Lambda arm64 0,0000133334 $/Go-s, S3 PUT 5 $ le million, GET 0,40 $ le million, stockage 0,023 $/Go-mois, SQS 0,40 $ le million, journaux 0,54 $/Go, CloudFront 0,085 $/Go et 1,20 $ le million de requêtes, alarme 0,10 $, DynamoDB à la demande 0,67 $ le million d'écritures [V].

### 10.2 (a) Combien de tirages tiennent dans le gratuit Lambda

Le coût Lambda est (CPU consommé) × 1,727 Go : 2 455 s × 1,727 ≈ **4 240 Go-s par tirage** [C], d'où mes 4 200. Les Lambdas annexes (poller toutes les 5 min, constructeur de manifeste) ajoutent ≈ 7 000 Go-s/mois [E]. Budget quotidien : (400 000 − 7 000) / 30 ≈ **13 100 Go-s/jour**.

Hypothèses de réduction du HD (tuilage 21 s → ≈ 8 s si seulement la France est tuilée, soit ≈ 38 % de la surface utile du domaine ; l'ingestion de 8 s reste entière car le GRIB se décode en entier) : **[E], non mesurées.**

| Profil par tirage | Go-s | Tirages/jour (nominal) | Tirages/jour (avec +30 % Graviton) |
|---|---|---|---|
| Complet : HD + AROME, 52 échéances, tout le domaine | 4 200 | 3 (12 600/jour, très juste) | **2** |
| HD limité à la France, 52 échéances | 3 040 | 4 | 3 |
| HD France et 25 échéances (0 à 24 h), AROME 52 | 2 290 | 5 | **4** |
| AROME seul (sans HD), 52 échéances | 1 600 | 8 | 6 |

Au-delà du gratuit : 75 000 Go-s coûtent 1,00 $. Un tirage complet de plus par jour (126 000 Go-s/mois) coûte **1,68 $/mois** [C]. Sans palier gratuit (compte sans accès à l'offre) : 2 tirages complets = 3,45 $/mois [C], donc la contrainte < 1 $ **dépend du palier gratuit Lambda**.

### 10.3 (b) Requêtes S3 et regroupement

| Profil | Fichiers/tirage | PUT/mois (après expiration du palier) | Coût |
|---|---|---|---|
| L-A : 2 tirages/jour, domaine entier | 824 | 49 400 | **0,25 $** [C] |
| L-B : 4 tirages/jour, HD France 0 à 24 h | ≈ 608 (196 HD + 412 AROME) | 73 000 | **0,36 $** [C] |
| Variante B (un fichier par modèle, tirage, échéance, variables multiplexées) | 104 | 6 200 (L-A) | 0,03 $ ; **format non standard** |
| Une archive par (modèle, tirage, variable) écrite à la fin du tirage | 16 | ≈ 960 | ≈ 0,00 $ ; **supprime la publication progressive** |

Le regroupement économise au mieux 0,2 à 0,35 $/mois. La variante « une archive par variable à la fin du tirage » est à écarter même ici : avec un tirage toutes les 6 h et une publication de 2,6 à 5,3 h, attendre la fin ferait servir presque en permanence le tirage précédent. **Je garde D2 (publication progressive)** et je réduis plutôt le nombre de fichiers par le périmètre : retirer 3 des 8 variables au lancement (par exemple `cape`, `snow_fraction`, `cloud_total`) diminue à la fois les PUT (−38 %) et le calcul Lambda (−25 à 30 % [E]). Autres lignes S3 :

- Manifeste et `sante.json` : écrits par le poller **seulement quand l'état change** (pas toutes les 5 min : 8 600 PUT/mois = 0,04 $ inutiles) : ≈ 2 200 PUT/mois ≈ 0,01 $ [C]. Listages et lectures d'en-têtes du constructeur : ≈ 4 000/mois ≈ 0,02 $ [C].
- Stockage avec rétention « 3 tirages » : une règle d'expiration ne descend pas sous 1 jour ; avec expiration à 2 jours, L-A garde 4 à 5 tirages ≈ 4 à 5 Go, L-B ≈ 8 tirages ≈ 4 à 5 Go (HD réduit) : **≈ 0,10 $/mois** [C]. Le forfait CloudFront Free offre 5 Go de crédit S3 qui l'annulerait [V].
- GET S3 (échecs de cache CloudFront), 20 utilisateurs : 600 sessions × 535 requêtes = 321 000 requêtes, 70 % d'échecs [E] ≈ 225 000 GET = **0,09 $** ; avec 150 requêtes par session : 0,03 $ [C].

### 10.4 (c) CloudFront face à 20 utilisateurs/jour

600 sessions/mois : **321 000 requêtes et 6,1 Go** avec 535 requêtes/session ; **90 000 requêtes et 2,4 Go** avec 150 [C]. C'est 32 % (puis 9 %) de l'allocation de requêtes du forfait Free (1 M) et 6 % (2,4 %) de ses 100 Go, et très en dessous du palier à l'usage (10 M, 1 To). **0 $ dans les deux cas.** Choix :

- **Forfait Free** : 0 $, WAF et limitation de débit par IP inclus, aucun dépassement facturé, crédit S3 de 5 Go. Réserve : la documentation exclut les comptes « utilisant l'offre gratuite AWS » (contrainte au niveau du compte) ; la portée exacte vis-à-vis du plan de compte Free n'est pas claire **[S]**, à tester à la création. Un seul domaine apex par plan (`runeride.fr`).
- **À l'usage avec palier gratuit** : 0 $ mais **aucun plafond** (au-delà de 10 M requêtes : 1,20 $ le million) et pas de WAF. Mettre un budget d'alerte à 1 $ et 3 $.

### 10.5 (d) Le reste

| Poste | Hypothèse de lancement | Coût | Statut |
|---|---|---|---|
| Journaux CloudWatch | niveau WARN, un résumé par invocation (≈ 0,5 Ko), rétention 7 jours : ≈ 0,05 Go/mois | 0 $ (5 Go gratuits ; 0,03 $ sans) | [C] ; **piège** : des journaux détaillés de 1 Mo par invocation (25 000/mois) = 25 Go ≈ 11 $ |
| Alarmes et métriques | ≤ 10 alarmes et ≤ 10 métriques personnalisées (EMF compris) | 0 $ | [V] |
| DynamoDB | table **provisionnée** 5 UE écriture / 5 lecture, classe Standard, ≈ 20 000 écritures/mois | 0 $ (à la demande : ≈ 0,02 $) | [V]/[C] |
| SQS | **une seule file** de tâches + DLQ. Le mapping Lambda sonde en continu (jusqu'à 5 lecteurs en attente longue de 20 s ≈ 650 000 requêtes/mois [S]) | 0 $ dans 1 M ; ≈ 0,26 $ au pire sans palier | [S] |
| EventBridge Scheduler | 8 600 invocations/mois | 0 $ | [V] |
| File « manifeste » | **supprimée** : le poller reconstruit le manifeste quand DynamoDB signale de nouvelles échéances (retard ≤ 5 min) | 0 $ | choix |
| ECR | 1 image arm64 ≈ 0,5 Go, règle de cycle de vie qui garde 2 images | ≈ 0,05 $ ; 0 $ en archive ZIP | [V] prix / [E] taille |
| WAF | **inclus dans le forfait Free**, sinon aucun. WAF payant ≥ 6 $/mois | 0 $ | [V] |
| NAT, VPC | **aucun** (Lambda hors VPC) | 0 $ (NAT : 66 $) | [V]/[C] |
| ACM, DNS o2switch | certificat public, CNAME dans cPanel | 0 $ | [S] |
| Secrets Manager | pas d'AROME-PI au lancement ; plus tard préférer un paramètre SSM chiffré | 0 $ | [S] |
| Entrée de données depuis OVH | gratuite | 0 $ | [S] |

### 10.6 Coût total

| Ligne (mensuel, régime établi, aucun palier S3) | L-A : 2 tirages, tout le domaine | L-B : 4 tirages, HD France 0 à 24 h |
|---|---|---|
| Lambda calcul (palier gratuit) | 0 $ (259 k Go-s, 335 k avec +30 %) | 0 $ (282 k Go-s, 374 k avec +30 %) |
| Lambda requêtes | 0 $ | 0 $ |
| S3 PUT, manifeste, listages | 0,28 $ | 0,40 $ |
| S3 stockage | 0,10 $ (0 $ avec crédit du forfait Free) | 0,11 $ (0 $ idem) |
| S3 GET (20 utilisateurs) | 0,03 à 0,09 $ | 0,03 à 0,09 $ |
| CloudFront | 0 $ | 0 $ |
| DynamoDB, SQS, Scheduler, journaux, alarmes | 0 à 0,3 $ | 0 à 0,3 $ |
| ECR | 0,05 $ | 0,05 $ |
| **Total** | **≈ 0,5 à 0,8 $** | **≈ 0,6 à 1,0 $ (à la limite)** |

Avec les crédits de compte neuf (100 à 200 $ sur 12 mois) la facture réelle des premiers mois est de 0 $. Incertitudes : durée Graviton (±30 %), applicabilité du palier Lambda, taux d'échec du cache de CloudFront à faible audience (50 à 90 %).

**Recommandation de lancement : L-A.** Il ne change rien au code (même découpage, pas de zone spéciale), garde une marge de 0,2 à 0,5 $ et n'utilise que 65 à 85 % du palier Lambda. L-B apporte un tirage toutes les 6 h au lieu de 12 h mais exige un tuilage HD limité à une zone (non mesuré) et frôle le plafond.

### 10.7 Ce qu'il faut sacrifier

- **Fraîcheur** : 2 tirages par jour (donnée jusqu'à 12 h plus ancienne, plus ≈ 3 à 5 h de retard de publication) au lieu de 8 ; le jeu de tirages à choisir (par exemple 06 h et 18 h UTC) est une décision de produit. L-B : un tirage toutes les 6 h.
- **Zone et horizon** (L-B seulement) : HD sur la France et 24 h ; ailleurs AROME 0,025° (règle D7 de repli par zone à ajouter).
- **Fonctions** : pas d'API point ni itinéraire, pas d'AROME-PI, pas de WAF payant, pas de file « manifeste », au plus 10 alarmes, journaux minimaux, pas de rétention de 15 jours.
- **Conservé** : publication progressive par échéance, manifeste, `immutable`, CloudFront + OAC, seuls ces postes comptent pour la qualité perçue.

### 10.8 Ce qui ferait passer au-dessus de 1 $

| Facteur | Effet [C] |
|---|---|
| Un 3e tirage complet par jour (ou palier gratuit Lambda indisponible) | +1,68 $ (ou +3,45 $ avec 2 tirages) |
| 8 tirages complets par jour | ≈ 8 $ avec le palier, 13,5 à 17,5 $ sans |
| Rétention de 15 jours en domaine entier | +2,7 $ (3 tirages/jour : 119 Go) |
| WAF payant | ≥ 6 $ |
| Lambda en VPC avec NAT | ≈ 66 $ |
| Journaux verbeux (≥ 1 Mo/invocation) | ≈ +11 $ |
| Audience : ≈ 300 à 800 utilisateurs actifs/jour avec 535 requêtes/session (les GET S3 seuls atteignent 1 $ ; CloudFront à l'usage reste gratuit jusqu'à ≈ 650 utilisateurs/jour, ≈ 2 400 en session sobre) | franchit 1 $ |
| Pic de trafic anormal (moissonnage) sur CloudFront à l'usage, sans WAF | 1,20 $ par million de requêtes au-delà de 10 M ; le forfait Free l'évite |
| Plus de 10 alarmes ou métriques, ou plus de 5 Go de journaux | 0,10 $ par alarme ; 0,54 $/Go |

## 11. Sources consultées (18/10/2026)

- Documentation CloudFront : « Restrict access to an AWS Lambda function URL origin », « Restrict access to an AWS Lambda Web Function origin », « How CloudFront processes partial requests (range GETs) », « CloudFront flat-rate pricing plans » (allocations, fonctionnalités non supportées, quotas), FAQ (palier gratuit).
- Annonces : « Amazon CloudFront now supports AWS Lambda Web Functions as an origin » (12/10/2026) ; « Announcing AWS Lambda Web Functions » ; billet de blog Compute « Exploring AWS Lambda Web Functions » (30/09/2026).
- Documentation Lambda : « Lambda quotas », « Understanding Lambda Event Function scaling », dépannage de déploiement (comptes neufs) ; blog « maximum concurrency … SQS ».
- Documentation S3 : « S3 Express One Zone Availability Zones and Regions ».
- API de prix AWS (`pricing:GetProducts`, lecture seule) : Lambda, S3 (dont Express), SQS, CloudFront, EventBridge, Step Functions, DynamoDB, ECS/Fargate, ECR, VPC (NAT), CloudWatch, WAF.
- Paliers gratuits (§10) : tarifs Lambda, FAQ SQS, tarifs EventBridge (Scheduler), tarifs DynamoDB, tarifs CloudWatch, FAQ CloudFront (palier gratuit et forfait Free), conditions du Free Tier AWS (9/07/2025) et billet « AWS Free Tier update ».
- Compte : appel `lambda:GetAccountSettings` en `eu-north-1`, lecture seule ; aucune ressource créée ni modifiée.
