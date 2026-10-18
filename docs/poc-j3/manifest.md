<!-- SPDX-License-Identifier: EUPL-1.2 -->
# J3 : manifest et choix automatique du modèle

Brique « manifest » du jalon J3. Elle réalise D7 (choix du modèle par zoom, repli par couche) et D8 (publication par variable et par échéance, risque R11) de [../specifications.md](../specifications.md) §3.1, §4 et §5.3. Elle remplace l'`index.json` dynamique de la visualisation par un fichier décrit, produit par le pipeline.

| Élément | Fichier |
|---|---|
| Générateur | `pipeline/manifest/` (`build.py`, `domaine.py`, `__main__.py`) |
| Règles de sélection côté client (module pur, sans DOM) | `viewer/js/modele-auto.js` |
| Tests Python (14) | `tests/manifest/test_manifest.py` |
| Tests Node (29) | `viewer/tests/modele-auto.test.mjs`, `viewer/tests/aide.mjs` |
| Sortie | `out/manifest/manifest.json` |

```sh
export PYTHONPATH=<scratchpad>/pylib2          # paquet pmtiles (voir poc-j1-contrat.md §6)
python3 -m pipeline.manifest --tiles out/tiles-reduit2 --out out/manifest/manifest.json
python3 -m unittest discover -s tests/manifest -t .
node --test viewer/tests                       # Node 24 : le dossier est résolu par viewer/tests/package.json
```

Options : `--canon out/canon` (champs canoniques pour le polygone ; absent, repli sur le rectangle), `--tolerance 0.04` (simplification, degrés), `--generated` (date fixée, pour les tests). Durée sur les données réelles : 0,5 s (liste des fichiers + 1 lecture d'en-tête par modèle, tirage et variable ; aucune tuile décodée).

## 1. Schéma

Les clés suivent l'exemple de la spécification §5.3 (`generated`, `attribution`, `models`, `runs`, `complete`, `variables`) et ajoutent `version`, `couches`, `domain`, `valid_debut`. Exemple réel, abrégé (tirages d'AROME HD du 17/10/2026) :

```json
{
  "version": 1,
  "generated": "2026-10-18T11:28:52Z",
  "attribution": "Source : Météo-France, Licence Ouverte v2.0",
  "file_template": "{model}/{run_id}/{variable}/{step}.pmtiles",
  "models": {
    "arome-hd": {
      "label": "AROME HD", "resolution_deg": 0.01,
      "grid": [-12.0, 37.5, 16.0, 55.4],
      "domain": {
        "polygon": [[14.01, 46.96], [14.84, 50.4], [16.0, 54.48], "... 20 sommets ...", [14.01, 46.96]],
        "vertices": 20, "source": "canon/arome-hd/2026-10-17T06Z/03", "tolerance_deg": 0.04
      },
      "horizon_h": 51,
      "zooms": { "t2m": [5, 7], "wind10": [5, 7], "precip_rate": [5, 8], "snow_fraction": [5, 8], "...": "..." },
      "variables": ["cape", "cloud_total", "gust10", "precip_rate", "rh2m", "snow_fraction", "t2m", "wind10"],
      "runs": [
        { "run": "2026-10-17T03:00:00Z", "id": "2026-10-17T03Z", "path": "arome-hd/2026-10-17T03Z",
          "valid_debut": "2026-10-17T03:00:00Z", "valid_fin": "2026-10-19T06:00:00Z", "complete": true,
          "variables": { "t2m": { "steps": [0, 1, "... 51"], "complete": true },
                         "gust10": { "steps": [1, 2, "... 51"], "complete": true } } },
        { "run": "2026-10-17T06:00:00Z", "id": "2026-10-17T06Z", "path": "arome-hd/2026-10-17T06Z",
          "valid_debut": "2026-10-17T06:00:00Z", "valid_fin": "2026-10-18T00:00:00Z", "complete": false,
          "variables": { "t2m": { "steps": [0, 1, "... 18"], "complete": false },
                         "gust10": { "steps": [1, 2, "... 18"], "complete": false } } }
      ]
    },
    "arome": { "...": "même forme, resolution_deg 0.025, zooms [3, 7], un tirage 06Z complet" }
  },
  "variables": {
    "t2m": { "encoding": "scalar8", "scale": "linear", "unit": "K", "first_step": 0,
             "per_file_range": true, "min": 243.15, "max": 306.65, "quant_step": 0.25,
             "models": ["arome", "arome-hd"] },
    "wind10": { "encoding": "wind8", "components": ["wind10_u", "wind10_v"], "...": "..." }
  },
  "couches": {
    "vent": { "nom": "Vent", "variable": "wind10", "models": ["arome", "arome-hd"] },
    "t2m": { "nom": "Température", "variable": "t2m", "models": ["arome", "arome-hd"] }
  },
  "hors_raster": { "pression": { "variable": "mslp", "models": ["arome"], "rendu": "isobares vectorielles" } }
}
```

