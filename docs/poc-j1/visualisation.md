# PoC J1 : brique « visualisation »

Page de visualisation minimale dans `viewer/` : carte MapLibre, calques WebGL personnalisés qui décodent les tuiles dans le shader, interface sombre mobile d'abord. Testée sur les **vraies tuiles** `out/tiles/arome-hd/2026-10-17T06Z/` (échéances 0 à 6 au moment des mesures) et sur des tuiles synthétiques.

## 1. Lancer

```
python3 viewer/serve.py            # http://127.0.0.1:8187/ (127.0.0.1 uniquement ; --port pour changer)
python3 viewer/tools/synth_tiles.py   # fausses tuiles -> out/tiles-synth (45 Mo, 26 s)
```

Paramètres d'URL : `?src=data|synth|sans|brute` (par défaut `data` s'il y a des tuiles, sinon `synth`), `?modele=arome`, `?run=…`, `?couche=vent|rafales|t2m|pluie|nuages|humidite|cape`. L'état est aussi dans le hash (`#couche=vent&t=3.5&c=lat,lng&z=6`). Clavier : espace, flèches (±1 h, Maj ±6 h), Début/Fin.

Montages de `serve.py` : `/data` = `out/tiles`, `/data-synth`, `/data-sans` (tuiles 256 sans bordure), `/data-brute` (258 mais bordure clonée du bord) ; `/<montage>/index.json` est un inventaire dynamique (le PoC n'a pas de manifest). Range 206/416 géré, `.pmtiles` en `application/octet-stream`, `Cache-Control: no-store`.

## 2. Choix techniques

- **MapLibre GL JS 5.24.0** et **pmtiles 4.5.0**, versions épinglées, chargées depuis jsDelivr (builds UMD, avec empreintes SRI sha384). Pas d'outil de build ; modules ES dans `viewer/js/` (`config`, `donnees`, `couche-gl`, `fleches`, `main`).
- **Fond de carte** : style MapLibre sans tuiles vectorielles, fond `#101214`, pays Natural Earth 1:50 M (GeoJSON jsDelivr, ~0,9 Mo, hors budget « données »), contours dessinés **au-dessus** de la couche de données. Un vrai fond vectoriel (relief, villes, étiquettes) reste à choisir.
- **Calque WebGL2** (`CustomLayerInterface`) : un quad par tuile visible, texture `R8` (scalar8) ou `RGB8` (wind12) en filtrage NEAREST ; le fragment shader fait le décodage, l'**interpolation bilinéaire manuelle après décodage** (un voisin au code 0 donne « pas de donnée », comme le contrat), le **mélange temporel** entre les échéances h et h+1 (pour le vent, mélange de `u` et `v`, jamais de la vitesse), puis la rampe de couleurs (texture 1D 256×1). La bordure est lue dans la taille de l'image (258 donne bordure 1) ; si une tuile voisine n'est pas là, repli sur un ancêtre déjà chargé.
- **Flèches de vent** : surcouche canvas 2D, lues côté CPU dans les mêmes tuiles (grille de 58 px), en second plan.
- **Valeur au point** : décodage CPU de la tuile (miroir du shader), survol souris ou toucher ; épingle au clic.
- **Interface** : thème `#17191C` / `#EBE8E0` / ochre `#C6934B`, cibles ≥ 48 px (lecture 56 px), légende chiffrée, attribution « Source : Météo-France, Licence Ouverte v2.0 », vitesses ×1/×2/×4 (×1 = 1 h de prévision par seconde), préchargement des 3 échéances suivantes. Vérifiée à 390 px de large.
- Les tuiles sont des données non fiables : elles ne sont que décodées comme image (taille 256 ou 258 exigée), jamais exécutées.

## 3. Mesures (Chrome 155 sans interface, WebGL **logiciel SwiftShader**)

Banc : `?bench=1`, piloté par `viewer/tools/pilote_chrome.mjs` (protocole CDP, Node). Données réelles, couche vent, échéance 0 puis animation ×4 pendant 8 s. Trois exécutions chacune. **Tout ce qui suit est MESURÉ sur cette machine, sauf mention.**

| Mesure | Bureau 1280×800 (dpr 1) | Mobile émulé 390×844 (dpr 3) |
|---|---|---|
| Première couche visible depuis le début de navigation | 930 / 1003 / 1117 ms | 930 / 1105 / 1119 ms |
| Tout chargé (échéances h et h+1) | 1,24 à 1,33 s | 1,23 à 1,37 s |
| Octets de données à l'ouverture (somme des corps Range) | **3 412 610** (3,41 Mo) | **905 646** (0,91 Mo) |
| Requêtes de données à l'ouverture | 56 (54 tuiles + 2 en-têtes) | 14 (12 tuiles + 2 en-têtes) |
| Tuiles absentes (hors emprise, sans requête) | 42 | 8 |
| Images/s en animation (rendus MapLibre) | 23,4 / 23,0 / 20,1 | 13,1 / 12,6 / 13,5 |
| Intervalle médian / p95 entre images | 42 ms / 50 à 93 ms | 72 à 77 ms / 142 à 151 ms |

Autres chiffres mesurés : décodage et mise en cache d'une tuile, 46 ms en moyenne côté bureau (réseau local compris) ; animation de 8 s sur le bureau : 4,9 Mo et 84 requêtes de plus (préchargement). Bibliothèques (transfert) : maplibre-gl.js 277 ko, maplibre-gl.css 10 ko, pmtiles.js 8,5 ko, code de la page ~50 ko ; fond de carte ~0,9 Mo (mesuré par curl, hors du compteur de la page). Poids moyen d'une tuile de vent à z7 : ~63 ko (3,41 Mo / 54).

**Limites** : le rendu est logiciel (SwiftShader, `ANGLE … SwiftShader`), sur un serveur local sans latence ni limite de débit. Les images/s et les temps ne sont **pas représentatifs d'un téléphone** (ni dans un sens ni dans l'autre) ; seuls les **octets et le nombre de requêtes** sont indépendants de la machine. Le temps « 4G » de la spécification (2,5 s) n'est pas mesuré ; **ESTIMATION** : à 10 Mbit/s, 0,9 Mo ajoutent environ 0,7 s aux 1 s mesurées sans réseau, plus les 0,3 Mo de bibliothèques.

