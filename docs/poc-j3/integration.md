<!-- SPDX-License-Identifier: EUPL-1.2 -->
# J3 : intégration dans le viewer principal

Les trois modules J3 (manifest et choix du modèle, particules de vent, isobares) sont branchés dans `viewer/index.html` / `viewer/js/main.js`. Mesures : Chrome 155 sans interface, WebGL **logiciel** (SwiftShader) : **aucune conclusion sur les images par seconde** ; les octets et les requêtes sont indépendants de la machine, les temps ne le sont pas.

## 1. Ce qui est branché, et comment

| Module | Où | Rôle dans la page |
|---|---|---|
| `modele-auto.js` (+ `/manifest/manifest.json`) | `main.js` : `resoudre`, `choisirTirage`, `plageTemps`, `attribution`, `cheminFichier` | modèle, tirage, échéance, fichiers, plage de la frise, attribution |
| `particules.js` | `creerParticules`, `champVent` | calque de particules, **même cache de tuiles** (`donnees.js`) que le calque vent : aucune requête en double |
| `isobares.js` | `majIsobares` | couche « Pression » et superposition « Isobares » |
| **nouveau** `integration-logique.js` | module pur, 8 tests Node | frise en temps valide, hash, décision de bascule, textes de pastille et d'avis, échéance des isobares, manifest de repli |

`serve.py` monte `/manifest/` (`out/manifest`) et `/isobares/` (`out/isobars`).

### 1.1 Manifest et frise en temps valide

- Le manifest remplace `index.json`. Pour les jeux de test sans manifest (`?src=synth|sans|brute`), `manifestDepuisIndex` en fabrique un à partir de l'inventaire ; pour les profils `contrat` et `reduit`, le manifest de `reduit2` décrit la même arborescence (les fichiers sont lus sous `/data` ou `/data-reduit`).
- La **frise est en temps valide UTC** : `plageTemps(manifest, couche)` donne début et fin (union des modèles, ou du modèle choisi en mode forcé) ; la valeur du curseur est en heures depuis le début de la plage (pas de 0,01 h). À l'ouverture, l'heure courante bornée à la plage (`?t=` en heures depuis le plus ancien tirage ou `#t=2026-10-18T12:00` l'imposent).
- **Heure locale avec décalage** (« 14:00 UTC+2 »), heure UTC dans la ligne de détail et dans l'infobulle (`title`) ; la ligne de détail donne aussi l'échéance (+33,0 h) et le tirage.
- Clavier : ← → ±1 h (Maj ±6 h), Début / Fin = bornes de la plage ; espace = lecture (×1 : 1 h de prévision par seconde, ×2, ×4).

### 1.2 Choix du modèle, pastille, repli

- **Auto** par défaut ; sélecteur **Auto / AROME / AROME HD** et `?modele=` (`auto|arome|arome-hd`, aussi `hd`). Auto : HD à partir de zoom 7 et vue dans le domaine, hystérésis (zoom 6,75 et tolérance de 0,25° pour rester en HD), repli par couche : règles de `choisirModele`. La vue (zoom, emprise) n'est relue qu'**en fin de geste** (`moveend`), pas à chaque image ; l'échéance est résolue à chaque image par `resoudre`.
- **Pastille** en haut à droite : « AROME HD 0,01° · tirage 03 h UTC ». **Avis discret** sous la pastille (texte ochre, une à deux lignes) dans ces cas : couche absente du modèle (repli), échéance hors plage du modèle demandé, tirage de repli (« Tirage 03 h : le tirage plus récent n'a pas cette échéance. »), vue hors du domaine HD, HD forcé à zoom < 5, bascule en cours. L'attribution de la barre du bas suit le modèle affiché (`attribution()`) : « Source : Météo-France, Licence Ouverte v2.0 · modèle AROME HD 0,01° · Protomaps © OpenStreetMap contributors (ODbL) ».
- **Bascule sans clignotement** (`decisionBascule`) : quand le modèle ou le tirage voulu change (zoom, panoramique, passage au tirage 03 h), on **garde l'ancien calque affiché** (il continue de suivre l'heure), on demande les tuiles visibles du nouveau (échéances A et B), et on **échange en une seule image** quand elles sont toutes arrivées (chargées ou absentes du fichier). Si l'ancien ne peut plus servir (instant hors de sa plage), l'échange est immédiat. Un changement de couche demandé par l'utilisateur est toujours immédiat. **Pas de fondu** entre modèles : coupure franche au moment de l'échange (la maille change de toute façon) ; un fondu court reste possible plus tard.

### 1.3 Particules

- Case « Particules » : **active par défaut sur la couche Vent**, désactivée sur les autres tant que l'utilisateur n'a pas choisi (un clic fixe le choix, `#…&p=1`) ; possible en superposition sur toute couche et même sur « Pression ».
- Champ de vent : `champVent()` résout `wind10` dans le **modèle affiché** à l'instant courant (avec repli sur l'autre modèle) ; sur la couche Vent ce sont exactement les fichiers du calque couleur. `ParticulesVent` est configuré par `fournisseurChamp: () => champVent()` (le temps est géré par la page, pas par `fournisseurPmtiles`) ; nombre adaptatif de la brique.
- **Ordre des calques** : fond (terre, eau) < calque couleur < particules < isobares < routes, frontières, libellés (`addLayer(couche, 'roads_tunnels_other_casing')` : le premier identifiant du fond qui doit rester au-dessus).
- **Mouvement réduit** (`prefers-reduced-motion`, ou `etat.mouvementReduit`) : les particules ne tournent pas, les **flèches** de la brique J1 s'affichent à la place (290 flèches comptées en France dans le test) ; le bouton indique « (flèches) ». Sinon les flèches sont masquées dès que les particules sont visibles.
- **Onglet masqué** : `visibilitychange` désactive puis réactive les particules.

