# PoC Rust : GRIB2 → champs canoniques → PMTiles (18 octobre 2026)

Réécriture en Rust du traitement d'**une échéance** (unité d'une invocation AWS Lambda arm64) et comparaison avec le Python de référence (`pipeline/ingest`, `pipeline/tiling`), profil `reduit2`. Code dans `rust/` (workspace Cargo : `rw-grib`, `rw-tiles`, `rw-cli`), outils de mesure dans `rust/tools/`, sorties dans `out/rust/`. Aucun code Python du dépôt n'a été modifié, rien n'a été commité, pas d'AWS.

Statut des chiffres : **mesuré** = relevé par `rust/tools/bench.py` ; **calculé** = extrapolation (voir §5).

## 1. Verdict

- **Exactitude : acquise.** Champs canoniques **identiques bit à bit** au Python (10 échéances, 2 modèles). Tuiles : **100 % des pixels de même code**, écart max **0 code**, métadonnées PMTiles et en-têtes identiques, fichiers relisibles par la bibliothèque `pmtiles` Python et par le décodeur du dépôt (`pipeline.tiling.decode`).
- **Gain : réel pour le décodage GRIB, quasi nul pour le tuilage tant qu'on garde libwebp.** Le temps du tuilage (≈ 90 % du total en Python) est dans l'encodeur WebP, qui est du C (libwebp) dans les deux cas. Rust avec libwebp gagne 1,4 à 2,1 × sur le total ; Rust avec l'encodeur pur Rust `image-webp` gagne **13 à 23 ×** mais produit des tuiles **+21 % (scalaires) à +75 % (vent) plus lourdes**.
- Recommandation : si le poids stocké importe, libwebp (statique) ; si c'est le coût de calcul Lambda, `image-webp` (à arbitrer, voir §6).

## 2. Choix techniques

### CCSDS / AEC (point dur n°1) : décodeur Rust pur, **`rust-aec` 0.1.1**

Évalués sur 244 champs réels (HD SP1/SP2, AROME SP1/SP2/SP3, 8, 12 et 16 bits), comparés élément par élément à eccodes :

| Crate | Résultat |
|---|---|
| **`rust-aec`** | **exact sur 244/244 champs** ; ≈ 90 ms pour un champ HD de 4,2 M valeurs (eccodes ≈ 55-65 ms via Python) |
| `tensogram-szip` 0.24 (ECMWF) | plus rapide (≈ 55 ms) mais **inexact** : 7 valeurs fausses sur un champ HD, jusqu'à des milliers sur d'autres (74 champs sur 244) |
| `oxiarc-szip` 0.4 | échec au décodage ; le test n'a été fait qu'avec un paramètre RSI mal converti (en échantillons au lieu de blocs). Corrigé ensuite dans le code, **non retesté** |
| `libaec-sys` (C) | non essayé : exige cmake + bindgen/libclang (clang absent) ; la voie « binding C statique » n'a donc pas été nécessaire. À garder en réserve |

Décision : `rust-aec`, pur Rust, donc cross-compilation arm64 triviale. Le CCSDS en Rust pur **n'est pas impossible**, il est déjà fait ; la seule précaution est de tester contre eccodes (test `fichier_reel_contre_eccodes`, et script de comparaison champ à champ). Le format est géré par `rw-grib` (≈ 400 lignes, écrites pour le PoC) : sections 3.0, 4.0/4.8, 5.42, masque de bits (6), messages concaténés ; formule `Y = (R + X·2^E)/10^D` en f64 comme eccodes ; 9999 → NaN ; ordre de balayage (j nord→sud, retournement si besoin). Les maxima (fenêtre n−1→n) et cumuls (0→n) sont vérifiés comme dans le Python, et le débit de précipitation est la différence de cumuls (min 0), la rafale HD `hypot(max_10efg, max_10nfg)`, etc. (port fidèle de `canon.py`, mêmes types f32/f64 qu'en NumPy).

### WebP sans perte sans alpha (point dur n°2) : deux encodeurs, au choix à l'exécution

Même tuile t2m z7 (258 × 258, extrait du Python) :

| Encodeur | Temps/tuile | Octets scalaire | Octets vent |
|---|---:|---:|---:|
| Pillow (libwebp, méthode 4/3) | ≈ 20 ms | 24 766 | 27 920 |
| **`webp` 0.3 = libwebp compilé statiquement (C)** | ≈ 20 ms | 24 838 | 27 776 |
| **`image-webp` 0.2.4 (Rust pur)** | **≈ 0,5 ms** | 31 286 (+26 %) | 49 616 (+78 %) |

