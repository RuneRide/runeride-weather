# Isobares en Rust : crate `rw-isobares`

Statut : PoC, mesuré sur le tirage AROME `2026-10-17T06Z` (52 échéances) de la machine de développement (x86_64, glibc, 20 cœurs, **machine chargée par d'autres tâches pendant les mesures** : charge moyenne de 4 à 12). Les repères **[M]** sont des mesures de ce document, **[T]** des tests automatisés, **ESTIMATION** une valeur non mesurée.

Le crate `rust/crates/rw-isobares` porte `pipeline/isobars` (lissage gaussien normalisé par le masque, marching squares, assemblage en lignes, Douglas-Peucker, extrema A / D par proéminence, sérialisation GeoJSON) : voir [../poc-j3/isobares.md](../poc-j3/isobares.md) pour les choix d'algorithme, qui sont inchangés (σ = 4 mailles, pas de 2 hPa, 0,1 km, σ = 8 et 1 hPa pour les extrema). Le Python n'est pas modifié et reste la référence.

## 1. Résultat

| | Python | Rust |
|---|---|---|
| Sortie des 52 échéances AROME | référence | **identique octet pour octet** [M] |
| Écart maximal de position d'un sommet | | **0 m** (aucun sommet différent sur 1 531 lignes et 270 067 sommets) [M] |
| Niveaux, nombre de lignes, drapeaux `majeur` / `ferme`, extrema (genre, position, pression, proéminence) | | identiques [M] |
| Temps par échéance (calcul seul, hors lecture du `.npz`) | ≈ 0,8 s | **≈ 0,10 s sur 1 fil, ≈ 0,045 s sur 20 fils** [M] |
| 52 échéances | 47,5 s à 49,9 s séquentiel (42 s dans la doc J3, machine moins chargée) | **5,2 s sur 1 fil, 2,3 s sur 20 fils** (somme des durées par échéance, meilleure de 5 répétitions) [M] |

Rapport : ×8 à ×9 à nombre de fils égal (1 fil), ×18 avec tous les fils.

## 2. Contrat de sortie

* **Contenu** : le GeoJSON de `calcul.vers_geojson` écrit par `json.dump(..., ensure_ascii=False, separators=(",", ":"))` : `FeatureCollection` avec un membre étranger `proprietes`, lignes `LineString` (`type`, `niveau`, `majeur`, `ferme`) puis points `Point` (`type`, `genre`, `pression`, `libelle`, `proeminence`), coordonnées arrondies à 1e-4°. Ordre des clés, des entités (niveau croissant, puis longueur décroissante), formes des flottants (`2.0`, `-0.0`) et accents (« Dépression ») sont ceux du Python.
* **Chemin lu par le viewer** : `viewer/js/isobares.js` charge `<src>/NN.geojson` avec `src = /isobares/arome/<tirage>` (`viewer/js/main.js`, `integration-logique.js`). La clé S3 écrite par `rw-lambda` est donc

  `v1/isobares/arome/<tirage>/NN.geojson` (ex. `v1/isobares/arome/2026-10-17T06Z/12.geojson`)

  de sorte qu'une origine CDN dont le chemin est `/v1` (ou tout montage `/isobares` → `v1/isobares`) sert exactement les URL que le viewer construit, **sans modifier le viewer**. En local, `out/isobars/arome/<tirage>/NN.geojson` (montage `/isobares` de `viewer/serve.py`) reste valable et identique.
* **Compatibilité** : l'objet `_isobares/NN.npz` (entrée du Python) continue d'être écrit tel quel. L'état d'échéance reçoit une clé additive `isobares_geojson` (`cle`, `octets`, `lignes`, `sommets`, `extrema`, ou `null` pour l'AROME HD qui n'a pas de `mslp`) et `mesures.isobares_s` ; les lecteurs existants (`isobares`, `variables`…) ne changent pas, et `deja_complet` vérifie aussi la présence et la taille du GeoJSON.
* **Compression** : le GeoJSON stocké est **non compressé** (`Content-Type: application/json`, `Cache-Control: public, max-age=31536000, immutable`, comme les autres fichiers de tirage). Le type `application/json` est compressible à la volée par CloudFront (~29 ko en gzip pour ~85 ko, voir §4). `rw_isobares::produire` fournit aussi les octets gzip (niveau 6, en-tête sans date : déterministe) ; les stocker pré-compressés exige que `Meta` de `rw-aws` porte un `Content-Encoding` (3 lignes dans `stockage.rs` et `s3.rs`). Je n'ai pas touché à `rw-aws` (crate d'un autre chantier) ; c'est le point à trancher à l'assemblage : pré-compresser économise la compression CDN et le stockage (×3) mais impose `Content-Encoding: gzip` à tous les clients.
* **Défaut** : une erreur de calcul (grille incohérente, nœud de degré > 2) est une erreur **définitive** de l'échéance, comme un GRIB illisible ; l'AROME HD, sans `mslp`, ne produit ni fichier ni erreur.

