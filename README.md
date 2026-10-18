# RuneRide Météo

Prévisions météo de RuneRide, prévues pour `weather.runeride.fr` : une carte interactive aussi confortable que windy.com, construite sur les données ouvertes **AROME** (0,025°) et **AROME HD** (0,01°) de Météo-France, reliée aux trajets et aux itinéraires de l'application.

**Statut : spécification et PoC de la chaîne de données (J1) et de la visualisation (J2, prototype).** Rien n'est déployé.

- Spécifications : [docs/specifications.md](docs/specifications.md)
- Données AROME, mesures réelles (licence, accès, cadence, grilles, paquets, volumes) : [docs/donnees-arome.md](docs/donnees-arome.md)
- Couverture hors France (ARPEGE, ICON-D2, autres modèles, vérifications) : [docs/couverture-europe.md](docs/couverture-europe.md)
- **PoC J1** : synthèse [docs/poc-j1/synthese.md](docs/poc-j1/synthese.md), contrat entre briques [docs/poc-j1-contrat.md](docs/poc-j1-contrat.md), rapports [ingestion](docs/poc-j1/ingestion.md), [tuilage](docs/poc-j1/tuilage.md), [visualisation](docs/poc-j1/visualisation.md)
- Code : `pipeline/ingest/` (GRIB vers champs canoniques), `pipeline/tiling/` (tuiles PMTiles), `viewer/` (page MapLibre + WebGL), `tests/`
- **Mise en production** (viewer sur o2switch, données sur CloudFront, CI/CD, retour arrière) : [docs/mise-en-production.md](docs/mise-en-production.md)
- Outil d'inventaire GRIB : `tools/lsgrib.py` (paquet Python `eccodes`)

Même démarche que `runeride-carto` : prétraitement hors de l'hébergeur mutualisé, fichiers statiques pour l'interface, tuiles PMTiles lues par requêtes `Range`.

Données © Météo-France, Licence Ouverte v2.0. Code sous EUPL-1.2, contenus sous CC BY-SA 4.0 : voir `NOTICE.md`.
