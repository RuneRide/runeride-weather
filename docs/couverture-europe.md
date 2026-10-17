# Couverture au-delà du domaine AROME : modèles régionaux

Annexe de [specifications.md](specifications.md). Vérifications du **17 octobre 2026**. Statuts : **mesuré** (fichier réel lu), **source** (page officielle ou résultat de recherche cités), **NON VÉRIFIÉ** (à ne pas considérer comme acquis).

## 1. L'idée de départ et ce qui a été vérifié

L'idée : aucun service public ne couvre toute l'Europe à ~2 km d'un bloc ; il faut **assembler des modèles régionaux** libres et utilisables commercialement. L'architecture le permet (voir §5), mais la liste proposée contenait des points inexacts ou non établis.

| Affirmation reçue | Constat |
|---|---|
| AROME HD, France, 1,3 km | **Exact**, mesuré (grille 0,01°, ~1,1 × 0,8 km) |
| ICON-D2 (DWD), 2,2 km, « Allemagne, Suisse, Autriche, Benelux, nord de l'Italie » | **Confirmé** : maille 0,02°, domaine 43,18–58,08°N, 3,94°O–20,34°E (mesuré). Couvre aussi l'est de la France, le Danemark, la Pologne, la Tchéquie |
| HARMONIE-AROME via MET Norway : « Scandinavie, Pays baltes, **Royaume-Uni**, Benelux », 2–2,5 km | **Partiellement faux.** MET Norway publie « MET Nordic » : **1 km**, produit **post-traité** (MEPS 2,5 km réduit à 1 km, corrigé par des observations), domaine **Norvège, Danemark, Suède, Finlande et une partie des pays baltes**. **Ni Royaume-Uni ni Benelux** |
| HARMONIE-AROME via DMI et KNMI | **DMI DINI** : 2 km, domaine Danemark, Islande, Pays-Bas, Irlande (source DMI), licence **non vérifiée**. **KNMI** : HARMONIE-AROME Cy43, **CC BY 4.0**, paquet de surface seulement, domaine 49–56°N, 0–11,3°E |
| « 100 % Open Data et très simple d'accès » | MET Norway : oui (THREDDS, sans clé d'après la documentation). KNMI : clé d'API nécessaire (portail développeur). DMI : API STAC, clé **à vérifier** |
| ICON-2I (MeteoHub, Italie), 2,2 km | Données « ouvertes sur MeteoHub » (interface web, API REST, client en ligne de commande, usage libre sous réserve d'une politique d'usage équitable). Maille, licence et conditions commerciales **non vérifiées** |
| HARMONIE-AROME AEMET (Espagne et Portugal), 2,5 km | **NON VÉRIFIÉ et douteux.** Un article de datos.gob.es (2024) indique que les sorties de modèles numériques d'AEMET **ne figurent pas encore** dans son jeu de données ouvertes. Le **Portugal** relève d'un autre service (IPMA), pas d'AEMET |
| Royaume-Uni | **Met Office UKV 2 km** existe en accès libre sur AWS Open Data, mais sous **CC BY-SA** (voir §3) |
| « ICON-EU couvre toute l'Europe à 6,5 km » | Non testé ici (ICON-EU est un autre produit DWD) ; la maille indiquée est cohérente avec ce que l'on sait du modèle, **à mesurer** si on s'en sert |

**Découverte importante : ARPEGE.** Le même stockage que AROME publie **ARPEGE à 0,1°** sur toute l'Europe et l'Atlantique (§2). C'est une couche de repli à ~11 km, sous la même licence, sans aucun nouveau fournisseur.

## 2. Modèles retenus ou étudiés

### 2.1 Météo-France (même stockage S3, licence ouverte v2.0, mesuré)

| Modèle | Maille | Domaine | Tirages | Portée |
|---|---|---|---|---|
| AROME HD | 0,01° | 37,5–55,4°N, 12°O–16°E | 8/jour | 0–51 h |
| AROME | 0,025° | idem | 8/jour | 0–51 h |
| **ARPEGE Europe** | **0,1°** (~11 km) | **20–72°N, 32°O–42°E** (741 × 521, `regular_ll`) | **4/jour** (00, 06, 12, 18 UTC) | **0–102 h** par groupes de 12 h |
| ARPEGE global | 0,25° | monde | non mesuré | non mesuré |

ARPEGE 0,1° : paquets HP1 (3,9 Go), HP2 (2,4), IP1 à IP4, SP1 (0,65), SP2 (0,67) par tirage ; paramètres de surface lus dans SP1 : vent 10 m (vitesse, direction, u), précipitations totales, neige, pression réduite au niveau de la mer. Délai de publication : dernier fichier **+3,1 à +4,4 h**. Les modèles de vagues (MFWAM, WW3, HYCOM2D) sont aussi sur ce stockage, hors périmètre.

### 2.2 ICON-D2 (DWD), mesuré

