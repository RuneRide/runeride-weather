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
