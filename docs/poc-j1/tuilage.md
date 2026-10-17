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
4. **Métadonnées PMTiles** : en plus des champs du contrat (`encoding`, `min`, `max`, `scale`, `unit`, `border`, `tile_size`), `variable`, `model`, `run`, `step`, `step` (pas), `stored_size`, `nodata_code`, `components` (vent), `minzoom`/`maxzoom`, `attribution`. Pour le vent, `min`/`max` valent ±64, `unit` m/s.
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