Champs :

- **`models.<m>.grid`** : rectangle de la grille `[ouest, sud, est, nord]`. **`domain.polygon`** : anneau fermé `[lon, lat]` du domaine réel, dérivé des données (§2). `domain.source` dit d'où il vient (`canon/...` ou `grille (rectangle, ...)` en repli).
- **`zooms`** : `[minzoom, maxzoom]` de chaque variable, lus dans le PMTiles du tirage le plus récent qui la contient. Le profil `reduit2` plafonne à z7 sauf `precip_rate` et `snow_fraction` (z8) : le client surzoome au-delà.
- **`runs[].run`** : instant nominal du tirage (ISO UTC). **`valid_debut` / `valid_fin`** : instants valides de la première et de la dernière échéance publiées (toutes variables confondues). **`variables.<v>.steps`** : liste des échéances (heures) effectivement présentes ; `complete` = la liste vaut `first_step .. horizon_h`. **`runs[].complete`** = toutes les variables du modèle sont complètes. Une variable absente d'un tirage n'a pas d'entrée (et rend le tirage incomplet).
- **Fichier d'une échéance** : `file_template` avec `model`, `run_id` (= `id`), `variable`, `step` sur deux chiffres, relatif à la racine des tuiles ; `runs[].path` = `<modèle>/<id>`. Fonction `cheminFichier` dans `modele-auto.js`.
- **`variables`** : encodage (`scalar8`, `wind8`), échelle (`linear`, `sqrt`), unité, `first_step` (1 pour `gust10`, `precip_rate`, `snow_fraction`, `cloud_total`, qui n'existent pas à l'échéance 0), `models` qui la fournissent. `min`, `max`, `quant_step` sont **indicatifs** (voir §5).
- **`couches`** : clés identiques à `viewer/js/config.js` (`vent`, `rafales`, `t2m`, `pluie`, `nuages`, `humidite`, `neige`, `cape`) → variable tuilée et modèles qui la fournissent. La pression n'est pas tuilée en raster : elle figure dans `hors_raster` (informatif, AROME seulement) et pas dans `couches`.
- **`attribution`** : mention légale à afficher (jamais à omettre).

## 2. Le polygone du domaine

Source : le champ canonique `t2m` de l'échéance 3 du tirage le plus récent présent dans `out/canon/<modèle>/` (le masque change sur 2 à 6 points seulement entre échéances, [poc-j1/synthese.md](../poc-j1/synthese.md)). Les tuiles ne sont pas utilisées : le masque de la grille est exact, alors que les tuiles à z5-z7 sont rééchantillonnées.

Méthode : le domaine n'est pas un trapèze strict. Ses bords nord et sud sont des arcs, et **le bord sud est concave** (le milieu du bord sud est à 38,0°N, les coins à 37,5°N). Une étendue « première et dernière colonne valides par ligne » déborde donc du domaine (mesuré : 1,5 % de la surface du polygone invalide, tout le long du bord sud). On lance à la place 2880 rayons depuis le barycentre des points valides, on garde sur chacun le point valide le plus éloigné (domaine étoilé), puis on simplifie par Douglas-Peucker.

Résultat mesuré avec une tolérance de 0,04° (1,6 maille AROME, 4 mailles HD) :

| Modèle | Sommets | Points valides couverts | Surface du polygone valide |
|---|---|---|---|
| AROME | 20 | 99,8 % | 99,99 % |
| AROME HD | 20 | 99,9 % | 99,9 % |

0,02° donne 157 sommets (AROME, bruit de maille) pour un gain nul ; 0,08° donne 13 à 15 sommets et 99,6 à 99,9 % : 0,04° est le compromis retenu. Le polygone est volontairement **fidèle** et non conservateur : l'écart (0,2 % de points valides hors polygone) est absorbé par la tolérance de vue (§3).

## 3. Règles de sélection (`viewer/js/modele-auto.js`)

Module ES sans dépendance ni accès au DOM, à l'horloge ou au réseau. Toutes les fonctions sont pures.

**Choix du modèle, `choisirModele({ zoom, bounds, couche, mode, manifest, precedent })`**

