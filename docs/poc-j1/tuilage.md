# Brique tuilage : rapport PoC J1

Paquet `pipeline/tiling/` : champs canoniques `.npz` (contrat §2) vers PMTiles v3 (contrat §3). Toutes les mesures ci-dessous ont été faites le 17/10/2026 sur la machine de développement (20 cœurs) avec `out/hd_step03.npz` (grille HD complète 2801 × 1791), puis `out/canon/arome-hd/2026-10-17T06Z/00..06.npz` et `out/canon/arome/2026-10-17T06Z/00..06.npz` produits par l'ingestion.

## Ce qui a été fait

| Fichier | Rôle |
|---|---|
| `pipeline/tiling/encode.py` | quantification `scalar8` / `wind12`, empaquetage RVB, WebP sans perte sans alpha |
| `pipeline/tiling/mercator.py` | formules XYZ, pixels de tuile avec bordure, bilinéaire sur grille lat/lon (NaN si un voisin est NaN ou hors emprise) |
| `pipeline/tiling/build.py` | une tuile = une tâche (processus `fork`, 20 par défaut) ; écriture PMTiles v3 |
| `pipeline/tiling/__main__.py` | ligne de commande |
| `pipeline/tiling/decode.py` | **décodeur de référence** (`TileFile.read`, `TileFile.sample`) |
| `pipeline/tiling/measure.py` | toutes les mesures de ce rapport |
| `tests/tiling/test_tiling.py` | 18 tests `unittest` (pytest n'est pas installé) |

PMTiles : écriture par le paquet Python `pmtiles` 3.8.1 (installé avec `pip install --target <dossier hors dépôt> pmtiles`), **aucun conteneur écrit à la main**. Le paquet est trouvé via `PYTHONPATH` (ou `RUNERIDE_PYLIB`). WebP sans perte : Pillow avec libwebp, vérifié par `PIL.features.check('webp')` (vrai) et par l'aller-retour exact testé.

### Rejouer

```sh
S=/tmp/claude-1000/-home-philippe-github-com-runeride/93bcb318-8a08-4043-adfc-8845d03af2b4/scratchpad
export PYTHONPATH=$S/pylib2
python3 -m pipeline.tiling --canon out/canon/arome-hd/2026-10-17T06Z/03.npz --out out/tiles [--variables t2m wind10] [--workers N] [--webp-method N] [--json mesures.json]
python3 -m unittest discover -s tests/tiling -t .
python3 -m pipeline.tiling.measure --canon out/hd_step03.npz --tiles out/tiles --out mesures.json --with-timing          # HD
python3 -m pipeline.tiling.measure --canon out/canon/arome/2026-10-17T06Z/03.npz --tiles out/tiles --run-rel arome/2026-10-17T06Z --zoom 7 --zmin 3
```

Le tirage et l'échéance du chemin de sortie viennent de la méta du `.npz` (`run`, `step`) : un fichier hors de `out/canon/` (fixture) est donc rangé correctement. Sortie : `out/tiles/<modèle>/<tirage>/<variable>/<échéance>.pmtiles`, avec `wind10` (wind12, u et v ensemble) ; `mslp` est ignoré.

### Conventions de décodage (pour la visualisation)

- Tuile stockée 258 × 258 ; pixel de tuile `i` (0..255) à l'indice de texture `i + 1`. Le centre du pixel `i` est à `x_global = (x·256 + i + 0,5) / (256·2^z)` en Mercator normalisé. Un client qui échantillonne la texture en bilinéaire doit mapper le bord de tuile (`p = 0` ou `256` en bord de pixel) sur l'indice continu `p + 0,5` de la texture.
- Une tuile entièrement « pas de donnée » **n'est pas écrite** (voir écarts) : tuile absente = NaN partout.
- Mêmes formules que le contrat ; `decode.py` les applique (code 0 donne NaN ; vent : `(R,G,B) = (0,0,0)`).
- Les images scalar8 sont du WebP RVB dont R = G = B (Pillow/libwebp n'écrit pas de WebP en niveaux de gris) : lire le canal R.

## Mesures

### Tuiles par zoom (fichier non vide)

| Modèle | z3 | z4 | z5 | z6 | z7 | z8 | Total |
|---|---|---|---|---|---|---|---|
| AROME HD (z5–z8) | | | 10 | 27 | 97 | 352 | **486** |
| AROME (z3–z7) | 4 | 4 | 10 | 27 | 97 | | **142** |

Le décompte « emprise rectangulaire » est 12 / 30 / 110 / 420 (spécifications §5.1) ; le domaine AROME n'est pas un rectangle (17,1 % de la grille HD est NaN, coins nord-ouest, nord-est, sud-ouest, sud-est), les tuiles qui ne contiennent aucun point valide ne sont pas écrites (86 tuiles de moins en HD).

### Poids et temps (20 processus, wall-clock, WebP sans perte)

Poids moyen d'un fichier par variable (échéances 1 à 6, `arome-hd`, une valeur ± 1 % d'une échéance à l'autre) et temps de **construction d'une variable seule** (lecture du `.npz` comprise, ~0,4 s de base en HD) :

| Variable | HD Mo | HD tps (s) | AROME Mo | AROME tps (s) |
|---|---|---|---|---|
| t2m | 2,84 | 0,88 | 0,88 | 0,38 |
| gust10 | 4,19 | 1,05 | 1,36 | 0,40 |
| rh2m | 5,41 | 1,09 | 1,75 | 0,46 |
| cloud_total | 5,22 | 0,88 | 1,84 | 0,34 |
| snow_fraction | 0,015 | 0,65 | 0,008 | 0,25 |
| precip_rate | 1,23 | 0,71 | 0,44 | 0,29 |
| cape | 3,65 | 1,00 | 1,19 | 0,42 |
| wind10 (u+v) | 19,0 | 2,59 | 6,53 | 1,05 |
| **Total par échéance** | **≈ 41,3** | **5,4 à 6,1** (toutes variables en une passe) | **≈ 13,9** | **1,8 à 2,1** |

L'échéance 0 est plus légère (31,8 Mo HD, 10,6 Mo AROME) : `gust10`, `precip_rate`, `snow_fraction` et `cloud_total` y sont absents dans les `.npz` de l'ingestion. Répartition HD d'un fichier `t2m` par zoom : z5 0,08 Mo, z6 0,24, z7 0,67, z8 1,79 ; `wind10` : 0,49 / 1,63 / 4,82 / 11,72 Mo. **z8 représente 63 % du poids HD.**

**ESTIMATION d'un tirage complet** (7 variables + vent = 8 fichiers × 52 échéances, extrapolation linéaire des échéances 1 à 6 mesurées, sans mesure sur les échéances 7 à 51) :

| | Poids | Temps de tuilage (1 machine, 20 cœurs) |
|---|---|---|
| AROME HD | 52 × 41,3 Mo ≈ **2,1 Go** | 52 × 5,7 s ≈ 5 min |
| AROME | 52 × 13,9 Mo ≈ **0,7 Go** | 52 × 2 s ≈ 1,7 min |

Ces poids sont **supérieurs** à l'estimation des spécifications (1 à 1,5 Go HD, 0,2 Go AROME) : le vent à 12 bits par composante est quasi incompressible (bruit dans les bits bas) et pèse à lui seul 46 % du total. Trois tirages conservés : ~8 Go (et non 4 à 5 Go). Pistes (non faites, hors périmètre du PoC) : supprimer z8 pour le vent, ou le coder en 8 bits au z8 ; ne pas stocker z8 pour `cloud_total`/`rh2m`.

### WebP sans perte contre PNG (25 tuiles z7 de l'échantillon, 258 × 258)

| Encodage | WebP (effort par défaut) | WebP effort 4 | WebP effort 6 | PNG optimisé | PNG / WebP |
|---|---|---|---|---|---|
| scalar8 (`t2m`, HD) | 178 432 o | 178 432 | 176 244 | 205 295 | +15 % |
| wind12 (`wind10`, HD) | 1 262 998 o | 1 255 266 | 1 260 902 | 1 643 239 | +30 % |
| scalar8 (`t2m`, AROME) | 146 502 o | 146 502 | 142 276 | 164 942 | +13 % |
| wind12 (`wind10`, AROME) | 1 096 994 o | 1 095 862 | 1 104 986 | 1 491 767 | +36 % |

Effort WebP : mesuré sur une tuile pleine, l'effort 6 coûte 0,72 s contre 0,016 s à l'effort 4 pour 6 % de moins (scalar8) ; pour le vent RVB, 4 à 6 ne sont **pas plus petits** que 2 à 3 et sont 5 à 70 fois plus lents (0,03 s à l'effort 3, 2,1 s à l'effort 6). Choix : effort 4 pour scalar8, **3 pour wind12** (le tuilage HD du vent est passé de 13,1 s à 2,6 s).

### Erreur d'aller-retour de quantification (grille HD complète, 4 160 519 points valides)

Quantification pure (`encode` puis `decode`) de la grille source, valeurs valides, comparée à la demi-largeur de pas :

| Variable | Erreur max | Erreur moyenne | Demi-pas | Valeurs hors plage (bornées) |
|---|---|---|---|---|
| t2m (K) | 0,1968 | 0,0985 | 0,1969 | 0 |
| gust10 (m/s) | 0,1260 | 0,0630 | 0,1260 | 0 |
| rh2m (%) | 0,1968 | 0,0981 | 0,1969 | 11 925 (dépassement ≤ 0,017) |
| cloud_total (%) | 0,1969 | 0,0436 | 0,1969 | 1 016 099 (dépassement ≤ 0,0033) |
| snow_fraction | 0 | 0 | 0,00197 | 0 |
| precip_rate (mm/h, sqrt) | 0,239 | 0,0014 | 0,393 (demi-pas maximal, en haut de plage) | 0 |
| cape (J/kg, sqrt) | 14,52 | 0,63 | 19,65 (idem) | 0 |
| wind10_u (m/s) | 0,01563 | 0,00782 | 0,01563 | 0 |
| wind10_v (m/s) | 0,01563 | 0,00788 | 0,01563 | 0 |

Toutes les erreurs maximales sont ≤ demi-pas (testé aussi en unitaire sur 200 000 valeurs aléatoires par variable). Pour AROME, mêmes ordres de grandeur, toujours ≤ demi-pas (ex. cape max 12,1). `cloud_total` dépasse de quelques millièmes 100 % pour 24 % des points (formule en recouvrement aléatoire) : l'ingestion devrait borner à [0, 100] ; le tuilage borne de toute façon.

### Couture entre tuiles voisines, avec et sans bordure

Méthode : pour chaque paire de tuiles voisines est-ouest, 9 hauteurs, on lit, comme le ferait le client WebGL en bilinéaire, la valeur exactement sur la ligne de jonction, côté A (bord est) puis côté B (bord ouest). **Avec bordure** : texture 258 entière. **Sans bordure** : texture 256 (colonnes 1 à 256), bord répété (clamp). Le saut `|A − B|` est la couture visible. L'écart à la vérité est mesuré contre la grille source rééchantillonnée au même point.

HD, z8 (2 805 points par variable) :

| Variable | saut A−B, avec bordure (max / moy) | saut A−B, sans bordure (max / moy) | écart à la vérité, avec (max / moy) | écart à la vérité, sans (max / moy) |
|---|---|---|---|---|
| t2m (K) | **0 / 0** | 1,526 / 0,066 | 0,452 / 0,082 | 0,919 / 0,104 |
| gust10 (m/s) | **0 / 0** | 2,016 / 0,086 | 0,347 / 0,046 | 1,129 / 0,077 |
| wind10_u (m/s) | **0 / 0** | 0,973 / 0,064 | 0,225 / 0,007 | 0,517 / 0,034 |

HD, z5 (44 points) : saut sans bordure t2m 2,36 K max (0,36 moy), gust10 1,45, wind10_u 1,86 ; avec bordure 0. AROME z7 (702 points) : sans bordure t2m 1,82 K max (0,083 moy) ; z3 (5 points) : 1,97 K (moy 0,67), vent 5,6 m/s (moy 1,74) ; avec bordure 0 partout.

Conclusion : **la bordure d'1 px supprime entièrement la couture** (saut nul, les deux tuiles stockent les mêmes codes sur le recouvrement), alors que sans bordure on observe un saut égal au gradient d'un pixel (jusqu'à 1,5 K à z8 en HD, 2 m/s sur les rafales). Il reste un petit écart à la vérité (max 0,45 K, 0,22 m/s) dû au rééchantillonnage à deux niveaux (voir ci-dessous), identique de chaque côté. Le coût de la bordure : 258² / 256² = +1,6 % de pixels. **Décision à valider (spec §5.1) : la bordure est recommandée.**

### Contrôle de 20 points (HD, z8 ; mêmes 20 positions pour toutes les variables)

Positions : 6 à 3 mailles (0,03°) à l'intérieur du bord ouest/est du domaine valide (à trois latitudes), 2 près des bords nord et sud, 12 tirées au hasard (graine fixe 2026) parmi les points valides. Valeur décodée depuis les tuiles par bilinéaire client (`TileFile.sample`) contre la source interpolée en bilinéaire au même point. 19 points comparables par variable ; 1 point est NaN des deux côtés (cohérent).

| Variable | Pas | (a) au centre du pixel de tuile : écart max, en pas | (b) position quelconque : écart max, en pas |
|---|---|---|---|
| t2m | 0,394 K | 0,490 | 0,727 |
| gust10 | 0,252 m/s | 0,484 | 0,492 |
| rh2m | 0,394 % | 0,497 | **1,69** |
| cloud_total | 0,394 % | 0,427 | **4,10** |
| snow_fraction | 0,0039 | 0 | 0 |
| precip_rate | ≤ 0,786 mm/h | 0,018 | 0,007 |
| cape | ≤ 39,3 J/kg | 0,102 | 0,098 |
| wind10 (u et v) | 0,0313 m/s | 0,498 | **3,73** |

(a) **Tuilage et quantification seuls** : écart ≤ demi-pas partout, critère satisfait. Aucun point n'est incohérent sur le NaN (code 0 si et seulement si source NaN). (b) Position quelconque : t2m, gust10, precip_rate, cape, snow_fraction restent ≤ 1 pas, mais `rh2m`, `cloud_total` et le vent dépassent 1 pas en des points de fort gradient. **Ce dépassement n'est pas une erreur de quantification** : c'est l'interpolation bilinéaire appliquée deux fois (source vers pixels de tuile à 425 m, puis texture vers le point), qui diffère de la bilinéaire directe sur la source là où la grille source présente un coude entre deux mailles de 1 km. Sur 300 points aléatoires (diagnostic séparé), le dépassement maximal pour `rh2m` était de 0,71 pour 0,394 de pas, avec 99 % des écarts sous 0,52 ; pour la t2m 0,225 (p99 0,19). Pour AROME (z7, maille de 2,5 km sur pixels de 850 m) les écarts relevés sont plus grands : rh2m 3,4 pas, cloud_total 10,1 pas, vent 5,5 pas (0,17 m/s) ; le centre de pixel reste ≤ 0,5 pas.

**Proposition pour le critère d'acceptation §5** : le formuler en (a) (écart ≤ demi-pas au centre de pixel de tuile), et considérer (b) comme erreur de rééchantillonnage à reporter séparément (valeurs ci-dessus). À trancher par le coordinateur.

## Écarts au contrat et choix

1. **Tuiles entièrement vides non écrites** (aucun point valide dans les 258 × 258 pixels) : gain de 86 tuiles en HD ; le client doit traiter une tuile absente comme « pas de donnée ». Contrat §3 dit « seules les tuiles qui recoupent [l'emprise] sont écrites » : lecture raffinée, à confirmer avec la visualisation.
2. **Notion de « pas » pour les échelles racine carrée** : le pas est inégal ; le métadonnée `step` donne le pas maximal (en haut de plage, ex. 39,3 J/kg pour la CAPE) ; l'erreur ≤ demi-pas local est vérifiée par test.
3. **Un voisin NaN** donne NaN, comme demandé : la limite du domaine valide est donc érodée d'environ un pixel de tuile (850 m à z7 AROME).
4. **Métadonnées PMTiles** : en plus des champs du contrat (`encoding`, `min`, `max`, `scale`, `unit`, `border`, `tile_size`), `variable`, `model`, `run`, `step` (échéance), `quant_step` (pas de quantification ; correction de l'étape 2, une collision de clé écrasait le pas), `profile`, `levels`, `stored_size`, `nodata_code`, `components` (vent), `minzoom`/`maxzoom`, `attribution`. Pour le vent, `min`/`max` valent ±64, `unit` m/s.
5. **Compression de tuile PMTiles : aucune** (le WebP est déjà compressé) ; en-tête `tile_type = webp`. La compression interne (annuaires, métadonnées) est gzip, comme le paquet `pmtiles` l'impose.
6. **WebP des scalar8 en RVB (R = G = B)**, non en niveaux de gris : contrainte de libwebp (accepté par le contrat « RVB ou niveaux de gris »). Pas de canal alpha (mode vérifié en test).
7. **Pas de `pipeline/__init__.py`** : `python3 -m pipeline.tiling` fonctionne en paquet-espace de noms ; à ne pas ajouter sans accord (partagé avec l'ingestion).
8. `mslp` ignoré (contrat), `wind10` regroupe u et v ; si seule une composante est présente, le fichier vent n'est pas produit.

## Points ouverts

- Poids du vent (46 % d'un tirage) supérieur à l'estimation : à arbitrer (z8 en 8 bits, ou pas de z8 pour le vent).
- Critère d'acceptation « 20 points » : voir (a) / (b) ci-dessus.
- Validation croisée avec la visualisation : le décodeur de référence (`decode.py`) donne des valeurs à comparer au rendu WebGL, en particulier la convention de bord (indice `p + 0,5`).
- Les estimations d'un tirage complet reposent sur les échéances 0 à 6 ; les échéances lointaines (nuages, précipitations plus actifs) peuvent peser davantage.
- `gust10` HD et `cloud_total` : hypothèses de l'ingestion, non vérifiées ici.


## Réduction du poids et 0-51 h (étape 2)

Options ajoutées, **le profil `contrat` reste le défaut** : `python3 -m pipeline.tiling ... --profil <nom>` (définitions : `pipeline/tiling/profiles.py`). Mesures sur `out/hd_step03.npz` (HD, z5-z8), toutes variables, poids d'une échéance, erreurs contre la source rééchantillonnée en flottant (tuile : tous les pixels décodés, un sur trois des tuiles du zoom maximal stocké ; position : 200 points aléatoires valides, bilinéaire client sur tuiles décodées contre bilinéaire directe sur la source). `python3 -m pipeline.tiling.profils_mesure --canon out/hd_step03.npz --out m.json`.

### Variantes (poids total d'une échéance HD ; contrat = 41,17 Mo)

| Profil | Poids | Vent | Erreur max / moy du pixel de tuile | Erreur max / moy à 200 positions | Ce qui se voit |
|---|---|---|---|---|---|
| `vent8` (8 bits/composante ±64 m/s) | 27,3 Mo | 4,93 Mo (18,77) | 0,252 / 0,126 m/s | 0,242 / 0,103 m/s | 0,5 m/s ≈ 1 nœud ; invisible sur flèches/particules, visible seulement sur un aplat de vitesse à pas fin |
| `vent8-48` (±48 m/s) | 28,0 Mo | 5,65 Mo | 0,189 / 0,095 m/s | 0,187 / 0,073 m/s | idem, plus fin ; la plage ±48 est à confirmer (mesuré : composantes ≤ 17 m/s sur ce tirage ; une tempête dépasse rarement 40 m/s en vent moyen, valeur bornée au-delà) |
| `vent12-gros2` (wind12 arrondi à 4 codes) | 34,1 Mo | 11,67 Mo | 0,0625 / 0,031 m/s | 0,061 / 0,023 | rien ; **même format**, aucun changement de décodeur |
| `vent12-gros3` (8 codes) | 30,3 Mo | 7,95 Mo | 0,125 / 0,063 m/s | 0,123 / 0,051 | rien |
| `vent-z7` (vent sans z8) | 29,4 Mo | 7,0 Mo | 0,0156 / 0,0078 (z7) | 0,137 / 0,013 m/s (surzoom depuis z7) | rien à l'œil sur flèches |
| `sans-bordure-z8` | 40,8 Mo | 18,59 | inchangée | inchangée | **−1,0 %** seulement : la bordure ne coûte que 1,6 % de pixels ; en contrepartie la couture réapparaît (sauts jusqu'à 1,5 K / 2 m/s mesurés plus haut). **Déconseillé.** |
| `lossy-t2m` (WebP qualité 95, tuiles sans trou) | 39,4 Mo | | t2m **1,77 K max** / 0,162 K moy (contrat : 0,197 / 0,099) | 0,607 / 0,139 K | t2m : 2,79 vers 1,01 Mo (−64 %) mais erreur max 9 fois plus grande, concentrée sur les fronts : **rejeté** (et le décodage avec perte peut différer d'un navigateur à l'autre ; les tuiles à trous restent sans perte) |
| `bits7` (rh2m, cloud_total, gust10 sur 126 niveaux) | 37,9 Mo | | rh2m 0,40 % ; cloud 0,40 % ; rafale 0,25 m/s | | −3,3 Mo (−8 %) |
| `pas-gros` (t2m 0,5 K, rh2m/cloud 1 %, rafale 0,5 m/s) | 36,9 Mo | | t2m 0,25 K ; rh2m 0,50 % ; cloud 0,50 % ; rafale 0,25 m/s | t2m 0,247 ; rh2m 0,49 ; rafale 0,247 | −4,3 Mo (−10 %) ; valeurs inférieures à celles qu'un utilisateur lit (température au demi-degré, humidité et nébulosité au pour cent, rafale au demi-m/s : **hypothèse d'affichage à valider avec la visualisation**) |
| `z7-tout` (aucun z8) | 15,0 Mo | 7,0 | t2m 0,197 (z7) | t2m 0,21 ; rh2m 0,73 % ; cloud 4,9 % (p99 3,4) ; rafale 0,68 m/s ; cape 7,2 J/kg | −63 % ; t2m, rh2m, cape, vent : aucune différence utile ; cloud_total et rafale perdent un peu de détail aux fronts |

Constats : le zoom 8 est de loin le premier poste (63 % du poids HD) alors qu'il ne fait que sur-échantillonner la maille native (~1 km pour des pixels de 425 m) ; le WebP avec perte n'est pas tenable à cause des fronts et des tuiles de bord ; le vent 8 bits réduit le vent de 74 % pour un pas de 0,38 à 0,5 m/s.

### Configuration recommandée : profil `reduit`

Vent `wind8` (±48 m/s) ; `t2m` 0,5 K, `rh2m` et `cloud_total` 1 %, `gust10` 0,5 m/s ; **plafond z7** pour `t2m`, `gust10`, `rh2m`, `cloud_total`, `cape` et `wind10` ; z8 conservé pour `precip_rate` et `snow_fraction` (cellules petites et nettes, 1,2 Mo seulement) ; bordure 1 px et WebP sans perte conservés. Variante `reduit-z8` : mêmes encodages mais tous les zooms (23,8 Mo par échéance).

Poids d'une échéance HD (`out/hd_step03.npz`) : **9,36 Mo** (contrat 41,17, soit −77 %) : t2m 0,89, gust10 1,14, rh2m 1,38, cloud_total 1,41, cape 1,28, precip_rate 1,24, snow_fraction 0,02, vent 2,01. Erreurs du profil `reduit` (pixel de tuile max / moy ; 200 positions max / moy) :

| Variable | Pixel de tuile (max / moy) | 200 positions (max / moy / p99) |
|---|---|---|
| t2m | 0,250 / 0,125 K | 0,283 / 0,105 / 0,238 K |
| rh2m | 0,500 / 0,250 % | 0,673 / 0,210 / 0,613 % |
| cloud_total | 0,500 / 0,112 % | **5,12** / 0,223 / 3,19 % |
| gust10 | 0,250 / 0,125 m/s | 0,618 / 0,097 / 0,331 m/s |
| wind10 | 0,189 / 0,095 m/s | 0,208 / 0,073 / 0,172 m/s |
| cape | 12,6 / 0,57 J/kg | 7,18 / 0,52 / 4,89 J/kg |
| precip_rate | 0,241 / 0,0014 mm/h | 0,051 / 0,001 / 0,026 mm/h |

Réserve : `cloud_total` atteint 5 % d'écart à un point de front net (surtout z7) ; moyenne 0,22 %. Considéré invisible pour une nébulosité rendue en opacité, **à valider visuellement**. Réserve vent : la plage ±48 doit être confirmée sur une vraie tempête.

### Tirage complet 0-51 h (mesures réelles)

AROME : 52 échéances tuilées dans les deux profils. HD : **seules les échéances 0 à 18 existent** dans `out/canon/arome-hd/2026-10-17T06Z/` (l'ingestion s'est arrêtée après `18.npz` vers 15:06, plus de processus actif au moment de la mesure) ; les échéances 19 à 51 ne sont donc **pas** mesurées.

| | Échéances | Poids mesuré | Poids moyen / échéance | Temps de tuilage (20 cœurs) |
|---|---|---|---|---|
| AROME, `contrat` (`out/tiles/arome/`) | 52 | **721 Mo** (342 Mo de vent) | 13,9 Mo (10,6 à 14,4) | 107 s (2,05 s/éch.) |
| AROME, `reduit` (`out/tiles-reduit/arome/`) | 52 | **413 Mo** (vent 101 Mo) | 7,95 Mo | 87 s |
| HD, `contrat` (`out/tiles/arome-hd/`) | 19 (0-18) | 773 Mo (vent 355 Mo) | 40,7 Mo (31,8 à 42,6) | 115 s (6,1 s/éch.) |
| HD, `reduit` (`out/tiles-reduit/arome-hd/`) | 19 (0-18) | 179 Mo (vent 38 Mo) | 9,4 Mo (5,7 à 10,7) | 41 s (2,2 s/éch.) |

Pas d'évolution marquée du poids avec l'échéance (HD contrat 39,6 à 42,6 Mo) ; la mesure AROME sur 52 échéances (721 Mo) confirme l'extrapolation précédente (0,7 Go) à 3 % près.

**Projection d'un tirage de 52 échéances, ESTIMATION pour la HD** (19 échéances mesurées, 33 extrapolées) : HD `contrat` ≈ 31,8 + 51 × 40,7 ≈ **2,1 Go** ; HD `reduit` ≈ 5,7 + 51 × 9,4 ≈ **0,49 Go**. Tirage AROME + HD : `contrat` ≈ 2,8 Go, `reduit` ≈ **0,9 Go** ; trois tirages : ≈ 8,4 Go contre ≈ **2,7 Go**. Temps de tuilage du tirage complet : `contrat` ≈ 5,5 min HD + 1,8 min AROME, `reduit` ≈ 2 min + 1,5 min.

À refaire quand l'ingestion aura fini la HD : `python3 -m pipeline.tiling --canon out/canon/arome-hd/2026-10-17T06Z/[0-9][0-9].npz --out out/tiles[-reduit] [--profil reduit]`.

### Précision à position quelconque (double interpolation)

200 positions aléatoires valides (précipitations : 200 cellules pluvieuses > 0,1 mm/h), **sans quantification** pour isoler l'effet du rééchantillonnage : le client interpole en bilinéaire entre les quatre pixels voisins d'une tuile dont les pixels valent soit la bilinéaire de la source (tuilage actuel), soit la maille source la plus proche (`proche`). Référence : bilinéaire directe sur la source (`python3 -m pipeline.tiling.precision`). Erreur max / moyenne :

| Variable | bilinéaire z7 | **bilinéaire z8 (actuel)** | bilinéaire z9 | proche z8 | proche z9 |
|---|---|---|---|---|---|
| t2m (K) | 0,251 / 0,014 | **0,097 / 0,005** | 0,033 / 0,001 | 0,394 / 0,032 | 0,526 / 0,038 |
| rh2m (%) | 2,54 / 0,072 | **1,11 / 0,021** | 0,255 / 0,008 | 2,33 / 0,157 | 2,53 / 0,177 |
| cloud_total (%) | 1,76 / 0,134 | **1,30 / 0,042** | 0,538 / 0,014 | 21,7 / 0,598 | 16,2 / 0,725 |
| vent u (m/s) | 0,216 / 0,012 | **0,140 / 0,004** | 0,013 / 0,001 | 0,278 / 0,032 | 0,244 / 0,038 |
| vent v (m/s) | 0,175 / 0,018 | **0,058 / 0,005** | 0,026 / 0,001 | 0,749 / 0,044 | 0,749 / 0,047 |
| rafale (m/s) | 0,363 / 0,016 | **0,047 / 0,003** | 0,051 / 0,001 | 0,763 / 0,058 | 0,850 / 0,060 |
| précip. (mm/h) | 0,355 / 0,006 | **0,037 / 0,001** | 0,014 / 0 | 1,15 / 0,025 | 1,59 / 0,032 |

Conclusions : (1) le **plus proche voisin est pire** à tous les zooms (erreur moyenne 3 à 40 fois plus grande, il crée des blocs) : à rejeter ; (2) la **résolution double (z9) réduit l'erreur max d'un facteur 3 à 10** mais pèse beaucoup : mesuré sur le HD, z9 seul pèse 4,53 Mo pour `t2m` (z5-z8 : 2,79 Mo) et 31,3 Mo pour le vent (z5-z8 : 18,8 Mo), soit environ 2,5 fois le zoom 8 ; **non justifié** pour un gain d'erreur qui reste sous les seuils d'affichage ; (3) l'erreur moyenne à z8 est déjà ≤ 0,04 % / 0,02 K / 0,005 m/s, et le profil `reduit` en z7 triple le maximum sans changer la moyenne. Pas de modification recommandée du rééchantillonnage.

### Tests ajoutés

`tests/tiling/test_profils.py` (8 tests, les 18 précédents passent, 26 au total) : profil par défaut inchangé, aller-retour `wind8` (±64 et ±48) et code 0, `wind12` grossier dans le même format, niveaux réduits, plafond de zoom par variable et métadonnées du profil `reduit`, erreurs ≤ demi-pas sur les tuiles du profil `reduit`, tuiles sans bordure lues et échantillonnées par le décodeur, WebP avec perte jamais écrit sur une tuile contenant du « pas de donnée ».

### Écarts et points ouverts de l'étape 2

- Le décodeur déduit la bordure de la taille réelle de l'image (256 ou 258) et gère `wind8`/`levels` ; un client WebGL doit lire `encoding`, `max` (demi-plage du vent) et `levels` dans les métadonnées PMTiles, sans quoi `reduit` n'est pas décodable avec les constantes du contrat.
- `wind8` est un nouvel encodage (u en R, v en G, B = 0) : **hors contrat**, à valider avec la visualisation (le contrat n'est pas modifié).
- Échéances HD 19 à 51 non mesurées (ingestion arrêtée à 18).
- Seuils d'affichage (0,5 K, 1 %, 0,5 m/s) et plage de vent ±48 m/s : hypothèses.

## Profil reduit2

Demande de la visualisation (banding de t2m à 0,5 K et grain des rafales faibles). `--profil reduit2` = `reduit` avec **t2m à 0,25 K** et **gust10 à 0,25 m/s** ; vent `wind8` ±48 m/s, plafond z7, niveaux de rh2m/cloud_total (1 %), bordure et WebP sans perte identiques. Le décodage reste `v = min + (code − 1) × quant_step` ; les métadonnées portent la plage propre au profil (`min`, `max`, `levels`, `quant_step`), le décodeur de référence les lit.

### Plages retenues (254 intervalles, codes 1 à 255)

À 0,25 de pas, 8 bits couvrent **63,5 unités** : la plage de t2m du contrat (100 K) ne tient pas, elle est resserrée.

| Variable | Plage `reduit2` | Pas | Justification |
|---|---|---|---|
| t2m | **243,15 à 306,65 K** (−30 à +33,5 °C) | 0,25 K (mesuré dans les métadonnées : 0,25) | centre d'automne/printemps |
| gust10 | **0 à 63,5 m/s** (et non 63,75 : 254 intervalles de 0,25) | 0,25 m/s | la plage du contrat était 0 à 64 |

Valeurs réelles mesurées (min / max sur toutes les échéances disponibles) :

| Jeu | Échéances | t2m min / max | gust10 max |
|---|---|---|---|
| AROME HD 06Z | **19 (0-18 ; 19-51 absentes de out/canon)** | 257,31 K (−15,8 °C) / 303,95 K (30,8 °C) | 28,18 m/s |
| AROME 06Z | 52 (0-51) | 259,72 K (−13,4 °C) / 303,80 K (30,7 °C) | 36,81 m/s |

Aucun écrêtage : le tuilage compte les pixels hors plage (`pixels_ecretes`) : **0** sur les 148 fichiers HD et les 412 fichiers AROME. Marge : 14,2 K sous le minimum et 2,7 K au-dessus du maximum pour t2m (le maximum est la contrainte : +30,8 °C d'un jour d'octobre, plage limitée à +33,5 °C) ; 26,7 m/s de marge pour la rafale.

**Extrêmes d'une autre saison** : la plage tient dans la métadonnée de chaque fichier, mais un été caniculaire (jusqu'à +45 °C) dépasse +33,5 °C et un hiver rigoureux descend sous −30 °C. Trois profils à plage décalée sont fournis, **même poids, même décodage** : `reduit2` (−30 à +33,5 °C), `reduit2-ete` (−15 à +48,5 °C), `reduit2-hiver` (−40 à +23,5 °C). Proposition : choisir le profil par saison dans le pipeline (ou, mieux, une plage glissante : minimum du tirage arrondi, décidée à l'ingestion), le client lisant toujours `min`/`max` dans le PMTiles ; ne jamais coder la plage en dur côté client. Les rafales à 63,5 m/s ne sont pas écrêtées en pratique (record français ~ 60 m/s en altitude). Un écrêtage éventuel est compté dans `pixels_ecretes` du rapport JSON (`--json`) et devrait déclencher une alerte.

### Mesures (`out/tiles-reduit2/`)

| Jeu | Fichiers | Poids total (octets décimaux) | Par échéance | Temps (20 cœurs) |
|---|---|---|---|---|
| HD 06Z, échéances 0-18 | 148 | **191 249 167** (191,2 Mo) | 10,07 Mo (6,0 à 11,4) | 44,1 s (2,32 s/éch.) |
| AROME 06Z, échéances 0-51 | 412 | **444 475 113** (444,5 Mo) | 8,55 Mo (5,5 à 9,0) | 89,1 s (1,71 s/éch.) |

Sur 19 échéances HD, t2m passe de 17,1 Mo (`reduit`) à 23,2 Mo et gust10 de 19,7 à 26,1 Mo.

**Tirage complet HD + AROME, 52 échéances** (AROME mesuré ; HD **ESTIMATION** : 19 échéances mesurées, 33 extrapolées : 6,0 + 51 × 10,07 Mo) :

| Profil | HD | AROME | Total |
|---|---|---|---|
| `contrat` | ≈ 2,1 Go | 0,721 Go (mesuré) | ≈ 2,8 Go (annoncé 2,87) |
| `reduit` | ≈ 0,49 Go | 0,414 Go (mesuré) | ≈ 0,90 Go (annoncé 0,92) |
| `reduit2` | **≈ 0,52 Go** | **0,444 Go** (mesuré) | **≈ 0,97 Go** |

`reduit2` pèse donc ≈ 7 % de plus que `reduit` (+0,07 Go par tirage) et ≈ 34 % du `contrat`.

### Erreur de relecture (200 points aléatoires valides, z7)

| Jeu | Variable | Plage / pas dans les métadonnées | Au centre du pixel de tuile (max / moy) | À position quelconque (max / moy) |
|---|---|---|---|---|
| HD 03 | t2m | 243,15–306,65 / 0,25 | **0,1249 / 0,0636 K** | 0,454 / 0,058 K |
| HD 03 | gust10 | 0–63,5 / 0,25 | **0,1245 / 0,0613 m/s** | 0,374 / 0,057 m/s |
| AROME 30 | t2m | idem | **0,1249 / 0,0663 K** | 0,369 / 0,056 K |
| AROME 30 | gust10 | idem | **0,1248 / 0,0667 m/s** | 0,215 / 0,052 m/s |

Au centre du pixel (quantification et tuilage seuls) l'erreur est ≤ demi-pas (0,125) partout. À une position quelconque le maximum dépasse le demi-pas : c'est l'erreur de double interpolation déjà analysée (aux fronts), pas de la quantification.

### Tests ajoutés

`tests/tiling/test_profils.py::TestReduit2` (3 tests, 29 au total, tous passent) : pas et plages des trois profils, aller-retour sur plage resserrée avec écrêtage aux bornes (codes 1 et 255, jamais 0), tuilage `reduit2` de la fixture avec relecture ≤ demi-pas (0,125) et 0 pixel écrêté.

### Points ouverts

- Échéances HD 19 à 51 toujours absentes de `out/canon/arome-hd/` : poids HD du tirage `reduit2` extrapolé.
- Choix automatique de la plage de t2m (saison ou minimum du tirage) à décider ; le client doit lire `min`/`max` dans les métadonnées.
