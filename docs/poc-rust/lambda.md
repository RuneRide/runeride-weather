<!-- SPDX-License-Identifier: EUPL-1.2 -->
# Binaire Lambda `rw-lambda` : une échéance par message SQS (18 octobre 2026)

Suite de [synthese.md](synthese.md) (portage Rust du traitement d'une échéance) et mise en œuvre du worker décrit dans [../architecture-aws.md](../architecture-aws.md) (§3 et §10 : fan-out par échéance, publication progressive, manifeste en dernier). **Rien n'est déployé, aucune ressource AWS n'a été créée ni appelée, aucun identifiant AWS réel n'a été lu.** Tout ce qui suit a été compilé et testé en local, avec une source HTTP et un « faux S3 » locaux (sauf une vérification de la source réelle, §9).

Étiquettes : **[M]** mesuré ici, **[T]** vérifié par un test automatique, **[N]** non testé / à vérifier au déploiement.

## 1. Ce qui existe

| Élément | Fichier |
|---|---|
| Crate `rw-lambda` (bibliothèque + binaire `bootstrap`) | `rust/crates/rw-lambda/` |
| Fonction réutilisable de `rw-cli` (liste des GRIB requis, ingestion depuis la mémoire) | `rust/crates/rw-cli/src/canon.rs` (`noms_requis`, `ingerer_octets` ; `ingerer` en est une enveloppe, comportement inchangé) |
| Serveur local source GRIB + faux S3 (vérifie la signature SigV4) | `rust/tools/serveur_local.py` |
| Faux API Runtime de Lambda (essai du binaire avec `lambda_runtime`) | `rust/tools/essai_runtime_lambda.py` |
| Vérification de bout en bout et d'exactitude (tuiles et champs contre le Python) | `rust/tools/verifier_lambda.sh` |
| Emballage du zip (sans envoi) | `rust/tools/package-lambda.sh` |
| Tests | `cargo test --release -p rw-lambda` (10 tests unitaires + 9 tests d'intégration) |

Le code Python du dépôt n'est pas modifié.

## 2. Architecture du binaire

```text
SQS (BatchSize 1)  ──►  bootstrap (lambda_runtime, runtime Tokio « current_thread »)
                          │  spawn_blocking
                          ▼
                     handler::traiter_lot ──► pour chaque message : traiter_corps
                          │                       message.rs   JSON → Travail → validation stricte
                          │                       traitement.rs idempotence → téléchargement → canon → tuiles → écritures → état
                          ▼
              { "batchItemFailures": [ { "itemIdentifier": … } ] }
```

| Module | Rôle |
|---|---|
| `message` | `{"modele": "arome-hd", "tirage": "2026-10-17T03:00:00Z", "echeance": 12}` ; le tirage accepte aussi `2026-10-17T03Z`. Validation stricte (modèle, échéance 0 à 51, date réelle, ASCII) car ces valeurs entrent dans des URL et des clés S3. |
| `source` | Trait `SourceGrib` ; `SourceHttp` (OVH, ou n'importe quelle base HTTP) et `SourceLocale` (dossier). |
| `stockage`, `s3` | Trait `Stockage` (`ecrire`, `lire`, `taille`) ; `StockageLocal` (fichier temporaire puis `rename`) et `StockageS3` (client minimal SigV4, PutObject / GetObject / HeadObject). |
| `sigv4` | Signature AWS SigV4, vérifiée contre l'exemple de la documentation S3 [T]. |
| `http` | Agent `ureq` partagé : rustls avec cryptographie en Rust pur, délais, User-Agent. |
| `traitement` | La chaîne d'une échéance ; clés de sortie ; état. |
| `handler` | Lot SQS → `SqsBatchResponse` ; configuration par variables d'environnement. |
| `erreur` | Classement transitoire / définitive. |

Mode de test sans runtime Lambda : `bootstrap --evenement evenement-sqs.json` exécute un événement SQS écrit en JSON avec la configuration des variables d'environnement et affiche la réponse ; code de sortie 3 s'il reste des messages à rejouer.

### 2.1 Choix de dépendances et mesures

| Question | Choix | Raison, mesures |
|---|---|---|
| Client S3 | **Client minimal signé SigV4** (`ureq` + `hmac` + `sha2`, 300 lignes dans `s3.rs` et `sigv4.rs`) plutôt qu'`aws-sdk-s3` | Sonde de comparaison (PutObject + HeadObject + GetObject, même profil de compilation, x86_64) **[M]** : `aws-sdk-s3` 1.152 = **7,95 Mo**, 91 s de compilation ; client minimal (avec la pile TLS) = **1,98 Mo**, 28 s. Surtout : `aws-sdk-s3` tire `aws-lc-sys` (code C) et **ne se compile pas pour arm64 ici** (« aarch64-linux-musl-gcc introuvable ») **[M]**. Le démarrage à froid du SDK n'a pas été mesuré. Inconvénients du client minimal : on maintient la signature (testée contre le vecteur officiel et contre une vérification indépendante en Python), pas de chaîne de fournisseurs d'identifiants (on lit seulement `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, ce que fournit Lambda), pas de multipart (inutile : fichiers < 30 Mo). |
| Client HTTP | **`ureq` 3** (synchrone) plutôt que `reqwest` | Pas de runtime asynchrone ni de `hyper` pour la source et S3 ; le travail est de toute façon CPU et mémoire. (`lambda_runtime`, imposé, tire tout de même Tokio et hyper.) |
| TLS | **rustls + `rustls-rustcrypto` 0.0.2-alpha (Rust pur)**, racines `webpki-roots` | Ni `ring` ni `aws-lc` : **aucun compilateur C pour l'arm64** (seul `rust-lld` est utilisé). Poignées de main TLS vérifiées avec OVH (la source réelle), `example.com` et `wikipedia.org` **[M]**. **[N]** : jamais testé contre un point de terminaison AWS (aucun appel AWS). Risque : bibliothèque en version alpha ; repli = `rustls` + `aws-lc-rs` compilé avec `cargo lambda` / zig. |
| Source en mémoire ou `/tmp` | **Mémoire** | Fichiers de 19 à 55 Mo, 4 par échéance ; pas d'écriture disque, plus simple et plus sûr (pas de fichier à nettoyer entre deux invocations d'un même environnement). Pic mesuré **616 Mo (HD)** et **187 Mo (AROME)** **[M]** → 1 024 Mo de mémoire minimum ; 1 769 Mo = 1 vCPU. |
| WebP | `image-webp` (Rust pur) | Décision de l'utilisateur ; sortie octet pour octet identique à `rw run --webp image-webp` du PoC **[T]**. |

## 3. Clés S3 et en-têtes

Disposition de [../architecture-aws.md](../architecture-aws.md) (§6, §2.3 : préfixe stable `v1/`, un préfixe par modèle, `{modèle}/{tirage}/{variable}/{échéance}.pmtiles`, clés déterministes), complétée par des dossiers **commençant par `_`** (jamais une variable) :

| Clé | Contenu | `Content-Type` | `Cache-Control` |
|---|---|---|---|
| `v1/{modèle}/{tirage}/{variable}/{NN}.pmtiles` | tuiles (8 variables en HD et en AROME ; `gust10`, `precip_rate`, `snow_fraction`, `cloud_total` absentes à l'échéance 0) | `application/vnd.pmtiles` | `public, max-age=31536000, immutable` |
| `v1/{modèle}/{tirage}/_etat/{NN}.json` | **état d'échéance, écrit en dernier** (marqueur de fin) ; schéma §6 | `application/json` | `no-cache` |
| `v1/{modèle}/{tirage}/_isobares/{NN}.npz` | source des isobares (AROME seulement) : `lat`, `lon`, `mslp` (Pa), `meta` | `application/octet-stream` | immuable |
| `v1/{modèle}/{tirage}/_domaine/{NN}.npz` | masque `t2m` d'une échéance de référence (3 par défaut), pour le polygone du domaine | idem | immuable |
| `v1/{modèle}/{tirage}/_canon/{NN}.npz` | champs canoniques complets, **seulement si `RW_ECRIRE_CANON=1`** (comparaisons) | idem | immuable |

`{tirage}` = `2026-10-17T03Z`, `{NN}` sur deux chiffres, `{modèle}` = `arome` ou `arome-hd`. Le chemin relatif `{modèle}/{tirage}/{variable}/{NN}.pmtiles` est exactement le `file_template` du manifeste ([../poc-j3/manifest.md](../poc-j3/manifest.md)). Le préfixe (`RW_PREFIXE`) peut porter une révision du pipeline (`v1` → `v1r2`) : c'est la parade à l'immuabilité d'un an si une échéance doit être recalculée avec un contenu différent (décision ouverte de l'architecture, §4.3). **À ne pas exposer par CloudFront** : les dossiers `_*` (comportement de cache dédié ou politique de chemin).

**Atomicité.** Un `PutObject` S3 est atomique par objet (un lecteur voit l'ancien ou le nouveau contenu). L'ordre est : PMTiles (en parallèle), fichiers dérivés, **état en dernier**. Tant que l'état `complete` n'existe pas, l'échéance n'est pas publiée. Une panne au milieu laisse des fichiers déterministes sans marqueur ; le rejeu les réécrit à l'identique. Le stockage local écrit dans un fichier temporaire du même dossier puis `rename`. [T]

## 4. Variables d'environnement

| Variable | Défaut | Rôle |
|---|---|---|
| `RW_BUCKET` | (obligatoire en mode S3) | bucket de sortie |
| `AWS_REGION` | (fournie par Lambda) | région pour SigV4 et l'URL S3 |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` | (fournies par Lambda) | identifiants du rôle d'exécution ; jamais journalisés |
| `RW_PREFIXE` | `v1` | préfixe de clé |
| `RW_SOURCE` | `https://meteofrance-pnt.s3.rbx.io.cloud.ovh.net/pnt` | base HTTP des GRIB ; `dossier:/chemin` pour un dossier local |
| `RW_SORTIE` | `s3` | `local:/chemin` écrit sur disque (développement) |
| `RW_S3_ENDPOINT` | (absent) | point de terminaison S3 explicite en accès « path-style » (tests : faux S3) |
| `RW_THREADS` | tous les processeurs disponibles | fils de calcul (rayon) |
| `RW_CONNEXIONS` | 3 | téléchargements simultanés (limite « courtoise » héritée du Python) |
| `RW_ESSAIS_SOURCE`, `RW_PAUSE_MS` | 4, 1000 | essais et pause de base (doublée à chaque essai, plafonnée à 30 s) |
| `RW_PROFIL` | `reduit2` | profil de tuilage (`reduit2`, `-ete`, `-hiver`) |
| `RW_WEBP` | `image-webp` | seul encodeur compilé dans ce binaire |
| `RW_ECHEANCE_DOMAINE` | `3` | échéance qui écrit `_domaine/NN.npz` (`none` pour désactiver) |
| `RW_ECRIRE_CANON` | `0` | `1` : écrit aussi `_canon/NN.npz` |
| `RW_FORCER` | `0` | `1` : ignore l'état et retraite |
| `RW_DEFINITIF` | (absent) | `rejouer` : les erreurs définitives sont rendues à SQS au lieu d'être acquittées |

## 5. Comportement face aux messages

**Lot.** Taille 1 recommandée (une échéance ≈ 1 à 3 s de calcul, jusqu'à ~10 s avec le téléchargement). Une taille plus grande fonctionne (les messages sont traités à la suite et seuls les messages en échec sont rendus) mais allonge l'exécution et le risque de dépasser le délai de la fonction. La réponse est `{"batchItemFailures": [{"itemIdentifier": "<messageId>"}]}` ; le *mapping* de la source d'événements doit déclarer `FunctionResponseTypes: ["ReportBatchItemFailures"]`, sans quoi un lot « réussi » est supprimé en entier. [T pour la réponse ; N pour le mapping]

| Classe | Exemples | Effet |
|---|---|---|
| **Transitoire** | source **HTTP 404** (fichier non encore publié chez Météo-France), autre HTTP ≠ 2xx, réseau, coupure, fichier tronqué après tous les essais, S3 5xx / 429 après essais, **S3 403** (droits ou configuration à corriger) | message rendu (`batchItemFailures`) ; SQS le redonne après le délai de visibilité ; DLQ après `maxReceiveCount`. Aucun état écrit. |
| **Définitive** | message illisible, modèle inconnu, échéance > 51, tirage mal formé, GRIB illisible ou incohérent, grille différente entre fichiers, cumul n−1 absent des GRIB | message **acquitté** (pas de rejeu inutile), journalisé au niveau `ERROR` avec `"classe": "definitive"`, et, si le message est valide, état `statut: "echec_definitif"` écrit (meilleur effort). `RW_DEFINITIF=rejouer` les rend à SQS (donc DLQ après quelques réceptions). |

Choix assumé : une erreur définitive acquittée n'est visible que par le journal (à surveiller par un filtre de métrique sur `"classe":"definitive"`) et par l'état d'échec. Une erreur de configuration (403 S3, variable absente) est classée transitoire exprès : elle ne doit pas faire disparaître les messages, mais les conduire en DLQ où ils sont rejouables.

**Pas de contrôle de la cadence de rejeu côté fonction** : le délai avant nouvel essai d'un 404 est le délai de visibilité de la file. Voir §10 pour les valeurs.

**Idempotence.** Au début de chaque message : lecture de `_etat/NN.json` ; si `statut = complete`, même version de schéma, de pipeline, même modèle/tirage/échéance, et si chaque fichier cité (PMTiles, isobares, domaine) existe avec la taille inscrite (une requête HEAD par fichier), le message est acquitté sans télécharger ni écrire (`"statut": "deja_fait"`). Sinon l'échéance est refaite en entier. Deux livraisons simultanées d'un même message écrivent les mêmes octets (sorties reproductibles, vérifié ici contre la sortie du PoC) : sans danger. [T : rejeu, fichier manquant, `RW_FORCER`]

## 6. Source des GRIB : quels paquets, et l'échéance n−1

URL (comme `pipeline/ingest/download.py`) : `<base>/<tirage>/arome/<0025|001>/<paquet>/arome__<res>__<paquet>__<étiquette>__<tirage>.grib2`. Seuls **SP1 et SP2** sont téléchargés (ce qui sert aux 8 variables : température, humidité, vent, rafales, précipitations, nuages, CAPE, pression). L'étiquette est `NNH` en 0,01° (un fichier par heure) et un groupe de six échéances en 0,025° (`00H06H`, `07H12H`, …, `49H51H`).

**Pourquoi l'échéance n−1.** Les cumuls `tirf`, `tsnowp`, `tgrp` sont des cumuls 0 → n ; le débit `precip_rate` et la fraction de neige sont des différences `cumul(n) − cumul(n−1)` (borné à 0). Les maxima (rafales) portent sur la fenêtre n−1 → n et n'ont pas besoin du fichier précédent. Le Python (`canon.construire`) et le Rust (`canon.rs`) ont la même règle ; à l'échéance 1, le cumul précédent est nul par construction et à l'échéance 0 il n'y a rien à différencier.

| Cas | Fichiers téléchargés (SP1 + SP2) |
|---|---|
| HD, échéance 0 ou 1 | échéance n seule : 2 fichiers (≈ 25 Mo à l'échéance 0, ≈ 42 Mo à l'échéance 1) |
| HD, échéance ≥ 2 | n et n−1 : 4 fichiers (≈ 86 Mo) |
| AROME 0,025°, n et n−1 dans le même groupe | 2 fichiers (≈ 98 Mo : un groupe de six échéances) |
| AROME 0,025°, n premier du groupe (7, 13, 19…) | 4 fichiers : le groupe de n et celui de n−1 (≈ 200 Mo) |

Écart avec le Python : `fichiers_requis` du Python demande aussi le fichier n−1 pour n = 1, que le calcul n'utilise pas ; le Rust ne le télécharge pas. [T : les listes ci-dessus sont vérifiées par les tests d'intégration] Conséquence à connaître : chaque heure HD est téléchargée deux fois dans un tirage (pour n et pour n+1) et chaque groupe AROME jusqu'à six fois ; c'est du transfert entrant gratuit pour Lambda, mais du temps (voir risques).

**Robustesse HTTP.** `User-Agent: runeride-weather-rust/0.1.0 (ingestion; contact: …)` ; délais : connexion 10 s, première réponse 30 s, requête entière 180 s ; jusqu'à 4 essais avec pauses croissantes ; coupure ou troncature → reprise par `Range: bytes=<reçus>-` (si le serveur répond 200 au lieu de 206, on repart de zéro) ; taille reçue comparée au `Content-Length` ; 3 téléchargements simultanés au plus. 404 → « absente », sans nouvel essai interne. [T : reprise après coupure au tiers de chaque fichier, 404]

## 7. État d'échéance et lien avec `pipeline/manifest`

Un petit JSON par échéance, que la Lambda « manifeste » agrège **à la place** de la lecture d'un en-tête PMTiles par variable. Exemple réel (AROME, échéance 48, abrégé) :

```json
{
  "schema": 1, "statut": "complete",
  "modele": "arome", "tirage": "2026-10-17T06Z", "run": "2026-10-17T06:00:00Z", "echeance": 48,
  "valide": "2026-10-19T06:00:00Z", "traite": "2026-10-18T14:09:06Z",
  "pipeline": { "version": "0.1.0", "profil": "reduit2", "webp": "image-webp" },
  "grille": { "nlat": 717, "nlon": 1121, "lat": [55.4, 37.5], "lon": [-12.0, 16.0], "points_valides_t2m": 665681 },
  "variables": {
    "t2m": { "chemin": "arome/2026-10-17T06Z/t2m/48.pmtiles", "cle": "v1/arome/2026-10-17T06Z/t2m/48.pmtiles",
             "octets": 1441253, "tuiles": 142, "minzoom": 3, "maxzoom": 7,
             "encoding": "scalar8", "scale": "linear", "unit": "K", "min": 243.15, "max": 306.65, "quant_step": 0.2499999999999999 },
    "wind10": { "chemin": "arome/2026-10-17T06Z/wind10/48.pmtiles", "cle": "v1/arome/2026-10-17T06Z/wind10/48.pmtiles", "octets": 3548226, "tuiles": 142,
                "minzoom": 3, "maxzoom": 7, "encoding": "wind8", "scale": "linear", "unit": "m/s", "min": -48.0, "max": 48.0,
                "quant_step": 0.3779527559055118, "components": ["wind10_u", "wind10_v"] }
  },
  "isobares": { "cle": "v1/arome/2026-10-17T06Z/_isobares/48.npz", "octets": 727136, "variable": "mslp", "unite": "Pa" },
  "domaine": null,
  "sources": [ "arome__0025__SP1__43H48H__2026-10-17T06:00:00Z.grib2", "arome__0025__SP2__43H48H__2026-10-17T06:00:00Z.grib2" ],
  "mesures": { "telechargement_s": 0.04, "decodage_grib_s": 0.09, "tuilage_s": 0.13, "ecriture_s": 0.32, "total_s": 0.61, "fils": 20, "…": "…" }
}
```
(Valeurs relevées sur la sortie réelle ; la liste des variables et `mesures` sont abrégées.)

Correspondance avec le manifeste actuel (`pipeline/manifest/build.py`) :

| Manifeste | Obtenu par agrégation des états `complete` |
|---|---|
| `models.<m>.runs[].id` / `run` / `path` | `tirage` / `run` / `{modèle}/{tirage}` |
| `runs[].variables.<v>.steps` | échéances `echeance` des états où `variables.<v>` existe, triées ; `complete` = liste égale à `première échéance..horizon_h` (règle inchangée, `PREMIERE_ECHEANCE`) |
| `runs[].valid_debut` / `valid_fin` | `valide` minimal / maximal |
| `runs[].traite` | `traite` maximal (remplace la date de modification du fichier) |
| `zooms.<v>` | `variables.<v>.minzoom`, `maxzoom` (état du tirage le plus récent) |
| `variables.<v>` (`encoding`, `scale`, `unit`, `min`, `max`, `quant_step`, `components`) | mêmes clés dans l'état |
| `models.<m>.domain.polygon` | **non fourni par l'état** : calculé à partir de `_domaine/03.npz` (variable `t2m`, mêmes tableaux `lat`, `lon`, `t2m` que `polygone_depuis_masque`) ; non porté en Rust |
| `file_template` | `chemin` d'une variable = `{modèle}/{tirage}/{variable}/{NN}.pmtiles` (champ `chemin`) |

Déclenchement conseillé de la Lambda « manifeste » : événement S3 sur le suffixe **`/_etat/*.json`** (un seul événement par échéance, émis après l'écriture de tous les fichiers de l'échéance), et non sur `.pmtiles` (huit événements par échéance, dont les sept premiers arrivent avant que l'échéance soit publiable). Le manifeste reste le dernier `PUT`, comme prévu à l'architecture. Cette fonction n'est pas écrite ici.

## 8. Source des isobares

`_isobares/NN.npz` (AROME, toutes échéances) est le champ canonique `mslp` en pascals avec `lat`, `lon` et `meta` (`model`, `run`, `step`, …) : exactement l'entrée de `pipeline.isobars.calcul.charger`. **Vérification [M]** : `pipeline.isobars.calcul.traiter` exécuté sur ce fichier (échéance 48) et sur le canonique Python de référence produit des GeoJSON **identiques octet pour octet** (30 lignes, 1 extremum, 93 442 octets). Le canonique `mslp` lui-même est identique bit à bit au Python.

**Le calcul des isobares n'est pas porté en Rust.** Il représente environ 1 000 lignes de Python numérique : lissage gaussien séparable (σ = 4 et σ = 8 mailles), contours par marching squares avec assemblage des segments en lignes, simplification de Douglas-Peucker en kilomètres, détection des extrema (proéminence), puis conversion en tuiles vectorielles MVT avec découpe et PMTiles (`mvt.py`, 267 lignes) et `validation.py`. De plus, le format de production des isobares (GeoJSON ou tuiles MVT dans un PMTiles) n'est pas arrêté : [../poc-j3/isobares.md](../poc-j3/isobares.md) l'évalue seulement. Tant que ce choix et ce port ne sont pas faits, le Python peut tourner sur `_isobares/NN.npz` hors de cette fonction.

## 9. Mesures et exactitude

Machine de développement : x86_64, 20 cœurs partagés, source et S3 simulés en boucle locale (donc **téléchargement et PUT quasi gratuits** : ce ne sont pas des durées Lambda).

| Mesure | Valeur |
|---|---|
| Binaire `bootstrap` arm64 (`aarch64-unknown-linux-musl`, statique, strippé, LTO) | **2,82 Mo** ; zip 1,50 Mo **[M]** |
| Binaire x86_64 glibc / x86_64 musl | 3,26 Mo / 3,40 Mo (zip musl 1,61 Mo) **[M]** |
| Démarrage x86_64 (processus lancé, config lue, message invalide refusé ; médiane de 200, depuis Python) | 3,6 ms (p95 4,1 ms) **[M]** ; arm64 non exécuté (pas de qemu) |
| HD échéance 12 ou 24, 1 fil, musl | 2,5 s (GRIB 1,1 s, tuilage 1,35 s) ; 2 fils 1,7 s ; 4 fils 1,0 s **[M]** ; glibc 1 fil 2,0 s |
| AROME échéance 24, 1 fil, musl | 1,2 s (GRIB 0,2 s, tuilage 0,9 s) ; 2 fils 0,7 s **[M]** |
| 20 fils (HD 12, glibc) | 0,44 s **[M]** |
| Pic mémoire (RSS) | HD 616 Mo ; AROME 187 Mo **[M]** |
| Poids publié par échéance | HD ≈ 13 à 15 Mo ; AROME ≈ 12 Mo **[M]** |
| Source réelle (OVH, TLS en Rust pur), HD échéance 12 et 24, une fois chacune | 86 Mo téléchargés en **7,7 à 7,9 s** (3 connexions, ≈ 11 Mo/s depuis ce poste, limité par la liaison locale) ; total 9,3 à 9,5 s à 2 fils **[M]** ; sortie octet pour octet identique au PoC **[M]** |
| Durée d'un rejeu idempotent | ≈ 50 à 70 ms (10 requêtes HEAD/GET sur le faux S3) **[M]** |

Les durées de calcul ci-dessus sont proches de l'estimation de l'architecture pour `image-webp` (≈ 2 s en HD à 1 fil) ; le surcoût du `musl` par rapport à glibc est d'environ **+20 %** (allocateur de musl), à confirmer sur arm64.

**Exactitude [M, `rust/tools/verifier_lambda.sh`].** 10 échéances (HD du tirage 03Z et AROME du tirage 06Z, échéances 0, 12, 24, 36, 48), traitées à travers HTTP, SigV4 et le faux S3, comparées au Python de référence (`out/rust/py`) :

- **72 fichiers PMTiles** comparés par `cmp_tiles.py` : **100,0000 % de pixels de code identique, écart maximal 0 code**, métadonnées et en-têtes identiques, fichiers relus par le décodeur du dépôt ;
- **champs canoniques** (`RW_ECRIRE_CANON=1`) comparés par `cmp_canon.py` : **identiques bit à bit** pour toutes les variables et les axes ;
- les fichiers sont de plus **identiques octet pour octet** à la sortie `rw run --webp image-webp` du PoC [T], y compris avec le binaire `musl` x86_64 (16 fichiers, échéance 48) ;
- l'écart d'exactitude **sur arm64 (libm de musl arm64) reste non vérifié**.

Le binaire a aussi été exercé avec la vraie bibliothèque `lambda_runtime` contre un faux API Runtime (`essai_runtime_lambda.py`) : trois invocations, réponses `batchItemFailures` attendues (vide, un message rejoué sur 404, vide au rejeu) **[M]**.

## 10. Politique IAM minimale et réglages recommandés

### 10.1 Rôle d'exécution du worker (exemple, à adapter : remplacer `BUCKET`, `REGION`, `COMPTE`, `FILE`, `FONCTION`)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "LireLaFileDesTaches",
      "Effect": "Allow",
      "Action": ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"],
      "Resource": "arn:aws:sqs:REGION:COMPTE:FILE"
    },
    {
      "Sid": "EcrireEtRelireLesSorties",
      "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:GetObject"],
      "Resource": [
        "arn:aws:s3:::BUCKET/v1/arome/*",
        "arn:aws:s3:::BUCKET/v1/arome-hd/*"
      ]
    },
    {
      "Sid": "DistinguerAbsentEtInterdit",
      "Effect": "Allow",
      "Action": "s3:ListBucket",
      "Resource": "arn:aws:s3:::BUCKET"
    },
    {
      "Sid": "Journaux",
      "Effect": "Allow",
      "Action": ["logs:CreateLogStream", "logs:PutLogEvents"],
      "Resource": "arn:aws:logs:REGION:COMPTE:log-group:/aws/lambda/FONCTION:*"
    }
  ]
}
```

Notes :

- Pas de `s3:DeleteObject`, pas de DynamoDB : l'état est dans S3 (la « variante sans nouveau service » de [../architecture-aws.md](../architecture-aws.md) §3.1). C'est un écart avec le §6 de ce document (« aucune lecture S3 »), nécessaire à l'idempotence : `GetObject` couvre aussi `HeadObject`.
- `s3:ListBucket` sert uniquement à ce que S3 réponde **404** (objet absent) au lieu de **403** lorsque le rôle n'a pas le droit de lister ; sans lui, la première lecture de l'état d'une échéance neuve échoue en 403 (classé transitoire : le message reste en file). Je ne l'ai pas restreint par `s3:prefix`, car je n'ai pas vérifié que cette condition s'applique à ce contrôle **[N]**.
- `logs:CreateLogGroup` est omis : créer le groupe de journaux à l'avance (rétention courte, 14 jours dans l'architecture).
- Aucun accès à Internet n'est nécessaire en dehors de la source OVH et de S3 ; fonction **hors VPC** (pas de NAT).
- La Lambda « manifeste » et le « poller » ont leurs propres rôles (§6 de l'architecture) ; ils ne font pas partie de ce binaire.

### 10.2 Réglages de la fonction et de la file

| Réglage | Valeur proposée | Raison |
|---|---|---|
| Runtime / architecture | `provided.al2023`, `arm64`, handler sans objet | binaire `bootstrap` à la racine du zip |
| Mémoire | 1 769 Mo (1 vCPU) pour l'AROME ; 1 769 à 2 048 Mo pour le HD | pic 616 Mo HD ; au-delà de 1 769 Mo, plus d'un vCPU et `RW_THREADS` suit le nombre de cœurs vus |
| Délai | 120 s | durée attendue 3 à 15 s ; large marge pour la lenteur de la source |
| Mapping SQS | `BatchSize: 1`, `FunctionResponseTypes: ["ReportBatchItemFailures"]`, `ScalingConfig.MaximumConcurrency: 10` | partial batch failure ; politesse envers OVH (10 × 3 connexions au plus) |
| Délai de visibilité | ≥ 6 × le délai de la fonction (720 s) | recommandation AWS pour SQS + Lambda |
| DLQ | `maxReceiveCount` 5 ; alarme sur profondeur ≥ 1 | échecs répétés (404 persistant, 403) |
| Variables | `RW_BUCKET`, `RW_PREFIXE=v1` ; les autres par défaut | §4 |
| Journaux | retention 14 jours ; filtre de métrique sur `"classe":"definitive"` | seule trace d'un échec définitif acquitté |

Coût de calcul indicatif **[C, non mesuré sur Lambda]** : par tirage, ≈ 52 × (≈ 3,5 s HD + ≈ 1,5 s AROME) × 1,727 Go ≈ 450 Go-s, contre ≈ 4 240 Go-s estimés pour le Python dans l'architecture (§10.2) ; les durées de téléchargement depuis Lambda sont inconnues (elles dominent probablement).

## 11. Emballage

```sh
rustup target add aarch64-unknown-linux-musl     # une seule fois (déjà fait ici)
rust/tools/package-lambda.sh                     # arm64 → rust/target/lambda/rw-lambda-arm64.zip
rust/tools/package-lambda.sh x86_64              # x86_64 musl, pour comparer / tester
```

Le script exécute `RUSTFLAGS="-C linker=rust-lld" cargo build --release --locked -p rw-lambda --bin bootstrap --target aarch64-unknown-linux-musl`, installe `bootstrap` en mode 0755 à la racine d'un zip sans horodatage (le zip est reproductible : deux exécutions donnent la même somme SHA-256) et affiche la taille et la somme. **Il n'envoie rien.** Aucun compilateur C croisé, ni `cargo-lambda` ni zig, n'est requis ni installé. À ne pas oublier : compiler avec `-p rw-lambda` ; un `cargo build` à la racine de l'espace de travail active aussi `libwebp` (C) pour `rw-cli` par unification des fonctionnalités, ce qui casserait la compilation arm64.

Déploiement futur (non fait) : `aws lambda update-function-code --zip-file fileb://rust/target/lambda/rw-lambda-arm64.zip` ou équivalent infrastructure-as-code.