1. `mode` `arome` ou `arome-hd` : modèle imposé (raison `force`). Sinon (`auto`) :
2. HD si `zoom >= 7` **et** vue dans le domaine, sinon AROME. Hystérésis : si `precedent` est le HD, on y reste tant que `zoom >= 6,75` (constantes `ZOOM_HD = 7`, `HYSTERESIS = 0,25`). Sans `precedent`, seuil net à 7.
3. « Vue dans le domaine » : le rectangle de vue, **rétréci de la tolérance de chaque côté**, est contenu dans `domain.polygon` (test des coins, des arêtes et des sommets : un polygone concave est géré). Tolérance **0,05°** pour entrer en HD, **0,25°** pour y rester (`TOLERANCE_ENTREE`, `TOLERANCE_SORTIE`) : un léger dépassement au panoramique ne fait pas basculer. Sans emprise (`bounds` nul) ou sans domaine, on ne refuse pas.
4. Repli par couche : si le modèle voulu ne fournit pas la variable (au moins une échéance dans au moins un tirage), on prend l'autre (`repli: true`, raison `variable_absente_hd` ou `variable_absente_arome`), aussi en mode forcé. Aucun modèle ne la fournit : `modele: null`.

`precedent` accepte le nom du modèle ou le résultat précédent. Retour : `{ modele, raison, repli, dansDomaine, demande, variable }`. Raisons : `zoom`, `zoom_faible`, `hors_domaine`, `force`, `variable_absente_hd`, `variable_absente_arome`, `variable_absente`, `couche_inconnue`.

**Choix du tirage, `choisirTirage({ modele, variable, tValide, manifest })`**

- Pour chaque tirage du plus récent au plus ancien : échéance = `tValide − run` en heures ; le premier tirage dont **la variable existe à cette échéance** est retenu, qu'il soit complet ou non (D8). HD 06Z (0 à 18 h) sert donc jusqu'au 18/10 à 00:00 UTC, puis le HD 03Z (échéance 22 à 06:00 UTC le 18/10, etc.).
- Instant fractionnaire : l'échéance suivante vient du **même tirage** (jamais deux tirages dans une image, D8). On préfère le plus récent tirage qui possède les deux échéances voisines ; à défaut, le plus récent qui a l'échéance inférieure, avec `fraction = 0`.
- Retour : `{ modele, variable, run, id, path, complete, step, stepSuivant, fraction, tValide, plusRecent }`, ou `null`. `plusRecent: false` signale qu'un tirage plus récent existe sans cette échéance (à indiquer à l'utilisateur, D8).

**Temps.** La frise est en temps valide UTC ; `tValideDe(run, echeance)` et `echeanceDe(run, tValide)` convertissent. À `2026-10-17T09:00Z`, l'échéance est 6 pour HD 03Z et 3 pour AROME 06Z. `plageTemps(manifest, couche, { modele, depuis })` renvoie `{ debut, fin, trous, modeles }` (ms UTC) : union des instants servis par les modèles qui fournissent la couche ; `trous` liste les heures sans donnée entre `debut` et `fin`.

**Autres fonctions.** `attribution(modele, manifest)` : « Source : Météo-France, Licence Ouverte v2.0 · modèle AROME HD 0,01° ». `etatCouche({ couche, modele, tValide, manifest, bounds })` : `{ disponible, raison, detail, message, tirage, partiel, variable }`, avec les raisons `ok`, `couche_inconnue`, `variable_absente_modele`, `hors_domaine` (la vue ne touche pas du tout le polygone), `echeance_non_publiee` (détail `avant_premiere_echeance`, `apres_derniere_echeance` ou `trou`) ; `partiel` si la vue dépasse du domaine sans en être totalement sortie. `resoudre({ ..., tValide })` enchaîne les deux choix et, si le modèle choisi n'a pas l'échéance, essaie l'autre (`repliTemps: true`).

## 4. Cas limites testés

