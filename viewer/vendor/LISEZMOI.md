# Bibliothèques tierces copiées (aucune ressource externe à l'exécution)

Copies **non modifiées** des fichiers publiés sur npm (jsDelivr), versions épinglées. Les empreintes SHA-384 ci-dessous sont celles qui étaient déclarées (SRI) dans `index.html` avant l'auto-hébergement ; les fichiers copiés y correspondent.

| Fichier | Paquet | Licence | SHA-384 (base64) |
|---|---|---|---|
| `maplibre-gl/maplibre-gl.js` | maplibre-gl 5.24.0 | BSD-3-Clause (`maplibre-gl/LICENSE.txt`) | `5+cfbwT0iiub6VsQAdn6yz16nr6sDiQoHx6tm4O8OVYXHYOxcffFmCJBL0dgdvGp` |
| `maplibre-gl/maplibre-gl.css` | maplibre-gl 5.24.0 | idem | `uTttxo/aOKbdE5RlD/SPzSDoDmNvGlUYPjONi2MN/b7c9HPSvW07OIuyP7uL6jxK` |
| `pmtiles/pmtiles.js` | pmtiles 4.5.0 | BSD-3-Clause (`pmtiles/LICENSE`) | `pbKNZbDshUsLnZMIVsn/t+j3xEY9PGfuX5Z22l5rcy/obXz3EwaMx1F5d9sRsJMn` |
| `protomaps-basemaps/basemaps.js` | @protomaps/basemaps 5.7.2 | BSD-3-Clause (`protomaps-basemaps/LICENSE`) | `fzuU7k7ZWwfZAe1+3h7plrSA6dNazJaM2NsKVwcrReJLK0gwVwyOSyPwROl9m1M0` |

Vérifier : `openssl dgst -sha384 -binary vendor/pmtiles/pmtiles.js | base64 -w0` (un test Node le fait aussi : `viewer/tests/production.test.mjs`).

Mise à jour : télécharger la nouvelle version depuis `https://cdn.jsdelivr.net/npm/<paquet>@<version>/dist/...`, remplacer les fichiers, mettre à jour ce tableau et `VERSION` dans `sw.js`.

Les polices et sprites du fond de carte sont dans `assets/fond/` (35 fichiers, 2,6 Mo ; origine `https://protomaps.github.io/basemaps-assets/` ; polices Noto Sans sous SIL OFL 1.1 : `assets/fond/fonts/OFL.txt` ; sprites dérivés de tangrams/icons sous licence MIT : `assets/fond/sprites/LICENSE-tangrams-icons.md`). Icônes RuneRide : copie de `identity/logo/icon` (CC BY-SA 4.0).
