# RuneRide Météo

Prévisions météo de RuneRide, prévues pour `weather.runeride.fr` : une carte interactive aussi confortable que windy.com, construite sur les données ouvertes **AROME** (0,025°) et **AROME HD** (0,01°) de Météo-France, reliée aux trajets et aux itinéraires de l'application.

**Statut : spécification.** Rien n'est développé ni déployé.

- Spécifications : [docs/specifications.md](docs/specifications.md)
- Données AROME, mesures réelles (licence, accès, cadence, grilles, paquets, volumes) : [docs/donnees-arome.md](docs/donnees-arome.md)
- Outil d'inventaire GRIB : `tools/lsgrib.py` (paquet Python `eccodes`)

Même démarche que `runeride-carto` : prétraitement hors de l'hébergeur mutualisé, fichiers statiques pour l'interface, tuiles PMTiles lues par requêtes `Range`.

Données © Météo-France, Licence Ouverte v2.0. Code sous EUPL-1.2, contenus sous CC BY-SA 4.0 : voir `NOTICE.md`.