| Cas | Comportement vérifié |
|---|---|
| Zoom 6,99 / 7 / oscillation 6,9 à 7,05 | AROME / HD / au plus une bascule |
| Retour du HD vers AROME | à `zoom < 6,75` seulement, si le HD était affiché |
| Vue dans le rectangle de la grille mais hors trapèze (coin sud-ouest, sud-est) | AROME, raison `hors_domaine` |
| Dépassement de 0,1° du bord | AROME à l'entrée, HD conservé si on y était |
| Polygone concave (test en U) | un rectangle dont les coins sont dedans mais qui traverse un creux est refusé |
| Pression (AROME seulement) | zoom 9 ou mode `arome-hd` : AROME, `repli`, sans erreur ; `etatCouche` HD : `variable_absente_modele` |
| HD 06Z limité à 0-18 h | HD 06Z jusqu'à 06Z + 18 h, HD 03Z au-delà, en entier comme en instant fractionnaire |
| HD au-delà de 03Z + 51 h (19/10 06:00 UTC) | repli sur AROME 06Z (jusqu'à 19/10 09:00 UTC), `repliTemps` |
| Tirages de 3 h d'écart | mêmes instants valides, échéances 6 (HD 03Z) et 3 (AROME 06Z) |
| Rafales, précipitations, nuages, neige | pas d'échéance 0 : première heure de frise décalée d'une heure |
| Début et fin de frise | chaque heure de `debut` à `fin` est servie à zoom 4 et 8 ; une heure après `fin` : `apres_derniere_echeance` |
| Trou de publication | listé dans `plageTemps.trous`, `etatCouche` détail `trou` |
| HD sans aucun tirage ; couche inconnue | AROME ; `modele: null`, raison `couche_inconnue` |
| Vrai manifest | même scénarios sur `out/manifest/manifest.json` (ignorés si absent) |

## 5. Ce que l'intégrateur doit appeler, et quand

1. **Au démarrage**, puis périodiquement (le manifest est à cache court, `ETag`, spec §5.3) : charger `manifest.json`. Si le manifest change, recalculer ce qui suit.
2. **Construction de la frise** : `plageTemps(manifest, couche)` (début, fin, trous). À rappeler au changement de couche et à chaque nouveau manifest.
3. **À chaque `moveend`/`zoomend` de la carte** (pas à chaque image) et au changement de couche ou de mode : `choisirModele({ zoom: map.getZoom(), bounds: map.getBounds(), couche, mode, manifest, precedent: modeleAffiché })`. Conserver le résultat comme `precedent` (c'est lui qui active l'hystérésis). Afficher `attribution(modele, manifest)` et, si `repli`, mentionner le modèle sans message d'erreur (D7).
4. **À chaque changement du temps valide de la frise** (jusqu'à 60 fois par seconde en lecture, la fonction est légère) : `choisirTirage` pour le modèle retenu, puis `cheminFichier(manifest, modele, tirage.id, variable, tirage.step)` pour l'échéance A, et `tirage.stepSuivant` pour B avec le poids `tirage.fraction`. Ou `resoudre(...)` qui fait modèle et tirage ensemble et gère aussi le repli faute d'échéance.
5. **Pour un message ou une icône « indisponible »** : `etatCouche(...)`.
6. À chaque changement de fichier, **relire `min`, `quant_step`, `encoding` dans les métadonnées PMTiles** : le manifest ne les fige pas (§6).

La carte devra recharger la couche quand `modele` ou `tirage.id` change, et ne mélanger les modèles que par un fondu court (la maille change). Le zoom et les `zooms` du manifest donnent `zmin`/`zmax` de la couche sans ouvrir de fichier.

## 6. Ce qui reste ouvert

- **D14 : plage propre à chaque fichier.** Le manifest n'impose aucune plage : `variables.<v>.min`, `max`, `quant_step` sont ceux d'**un fichier lu** (`per_file_range: true`) et ne servent qu'à la légende initiale ou aux tests. Le client décode avec les métadonnées de chaque PMTiles (`min + (code − 1) × quant_step`). Quand D14 sera réalisé (plage de `t2m` et `gust10` calculée par échéance), ces trois champs pourraient varier d'une échéance à l'autre sans changer le schéma. À valider sur un tirage d'été.
- **Attente du client sur `plusRecent: false`** : l'indication à l'utilisateur d'un tirage de repli (« données du tirage 03 h ») n'est pas spécifiée dans l'interface.
- **Pression et isobares** : aucune entrée raster. Quand les isobares vectorielles existeront, une entrée `couches.pression` de rendu vectoriel (format de tuiles à définir) remplacera `hors_raster`.
- **Rétention (D9)** : le générateur décrit ce qui est sur disque ; la purge des 3 derniers tirages et la publication atomique sur S3 (le manifest doit être écrit **après** les tuiles, `ETag`, cache court) relèvent du déploiement, pas de cette brique.
- **Zoom du HD en dessous de 5** : le PMTiles HD commence à z5 (`zooms`). La règle D7 ne bascule en HD qu'à partir de 7, donc sans conflit, mais en mode `arome-hd` forcé à zoom < 5 la couche est vide ; le sélecteur devrait le signaler.
- **Domaine commun** : les deux modèles ont aujourd'hui le même domaine (mesuré à 0,01° près). Le polygone est stocké par modèle pour le jour où ils différeront (ajout de modèles, D10), mais `choisirModele` teste le domaine du HD pour décider du HD.
- **Polygone dérivé d'une seule échéance** : si le masque change nettement entre tirages (changement de domaine côté Météo-France), le générateur le suit au prochain passage, mais un test d'alerte sur l'écart de sommets n'existe pas.
- **Dateline / longitudes > 180** : sans objet pour ce domaine (−12 à 16°E), non géré.
- **Chargement sous Node** : `modele-auto.js` est un ES module sans dépendance ; Node 24 détecte la syntaxe ESM sans avertissement observé. Un `viewer/package.json` avec `"type": "module"` le rendrait explicite (hors de cette brique).