Sur une échéance HD complète (4 threads, canonique Python identique) : libwebp méthode 4 : 3,58 s, 10,0 Mo ; méthode 0 : 1,89 s, 15,9 Mo (pire que `image-webp`) ; méthodes 1-3 : 3,3-3,65 s, 10,1-10,2 Mo ; méthode 6 : 122 s, 9,7 Mo ; `image-webp` : 0,27 s, 13,3 Mo.
Les images en niveaux de gris sont stockées en RVB (R = V = B) comme Pillow ; le décodage par le navigateur est le même (libwebp) : les tuiles `image-webp` ont été relues avec Pillow (libwebp) et donnent exactement les mêmes codes. Pas de canal alpha vérifié (`has_alpha() == false`).

### PMTiles v3 : écrit à la main (`rw-tiles/src/pmtiles.rs`)

Aucune crate d'écriture évaluée n'était nécessaire : port du `Writer` Python (index Hilbert signé, répertoires et métadonnées gzip, déduplication des tuiles identiques avec plages, répertoire racine < 16 257 octets avec feuilles de 4096 entrées doublées au besoin). Métadonnées JSON : mêmes clés, même ordre, mêmes valeurs que `metadata_for` (dont `quant_step = 0.2499999999999999`). Écart cosmétique : le JSON n'échappe pas les accents en `\uXXXX` (même dictionnaire une fois relu). Le Python déduplique par `hash()` sans comparer ; le Rust compare les octets.

### Reste
`rayon` : parallélisme par tuile (tuilage) et par champ GRIB (décodage) ; `--threads N` explicite. `zip` + `flate2/zlib-rs` pour lire et écrire les `.npz` (interopérabilité avec le Python, utilisée pour comparer et alimenter le tuilage Python). Profil : seule la famille `reduit2` (`reduit2`, `-ete`, `-hiver`) est portée ; `contrat` (wind12) ne l'est pas.

## 3. Écarts constatés (Rust contre Python, 5 échéances 0, 12, 24, 36, 48 × 2 modèles)

Sources : HD tirage 2026-10-17T03Z, AROME tirage 2026-10-17T06Z (GRIB de `out/grib`). `out/rust/bench/ecarts.json`.

| Contrôle | Résultat |
|---|---|
| Champs canoniques, toutes variables, lat/lon | **identiques bit à bit** (écart relatif max 0, 0 NaN différent) ; en plus égaux à 1e-4 près aux `out/canon` préexistants (tests `rw-cli/tests/reel.rs`) |
| Tuiles, 16 couples (modèle, variable), 5 échéances | **100,0000 % de pixels de code identique**, **écart max 0 code**, 0 pixel « pas de donnée » différent, mêmes ensembles de tuiles, mêmes métadonnées, mêmes en-têtes |
| Poids des fichiers PMTiles (libwebp) | rapport Rust / Python = 1,000 (± 0,1 % selon la version de libwebp) |
| Poids avec `image-webp` | × 1,21 à 1,29 (scalaires), × 1,71 à 1,75 (vent) ; mêmes pixels |

Le critère « ±1 code » est donc largement tenu (0 code). Les échéances 0 : `gust10`, `precip_rate`, `snow_fraction`, `cloud_total` absentes des deux côtés (normal).

## 4. Mesures

Machine partagée de 20 cœurs (charge 1 min médiane 2,9 pendant le banc, max 4,9). 3 répétitions entrelacées Python/Rust, **médiane** de la somme sur les 5 échéances ; un processus par échéance (comme une invocation, démarrage inclus), sous `/usr/bin/time -v` ; caches de fichiers chauds. « CPU » = utilisateur + système. Python : `--workers N` pour le tuilage, ingestion monothread (donc identique à 1 et 4 threads). Tableaux complets : `out/rust/bench/resume.md`. Les chiffres ci-dessous sont en secondes **murales** sauf mention.

### (a) GRIB → canonique (5 échéances, **mesuré**)

