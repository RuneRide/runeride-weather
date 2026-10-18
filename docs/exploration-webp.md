<!-- SPDX-License-Identifier: EUPL-1.2 -->
# Exploration : coût de l'encodage WebP des tuiles et alternatives

Date : 2026-10-18. Périmètre : tuilage d'une échéance AROME HD, profil `reduit2`
(`out/canon/arome-hd/2026-10-17T03Z/12.npz`). Aucun code du dépôt n'a été modifié ; tout le code
d'exploration est dans le scratchpad (voir « Reproduction »). Machine partagée de 20 cœurs ;
tous les chronométrages sont **à 1 thread, médiane de 3 répétitions**. Les mentions **[mesuré]**
et **[estimé]** distinguent ce qui a été chronométré de ce qui est déduit.

## Verdict en cinq lignes

1. L'hypothèse est confirmée : l'encodage WebP sans perte est **83 % du temps en Python** (14,1 s
   sur 17,0 s) et **≈ 96 % en Rust** (≈ 13,7 s sur 14,2 s). Écriture PMTiles et calcul des
   coordonnées sont négligeables (< 0,1 %).
2. Le levier n°1 n'est **pas** la méthode (`method`) mais le paramètre `quality` de libwebp en mode
   lossless, qui règle l'effort de recherche. Il vaut 100 aujourd'hui (Pillow : `quality=100` ;
   Rust : `cfg.quality = 100.0`). Avec `method=4` inchangé et `quality=50` : **−50 % de temps
   d'encodage, +0,2 % d'octets**, décodage identique octet pour octet **[mesuré]**.
3. Avec `quality=25` : −62 % d'encodage, +1,4 % d'octets. Avec `quality=0` : −66 %, +2,4 %.
4. Changer de format ne vaut pas sa complexité : WebP lossless est déjà le plus petit des formats
   décodables nativement (PNG +26 à +37 %, binaire maison + gzip +17 à +22 %), et les formats
   « rapides » (image-webp, PNG rapide) coûtent +33 à +50 % d'octets.
5. Recommandation : garder WebP lossless, passer `quality` de 100 à 50 (ou 25), à valider par le
   comparateur de tuiles existant (`rust/tools/cmp_tiles.py`).

## 1. Profilage du tuilage d'une échéance (question 1)

Échéance 12, 1776 tuiles non vides (8 variables, z5-z7 et z8 pour precip/snow), 10,0 Mo.

### Python [mesuré] (`prof_py.py`, 1 processus, chronomètres autour de chaque étape)

| Étape | Temps (s) | Part |
|---|---|---|
| (i) Rééchantillonnage Mercator (`Grid.sample` bilinéaire) | 2,25 | 13,2 % |
| coordonnées des pixels (`pixel_lonlat`) | 0,02 | 0,1 % |
| (ii) Quantification (`encode_scalar8` / `encode_wind8`) | 0,56 | 3,3 % |
| (iii) Encodage WebP (Pillow, méthode 4, vent 3) | 14,10 | 83,1 % |
| (iv) Écriture PMTiles (8 fichiers) | 0,013 | 0,08 % |
| Total | 16,96 (+ chargement .npz hors mesure) | |

Dispersion entre répétitions : 16,83 à 16,96 s. Ce total est cohérent avec le ≈ 18 s du banc
(le banc inclut la lecture du .npz et le démarrage du processus).

### Rust [mesuré + dérivé]

La sortie JSON de `rw tile` ne sépare pas rééchantillonnage et encodage
(`dont_rechantillonnage_encodage_s` = 14,16 à 14,18 s sur 3 répétitions, `dont_assemblage_pmtiles_s`
= 0,013 s, `ecriture_fichiers_s` = 0,003 s). Pour les séparer, j'ai lancé le même binaire avec
`--webp image-webp` (encodeur Rust pur, 0,26 ms par tuile mesuré par micro-banc) :