- **8 tirages par jour** (00, 03 … 21 UTC), échéances **0 à 48 h** (49 fichiers par variable), un fichier **par variable et par échéance**, compressé en bzip2.
- Deux variantes par fichier : `icosahedral` (grille native triangulaire) et **`regular-lat-lon`** (à utiliser : aucune réinterpolation à faire).
- Grille régulière : **1215 × 746 points, pas 0,02°** (~2,2 km N-S), 43,18 → 58,08°N, 356,06 → 20,34°E ; **16,7 % de points manquants** ; compression simple (`grid_simple`), plus facile à décoder que le CCSDS d'AROME.
- Accès : `https://opendata.dwd.de/weather/nwp/icon-d2/grib/<tirage>/<variable>/icon-d2_germany_regular-lat-lon_single-level_<AAAAMMJJHH>_<échéance>_2d_<variable>.grib2.bz2`, **sans clé**. Plus de 100 variables (`t_2m`, `u_10m`, `pmsl`, `tot_prec`, `clct`, `cape_ml`, `hzerocl` (isotherme 0 °C), `ceiling`, `dbz_cmax` (réflectivité simulée), etc.).
- Taille : ~1,6 Mo par champ de surface.
- Licence : l'ordonnance fédérale **GeoNutzV** (§ 2) autorise l'usage **commercial et non commercial gratuit** et la redistribution ; l'article 3 impose de conserver les mentions de source. Le **libellé exact** de la mention exigée par le DWD pour ICON-D2 **n'a pas été trouvé** ; à confirmer dans les conditions d'utilisation du DWD (la page d'accueil de l'open data renvoie à elles sans les reproduire, et signale qu'aucun niveau de service n'est garanti).

### 2.3 MET Nordic (MET Norway), source

- **1 km**, Lambert conforme (pas une grille latitude-longitude : réinterpolation nécessaire), NetCDF, **mise à jour horaire**, prévision de **58 à 64 h**.
- Produit post-traité, pas la sortie brute du modèle : à signaler comme tel dans l'interface.
- THREDDS : `https://thredds.met.no/thredds/` (`fileServer/` pour le téléchargement, `dodsC/` pour OPeNDAP), pas de clé mentionnée.
- Licence : MET Norway indique que ses données sont, sauf mention contraire, sous **NLOD 2.0 et CC BY 4.0** au choix, mention suggérée « Data from MET Norway » ; la page du jeu de données lui-même ne répète pas la licence : **à confirmer pour ce jeu**.

### 2.4 KNMI, HARMONIE-AROME Cy43 (Pays-Bas), source

