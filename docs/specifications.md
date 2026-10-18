# RuneRide Météo : spécifications (`weather.runeride.fr`)

Statut : **spécification v0.2, rien n'est développé ni déployé.** Décisions prises le 17 octobre 2026 : **D1 validée** (calcul et données sur AWS), **service public**, **v1 élargie** (carte, fiche de point, météo d'itinéraire, pluie très court terme). Les mesures sur les données sont dans [donnees-arome.md](donnees-arome.md) ; ce document en reprend les conclusions. Vocabulaire : **mesuré** (vérifié sur les vrais fichiers), **ESTIMATION** (calcul à confirmer), **DÉCISION** (à valider), **OUVERT** (question à trancher).

## 1. Vision

Une météo de motard : **les prévisions haute résolution de Météo-France, affichées aussi confortablement que windy.com**, et reliées à la route. On regarde un vent, une pluie, une température sur la carte, on fait glisser le temps, on touche un point pour voir sa courbe sur 51 h, et on voit ce qui attend sur son itinéraire à l'heure où on y sera.

Ce qui nous distingue d'un clone : (1) AROME est le meilleur modèle disponible gratuitement sur la France (maille de 1,3 km en HD) ; (2) la météo **le long d'un trajet**, avec vent de face et de travers, rafales et froid ressenti à la vitesse de roulage.

Référence d'ergonomie : windy.com (carte plein écran, particules de vent animées, chronologie, sélecteur de couches, info au point). On vise ce confort, pas une copie : pas de code ni de ressources de Windy.

## 2. Périmètre

**Inclus (v1)** : France métropolitaine et alentours (domaine AROME : 37,5–55,4°N, 12°O–16°E), prévisions de 0 à 51 h, couches vent, rafales, température, précipitations, nuages, pression, orages ; modèles AROME (0,025°) et AROME HD (0,01°) ; fiche de point (météogramme) ; météo le long d'un itinéraire ; **pluie très court terme (AROME-PI, voir réserve ci-dessous)** ; interface installable (PWA), utilisable au guidon ; **service public**, sans compte.

**AROME-PI (J1b, résolu le 17/10/2026)** : servi par l'**API de Météo-France** (OGC WCS/WMS), pas par le stockage S3 ouvert ; **Licence Ouverte v2.0**, **compte gratuit et jeton OAuth2 requis**, 100 requêtes/min ; 0,01° et 0,025° ; tirage horaire, 0 à 6 h par pas de 15 min (wiki). Il faut donc que le titulaire du projet crée le compte et fournisse un jeton (stocké dans AWS Secrets Manager). **PIAF** (5 min) est écarté de la v1 : environnement « APIS commerciales » et licence non déclarée. Détails : [donnees-arome.md §11](donnees-arome.md).

**Exclus (v1)** : radar et satellite (non fournis par AROME ; pistes en §11), vagues, qualité de l'air, visibilité (paramètre non identifié), alertes. **Aucune alerte de sécurité n'est émise par RuneRide** : on renvoie vers la Vigilance Météo-France. Hors domaine AROME, rien n'est affiché (voir §11 pour une prévision de repli).

**Pistes ultérieures** : PIAF (pluie à 5 min) ; AROME ensemble pour des probabilités ; **extension européenne** par assemblage de modèles régionaux, avec **ARPEGE 0,1°** (Météo-France, 20–72°N, 32°O–42°E, 0 à 102 h) comme couche de base et **ICON-D2** (DWD, 2,2 km, Allemagne et alentours) en premier relais : voir [couverture-europe.md](couverture-europe.md), qui corrige aussi la liste de modèles reçue.

## 3. Données

### 3.1 Modèles et sélection

| Modèle | Maille | Domaine | Tirages | Échéances |
|---|---|---|---|---|
| AROME | 0,025° (~2,8 × 1,9 km) | 37,5–55,4°N, 12°O–16°E | 8 par jour (00, 03 … 21 UTC) | 0 à 51 h, pas 1 h |
| AROME HD | 0,01° (~1,1 × 0,8 km) | même domaine | 8 par jour | 0 à 51 h, pas 1 h |

Les deux sont sous **Licence Ouverte v2.0**, accessibles sans clé sur un S3 public (détails et mesures : [donnees-arome.md](donnees-arome.md)). Délai de publication **mesuré** : le dernier fichier d'un tirage arrive **2,6 à 5,3 h après l'heure nominale**, étalé sur ~1 h.