| Backend | Tuilage (s) | Octets |
|---|---|---|
| libwebp (défaut) | 14,18 / 14,19 / 14,19 | 10 001 939 |
| image-webp | 0,97 / 0,99 / 1,00 | 13 256 092 (+32,5 %) |

Donc, **[dérivé]**, hors encodage : ≈ 0,98 − 0,26 × 1776 / 1000 ≈ **0,5 s** de rééchantillonnage
et quantification, soit ≈ 13,7 s d'encodage libwebp (≈ 96 %). Le Rust est ≈ 4,5 fois plus rapide
que Python sur le rééchantillonnage (0,5 s contre 2,8 s) mais pas sur l'encodage (même libwebp C).
D'où le gain Rust/Python limité à ≈ 18 %.

Réserve : mon micro-banc (méthode 4 pour toutes les tuiles, vent compris, 9,4 ms par tuile soit
16,7 s sur 1776 tuiles) surestime d'environ 18 % l'encodage réel de `rw` (qui encode le vent en
méthode 3). Les ratios entre réglages ci-dessous restent valables, les secondes absolues du
micro-banc sont un peu hautes.

Répartition de l'effort par zoom (Pillow, méthode 4, **[mesuré]**) : z5 6 %, z6 20 %, z7 63 %,
z8 10 % du temps d'encodage ; octets : z5 7,7 %, z6 22,5 %, z7 61,4 %, z8 8,3 %. Par variable, le
vent (RVB, plus de 256 couleurs donc pas de palette libwebp) coûte 2 à 4 fois plus par tuile que
les scalaires (palette 8 bits probable, **[estimé]**).

## 2. WebP : réglages et encodeurs (question 2)