Objectif de la spécification (≤ 1,5 Mo à l'ouverture sur la vue France) : atteint sur téléphone (0,91 Mo), **dépassé sur grand écran** (3,41 Mo) parce que le zoom de données (round(zoom carte + 1), soit z7) est plus fin que nécessaire ; les deux échéances (h et h+1) doublent le coût. Pistes : décaler le zoom de données d'un demi-niveau vers le bas sur grand écran, ne charger h+1 qu'après h.

## 4. Vérifications

- **Shader contre décodage CPU** (`__app.verifierShader`, rendu en niveaux de gris de la position dans la rampe, 60 à 80 points au hasard, mélange temporel inclus) : médiane de l'écart 0,25 à 0,3 niveau de 8 bits, p95 ≤ 0,55 niveau pour t2m, vent, pluie, rafales, CAPE, sur tuiles synthétiques (t = 2,5 et 3,7, donc interpolées) et réelles. Quelques valeurs aberrantes en bordure de seuil ou de zone sans donnée (max 0,06 à 0,26 de la rampe, 1 point sur 80 environ).
- **Couture** (`__app.couture`) : sur les tuiles réelles, rendu avec la bordure puis en l'ignorant (clamp aux bords utiles, ce qui émule un tuilage sans bordure), comparaison pixel à pixel. Zoom carte 9,5 sur la jonction lon 0° (tuiles z8, 5,7 px écran par pixel de donnée) : **237 pixels sur 800 000 diffèrent pour t2m (0,03 %), écart maximal 1 niveau** (~0,26 K) ; **2 022 pixels pour le vent (0,25 %), écart maximal 2 niveaux** (~0,8 km/h). Les écarts sont situés exactement sur les colonnes 498 à 502 (jonction verticale) et les lignes 289 à 293 (jonction horizontale), donc bien aux joints de tuiles. À l'œil (`img/viz-couture-zoom.png`, zoom 10,3 sur la jonction), on ne distingue **aucune couture** avec la bordure ; les paliers visibles sont ceux de la quantification 8 bits (0,4 K).
- **Conclusion** : la bordure d'1 px fonctionne ; sans elle, l'erreur à la jonction est de l'ordre d'un pas de quantification (invisible pour la température, à peine mesurable pour le vent sur ce zoom). Elle resterait utile pour des champs à forts gradients (pluie, CAPE) ; **correction proposée** si une couture apparaissait quand même : aucune côté shader (il lit déjà la bordure) ; côté tuilage, vérifier que la bordure vient du voisin et non d'un clone du bord (les tuiles synthétiques `--bordure-brute` reproduisent ce défaut).

## 5. Captures (`docs/poc-j1/img/`)

`viz-vent-france.png`, `viz-temperature-france.png` (vue France, 1280×800), `viz-vent-zoom.png` (Provence, avec valeur au point), `viz-temperature-zoom.png` (Alpes), `viz-couture-zoom.png`, `viz-mobile-vent.png`, `viz-mobile-temperature.png` (390 px de large, ×2).

## 6. Écarts au contrat et observations

- Aucun écart de format : lecture de 258×258 avec bordure 1, codes 0 transparents, encodages scalar8 / wind12, métadonnées PMTiles (`encoding`, `min`, `max`, `scale`, `unit`, `border`, `tile_size`) relues et conformes au contrat.
- **Emprise réelle en trapèze** : les tuiles AROME HD réelles ne couvrent pas le rectangle 12°O–16°E ; le domaine (code 0 hors du domaine du modèle d'origine) se rétrécit vers le sud. Cohérent avec des 9999 → NaN de l'ingestion, à confirmer.
- Le tuilage ne génère pas les tuiles vides (42 tuiles « absentes » à l'ouverture) : géré (aucune requête).
- Le `gust10` et la pluie n'existent pas à l'échéance 0 : le curseur est borné par couche.

## 7. Points ouverts

- Vrai fond de carte vectoriel (relief, localités), sélecteur de modèle (Auto / AROME / AROME HD, actuellement `?modele=`), manifest (`/v1/manifest.json`) à la place de `index.json`.
- Particules de vent (hors PoC : flèches seulement), `premultiplyAlpha` est déjà `none` au décodage.
- Mesure sur vrai téléphone et en 4G ; limite de mémoire du cache (360 tuiles décodées, ~20 à 90 Mo) à valider sur appareil.
- Zoom de données et poids à l'ouverture sur grand écran (voir §3) ; décodage WebP sur le fil principal (46 ms en moyenne par tuile) à déplacer dans un worker si besoin.
- Précision en haut de zoom : le zoom maximal est limité à 10,5 pour la précision des matrices en flottants 32 bits.
