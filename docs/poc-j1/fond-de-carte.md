# Brique « fond de carte » (PoC J1)

Statut : PoC. Fond de carte vectoriel PMTiles (Protomaps / OpenStreetMap) pour le viewer. Rien n'est branché dans `viewer/` (hors périmètre de la brique).

## 1. Ce qui a été fait

- `pipeline/basemap/` : réimplémentation Python de `pmtiles extract` (go-pmtiles **non** téléchargé, non exécuté) avec la bibliothèque `pmtiles` 3.8.1 (lecteur/écrivain PMTiles v3).
  - `tuiles.py` : logique pure (tuiles recoupant une boîte, filtrage par zoom, fusion de plages d'octets, choix du build).
  - `reseau.py` : client HTTP poli (Range, reprises à attente exponentielle avec gigue, `Retry-After`, compteurs).
  - `extract.py` : lecture de l'en-tête, des métadonnées et des répertoires par Range (descente seulement dans les répertoires-feuilles utiles), téléchargement des tuiles voulues (au plus 6 requêtes simultanées), écriture du PMTiles.
  - `verifier.py` : contrôle d'en-tête, de couverture, et comparaison octet pour octet avec la source.
  - `glyphes.py` : plages de glyphes nécessaires, déduites des noms présents dans les tuiles (MVT décodé à la main, rien d'exécuté).
  - `serveur.py` : serveur local avec `Range` (127.0.0.1, port libre, jamais 8187/8097/8099).
  - `demo/index.html` : page de test (MapLibre 5.24.0, pmtiles 4.5.0, @protomaps/basemaps 5.7.2, mêmes empreintes SRI que la démo de `runeride-carto`, langue `fr`, thèmes clair/sombre) ; `demo/capture.mjs` : capture Chrome pilotée par CDP.
- Courtoisie : 6 requêtes simultanées au maximum (plafond codé en dur), 0,05 s de pause après chaque requête par fil, 6 essais avec attente 1, 2, 4, 8, 16 s (+ gigue ≤ 25 %), `Retry-After` respecté, User-Agent identifiable. Aucun fichier distant n'est exécuté ; seul le contenu des tuiles est copié tel quel.
- Reprise : les blocs reçus sont journalisés dans `<sortie>.reprise/` ; une relance identique ne les redemande pas ; le fichier final est écrit en `.part` puis renommé. **Testé** : extraction à maxzoom 10 interrompue après 8 s, relancée : 768 blocs repris du journal, 109 requêtes (408,5 Mo) au lieu de 143 (478,9 Mo) ; fichier relu et identique à la source sur 50 tuiles.
- Build retenu : **20261017** (`https://build.protomaps.com/20261017.pmtiles`, 138 941 094 895 octets, modifié le 2026-10-17 09:04 UTC) : HTTP 200 dès la date du jour. Un `User-Agent` générique de Python (`Python-urllib`) est refusé en **403** par le serveur (mesuré) : le client envoie un UA identifiable.

## 2. Mesures (boîte -12,37.5,16,55.4 ; build 20261017 ; zooms 0 à maxzoom)

Mesurées sur la machine de développement, une exécution par ligne, extraction réelle (aucune extrapolation, y compris à maxzoom 10).

| maxzoom | tuiles | contenus uniques | requêtes HTTP (réussies / reprises) | octets transférés | durée | poids final |
|---|---|---|---|---|---|---|
| 7 | 165 | 152 | 23 / 0 | 15,04 Mo | 7,3 s | **14,85 Mo** |
| 8 | 585 | 511 | 30 / 0 | 54,06 Mo | 7,2 s | **53,87 Mo** |
| 9 | 2 184 | 1 688 | 69 / 0 | 175,44 Mo | 13,9 s | **175,11 Mo** |
| 10 | 8 340 | 5 801 | 143 / 0 | 478,90 Mo | 25,1 s | **478,28 Mo** |

- Les requêtes comptent l'en-tête, les métadonnées et les répertoires (3 à 6 requêtes), plus 1 requête HEAD de sondage du build. Poids final = octets des tuiles uniques + répertoires + métadonnées ; le transfert dépasse le poids de ~0,1 à 0,3 Mo (répertoires-feuilles lus mais non conservés).
- Poids exact par zoom (maxzoom 10, avant dédoublonnage) : z8 39,0 Mo, z9 121,3 Mo (1 599 tuiles), z10 303,3 Mo (6 156 tuiles) ; les zooms 0-7 pèsent ensemble ~14,8 Mo. Chaque zoom supplémentaire multiplie le poids par ~2,7 à 3,3.
- Détail dans `out/basemap/mesures-extraction.json`.
- Mode `--a-blanc` : compte tuiles et octets exacts en ne lisant que les répertoires (~3 s, 0,15 à 0,5 Mo), utile pour décider avant de télécharger.

### Recommandation de maxzoom

**maxzoom 9** (175,1 Mo, livré : `out/basemap/domaine-z9.pmtiles`). Raisons : le z10 pèse 478 Mo (> 400 Mo, plafond fixé) ; le viewer s'arrête à 10,5, soit 1,5 niveau de surzoom de MapLibre au-delà de z9 ; le fond d'une carte météo sert de repère (côtes, frontières, grandes routes, villes) et la donnée météo la plus fine (0,01°, ~1 km) n'a pas plus de détail que ça. Les bâtiments n'existent qu'à partir de z11 dans ce schéma (vector_layers : `buildings` minzoom 11), donc aucun gain à z10 de ce côté ; le z10 ajoute surtout des routes secondaires et des points d'intérêt (jugement visuel, pas de mesure comparative). Capture de surzoom à 10,5 sur Toulouse : lisible, routes et noms présents (`fond-de-carte-toulouse-surzoom-clair.png`). Alternative légère pour la PWA hors ligne : **maxzoom 8, 53,9 Mo** (non évalué visuellement hors Alpes/France).

## 3. Vérification du résultat

`python3 -m pipeline.basemap.verifier out/basemap/domaine-z9.pmtiles --source https://build.protomaps.com/20261017.pmtiles --graine 20261017` : 15 contrôles, tous OK (résultat dans `out/basemap/mesures-extraction.json`, clé `verification_z9`). Les mêmes contrôles sont OK sur les extraits maxzoom 7, 8 et 10 (graines 7, 8, 10 ; 15/15 OK chacun) et sur l'extrait repris après interruption.

(a) En-tête : version 3 ; type de tuile MVT et compression gzip conservés ; zooms min/max de l'en-tête (0 et 9) = zooms réels ; ensemble des tuiles = exactement celles qui recoupent la boîte (2 184 présentes, 2 184 attendues) ; `addressed_tiles_count` 2 184, `tile_contents_count` 1 688 ; emprise de l'en-tête = (-12, 37.5, 16, 55.4) ; centre dans l'emprise, zoom 5 ; sections contiguës, taille de fichier cohérente ; racine 5 497 octets (< 16 257) ; `clustered` ; métadonnées de la source conservées à l'identique (attribution OSM, 9 couches `vector_layers`, `name`, `version` 4.15.2, `planetiler:*`), plus une clé ajoutée `runeride:extraction` (source, boîte, zooms, date).
(b) 50 tuiles tirées au hasard (graine 20261017, zooms 5 à 9) relues par la bibliothèque dans l'extrait et dans la source distante : **octets identiques**, 0 différence.
(c) Rendu réel : captures dans `docs/poc-j1/img/` (Chrome 155 sans interface, SwiftShader, 1280x800) :
- `fond-de-carte-france-sombre.png`, `fond-de-carte-france-clair.png` (z5) ;
- `fond-de-carte-alpes-sombre.png`, `fond-de-carte-alpes-clair.png` (z8,5) ;
- `fond-de-carte-toulouse-surzoom-clair.png` (z10,5, surzoom au-delà de z9).
Étiquettes en français (Londres, Genève, Turin, Brive-la-Gaillarde...). Requêtes sur le fichier : toutes en 206 (13 à z5 : 633 Ko ; 7 à z8,5 : 1,59 Mo ; 5 à z10,5 : 0,76 Mo). Bibliothèques JS depuis unpkg : ~302 Ko transférés.

**Un bogue détecté par le contrôle (b)** : une première version écrivait les tuiles avec des offsets relatifs à la section de données de la source au lieu d'absolus ; l'en-tête et les comptes étaient cohérents mais 50 tuiles sur 50 étaient fausses. Corrigé, tout a été remesuré (les chiffres ci-dessus sont ceux après correction).

## 4. Hors ligne et autonomie

Le style `basemaps.layers('protomaps', flavor, { lang: 'fr' })` demande des **polices** `…/fonts/{fontstack}/{range}.pbf` et des **sprites** `…/sprites/v4/{flavor}.json|png` (+ `@2x` sur écran haute densité).

**Piles de polices** (lues dans `basemaps.js` 5.7.2, empreinte SRI vérifiée) : `Noto Sans Regular` (libellés), `Noto Sans Medium` (variante « bold » du style), `Noto Sans Italic` (eaux, numéros d'adresse). `Noto Sans Devanagari Regular v1` existe dans le style mais n'est demandée que pour des objets de script devanagari (hors zone).

**Plages observées** dans Chrome (France entière, z5, qui montre aussi l'Europe, l'Afrique du Nord, l'Atlantique ; 12 requêtes vers protomaps.github.io, 580 894 octets transférés compressés, sprites compris) :
- Regular : 0-255, 256-511, 512-767, 768-1023, 1024-1279, 1536-1791, 11520-11775 ;
- Medium : 0-255, 256-511 ; Italic : 0-255 ;
- sprites : `light.json`, `light.png`, `dark.json`, `dark.png` (une seule variante par thème en pratique).
Vues Alpes (z8,5) et Toulouse (z10,5) : 5 requêtes seulement (Regular, Medium et Italic en 0-255, + `light.json` et `light.png`), ~161 Ko ; les plages 256-511 n'y sont pas demandées.

**Plages possibles** (borne haute, déduite des noms `name`, `name:fr`, `name:en`, `name2`, `name3` présents dans les tuiles de l'extrait, `out/basemap/glyphes-*.json`) : latin (0-255, 256-511, 512-767, 7680-7935), ponctuation (8192-8447 : tiret demi-cadratin, apostrophe typographique), grec, cyrillique, arabe, éthiopien, tifinagh, CJK, etc. Pour la boîte France métropolitaine, 24 plages sont théoriquement possibles à cause des noms natifs de lieux étrangers ; l'observation montre que le style n'en demande que 7 sur la vue Europe. Pour des libellés **français**, 0-255, 256-511 et 8192-8447 suffisent (œ, ç, é, ’, –).

**Poids mesurés** (fichiers réellement téléchargés une fois chacun ; « compressé » = gzip -9, proche du transfert observé par Chrome : 46 059 octets pour Regular 0-255 contre 45 950 estimés) :

| Lot | Fichiers | Brut | Compressé |
|---|---|---|---|
| A. France stricte : 3 piles x {0-255, 256-511, 8192-8447} | 9 | 805 565 o | 462 313 o |
| B. A + {512-767, 7680-7935} (latin étendu) | 15 | 1 507 804 o | 793 922 o |
| C. B + {768-1023, 1024-1279, 1536-1791, 11520-11775} (tout ce que la vue Europe a demandé, pour les 3 piles) | 27 | 2 512 176 o | 1 382 540 o |
| Sprites 1x : dark, light (json + png) | 4 | 39 326 o | ~35 000 o |
| Sprites 2x : dark, light (json + png) | 4 | 64 417 o | ~57 000 o |

Poids unitaire : Regular 0-255 76 044 o, 256-511 127 726 o, 8192-8447 64 220 o ; Medium 77 628 / 129 635 / 65 101 o ; Italic 79 344 / 132 976 / 52 891 o. (Italic 1536-1791 : 26 octets, plage vide.)

**Plan d'auto-hébergement proposé** (à valider avec le viewer, non réalisé) :
1. Copier depuis `https://protomaps.github.io/basemaps-assets/` : `fonts/Noto Sans {Regular,Medium,Italic}/{plage}.pbf` pour le lot C (27 fichiers), et `sprites/v4/{light,dark}.json|png` + `@2x` (8 fichiers). **Total : 35 fichiers, 2,68 Mo bruts (~1,5 Mo transférés en gzip)**. Le dépôt `basemaps-assets` n'est pas à cloner en entier.
2. Placer sous `viewer/assets/` (ou équivalent) en gardant l'arborescence ; style : `glyphs: './assets/fonts/{fontstack}/{range}.pbf'`, `sprite: './assets/sprites/v4/' + flavor` (URL absolue requise par MapLibre : la construire avec `new URL(..., location.href)`).
3. Hôte statique : activer la compression gzip pour `.pbf` et `.json` (l'amont les sert en gzip ; les `.png` sont déjà compressés) ; ne PAS recompresser le `.pmtiles`.
4. Service worker : précacher le lot A (0,46 Mo) + sprites 1x du thème (~20 Ko) ; mettre en cache à l'exécution (stale-while-revalidate) le reste du lot C ; une plage absente donne seulement une erreur de chargement MapLibre, les libellés restent affichés sans les caractères manquants.
5. Auto-héberger aussi maplibre-gl, pmtiles et @protomaps/basemaps (≈ 302 Ko transférés), en gardant les versions épinglées.
6. Fichier carte : `domaine-z9.pmtiles` (175 Mo) ne se précache pas en entier : le servir par Range ; pour un mode hors ligne réel, prévoir une extraction plus petite (zone d'intérêt) ou maxzoom 8 (54 Mo).
7. Licences des assets : polices Noto Sans (SIL OFL 1.1, à confirmer dans le dépôt `basemaps-assets`) et sprites : copier les fichiers de licence du dépôt d'origine ; non vérifié ici.

## 5. Licence et attribution

- Données © contributeurs d'OpenStreetMap, sous licence **ODbL**. Afficher « **Protomaps © OpenStreetMap contributors** » (avec liens) sur la carte : c'est le texte de l'attribution du style de la page de test (visible en bas à droite des captures, avec « MapLibre »). L'attribution inscrite dans le fichier (métadonnée source) est seulement `© OpenStreetMap` ; elle est conservée mais ne remplace pas celle du style.
- L'extrait est une base de données dérivée d'OSM : si le fichier est distribué ou publié, il faut conserver l'attribution, indiquer la licence ODbL et la mettre à disposition sous ODbL (partage à l'identique pour la base). Le build Protomaps mentionne aussi Natural Earth (domaine public, à confirmer). Ceci n'est pas un avis juridique.
- Le code de la brique est sous EUPL-1.2.

## 6. Écarts au cahier des charges

- Capture : `--screenshot --virtual-time-budget` seul (essayé avec 25 s, 60 s et 300 s) produit une image avec le seul fond uni, sans tuiles ni libellés : MapLibre travaille dans des workers et le temps virtuel ne les attend pas. Les captures sont donc faites avec **Chrome sans interface piloté par le protocole DevTools** (`demo/capture.mjs`, Node 24, aucune dépendance) qui attend l'évènement `idle` de MapLibre avant de capturer. Mêmes options Chrome (`--headless=new --no-sandbox --enable-unsafe-swiftshader`).
- Les mesures à maxzoom 10 sont réelles (extraction complète), pas extrapolées ; le fichier à 10 (478 Mo) n'est pas livré (plafond 400 Mo) et reste hors du dépôt.
- Les fichiers de mesure (`out/basemap/mesures-extraction.json`, `glyphes-*.json`) s'ajoutent à la sortie demandée.

## 7. Points ouverts

- Rendu non comparé visuellement entre maxzoom 9 et 10 (jugement et chiffres seulement).
- Mobile, Apache/o2switch, intégration au viewer, service worker : non testés.
- Rythme de mise à jour du fond : le build change chaque jour ; trimestriel semble suffisant (comme dans `runeride-carto`).
- Sprites 2x et lots de polices : poids mesurés, rendu à DPR 2 non capturé.
- L'extraction n'est pas conçue pour traverser l'antiméridien ni pour des boîtes mondiales.

## 8. Rejouer

```sh
export PYTHONPATH=<scratchpad>/pylib2:.            # bibliothèque pmtiles
python3 -m pipeline.basemap --bbox -12,37.5,16,55.4 --maxzoom 9 --out out/basemap/domaine-z9.pmtiles --mesures m.json
python3 -m pipeline.basemap --bbox -12,37.5,16,55.4 --maxzoom 10 --out /tmp/x.pmtiles --a-blanc   # compter sans télécharger
python3 -m pipeline.basemap.verifier out/basemap/domaine-z9.pmtiles --source https://build.protomaps.com/AAAAMMJJ.pmtiles
python3 -m pipeline.basemap.glyphes out/basemap/domaine-z9.pmtiles --bbox -5.5,41,10,51.5 --zoom-max 9
python3 -m pipeline.basemap.serveur                  # affiche l'URL (port libre)
node pipeline/basemap/demo/capture.mjs "http://127.0.0.1:PORT/?theme=dark&c=2.5,46.6&z=5" capture.png reseau.json
python3 -m unittest discover -s tests/basemap -t .   # 34 tests, sans réseau
```