**DÉCISION D7 : choix automatique du modèle.** HD quand le zoom est ≥ 7 et que la vue est dans le domaine ; AROME en dessous. Un sélecteur permet de forcer l'un ou l'autre. Si la couche demandée n'existe pas en HD (vent au-dessus de 100 m, pression au niveau de la mer, point de rosée), on bascule sur AROME pour cette couche, sans message d'erreur, avec la mention du modèle affichée.

### 3.2 Paquets à ingérer (MVP)

On n'ingère **que ce qui est affiché** : lire tout représente ~250 Go/jour (ESTIMATION).

| Modèle | Paquets | Volume par tirage (mesuré) | Usage |
|---|---|---|---|
| AROME HD | SP1, SP2, HP1 | 1,22 + 0,98 + 3,87 = **6,1 Go** | T 2 m, humidité, vent 10 m, rafales, nuages, précipitations, CAPE, vent à 20/50/100 m |
| AROME | SP1, SP2 | 0,48 + 0,37 = **0,85 Go** | idem + pression, point de rosée |
| AROME | HP1 (option, jalon J3) | 6,13 Go | vent en altitude jusqu'à 3000 m |

SP3 (flux de surface) et IP1–IP5 (niveaux de pression) sont écartés de la v1. HP1 en 0,01° pèse 3,9 Go pour peu de valeur (4 hauteurs) : il peut être écarté et le vent 20–100 m servi par AROME seulement (OUVERT).

### 3.3 Variables d'affichage et dérivations

| Couche | Calcul | Modèles | À valider |
|---|---|---|---|
| Vent 10 m | `u, v` → vitesse `√(u²+v²)`, direction, particules | AROME, HD | repère des composantes (est/nord attendu) |
| Rafales | `√(max_10efg² + max_10nfg²)` | AROME, HD | fenêtre exacte du maximum ; absent à l'échéance 0 |
| Température 2 m | `2t` − 273,15 | AROME, HD | — |
| Précipitations (mm/h) | différence de cumul : `Δ(tirf + tsnowp + tgrp)` ; en AROME on peut recouper avec `tp` | AROME, HD | cohérence entre `tp` et la somme ; cumul au pas de 1 h |
| Type de précipitation | part de neige = `Δtsnowp / Δ total` | AROME, HD | seuils pluie / neige / grésil |
| Nuages | bas, moyens, hauts ; total = 1 − (1−bas)(1−moyen)(1−haut) (recouvrement aléatoire, **hypothèse**) | AROME, HD | formule |
| Pression et isobares | `prmsl` | **AROME seulement** | pas de pmer en HD |
| Humidité, point de rosée | `2r` ; point de rosée `2d` (AROME) ou calculé (HD) | AROME, HD | formule de Magnus |
| Orages | `CAPE_INS` | AROME, HD | seuils d'échelle |
| Vent en altitude | HP1 : 20 m à 3000 m | AROME (HD : ≤ 100 m) | niveaux proposés |
| Froid ressenti à moto | refroidissement éolien à la vitesse de roulage choisie (défaut 90 km/h) | dérivé de T et vent | formule et plage de validité à documenter |

Traitements obligatoires : masquer `9999` (17 % des points, coins hors modèle), dé-cumuler les cumuls, convertir les unités, vérifier la grille et la date du fichier.

## 4. Architecture

```
Météo-France (S3 public, GRIB2, 8 tirages/jour)
        │  (1) détection + téléchargement progressif
        ▼
 Pipeline de traitement  ── AWS eu-north-1 (comme RuneRide Cloud) ──
   décodage eccodes · masque 9999 · dé-cumul · dérivés · quantification · tuilage PMTiles · séries ponctuelles
        │  (2) publication progressive, échéance par échéance
        ▼
 S3 « weather-data »  ──►  CloudFront  ──►  weather-data.runeride.fr  (tuiles de données, manifest)
                                                  │
 weather.runeride.fr (site statique, o2switch) ◄──┘  navigateur : MapLibre + WebGL (couleurs, particules, isobares)
        │
        └──►  API point / itinéraire (petite fonction sans état)   ──  lit les séries ponctuelles sur S3
```

