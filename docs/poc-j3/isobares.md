# PoC J3 : brique « isobares »

Statut : PoC. Isobares tous les 2 hPa et repères A / D calculés par le pipeline à partir de `mslp` (AROME 0,025° seulement : l'AROME HD n'a pas de pression au niveau de la mer, voir [poc-j1-contrat.md](../poc-j1-contrat.md) §2). Rien n'est branché dans `main.js` : la couche est un module ES autonome (`viewer/js/isobares.js`) et une page de démonstration (`viewer/isobares.html`).

Tout ce qui suit est **mesuré** sur le tirage `2026-10-17T06Z` (52 échéances) sur la machine de développement, sauf mention « ESTIMATION ».

## 1. Ce qui a été fait

| Fichier | Rôle |
|---|---|
| `pipeline/isobars/contours.py` | conversion Pa → hPa, lissage gaussien normalisé par le masque, marching squares vectorisé, assemblage en lignes, Douglas-Peucker (numpy seul : pas de scipy ni shapely) |
| `pipeline/isobars/extrema.py` | extrema A / D par proéminence (inondation + union-find) |
| `pipeline/isobars/calcul.py`, `__main__.py` | chaîne complète pour une échéance, CLI parallèle, écriture GeoJSON |
| `pipeline/isobars/mvt.py` | évaluation MVT / PMTiles (encodeur MVT minimal écrit pour l'occasion, aucune dépendance) |
| `pipeline/isobars/validation.py` | validation géométrique, topologie, bord du domaine, études lissage et simplification |
| `viewer/js/isobares.js`, `viewer/isobares.html` | affichage MapLibre, curseur de temps |
| `tests/isobars/test_isobars.py` | 35 tests unittest |

Sortie : `out/isobars/arome/<tirage>/NN.geojson` (un par échéance) + `stats.json` ; variante sans lissage `out/isobars/arome-sans-lissage/…` (pour comparer) ; tuiles `out/isobars/arome/<tirage>/mvt/NN.pmtiles` (évaluation).

Format GeoJSON (une `FeatureCollection` par échéance, avec un membre étranger `proprietes` : modèle, tirage, échéance, paramètres) :
- lignes : `LineString`, `properties = {type: "isobare", niveau (hPa, entier), majeur (multiple de 10), ferme}` ;
- extrema : `Point`, `properties = {type: "extremum", genre: "A" | "D", libelle: "Anticyclone" | "Dépression", pression (hPa, 0,1), proeminence}`.
Coordonnées arrondies à 1e-4° (11 m).

## 2. Contours

### 2.1 Algorithme

1. `mslp` (Pa) / 100 → hPa (float64).
2. Lissage gaussien (§2.2).
3. Marching squares : une cellule n'est traitée que si ses **quatre coins sont valides** (et, avec `MARGE = 1`, ses voisines aussi). Un NaN n'est ni « haut » ni « bas » : aucune cellule touchant un NaN n'engendre de segment, donc **aucune fausse ligne le long du bord du trapèze** ; une isobare qui atteint le masque s'y arrête (ligne ouverte). Selles (cas 5 et 10) tranchées par la moyenne des quatre coins.
4. Assemblage : chaque arête de la grille est un nœud (deux cellules voisines partagent le même identifiant) ; un nœud a au plus 2 segments ; les lignes ouvertes partent des nœuds de degré 1, les anneaux sont repris ensuite (premier point = dernier).
5. Les anneaux de moins de 12 km de long sont supprimés (bruit résiduel).
6. Douglas-Peucker (§2.3), conversion en lon/lat (la latitude décroît dans les fichiers canoniques).

Niveaux : multiples de 2 hPa couverts par le champ ; `majeur` aux multiples de 10 (trait plus marqué à l'affichage).

### 2.2 Lissage : noyau gaussien σ = 4 mailles (10 km)

Sans lissage, la maille de 2,5 km donne des isobares inutilisables (captures `img/france-sans-lissage.webp` et `img/zoom-sans-lissage.webp`) : la pression réduite au niveau de la mer garde la signature du relief (réduction faite à partir de la pression en surface) ; dans les zones à faible gradient, une variation de 0,3 hPa à l'échelle d'une maille crée des centaines de petites boucles. Le lissage est **normalisé par le masque** (`conv(F·m) / conv(m)`) : pas de valeur factice injectée au bord du trapèze.

Mesure sur 3 échéances (étude sans suppression des anneaux courts ; la « rugosité » est la somme des angles de virage par km de ligne, rad/km ; l'écart de position est la distance de sommets lissés au tracé non lissé) :

| σ (mailles) | lignes (éch. 0 / 24 / 48) | anneaux < 12 km (0 / 24 / 48) | longueur km (éch. 24) | rugosité (éch. 24) | écart moyen / p95 (km, éch. 24) | écart RMS du champ (hPa, éch. 24) |
|---|---|---|---|---|---|---|
| 0 | 1238 / 1358 / 1714 | 869 / 958 / 1159 | 61 173 | 0,316 | — | 0 |
| 1 | 148 / 168 / 235 | 31 / 42 / 76 | 39 476 | 0,126 | 0,59 / 1,45 | 0,082 |
| 2 | 49 / 49 / 70 | 2 / 6 / 3 | 31 853 | 0,056 | 1,00 / 2,70 | 0,119 |
| 3 | 32 / 25 / 42 | 0 / 0 / 0 | 29 188 | 0,032 | 1,36 / 3,63 | 0,141 |
| **4** | **27 / 21 / 31** | **0 / 0 / 1** | **27 978** | **0,0225** | **1,71 / 4,72** | **0,157** |
| 6 | 19 / 18 / 23 | 1 / 0 / 0 | 26 467 | 0,014 | 2,26 / 6,30 | 0,184 |
| 8 | 19 / 20 / 21 | 0 / 0 / 0 | 25 446 | 0,010 | 2,98 / 8,83 | 0,210 |

**Choix σ = 4** (noyau de rayon 3σ = 12 mailles = 30 km, largeur à mi-hauteur ≈ 23 km) : c'est le genou de la courbe. Au-delà de 4 la rugosité ne gagne presque plus (0,0225 → 0,014) alors que le déplacement des lignes augmente de 30 à 75 %. À σ = 4 il ne reste plus d'anneau parasite (sauf 1 à l'échéance 48), le nombre de lignes passe de ~1 400 à ~25, et le tracé moyen bouge de 1,7 km (moins d'une maille) pour un écart RMS du champ de 0,16 hPa (< 1/10 du pas de 2 hPa). Les isobares synoptiques ont des rayons de courbure de plusieurs centaines de km : un lissage de 10 km les respecte. **Limite assumée** : les structures mésoéchelle (brises, creux méditerranéens de quelques dizaines de km) sont gommées ; l'écart maximal du champ lissé au champ brut atteint 2,3 à 3,3 hPa, sur le relief alpin et pyrénéen (artefact de réduction, pas un signal).

### 2.3 Simplification : Douglas-Peucker, 0,1 km

Mesure sur l'échéance 24 (σ = 4), erreur = |champ lissé interpolé − niveau| aux **milieux de segments** (les sommets restent exactement sur la ligne) :

| tolérance (km) | sommets | erreur max (hPa) | p95 | moyenne |
|---|---|---|---|---|
| 0 | 16 161 | 0,004 | 0,000 | 0,0001 |
| 0,05 | 6 895 | 0,008 | 0,001 | 0,0004 |
| **0,1** | **5 023** (−69 %) | **0,013** | 0,004 | 0,0009 |
| 0,25 | 3 101 | 0,032 | 0,009 | 0,0028 |
| 0,5 | 2 069 | 0,056 | 0,017 | 0,0056 |
| 1,0 | 1 381 | 0,111 | 0,033 | 0,0107 |

**0,1 km** : environ 1 px à z9 (106 m/px à 46° de latitude en tuiles de 512 px), donc invisible aux zooms utilisés, pour une erreur maximale de 0,013 hPa (< 1 % du pas) et 69 % de sommets en moins. Au-delà, on gagne peu de poids (0,25 km : −38 % de sommets de plus) et l'on risque des facettes visibles à fort zoom.

### 2.4 Extrema A / D

Détection sur un champ **plus lissé** (σ = 8 mailles, 20 km) pour effacer le bruit de réduction sur le relief, par **proéminence** : hauteur dont il faut descendre pour rejoindre un maximum plus élevé **ou le bord du domaine valide** (un sommet collé au masque pourrait n'être que le flanc d'un centre situé hors domaine : il est écarté). Inondation décroissante et union-find sur la grille sous-échantillonnée d'un facteur 3, position affinée sur la grille pleine. La valeur affichée est celle du champ des isobares (σ = 4) au point trouvé, donc cohérente avec les lignes.

Seuil : **proéminence ≥ 1 hPa** (un demi-pas d'isobare). Nombre d'extrema cumulés sur les 52 échéances (anticyclones / dépressions / nombre d'échéances ayant au moins un extremum) :

| σ de détection | seuil 1 hPa | seuil 2 hPa (un pas : isobare fermée garantie) |
|---|---|---|
| 4 | 131 / 57 / 52 | 1 / 0 / 1 |
| **8** | **17 / 29 / 27** | 0 / 0 / 0 |

À 2 hPa, **aucun extremum** ne ressort sur ce tirage (situation anticyclonique à faible gradient : le centre d'action du 1039 hPa est hors domaine, dans l'angle nord-ouest, donc écarté). À σ = 4 et 1 hPa on obtient 131 anticyclones, presque tous des artefacts de réduction (Alpes, Sardaigne, Corse). Le compromis σ = 8 + 1 hPa donne 46 extrema (17 A, 29 D), répartis sur 27 échéances. Ce seuil est un **réglage à valider avec un météorologue** ; un extremum sans isobare fermée autour est possible.

## 3. Validation (`out/isobars/validation.json`)

`python3 -m pipeline.isobars.validation …`, sur 3 échéances (0, 24, 48), lecture des **GeoJSON écrits** (donc arrondi des coordonnées compris).

- **Cohérence géométrique** : 200 sommets tirés au hasard (graine fixe), champ lissé interpolé bilinéairement. Tolérance déduite de l'arrondi à 1e-4° : erreur de position ≤ 0,5e-4·√2° ≈ 7,9 m, multipliée par le gradient maximal du champ (0,16 à 0,23 hPa/km) = **0,0013 à 0,0018 hPa**. Écart mesuré maximal : **0,00033 / 0,00041 / 0,00041 hPa**, donc 200 sur 200 conformes pour les trois échéances (0 NaN). Aux milieux de segments (effet de Douglas-Peucker) : max 0,006 / 0,011 / 0,010 hPa.
- **Topologie** : 0 anneau mal fermé ; toutes les extrémités de lignes ouvertes (17 / 14 / 17 lignes ouvertes) sont au contact du bord du domaine valide ; **0 croisement strict** entre segments (toutes lignes et tous niveaux confondus, hachage spatial) sur les 3 échéances.
- **Bord du domaine masqué** : part de la longueur de ligne à moins de 1,5 maille du masque : **0,0008 % / 0 % / 0,0012 %** (≤ 0,33 km sur 25 000 à 28 000 km) avec la méthode retenue, contre **31 % / 29 % / 26 %** avec une méthode naïve (NaN remplacés par la moyenne, mêmes lissage et niveaux), qui trace des isobares le long du trapèze.
- **Avant / après lissage** (3 échéances) : voir le tableau du §2.2 ; en nombre de lignes 1 238 → 27, 1 358 → 21, 1 714 → 31 ; en longueur 53 067 → 25 031 km, 61 173 → 27 978 km, 72 961 → 28 624 km (avant simplification), c'est-à-dire que 53 à 61 % de la longueur brute était du crénelage.
- Tests unitaires : `python3 -m unittest discover -s tests/isobars -t .` (35 tests : marching squares sur cercle et plan incliné avec solution exacte, NaN, selles, assemblage, extrema, unités, lissage, Douglas-Peucker, croisements, MVT). Le test PMTiles est ignoré si `pmtiles` n'est pas dans `PYTHONPATH`.

## 4. Mesures de génération et de poids (52 échéances)

| Mesure | Valeur |
|---|---|
| Temps, 1 processus | **42,0 s** pour 52 échéances (0,81 s par échéance en moyenne : lissage 0,15 s, contours + simplification 0,15 s, extrema 0,50 s) |
| Temps, 16 processus | 21,1 s (accélération de ×2 seulement : machine partagée avec d'autres tâches pendant la mesure, cause non investiguée) |
| Poids total GeoJSON | **4,48 Mo** (73 à 99 ko par échéance, 86 ko en moyenne) ; ~1,46 Mo en gzip (28 ko par échéance, mesuré par `gzip -6`) |
| Poids total sans lissage (σ = 0) | 24,0 Mo |
| Lignes par échéance | 20 à 43, moyenne 29,4 (sans lissage : 330 à 606, moyenne 436) |
| Sommets par échéance | 5 194 en moyenne (sans lissage : 25 436) |
| Extrema | 46 sur 52 échéances (17 A, 29 D), 27 échéances en ont au moins un |

## 5. Alternative vectorielle : MVT en PMTiles

`python3 -m pipeline.isobars.mvt` (nécessite `PYTHONPATH=<…>/scratchpad/pylib2` pour le paquet `pmtiles`, voir le contrat §6) : encodeur MVT 2.1 écrit à la main (protobuf, géométrie à 4096, recouvrement de 64 unités pour que les libellés ne soient pas coupés au joint, Douglas-Peucker à 4 unités soit ~0,5 px, gzip), un PMTiles par échéance, z3 à z8, couches `isobares` (lignes `niveau`, `majeur`) et `extrema` (points). Calculé à partir des GeoJSON : mêmes lignes.

| | GeoJSON | MVT / PMTiles |
|---|---|---|
| Poids par échéance (fichier complet) | 86 ko brut, **28 ko gzip** | 75 à 93 ko (déjà gzip dans les tuiles ; ~400 tuiles, z3 à z8) |
| Poids total 52 échéances | 4,48 Mo (1,46 Mo gzip) | 4,49 Mo |
| Génération | incluse (0,8 s par échéance) | 0,24 s par échéance en plus (13,4 s pour 52) |
| Octets transférés pour la vue France (z5,2), une échéance | **86 ko** brut (serveur local sans gzip), ~28 ko avec gzip (ESTIMATION d'après `gzip -6`) | **~23 ko** (7 requêtes Range : 16,7 ko pour l'en-tête et le répertoire, puis 6 tuiles de 0,7 à 1,7 ko) |
| Requêtes HTTP | 1 | 7 par échéance |
| Création de la couche jusqu'au rendu terminé (`idle`), 3 essais | 318 / 314 / 343 ms | 322 / 332 / 330 ms |
| Changement d'échéance jusqu'au rendu (curseur), 2 changements × 3 essais | 57 à 103 ms | 370 à 386 ms (nouvelle source, en-tête + tuiles, puis retrait de l'ancienne) |
| Rendu | référence | identique à l'œil ; 0,5 % des pixels de la vue France diffèrent de plus de 30 niveaux (grille à 4096, simplification), 0,008 % au zoom 8 (`img/france-mvt.webp`, `img/zoom-mvt.webp`) |

Mesures : Chrome 155 sans interface, WebGL logiciel (SwiftShader), serveur local sans latence (`out/isobars/mesures-affichage.json`). Les durées ne sont pas représentatives d'un téléphone ; les octets et le nombre de requêtes le sont. Le GeoJSON n'est pas visible dans le journal réseau de CDP (chargé par un worker) : son poids est celui du fichier, et le gzip est une estimation.

Lecture :
- À ce volume (un seul domaine, ~30 lignes), le MVT **ne fait pas mieux** que le GeoJSON compressé : le poids transféré est du même ordre, le curseur est plus lent (un PMTiles par échéance impose une source neuve, soit 7 requêtes, contre un `setData` d'un seul fichier) ; **ESTIMATION** : avec 100 ms de latence, le MVT ajoute au moins 2 allers-retours en série (en-tête, puis tuiles), soit ~200 ms de plus que le GeoJSON.
- Le MVT devient intéressant si la géométrie grossit : isotherme et isobares sur plusieurs modèles, zones plus vastes (ARPEGE Europe), tracé non lissé plus fin ; il découpe par tuile (seule la vue est transférée, sans analyser tout le fichier) et épargne le travail de `geojson-vt` sur le téléphone. Il s'insère alors dans la même chaîne PMTiles que le raster.

## 6. Affichage

`viewer/js/isobares.js` (ES module, ne dépend de rien d'autre) :

```js
import { ajouterIsobares, majEcheance, retirerIsobares } from './js/isobares.js';
await ajouterIsobares(map, { src: '/isobares/arome/2026-10-17T06Z', avant: idPremierLibelleDeLieu, echeance: 12, format: 'geojson' });
await majEcheance(map, 13);      // curseur de temps
retirerIsobares(map);
```

- `src` : base des données d'un tirage ; `avant` : id de la couche devant laquelle insérer (par défaut, au-dessus de tout) ; `format` : `'geojson'` (défaut) ou `'mvt'` (la page doit avoir enregistré le protocole `pmtiles://`) ; `valeurs` et `extrema` (booléens) ; renvoie une promesse résolue quand les données sont chargées. Export auxiliaires : `urlEcheance`, `echeanceCourante`, `couchesIsobares`.
- Couches : halo sombre discret (`#0b0d10`, opacité 0,4, flou 1) sous une ligne claire fine (`#e8eef4` ; multiples de 10 : 2,2 px à z10 et opacité 0,95, autres 1,2 px et 0,62) ; **valeurs en hPa le long des lignes** (`symbol-placement: line`, `symbol-spacing: 260`, `text-max-angle: 28`, police « Noto Sans Medium » de la pile du fond Protomaps, halo de 1,8 px, priorité aux multiples de 10) ; marqueurs **A** (bleu clair) et **D** (rouge clair) avec la valeur en dessous, toujours affichés.
- `viewer/isobares.html` : démo autonome (MapLibre 5.24.0, pmtiles 4.5.0, @protomaps/basemaps 5.7.2 avec les mêmes empreintes SRI que le fond de carte, fond Protomaps sombre depuis `/basemap/domaine-z9.pmtiles`, montage `/isobares`). Curseur de 0 à 51 h, lecture 1 h/s, validité en UTC et en heure de Paris, bascule lissage / format. Paramètres : `?n=&c=lon,lat&z=&format=geojson|mvt&lissage=0|1&panneau=0&theme=clair`.
- Captures (`docs/poc-j3/img/`, 1280×800 sauf mention) : `france-lisse.webp`, `france-sans-lissage.webp` (échéance +15 h), `zoom-lisse.webp`, `zoom-sans-lissage.webp` (Provence, z8), `zoom-extremum.webp` (A / D, z7), `france-mvt.webp`, `zoom-mvt.webp`, `france-curseur-36.webp` (avec le curseur), `mobile-lisse.webp` (390 px, ×2).

### Lisibilité (`out/isobars/mesures-etiquettes.json`)

Les collisions sont gérées par MapLibre (libellés de lignes entre eux et avec les noms de lieux, qui gardent la priorité : les isobares sont insérées sous le premier libellé de lieux) ; rien ne se chevauche. Sur 9 vues (échéances 0, 15, 36 ; z5,2, z7, z9 ; interrogation des symboles réellement placés) : à z5,2, 8 à 10 niveaux visibles sur 9 à 12 portent au moins une étiquette ; à z7, 1 à 4 niveaux sur 2 à 6 ; à z9, 1 à 2 niveaux, tous étiquetés. Les niveaux sans étiquette sont ceux dont le tracé visible est plus court que l'étiquette ou trop courbe (angle maximal 28°). Sans lissage, **aucune étiquette** n'est posée (tracé trop anguleux pour 28°).

Bord du domaine : les isobares s'arrêtent net sur le bord du trapèze AROME (par exemple à 12°O en bas à gauche de `france-lisse.webp`) ; il n'y a aucune ligne parasite le long du bord (§3). Limites constatées : les marqueurs A / D, affichés avec chevauchement autorisé, peuvent recouvrir un nom de ville (Innsbruck dans `france-curseur-36.webp`) ; le A de Corse est sur le relief, probable résidu de réduction.

## 7. Recommandation

- **PoC** : **GeoJSON**, un fichier par échéance (86 ko, ~28 ko gzip), `setData` au changement d'échéance (≈ 60 à 100 ms dans nos mesures), aucune brique de plus ; l'hébergeur doit activer la compression des `application/geo+json` (le serveur de test ne le fait pas).
- **Production** : la spécification (§5.2) dit « tuiles vectorielles ». Ce que nous mesurons ne le justifie pas à ce volume (poids équivalent, curseur 4 à 6 fois plus lent). Proposition : garder le GeoJSON compressé pour les isobares d'un seul domaine, **passer au MVT / PMTiles** dès que le volume par échéance dépasse quelques centaines de ko compressés ou que l'on ajoute des modèles / isothermes en pleine résolution (**seuil indicatif, ESTIMATION**, à mesurer), et ajuster la formulation du §5.2 en conséquence. L'encodeur et le chemin d'affichage MVT sont prêts et testés.

## 8. Écarts, limites, points ouverts

- **A / D** : la spécification (§6.4) écrit « H / B » ; la mission demande les libellés français « A » (anticyclone) et « D » (dépression) : à trancher et aligner dans la spécification.
- **Pas d'interpolation temporelle** : une isobare ne se mélange pas comme un raster ; l'animation saute d'une heure à l'autre (aucun morphing essayé).
- **AROME HD** : pas de `mslp` : couche à basculer sur AROME 0,025° quand le modèle HD est sélectionné (à traiter côté interface). Le domaine AROME est un trapèze : les centres d'action situés hors domaine (ici le 1039 hPa à l'angle nord-ouest) n'ont pas de marqueur ; ARPEGE (Europe) les couvrirait.
- Extrema : seuil 1 hPa à valider avec un météorologue ; des anticyclones sur le relief (Corse, Alpes) restent probablement des artefacts de réduction à σ = 8.
- Lissage σ = 4 fixe, sans adaptation au gradient ni à l'altitude ; mésoéchelle gommée.
- L'affichage n'a été testé que sur Chrome sans interface (WebGL logiciel, serveur local) : ni vrai téléphone, ni latence réseau. Le fond Protomaps charge ses polices depuis protomaps.github.io (voir [fond-de-carte.md](../poc-j1/fond-de-carte.md)) : la démo n'est pas hors ligne.
- `viewer/serve.py` monte déjà `/isobares` (non modifié). Intégration dans `main.js` : non faite (hors périmètre).
- Attribution à afficher : « Source : Météo-France, Licence Ouverte v2.0 » ; fond « Protomaps © OpenStreetMap contributors » (ODbL).

## 9. Rejouer

```sh
python3 -m pipeline.isobars --canon out/canon/arome/2026-10-17T06Z --out out/isobars                       # 52 GeoJSON
python3 -m pipeline.isobars --canon out/canon/arome/2026-10-17T06Z --out out/isobars --sigma 0 --suffixe=-sans-lissage
PYTHONPATH=<scratchpad>/pylib2:. python3 -m pipeline.isobars.mvt --iso out/isobars/arome/2026-10-17T06Z \
    --out out/isobars/arome/2026-10-17T06Z/mvt --echeances 0,6,12,24,36,51 --zmin 3 --zmax 8
python3 -m pipeline.isobars.validation --canon out/canon/arome/2026-10-17T06Z --iso out/isobars/arome/2026-10-17T06Z --sortie out/isobars/validation.json
python3 -m unittest discover -s tests/isobars -t .
python3 viewer/serve.py     # puis http://127.0.0.1:8187/isobares.html?n=15
```

Options du CLI : `--sigma` (4), `--pas` (2), `--tol-km` (0,1), `--proeminence` (1), `--sigma-extrema` (8), `--echeances`, `--jobs`.
