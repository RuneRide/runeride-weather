<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# PoC J1 : validation croisée (signe, unité, convention)

Objectif : détecter des erreurs de **signe, d'unité ou de convention** dans les champs canoniques AROME / AROME HD (tirage `2026-10-17T06:00:00Z`, échéances 0 à 6) en les confrontant à des références **indépendantes de notre code** : champs du modèle lui-même (`10si`, `10wdir`, `tp`…), un autre modèle (ARPEGE 0,1°), la physique (équilibre géostrophique, formule de Magnus). Tout ce qui est chiffré a été **mesuré** le 17/10/2026 ; ce qui est une estimation est marqué ESTIMATION.

**Indépendance.** Les scripts n'importent rien de `pipeline/` : le décodage GRIB est réécrit (`tests/validation/commun.py`, eccodes brut, `9999` → NaN). Les champs canoniques `.npz` sont lus tels quels, comme le fera le tuilage. Les GRIB sont traités comme des données non fiables (décodés, jamais exécutés ; Python lancé avec `-I` hors des scripts).

## Verdict

| # | Question | Verdict |
|---|---|---|
| 1 | Convention de signe de `wind10_u/v` | **OK** : u est positif vers l'est, v positif vers le nord ; direction météo (d'où vient le vent) = (270 − atan2(v,u)) mod 360, confirmée à 0,2° près pour les vitesses ≥ 2 m/s |
| 1b | Flèches du viewer | **Correctes** : elles pointent vers où va l'air (voir §1.3) |
| 2 | AROME contre ARPEGE 0,1° | **Pas de signe inversé, pas de facteur d'unité.** Corrélations 0,96 (vent), 0,999 (pression), 0,98 (T 2 m) ; biais modestes (§2). La pluie diffère beaucoup plus (corrélation 0,5 à 0,6, AROME 13 à 18 % moins de cumul au total) : écart de modèle, pas d'unité |
| 3 | `tp` = `tirf` + `tsnowp` + `tgrp` | **Vérifié** à ≤ 1 pas de codage près (écart max 0,00097 mm) ; formule du contrat valide. Neige **non** mise à l'épreuve (journée sans neige) |
| 4 | Plausibilité 0 à 6 h | **Aucune valeur hors bornes physiques** ; 2 anomalies mineures (arrondi de `snow_fraction` en HD, masque de bord variable) et une observation non expliquée (rafale < vent, §4.2) |

## 1. Vent : u/v contre `10si` / `10wdir`

### 1.1 Méthode