## 12. Vérification en local

```sh
cd rust && cargo test --release -p rw-lambda          # 10 unitaires + 9 d'intégration (serveur Python local lancé et arrêté par le test, par PID)
PYLIB_PMTILES=…/pylib2 rust/tools/verifier_lambda.sh  # dix échéances, tuiles et champs contre le Python (≈ 30 s)
```

Les tests d'intégration couvrent : HD de bout en bout par S3 simulé (signature vérifiée par un second code indépendant, en-têtes `Cache-Control` / `Content-Type` posés, état écrit en dernier), octets identiques au PoC, rejeu idempotent sans requête GRIB ni PUT, fichier manquant qui force le retraitement, `RW_FORCER` ; AROME avec la source des isobares ; échéance 7 qui demande le groupe précédent, échéances 12 et 0 qui n'en demandent pas ; **404 → `batchItemFailures`** (et message voisin du même lot acquitté) sans aucun état écrit ; reprise par `Range` après coupure ; 503 S3 absorbés par les essais, signature fausse (403) refusée ; erreurs définitives (message mal formé, GRIB corrompu avec état `echec_definitif`) acquittées par défaut et rejouées avec `RW_DEFINITIF=rejouer`.

## 13. Reste à faire avant de déployer

1. **Infrastructure** (rien n'existe) : bucket, file SQS + DLQ, fonction, mapping, rôles, journaux, règle de cycle de vie, comportements CloudFront excluant `_*`.
2. **Poller** qui liste le S3 OVH et poste les messages `{modele, tirage, echeance}` (une fois les fichiers visibles), et **Lambda « manifeste »** qui agrège les états (§7), calcule le polygone du domaine à partir de `_domaine/03.npz` (Python existant ou port) et écrit le manifeste en dernier.
3. **Tester sur un compte réel** avec un bucket jetable : la pile TLS en Rust pur face aux points de terminaison S3 d'AWS (chaîne de certificats Amazon, suites de chiffrement), `ListBucket`/404, durée de téléchargement depuis Lambda, mémoire réelle, démarrage à froid.
4. **Exécuter `verifier_lambda.sh` sur arm64** (instance Graviton ou Lambda) pour lever le doute sur le libm de musl arm64 (±1 code) ; l'exactitude n'est démontrée ici que sur x86_64 (glibc et musl).
5. Décisions ouvertes de l'architecture : révision du pipeline dans le préfixe (immuabilité), rétention, valeur de `MaximumConcurrency`, état S3 ou DynamoDB.
6. Porter ou conserver en Python le calcul des isobares, et arrêter leur format (§8).
7. Mesurer le coût du `musl` (allocateur) sur arm64 et décider d'un allocateur alternatif si l'écart de +20 % se confirme.
8. Évaluer la réduction des téléchargements redondants (une heure HD téléchargée deux fois, un groupe AROME jusqu'à six fois) si le temps de transfert se révèle dominant.

## 14. Risques

| Risque | Gravité | Remarque |
|---|---|---|
| `rustls-rustcrypto` 0.0.2-alpha (cryptographie non auditée, version alpha) | moyenne | utilisée côté client vers des services publics ; secrets SigV4 jamais transmis (seulement signés, en HMAC-SHA256 de RustCrypto, bibliothèque mûre). Repli : `rustls` + `aws-lc-rs` (exige zig / un compilateur C croisé). Compatibilité avec S3 non testée. |
| Débit TLS en Rust pur sur arm64 | moyenne | 86 Mo en 7,7 s mesurés depuis un poste, limité par le réseau ; le coût du déchiffrement sur Graviton n'est pas mesuré. |
| Exactitude du libm sur arm64 | faible | déjà signalé dans [synthese.md](synthese.md) §5 ; test prévu au §13. |
| Rejouabilité des 404 | moyenne | cadence fixée par le délai de visibilité ; un tirage publié tard consomme des réceptions (DLQ). Le poller n'envoie qu'après avoir vu le fichier dans le listage, ce qui limite le cas. |
| Erreurs définitives acquittées | moyenne | invisibles hors journal et état ; alarme sur le filtre de métrique, ou `RW_DEFINITIF=rejouer`. |
| État dans S3 sans transaction | faible | deux livraisons simultanées retraitent deux fois (mêmes octets) ; coût négligeable. |
| Immuabilité d'un an | moyenne | une échéance corrigée n'est visible qu'avec une nouvelle révision de préfixe ou une invalidation. |
| Charge sur OVH | faible à moyenne | `MaximumConcurrency` × `RW_CONNEXIONS` connexions, User-Agent identifiable avec contact. |
| Mémoire | faible | pic 616 Mo ; un AROME en début de groupe lit ≈ 200 Mo de GRIB. |
| Fichier GRIB incomplet côté source | faible | S3 écrit les objets de façon atomique ; la taille reçue est contrôlée ; un fichier atypique mais complet serait une erreur définitive. |
| Le S3 de l'hébergeur répond 403 au lieu de 404 pour un fichier absent | faible | vérifié pour OVH (404 constaté sur un tirage futur) ; classé de toute façon transitoire. |
