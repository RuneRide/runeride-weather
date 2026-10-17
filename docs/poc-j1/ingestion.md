<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# PoC J1 : brique ingestion

Transforme les GRIB2 AROME / AROME HD de Météo-France en **champs canoniques** `.npz` (contrat [poc-j1-contrat.md](../poc-j1-contrat.md) §2). Tout ce qui est chiffré ci-dessous a été **mesuré** le 17/10/2026 sur le tirage `2026-10-17T06:00:00Z` ; ce qui est marqué ESTIMATION ne l'a pas été.

## 1. Ce qui a été fait

| Élément | Fichier |
|---|---|
| Téléchargement courtois (3 connexions max, reprise `.part` + `Range`, contrôle de taille par `Content-Length`, jamais de retéléchargement) | `pipeline/ingest/download.py` |
| Décodage eccodes, masque `9999` → NaN, grille nord→sud, longitude −180..180 | `pipeline/ingest/grib.py` |
| Variables canoniques (calcul pur numpy, contrôle des fenêtres GRIB) et écriture `.npz` | `pipeline/ingest/canon.py` |
| Ligne de commande `python3 -m pipeline.ingest` | `pipeline/ingest/cli.py`, `__main__.py` |
| Comparaison à une référence (fixtures) | `pipeline/ingest/verifier.py` |
| Validations scientifiques (JSON complet dans `out/canon/validations.json`) | `pipeline/ingest/validations.py` |
| Tests (24, `unittest` : `pytest` n'est pas installé) | `tests/ingest/test_ingest.py` |

Sorties produites dans `out/canon/` : AROME HD `arome-hd/2026-10-17T06Z/00.npz … 06.npz` et AROME 0,025° `arome/2026-10-17T06Z/00.npz … 06.npz`, chacun accompagné d'un `ingest_stats.json` (temps, tailles, mémoire). Les GRIB téléchargés sont dans `out/grib/arome-hd/2026-10-17T06:00:00Z/` (183 Mo, 9 fichiers).

### Rejouer

```sh
export PYTHONPATH=<scratchpad>/pylib          # eccodes (contrat §6)
python3 -m pipeline.ingest --model arome-hd --run 2026-10-17T06:00:00Z --steps 0-6 \
        --src <scratchpad>/grib --download --out out/canon
python3 -m pipeline.ingest --model arome    --run 2026-10-17T06:00:00Z --steps 0-6 \
        --src <scratchpad>/grib --out out/canon
python3 -m pipeline.ingest.validations --src <scratchpad>/grib out/grib --canon out/canon
RUNERIDE_GRIB_SRC=<scratchpad>/grib python3 -m unittest discover -s tests/ingest -v
```

`--src` accepte plusieurs dossiers (recherche récursive, nom avec ou sans suffixe de tirage, comme les échantillons `hd3/`). Les fichiers absents sont téléchargés dans `--dl-dir` (défaut `out/grib/<modèle>/<tirage>`). Les fichiers déjà présents sont **vérifiés** (taille contre `HEAD`) mais jamais retéléchargés ; un fichier de mauvaise taille fait échouer le traitement. `--sans-bornes` désactive le bornage (anciennes sorties, voir §3).

### Choix de conception

- **Échéance n−1** : `fichiers_requis` ajoute toujours n−1. En 0,025° le groupe est trouvé par `groupe_025` (`00H06H`, `07H12H`, …, `49H51H`) : l'échéance 7 charge `00H06H` pour l'échéance 6 (testé). À l'échéance 1, le cumul de l'échéance 0 vaut 0 par définition (les fichiers `00H` ne contiennent aucun cumul, **mesuré**).
- **Fenêtres contrôlées à chaque lecture** : un cumul doit être `accum` de 0 à n, un maximum `max` de n−1 à n, sinon l'ingestion s'arrête (`ValueError`) plutôt que de calculer faux.
- **Arithmétique** : cumuls et nébulosité en `float32`, rafale HD en `float64` puis `float32`. C'est la seule combinaison qui retrouve la fixture bit à bit (essayé : somme `float64` des cumuls, formule de nébulosité en `float64` ou avec `0,01×` : écarts de 1e-6 à 2e-5).
- **Écriture** : `savez_compressed` dans un fichier temporaire puis renommage atomique ; `meta` en JSON ASCII.
- Les paramètres hors contrat (SP3, `unknown`, `ssrd`, `2d`, `blh`, `min_2t`…) sont ignorés à la lecture (filtre sur `shortName`).

## 2. Reproduction des fixtures (point 2)

Reconstruction de l'échéance 3 d'AROME HD depuis `SP1 03H`, `SP2 03H`, `SP2 02H` (configuration par défaut, après régénération des fixtures par le coordinateur) :

| Référence | Variables identiques **bit à bit** | Écart |
|---|---|---|
| `out/hd_step03.npz` (2801 × 1791) | 9 / 9, `lat`, `lon` et masque NaN (856 072 points) identiques | **0** |
| `fixtures/hd_step03_crop.npz` (300 × 300) | 9 / 9 | **0** |

Avec les fixtures régénérées (rh2m et cloud_total bornées) et la configuration par défaut, la reproduction est **9/9 bit à bit** sur les deux fixtures, sans `--sans-bornes`.

## 3. Écarts au contrat

1. **`rh2m` et `cloud_total`** : le contrat a été révisé (17/10) et les fixtures régénérées avec les deux variables bornées à 0..100. **Résolu** : la production borne les deux par défaut ; `--sans-bornes` ne sert plus qu'à reproduire d'anciennes sorties non bornées (rh2m max 100,0166, cloud_total max 100,0033).
2. **Sources > 100** : les couches `lcc/mcc/hcc` dépassent elles-mêmes 100 de quelques millièmes (max 100,0033) ; 21 à 27 % des points ont un total brut > 100. Le bornage les ramène à 100. **Résolu.**
3. **Précipitations AROME 0,025° : deux paquets.** `tirf` est dans SP2 mais `tsnowp` et `tgrp` sont dans **SP1** (le contrat dit « SP2 »). Les deux paquets SP1 et SP2 sont donc lus pour les deux modèles. `tp` (SP1) sert de recoupement (§4b) et n'est pas écrit dans le `.npz`.
4. **`cloud_total` absente à l'échéance 0** dans les deux modèles (aucun champ de nébulosité dans `00H`, **mesuré**) ; `gust10` et `precip_rate` aussi (conforme). Donc à l'échéance 0 : `wind10_u/v`, `t2m`, `rh2m`, `cape` (+ `mslp` en AROME) seulement.
5. **Fenêtres (point d)** : conformes au contrat, rien à corriger, mais les cumuls ont besoin d'une précision : voir §4d. Le débit est borné à 0 et la somme des débits s'écarte du cumul d'au plus 2,3 millièmes de mm (§4b).
6. **Hypothèse `gust10` HD** : confirmée (§4a), les composantes de rafale sont des composantes du vecteur rafale (signées), pas des maxima indépendants ; le contrat peut passer de « hypothèse à valider » à « validée ».
7. Les `.npz` HD pèsent **55–58 Mo** (conforme au « ~55 Mo » du contrat) ; la compression `zlib` coûte 4,75 s par échéance HD (§5). Aucun autre écart.

## 4. Validations scientifiques (point 5)

Grille commune de comparaison AROME : 1121 × 717 = 803 757 points, dont **665 681 valides** (82,8 %). Le rééchantillonnage HD → AROME est bilinéaire aux nœuds AROME ; « lissé » = moyenne 3×3 HD (≈ 0,03°) avant échantillonnage. Un nœud AROME tombe exactement sur un nœud HD pour 1 point sur 5 (0,025 = 2,5 × 0,01), sinon on interpole.

### 4a. Rafales : `gust10` HD = `hypot(max_10efg, max_10nfg)` contre `max_i10fg`

| Échéance | Corrélation | Biais HD−AROME (m/s) | RMSE (m/s) | Écart max (m/s) | Corr. lissé 3×3 | RMSE lissé |
|---|---|---|---|---|---|---|
| 1 | 0,99974 | −0,0001 | 0,104 | 6,32 | 0,99876 | 0,227 |
| 2 | 0,99973 | −0,0000 | 0,106 | 5,98 | 0,99878 | 0,228 |
| 3 | 0,99969 | −0,0003 | 0,113 | 4,26 | 0,99875 | 0,226 |
| 4 | 0,99966 | −0,0001 | 0,113 | 5,26 | 0,99873 | 0,220 |
| 5 | 0,99964 | −0,0004 | 0,113 | 5,65 | 0,99869 | 0,214 |
| 6 | 0,99961 | −0,0002 | 0,114 | 5,66 | 0,99861 | 0,214 |

Moyennes : ~7,2 m/s (éch. 1) à ~9,0 m/s (éch. 6) ; 664 904 points comparés.

**Test direct dans AROME 0,025°**, qui contient à la fois `max_i10fg` et les composantes : `hypot(max_10efg, max_10nfg)` contre `max_i10fg` donne une corrélation de **0,9999995**, un biais de −4e-6 m/s, une RMSE de **0,004 m/s** et un écart maximal de **0,0147 m/s** (échéances 1 à 6). L'hypothèse est donc **confirmée** (écart de l'ordre du pas de codage). Ce n'est pas un maximum de composantes indépendantes : la composante est négative en moyenne pour **51 à 67 %** des points (est) et **83 à 84 %** (nord), et le module reste exact. Les valeurs négatives sont normales (vent d'ouest / de nord) et ne gênent pas le calcul.