| | HD 1 th | HD 4 th | AROME 1 th | AROME 4 th |
|---|---:|---:|---:|---:|
| Python | 32,6 | 32,5 | 13,3 | 13,3 |
| Rust (décode l'échéance, écrit le npz niveau 6) | 10,6 | 7,9 | 2,2 | 1,7 |
| Rust « décode tout » (même travail que le Python) | 13,2 | 8,6 | 6,6 | 2,9 |
| Pic mémoire Python / Rust (Mo) | 902 / 626-697 | | 493 / 187-198 | |

Deux effets distincts. **Algorithmique** : le Python décode tous les messages des fichiers de l'échéance et de la précédente (en AROME, un groupe de 6 échéances à chaque fois) ; le Rust ne décode que les champs de l'échéance n et les cumuls de n−1 (16 champs au lieu de beaucoup plus). Ce choix s'appliquerait aussi en Python. **Langage** : à travail égal (« décode tout »), Rust est 2,5 × (HD) et 2 × (AROME) plus rapide à 1 thread. Où va le temps en HD (Rust, 1 thread, 5 éch.) : écriture du `.npz` compressé 6,1 s, décodage AEC 3,9 s, calcul 0,4 s, lecture 0,1 s : l'écriture du npz, absente d'une chaîne en mémoire, domine. Côté Python (échéance 48 HD, `ingest_stats.json`) : décodage eccodes 2,2 s sur 7,0 s.

### (b) Tuilage (5 échéances, entrée = même `.npz` Python, **mesuré**)

| | HD 1 th | HD 4 th | AROME 1 th | AROME 4 th |
|---|---:|---:|---:|---:|
| Python (libwebp via Pillow) | 84,6 | 23,3 | 69,5 | 18,6 |
| Rust + libwebp (C statique) | 70,1 | 19,4 | 58,2 | 15,1 |
| Rust + `image-webp` (pur) | **6,4** | **3,3** | **3,4** | **1,2** |
| Pic mémoire Python / Rust (Mo) | 227 / 206-263 | | 84 / 62-98 | |

Avec libwebp, ≈ 96 % du temps Rust est l'encodage (`rééchantillonnage + WebP` = 67,4 s sur 70,1 en HD à 1 thread). Le rééchantillonnage bilinéaire + quantification Rust vaut moins de 1 s sur les 5 échéances HD (déduit : 6,4 s avec `image-webp` dont l'encodage). Le gain de Rust avec libwebp (1,2 ×) vient seulement du rééchantillonnage NumPy remplacé.

### (c) Total, une échéance de bout en bout (5 échéances, **mesuré**)

| | HD 1 th | HD 4 th | AROME 1 th | AROME 4 th |
|---|---:|---:|---:|---:|
| Python (a)+(b), 2 processus | 117,2 | 55,8 | 82,9 | 31,8 |
| Rust (a)+(b) via npz | 80,7 | 27,3 | 60,4 | 16,8 |
| **Rust en mémoire, libwebp** | **72,0** | **19,2** | **58,9** | **15,3** |
| **Rust en mémoire, `image-webp`** | **9,0** | **3,1** | **4,0** | **1,4** |
| CPU total Python → Rust libwebp → Rust pur | 117 → 72 → 9 | 118 → 72 → 9 | 83 → 59 → 4 | 83 → 59 → 4 |

### Extrapolation à un tirage complet : 52 échéances × 2 modèles (**calculée**)

Formule : échéance 0 + 51 × moyenne des échéances 12, 24, 36, 48 (mesurées), somme HD + AROME. Hypothèse : les échéances intermédiaires ressemblent à ces quatre (non vérifié). Temps **CPU cumulé** (≈ facturation Lambda à mémoire égale), puis temps mur si les 104 invocations étaient séquentielles à 1 thread :

| | CPU cumulé (s) | Mur séquentiel, 1 th (min) |
|---|---:|---:|
| Python | 1318 + 909 = 2227 (≈ 37 min) | 37 |
| Rust + libwebp | 815 + 650 = 1465 | 24 |
| Rust + `image-webp` | 105 + 46 = **151 (2,5 min)** | **2,5** |

Mur d'une seule invocation (extrapolé, 1 thread, libwebp) : HD ≈ 15,7 s Rust contre 25,4 s Python (`(815 s ou 1320 s) / 52`) ; `image-webp` ≈ 2 s. Avec 4 threads : HD ≈ 4,2 s (libwebp) et 0,7 s (pur). En Lambda la mémoire fixe les vCPU (≈ 1 vCPU par 1769 Mo) : 4 threads n'ont de sens qu'au-delà de ≈ 7 Go.

### Binaire et démarrage (**mesuré**)

| Variante | Taille (strip, LTO fat, panic=abort) | Démarrage (`rw version`, médiane de 200) |
|---|---:|---:|
| x86_64 + libwebp (C) + `image-webp` | 1,53 Mo | 0,85 ms |
| x86_64 `image-webp` seul | 1,03 Mo | 0,86 ms |
| **aarch64 musl statique, `image-webp` seul** | **0,99 Mo** | non exécuté (pas de qemu) |
| Python : import des modules (eccodes, numpy, PIL, pmtiles) | | 191 ms |

Le binaire arm64 a été **construit** (`rustup target add aarch64-unknown-linux-musl`, seule installation faite, réversible avec `rustup target remove` ; éditeur de liens `rust-lld`, aucun compilateur C croisé). La variante avec libwebp (C) exige un compilateur C arm64 : `cargo-lambda`/`zig` non présents et non installés, donc **non construite** ; la procédure est en §7. Aucun test d'exécution arm64 ni sur Lambda.

## 5. Difficultés rencontrées

