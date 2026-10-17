# Données AROME et AROME HD : ce qui a été mesuré

Annexe factuelle des spécifications. Tout ce qui est marqué **mesuré** a été vérifié le **17 octobre 2026** sur les fichiers réels (tirage du 17/10 à 06:00 UTC, sauf mention). **ESTIMATION** = calcul à confirmer par un PoC. **À vérifier** = non établi.

## 1. Nommage

| Dans RuneRide | Nom officiel Météo-France | Pas de grille | Taille de maille à 46°N |
|---|---|---|---|
| AROME | « Paquets Arome - Résolution 0,025° » | 0,025° | ~2,8 km (N-S) × 1,9 km (E-O) |
| AROME HD | « Paquets Arome - Résolution 0,01° » | 0,01° | ~1,1 km (N-S) × 0,8 km (E-O) |

« AROME HD » n'est pas un nom officiel dans ces jeux : c'est la sortie à 0,01° du même modèle AROME métropole.

## 2. Accès et licence

- Jeux de données sur data.gouv.fr : `paquets-arome-resolution-0-025deg` et `paquets-arome-resolution-0-01deg`, producteur Météo-France, **Licence Ouverte v2.0** (identifiant `lov2`). L'attribution à Météo-France est requise ; la date de mise à jour doit être indiquée (à rédiger précisément avec la licence).
- Stockage public, **sans clé d'API ni compte** : `https://meteofrance-pnt.s3.rbx.io.cloud.ovh.net/pnt/<AAAA-MM-JJTHH:00:00Z>/arome/<0025|001>/<PAQUET>/arome__<res>__<PAQUET>__<échéance>__<tirage>.grib2`. Le listage S3 (`?list-type=2&prefix=…`) est ouvert, avec dates de modification et tailles.
- Une seule connexion depuis ce poste : **~10 Mo/s** (mesuré, 5 à 11 Mo/s selon les fichiers).
- Documentation de référence : le wiki « OpenDataMeteoFrance » de Météo-France (la page n'énumère pas les paquets SP/HP/IP ; leur contenu ci-dessous vient de la lecture des fichiers).

## 3. Cadence, rétention, délai de publication

| Mesure | Valeur | Statut |
|---|---|---|
| Tirages par jour | **8**, toutes les 3 h : 00, 03, 06, 09, 12, 15, 18, 21 UTC (les deux résolutions) | mesuré |
| Rétention sur le S3 | **119 tirages ≈ 15 jours** (du 02/10 12:00 au 17/10 06:00) | mesuré |
| Échéances | **0 à 51 h**, pas de 1 h | mesuré |
| Fichiers par tirage complet | 0,01° : **208** (4 paquets × 52 échéances, un fichier par heure) ; 0,025° : **99** (11 paquets × 9 groupes de 6 h) | mesuré |
| Délai de publication (premier fichier / dernier fichier après l'heure du tirage) | 00 UTC : +1,9 h / +2,8 h ; 03 UTC : +1,8 h / +2,6 h ; 12 UTC : +3,0 h / +4,0 h ; 18 UTC : +4,2 h / +5,1 h | mesuré sur 4 tirages |

Conséquence : la publication d'un tirage s'étale sur **~1 h** et son délai varie de **~2 à ~5 h** selon l'heure. Un tirage n'est donc « utilisable » que 2 à 5 h après son heure nominale, et la fraîcheur affichée doit en tenir compte.

Autres produits open data Météo-France, lus sur le wiki (non testés ici) :
- **AROME-PI** (prévision immédiate) : 0,01° et 0,025°, GRIB2, **24 tirages par jour** (toutes les heures), échéances 0 à 360 min par pas de 15 min, données disponibles 30 min après l'heure ronde.
- **PIAF** : précipitations très court terme, 0,01°, échéances 5 à 195 min par pas de 5 min, tirage toutes les 5 min, disponible 10 min après.
- **AROME IFS** (0,025°) et **AROME ensemble** (1 contrôle + 24 membres, 0,025°, jusqu'à +51 h, tirages toutes les 6 h à partir de 03 UTC).

## 4. Grille

Identique pour les deux résolutions (**mesuré**) : latitude régulière 55,4°N → 37,5°N, longitude −12° → +16° (stockée en 348°→16°), `regular_ll`.

| | Points | Taille d'un champ (4 octets/point) |
|---|---|---|
| 0,025° | 1121 × 717 = 0,80 M | 3,2 Mo |
| 0,01° | 2801 × 1791 = 5,02 M | 20 Mo |

Particularités qui piègent :
- **Valeur manquante `9999`** avec bitmap : **17,1 %** des points sont masqués (coins hors du domaine réel du modèle, sur les deux résolutions). Sans traitement, les `9999` écrasent toute échelle de couleurs.
- **Compression CCSDS** (`grid_ccsds`) : il faut une bibliothèque GRIB avec `libaec` (eccodes 2.50 l'a décodée sans configuration).
- **Cumuls depuis H0** : précipitations, rayonnements, flux sont des cumuls (`accum`) à dé-cumuler pour obtenir des valeurs horaires. Rafales, min/max de température : extrema sur la période de l'échéance (`max`/`min`).
- Fichiers 0,025° : un fichier contient un groupe d'échéances consécutives (le premier, 00H06H, en contient **7** : 0 à 6 ; les suivants 6 : 07H12H = 7 à 12, etc. ; le dernier, 49H51H, 3). Il n'y a pas de recouvrement entre groupes.
- Certains champs ressortent en `unknown` dans eccodes (paramètre local Météo-France) : leur identité exacte est **à établir** avant usage (voir §6).

## 5. Volumes par tirage (mesuré, tirage 03:00 UTC du 17/10)

| Résolution | Paquet | Fichiers | Taille |
|---|---|---|---|
| 0,01° | HP1 | 52 | 3,87 Go |
| 0,01° | SP1 / SP2 / SP3 | 52 chacun | 1,22 / 0,98 / 0,20 Go |
| 0,01° | **Total** | 208 | **6,3 Go** |
| 0,025° | HP1 / HP2 / HP3 | 9 chacun | 6,13 / 4,81 / 0,10 Go |
| 0,025° | IP1 / IP2 / IP3 / IP4 / IP5 | 9 chacun | 3,81 / 1,35 / 5,95 / 0,54 / 1,21 Go |
| 0,025° | SP1 / SP2 / SP3 | 9 chacun | 0,48 / 0,37 / 0,51 Go |
| 0,025° | **Total** | 99 | **25,3 Go** |

Débit d'ingestion si tout est lu : 8 tirages × (6,3 + 25,3) Go ≈ **250 Go par jour** (ESTIMATION). Se limiter aux paquets utiles (voir §7) ramène à une fraction.

## 6. Contenu des paquets (lu dans les fichiers)

**0,01° (AROME HD)** : seulement 4 paquets.

| Paquet | Contenu vérifié |
|---|---|
| SP1 | T 2 m, humidité relative 2 m, vent 10 m (u, v) ; rafales max (composantes est/nord), présentes à 3 h et absentes à 0 h ; la fenêtre exacte du maximum (dernière heure ?) est **à vérifier** |
| SP2 | CAPE, pression de surface, nébulosité basse / moyenne / haute, pluie cumulée (`tirf`), neige cumulée (`tsnowp`), graupel cumulé (`tgrp`), 1 champ `unknown` |
| SP3 | 1 champ `unknown` (+ hauteur au-dessus du sol à H0) |
| HP1 | vent et humidité relative à **10, 20, 50, 100 m seulement** (direction, vitesse, u, v) |

Pas de pression réduite au niveau de la mer, pas de rayonnement, pas de point de rosée, pas de vent au-dessus de 100 m en 0,01°.

**0,025° (AROME)**

| Paquet | Contenu vérifié |
|---|---|
| SP1 | vent 10 m (u, v, vitesse, direction), rafales max (vitesse et composantes ; fenêtre exacte à vérifier), T 2 m, humidité relative 2 m, **pression réduite au niveau de la mer**, précipitations totales (`tp`), neige, graupel, rayonnement solaire descendant (`ssrd`) |
| SP2 | point de rosée 2 m, humidité spécifique 2 m, pression de surface, T de surface, nébulosité basse/moyenne/haute, CAPE, **hauteur de couche limite**, pluie cumulée, T min/max 2 m |
| SP3 | flux de chaleur latente/sensible, rayonnements thermique et solaire nets, tensions turbulentes (peu utile pour un affichage) |
| HP1 | vent (direction, vitesse, composantes) sur **24 hauteurs de 20 m à 3000 m** (20, 35, 50, 75… 2750, 3000 m), plus le vent à 10 m |
| HP2 | point de rosée sur 25 hauteurs |
| HP3 | 1 champ `unknown` sur 7 hauteurs (500 à 3000 m) |
| IP1…IP5 | niveaux de pression de 100 à 1000 hPa par pas de 25 hPa (24 niveaux) : vent (composantes et direction), eau/glace nuageuse, énergie cinétique turbulente, tourbillon… |

La liste complète des paramètres des gros paquets (HP, IP) n'a été lue que sur leurs ~30 premiers Mo : à compléter par un inventaire complet au PoC (`tools/lsgrib.py`).

## 7. Ce qui est disponible, ce qui ne l'est pas

| Besoin d'un affichage « météo » | Source AROME | Disponible |
|---|---|---|
| Vent 10 m (vitesse, direction, flux de particules) | SP1 / HP1 | oui, 0,025° et 0,01° |
| Rafales | SP1 | oui (maximum sur la période de l'échéance, fenêtre à vérifier ; absent à l'échéance 0) |
| Température 2 m | SP1 | oui |
| Humidité, point de rosée | SP1, SP2 | humidité oui ; point de rosée 0,025° seulement (calculable en 0,01° depuis T et HR) |
| Précipitations (mm/h), pluie/neige | SP1, SP2 | oui (cumuls à dé-cumuler) ; type pluie/neige par séparation `tirf` / `tsnowp` / `tgrp` |
| Nuages (bas/moyen/haut) | SP2 | oui |
| Pression et isobares | SP1 | **0,025° seulement** (pas de pmer en 0,01°) |
| Orages (CAPE) | SP2 | oui |
| Vent en altitude | HP1, IP1/IP3 | 0,025° jusqu'à 3000 m et 24 niveaux de pression ; 0,01° jusqu'à 100 m |
| Isotherme 0 °C, température en altitude | IP* | à vérifier (paramètres non encore inventoriés) |
| Visibilité, brouillard | — | non identifié |
| Radar, satellite, foudre | — | hors AROME (radar via PIAF / autres jeux Météo-France, à étudier) |
| Vagues, qualité de l'air | — | hors périmètre |

## 8. Mesures de traitement (poste de développement, 20 cœurs)

| Mesure | Valeur |
|---|---|
| Décodage de 6 champs 0,01° (2801 × 1791, CCSDS) | **0,43 s** |
| PNG 4 canaux 8 bits (u et v sur 16 bits) à pleine grille 0,01° | **8,4 Mo**, 2,2 s (les `9999` n'étaient pas masqués : à refaire proprement) |
| Température 8 bits à pleine grille 0,01° | PNG **0,78 Mo** ; WebP sans perte **0,62 Mo** ; WebP qualité 90 : 0,09 Mo (avec perte) |
| Erreur de quantification de la température sur 8 bits (−40 à +60 °C) | **0,2 K** |
| Résolution du vent sur 16 bits (±64 m/s) | **0,002 m/s** |

## 9. Tuiles de données : pyramide Web Mercator

Emprise : 37,5–55,4°N, 12°O–16°E ; tuiles de 256 px (**calculé**).

| Zoom | Tuiles | Résolution au sol à 46°N | Pertinent pour |
|---|---|---|---|
| 5 | 12 | 3,4 km/px | vue France entière |
| 6 | 30 | 1,7 km/px | maille native AROME 0,025° |
| 7 | 110 | 850 m/px | maille native AROME HD (0,8–1,1 km) |
| 8 | 420 | 425 m/px | AROME HD suréchantillonné ×2 |
| 9 | 1 599 | 212 m/px | inutile en données (surzoom côté client) |
| 3 à 8 cumulés | **580** | | pyramide HD complète |

Au-delà du zoom 8, la donnée n'apporte rien : l'affichage surzoome et interpole (bilinéaire) côté client.

## 10. Reproduire ces mesures

```sh
pip install --target ./pylib eccodes
# listage d'un tirage : https://meteofrance-pnt.s3.rbx.io.cloud.ovh.net/?list-type=2&prefix=pnt/2026-10-17T06:00:00Z/arome/&max-keys=1000
curl -O https://meteofrance-pnt.s3.rbx.io.cloud.ovh.net/pnt/2026-10-17T06:00:00Z/arome/001/SP1/arome__001__SP1__03H__2026-10-17T06:00:00Z.grib2
PYTHONPATH=./pylib python3 tools/lsgrib.py "arome__001__SP1__03H__*.grib2"
```

Les tirages des 17/10 (00, 03, 06, 12, 18 UTC) et du 16/10 (12, 18 UTC) ont servi aux mesures de délai et de volume.

## 11. AROME-PI et PIAF (jalon J1b, vérifié le 17/10/2026)

**Ces deux produits ne sont pas sur le stockage S3 ouvert** (qui ne contient que `arome`, `arpege`, `phealth` et `vague-surcote`) : ils sont servis par des **API** du portail de Météo-France (`portail-api.meteofrance.fr`), déclarées comme « services de données » sur data.gouv.fr.

| | AROME-PI (« API Modèle AROME Prévision Immédiate ») | PIAF (« Prévision Immédiate Agrégée Fusionnée ») |
|---|---|---|
| Contenu | modèle AROME à très courte échéance : analyse et prévision, paramètres, échéances et domaines **paramétrables** | précipitations probabilistes, J-4 à T+3 h, **toutes les 5 min** |
| Licence déclarée | **Licence Ouverte v2.0** | **aucune indiquée** |
| Accès déclaré | `open_with_account` : **compte gratuit** nécessaire | `open_with_account`, mais publié dans l'environnement **« APIS commerciales »** |
| Limite | **100 requêtes/min**, disponibilité annoncée 99,9 % | idem |
| Interface | **OGC WMS** (images) et **OGC WCS** (champs par sous-ensemble, `GetCoverage`), couches `MF-NWP-HIGHRES-AROMEPI-001-FRANCE` (0,01°) et `…-0025-FRANCE` (0,025°) ; OAuth2 | non lue |
| Authentification | **mesuré : HTTP 401 sans jeton** (la description OpenAPI, elle, est publique) | — |

Cadence et pas lus sur le wiki (non testés faute de jeton) : AROME-PI **24 tirages par jour** (toutes les heures, disponible 30 min après), **échéances 0 à 360 min par pas de 15 min** ; PIAF 5 à 195 min par pas de 5 min.

**Conclusion J1b :**
- **AROME-PI est utilisable** : licence ouverte, interface standard, 0,01° disponible. Il faut un **compte et un jeton** sur le portail de Météo-France, créés par le titulaire du projet (je ne crée pas de compte). Le jeton serait stocké dans AWS Secrets Manager, jamais dans le dépôt.
- Le catalogue des paramètres d'AROME-PI n'est lisible qu'avec un jeton : à inventorier au premier appel (`GetCapabilities`).
- **PIAF est écarté de la v1** : son environnement « commerciales » et l'absence de licence déclarée laissent penser qu'il n'est pas librement réutilisable ; à réexaminer seulement après confirmation écrite de Météo-France.
- Contrainte à dimensionner : 24 tirages par jour × une vingtaine d'échéances × le nombre de paramètres, à 100 requêtes/min. ESTIMATION : tenable pour une dizaine de paramètres, à mesurer.