Échantillon : 200 tuiles tirées au hasard (graine 42) sur l'échéance 12, tous zooms, stratifié par
variable (precip 36, t2m 27, vent 27, gust/rh/cloud/cape 25 chacune, snow 10 ; z5 11, z6 39,
z7 116, z8 34), 16,9 Mo bruts. Toutes les tuiles ont été relues par Pillow (décodeur
indépendant de l'encodeur Rust) : **200/200 identiques** pour `method` 0 à 5 (Pillow) et pour toutes
les combinaisons `method`/`quality` de la grille ci-dessous. Les sorties de la crate `webp` (Rust)
sont à ±0,03 % des octets de Pillow (même libwebp), je ne les ai pas relues par Pillow une à une.

### Effort `method` (quality = 100), temps par tuile à 1 thread

| Réglage | Octets (200 tuiles) | Écart | Pillow ms/tuile | Rust (crate webp) ms/tuile |
|---|---|---|---|---|
| method 0 | 3 018 998 | +67,9 % | 4,5 | 4,2 |
| method 1 | 1 862 028 | +3,6 % | 9,7 | 9,5 |
| method 2 | 1 843 740 | +2,5 % | 10,0 | 9,7 |
| method 3 | 1 825 176 | +1,5 % | 11,6 | 10,5 |
| **method 4 (actuel scalaires)** | **1 798 054** | réf. | **15,9** | **15,0** |
| method 5 | 1 759 888 | −2,1 % | 41,0 | 22,3 |
| method 6 | 1 776 028 (Pillow) / 1 747 188 (Rust) | −1,2 % / −2,8 % | 490 | 433 |

La méthode 0 est inutilisable (+68 %). Entre method 1 et 4 on ne gagne que 3,6 % d'octets pour
+63 % de temps. Le palier method 5 (−2 %) coûte ×1,5 à ×2,6 ; method 6 est absurde (×30).

### Effort `quality` en lossless (le levier ignoré) [mesuré, 200 tuiles, Pillow]

| method | q=0 | q=25 | q=50 | q=75 | q=100 |
|---|---|---|---|---|---|
| 1 | 1 905 498 o / 3,5 ms | 1 884 224 / 4,2 | 1 863 742 / 5,8 | 1 863 074 / 7,6 | 1 862 028 / 9,8 |
| 3 | 1 867 288 / 3,9 | 1 848 388 / 4,6 | 1 827 454 / 6,3 | 1 827 252 / 8,1 | 1 825 176 / 11,0 |
| 4 | 1 841 384 / 5,2 | 1 821 012 / 6,1 | **1 800 050 / 7,6** | 1 800 118 / 9,6 | 1 798 054 / 16,3 |

À `quality` ≥ 50 les octets sont quasi constants ; le temps double entre 50 et 100.

### Confirmation sur les 1776 tuiles réelles, micro-banc Rust (crate `webp`, 1 thread)

| Réglage | Octets | Écart | ms/tuile | Temps relatif |
|---|---|---|---|---|
| method 4 q100 (actuel) | 9 988 282 | réf. | 9,43 | 100 % |
| method 4 q75 | 9 999 900 | +0,1 % | 6,01 | 64 % |
| **method 4 q50** | 10 007 992 | **+0,2 %** | **4,72** | **50 %** |
| method 4 q25 | 10 124 668 | +1,4 % | 3,63 | 38 % |
| method 4 q0 | 10 224 786 | +2,4 % | 3,16 | 33 % |
| method 3 q25 | 10 279 608 | +2,9 % | 3,01 | 32 % |
| method 1 q0 | 10 541 358 | +5,5 % | 2,42 | 26 % |
| image-webp (Rust pur) | 13 291 058 | +33,1 % | 0,26 | 2,8 % |

### Confirmation de bout en bout en Python [mesuré, `prof_py_q.py`, échéance entière, 3 répétitions]

| Pillow quality (method 4, vent 3) | Encodage WebP (s) | Total rééchantillonnage + encodage (s) | Octets | Écart |
|---|---|---|---|---|
| 100 (actuel) | 14,25 | 17,27 | 10 009 954 | réf. |
| 50 | 8,31 | 11,32 (−34 %) | 10 028 802 | +0,19 % |
| 25 | 6,26 | 9,28 (−46 %) | 10 144 658 | +1,34 % |
| 0 | 5,55 | 8,57 (−50 %) | 10 244 300 | +2,34 % |

### Estimation Rust de bout en bout [estimé, non mesuré : je ne modifie pas `rust/`]

En appliquant les ratios du micro-banc à l'encodage dérivé de 13,7 s et en ajoutant 0,5 s :
q50 ≈ 7,4 s, q25 ≈ 5,8 s, q0 ≈ 5,1 s (contre 14,2 s). Un changement d'une ligne dans
`rw-tiles/src/encode.rs` (`cfg.quality = 100.0` devient `50.0`) le validerait en mesure réelle.

### Compromis pour Lambda

Le coût Lambda d'une échéance est faible : environ 1,8 Go de mémoire (1 vCPU) pendant 14 s, soit
≈ 25 Go-s ≈ 0,0004 $ (tarif ≈ 0,0000167 $/Go-s, **[estimé de mémoire, à vérifier]**), donc de
l'ordre de 0,02 $ par run de 52 échéances. Le gain monétaire de `quality` 50 est de l'ordre de
quelques dollars par mois ; le gain réel est la latence de production d'un run et la marge sur le
temps imparti. L'écart d'octets (+0,2 à +1,4 %) est négligeable pour le stockage et la sortie
réseau. Meilleur compromis : **method 4, quality 50** (quasi la même taille, moitié moins de CPU) ;
**quality 25** si l'on veut aller plus loin au prix de +1,4 % d'octets. Ne pas descendre sous method 3.

### Encodeurs alternatifs

- `image-webp` 0.2 (déjà dans `rw-tiles`, option `--webp image-webp`) : 0,26 à 0,32 ms par tuile
  (50 fois plus rapide), mais +33 à +35 % d'octets. Aucune réglage d'effort. Décodage vérifié
  identique (relecture par image-webp **dans** la même crate pour ce test ; pour le contrat, relire
  par Pillow serait préférable). Utile si la latence prime sur le poids.
- Crate `webp` 0.3 (binding libwebp, déjà utilisée) : compile hors ligne, expose `method` et
  `quality`.

## 3. Alternatives de format (question 3 et complément)

Même échantillon de 200 tuiles (scalaires 258 x 258 en 1 canal, vent `wind8` en RVB avec B = 0).
Le « binaire maison » stocke les canaux en plans séparés, après prédiction (delta horizontal ou MED
de LOCO-I, modulo 256), puis gzip. Décodage de ce binaire vérifié identique par un décodeur numpy
(200/200) et, dans Chrome, par hachage des pixels (voir plus bas).

| Format | Octets | Écart / WebP m4 q100 | Encodage ms/tuile (1 thread) | Navigateur natif |
|---|---|---|---|---|
| **WebP lossless m4 q100** (actuel) | 1 798 054 | réf. | 15,9 (Pillow), 15,0 (Rust) | oui |
| WebP lossless m4 q50 | 1 800 050 | +0,1 % | 7,6 | oui |
| WebP image-webp (Rust) | 2 429 552 | +35,1 % | 0,32 (Rust) | oui |
| PNG niveau 1 (Pillow/zlib) | 2 968 059 | +65,1 % | 0,64 | oui |
| PNG niveau 6 | 2 333 870 | +29,8 % | 2,49 | oui |
| PNG niveau 9 + optimize | 2 196 826 | +22,2 % | 24,1 | oui |
| PNG crate Rust « Fast » | 2 700 877 | +50,2 % | 0,10 | oui |
| PNG crate Rust « Balanced » | 2 403 917 | +33,7 % | 1,17 | oui |
| PNG crate Rust « High » | 2 271 444 | +26,3 % | 9,6 | oui |
| Brut + gzip 6 | 2 806 872 | +56,1 % | 1,4 | `DecompressionStream` |
| Delta H + gzip 6 | 2 505 913 | +39,4 % | 2,0 | idem |
| MED + gzip 1 (zlib Pillow) | 2 682 044 | +49,2 % | 0,56 | idem |
| **MED + gzip 6** | 2 205 821 | +22,7 % | 2,2 (Python), 1,0 (Rust zlib-rs, 2 190 318 o) | idem |
| MED + gzip 9 | 2 105 605 | +17,1 % | 20,7 | idem |
| MED + brotli 9 (indicatif) | 2 097 266 | +16,6 % | 4,6 | non (brotli non exposé par `DecompressionStream`) |
| MED + zstd 19 (indicatif, CLI) | 1 878 736 | +4,5 % | non chronométré | non |

Remarques : zlib-rs au niveau 1 est mauvais sur des résidus (3,86 Mo) ; zstd/brotli ne sont pas
décodables nativement dans le navigateur (il faudrait embarquer un décodeur WASM). Même zstd 19, qui
est lent, ne bat pas WebP. Aucun format natif ne fait mieux que WebP en taille.

### Décodage dans le navigateur [mesuré, Chrome 155 headless, logiciel, machine chargée]

Boucle séquentielle de 200 tuiles x 3 répétitions (médiane), par tuile, chemin du viewer actuel
(`createImageBitmap` avec `premultiplyAlpha:'none'` et `colorSpaceConversion:'none'`, puis
`drawImage` + `getImageData` comme `lirePixels`) :

| Format | Décodage image | Relecture des pixels | Total par tuile |
|---|---|---|---|
| WebP lossless m4 | 0,64 ms | 0,27 ms | **0,90 ms** |
| PNG niveau 6 | 0,36 ms | 0,23 ms | 0,59 ms |
| MED + gzip 6 (`DecompressionStream` + prédiction en JS, sans canvas) | | | 0,75 ms |

Les trois chemins donnent des **pixels strictement identiques** (hachage FNV des 200 tuiles
égal). Le décodage est donc négligeable (< 1 ms contre des dizaines de ms de réseau) et **ne
justifie aucun changement de format**. Limite : un seul navigateur (Chrome), sur un thread
principal chargé ; Firefox et Safari non testés.

### Chemin GPU et impact sur le viewer

Le viewer ne passe **pas** une image à WebGL : `donnees.js` décode en bitmap, le recopie dans un
`OffscreenCanvas` 2D (`alpha:false`, sRGB), relit `getImageData` et stocke un `Uint8Array` (`e.px`)
que `couche-gl.js` envoie par `texImage2D(... R8/RGB8 ..., e.px)` avec `UNPACK_PREMULTIPLY_ALPHA_WEBGL`
à faux. La lecture CPU (`lireAuPoint`) lit aussi `e.px`. Donc :

- Aujourd'hui, le risque de prémultiplication et de gestion de couleur est déjà maîtrisé par ces
  options et le passage par un canvas sans alpha ; les tuiles sont sans alpha et 0 = nodata. Les
  tests Chrome ci-dessus confirment l'exactitude.
- Un format binaire alimenterait directement le même `Uint8Array` : il supprime cette famille de
  risques (aucun canvas, aucune conversion de couleur), mais rien ne le cassait.
- Changements nécessaires pour un binaire : dans `demanderTuile` remplacer `createImageBitmap` +
  `lirePixels` par décompression gzip + prédiction inverse (≈ 15 lignes JS) ; type de tuile
  PMTiles autre que WEBP (en-tête, `format` des métadonnées, `tile_compression`; si l'en-tête annonce
  gzip, pmtiles.js décompresse tout seul, **à vérifier**) ; adapter `lirePixels` pour la taille,
  les outils de comparaison (`decode.py`, comparateurs Rust) et la chaîne de test. C'est un
  changement de contrat.

## 4. Autres leviers (question 3 fin et 4)

- **Tuiles vides ou constantes, doublons** [mesuré, 1776 tuiles, Pillow m4] : les tuiles entièrement
  nodata sont déjà sautées (`if not arr.any(): continue`). Restent 412 tuiles constantes (23 %,
  presque toutes `snow_fraction`, 269 au z8) et 477 doublons exacts (mêmes octets), mais elles
  coûtent peu à encoder : constantes 217 ms (1,2 % du temps), doublons 245 ms (1,4 %) ; octets : 18 Ko
  et 24 Ko (0,2 %). Gain maximal ≈ 2 % : sauter ou mémoïser est sans risque mais sans intérêt.
- **Moins de tuiles** : le z8 (precip/snow seulement) pèse 10 % du temps d'encodage et 8,3 % des
  octets ; le z5 6 %. Supprimer le z8 de precip_rate gagnerait ≈ 9 % du temps (≈ 1,7 s en Python
  méthode 4 q100) mais c'est une décision produit (surzoom côté client).
- **Parallélisme intra-échéance** : déjà présent (Rayon en Rust, `Pool` en Python), les tuiles sont
  indépendantes. Une fonction Lambda n'a ≈ 1 vCPU par 1,8 Go ; avec plusieurs vCPU, le temps
  d'encodage se divise presque linéairement (**[estimé]**, non mesuré ici car chronométrage à
  1 thread demandé).