- **CCSDS** : un décodeur pur Rust « validé » (`tensogram-szip`) est faux sur des données réelles ; seule la comparaison systématique à eccodes l'a révélé. Garder ce test en intégration continue.
- **Reproduction bit à bit** : demande de reproduire les types NumPy (f32 pour les cumuls et la nébulosité, f64 pour `hypot` et le bilinéaire, `maximum` qui propage NaN, `round` au pair le plus proche, arrondi des axes à 1e-6). Le résultat est exact ici ; un autre libm (musl arm64) pourrait inverser un bit de sinh/atan dans le calcul des latitudes de pixel, donc ±1 code rarissime : **non vérifié sur arm64**.
- **Hilbert** : l'implémentation de référence utilise des entiers qui deviennent négatifs ; un port naïf en `u64` déborde.
- Les `fixtures/` ne contiennent aucun GRIB : les tests sur données réelles sont ignorés si `out/grib` est absent.

## 6. Où le temps est passé, et décision à prendre

1. En Python comme en Rust avec libwebp, **le tuilage = ≈ 70-85 % du total** et c'est de l'encodage WebP en C : réécrire en Rust n'y change presque rien.
2. Le gain « langage » mesurable est sur le GRIB (2-2,5 ×) et le rééchantillonnage (négligeable en Rust), plus la mémoire (−30 à −60 %) et le démarrage (0,9 ms contre 191 ms).
3. Le vrai levier est la **qualité de compression WebP** : `image-webp` est ×13 à ×23 plus rapide pour **+21 à +75 % d'octets**. Sur un tirage de ≈ 1 Go, cela fait ≈ +0,3 Go stockés/transférés contre ≈ 2 100 s de CPU économisées. À arbitrer selon le coût S3/CDN et Lambda ; une voie intermédiaire (libwebp méthode 1-3 : −8 % de temps seulement) n'apporte rien. Une amélioration de l'encodeur pur Rust (palette, LZ77) est hors périmètre.

## 7. Reproduction et déploiement

```sh
# depuis la racine du dépôt
export PYLIB_ECCODES=…/scratchpad/pylib PYLIB_PMTILES=…/scratchpad/pylib2 GRIB_00H06H=…/scratchpad/grib
rust/tools/reproduire.sh          # compile, teste, prépare out/rust/src, lance le banc (≈ 30 min) et les comparaisons
python3 rust/tools/resumer.py out/rust/bench/resultats.json     # tableaux de §4
```
Une échéance à la main :
```sh
rust/target/release/rw run --model arome-hd --run 2026-10-17T03:00:00Z --step 12 \
    --src out/rust/src --out out/rust/rs/tiles --threads 4 [--webp image-webp] [--canon-out out/rust/rs/canon]
```
Tests : `cd rust && cargo test --release` (32 tests : GRIB synthétiques avec masque, 9999, fenêtres, tronqués ; bilinéaire ; quantification ; Hilbert ; PMTiles avec feuilles ; tuilage d'un champ linéaire relu dans le demi-pas avec bordure continue ; npz ; canonique contre `out/canon`, ignorés si absents).

Déploiement arm64 (non fait) :
- Rust pur : `rustup target add aarch64-unknown-linux-musl && RUSTFLAGS="-C linker=rust-lld" cargo build --release -p rw-cli --no-default-features --features webp-pure --target aarch64-unknown-linux-musl` (testé, binaire de 0,99 Mo ; runtime `provided.al2023`, arm64).
- Avec libwebp : `cargo lambda build --release --arm64` (cargo-lambda utilise zig comme éditeur de liens et compilateur C) ou `cargo zigbuild --target aarch64-unknown-linux-gnu`.
- Adaptations nécessaires pour Lambda, non écrites : lecture des GRIB depuis S3 (un fichier de groupe 0,025° de 6 échéances est lu à chaque invocation), écriture des PMTiles dans S3, point d'entrée `lambda_runtime`. Le binaire actuel travaille sur fichiers locaux.
- Suite : le binaire Lambda (`rw-lambda`, source HTTP, sortie S3, SQS, idempotence, état d'échéance) est décrit dans [lambda.md](lambda.md).

## 8. Limites et points ouverts

- Mesures sur un seul tirage par modèle, 5 échéances ; extrapolation non vérifiée sur les 52. Machine partagée (charge 2,9 en médiane) : écarts de quelques % entre répétitions possibles (`mur_s_reps` dans `resultats.json`).
- Le tuilage Python lancé avec `--workers N` utilise `fork` ; le coût de démarrage des processus est inclus.
- Non faits : exécution sur arm64/Lambda, profil `contrat`/wind12, test de décodage dans un vrai navigateur (relecture faite avec Pillow/libwebp, même décodeur que Chrome), ré-évaluation d'`oxiarc-szip` et de `libaec-sys`.
- `rust-aec` est 1,5 × plus lent que libaec C (estimé via eccodes) ; le décodage AEC représente 3,9 s sur 72 s (HD, 5 éch.), donc peu pesant pour l'instant.