GRIB `arome__0025__SP1__00H06H__2026-10-17T06:00:00Z.grib2` (contrat §6). Pour chaque échéance 0 à 6 et chacun des **665 681 points valides** (82,8 % de la grille 1121 × 717 ; 665 679 à l'échéance 0, voir §4.4), comparaison de `wind10_u`, `wind10_v` (out/canon/arome) à :
- vitesse : `hypot(u, v)` contre `10si` ;
- direction : `dir = (270 − atan2(v, u)·180/π) mod 360` contre `10wdir` (convention météo : d'où vient le vent). Écart angulaire ramené dans ±180°.

En plus, **trois conventions alternatives** sont calculées pour vérifier qu'elles sont bien rejetées : signes de u et v opposés (≡ direction « vers où »), u et v permutés. Contrôle annexe : canon contre GRIB `10u`/`10v` bruts.

### 1.2 Résultats (échéances 0 à 6, `out/validation/vent_direction.json`)

| Mesure | Résultat |
|---|---|
| Vitesse, écart `hypot(u,v)` − `10si` : maximum / moyen (sur les 7 échéances) | **0,0094 m/s** / **0,0026 m/s** ; biais |moyen| < 6e-5 m/s |
| Direction, vitesses ≥ 2 m/s (≈ 470 000 points par échéance) : écart maximal / moyen | **0,21°** / **0,039°** ; biais signé |·| < 2e-4° ; 99,9e centile 0,16° |
| Direction, tous points, écart moyen | 0,05 à 0,08° |
| Direction, tous points, écart **maximal** | 44,7° (éch. 0), 10 à 23° (autres) : uniquement aux vents quasi nuls |
| Convention « signes opposés » (≡ « vers où ») : écart moyen | **179,96°** (rejetée sans ambiguïté) |
| Convention « u et v permutés » : écart moyen | 71 à 83° (rejetée) |
| Canon contre GRIB `10u`/`10v` : écart maximal | **0** exactement (aucune transformation) |

Écart de direction par classe de vitesse (éch. 3) : 0-0,1 m/s : max 20,95° (1 166 pts) ; 0,1-0,5 : 2,6° ; 0,5-1 : 0,62° ; 1-2 : 0,34° ; 2-5 : 0,20° ; ≥ 5 : 0,12°. L'écart décroît comme 1/vitesse : c'est l'effet de la **quantification de `10si`** (pas de codage ≈ 0,01 m/s, ordre de grandeur d'après l'écart de vitesse maximal 0,009 m/s ; une erreur de 0,009 m/s sur un vent de 2 m/s donne 0,26°). Ce n'est **pas** un défaut de convention (ESTIMATION de la cause, la loi en 1/vitesse est mesurée).

Contrôle indépendant supplémentaire, **équilibre géostrophique** (`geostrophie()`, éch. 0, 3 et 6) : vents et mslp lissés par blocs de 0,5° (1 386 cellules), le vent doit suivre `u ≈ −(1/ρf)·∂p/∂y`, `v ≈ +(1/ρf)·∂p/∂x`. Corrélations mesurées : u 0,46 / 0,47 / 0,36, v 0,39 / 0,45 / 0,51, **toutes positives** (un signe inversé donnerait des valeurs négatives). Les pentes valent seulement 0,17 à 0,26 (vent à 10 m freiné par le sol et relief, gradient de mslp bruité sur les reliefs) : ce test n'est que **qualitatif**, il confirme le signe, pas l'amplitude.

**Conclusion : convention OK** (ni inversée, ni décalée). Les composantes canoniques sont bien l'est (u) et le nord (v) en m/s, vers où va l'air.

### 1.3 Conséquences pour le viewer (lecture du code, rien modifié)

- `viewer/js/fleches.js` : `ang = Math.atan2(-v, u)` avec l'axe y de l'écran vers le bas, direction du trait `(dx, dy) = (cos ang, sin ang) = (u, −v)/|w|`. u > 0 donne dx > 0 (vers la droite = est), v > 0 donne dy < 0 (vers le haut = nord). La pointe est dessinée en `(x1, y1) = (x, y) + (dx, dy)·L/2`, la queue en `(x0, y0)`. Les flèches pointent donc **vers où va le vent** (le sens du déplacement de l'air), ce qui est la convention habituelle des flèches de vecteur. **Correct.** La carte est en Web Mercator nord en haut, donc pas de rotation à appliquer.
- `viewer/js/main.js` (panneau d'info, ligne 145) : `dir = (270 − atan2(v,u)·180/π + 360) % 360` et libellé « de <point cardinal> » : formule **identique** à celle validée ici, affichage cohérent (« de N 0° » pour un vent venant du nord).
- Limite : seule la **lecture du code** a été faite ; aucun rendu n'a été confronté au GRIB. Le vent dans les tuiles est quantifié (pas 0,031 m/s) et interpolé en u/v, ce qui n'affecte pas le signe.

## 2. AROME 0,025° contre ARPEGE 0,1°

### 2.1 Méthode

- **Fichier ARPEGE** : `arpege__01__SP1__000H012H__2026-10-17T06:00:00Z.grib2`, **76 105 751 octets** (72,6 Mio ; la taille « ~72 Mo » de la consigne était donc en Mio), taille égale au `Content-Length` du serveur. Il n'était pas présent en entier : un fragment de 20 000 001 octets (`sp1_part.grib2`) a été **repris** (`curl -C -`, **1 connexion**) dans `…/scratchpad/arp/`. Le fichier complet y est maintenant ; ne pas le retélécharger.
- **Grille** ARPEGE 741 × 521, 72°N→20°N, 328°E→42°E (pas 0,1°). Mesure : les nœuds ARPEGE tombent sur des nœuds AROME (écart d'alignement max 4,5e-14°). Zone commune : 178 × 279 cellules (lat 37,6 à 55,3°N, lon −11,9 à 15,9°E), dont **41 145 valides** (le reste est masqué dans AROME, trapèze).
- **Rééchantillonnage : moyenne par blocs** (pas bilinéaire) : chaque cellule ARPEGE (±0,05° autour du nœud) est la moyenne de 5 × 5 nœuds AROME de poids trapèze [0,5 1 1 1 0,5]² normalisés ; une cellule avec un nœud AROME NaN est exclue. L'échantillonnage **ponctuel** (nœud AROME coïncident) est aussi calculé (`ponctuel` dans le JSON) pour séparer l'écart d'échelle de l'écart de modèle.
- Échéances 3 et 6 (ARPEGE : cumul `tp` 0→n, mêmes fenêtres qu'AROME).

### 2.2 Résultats (`out/validation/arome_vs_arpege.json`), biais = AROME − ARPEGE, n = 41 145

| Variable | Éch. | Biais | RMSE | Corrélation | Moyennes AROME / ARPEGE |
|---|---|---|---|---|---|
| u 10 m (m/s) | 3 | −0,217 | 0,917 | 0,960 | −1,227 / −1,011 |
| u 10 m | 6 | −0,146 | 0,923 | 0,961 | −1,748 / −1,601 |
| v 10 m (m/s) | 3 | −0,051 | 0,981 | 0,964 | −3,701 / −3,650 |
| v 10 m | 6 | −0,036 | 0,972 | 0,965 | −3,723 / −3,688 |
| vitesse (hypot des moyennes) | 3 / 6 | +0,085 / +0,069 | 0,925 / 0,900 | 0,971 / 0,967 | |
| mslp (Pa) | 3 | −23,6 | 36,6 | 0,9992 | 102 509,7 / 102 533,3 |
| mslp (Pa) | 6 | −35,8 | 48,1 | 0,9991 | 102 450,6 / 102 486,3 |
| T 2 m (K) | 3 | +0,311 | 0,739 | 0,989 | 287,34 / 287,02 |
| T 2 m (K) | 6 | +0,245 | 0,877 | 0,982 | 288,80 / 288,55 |
| HR 2 m (%) | 3 / 6 | −2,56 / −2,40 | 6,71 / 7,47 | 0,874 / 0,857 | 75,0 / 77,5 ; 67,7 / 70,1 |
| tp cumul 0→n (mm) | 3 | −0,035 | 0,596 | 0,513 | 0,159 / 0,194 |
| tp cumul | 6 | −0,049 | 0,937 | 0,591 | 0,329 / 0,378 |

Compléments :
- **Direction** AROME − ARPEGE (cellules à vent ≥ 3 m/s, 24 403 en éch. 3 et 26 824 en éch. 6) : écart absolu médian 4,4° / 4,0°, moyen 6,3° / 6,1°, 90e centile 13,6° / 13,5°, biais signé +1,7° / +1,4°. Pas de décalage ni d'inversion. Corrélation de u avec −u_ARPEGE : −0,96 (une inversion de signe est donc exclue).
- **mslp** : biais de −0,24 hPa (éch. 3) et −0,36 hPa (éch. 6), corrélation 0,999 : **aucun facteur 100 (hPa/Pa)**. Unités concordantes (Pa des deux côtés).
- **Précipitations** : sur les cellules de la zone, somme AROME / somme ARPEGE = **0,82** (éch. 3 : 6 546 contre 7 978 mm cumulés sur les cellules) et **0,87** (éch. 6 : 13 544 contre 15 556 mm). Maxima comparables (éch. 3 : 12,5 contre 13,9 mm en blocs ; éch. 6 : 27,1 contre 26,7 mm). Sur les cellules pluvieuses (≥ 0,1 mm dans l'un des deux) la corrélation tombe à 0,36 (éch. 3) et 0,48 (éch. 6) ; éch. 3 : **31 %** des cellules pluvieuses d'AROME sont sèches (≤ 0,1 mm) dans ARPEGE et **46 %** des cellules pluvieuses d'ARPEGE sont sèches dans AROME (éch. 6 : 28 % et 37 %). Le facteur ~0,85 n'est **pas** un facteur d'unité (aucun facteur d'unité plausible : 0,82 ≠ 1/3,6, 0,001, 25,4…) ; la cause (modèle plus ou moins pluvieux, convection explicite contre paramétrée, déplacement des cellules) est une **hypothèse non testée**.
- **Température 2 m** : **ARPEGE SP1 la contient** (`2t`, avec `2r`) : c'est ce qui permet le tableau ci-dessus. AROME est plus chaud de 0,25 à 0,31 K en moyenne. Cause probable : altitude du relief plus fine dans AROME (ESTIMATION, non vérifiée : l'orographie n'a pas été lue). Pas un problème d'unité (corrélation 0,98 à 0,99).
- **Humidité relative** : biais −2,4 à −2,6 % et corrélation plus faible (0,86 à 0,87) que la température : normal pour une variable plus locale (ESTIMATION) ; unités concordantes.
- **Échelle** : l'échantillonnage ponctuel donne des RMSE plus grandes (vent 1,02 à 1,10 m/s, T 0,86 à 1,00 K, pluie 0,70 à 1,09 mm) et des corrélations plus basses (vent 0,95 à 0,96, pluie 0,44 à 0,52) que les blocs : une partie de l'écart est l'échelle spatiale (représentativité), pas le modèle.
- **Convention de vent d'ARPEGE** (indépendante d'AROME, éch. 3, 320 882 points ≥ 2 m/s) : `10u`/`10v` contre `10wdir`, écart maximal 0,34°, vitesse 0,0145 m/s : ARPEGE suit la même convention que celle validée en §1.

**Écarts suspects** : aucun biais systématique de signe ou d'unité. Seuls points à surveiller : biais de pression qui croît avec l'échéance (−24 puis −36 Pa, **à suivre**, sans conclusion à 2 échéances), pluie AROME ~15 % plus faible qu'ARPEGE sur cette zone et ce tirage (un seul tirage ; **ne pas généraliser**).

## 3. Précipitations : `tp` contre `tirf` + `tsnowp` + `tgrp`

AROME 0,025°, `tp`, `tsnowp`, `tgrp` dans SP1, `tirf` dans SP2, cumuls 0 → n, 665 681 points, échéances 1 à 6 (`out/validation/precip_tp_vs_somme.json`).

| Éch. | Écart max abs `tp − Σ` (mm) | RMSE (mm) | Biais (mm) | Points > 1e-3 mm | Part des points avec |écart| ≤ 1e-6 | `tp` max (mm) | `tgrp` max (mm) |
|---|---|---|---|---|---|---|---|
| 1 | 0,00041 | 1e-6 | ≈ 0 | 0 | 99,97 % | 18,68 | 0,00014 |
| 2 | 0,00048 | 2e-6 | 0 | 0 | 99,93 % | 23,10 | 0,00042 |
| 3 | 0,00097 | 5e-6 | 0 | 0 | 99,89 % | 46,53 | 0,00064 |
| 4 | 0,00097 | 5e-6 | ≈ 0 | 0 | 99,86 % | 46,54 | 0,00248 |
| 5 | 0,00097 | 6e-6 | ≈ 0 | 0 | 99,83 % | 52,21 | 0,0036 |
| 6 | 0,00095 | 7e-6 | ≈ 0 | 0 | 99,80 % | 62,01 | 0,0545 |

Corrélation 1,000 à toutes les échéances ; sommes sur la grille identiques à mieux que 1e-5 % près. Le plus petit pas de `tp` observé vaut 0,000488 mm (éch. 1-2) puis 0,000977 mm (2⁻¹⁰, éch. 3-6) : **l'écart est de l'ordre d'un pas de codage GRIB**, donc `tp = tirf + tsnowp + tgrp` aux erreurs d'arrondi près ; `tp` ne contient **aucune autre espèce** mesurable. Variantes : à l'éch. 6, aux 10 points où `tgrp` > 0,005 mm, l'écart max `|tp − tirf|` vaut 0,055 mm (soit tout le graupel) contre 0,0007 mm pour `|tp − Σ|` : **le graupel est bien une espèce à part, à ajouter à `tirf`** (le contrat le fait).

**Limite importante** : `tsnowp` n'excède **1e-6 mm** ce jour-là (aucune neige) ; l'additivité de la neige n'est donc **pas démontrée par ces données** (seulement qu'elle est négligeable ici), et le graupel n'est testé que sur 10 points à l'éch. 6. Le test de la neige doit être rejoué sur un tirage hivernal. Autre limite : les cumuls ne sont pas strictement monotones (décréments jusqu'à −0,00098 mm aux échéances 2, 3, 5, 6 ; 3 225 à 18 748 points décroissants selon l'intervalle, aucun entre 3 et 4), cohérent avec la brique ingestion ; le plancher à 0 du débit est justifié.

**Validation du contrat §2** : la formule `precip_rate = max(0, Δ(tirf + tsnowp + tgrp))` est équivalente à `Δ(tp)` à moins d'un pas de codage près (≈ 0,001 mm par heure sur un débit moyen de 0,05 à 0,1 mm/h : négligeable sauf pluie très faible).

## 4. Plausibilité physique (échéances 0 à 6, AROME et AROME HD)

Seuils et extrêmes dans `out/validation/plausibilite.json`. Les bornes de §4.1 sont des bornes **physiques** (une violation serait une erreur certaine) ; celles de §4.3 sont des **seuils de jugement** (ESTIMATION, voir justification).

### 4.1 Bornes

| Variable | Bornes testées | Extrêmes mesurés AROME (éch. 0-6) | Extrêmes mesurés HD | Hors bornes |
|---|---|---|---|---|
| T 2 m (K) | 200 à 330 (suspect : < 255 ou > 305) | 259,7 à 301,3 | 257,3 à 302,2 | **0** |
| HR 2 m (%) | 0 à 100 | 7,3 à 100,0 | 5,0 à 100,0 | **0** |
| nébulosité (%), éch. 1-6 | 0 à 100 | 0,009 à 100,0 | idem | **0** |
| CAPE (J/kg) | 0 à 8 000 | 0 à 3 100 | 0 à 3 194 | **0** (jamais négative) |
| mslp (Pa) | 87 000 à 109 000 (suspect : < 96 000 ou > 105 500) | 101 328 à 103 966 | absente | **0** |
| débit (mm/h) | 0 à 100 | 0 à 41,5 | 0 à 44,6 | **0** |
| rafale (m/s) | 0 à 80 | 0,005 à 24,1 | 0,003 à 24,1 | **0** |
| u, v (m/s) | ± 80 | u −14,6 à 9,8 ; v −16,6 à 9,5 | u −14,9 à 10,1 ; v −17,0 à 9,7 | **0** |
| `snow_fraction` | 0 à 1 | 0 à 9e-7 | −3,8e-10 à 2,7e-4 | AROME 0 ; **HD : 6 points < 0 à l'éch. 6** (voir §4.4) |

Le jour est cohérent avec un anticyclone (mslp 1013 à 1040 hPa) : vent moyen de nord-est (u moyen −1,2 à −1,7 m/s, v moyen −3,7 m/s sur la zone commune).

**Contrôle de l'humidité** (AROME, formule de Magnus 17,625/243,04 appliquée à `2t` et `2d` du GRIB, comparée à `2r`) : écart absolu moyen **0,03 à 0,06 %**, biais 0,015 à 0,042 %, corrélation 0,9998 à 0,99994, 99e centile de l'écart 0,07 à 1,7 %, aucun point avec point de rosée > température. L'écart **maximal** vaut 4,4 à 4,5 % (quelques points, cause non étudiée, probablement un traitement glace/eau près de 0 °C : ESTIMATION, **non vérifié**). HR confirmée en % (pas en fraction).

### 4.2 Rafale contre vent moyen

`gust10` (rafale du dernier pas d'une heure) contre `hypot(u, v)` (vent instantané) :

| Éch. | % de points gust < vent AROME | % HD | Déficit max AROME / HD (m/s) | % avec déficit > 1 m/s (AROME / HD) |
|---|---|---|---|---|
| 1 | 0,863 | 0,876 | 2,84 / 4,37 | 0,0114 / 0,0137 |
| 2 | 0,593 | 0,601 | 2,19 / 2,81 | 0,0084 / 0,0084 |
| 3 | 0,241 | 0,240 | 2,33 / 3,20 | 0,0095 / 0,0085 |
| 4 | 0,099 | 0,103 | 3,56 / 3,81 | 0,0059 / 0,0066 |
| 5 | 0,068 | 0,068 | 3,20 / 4,05 | 0,0056 / 0,0067 |
| 6 | 0,055 | 0,059 | 3,43 / 4,01 | 0,0051 / 0,0061 |

Le pourcentage de points où la rafale est inférieure au vent moyen est de **0,86 % (éch. 1) à 0,05 % (éch. 6)**, presque identique pour les deux grilles (écart ≤ 0,02 point à chaque échéance). Il **décroît avec l'échéance**. Ces points sont à **vent faible** (vent moyen 0,85 m/s à l'éch. 1 pour les points concernés, 90e centile 1,4 m/s ; 1,6 m/s à l'éch. 6, 90e centile 3,1) et le déficit moyen sur ces points est de 0,20 à 0,42 m/s ; le déficit dépasse 1 m/s sur seulement 0,005 à 0,014 % des points. Interprétation (ESTIMATION, non vérifiée) : le vent est instantané à l'échéance n tandis que la rafale est un maximum sur l'heure précédente avec un traitement propre au modèle (rafale diagnostique minimale) ; la décroissance avec l'échéance évoque une phase d'ajustement (spin-up) du premier pas. Le rapport moyen rafale / vent (vent ≥ 3 m/s) est de **1,57 à 1,67**, ce qui est cohérent avec un facteur de rafale usuel (1,4 à 2 : ordre de grandeur, ESTIMATION). Pour l'affichage : **rafale = max(rafale, vent moyen)** supprimerait l'anomalie sans effet visible ; à décider.

### 4.3 Cohérence temporelle (variation d'une échéance à la suivante)

Seuils (ESTIMATION, jugement) : T 2 m 8 K/h (un front ou un foehn fort donne 5 à 10 K en 1 h ; au-delà, suspect), HR 60 %/h, mslp 600 Pa/h (6 hPa/h dépasse les plus fortes variations synoptiques d'une dépression explosive), vecteur vent 20 m/s/h, rafale 25 m/s/h. Résultats (maxima sur la grille, par intervalle ; AROME) :

| Intervalle | ΔT 2 m max (K) | ΔHR max (%) | Δmslp max (Pa) | Δvecteur vent max (m/s) |
|---|---|---|---|---|
| 0 → 1 | 5,7 | 38,4 | 202 | 12,9 |
| 1 → 2 | 7,8 | 32,3 | 196 | 11,6 |
| 2 → 3 | 7,2 | 33,5 | 331 | 12,3 |
| 3 → 4 | 7,5 | 36,5 | 272 | 12,7 |
| 4 → 5 | 6,6 | 35,3 | 194 | 13,4 |
| 5 → 6 | 6,1 | 48,9 | 302 | 11,9 |

**Aucun dépassement en AROME.** En HD, **un seul point** dépasse un seuil : ΔT = 8,53 K entre les échéances 2 et 3 en (41,12°N, −8,38°E), près de Porto (côte, ESTIMATION : brise ou effet de masque côtier, non vérifié). Les 99,9e centiles sont stables d'un intervalle à l'autre (T 3,1 à 4,9 K ; vent vecteur 6,1 à 7,0 m/s) et sont du même ordre au premier intervalle (0 → 1) qu'aux suivants : **pas d'artefact de raccord entre l'échéance 0 et 1**. Le vent change tout de même beaucoup d'une heure à l'autre (99,9e centile ≈ 6 à 7 m/s, moyenne 4,6 à 5,5 m/s) ; sans référence indépendante à l'heure, impossible de dire si c'est de la turbulence résolue (CAPE jusqu'à 3 100 J/kg ce jour-là) : **point ouvert**.

### 4.4 Anomalies

1. **`snow_fraction` HD légèrement négative** : 6 points à l'échéance 6 valent entre −3,8e-10 et −1,0e-10 (où le débit vaut 0,3 à 1,1 mm/h). Cause probable : Δ`tsnowp` négatif par arrondi du cumul. Sans effet à l'affichage (quantification bornée à 1..255), mais le contrat dit 0..1 : **borner à 0** dans `canon.py` (non modifié ici, hors de mon périmètre).
2. **Masque de bord variable** : 2 points (AROME) et 6 (HD, dont 1 dans l'autre sens) sur 665 681 / 4,16 M changent d'état valide/NaN entre l'échéance 0 et 1, par exemple (37,55°N, −7,45°E) et (37,55°N, 11,45°E), au bord du trapèze. Toutes les variables ensemble. Sans conséquence visible (bord de domaine) mais le masque n'est pas constant d'une échéance à l'autre : le tuilage ne doit pas supposer un masque fixe.
3. **Rafale < vent** en 0,05 à 0,88 % des points : voir §4.2 (cause non établie).
4. Aucune valeur hors bornes physiques, aucun NaN inattendu (nébulosité, rafale, débit absents à l'échéance 0 comme prévu).

## 5. Limites

- **Un seul tirage, une seule journée** (anticyclone, peu de neige, peu de graupel) ; échéances 0 à 6 seulement. Les échéances lointaines (jusqu'à +51 h), la neige et les situations extrêmes ne sont pas couvertes.
- **ARPEGE** : un seul fichier, deux échéances (3 et 6), un seul tirage ; biais mesurés sur une zone où 17 % des cellules sont masquées dans AROME. Les écarts (T, HR, pluie) sont des écarts **entre modèles**, pas des erreurs ; ni l'un ni l'autre n'est comparé à des **observations**, donc rien ne dit lequel est le plus juste.
- **Les causes physiques** invoquées (altitude du relief, convection, spin-up, Porto) sont des **hypothèses** non testées, sauf indication contraire.
- Les seuils de §4.3 sont des jugements, pas des normes ; les bornes de §4.1 sont physiques.
- L'indépendance est partielle : `10si`/`10wdir` et `10u`/`10v` viennent du même modèle (même fichier), leur cohérence confirme la **convention de signe et d'unité**, pas la justesse météorologique du vent.
- La lecture du viewer est statique (aucun rendu comparé).

## 6. Rejouer

```sh
export PYTHONPATH=/tmp/claude-1000/-home-philippe-github-com-runeride/93bcb318-8a08-4043-adfc-8845d03af2b4/scratchpad/pylib   # eccodes (contrat §6)
python3 tests/validation/controle_vent.py          # §1  -> out/validation/vent_direction.json
python3 tests/validation/controle_arpege.py        # §2  -> out/validation/arome_vs_arpege.json
python3 tests/validation/controle_precip.py        # §3  -> out/validation/precip_tp_vs_somme.json
python3 tests/validation/controle_plausibilite.py  # §4  -> out/validation/plausibilite.json
python3 -m unittest discover -s tests/validation -v   # 16 tests, ~16 s
```

Les GRIB sont cherchés dans `RUNERIDE_GRIB_SRC`, `out/grib/` et le dossier `scratchpad/grib` ; le fichier ARPEGE dans `RUNERIDE_ARPEGE_SRC` ou `scratchpad/arp/`. Si eccodes, un GRIB ou un `.npz` manque, le test correspondant est **ignoré** (sans eccodes : 14 tests sur 16 ignorés, mesuré avant l'ajout du test géostrophique : 13 sur 15) ; **aucun test ne télécharge** (le fichier ARPEGE se récupère à la main : `https://meteofrance-pnt.s3.rbx.io.cloud.ovh.net/pnt/2026-10-17T06:00:00Z/arpege/01/SP1/arpege__01__SP1__000H012H__2026-10-17T06:00:00Z.grib2`, une connexion, `curl -C -` pour reprendre).

| Fichier | Rôle |
|---|---|
| `tests/validation/commun.py` | décodage GRIB indépendant, recherche de fichiers, statistiques d'écart |
| `tests/validation/controle_vent.py` | §1 (+ équilibre géostrophique) |
| `tests/validation/controle_arpege.py` | §2 |
| `tests/validation/controle_precip.py` | §3 |
| `tests/validation/controle_plausibilite.py` | §4 (+ HR par Magnus) |
| `tests/validation/test_validation.py` | tests unitaires |