- **Rééchantillonnage SIMD** : en Rust il ne représente que ≈ 0,5 s sur 14,2 s (3,5 %). Après
  réduction de l'effort WebP (q50 : ≈ 7,4 s), il reste ≈ 7 % ; un SIMD manuel ne pourrait gagner
  que quelques dixièmes de seconde. Non justifié. En Python, le rééchantillonnage numpy
  (2,25 s + 0,56 s) reste 13 à 17 % ; migrer vers Rust le divise déjà par ≈ 5.
- **Écriture PMTiles** : 13 ms ; rien à faire.

## Conclusion honnête

- **Part de l'encodage** : 83 % (Python) et ≈ 96 % (Rust) du temps de tuilage d'une échéance HD.
  L'hypothèse tient.
- **Gain possible sans toucher au contrat** : `quality` 100 vers 50 en WebP lossless
  (méthode 4 conservée) : encodage −41 % en Python mesuré (14,25 vers 8,31 s ; total −34 %), −50 %
  attendu en Rust (14,2 vers ≈ 7,4 s, **estimé**), pour +0,2 % d'octets. Avec 25 : total −46 %
  (mesuré Python) et ≈ 5,8 s (Rust, estimé), +1,4 % d'octets. Aucune perte : décodage identique
  vérifié sur 200 tuiles par Pillow pour 25 réglages, et sur Chrome pour le chemin du viewer au
  réglage actuel.
