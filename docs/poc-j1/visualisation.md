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

## 8. Profil `reduit` (décision D13)

Tout ce qui suit est **mesuré** (Chrome 155 sans interface, WebGL logiciel : aucune conclusion sur les images/s) sauf mention. Tuiles : HD tirage 03Z, 52 échéances, profils `contrat` (`out/tiles`) et `reduit` (`out/tiles-reduit`).

### 8.1 Ce qui a changé dans le viewer

- **Décodage unique** (shader et CPU : valeur au point, flèches) : `v = min + (code − 1) × quant_step` pour les scalaires linéaires, `quant_step` et `levels` lus dans les métadonnées PMTiles (`js/config.js`, `paramsDecodage`) ; `sqrt` : `min + (max−min) × ((code−1)/levels)²` ; **`wind8`** : `u = (R−1)/254 × 2·max − max`, idem `v` avec G, B ignoré, `R = 0` ou `G = 0` = pas de donnée (`max` = 48 lu dans les métadonnées). Repli sur les constantes du contrat si une clé manque (tuiles synthétiques).
- **Zoom propre à chaque couche** : le zoom maximal vient de l'en-tête PMTiles du fichier de la couche (7 pour t2m, rafales, humidité, nuages, CAPE et vent ; 8 pour pluie et neige) ; au-delà, surzoom côté shader. Nouvelle couche **Neige (part)** (`snow_fraction`).
- `?profil=contrat|reduit` (le serveur monte `out/tiles-reduit` sous `/data-reduit`), `?run=` ; **tirage par défaut** : celui qui a le plus d'échéances de température (HD 03Z, 52). `?decodage=ancien` force l'ancienne formule min/max ; `?ui=0` masque l'interface (captures comparatives).
- Au passage : le canvas des flèches est en `willReadFrequently` (le canvas accéléré de Chrome sans interface restituait parfois une image périmée, des flèches de vent restant visibles sur d'autres couches dans les captures).

### 8.2 Décodage contre le décodeur Python de référence (`pipeline/tiling/decode.py`)

120 points aléatoires valides par variable et par profil (France, échéance 24, z7, bilinéaire sur la tuile comme le client), valeur du viewer (décodage CPU dans le navigateur) contre `TileFile.sample`. **Écart maximal** : `contrat` 1,5·10⁻⁵ (t2m), ≤ 6·10⁻⁶ pour les autres ; `reduit` 6·10⁻⁶ (t2m), 5·10⁻⁸ pluie, 4,6·10⁻⁷ vent (wind8), 0 pour la neige : donc égalité à l'arrondi `float32` près, pour les 8 variables (960 points par profil). Shader contre décodage CPU (rendu en niveaux de gris, 80 points) : médiane 0,25 niveau de 8 bits, 95e centile ≤ 0,5 niveau (t2m reduit : 5 niveaux sur ~1 point en 20, aux fortes pentes).

**Non-régression** : décodage `contrat` avec la nouvelle règle contre l'ancienne formule, même vue 1280×800, 8 couches : **images identiques au pixel** (écart 0 sur 8 couches). L'ancienne formule sur `reduit` est fausse sur 99 % des pixels de t2m, vent, rafales, humidité, nuages (écart moyen 24 à 74 niveaux) : la règle `quant_step` est indispensable.

### 8.3 Comparaison visuelle contrat contre reduit

Mêmes vues et échéances : France entière (zoom carte 5, données z6) aux échéances 6, 24, 48 pour les 8 couches ; zooms Alpes (z8,2 et 8,5), Provence, front de nuages au large de la Gironde (45,5°N 2,3°O, échéance 48) ; planches `img/cmp-*.png` (contrat | reduit | différence amplifiée) ; script `viewer/tools/comparer_profils.py`. Seuil : ΔE CIE76 > 2,3 = différence juste perceptible, > 5 = nette. Dénominateur : pixels où la couche change le fond. Exemples (`ventc` = couleur du vent sans les flèches) :

| Vue | Moy. RVB (0-255) | % pixels ΔE > 2,3 | % ΔE > 5 |
|---|---|---|---|
| France, t2m (6 / 24 / 48 h) | 0,53 à 0,57 | 0,11 à 0,20 | 0 |
| France, vent couleur seule | 0,72 à 0,75 | 0,9 à 1,2 | 0 |
| France, vent avec flèches | 0,80 à 0,86 | 1,5 à 1,8 | 0,23 à 0,30 (flèches) |
| France, rafales | 0,71 à 0,79 | 1,3 à 2,5 | 0 |
| France, humidité / nuages | 0,27 / 0,10 à 0,13 | ≤ 0,03 / 0 | 0 |
| France, pluie, CAPE, neige | 0 | 0 | 0 (même fichier dans les deux profils) |
| Alpes z8,2 : t2m / vent couleur / rafales | 0,70 / 1,25 / 1,38 | 5,1 / 8,1 / 11,2 | 0,07 / 0,16 / 0,69 |
| Provence : t2m / vent couleur | 0,63 / 0,93 | 0,9 / 3,7 | 0 / 0,01 |
| Front de nuages z8,2 | 0,98 | 2,6 | 0,21 |
| Provence : CAPE z8,2 | 3,2 | 3,7 | 3,65 |

**Ce qui ne se voit pas** : à l'échelle France (z6, identique dans les deux profils sauf quantification) l'écart est un bruit de 1 à 2 niveaux, sans structure ; la différence amplifiée ×20 à ×40 montre du grain, pas de formes. Les flèches : le profil change de très peu leur longueur et leur angle, d'où le 0,3 % de pixels > 5 sur le vent avec flèches, aucun sur la couleur seule. Le surzoom z7 contre z8 de la température, de l'humidité, des nuages et du vent (Alpes z8,2) ne se distingue pas côte à côte ; il ne s'écarte qu'aux limites du relief (5 à 11 % de pixels au-dessus du seuil de perception, 0,07 à 0,7 % nettement). **Bords de nuages** : écart de bord inférieur à 1 pixel d'écran, 0,21 % de pixels nettement différents, invisible sans amplification. **Pluie et neige** : le zoom 8 est conservé, le rendu est strictement identique, y compris le passage z7 à z8 (zoom carte 8,5 : 0 pixel de différence).

**Ce qui se voit, avec effort** : (1) le **banding de la température** : dans les zones lisses (mer, plaines), des contours de paliers sont perceptibles dans les deux profils ; le pas de 0,5 K les rend un peu plus nets que 0,39 K (`img/cmp-banding-t2m.png`). Écart de couleur entre deux paliers consécutifs avec ma palette (médiane / max, calculé, pas mesuré à l'écran) : 2,0 / 2,7 (contrat), **2,6 / 3,5 (reduit)**, donc autour du seuil de perception (2,3) dans les deux cas. L'interpolation bilinéaire dans le shader adoucit la jonction mais pas le palier. (2) Les **rafales** : 0,5 m/s soit 1,8 km/h : ΔE 4,2 médian par palier contre 2,1 ; l'effet visible est du grain dans les rafales faibles (Alpes : 11 % de pixels au-dessus de 2,3). (3) Le vent `wind8` : pas de 0,38 m/s soit 1,4 km/h par composante, ΔE de 4,1 médian pour un palier de vitesse, mais le mélange u/v rend les paliers diffus : aucun contour visible à l'œil sur les captures. (4) CAPE à z7 : le seuil d'affichage à 50 J/kg se déplace d'un pixel de donnée, formes plus empâtées sur les cellules isolées (3,65 % de pixels nettement différents) ; sans conséquence pour l'usage, mais c'est la seule couche où le plafond z7 se voit.

**Coutures** (bordure 1 px conservée, rendu avec puis sans la bordure, zoom carte 9,5 sur la jonction lon 0°) : reduit t2m 288 pixels sur 800 000 (1 niveau, ≈ 0,26 K), vent 1 399 (2 niveaux) ; contrat : 619 et 1 608 ; localisées uniquement sur les colonnes et lignes de jonction ; aucune couture visible à l'œil.

### 8.4 Octets et requêtes

Ouverture (somme des corps Range à la première couche complète : en-têtes + métadonnées + tuiles h et h+1), vue France :

| | contrat | reduit |
|---|---|---|
| Bureau 1280×800, vent | 3,42 Mo, 57 requêtes | **1,08 Mo**, 57 requêtes |
| Bureau, température | 0,55 Mo | 0,50 Mo |
| Mobile 390×844 (émulé), vent | 0,91 Mo, 15 requêtes | **0,35 Mo**, 15 requêtes |
| Mobile, température | 0,18 Mo | 0,16 Mo |

Objectif de 1,5 Mo atteint sur les deux écrans avec `reduit` (le vent seul dépassait avec `contrat` sur bureau). Les 15 ou 57 requêtes comprennent 2 en-têtes de 16 ko et 2 lectures de métadonnées ; les tuiles hors emprise n'ont pas de requête.

**Préchargement de toute la frise 0-51 h** (**mesuré** sur les 52 échéances, uniquement les tuiles visibles ; en-têtes de 16 ko par fichier inclus, soit ≈ 0,85 Mo par couche) :

| Couche | Bureau contrat | Bureau reduit | Mobile contrat | Mobile reduit |
|---|---|---|---|---|
| Vent | 93,9 Mo | **29,4 Mo** | 21,5 Mo | **8,4 Mo** |
| Température | 16,2 Mo | 14,6 Mo | 4,2 Mo | 3,8 Mo |
| Pluie | 9,6 Mo | 9,6 Mo | 3,1 Mo | 3,1 Mo |
| Nuages | 26,1 Mo | 20,5 Mo | 6,3 Mo | 5,3 Mo |

Soit 1 568 à 1 722 requêtes (bureau) et 329 à 336 (mobile) par couche. **ESTIMATION** (non mesurée) pour la frise d'une couche sur 4G à 10 Mbit/s : 8,4 Mo en 7 s sur mobile avec `reduit` contre 17 s avec `contrat`. Les mesures ne couvrent qu'une vue ; un déplacement ou un zoom ajoute des tuiles. Un fichier par échéance coûte 16 ko d'en-tête ; une lecture groupée des en-têtes ou des répertoires gagnerait 0,85 Mo par couche (non essayé).

### 8.5 Recommandation sur D13 : **oui, adopter `reduit`, avec un ajustement ciblé**

- Gains mesurés : vent 3,2 fois moins d'octets à l'ouverture, 3,2 à 2,6 fois moins pour la frise ; −68 % de poids de stockage (tuilage). Aucune différence visible à l'œil à l'échelle France ni sur les zooms Alpes ou Provence pour le vent, les nuages, l'humidité et les bords de nuages ; couture inchangée.
- **Ajustement recommandé** : garder des paliers fins là où la palette amplifie le pas : **`t2m` à 0,25 K** (le banding est déjà au seuil de perception à 0,39 K ; le coût en octets de la température est faible : 0,5 Mo à l'ouverture sur bureau) et **`gust10` à 0,25 m/s** (pas du contrat) si le grain des rafales faibles gêne ; ces deux ajustements ne touchent pas le vent, qui porte l'essentiel du gain. Le profil `reduit` actuel reste acceptable si l'on tolère un léger banding de température.
- **CAPE** : le plafond z7 est acceptable (seule couche où il se voit, aux contours) ; garder z8 uniquement pour la pluie et la neige comme dans `reduit`.
- Réserve : mesures faites en Chrome sans interface sur machine locale ; pas de test sur téléphone réel ni en 4G ; la validation de `wind8` ne porte que sur le tirage 03Z du 17/10/2026 (conditions calmes à modérées, vitesses < 48 m/s : un vent plus fort serait écrêté à ±48 m/s par composante).

### 8.6 Rejouer

`python3 viewer/serve.py` ; `python3 viewer/tools/comparer_profils.py scenario DOSSIER`, puis `node viewer/tools/pilote_chrome.mjs DOSSIER/scenario-<profil>.json` pour chacun, puis `python3 viewer/tools/comparer_profils.py analyse DOSSIER`. Octets : `?bench=1&profil=…` et `__app.mesurerFrise('vent')`. Décodage : `__app.comparer(points)` (voir §8.2).
