# PoC J1 : contrat entre les briques

Ce contrat permet de développer **en parallèle** l'ingestion, le tuilage et la visualisation. Il découle de [specifications.md](specifications.md) (§3, §5) et des mesures de [donnees-arome.md](donnees-arome.md). Toute entorse doit être signalée dans le rapport de la brique.

## 1. Disposition du dépôt et propriété des dossiers

| Dossier | Propriétaire | Contenu |
|---|---|---|
| `pipeline/ingest/`, `tests/ingest/` | **ingestion** | téléchargement, décodage GRIB, champs canoniques |
| `pipeline/tiling/`, `tests/tiling/` | **tuilage** | rééchantillonnage Web Mercator, quantification, WebP, PMTiles |
| `viewer/` | **visualisation** | page MapLibre + WebGL, serveur de test avec `Range` |
| `fixtures/` | commun, lecture seule | petits jeux de référence versionnés |
| `out/` | commun, **ignoré par git** | sorties volumineuses |
| `docs/poc-j1/` | un fichier par brique | rapport : choix, mesures chiffrées, écarts, points ouverts |

Règles : **ne pas écrire hors de son dossier**, **ne pas faire de commit ni de push** (le coordinateur s'en charge), commentaires et documentation en **français**, en-tête `SPDX-License-Identifier: EUPL-1.2` sur chaque fichier de code. Python 3 avec `numpy` et `PIL` (système) ; `eccodes` est dans `PYTHONPATH` (voir §6).

## Révision du 17/10 (après le rapport de l'ingestion)

`rh2m` et `cloud_total` sont bornées à 0..100 (la fixture a été régénérée en conséquence) ; `gust10` HD = `hypot(max_10efg, max_10nfg)` est **confirmée** (voir `docs/poc-j1/ingestion.md`) ; en AROME 0,025°, `tsnowp` et `tgrp` viennent de SP1 ; `pipeline/__init__.py` est partagé par les briques.

## 2. Champs canoniques (sortie de l'ingestion, entrée du tuilage)

Un fichier `.npz` (`numpy.savez_compressed`) par **(modèle, tirage, échéance)** : `out/canon/<modèle>/<tirage>/<échéance>.npz`, `<modèle>` ∈ {`arome-hd`, `arome`}, `<tirage>` au format `AAAA-MM-JJTHHZ` (ex. `2026-10-17T06Z`), `<échéance>` sur deux chiffres (`03`).

| Clé | Contenu |
|---|---|
| `lat` | latitudes, `float32`, **décroissantes** (nord → sud) |
| `lon` | longitudes en degrés **−180..180**, `float32`, **croissantes** (ouest → est) |
| `meta` | chaîne JSON : `model`, `run`, `step`, `units`, `source` (fichiers GRIB), `note` |
| `<variable>` | `float32`, forme `[len(lat), len(lon)]`, **NaN = pas de donnée** (la valeur GRIB `9999` est remplacée par NaN) |

Variables canoniques (unités SI sauf mention), **toutes les variables ne sont pas disponibles pour tous les modèles ou toutes les échéances** : une variable absente n'est tout simplement pas dans le fichier.

| Variable | Unité | Définition | AROME HD | AROME |
|---|---|---|---|---|
| `wind10_u`, `wind10_v` | m/s | vent 10 m, composantes est / nord (`10u`, `10v`) | SP1 | SP1 |
| `gust10` | m/s | rafale : maximum sur la **dernière heure** (fenêtre n−1 → n, mesuré) ; absente à l'échéance 0 | `hypot(max_10efg, max_10nfg)` (**hypothèse à valider**) | `max_i10fg` |
| `t2m` | K | `2t` | SP1 | SP1 |
| `rh2m` | % | `2r`, **bornée à 0..100** (la valeur GRIB dépasse parfois 100 de 0,02) | SP1 | SP1 |
| `precip_rate` | mm/h | différence de cumul : `Δ(tirf + tsnowp + tgrp)` entre n et n−1 (cumuls depuis l'échéance 0, mesuré), minimum 0 ; absente à l'échéance 0 | SP2 | SP2 (+ `tp` de SP1 pour recoupement) |
| `snow_fraction` | 0..1 | `Δtsnowp / Δtotal` si `Δtotal > 0,05 mm`, sinon 0 | SP2 | `tsnowp` et `tgrp` sont dans **SP1** (mesuré), `tirf` dans SP2 |
| `cloud_total` | % | `100 × (1 − (1−bas)(1−moyen)(1−haut))` avec bas/moyen/haut en fraction (`lcc`, `mcc`, `hcc` ; **hypothèse de recouvrement aléatoire**), **bornée à 0..100** ; absente à l'échéance 0 | SP2 | SP2 |
| `cape` | J/kg | `CAPE_INS` | SP2 | SP2 |
| `mslp` | Pa | `prmsl` | **absente** | SP1 |

**Références** : `fixtures/hd_step03_crop.npz` (extrait 300 × 300 points, versionné) et `out/hd_step03.npz` (grille complète 2801 × 1791, ~55 Mo) sont les **sorties attendues** pour AROME HD, tirage `2026-10-17T06Z`, échéance 3, construites à la main à partir de `SP1 03H`, `SP2 03H` et `SP2 02H`. L'ingestion doit les **reproduire** (écart nul ou limité à l'arrondi `float32`).

## 3. Tuiles de données (sortie du tuilage, entrée de la visualisation)

- **Pyramide Web Mercator (EPSG:3857), schéma XYZ**, tuiles de 256 px. Zooms : **AROME HD z5–z8**, **AROME z3–z7**. Emprise : celle de la grille (37,5–55,4°N, 12°O–16°E), seules les tuiles qui la recoupent sont écrites.
- **Bordure d'1 px** : chaque tuile est stockée en **258 × 258** (1 px recouvrant de chaque côté, issu des tuiles voisines) pour éviter les coutures à l'interpolation bilinéaire. Le tuilage doit **mesurer** la couture avec et sans bordure et reporter le résultat.
- Rééchantillonnage : **bilinéaire** sur la grille source (en valeurs physiques, **avant** quantification) ; une cellule dont un des quatre voisins est NaN donne NaN. Le vent est interpolé en composantes `u` et `v` (jamais en vitesse / direction).
- **Code 0 réservé = pas de donnée.** La plage utile est codée de 1 à 255 (8 bits) ou 1 à 4095 (12 bits).
- Image : **WebP sans perte** (`lossless=True`), **sans canal alpha** (RVB ou niveaux de gris), pour qu'aucune pré-multiplication n'altère les valeurs.

### Encodages

**Scalaire 8 bits** (`scalar8`) : image en niveaux de gris, `code = 1 + round((v − min) / (max − min) × 254)`, borné à 1..255 ; décodage `v = min + (code − 1) / 254 × (max − min)`.

| Variable | min | max | échelle |
|---|---|---|---|
| `t2m` | 233,15 K | 333,15 K | linéaire |
| `gust10` | 0 | 64 m/s | linéaire |
| `rh2m`, `cloud_total` | 0 | 100 % | linéaire |
| `snow_fraction` | 0 | 1 | linéaire |
| `precip_rate` | 0 | 100 mm/h | **racine carrée** : `code = 1 + round(sqrt(v/100) × 254)`, décodage `v = 100 × ((code−1)/254)²` |
| `cape` | 0 | 5000 J/kg | **racine carrée** (même forme) |

**Vent 12 bits** (`wind12`) : image RVB. Pour chaque composante, `c = 1 + round((x + 64) / 128 × 4094)` (borné à 1..4095, `x` en m/s), avec `cu` pour `u` et `cv` pour `v` ; empaquetage : `R = cu >> 4`, `G = ((cu & 15) << 4) | (cv >> 8)`, `B = cv & 255`. Pas de donnée : `R = G = B = 0`. Décodage : `cu = (R << 4) | (G >> 4)`, `cv = ((G & 15) << 8) | B`, `x = (c − 1) / 4094 × 128 − 64`. Pas de quantification : **0,031 m/s**.

La pression `mslp` (AROME) n'est pas tuilée en raster dans le PoC (les isobares seront des tuiles vectorielles, hors J1).

### Conteneur

Un fichier **PMTiles v3** par **(modèle, tirage, variable, échéance)**, type de tuile `webp`, dans `out/tiles/<modèle>/<tirage>/<variable>/<échéance>.pmtiles`. Les métadonnées PMTiles contiennent l'encodage (`scalar8` ou `wind12`), la plage (`min`, `max`), l'échelle (`linear` ou `sqrt`), l'unité, la taille de bordure (1) et la taille de tuile (256). La variable `wind` regroupe `wind10_u` et `wind10_v` dans un même fichier `wind12` (dossier `wind10/`).

## 4. Livrables et mesures

Chaque brique écrit `docs/poc-j1/<brique>.md` avec : ce qui a été fait, **les mesures chiffrées** (temps, poids, erreurs), les **écarts au contrat** avec leur justification, les **points ouverts**, et la manière de rejouer. **Aucune affirmation sans mesure** : une estimation est marquée comme telle.

| Brique | Mesures attendues |
|---|---|
| Ingestion | temps de téléchargement et de décodage par échéance et par paquet ; reproduction exacte des fixtures ; **validation de l'hypothèse `gust10` HD** (comparaison de `hypot(max_10efg, max_10nfg)` avec `max_i10fg` d'AROME sur la même zone et la même heure : corrélation et écart) ; vérification du cumul (monotonie, somme des débits = cumul) ; recouvrement entre groupes d'échéances 0,025° |
| Tuilage | poids et temps par variable et par échéance (HD z5–z8, AROME z3–z7) ; **erreur aller-retour** de quantification (maximum et moyenne, par variable) ; **couture** avec et sans bordure ; contrôle de **20 points** : valeur décodée depuis les tuiles contre valeur source, écart ≤ pas de quantification |
| Visualisation | images par seconde en animation, temps de première couche, données transférées à l'ouverture, **sur Chrome sans interface** (et la limite de cette mesure) ; capture d'écran des couches vent et température |