- **Risque pour le contrat** : nul pour `quality` (format, nodata 0, absence d'alpha inchangés ;
  seuls les octets compressés changent). Il reste à rejouer `cmp_tiles.py` sur une échéance complète
  avant adoption et à noter que les octets ne seront plus identiques à ceux du Python existant si
  les deux chaînes n'adoptent pas le même réglage (les décodés restent identiques).
- **Changer de format : non.** WebP lossless m4 est le plus petit format natif. Les gains de CPU des
  alternatives (image-webp : −97 % de temps ; PNG rapide : −99 %) s'achètent avec +33 à +50 %
  d'octets ; le binaire maison + gzip fait +22 % d'octets, ne va pas plus vite que image-webp, et
  impose un changement de contrat et de viewer. Le décodage (≈ 0,6 à 0,9 ms par tuile) n'est pas
  un argument.
- **Si la latence de production devient critique** : `image-webp` ramène une échéance à ≈ 1 s pour
  +33 % d'octets (10,0 vers 13,3 Mo par échéance) ; c'est le seul changement radical qui se justifie,
  à arbitrer contre la sortie réseau des utilisateurs.
- **Recommandation chiffrée** : appliquer `quality = 50` (Python : `img.save(..., quality=50)` dans
  `to_webp` ; Rust : `cfg.quality = 50.0`) en gardant `method` 4 (scalaires) et 3 (vent) :
  −34 % du temps total mesuré en Python, ≈ −48 % attendu en Rust, +0,2 % d'octets. Envisager 25 pour
  −46 %/−59 % au prix de +1,4 %.