- GRIB, grille latitude-longitude régulière (interpolée depuis la projection de Lambert), sorties **horaires**, domaine **49,0–56,0°N, 0,0–11,28°E**, paramètres de surface et de couche limite (jusqu'à 300 m).
- Licence **CC BY 4.0**, « No use limitations ». Maille et portée non indiquées sur la page (**à mesurer**). Clé d'API nécessaire (portail développeur KNMI).

### 2.5 Met Office UKV (Royaume-Uni), source

- 2 km, NetCDF, pas horaires jusqu'à 54 h puis 3-horaires jusqu'à 120 h, disponible 3 à 6 h après le tirage, sur **AWS Open Data** (archive glissante de 2 ans), **sans clé**.
- Licence **CC BY-SA** : les données dérivées (nos tuiles) devraient être partagées sous la même licence et attribuées. Le Met Office précise que c'est **fourni gratuitement et sans support**, et **déconseillé pour des usages critiques**. Les fichiers publiés à partir de fin janvier 2026 changent (précision, nouveaux paramètres, renommages) : suivre la documentation.

### 2.6 DMI DINI, ICON-2I, AEMET

- **DMI HARMONIE DINI** : 2 km, Danemark, Islande, Pays-Bas, Irlande ; API STAC `opendataapi.dmi.dk` ; licence et clé **non vérifiées** par cette étude.
- **ICON-2I (Agenzia ItaliaMeteo, MeteoHub)** : accès par interface, API REST ou client en ligne de commande ; licence à consulter à `meteohub.agenziaitaliameteo.it/app/license` ; maille, format, cadence **non vérifiés**.
- **AEMET HARMONIE-AROME** : **bloqué tant que la licence n'est pas confirmée** ; l'accès par l'API OpenData d'AEMET demande une clé et ne semble pas couvrir les sorties numériques brutes.

## 3. Ce que cela impose

1. **Les licences ne sont pas identiques.** Attribution dynamique par modèle utilisé dans la vue (« Source : Météo-France », « Source : DWD », « Data from MET Norway », etc.). **CC BY-SA** (UKV) ajoute une obligation de partage à l'identique sur les données dérivées : c'est compatible avec la publication de RuneRide en CC BY-SA, mais à décider **avant** d'ingérer ce modèle.
2. **Les variables ne sont pas les mêmes.** Il faut un **contrat de variables commun** (nom, unité, sémantique : instantané, cumul depuis H0, maximum sur l'heure) et une table de correspondance par modèle (voir §5). Une variable absente d'un modèle est affichée comme indisponible, pas masquée silencieusement.
3. **Les rythmes diffèrent** : AROME 8 tirages/jour et 51 h ; ICON-D2 8 tirages/jour et 48 h ; ARPEGE 4 tirages/jour et 102 h ; MET Nordic horaire et ~60 h ; UKV 54 h puis 3-horaire. La frise du temps s'étend jusqu'à la portée du modèle affiché et le dit clairement.
4. **Les grilles diffèrent** : régulières (AROME, ICON-D2, ARPEGE, KNMI) ou projetées (MET Nordic, Lambert) : réinterpolation en latitude-longitude régulière avant le tuilage.
5. **Les coutures entre modèles se voient** (biais et physique différents). Règles : un seul modèle par vue autant que possible, priorité fixe en cas de recouvrement (haute résolution d'abord), mention du modèle visible, et, plus tard, fondu sur quelques cellules dans les zones de recouvrement.
6. **Chaque nouveau fournisseur est un nouveau point de défaillance** (format, hébergement, clé, quotas) : un adaptateur isolé par source, un test de bout en bout par source, une alerte de fraîcheur par source.

## 4. Priorisation proposée

| Ordre | Source | Pourquoi |
|---|---|---|
| 0 | AROME + AROME HD | v1 France |
| 1 | **ARPEGE Europe 0,1°** | même fournisseur, même licence, même format ; couvre toute l'Europe avec une portée de 102 h |
| 2 | **ICON-D2** | accès le plus simple (sans clé, grille régulière), couvre Allemagne, Benelux, Suisse, Autriche, nord de l'Italie, 2,2 km |
| 3 | MET Nordic, KNMI | Scandinavie, Pays-Bas ; licences ouvertes, accès documentés (clé pour KNMI) |
| 4 | Met Office UKV | Royaume-Uni ; à lancer seulement après décision sur la licence CC BY-SA |
| 5 | DMI DINI, ICON-2I | vérifier licence et accès d'abord |
| — | AEMET | bloqué tant que la licence n'est pas confirmée |

## 5. Architecture multi-modèles

- **Adaptateur par source** : télécharge, décode, ramène à une grille régulière et produit des champs **canoniques** (même nom, même unité, même sémantique) par modèle.
- **Contrat de variables** : `wind10_u`, `wind10_v`, `gust10`, `t2m`, `rh2m`, `precip_rate` (mm/h), `snow_fraction`, `cloud_total`, `mslp`, `cape`… avec la **table de correspondance** vers les paramètres natifs de chaque modèle (par exemple : `precip_rate` = Δ(`tirf`+`tsnowp`+`tgrp`) pour AROME, Δ(`tot_prec`) pour ICON-D2, Δ(`tp`) pour ARPEGE).
- **Pipeline commun** à partir des champs canoniques : quantification, tuilage PMTiles, séries ponctuelles, manifest.
- **Manifest** étendu : par modèle, emprise, portée, tirages, variables disponibles, attribution.
- **Sélection côté client** : par emprise de la vue et zoom, puis par disponibilité de la variable, avec la priorité du §3.5.

## 6. Reproduire

```sh
# ARPEGE : listage et grille
curl -s "https://meteofrance-pnt.s3.rbx.io.cloud.ovh.net/?list-type=2&prefix=pnt/2026-10-17T06:00:00Z/arpege/01/SP1/"
# ICON-D2 : un champ régulier
curl -O https://opendata.dwd.de/weather/nwp/icon-d2/grib/06/t_2m/icon-d2_germany_regular-lat-lon_single-level_2026101706_001_2d_t_2m.grib2.bz2
bunzip2 icon-d2_germany_regular-lat-lon_single-level_2026101706_001_2d_t_2m.grib2.bz2
PYTHONPATH=./pylib python3 tools/lsgrib.py "icon-d2_*.grib2"
```

## 7. Sources consultées

- GeoNutzV (usage commercial gratuit des géodonnées fédérales, mention de la source) : `gesetze-im-internet.de/geonutzv`.
- MET Norway, licences et crédits : `api.met.no/doc/License`, `met.no/en/free-meteorological-data/Licensing-and-crediting` ; jeu MET Nordic : wiki `metno/NWPdocs`.
- KNMI Data Platform, HARMONIE-AROME Cy43 P1 : `dataplatform.knmi.nl`.
- DMI, documentation open data : `opendatadocs.dmi.govcloud.dk`.
- AWS Open Data, Met Office UKV : `registry.opendata.aws/met-office-uk-deterministic/`.
- MeteoHub : `meteohub.agenziaitaliameteo.it`.
- AEMET : FAQ OpenData et article datos.gob.es (juillet 2024) sur les jeux de données météorologiques de haute valeur.
