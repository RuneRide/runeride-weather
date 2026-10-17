# PoC J1 : synthèse (17 octobre 2026)

Résultat consolidé des trois briques (ingestion, tuilage, visualisation), réalisées en parallèle par des sous-agents sur un contrat commun ([../poc-j1-contrat.md](../poc-j1-contrat.md)). Les rapports détaillés sont dans ce dossier. **Statut des chiffres** : **recontrôlé** = rejoué ou recalculé par le coordinateur, indépendamment de l'agent ; **rapporté** = annoncé par l'agent et non rejoué.

## Verdict

Le critère d'acceptation de J1 est **tenu** : les valeurs relues dans les tuiles sont égales aux valeurs sources en 20 points de contrôle, à moins d'un demi-pas de quantification ; poids et temps sont mesurés ; les hypothèses `gust10` et `cloud_total` ont été confrontées aux données. Un prototype de visualisation (J2, première partie) fonctionne sur les vraies tuiles.

## Ce qui a été recontrôlé

| Contrôle | Résultat |
|---|---|
| Reproduction des fixtures de référence par l'ingestion (grille complète 2801 × 1791) | **9 variables sur 9 identiques bit à bit** (après bornage de `rh2m` et `cloud_total` à 0..100) |
| Tests unitaires ingestion / tuilage | 22 (1 ignoré) et 18, tous réussis |
| Bornes de production | `rh2m` 12,2 à 100,0 % ; `cloud_total` 0,009 à 100,0 % |
| **Relecture indépendante de 20 points** (mon propre décodeur écrit d'après le contrat, zoom 8, centres de pixel de tuile, HD) | `t2m` écart max 0,189 K (demi-pas 0,197) ; `rh2m` 0,17 % (0,197) ; vent u/v 0,0156 m/s (0,0156) ; pluie 0,015 mm/h : **tout dans le demi-pas** |
| Poids d'une échéance HD, 7 variables | 40 Mo (agent : 41,3 Mo) |
| Forme du domaine valide | **trapèze**, pas un rectangle : 27,7° de large à 54°N, 25,5° à 50°N, 23,6° à 46°N, 22,0° à 42°N, 20,7° à 38,5°N ; 17,1 % de points masqués |
| Captures de la visualisation | vent (couleur + flèches) et température au zoom avec valeur au point : conformes au rapport |

## Chiffres rapportés par les agents, non rejoués

- **Rafale HD** : `hypot(max_10efg, max_10nfg)` contre `max_i10fg` d'AROME 0,025° : corrélation 0,9996, écart quadratique 0,11 m/s, biais ≈ 0 ; en AROME, la norme des composantes égale `max_i10fg` à 0,015 m/s près → **hypothèse confirmée**.
- **Fenêtres GRIB** : cumuls 0 → n, maxima n−1 → n (j'avais aussi vérifié ces deux fenêtres moi-même avant la dispersion des agents).
- **Cumuls** : décréments jusqu'à −1,4e-3 mm (plancher à 0), somme des débits contre cumul ≤ 2,3e-3 mm.
- **HD ≈ AROME interpolé** : corrélation ≥ 0,999 ; RMSE vent 0,06 m/s, température 0,17 à 0,20 K.
- **Performance ingestion** : HD ≈ 5,7 s par échéance (dont 4,75 s de compression `npz`), pic de mémoire 0,9 à 1,1 Go ; AROME ≈ 1 s ; téléchargement ≈ 11 Mo/s avec 3 connexions.
- **Tuilage** : 486 tuiles HD (z5–z8), 142 AROME (z3–z7) ; ≈ 41 Mo et 5,5 s par échéance HD, ≈ 14 Mo et 2 s en AROME (20 cœurs) ; WebP sans perte 13 à 36 % plus léger que PNG ; **bordure d'1 px : saut nul entre tuiles voisines**, contre jusqu'à 1,5 K (`t2m`) et 2 m/s (rafales) sans bordure.
- **Visualisation** (Chrome sans interface, WebGL **logiciel**, donc **non représentatif d'un téléphone** pour les images par seconde) : première couche visible en 0,93 à 1,12 s ; données à l'ouverture 0,91 Mo sur mobile émulé, **3,41 Mo sur grand écran**.

## Conséquences pour les spécifications

1. **Le poids est supérieur à l'estimation initiale** : un tirage complet de 52 échéances pèse environ **2,1 Go en HD et 0,7 Go en AROME**, soit **~2,8 Go**, et **~8,4 Go pour les 3 tirages conservés**, contre 4 à 5 Go estimés. Le vent en représente 46 %. **ESTIMATION** par extrapolation de 7 échéances sur 52 (les échéances lointaines, avec plus de nuages et de pluie, pèseront peut-être davantage).
2. **Le critère « 20 points » est reformulé** : vérifié **au centre des pixels de tuile** (là où la quantification est la seule source d'erreur) ; à une position quelconque l'erreur peut dépasser 1 pas pour `rh2m`, `cloud_total` et le vent (jusqu'à 4,1 pas pour `cloud_total`, 3,7 pour le vent), à cause de la **double interpolation bilinéaire** (tuilage puis affichage), et non de la quantification. En valeur absolue, 3,7 pas de vent = 0,12 m/s : sans conséquence pratique.
3. **Tuile absente = pas de donnée** (les tuiles entièrement masquées ne sont pas écrites : 86 de moins en HD) : le client doit le gérer, ce que fait le prototype.
4. **Objectif de 1,5 Mo à l'ouverture** : tenu sur téléphone (0,91 Mo), dépassé sur grand écran (3,41 Mo) : décaler le zoom de données d'un demi-niveau et ne charger que l'échéance courante d'abord.
5. **Le vent pèse trop** : à arbitrer (vent en 8 bits au zoom 8, ou pas de zoom 8 pour le vent, ou vent HD seulement à partir d'un zoom donné).
6. **Hypothèses levées** : rafale HD = norme des composantes (confirmée) ; nébulosité totale à recouvrement aléatoire (cohérente avec le paramètre `unknown` (0,6,1) d'AROME, corrélation 0,99995, **à confirmer** : ce n'est pas encore établi que c'est la nébulosité totale du modèle).

## Reste à faire

- **Mesures sur un vrai téléphone, en 4G** : le seul résultat solide de la visualisation sur ce point est le nombre d'octets et de requêtes.
- **Particules de vent** (le prototype n'a que des flèches), **sélecteur de modèle** (actuellement `?modele=`), **manifest** à la place de l'inventaire dynamique, **fond de carte vectoriel** (relief, villes, étiquettes).
- **Raccord entre groupes d'échéances en 0,025°** (échéance 7 → 6) : testé seulement par test unitaire, pas sur données réelles.
- **`snow_fraction`** : non validée sur un cas de neige (la fixture n'en contient pas).
- **Échéances lointaines** (jusqu'à +51 h) : tout a été mesuré sur les échéances 0 à 6.
- **Inventaire complet des paramètres `unknown`** et des paquets d'altitude.

---

# Étape 2 : risques sur les données (17 octobre 2026, après-midi)

Trois sous-agents en parallèle : extension à 0-51 h et neige (ingestion), réduction du poids (tuilage), validation croisée. Même convention : **recontrôlé** par le coordinateur, **rapporté** par l'agent.

## Verdict

Les risques « échéances lointaines », « raccord de groupes » et « neige » sont **levés pour AROME 0,025°** ; la **convention du vent est validée** ; le poids d'un tirage est **mesuré** et réductible de 68 %. **Deux problèmes restent réels** : un tirage HD peut être publié **incomplet** par Météo-France, et le profil de poids `reduit` impose une **règle de décodage unique** que la visualisation n'applique pas encore.

## Recontrôlé

| Contrôle | Résultat |
|---|---|
| **Convention du vent**, refaite sur les composantes **brutes** du GRIB AROME 0,025° (497 943 points à plus de 2 m/s, échéance 3) | direction météo `(270 − atan2(v,u)) mod 360` contre `10wdir` du modèle : écart **max 0,203°, moyen 0,0385°** ; formule inversée : 180° ; u et v permutés : 76° ; `hypot(u,v)` contre `10si` : 0,0092 m/s. **Validée.** |
| Flèches du viewer | `fleches.js` dessine vers où va le vent (repère d'écran à axe vertical inversé pris en compte) : **correct** |
| Neige AROME 0,025°, tirage 06Z, échéances 1-51 (3 671 202 points précipitants) | part de fraction de neige ≥ 0,5 : **95,5 %** sous −2 °C, 62,3 % entre −2 et 0 °C, 8,9 % entre 0 et 2 °C, 0,3 % entre 2 et 4 °C ; `snow_fraction` bornée à 0..1 |
| Tests | ingestion 32 (1 ignoré) ; tuilage 26 (agent) ; validation 16 (agent, 14 ignorés sans eccodes) |
| **Tirage HD complet 0-51 h** (HD 03Z, tuilé par le coordinateur) | **2,15 Go en profil `contrat`** (5 min 21 s, 20 cœurs) ; **0,51 Go en profil `reduit`** (1 min 55 s) ; 412 fichiers (416 moins 4 variables absentes à l'échéance 0) |
| AROME 0,025°, 52 échéances | **0,72 Go** `contrat`, **0,41 Go** `reduit` |
| **Poids d'un tirage complet (HD + AROME)** | **2,87 Go** `contrat`, **0,92 Go** `reduit` (−68 %) ; pour 3 tirages conservés : ≈ **8,6 Go** contre ≈ **2,8 Go** |
| **Précision du profil `reduit`** (200 points, échéance 30, z7, mon décodeur) | température et rafales : écart max **0,250** = demi-pas de 0,5, **à condition de décoder avec `quant_step`** (voir ci-dessous) |
| **Disponibilité côté Météo-France** (listage S3, 13:17 UTC) | SP1 HD **incomplet** au tirage 06 h (19 échéances sur 52) et **absent** au tirage 09 h ; SP2, SP3, HP1 complets ; tirages 00 h et 03 h complets |

## Problème de contrat découvert : le décodage ne se déduit plus de `min` et `max`

Avec le profil `reduit`, mon décodeur (formule du contrat, `min + (code−1)/254 × (max−min)`) donne **jusqu'à 13,6 K d'erreur** sur la température. Ce n'est pas une erreur des données : le profil code `v = min + (code − 1) × quant_step` (0,5 K), et les métadonnées gardent les mêmes `min`/`max`. **Règle retenue pour le contrat** : décoder avec `quant_step` dans tous les profils (identique à l'ancienne formule pour `contrat`). **La visualisation décode encore avec `min`/`max` : elle affichera des valeurs fausses sur les tuiles `reduit` tant qu'elle n'est pas adaptée**, et ne sait pas lire `wind8`.

## Rapporté par les agents, non rejoué

- **AROME 0,025° 0-51 h** : 52 échéances en 73 s, pic mémoire 1,1 Go, 703 Mo téléchargés. Raccords 7, 13 … 49 continus à 4 % près (6 à 45 fois d'erreur si le cumul précédent était oublié) ; somme des débits contre cumul : 0,043 mm sur 562 mm ; groupe `49H51H` sans cas particulier ; `tp` contre `tirf+tsnowp+tgrp` : 0,029 mm/h au plus.
- **Neige réelle** : Alpes (Mont-Blanc, ≈ 60 mm d'eau cumulés à +51 h) et Pyrénées ; 38 à 40 % des points précipitants entre −2 et 0 °C ne sont pas classés en neige, **à expliquer**.
- **AROME contre ARPEGE 0,1°** (moyenne par blocs 5×5, 41 145 cellules, échéances 3 et 6) : vent corrélation 0,96, biais ≤ 0,22 m/s, RMSE ≈ 1 m/s ; pression corrélation 0,999 (pas de facteur d'unité) ; `t2m` biais +0,25 à +0,31 K, corrélation 0,98 ; pluie : AROME donne 0,82 à 0,87 du cumul ARPEGE, corrélation 0,51 à 0,59 (écart de modèle, cause non testée).
- **Plausibilité** (échéances 0-6) : aucune valeur hors bornes physiques ; rafale < vent moyen sur 0,05 à 0,88 % des points (vent faible, cause inconnue) ; le masque de bord change sur 2 (AROME) et 6 (HD) points entre les échéances 0 et 1 ; un point HD dépasse 8 K/h.
- **Valeurs extrêmes** sur 0-51 h : débits de 180 à 220 mm/h et vent qui saute de 35 m/s au large des Baléares (cellule orageuse plausible) ; chute de `t2m` de 11 à 12 K en 1 h au Portugal, présente dans les deux modèles : **non confrontées à une autre source**.
- **Tuilage, variantes** : vent 8 bits (`vent8`) −14 Mo par échéance HD pour 0,25 m/s d'erreur max ; sans bordure à z8 : −1 % seulement et couture qui revient (déconseillé) ; WebP avec perte pour `t2m` : erreur 1,77 K (rejeté) ; plus proche voisin pire que le bilinéaire à tous les zooms ; z9 réduit l'erreur à position quelconque d'un facteur 3 à 10 mais pèse environ 2,5 fois plus.
- **`cloud_total` en profil `reduit`** : 5 % d'écart max aux fronts nets (0,22 % en moyenne).

## Décisions que cela appelle

1. **Profil de poids de production** : `reduit` (0,92 Go par tirage) après validation visuelle de `wind8`, plutôt que `contrat` (2,87 Go). Décision D13.
2. **Règle de décodage unique** `min + (code−1) × quant_step` à inscrire au contrat (fait) et à implémenter dans la visualisation.
3. **Publication par variable et par échéance** (D8 révisée) à cause des tirages HD incomplets.
4. **HD 06Z au-delà de 18 h** : à rejouer quand Météo-France aura complété SP1 ; en attendant le tirage HD 03Z (0-51 h) sert de substitut dans les mesures.

## Reste à faire

- Adapter la visualisation à `quant_step` et à `wind8`, puis valider visuellement le profil `reduit`.
- Rejouer HD 06Z 19-51 quand SP1 sera publié ; comprendre pourquoi il ne l'est pas.
- Expliquer les 38-40 % de précipitations « non neige » entre −2 et 0 °C ; confronter les valeurs extrêmes à une autre source.
- Mesures sur un vrai téléphone en 4G ; particules de vent ; sélecteur de modèle ; manifest ; fond de carte.

---

# Étape 3 : profil de poids retenu (17 octobre 2026, fin d'après-midi)

## Verdict

**Adopter le profil `reduit2`** (décision D13 tranchée). Poids mesuré d'un tirage complet de 52 échéances : **0,99 Go** (HD 0,545 + AROME 0,444), soit **65 % de moins que `contrat` (2,87 Go)** et 7,6 % de plus que `reduit` (0,92 Go), pour une qualité visuelle meilleure sur la température et les rafales.

## Comparaison visuelle `contrat` contre `reduit` (agent visualisation, WebGL logiciel, seuil de ΔE > 2,3)

- **France entière** : 0,1 à 2,5 % de pixels au-dessus du seuil, aucune différence nette ; pluie, neige et CAPE à z6 identiques au pixel.
- **Zooms Alpes, Provence, front de nuages** : 1 à 11 % de pixels au-dessus du seuil de perception, 0,2 à 0,7 % nettement ; coutures à 1-2 niveaux.
- **À l'œil** : le banding de la température est perceptible dans les zones lisses (un peu plus net à 0,5 K qu'à 0,39 K) ; le grain des rafales faibles est visible ; le plafond z7 se voit sur les contours de la CAPE ; le reste ne se voit pas. D'où l'ajustement `reduit2`.
- **Octets** (vent, `contrat` → `reduit`) : à l'ouverture 3,42 → 1,08 Mo (bureau), 0,91 → 0,35 Mo (mobile émulé) ; frise 0-51 h entière 93,9 → 29,4 Mo (bureau), 21,5 → 8,4 Mo (mobile). Rapporté, non rejoué.

## `reduit2` : paramètres

Identique à `reduit` (vent `wind8` ±48 m/s, `rh2m` et `cloud_total` au pas de 1 %, zoom maximal 7 sauf `precip_rate` et `snow_fraction` à 8) sauf **`t2m` au pas de 0,25 K** (plage 243,15 à 306,65 K, soit −30 à +33,5 °C) et **`gust10` au pas de 0,25 m/s** (plage 0 à 63,5 m/s).

| Contrôle | Résultat |
|---|---|
| Poids **mesuré**, tirage complet (HD 03Z 52 échéances + AROME 06Z 52 échéances) | **0,990 Go** (412 + 412 fichiers) ; HD 0,545 Go en 1 min 59, AROME 0,444 Go |
| Valeurs réelles des tirages | `t2m` −15,8 à +30,8 °C (HD), −13,4 à +30,7 °C (AROME) ; `gust10` max 36,8 m/s ; **aucun pixel écrêté sur 560 fichiers** (rapporté) |
| Vent réel | vitesse moyenne max 25,5 m/s (HD 25,3, AROME 25,5), \|u\|, \|v\| max 25,4 : loin de l'écrêtage de ±48 (**recontrôlé**) |
| Précision de relecture à 200 points | centre de pixel : 0,125 pour `t2m` et `gust10` (= demi-pas) ; position quelconque : 0,45 K et 0,37 m/s (double interpolation) (rapporté) |

## Limite importante : une plage fixe ne couvre pas toutes les saisons

Une plage de 63,5 K en 254 niveaux à 0,25 K ne couvre ni les canicules (au-dessus de 33,5 °C, courantes en été en France) ni les grands froids (sous −30 °C). L'agent a ajouté `reduit2-ete` (−15 à +48,5 °C) et `reduit2-hiver` (−40 à +23,5 °C), mais le plus robuste est une **plage propre à chaque fichier** : `min` choisi d'après les données de l'échéance (par exemple `min = tmin − 1 K`), pas fixe à 0,25 K ; l'écart réel d'un champ sur la France (≈ 47 K ce jour-là) tient dans 63,5 K. Le client lit déjà `min` et `quant_step` dans les métadonnées, donc **aucun changement côté visualisation**. Décision D14.

## Conséquences

1. Adopter `reduit2` avec plage dynamique par fichier (D13 révisée, D14) ; revoir le poids d'un tirage sur un tirage d'été.
2. La visualisation décode `quant_step` pour `contrat` et `reduit`, mais **`?profil=reduit2` n'est pas encore prévu** ; à ajouter avec l'intégration du fond de carte.
3. `wind8` : validé sur un seul tirage (le vent moyen max est 25,5 m/s) ; à rejouer sur un tirage de tempête.