Cohérence physique : la rafale est inférieure au module du vent instantané en seulement 0,05 à 0,85 % des points (AROME) et 0,05 à 0,72 % (HD) ; ce n'est pas anormal, le vent instantané peut dépasser le maximum de l'heure précédente (fenêtre n−1 → n, vent à l'instant n).

### 4b. Cumuls

**Monotonie** (différence de cumul entre échéances consécutives 0 → 6) :

| Modèle | Cumul | Plus petite différence | Intervalles décroissants (< −1e-6 mm) | Cumul max à 6 h |
|---|---|---|---|---|
| HD | `tirf` | −0,00141 mm | 489 788 | 65,06 mm |
| HD | `tgrp` | −3,1e-6 mm | 761 | 0,138 mm |
| HD | `tsnowp` | −5,6e-8 mm | 0 | 5,6e-5 mm |
| AROME | `tirf` | −0,000977 mm (= 2⁻¹⁰) | 47 824 | 62,01 mm |
| AROME | `tp` | −0,000977 mm | 47 827 | 62,01 mm |
| AROME | `tgrp`, `tsnowp` | 0 | 0 | 0,055 mm ; 6e-7 mm |

Les cumuls ne sont **pas strictement monotones** : des décréments de l'ordre du millième de mm apparaissent (`tirf` : 1,2 % des couples point-intervalle en AROME, 47 824 sur 6 × 665 681 ; 2,0 % en HD, 489 788 sur 6 × 4,16 M). L'ordre de grandeur de 2⁻¹⁰ mm est compatible avec la précision de codage du GRIB (non vérifié dans les clés). D'où le plancher à 0 du débit (déjà dans le contrat).

**Somme des débits canoniques contre cumul total** (`tirf + tsnowp + tgrp`) :

| Échéance | 1 | 2 | 3 | 4 | 5 | 6 |
|---|---|---|---|---|---|---|
| HD, écart max (mm) | 0 | 0,00049 | 0,00098 | 0,00098 | 0,00195 | 0,00234 |
| HD, écart moyen (mm) | 0 | 2,4e-6 | 1,7e-5 | 1,7e-5 | 3,3e-5 | 6,6e-5 |
| AROME, écart max (mm) | 0 | 0,00049 | 0,00098 | 0,00098 | 0,00195 | 0,00195 |
| AROME, écart moyen (mm) | 0 | 2,4e-6 | 1,7e-5 | 1,7e-5 | 3,5e-5 | 5,5e-5 |

Le cumul maximal vaut 22,8 mm (éch. 1) à 65,1 mm (éch. 6) en HD : les écarts sont de l'ordre de 10⁻⁵ du cumul, **dus uniquement au plancher à 0**.

**Recoupement `tp` (AROME)** : `tirf + tsnowp + tgrp` contre `tp` : corrélation 0,99999999999, RMSE ≤ 7,2e-6 mm, écart max ≤ 0,00097 mm (échéances 1, 3, 6). `tp` est donc bien le total.

La neige est quasi nulle ce jour-là (`tsnowp` max 5,6e-5 mm HD) : `snow_fraction` vaut 0 presque partout et **n'a pas pu être validée sur un cas de neige**.

Recouvrement entre groupes 0,025° : le seul groupe réel disponible est `00H06H`. Le test de jonction (échéance 7 → échéance 6 du groupe précédent) est vérifié par test unitaire sur la logique de sélection de fichiers, **pas sur de vraies données** (le groupe `07H12H` n'a pas été téléchargé). Pas de recouvrement entre groupes (échéance 6 seulement dans `00H06H`, échéance 7 seulement dans `07H12H`) : conforme à `donnees-arome.md` §4.

### 4c. Nébulosité totale

Formule : `100 × (1 − (1−bas)(1−moyen)(1−haut))` (recouvrement aléatoire). Les chiffres de ce tableau sont ceux mesurés **avant** le bornage à 100 (la production borne désormais ; `validations.json` régénéré, max = 100). Résultats, échéances 1, 3, 6 :

| | HD éch. 1 | HD éch. 3 | HD éch. 6 | AROME éch. 1 | AROME éch. 3 | AROME éch. 6 |
|---|---|---|---|---|---|---|
| min / max (%) | 0,0087 / 100,0033 | idem | idem | idem | idem | idem |
| moyenne (%) | 64,50 | 63,53 | 64,95 | 64,50 | 63,54 | 64,95 |
| part > 95 % | 49,6 % | 48,8 % | 45,7 % | 49,6 % | 48,8 % | 45,7 % |
| part < 5 % | 21,4 % | 21,5 % | 15,0 % | 21,4 % | 21,5 % | 15,0 % |
| part du total > 100 % | 26,5 % | 24,4 % | 21,3 % | 26,5 % | 24,4 % | 21,3 % |

- **Plage** : 0,0087 à 100,0033 %. Le dépassement de 100 vient des couches elles-mêmes (max 100,0033 / 100,0026 / 100,0028 %), pas de la formule.
- **Cohérence formelle** : le total est ≥ la couche la plus nuageuse (la formule l'assure) ; 27,6 % des points (échéance 3, AROME) ont pourtant un total *inférieur* de plus de 0,001 % à `max(bas, moyen, haut)` ; **mesuré** : 100 % de ces points ont une couche > 100 (donc hors plage physique) et l'écart reste ≤ 0,0033 %, jamais de cas avec toutes les couches ≤ 100.
- **Confrontation à un autre champ** (sans en interpréter le sens) : le champ `unknown` (discipline 0, catégorie 6, numéro 1) de SP1 0,025° a une plage de 0,0087 à 100,0087 et, aux échéances 1 à 6, une moyenne de 62,71 à 64,93 % contre 62,73 à 64,95 % pour `cloud_total`. Corrélation **0,99993 à 0,99996**, biais de `cloud_total` +0,013 à +0,018 %, RMSE 0,38 à 0,48 %, écart absolu moyen 0,06 à 0,08 %, **écart maximal 22,6 à 34,3 points** (quelques points isolés). Cet accord numérique ne prouve pas l'identité du paramètre ; il montre que l'hypothèse de recouvrement aléatoire reproduit ce champ à ~0,4 % près en RMSE sur ce cas. **À confirmer** par la documentation Météo-France avant de conclure ; en HD ce champ n'existe pas.
- Limite : un seul tirage, une seule situation.

### 4d. Fenêtres exactes (clés GRIB)

Lues sur tous les messages `accum` / `max` / `min` de SP1 et SP2 (HD échéances 0–6, AROME 0,025° `00H06H`) : `stepType`, `startStep`, `endStep`, `typeOfStatisticalProcessing`, `lengthOfTimeRange` (unité : heure).

| Modèle | Champ | stepType | `typeOfStatisticalProcessing` | Fenêtre à l'échéance n |
|---|---|---|---|---|
| HD, AROME | `tirf`, `tsnowp`, `tgrp` | `accum` | 1 (cumul) | **0 → n**, `lengthOfTimeRange = n` |
| AROME | `tp`, `ssrd` | `accum` | 1 | 0 → n |
| HD, AROME | `max_10efg`, `max_10nfg` | `max` | 2 (maximum) | **n−1 → n**, `lengthOfTimeRange = 1` |
| AROME | `max_i10fg`, `max_2t` | `max` | 2 | n−1 → n |
| AROME | `min_2t` | `min` | 3 (minimum) | n−1 → n |

Le contrat est exact (« cumuls depuis l'échéance 0 », « rafales = maximum sur la dernière heure »). Aucun champ cumulé ni extremum n'est présent à l'échéance 0 (en dehors de `00H` qui ne contient que des champs `instant`). Les nébulosités (`lcc`, `mcc`, `hcc`) sont `instant`, présentes à partir de l'échéance 1.

### 4e. AROME HD contre AROME 0,025°

Échéances 0 à 6, HD échantillonné aux nœuds AROME (664 904 points valides communs ; 663 454 en version lissée) :

| Variable | Corrélation (ponctuel) | RMSE (ponctuel) | Biais | Écart max | RMSE lissé 3×3 |
|---|---|---|---|---|---|
| vent `u` | 0,9999 | 0,042–0,046 m/s | ≤ 1e-4 | 4,8 m/s | 0,10–0,115 |
| vent `v` | 0,9999 | 0,057–0,062 m/s | ≈ 0 | 6,1 m/s | 0,13–0,14 |
| vitesse du vent | 0,9998–0,9999 | 0,064–0,068 m/s | ≈ 0 | 5,9 m/s | 0,13–0,14 |
| `t2m` | 0,9992–0,9993 | 0,17–0,20 K | +0,0002 à +0,0004 K | 5,8 K | 0,15–0,22 |
| `rh2m` | 0,9996–0,9997 | 0,32–0,35 % | ≤ 0,004 % | 26 % | 0,71–0,82 |

Sens inverse (AROME interpolé en bilinéaire sur la grille HD, 4,15 M points valides) : corrélation 0,9988–0,999 sur le vent, RMSE 0,17–0,18 m/s ; `t2m` : 0,9976–0,9981, RMSE 0,30–0,31 K ; rafale 0,998, RMSE 0,26–0,29 m/s. Sur les seuls nœuds communs aux deux grilles : vent RMSE 0,044–0,046 m/s, rafale RMSE **0,034–0,039 m/s** (corrélation 1,0000), `t2m` RMSE 0,20–0,22 K (écart max 5,8 K).

**Lecture** : les deux modèles se ressemblent **très fortement** (corrélation ≥ 0,999) : l'information additionnelle de HD tient dans un écart de ~0,17 m/s en vent et ~0,3 K en température par rapport à une simple interpolation d'AROME. Les écarts maximaux (jusqu'à 5–6 m/s ou K) sont locaux (non analysés ici : relief / côtes à examiner, **non établi**). Conséquence produit : hors relief marqué, HD est peu différent d'AROME interpolé ; l'intérêt de HD aux zooms ≥ 7 est surtout le **relief et le trait de côte**, à vérifier visuellement.

### 4f. Paramètres `unknown` rencontrés

Aucun sens n'est déduit. `typeOfLevel` / niveau / `stepType` sont ceux de la clé GRIB ; plage = premier message.

| Résolution | Paquet | Discipline | Catégorie | Numéro | Niveau | `stepType` | Échéances vues | Plage (premier message) |
|---|---|---|---|---|---|---|---|---|
| 0,01° | SP2 | 0 | 16 | 193 | surface 0 | instant | 1–6 | 0 à 41,17 ; moyenne 0,077 |
| 0,01° | SP3 | 0 | 5 | 7 | aucun (pas de `typeOfLevel` lisible) | instant | 0, 3 | 244,37 à 294,74 ; moyenne 283,57 ; gabarit de produit 32 |
| 0,025° | SP1 | 0 | 6 | 1 | surface 0 | instant | 1–6 | 0,0087 à 100,0087 ; moyenne 64,48 (voir 4c) |
| 0,025° | SP3 | 0 | 1 | 6 | surface 0 | accum (gabarit 8) | 1–6 | −0,65 à 0,055 ; moyenne −0,112 |
| 0,025° | SP3 | 0 | 1 | 64 | surface 0 | instant | 0–6 | 1,47 à 49,63 ; moyenne 18,2 |
| 0,025° | HP3 (30 premiers Mo) | 0 | 16 | 192 | hauteur 500 à 3000 m (7 niveaux) | instant | 1–6 | 0 à 14,65 ; moyenne 0,043 |

L'unité eccodes est `unknown` partout. Le paquet HD SP3 n'a été lu que pour l'échéance 3 et 0 ; HP1/HP2/IP* 0,025° tronqués à 30 Mo (échantillons `part/`) : l'inventaire **n'est pas exhaustif** pour ces gros paquets (la liste complète des `unknown` de HP1, IP* reste à faire sur fichiers entiers). Aucun de ces champs n'est rattaché à une variable canonique (le (0, 6, 1) n'est utilisé qu'en recoupement, §4c).

## 5. Mesures de performance (point 6)

Poste de développement (20 cœurs, un seul processus Python utilisé), eccodes 2.50, numpy 2.5.4.

### Téléchargement (AROME HD, SP1 + SP2, échéances 0–6, 3 connexions simultanées)

9 fichiers nouveaux sur 14 (les 5 autres étaient déjà présents et n'ont pas été retéléchargés) : **191,8 Mo en 17,6 s** de temps réel, soit **≈ 10,9 Mo/s** au total et 2,4 à 6,3 Mo/s par fichier.

| Fichier | Taille (Mo) | Temps (s) | Débit (Mo/s) |
|---|---|---|---|
| SP1 01H | 24,4 | 8,07 | 3,0 |
| SP1 02H | 24,3 | 5,34 | 4,6 |
| SP1 04H | 23,5 | 4,81 | 4,9 |
| SP1 05H | 23,9 | 3,78 | 6,3 |
| SP1 06H | 23,3 | 3,95 | 5,9 |
| SP2 01H | 17,4 | 3,96 | 4,4 |
| SP2 04H | 18,1 | 7,32 | 2,5 |
| SP2 05H | 18,4 | 7,68 | 2,4 |
| SP2 06H | 18,6 | 4,55 | 4,1 |

Un « paquet » HD (SP1 + SP2 d'une échéance) pèse ≈ 41 à 43 Mo. ESTIMATION : 52 échéances de SP1 + SP2 (≈ 2,2 Go) en ≈ 200 s à 11 Mo/s. Le débit annoncé (≈ 10 Mo/s par connexion) n'est pas atteint par connexion ici ; il l'est au total avec 3 connexions. Mesure de réseau d'une seule exécution, forte variance.

### Décodage (eccodes, filtre sur les messages utiles)

| Fichier | Taille (Mo) | Messages décodés | Temps (s) | Remarque |
|---|---|---|---|---|
| HD SP1 `0xH` (échéances 1 à 6) | 23,3 – 24,4 | 6 | 0,38 – 0,50 | 4 champs à l'éch. 0 : 0,50 s |
| HD SP2 | 17,4 – 18,6 | 7 (1 à l'éch. 0) | 0,36 – 0,61 | 0,07 s à l'éch. 0 |
| HD SP1 / SP2 `03H` (échantillons) | 23,6 / 17,7 | 6 / 7 | 0,77 / 0,83 | probablement cache froid, **non vérifié** |
| AROME `00H06H` SP1 | 60,2 | 71 (7 échéances) | 1,16 | |
| AROME `00H06H` SP2 | 47,0 | 31 | 0,36 | |

Soit ≈ 60 Mo/s de GRIB compressé décodés, ≈ 75 millions de points/s (HD SP1 : 6 champs × 5,02 M en 0,4 s). Un « paquet » HD (SP1 + SP2, 13 champs décodés) coûte ≈ **0,8 – 1,0 s**.

### Échéance complète

| Étape | Temps | Note |
|---|---|---|
| HD, une échéance (décodage n et n−1 en cache, calcul, écriture) | 5,6 – 8,4 s selon l'échéance (4,4 s à l'éch. 0 ; 7,0 s en exécution isolée de l'éch. 3) | dont **4,75 s** de `savez_compressed`, contre 0,08 s pour `savez` non compressé (180,6 Mo au lieu de 55,6 Mo) |
| HD, 7 échéances (0 à 6) | 44,6 s au total | |
| AROME, 7 échéances | 8,7 s au total (1,04 – 1,11 s par échéance) | 2,3 s à l'éch. 0 (décodage du groupe entier) |
| Lecture d'un `.npz` HD | 0,42 s | |

**Mémoire** (pic RSS) : HD, une échéance isolée **922 Mo** ; HD, 7 échéances **1 118 Mo** ; AROME 0,025°, **523 Mo**. Poids des sorties : HD 34,4 Mo (éch. 0) puis 54,3 – 58,4 Mo ; AROME 6,9 Mo (éch. 0) puis ≈ 9,2 Mo. Total `out/canon` : 414 Mo.

**Débit de traitement** : HD ≈ 5,7 s par échéance (valeur typique) ; 52 échéances ≈ 5 min en série (ESTIMATION) ; la compression est évitable ou parallélisable (20 cœurs inutilisés) si le temps de traitement devient critique.

## 6. Points ouverts

1. Mettre à jour le contrat (gust HD validée, `tsnowp` / `tgrp` AROME dans SP1).
2. **Jonction 0,025° (échéance 7)** non testée sur données réelles : télécharger `07H12H` (≈ 108 Mo, SP1 + SP2) pour le confirmer.
3. **`snow_fraction`** non validée sur un cas de neige (tirage d'octobre, valeurs ≈ 0).
4. **Identité des `unknown`**, en particulier (0, 6, 1) de SP1 0,025° très proche de notre nébulosité totale : à confirmer avec la documentation Météo-France.
5. Les cumuls décroissent parfois de ~1e-3 mm (précision de codage, non vérifiée dans les clés) ; plancher à 0 en place.
6. **HD ≈ AROME interpolé** à 0,17 m/s près : examiner où HD apporte de l'information (relief, côtes) avant de généraliser le surcoût (6,3 Go contre 0,85 Go par tirage).
7. Performance : parallélisation par échéance et compression `.npz` allégée, si besoin ; reprise après interruption d'un tirage entier non écrite.
8. Un seul tirage a été exploité (aucune comparaison d'une situation à l'autre).

## 7. Extension 0-51 h (étape 2 : données lointaines, raccords, neige)

Mesuré le 17/10/2026 (13 h UTC). Rejouer : `python3 -m pipeline.ingest --model arome --run 2026-10-17T06:00:00Z --steps 0-51 --src <grib> out/grib --download --connexions 2 --out out/canon`, puis `python3 -m pipeline.ingest.extension --src <grib> out/grib --modeles arome` (options `--run`, `--echeances-max`). Les résultats complets sont dans `out/canon/extension_arome_06Z_0-51.json`, `extension_arome-hd_03Z_0-51.json`, `extension_arome-hd_06Z_0-18.json`.

### 7.1 Ce qui a pu être produit

| Modèle, tirage | Échéances produites | Remarque |
|---|---|---|
| AROME 0,025°, 06Z | **0 à 51** (52 fichiers, 9 groupes `00H06H` à `49H51H`) | complet |
| AROME HD, 06Z | **0 à 18 seulement** | côté Météo-France, SP1 HD du tirage 06Z s'arrête à `18H` : les `19H`… `51H` répondent **404** (SP2 est complet, 52 fichiers). Constaté à 13:03 et 13:06 UTC, soit 7 h après le tirage ; le tirage 09Z avait alors 43 SP2 et **0** SP1. À revérifier plus tard. |
| AROME HD, **03Z** (substitut) | **0 à 51** (52 fichiers) | tirage complet (SP1 et SP2 : 52 + 52), utilisé pour tester les échéances lointaines HD |

**Écart à la mission** : le HD 0-51 n'est donc pas celui du tirage 06Z mais du tirage 03Z (même modèle, 3 h plus tôt). Les sorties 06Z 0-18 sont conservées. Les HD 06Z 19-51 restent à produire dès que SP1 sera publié (même commande avec `--steps 0-51`, les fichiers déjà présents ne sont pas retéléchargés).

### 7.2 Mesures (deux connexions au plus)

| Mesure | AROME 0,025° 06Z, 0-51 | AROME HD 03Z, 0-51 |
|---|---|---|
| Fichiers GRIB / volume | 18 (SP1+SP2 × 9 groupes) dont 16 téléchargés : 703 Mo (+ 107 Mo déjà présents) | 104 : **2 191 Mo**, tous téléchargés |
| Téléchargement | 16 fichiers présents en moins d'une minute (durée **non chronométrée**, la commande a été interrompue par l'erreur 404 du HD) | **45,8 s** de temps réel, soit **47,9 Mo/s** au total, ≈ 24 Mo/s par connexion (somme des temps par fichier 90,6 s) ; débit très supérieur aux 5–11 Mo/s mesurés plus tôt, réseau variable |
| Décodage | 18 fichiers, 844 Mo en **11,2 s** ; SP1 0,69–1,25 s (55 Mo, 66 messages), SP2 0,32–0,79 s (42 Mo, 30 messages), dernier groupe 0,35 / 0,17 s | 104 fichiers, 2 191 Mo en **38,9 s** ; SP1 0,38 s et SP2 0,37 s en moyenne par fichier |
| Total, 52 échéances | **73,2 s** (1,0 à 3,2 s par échéance, moyenne 1,4 s ; l'échéance 7 prend 2,1 s car elle recharge le groupe précédent) | 325,6 s dont 45,8 s de téléchargement ; 4,6 à 5,7 s par échéance, 279,8 s au total (compression `.npz` comprise) |
| Mémoire (pic RSS) | 1 094 Mo | 1 165 Mo |
| Sorties `.npz` | 462 Mo (≈ 8,9 Mo par échéance) | 2 789 Mo (≈ 53,6 Mo par échéance) |

### 7.3 Plages, NaN et anomalies sur les 52 échéances

Aucune échéance absente, aucun trou. Pourcentage de NaN : **17,179 %** (AROME) et **17,065 %** (HD) sur toutes les variables et toutes les échéances (l'échéance 0 diffère de 0,0003 point au plus : moins de 3 points de grille ; non analysé). Plages (AROME 06Z, 52 échéances, `gust10`, `precip_rate`, `snow_fraction`, `cloud_total` sur 51) :

| Variable | Min | Max | Échéance du max | Observation |
|---|---|---|---|---|
| `wind10_u` / `wind10_v` (m/s) | −24,2 / −25,4 | 20,1 / 15,3 | 37 / 39 | |
| `gust10` (m/s) | 0,0015 | 36,8 | 39 | |
| `t2m` (K) | 259,7 | 303,8 | 9 | −13,4 °C à +30,7 °C |
| `rh2m` (%) | 7,3 | 100 | 1 | |
| `precip_rate` (mm/h) | 0 | 180,2 | 36 | cumul max sur 51 h : **562 mm** |
| `snow_fraction` | 0 | 1 | 15 | |
| `cloud_total` (%) | 0,0087 | 100 | 1 | |
| `cape` (J/kg) | 0 | 3 689 | 42 | |
| `mslp` (Pa) | 100 789 | 103 966 | 4 | |

HD 03Z (0-51) : vent −23,8 à 19,4 m/s, rafale max 35,4 m/s (éch. 42), `t2m` 257,5 à 304,1 K, `rh2m` 3,0 à 100 %, `precip_rate` max **219,9 mm/h** (éch. 43), cumul max 413 mm, `cape` max 3 735 J/kg ; HD 06Z 0-18 : mêmes ordres de grandeur (débit max 110 mm/h).

Aucune variable ne sort de la plage physique testée (bornes larges dans `extension.py`), **sauf `snow_fraction`, corrigée pendant cette étape** : voir §7.5. Anomalies et points à examiner (aucun n'est établi comme une erreur) :

1. **Débits extrêmes** : 180 mm/h (AROME, éch. 36, 39,43°N 2,08°E) et 220 mm/h (HD 03Z, éch. 43, 38,95°N 2,09°E), au large des Baléares ; cumul de 562 mm en 51 h. Valeurs très élevées, localisées sur des cellules en mer : orage méditerranéen plausible mais à confronter à une autre source.
2. **Sauts locaux entre échéances successives** : vent jusqu'à 35,7 m/s (AROME, éch. 36 → 37, 39,2°N 2,3°E, mêmes Baléares, rafale 34 m/s) ; HD 34,7 m/s (éch. 42 → 43, 38,86°N 2,13°E) : même cellule orageuse. `t2m` : saut de **10,8 K** en 1 h (AROME, éch. 10 → 11, 39,65°N 7,8°O) et **12,2 K** (HD 03Z, éch. 12 → 13, 39,75°N 7,5°O), Portugal intérieur, bord ouest du domaine, en fin d'après-midi : présent dans les deux modèles, donc plutôt physique (refroidissement brutal), mais à vérifier. Le 99,9ᵉ centile du saut horaire de `t2m` est de 6,2 K, ce qui est élevé.
3. `cape` : saut local de 3 258 J/kg d'une échéance à l'autre (éch. 42 → 43), cohérent avec la convection ci-dessus.
4. Aucun saut de moyenne anormal aux raccords de groupes (voir §7.4) ; les moyennes de domaine évoluent de façon continue (plus grand saut de la moyenne de `t2m` : 0,90 K).

### 7.4 Raccords entre groupes d'échéances 0,025° (données réelles)

Le cumul de chaque groupe démarre bien à 0 (`startStep = 0`, contrôlé par `verifier_fenetre` pour les 18 fichiers), donc l'échéance 7 est un cumul 0 → 7 comme les autres : le débit exige seulement l'échéance 6 du groupe précédent, ce que fait le code (le groupe est rechargé : +1 s à chaque raccord).

| Raccord n | Débit moyen n−1 / n / n+1 (mm/h) | n ÷ moyenne(n−1, n+1) | Débit moyen si le cumul précédent était oublié |
|---|---|---|---|
| 7 | 0,0597 / 0,0691 / 0,0781 | 1,00 | 0,397 |
| 13 | 0,0980 / 0,0999 / 0,1001 | 1,01 | 0,954 |
| 19 | 0,1156 / 0,1255 / 0,1345 | 1,00 | 1,615 |
| 25 | 0,1555 / 0,1653 / 0,1707 | 1,01 | 2,505 |
| 31 | 0,1705 / 0,1743 / 0,1766 | 1,00 | 3,539 |
| 37 | 0,1686 / 0,1616 / 0,1566 | 0,99 | 4,582 |
| 43 | 0,1544 / 0,1598 / 0,1590 | 1,02 | 5,507 |
| 49 | 0,1419 / 0,1436 / 0,1335 | 1,04 | 6,402 |

Continuité : le débit à n s'insère entre ses voisins à 4 % près ; l'erreur d'un oubli du cumul précédent serait de 6 à 45 fois la valeur. Le plus gros décrément de cumul à un raccord est −0,0156 mm (éch. 37), même ordre que les autres intervalles (§4b).

- **Somme des débits contre cumul**, 0-51 h : écart maximal **0,0425 mm** et moyen 0,0044 mm pour un cumul maximal de 562 mm (AROME) ; HD 03Z : 0,082 mm et 0,0057 mm pour 413 mm. Cet écart vient uniquement du plancher à 0 des décréments de codage.
- **Dernier groupe `49H51H`** : 3 échéances (49, 50, 51), 27,8 Mo (SP1) et 21,5 Mo (SP2) ; la chaîne le traite sans cas particulier (débit moyen 0,1436 / 0,1335 / 0,1387 mm/h).
- **Recoupement `tp` (SP1) contre `tirf + tsnowp + tgrp`**, débit sur 51 échéances : corrélation ≥ 0,9999998 ; biais ≤ 3,7e-6 mm/h ; RMSE ≤ 6,9e-4 mm/h ; écart maximal **0,029 mm/h** ; les cumuls eux-mêmes diffèrent d'au plus 0,0156 mm. Le plancher à 0 appliqué séparément à chaque série explique les écarts. Les deux méthodes sont équivalentes ; `tirf + tsnowp + tgrp` reste la référence du contrat.

### 7.5 Neige : cas réel et validation de `snow_fraction`

**Le tirage actuel (06Z) contient de la neige significative** : aucune recherche dans les autres tirages n'a été nécessaire (aucun téléchargement hors du plan). Elle apparaît à partir de l'échéance 11 et culmine entre les échéances 17 et 36.

- **Où** : Alpes du Nord et franco-suisses (45,8°N 6,9°E, massif du Mont-Blanc, mailles les plus enneigées ; Alpes suisses centrales vers 46,5-46,8°N 8-9°E), Pyrénées centrales (42,6°N 0,4°E, Hautes-Pyrénées / Aragon). Cumul `tsnowp` maximal à l'échéance 51 : **59,9 mm d'eau** (AROME) à 45,85°N 6,9°E ; HD 03Z : 56,1 mm au même endroit. Sur les points où `snow_fraction > 0,5` (51 échéances, débit > 0,05 mm/h) : 7 345 points-échéances en zone Alpes, 1 001 Pyrénées, 57 ailleurs (AROME).
- **Valeurs** : `snow_fraction` va de 0 à 1 ; 0 point ≠ 0 aux échéances 1-10, puis 142 à 1 768 points ≠ 0 par échéance (AROME).

**Problème trouvé et corrigé** : avant correction, `snow_fraction` valait −0,018 à **1,066** (hors du contrat 0..1) : Δ`tsnowp` peut être légèrement négatif ou supérieur à Δtotal (pas de codage des cumuls, §4b). `fraction_neige` borne maintenant le résultat à [0,1] (test ajouté). Aucune autre sortie n'a changé ; la reproduction de la fixture reste 9/9.

**Cohérence avec la température** (AROME, 51 échéances, points où le débit > 0,05 mm/h ; `t2m` du modèle, donc à l'altitude du relief du modèle) :

| T2m (°C) | Points | `snow_fraction` moyenne | Part avec fraction > 0,5 |
|---|---|---|---|
| ≤ −2 | 2 801 | 0,845 | 94,6 % |
| −2 à 0 | 6 037 | 0,566 | 60,5 % |
| 0 à 2 | 23 680 | 0,129 | 8,2 % |
| 2 à 4 | 62 809 | 0,0066 | 0,25 % |
| 4 à 6 | 173 122 | 0,00017 | 0,0006 % |
| > 6 | 4 133 655 | 1e-6 | 0 |

HD 03Z donne la même allure (≤ −2 °C : 89,8 % ; −2 à 0 : 46,2 % ; 0 à 2 : 6,2 % ; 2 à 4 : 0,19 % ; > 6 : 0). Sur l'extrait des tests (Alpes, échéances 18-21), tous les points avec fraction > 0,5 et débit > 0,05 mm/h ont `t2m` < 1,5 °C. **Ce qui colle** : la neige apparaît quasi exclusivement sous ~2 °C, croît vers le froid et se concentre sur les Alpes et les Pyrénées. **Ce qui ne colle pas entièrement** : entre −2 et 0 °C, 40 % des points précipitants (et 5 % sous −2 °C) ont une fraction < 0,5, soit pluie ou mélange à T2m négative (pluie surfondue, ou couche chaude en altitude : `snow_fraction` est une part du cumul, pas un test de température) ; et entre 0 et 2 °C une faible part est déjà de la neige. Je n'ai pas de donnée d'altitude réelle ni d'observation pour trancher : **non établi**.

### 7.6 Tests ajoutés

`tests/ingest/test_ingest.py` : 35 tests (dont 10 nouveaux de cette étape) passent ; fixtures légères réelles `tests/ingest/data/raccord_025.npz` (19 Ko) et `neige_025.npz` (107 Ko), recadrées sur les Alpes du Nord (41 × 61 points), fabriquées par `tests/ingest/make_fixtures.py`.

- Raccord : débit à 7 identique bit à bit avec le cumul du groupe précédent, continuité aux échéances 5-8, contre-épreuve, somme des débits (écart < 0,01 mm), recoupement `tp`, groupes `49H51H` et raccords 7, 13… 49 (chaque raccord charge deux groupes).
- Neige : valeurs attendues reproduites, fraction non nulle partout et dans [0,1], neige seulement sous 2 °C, zéro sans précipitation, bornage.
- Correctif annexe de sécurité : `localiser` n'accepte plus un fichier sans suffixe de tirage (échantillons `hd3/`) que si l'en-tête GRIB confirme le tirage. Le premier essai HD 03Z avait pris par erreur des échantillons du tirage 06Z ; la vérification de taille l'a arrêté avant toute sortie fausse.

### 7.7 Points ouverts

1. HD 06Z échéances 19-51 : SP1 non publié sur le S3 au moment de la mesure (404). À rejouer.
2. Débits extrêmes et sauts de `t2m` du Portugal (§7.3) non confrontés à une autre source.
3. Écart `tirf + tsnowp + tgrp` contre `tp` : équivalence établie sur un seul tirage.
4. Pluie à T2m négative : à comparer avec des observations ou l'altitude du modèle.
5. Temps de téléchargement AROME 0,025° 0-51 non chronométré ; débits très variables d'une mesure à l'autre (10 à 48 Mo/s).