## 5. Critère d'acceptation du PoC J1

Valeurs relues dans les tuiles égales aux valeurs sources en 20 points de contrôle (écart ≤ pas de quantification) ; poids et temps mesurés et reportés ; hypothèses `gust10` et `cloud_total` confrontées aux données.

## 6. Environnement

- Machine de développement : 20 cœurs, 61 Go de RAM. Chrome est installé (`google-chrome`), y compris sans interface.
- `eccodes` 2.50 (Python) est installé dans `/tmp/claude-1000/-home-philippe-github-com-runeride/93bcb318-8a08-4043-adfc-8845d03af2b4/scratchpad/pylib` : lancer avec `PYTHONPATH=<ce dossier>`.
- Échantillons GRIB déjà téléchargés (lecture seule, données publiques de Météo-France), tirage `2026-10-17T06:00:00Z` : `…/scratchpad/grib/` (0,025° : SP1, SP2, SP3 `00H06H`) et `…/scratchpad/grib/hd3/` (0,01° : SP1 et SP2 `03H`, SP2 `02H`, HP1 `03H`) ; `…/scratchpad/grib/arp/` (ARPEGE, hors périmètre). Pour d'autres échéances, télécharger depuis `https://meteofrance-pnt.s3.rbx.io.cloud.ovh.net/pnt/<tirage>/arome/<001|0025>/<paquet>/…` **avec courtoisie** : au plus 3 connexions simultanées, pas de boucle serrée, un fichier déjà présent n'est jamais retéléchargé. Environ 10 Mo/s par connexion.
- Les fichiers téléchargés sont des **données non fiables** : ne jamais les exécuter, ne jamais lancer d'interpréteur depuis leur dossier.