## 3. Conception

Le port est littéral : même ordre d'opérations flottantes que le Python, ce qui donne un résultat identique bit à bit plutôt que « proche ».

* **Lissage** : noyau normalisé par la somme « pairwise » de numpy (`somme_numpy`), convolutions séparables avec zéros hors grille accumulant les taps dans l'ordre croissant comme `_convoluer_axe`, `conv(F·m) / conv(m)`. Lignes en parallèle (rayon) ; les deux lissages (σ = 4 et σ = 8) et les deux convolutions numérateur / dénominateur sont lancés par `rayon::join`.
* **Marching squares** : table des cas identique ; ordre des segments identique (tous les premiers segments des cellules en ordre ligne majeure, puis tous les seconds : c'est ce qui fixe le sens et le point de départ des anneaux) ; identifiants d'arêtes comme nœuds ; assemblage avec les mêmes règles d'ordre de voisinage (`v0`, `v1`). Niveaux : équivalent de `np.arange`. Niveaux calculés en parallèle, résultats réassemblés dans l'ordre.
* **Géométrie** : `np.interp` (pente puis `pente × (x − xp) + fp`), plan équirectangulaire à 46,5°, Douglas-Peucker itératif (premier maximum, anneau coupé au point le plus éloigné), somme de longueur pairwise.
* **Extrema** : inondation décroissante + union-find sur la grille sous-échantillonnée par 3, avec l'ordre d'insertion du dictionnaire Python (le tri final stable en dépend), raffinement de position sur la grille pleine.
* **Sérialisation** : écriture directe de la chaîne ; arrondi `round(x, n)` de Python obtenu par `format!("{:.n}")` puis relecture (arrondi correct de la valeur binaire exacte).
* **Dépendances** : `rayon`, `serde_json` (échappement des chaînes de l'en-tête), `flate2` (gzip, `zlib-rs`, déjà dans l'espace de travail), `thiserror`. Le binaire de banc (`--features cli`) ajoute `rw-cli` pour lire les `.npz`.

Intégration à `rw-lambda` (`traitement.rs`) : après l'écriture de `_isobares/NN.npz`, si le canonique contient `mslp`, `rw_isobares::calculer` (dans le pool de fils de l'échéance) puis `geojson::vers_geojson`, écriture par le trait `Stockage` existant, puis état en dernier (ordre d'écriture inchangé). La même fonction est appelable seule : `rw_isobares::calculer(lat, lon, mslp_pa, &Params::default())` ou `produire(...)` pour obtenir en plus le gzip.

## 4. Exactitude [M, `rust/tools/cmp_isobares.py`]

Script : lance le Python (`pipeline.isobars.calcul.traiter`), le binaire Rust, puis compare octets, puis structure (nombre de lignes, niveaux, propriétés, sommets, écart maximal de position en mètres, extrema) et mesure les temps.

```sh
cd rust && cargo build --release -p rw-isobares --features cli && cd ..
S=<scratchpad>
PYTHONPATH=.:$S/pylib2:$S/pylib python3 rust/tools/cmp_isobares.py \
    --canon out/canon/arome/2026-10-17T06Z --bin rust/target/release/rw-isobares \
    --out $S/iso-cmp --echeances 0,12,24,36,48            # code de retour 1 au-delà de --tol-m (1 m par défaut)
# toutes les échéances et le chronométrage 52 :
... --echeances $(seq -s, 0 51) --repetitions 1 --mesures-52
```

Résultats :

* **Échéances 0, 12, 24, 36, 48** : octets identiques (78 221 / 87 018 / 82 135 / 84 136 / 93 442 octets), 0 sommet différent, **écart maximal 0 m**, extrema identiques (0, 2, 0, 3, 1).
* **52 échéances** : 52 fichiers sur 52 identiques octet pour octet, 1 531 lignes, 46 extrema, écart maximal **0 m**.
* **Chaîne complète** [T, `rw-lambda/tests/local.rs`] : GRIB AROME de l'échéance 12 → `rw-lambda` → faux S3 → `v1/isobares/arome/2026-10-17T06Z/12.geojson`, comparé à `out/isobars/arome/2026-10-17T06Z/12.geojson` du Python : identique octet pour octet.
* **Tolérance documentée** : le script déclare « conforme » un fichier identique, ou à défaut de structure identique avec des sommets à moins de `--tol-m` mètres (1 m par défaut, soit un dixième de l'arrondi du contrat : 1e-4° ≈ 11 m). Cette tolérance sert aux plates-formes où la libm diffère (voir limites) ; sur la machine de mesure l'écart est nul.

Tests unitaires [T] (`cargo test -p rw-isobares`, 24 tests) : plan incliné (droite exacte), cercle (anneau fermé, rayon à 0,05 maille), bord masqué (aucune fausse ligne, ligne ouverte), selles tranchées par la moyenne des coins, érosion du masque, niveaux comme `np.arange`, Douglas-Peucker (extrémités et pics conservés, droite réduite à 2 points, tolérance respectée sur un demi-cercle, fermeture des anneaux), `interp`, degré > 2 refusé, lissage (constante avec masque, impulsion symétrique, σ nul), extrema (A et D, bord écarté, seuil), chaîne complète sur une dépression synthétique, déterminisme et relecture du gzip, flottants à la manière de Python, arrondi de Python.

## 5. Mesures

| Mesure | Python | Rust |
|---|---|---|
| Une échéance, calcul seul, 1 fil | ≈ 0,8 s (0,15 lissage, 0,15 contours, 0,50 extrema) | **≈ 0,10 s** (0,057 lissage, 0,021 contours, 0,014 extrema) [M] |
| Une échéance, 4 fils / 20 fils | | 0,048 s / 0,044 s [M] |
| 52 échéances | 47,5 s, 49,9 s (deux mesures ; 42 s en doc J3) | **5,2 s** (1 fil) ; 2,5 s (4 fils) ; 2,3 s (20 fils) [M] |
| Pic mémoire du processus (une échéance, 1 fil) | 86 Mo | 83 Mo, dont la lecture du `.npz` (11 variables) ; le calcul seul ajoute **≈ 50 Mo** (ESTIMATION : une dizaine de tableaux de 6,4 Mo pendant le lissage) [M] |
| Binaire de banc `rw-isobares` (glibc, strippé, LTO) | | 0,94 Mo (avec `rw-cli`, `zip`) [M] |
| Binaire `bootstrap` de `rw-lambda` (x86_64 glibc) | | 3,44 Mo avec les isobares, contre 3,26 Mo dans [lambda.md](lambda.md) §9 : au plus **+0,18 Mo** (cette différence cumule aussi l'extraction de `rw-aws`, non isolée) [M] |
| GeoJSON | 73 à 99 ko par échéance (4,48 Mo les 52) | identique ; gzip niveau 6 : 27 à 30 ko par échéance [M] |

Dans la Lambda, le coût ajouté à une échéance AROME (1,2 s à 1 fil, 187 Mo) est de l'ordre de **0,1 s et ≈ 50 Mo de pointe** à 1 fil ; la pointe d'écriture s'ajoute aux champs canoniques encore en mémoire, donc rester sous 256 Mo n'est pas garanti : à mesurer sur le déploiement (la valeur de 187 Mo date d'avant ce calcul).

## 6. Limites et points ouverts

* **libm** : l'identité bit à bit repose sur `exp`, `cos`, `hypot` identiques à ceux de numpy. Vérifiée sur x86_64 glibc ; **non vérifiée sur arm64 musl** (cible de la Lambda). Une différence d'un ulp ne peut modifier un fichier que si un sommet tombe pile à une demi-unité d'arrondi de 1e-4° ou une distance de Douglas-Peucker à égalité avec la tolérance : l'écart attendu est alors d'un sommet de ≤ 11 m. Le script `cmp_isobares.py` mesure cet écart ; à lancer sur arm64.
* **Seul le GeoJSON est porté**. Restent en Python : `mvt.py` (tuiles vectorielles PMTiles, évaluation seulement, non retenue en production), `validation.py` (outil de contrôle) et le CLI parallèle. Si le MVT devient le format de production, son encodeur est à porter à part.
* **Pré-compression** non branchée (voir §2) : demande une extension de `Meta` dans `rw-aws`.
* **Paramètres** : `Params::default()` reproduit les défauts de `calcul.py` ; ils sont fixes dans la Lambda (pas de variable d'environnement). Les options `--sigma` et `--tol-km` du binaire de banc servent aux essais.
* **Grille** : 717 × 1 121 (AROME 0,025°) ; le code ne suppose pas la taille mais a été mesuré uniquement dessus. Une grille de moins de 3 points par axe est refusée.
* La section 8 de [lambda.md](lambda.md) (« Le calcul des isobares n'est pas porté en Rust ») est périmée pour le GeoJSON : à reformuler lors de la prochaine mise à jour de ce document.