## Limites

Un seul run (03Z) et une seule échéance (12) ; chronométrage sur machine partagée (dispersion
mesurée < 2 % entre répétitions en tuilage, mais non maîtrisée par ailleurs) ; échantillon de 200
tuiles pour les comparaisons de formats ; vent en `wind8` (profil `reduit2`), pas en `wind12`
(contrat nominal) ; pas de mesure de décodage sur GPU, Firefox, Safari ou mobile ; tarifs Lambda
cités de mémoire.

## Reproduction

Tout est dans
`/tmp/claude-1000/-home-philippe-github-com-runeride/93bcb318-8a08-4043-adfc-8845d03af2b4/scratchpad/webp-explo/`
(le paquet Python `pmtiles` est pris dans `../pylib2`) :

```bash
cd <scratchpad>/webp-explo
export PYTHONPATH=<scratchpad>/pylib2:<racine du dépôt> OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
C=<racine du dépôt>/out/canon/arome-hd/2026-10-17T03Z/12.npz
python3 prof_py.py $C 3                  # profil Python par étape, écrit tiles_raw.pkl
python3 make_sample.py                   # 200 tuiles (sample.bin / sample.json)
python3 bench_formats.py                 # WebP m0-6, PNG, delta/MED + gzip/brotli
python3 qgrid.py                         # grille method x quality (200 tuiles)
python3 pervar.py                        # taille/temps par variable, vérif. décodage
python3 sparse.py                        # tuiles constantes / doublons / nodata par zoom
for q in 100 50 25 0; do WEBPQ=$q python3 prof_py_q.py $C 3 | grep MEDIANE; done
(cd rsbench && cargo build --release --offline && ./target/release/rsbench)   # Rust, 200 tuiles
(cd rsbench && SET=all ONLY="libwebp m" ./target/release/rsbench)              # Rust, 1776 tuiles
<racine>/rust/target/release/rw tile --canon $C --out <tmp> --threads 1 --webp libwebp   # ou image-webp
python3 gen_files.py && python3 serve.py &    # puis Chrome sur http://127.0.0.1:8765/index.html
```