### 1.4 Isobares

- Couche **« Pression »** (pas de raster : fond + isobares ; légende remplacée par « Isobares tous les 2 hPa · A anticyclone · D dépression ») et case **« Isobares »** pour la superposer aux autres couches.
- Échéance = celle du tirage AROME le plus récent la plus proche du temps valide (`echeanceIsobares`, bornée à 0..51) ; `majEcheance` seulement quand elle change ; le tracé précédent reste affiché jusqu'à l'arrivée du suivant (module J3). En mode HD, l'avis ajoute « Pression : AROME (pas de pression en HD). » ; la frise de la couche Pression couvre le tirage AROME.
- Les libellés des isobares (valeurs, A / D) sont des couches de symboles MapLibre placées sous les libellés du fond ; MapLibre gère les collisions entre eux et avec les noms de lieux. Ils utilisent les polices du fond de carte : **sans fond (`?fond=0`) pas de valeurs d'isobares**.

### 1.5 URL partageable

`#couche=vent&t=2026-10-18T12:00&c=46.600,2.500&z=5.20&m=auto&p=1&i=0` : couche, temps valide UTC, centre, zoom, mode de modèle, particules, isobares. Réécrite à la fin d'un geste, d'un changement de couche, du clavier et de la pause. Un ancien `t=` numérique est ignoré. Paramètres de requête conservés : `?profil=`, `?theme=`, `?opacite=`, `?fond=`, `?src=`, `?modele=`, `?run=` (inutilisé : le tirage vient du manifest), plus `?particules=0|1`, `?isobares=1`, `?latence=ms` (réseau lent simulé, tests), `?precharge=0` (mesure de l'ouverture seule).

## 2. Mesures

### 2.1 Ouverture : vue France, vent + particules + fond de carte, profil reduit2, 12:00 UTC le 18/10, 2 exécutions par cas

Octets lus par CDP (`Network.loadingFinished`, octets réellement reçus, sans préchargement) ; le serveur de test ne compresse pas (le code de la page serait ~4 fois plus petit avec gzip : **ESTIMATION**).

| Poste | Bureau 1280×800 | Mobile émulé 390×844 (dpr 3) |
|---|---|---|
| Données météo (tuiles Range) | 0,197 Mo, 13 requêtes | 0,175 Mo, 9 requêtes |
| Fond de carte (tuiles) | 0,255 Mo, 5 requêtes | 0,301 Mo, 5 requêtes |
| Polices et sprites du fond | 0,463 Mo, 10 requêtes | 0,411 Mo, 9 requêtes |
| Manifest | 0,021 Mo, 1 requête | idem |
| Bibliothèques (MapLibre, pmtiles, basemaps) | 0,302 Mo, 4 requêtes | idem |
| Code de la page (non compressé) | 0,178 Mo, 15 requêtes | idem |
| **Total** | **1,42 Mo** | **1,39 Mo** |
| Première couche visible (depuis le début de navigation) | 1,34 et 1,35 s | 1,36 et 1,50 s |
| Carte complète (`idle`) | 1,51 et 1,52 s | 1,55 et 1,60 s |

- **Avec isobares** en plus (vent + particules + fond + isobares) : première couche 1,51 et 1,55 s (bureau), 1,53 et 1,82 s (mobile). Octets : même total, plus **un fichier GeoJSON de 91 Ko** (échéance 30 ; 4,5 Mo pour les 52 échéances, non compressé) ; le téléchargement est fait par un Worker de MapLibre, **absent de la mesure CDP de la page** : l'ajout est donc connu par la taille du fichier, pas par la capture. Chaque heure de frise charge un nouveau fichier de 76 à 91 Ko.
- Sans fond de carte (`?fond=0`) : première couche 0,81 à 0,84 s (bureau), 0,72 à 0,80 s (mobile) ; total 0,70 Mo / 0,68 Mo. Le fond de carte et ses polices pèsent donc 0,72 / 0,71 Mo et retardent la première couche d'environ 0,5 à 0,7 s en réseau local.
- **Particules : surcoût nul en requêtes** (mêmes tuiles) ; mesure de la brique : 34 requêtes / 0,72 Mo de tuiles pour les deux échéances A et B seules, reprises ici.
- À 12:00 UTC le jour de l'ouverture et à zoom France, AROME 0,025° est choisi (tuiles plus légères que le HD de la mesure J1 : 0,2 Mo contre 1,08 Mo).

### 2.2 Bascule HD ↔ AROME en zoomant (vent, pastille, particules actives, cache vide au départ)

| Étape | Bureau | Mobile |
|---|---|---|
| Zoom 6 (AROME, déjà affiché) | 0 octet | 0 octet |
| Zoom 8 : bascule vers AROME HD | **50,9 ko, 3 requêtes** (2 tuiles, 2 en-têtes de 16 ko) | 50,9 ko, 3 requêtes |
| Retour à zoom 6 : AROME | 0 octet (cache) | 0 octet |
| Nouveau zoom 8 : HD | 0 octet (cache) | 0 octet |
| Zoom 8 ailleurs (Alpes) | 76,3 ko, 3 requêtes | 55,0 ko, 2 requêtes |

Cache de 360 tuiles décodées (LRU) : le va-et-vient ne retélécharge rien tant que les tuiles restent dans le cache.

**Bascule sans clignotement, mesurée avec 500 ms de latence simulée par requête** (`?latence=500`, couche température, zoom 6 → 8,2, 120 images rendues) : l'échange HD a lieu à **1,02 s** ; pendant l'attente, **51 images** sont rendues avec l'ancien calque AROME, **aucune image sans tuile** (minimum 2 tuiles dessinées par image) ; la pastille et l'attribution changent à l'échange, pas avant.

## 3. Captures (`docs/poc-j3/img/`)

`j3-vent-particules-isobares.webp` (vue d'ensemble, vent + particules + isobares, bureau), `j3-zoom-hd-pastille.webp` (zoom Alpes en HD : pastille, avis, isobares sous les routes et libellés), `j3-pression-france.webp` (couche Pression), `j3-mobile-hd.webp` et `j3-mobile-pression.webp` (390 px), `j3-mouvement-reduit.webp` (flèches à la place des particules).

Constats : les particules sont **discrètes** sur ces échéances (vent faible, 0 à 40 km/h : traînées courtes et sombres, en partie masquées par la palette sombre du vent faible) ; les isobares blanches restent lisibles au-dessus des particules et sous les noms de villes ; les valeurs d'isobare tournent avec la ligne et leur halo sombre les détache du fond ; sur mobile, la pastille et l'avis occupent trois lignes en haut de l'écran (l'avis « Pression : AROME… » est trop long, à raccourcir).

## 4. Non-régression

Vérifiés (Chrome sans interface) : valeur au point et légende sous `?profil=contrat&opacite=0.7` (couche Neige : « 0 % neige »), `?profil=reduit&theme=clair&fond=0` (5,5 °C au point), `?src=synth` (manifest de repli, repli AROME → HD signalé), hash relu au chargement (couche, temps, vue, mode, particules, isobares), clavier (→ avance d'une heure), mouvement réduit (flèches), décodage shader contre CPU sans fond : médiane 0,26 niveau, 95e centile 0,47 niveau (avec le fond, les routes et libellés au-dessus faussent la comparaison pixel à pixel : à lancer avec `?fond=0`). Tests Node : **50** (`node --test viewer/tests viewer/tools` : 29 du manifest, 8 de l'intégration, 13 des particules) ; `viewer/tests/package.json` et `viewer/tools/package.json` désignent `tout.test.mjs` pour que les dossiers soient résolus et que tous les fichiers de test soient exécutés.

## 5. Écarts, limites, points ouverts

- **D14 (plage par fichier)** : les paramètres de décodage (`min`, `quant_step`, `levels`) sont lus **une fois par couche** (premier fichier ouvert), pas par fichier. Valable tant que les plages sont fixes (reduit2 actuel) ; à changer quand les plages seront dynamiques (relire les métadonnées à chaque nouveau fichier : une requête de ~1 ko par fichier).
- **Pas de fondu** entre modèles : échange franc, sans image blanche. Pas de fondu non plus entre deux tirages.
- **Particules** : toujours en coordonnées d'écran (traînée effacée à chaque geste) ; nombre adaptatif calibré par un banc GPU logiciel (46 ms : plancher de ~2 800 particules sur bureau, non représentatif).
- **Isobares** : GeoJSON (pas de tuiles vectorielles) : ~91 Ko par heure de frise ; pas de pression en HD ; isobares du seul tirage AROME 06 h (pas de choix de tirage) ; pas de valeur sans fond de carte (polices).
- **Heure** : l'ouverture se fait sur l'heure courante bornée à la frise ; la frise est la plage du modèle (mode forcé) ou l'union des modèles (Auto) ; hors de la plage du modèle demandé, l'avis le dit.
- **Particules et rafales** : la superposition de particules sur une couche sans vent dépend d'un fichier `wind10` de même instant ; absent, rien n'est tracé.
- Mesures : pas de téléphone réel, pas de 4G ; la mesure CDP ne voit pas les requêtes du Worker de MapLibre (isobares) ; pas d'images/s.
- Mobile : l'avis de repli le plus long (Pression en HD) tient sur trois lignes ; à raccourcir.