**DÉCISION D1 : le calcul et les données sont sur AWS, l'interface est statique.**
- Pourquoi : le décodage de plusieurs Go de GRIB toutes les 3 h ne peut pas tourner sur un mutualisé (règle déjà posée pour la carto : « prétraitement jamais sur l'hébergeur »). Les tuiles de données représentent des centaines de fichiers par tirage : le quota d'inodes d'o2switch est inconnu, et le trafic Range est limité par un usage « raisonnable » (voir `runeride-carto/docs/tuiles.md`).
- L'interface (HTML, JS, CSS) reste sur o2switch comme `carto` et la vitrine, avec la même chaîne de déploiement FTPS.
- Repli : un PoC peut servir un sous-ensemble statique depuis o2switch ; à abandonner dès que le volume ou le trafic le justifie.

**DÉCISION D2 : tuiles de données en PMTiles, un fichier par (modèle, tirage, variable, échéance).** Même technologie que le fond de carte, lecture par requêtes `Range`, aucun processus persistant. Alternatives écartées : millions de petits fichiers XYZ (inodes, listage) ; Zarr ou COG (outillage navigateur moins mûr) ; une seule archive par variable avec toutes les échéances (cache et publication progressive moins commodes).

**DÉCISION D8 : publication progressive, par variable et par échéance.** Une échéance d'une variable est publiée dès que ses fichiers sont traités ; un `manifest.json` par tirage liste l'état de **chaque variable à chaque échéance**. C'est nécessaire parce qu'un tirage peut être publié **incomplet paquet par paquet** : le 17/10 à 13:17 UTC, le tirage HD de 06 h n'avait que 19 échéances de SP1 (température, vent, rafales) alors que ses autres paquets étaient complets, et celui de 09 h n'avait aucune échéance de SP1 ([donnees-arome.md §3](donnees-arome.md)). Pour chaque variable, le client prend le **dernier tirage où elle existe**, et ne mélange jamais deux tirages dans une même image de la frise sans l'indiquer. Le client affiche le **dernier tirage dont l'échéance demandée existe**, et se rabat sur le tirage précédent pour les échéances manquantes : l'utilisateur n'attend pas la fin d'un tirage de 2,6 à 5,3 h après son heure nominale.

**DÉCISION D9 : rétention.** Les 3 derniers tirages complets sont gardés (≈ 9 h de recul), le reste est purgé par règle de cycle de vie S3. Permet « comparer avec le tirage précédent » plus tard.

**Domaines** : `weather.runeride.fr` (interface), `weather-data.runeride.fr` (tuiles, manifest, API). OUVERT : un seul nom ou deux. Certificat CloudFront via ACM ; enregistrement DNS dans la zone cPanel.

## 5. Format de diffusion

### 5.1 Tuiles de données

- Pyramide Web Mercator, tuiles de **256 px**, couvrant le domaine. Zooms de données : AROME **z3–z7**, AROME HD **z5–z8** ; au-delà, le client surzoome et interpole (maille native HD ≈ z7–z8, voir [donnees-arome.md §9](donnees-arome.md)). La pyramide HD complète z3–z8 compte **580 tuiles** par variable et échéance.
- **Bordure de 1 px recouvrante** entre tuiles pour éviter les coutures de l'interpolation bilinéaire (DÉCISION à valider au PoC).
- Charge utile : **WebP sans perte** (ou PNG), valeurs quantifiées en canaux de couleur, **sans canal alpha de données** (la pré-multiplication du navigateur corromprait les valeurs) ; décodage avec `premultiplyAlpha: 'none'`.
- **Code 0 réservé = « pas de donnée »** (points masqués) ; la plage utile est codée de 1 à 255 (8 bits) ou 1 à 65 535 (16 bits).
- Mesuré : température 8 bits en pleine grille HD = 0,62 Mo en WebP sans perte ; vent u/v sur 4 canaux 16 bits = 8,4 Mo en PNG avant masquage, ce qui est trop lourd : d'où le codage 24 bits (RVB) du §5.2, à re-mesurer. **Mesuré au PoC J1 sur des tirages complets de 52 échéances** (WebP sans perte) : profil `contrat` **2,15 Go en HD + 0,72 Go en AROME = 2,87 Go par tirage** ; profil `reduit` (vent 8 bits, pas de quantification plus gros, zoom maximal 7 sauf pluie et neige) **0,51 + 0,41 = 0,92 Go par tirage** (−68 %) ; soit pour 3 tirages conservés **≈ 8,6 Go** ou **≈ 2,8 Go**. Le vent pèse 46 % en `contrat`. **DÉCISION D13 tranchée (17/10)** : adopter le profil **`reduit2`** (`reduit` avec `t2m` et `gust10` au pas de 0,25), **0,99 Go par tirage** mesuré (HD 0,545 + AROME 0,444), soit 65 % de moins que `contrat`. Détails : [poc-j1/synthese.md](poc-j1/synthese.md).

### 5.2 Quantification

| Variable | Codage | Plage | Pas résultant |
|---|---|---|---|
| Vent u, v | **12 bits chacun**, packés dans R, G, B (24 bits, aucun alpha) | ±64 m/s | 0,031 m/s |
| Température 2 m | 8 bits | −40 à +60 °C | 0,39 K (erreur max 0,2 K, mesuré) |
| Rafales | 8 bits | 0 à 64 m/s | 0,25 m/s |
| Précipitations | 8 bits, échelle racine carrée | 0 à 100 mm/h | fin aux faibles intensités |
| Nuages, humidité | 8 bits | 0 à 100 % | 0,4 % |
| CAPE | 8 bits, échelle racine carrée | 0 à 5000 J/kg | — |
| Pression (isobares) | 16 bits | 870 à 1100 hPa | 0,004 hPa |

Les isobares et isothermes ne sont pas dessinées par le client à partir de données quantifiées : elles sont **pré-calculées en tuiles vectorielles** (contours) par le pipeline.

### 5.3 Manifest

`/v1/manifest.json` (court cache, `ETag`) :

```json
{
  "generated": "2026-10-17T10:12:00Z",
  "attribution": "Source : Météo-France, Licence Ouverte v2.0",
  "models": {
    "arome-hd": { "grid": [-12, 37.5, 16, 55.4], "runs": [
      { "run": "2026-10-17T06:00:00Z", "complete": false, "steps": { "0": "ready", "1": "ready", "2": "pending" } }
    ]},
    "arome": { "runs": [] }
  },
  "variables": { "wind10": { "encoding": "u16x2", "range": [-64, 64], "unit": "m/s", "models": ["arome", "arome-hd"] } }
}
```

Fichiers : `/v1/{modele}/{tirage}/{variable}/{echeance}.pmtiles`. Le client ne devine rien : il lit le manifest.

## 6. Interface

### 6.1 Principes

1. **La carte d'abord** : plein écran, tout le reste flotte par-dessus et se replie.
2. **Le temps est un geste** : glisser sur la frise, un appui lance l'animation.
3. **Une couche à la fois, lisible d'un coup d'œil**, avec légende et valeur au point.
4. **Fluide avant tout** : l'animation ne saccade pas, le changement de couche est immédiat quand les tuiles sont en cache.
5. **Utilisable avec des gants et en plein soleil** (charte RuneRide : cibles ≥ 48 px, 56 px pour les actions principales, thème sombre par défaut, contraste fort).

### 6.2 Disposition

```
┌──────────────────────────────────────────────────────────┐
│ [≡] [ Rechercher un lieu             ] [Ma position] [AROME HD ▾] │ barre haute, repliable
│                                                          │
│ Vent ●                                       ┌────────┐ │
│ Rafales                                      │ 14 m/s │ │  valeur au point (toucher / survol)
│ Température                                  │ 52 km/h│ │
│ Pluie                                        └────────┘ │
│ Nuages                                                   │
│ Pression            C A R T E   +  particules            │
│ Orages                                                   │
│                                          légende ▓▓▓▓▓  │
│                                          0  10  20  30  │
│ ┌──────────────────────────────────────────────────────┐ │
│ │ ▶  ven. 17 · 14 h        ─────●──────────────────    │ │  frise du temps
│ │    aujourd'hui | demain | après-demain               │ │
│ └──────────────────────────────────────────────────────┘ │
│ Source : Météo-France · tirage 06 h UTC · mis à jour 09:47│
└──────────────────────────────────────────────────────────┘
```

Fiche de point : un appui prolongé ouvre un volet bas avec la **météogramme** sur 51 h (température, pluie, vent et rafales, nuages, pression) et un curseur lié à la frise.

### 6.3 Interactions

| Geste / contrôle | Effet |
|---|---|
| Glisser la frise | change l'heure ; la carte suit en temps réel (interpolation entre deux échéances) |
| ▶ / espace | animation ; vitesses ×1, ×2, ×4 ; boucle sur la plage affichée |
| Appui / survol sur la carte | valeur de la couche active au point, avec direction du vent |
| Appui long | ouvre la météogramme du point et pose un repère |
| ← / → | ±1 h ; Maj + ← / → : ±6 h ; Début / Fin : première / dernière échéance |
| Sélecteur de couches | une couche principale ; le **vent en particules** peut se superposer à toute couche |
| Sélecteur de modèle | Auto (D7), AROME, AROME HD |
| Réglages | unités, fuseau, thème, palette, particules |
| Recherche de lieu | géocodage (OUVERT : service à choisir) ; « ma position » demande l'autorisation du navigateur |
| URL | l'état est dans l'adresse : `#couche=vent&t=2026-10-17T14&c=44.1,2.3&z=8` ; un lien partage exactement la vue |

### 6.4 Couches

| Couche | Rendu | Légende / palette | Particules |
|---|---|---|---|
| Vent | flux animé (particules) + couleur par vitesse | échelle en km/h, nœuds, m/s ou Beaufort | oui (couche propre) |
| Rafales | couleur par vitesse | idem vent | option |
| Température | dégradé continu, isothermes pré-calculées | −20 à +45 °C, palette scientifique | option |
| Précipitations | intensités mm/h, teintes différentes pluie / neige | 0,1 à 50 mm/h | option |
| Nuages | gris translucide, nébulosité totale | 0 à 100 % | non |
| Pression | isobares (vecteur) avec valeurs | pas de 2 hPa, repères H / B | option |
| Orages | CAPE | 0 à 3000 J/kg | non |

**Les palettes sont scientifiques, pas aux couleurs de la marque** : elles doivent rester lisibles et comparables ; le thème RuneRide (ochre) sert aux contrôles, à la frise et aux repères. Une option « palette daltonisme » est prévue.

### 6.5 Comportement attendu, à vérifier au PoC

- **Interpolation temporelle** côté client (shader) entre deux échéances pour que l'animation soit continue malgré le pas d'1 h (DÉCISION D3).
- **Préchargement** : l'échéance courante ±3 h, puis le reste en tâche de fond, avec un cache mémoire borné.
- **Particules** : nombre adaptatif (de 8 000 à 65 000 selon l'appareil), traînée sur toile persistante, désactivées si `prefers-reduced-motion` (remplacées par des flèches / barbules).
- **Objectifs de performance (à valider sur deux appareils de référence, un téléphone milieu de gamme et un portable)** : première couche visible en moins de 2,5 s en 4G (ESTIMATION), 1,5 Mo ou moins de données à l'ouverture sur la vue France, 30 images/s minimum en animation, défilement de la frise sans à-coup (réponse en moins de 100 ms pour une échéance en cache).
- **Hors-ligne** : la PWA garde l'interface, la dernière vue et les tuiles déjà consultées ; un message clair indique l'âge des données affichées.

### 6.6 Accessibilité et internationalisation

- Contraste suffisant, couleurs jamais seules porteuses de l'information (valeurs au point, légende chiffrée).
- Commandes au clavier (frise, couches, recherche), annonce des valeurs (`aria-live`), cibles tactiles ≥ 48 px.
- Interface en **français et en anglais** ; unités et fuseau configurables ; l'heure locale est affichée par défaut, avec l'heure UTC en infobulle ; jamais d'ambiguïté de changement d'heure.

### 6.7 Identité et lexique

- Thème sombre par défaut, tokens de la charte (`identity/tokens/tokens.css`), polices Inter et JetBrains Mono (chiffres tabulaires pour les valeurs).
- Règle **80 % utilité / 20 % lore** : libellés explicites (« Vent », « Précipitations »). Proposition de nom de module dans l'application : **« Les Augures »**, sous-titre « Météo et prévisions » (PROPOSITION à ajouter au dictionnaire de `CODEX.md` si validée). Le « Brouillard Aveugle » du lexique existant reste réservé au mode hors-ligne et aux alertes de zone sans réseau.

## 7. API (v1, interne dans un premier temps)

Même philosophie que l'API carto : petites routes JSON, CORS strict par origines listées, limitation de débit, aucune donnée personnelle conservée.

| Route | Rôle |
|---|---|
| `GET /v1/manifest.json` | état des tirages et échéances (statique, cache court) |
| `GET /v1/point?lat=&lon=&model=auto&vars=` | série de 0 à 51 h au point, toutes variables ; `model=auto` applique D7 |
| `POST /v1/route` | corps : trace (polyligne), heure de départ, vitesse moyenne ; réponse : par point, heure estimée de passage, T, pluie, vent (de face / de travers), rafales, froid ressenti ; **sans stockage** |
| `GET /v1/sante` | état du pipeline et fraîcheur du dernier tirage |

Unités SI en sortie (K, m/s, mm/h, hPa) avec l'unité dans la réponse ; le client convertit. **DÉCISION D6 (OUVERT)** : séries ponctuelles servies par une fonction sans état lisant des blocs de séries préparés par le pipeline (recommandé, car la lecture de 52 fichiers de tuiles pour une seule météogramme serait trop lourde), ou lues directement par le navigateur en `Range` sans serveur ; à trancher au PoC J4 sur la latence mesurée.

## 8. Exigences non fonctionnelles

| Exigence | Cible | Statut |
|---|---|---|
| Fraîcheur | une échéance est publiée **dans les 15 min** suivant la disponibilité de ses fichiers chez Météo-France | DÉCISION, à mesurer |
| Délai affiché | « tirage de 06 h UTC, mis à jour à 09:47 » toujours visible | DÉCISION |
| Disponibilité | interface statique ; en cas d'échec d'un tirage, le précédent reste servi | DÉCISION |
| Reprise sur erreur | téléchargements repris, somme de contrôle de taille, nouvel essai, alerte si aucun nouveau tirage depuis 6 h | DÉCISION |
| Coût | ordre de grandeur de quelques euros à quelques dizaines d'euros par mois, dominé par la diffusion ; à chiffrer au jalon J5 | ESTIMATION |
| Stockage | **≈ 3,0 Go** (profil `reduit2`, retenu) ou ≈ 8,6 Go (profil `contrat`) pour 3 tirages (§5.1) | MESURÉ sur des tirages complets |
| Calcul | **≈ 41 min de CPU par tirage** (HD + AROME, 52 échéances, `reduit2`, isobares incluses) : ingestion HD 8,0 s par échéance (0,89 Go de mémoire), AROME ≈ 1 s ; tuilage HD 21 s (0,23 Go par processus), AROME 16 s (0,09 Go). **≈ 11 min sur 4 vCPU**, ≈ 21 min sur 2, ≈ 41 min sur 1, ≈ 6 min sur 8 (passage à l'échelle quasi linéaire jusqu'à 8 processus, gaspillage au-delà). 8 tirages par jour ≈ 5,5 h de CPU par jour. Machine recommandée : 4 vCPU, 8 Go, 20 Go de disque, à la demande. Détails : [poc-j1/synthese.md](poc-j1/synthese.md) | MESURÉ par étape sur une machine de 20 cœurs, total calculé |
| Vie privée | position et trace ne servent qu'à répondre ; aucun traceur, aucune mesure d'audience tierce ; journaux sans coordonnées précises | DÉCISION |
| **Service public : maîtrise du coût** | budget AWS avec alarmes et plafond, cache CDN long sur les tuiles (immuables par tirage), diffusion en `Range`, limitation de débit sur l'API point / itinéraire, quota de requêtes par IP | DÉCISION |
| **Service public : abus** | pas de compte, donc limitation par IP ; l'API itinéraire est plus coûteuse que la carte (taille de trace plafonnée) ; interdiction du moissonnage massif dans les conditions d'utilisation | DÉCISION |
| **Service public : conditions** | conditions d'utilisation et mentions légales sur le site (éditeur en nom propre, voir le site vitrine) ; l'avertissement du §9 reste visible | DÉCISION |

## 9. Légal et responsabilité

- **Attribution** obligatoire dans l'interface et dans les réponses d'API : « Source : Météo-France » avec la licence et la date de mise à jour. Intitulé exact à reprendre de la Licence Ouverte v2.0.
- **Avertissement** : prévisions numériques, non garanties, ne remplaçant pas la Vigilance Météo-France ; lien visible. Rien dans l'interface ne doit laisser croire à une alerte de sécurité.
- **Données personnelles** : géolocalisation sur demande explicite ; traitements décrits dans la politique de confidentialité du site vitrine.
- **Code et contenus** : comme les autres dépôts (EUPL-1.2 pour le code, CC BY-SA 4.0 pour les contenus), notice d'usage de l'IA dans le dépôt. Les données AROME restent sous leur licence d'origine ; les tuiles dérivées en sont des œuvres dérivées à attribuer de la même façon. OUVERT : vérifier que republier des tuiles dérivées est couvert par la licence (le texte l'autorise a priori, à confirmer).
- **Bibliothèques** : tout code repris (par exemple un moteur de particules WebGL du type `mapbox/webgl-wind`, licence ISC à vérifier) est cité dans la notice.

## 10. Jalons et critères d'acceptation

| Jalon | Contenu | Critère d'acceptation |
|---|---|---|
| **J0** | Valider ce document et les décisions D1 à D9 | décisions tranchées, noms de domaines figés |
| **J1b** AROME-PI | **fait** : source = API Météo-France (WCS), licence ouverte, compte gratuit et jeton requis ; reste à créer le compte et à inventorier les paramètres avec un jeton | compte créé par le titulaire, jeton dans Secrets Manager, `GetCapabilities` lu, paramètres et volumes reportés dans `donnees-arome.md` |
| **J1** PoC données (**fait le 17/10** : 20 points contrôlés à moins d'un demi-pas ; risques sur les données traités à 0-51 h, raccord de groupes, neige, convention du vent : voir [poc-j1/synthese.md](poc-j1/synthese.md)) | un tirage AROME + HD : téléchargement, décodage, masque, dé-cumul, tuilage de vent, température, pluie | valeurs identiques à eccodes en 20 points de contrôle (écart ≤ pas de quantification) ; poids et temps mesurés et reportés ici |
| **J2** PoC visualisation (**prototype fait**, mesures sur appareil réel à faire) | MapLibre + couche couleur + frise interpolée sur un appareil réel | 30 images/s en animation, première couche < 2,5 s en 4G ; mesuré en WebGL logiciel : première couche 0,9 à 1,1 s, 0,91 Mo à l'ouverture sur mobile (3,41 Mo sur grand écran) |
| **J3** Vent et modèles | particules, rafales, bascule AROME / HD, isobares vectorielles, vent en altitude (option) | bascule invisible, pas de couture entre tuiles, particules adaptatives |
| **J4** Point | météogramme, séries, API `/v1/point` | réponse en moins de 500 ms (ESTIMATION), valeurs égales à celles de la carte |
| **J5** Production du pipeline | publication progressive, manifest, rétention, reprises, supervision, CloudFront, domaines | un tirage réel publié automatiquement, 7 jours sans intervention |
| **J6** Itinéraire | `/v1/route`, vue le long du trajet, lien depuis l'application | résultat en moins de 2 s pour 500 points (ESTIMATION), aucune trace stockée |
| **J7** Finitions | PWA hors-ligne, accessibilité, FR/EN, légal, **garde-fous du service public** (§8) | audit manuel clavier et lecteur d'écran, attribution et avertissement vérifiés, alarmes de coût actives |
| **J8** Très court terme | couche pluie à 15 min depuis AROME-PI (via WCS, jeton requis) | échéances de 15 min jusqu'à +6 h affichées sans saut avec la frise horaire |
| **J9** Europe (hors v1) | adaptateurs ARPEGE puis ICON-D2, contrat de variables, manifest multi-modèles, attributions dynamiques | un point à Munich et un à Lyon servis par le bon modèle, coutures documentées |

**Tests** : jeux de fichiers GRIB réduits pour les tests unitaires (décodage, masque, dé-cumul, conversions, quantification aller-retour avec erreur maximale chiffrée) ; test de bout en bout du pipeline sur un tirage réel ; captures d'écran de référence pour la visualisation ; mesures de performance sur deux appareils ; surveillance de la fraîcheur.

## 11. Risques et questions ouvertes

| # | Sujet | Risque / question | Piste |
|---|---|---|---|
| R1 | Source | Météo-France change les noms de paquets, la grille ou l'hébergement | lecture du manifest du dépôt, alerte sur échec, version de schéma |
| R2 | Source | limites d'appels du S3 public inconnues | téléchargement poli (peu de connexions), reprise ; à mesurer |
| R3 | Domaine HD | pression au niveau de la mer et vent > 100 m absents en 0,01° | bascule par couche (D7) |
| R4 | GPU | particules coûteuses sur téléphones modestes | nombre adaptatif, mode flèches |
| R5 | Tuiles | coutures et artefacts d'interpolation | bordure d'1 px, tests visuels |
| R6 | Coût | diffusion supérieure aux estimations | CDN avec cache fort, compression, plafonds de budget |
| R7 | Légal | réutilisation des tuiles dérivées et attribution | relecture de la licence, mention systématique |
| R9 | AROME-PI | dépend d'un compte et d'un jeton, limite de 100 requêtes/min, catalogue lisible seulement avec le jeton ; PIAF probablement non réutilisable | créer le compte avant J8 ; mesurer le nombre de requêtes par tirage ; PIAF hors v1 |
| R11 | Complétude | un tirage HD arrive incomplet (paquet SP1 absent ou tronqué alors que les autres sont complets, constaté le 17/10) | disponibilité par variable et par échéance (D8), repli sur le tirage précédent ou sur AROME 0,025° pour la variable manquante, supervision de la complétude |
| R10 | Multi-modèles | licences et formats hétérogènes, coutures visibles | priorité fixe, attribution dynamique, un adaptateur isolé par source (voir couverture-europe.md) |
| R8 | Positionnement | un tiers pourrait croire à une alerte officielle | avertissements, aucun vocabulaire d'alerte |
| O1 | Public | ~~service réservé à l'application ?~~ **Tranché le 17/10/2026 : service public**, sans compte | garde-fous du §8 |
| O2 | Géocodage | quel service de recherche de lieu (données OSM, Nominatim public non adapté à l'usage commercial) ? | à étudier avec l'API carto |
| O3 | Radar | quelle source pour la pluie observée (PIAF, autres jeux Météo-France) ? | étude après J3 |
| O4 | Hors domaine | prévision de repli hors de la France (autre modèle) ? | hors v1 |
| O5 | Fuseaux | affichage par défaut : heure locale du lieu consulté ou de l'utilisateur ? | à trancher |

## 12. Décisions à valider

| # | Décision | Recommandation |
|---|---|---|
| D1 | Calcul et données sur AWS, interface statique sur o2switch | **validée le 17/10/2026** |
| D2 | PMTiles par (modèle, tirage, variable, échéance) | oui |
| D3 | Interpolation temporelle côté client | oui |
| D4 | Moteur : MapLibre GL JS + couches WebGL personnalisées, avec TypeScript et un outil de build (l'interface est trop riche pour le sans build de la vitrine et de la PWA) | oui |
| D5 | Particules de vent calculées sur GPU à partir du champ u/v | oui |
| D6 | Séries ponctuelles via une fonction sans état | à trancher au PoC J4 |
| D7 | Choix automatique du modèle par zoom, repli par couche | oui |
| D8 | Publication progressive et repli sur le tirage précédent | oui |
| D9 | Rétention de 3 tirages | oui |
| D10 | Architecture multi-modèles : adaptateur par source, contrat de variables canoniques, manifest par modèle (v1 : AROME et HD seulement, mais le contrat est écrit dès le départ) | oui |
| D11 | Couche de base Europe : ARPEGE 0,1° en premier, puis ICON-D2, hors v1 | oui |
| D12 | Très court terme : AROME-PI via l'API WCS de Météo-France, jeton dans Secrets Manager ; PIAF exclu tant que sa licence n'est pas confirmée | oui, après création du compte |
| D13 | Profil de poids de production : **`reduit2`** (0,99 Go par tirage au lieu de 2,87 Go) ; règle de décodage unique `min + (code−1) × quant_step` | **tranchée le 17/10** |
| D14 | Plage de `t2m` et `gust10` **propre à chaque fichier** (même pas de 0,25), `min` calculé d'après les données, pour couvrir toutes les saisons ; le client lit `min` et `quant_step` | oui, à valider sur un tirage d'été |
