# PoC J3 : particules de vent sur GPU

Module autonome, non intégré au viewer. Fichiers : `viewer/js/particules.js` (classe `ParticulesVent`), `particules-logique.js` (logique pure), `particules-shaders.js` (GLSL ES 3.00), `particules-champ.js` (fournisseur PMTiles), `viewer/particules.html` (démonstration), `viewer/tools/particules-logique.test.mjs` (tests Node), `viewer/tools/pilote_particules.mjs` (copie du pilote CDP avec émulation de `prefers-reduced-motion`). Aucun fichier existant n'est modifié ; le module importe `config.js`, `donnees.js` et `couche-gl.js` (`tuilesVisibles`) en lecture seule.

## 1. API

```js
import { ParticulesVent } from './js/particules.js';
import { fournisseurPmtiles } from './js/particules-champ.js';
const champ = fournisseurPmtiles({ urlEcheance: h => `/data-reduit2/arome-hd/2026-10-17T03Z/wind10/${String(h).padStart(2,'0')}.pmtiles`, echeances: [0,1,...,51] });
const p = new ParticulesVent({ fournisseurChamp: champ.fournisseur, surEtat: e => majInterface(e) });
map.addLayer(p, idAvant);          // CustomLayerInterface (WebGL2, Mercator, tangage 0)
p.setTemps(10.5);                  // heures depuis le début du tirage, fractionnaire
```

- `fournisseurChamp(t)` (obligatoire) renvoie `{ urlA, urlB|null, mix, zmin, zmax, dec }` ou `null` : fichiers PMTiles des deux échéances, poids de B, zooms du fichier, `dec` = `paramsDecodage(...)` de config.js (`wind8` ou `wind12`, `max`). `fournisseurPmtiles` lit en-tête et métadonnées de la 1re échéance (`champ.pret` est une promesse) et résout t en deux échéances.
- Options : `id`, `nombre` (absent = adaptatif), `pxParMs` (3 : pixels CSS par seconde et par m/s, échelle de vitesse), `exposantZoom` (0 : vitesse écran indépendante du zoom ; 1 : vitesse géographique constante), `vieMoyenne` (2,2 s), `fondu` (0,955 par image à 60 Hz), `taillePoint` (1,8 px), `opacite`, `opaciteTrainee`, `echelleChamp` (0,5), `ratioMax` (2), `stockage` (`auto|flottant|paquete`), `respecterMouvementReduit` (true), `actif`, `surEtat`.
- Méthodes : `setTemps(t)`, `setNombre(n)` (`null` = adaptatif), `activer()`, `desactiver()`, `reduireMouvement(bool)`, `regler({...})`, `abonner(f)`.
- État pour l'interface : `p.visibles` et `p.etat` = `{ actif, mouvementReduit, visibles, indisponible, stockage, nombre, adaptatif, msBanc }`. **Si `actif && mouvementReduit` (ou `indisponible`), l'interface affiche les flèches à la place.**
- Diagnostics (vérifications) : `lireChamp()`, `lireEtat()`, `controleHorsDonnees()`, `autotestGpu()`, `diagnostic()`.

## 2. Conception et justifications

1. **Champ à l'écran** : les tuiles visibles (même sélection que le calque couleur, `tuilesVisibles`, même repli sur ancêtre) sont décodées dans un shader recopié de `couche-gl.js` (bilinéaire après décodage, code 0 = pas de donnée, mélange temporel de u et v) et rendues dans une texture à demi-résolution de l'écran. Elle n'est recalculée que si la vue, le temps ou les tuiles changent. Le cache de tuiles de `donnees.js` est partagé : avec le calque couleur du vent, **aucune requête supplémentaire**. Les textures de tuiles sont propres au module (LRU de 96), car `surLiberation` n'a qu'un abonné.
2. **Positions en coordonnées d'écran** [0,1]² : densité constante à tout zoom par construction, sans calcul de projection par particule. Contrepartie : les particules ne suivent pas la carte pendant un déplacement ; la traînée est donc effacée dès que la matrice de projection change (`vueChangee`), d'où l'absence de fantômes.
3. **Déplacement** : `pixels/s = vent (m/s) × pxParMs` (× 2^(exposantZoom·(zoom−6))), orienté par la rotation extraite de la matrice (`rotationDepuisMatrice`, gère la rotation de carte), donc proportionnel à la vitesse du vent et réglable. Choix par défaut : vitesse écran indépendante du zoom (sinon 32 fois plus rapide cinq niveaux plus loin).
4. **Réapparition** : loi exponentielle (durée de vie moyenne, indépendante du pas de temps), sortie d'écran, ou passage hors des données ; jusqu'à 4 tirages uniformes, le premier tombant dans les données est retenu. Le dessin ignore toute particule dont la position n'est pas dans les données. Hasard : hachage entier 32 bits identique en JS et GLSL (vérifié par `autotestGpu`).
5. **Traînée** : deux textures RGBA8 en alternance, estompage `max(c·f − 1,5/255, 0)` : le retrait minimal est indispensable en 8 bits (sans lui un canal faible ne décroît jamais, test Node à l'appui). Résolution plafonnée à `ratioMax` pixels par pixel CSS (mobile dpr 3 : 780×1688 au lieu de 1170×2532).
6. **Couleur** : texture de palette de `COUCHES[0]` (vent), vitesse × 3,6 km/h sur 0 à 100 : même rampe que la légende. L'opacité de la rampe est ignorée (le bas de rampe est transparent pour le calque couleur).
7. **Stockage** : `EXT_color_buffer_float` présent et cibles RGBA32F (état) et RGBA16F (champ) réellement complètes, sinon **repli 8 bits** : positions sur 16 bits (2 octets), vent sur 16 bits sur ±64 m/s (pas de 0,002 m/s, code 0 = pas de donnée). Le repli est forçable (`stockage:'paquete'`). Limite : pas de position de 0,02 px, les vents très faibles avancent par à-coups.
8. **Nombre adaptatif** : `surface / 100 px²` (1280×800 : 10 200), mobile (largeur < 700 px, ou < 900 px tactile) divisé par 1,5 et plafonné à 6 000, borné à 1 000..40 000, multiplié par `max(0,25 ; 12 ms / msBanc)` si le **banc GPU** (6 mises à jour + dessins de 16 384 particules, synchronisés par `readPixels`) dépasse 12 ms. Recalculé au redimensionnement.
9. **Mouvement réduit** : `matchMedia('(prefers-reduced-motion: reduce)')` lu et suivi en direct ; dans cet état, aucun calcul ni repeinte. `reduireMouvement(false)` permet un choix explicite de l'utilisateur.

Références conceptuelles (aucun code repris) : V. Agafonkin, « How I built a wind map with WebGL » (Mapbox, 2017), pour la technique des particules dans une texture d'état et le dessin des points lus dans cette texture ; la traînée par textures alternées estompées est d'usage courant (earth.nullschool.net, windy.com). Hachage « lowbias32 » de C. Wellons (domaine public).

## 3. Vérifications (Chrome 155 sans interface, WebGL logiciel SwiftShader)

**Aucune conclusion sur les images par seconde ni sur les temps** : le rendu est logiciel. Seuls les nombres ci-dessous sont indépendants de la machine. Données réelles `/data-reduit2/arome-hd/2026-10-17T03Z/wind10/` (wind8, 52 échéances), t = h+10,5, 1280×800, 10 000 particules.

- **Tests Node** : `node --test viewer/tools/particules-logique.test.mjs` : 13 tests (hachage, uniformité, réapparition, taux indépendant du pas, vitesse → couleur identique à la texture de palette du calque couleur, échelle de déplacement, orientation avec rotation de carte, empaquetages 16 bits, estompage sans fantôme, nombre adaptatif, changement de vue, résolution des échéances) : 13 réussis.
- **Autotest GPU** : hachage GPU = JS (écart 0) ; empaquetage vent 16 bits : écart maximal 1 code (arrondi flottant GPU, 0,002 m/s).
- **Champ rendu contre décodage CPU indépendant** (`lireAuPoint`, mélange temporel inclus, ~400 points) : écart médian 0,005 m/s, p95 0,023, max 0,09 (flottant) ; repli 8 bits : 0,006 / 0,035 / 0,09.
- **Direction et échelle** : sur ~8 200 particules, cosinus moyen entre déplacement et vent 0,978 ; 98 % au-dessus de 0,9 ; rapport déplacement/vent stable (p25 0,81, médiane 0,84, p75 0,86 px par m/s sur l'intervalle mesuré).
- **Hors des données** : vue sur le bord sud-ouest du domaine (champ valide à 41 %) : 126 073 pixels de traînée allumés, **0** à plus de 3 px du champ valide ; même résultat (0) dans toutes les vues testées (déplacement, zoom, repli 8 bits). Capture : `img/part-hors-donnees.webp`.
- **Déplacement et zoom** : traînée effacée au changement de vue (captures `img/part-apres-deplacement-120ms.webp` / `-1500ms.webp`, `img/part-apres-zoom-150ms.webp` / `-1650ms.webp`) ; pas de trace à l'ancienne position, bord du domaine net.
- **Captures à t0 et +1,5 s** : `img/part-france-t0.webp`, `img/part-france-t1500.webp` (repli 8 bits : `img/part-paquete-t0.webp`, `-t1500.webp`) ; mobile émulé 390×844 dpr 3 : `img/part-mobile.webp` (1 000 particules : le banc SwiftShader, 47 ms, a fait descendre le nombre automatique à son plancher).
- **Mouvement réduit** (émulation CDP du média réel) : `visibles=false`, 0 image rendue en 2,5 s, avis « flèches » affiché (`img/part-mouvement-reduit.webp`) ; `reduireMouvement(false)` relance l'animation.
- **Octets et requêtes** (vue France 1280×800, t = 10,5, profil reduit2) : 30 tuiles (échéances 10 et 11), 34 requêtes Range, **716 811 octets**, identiques avec les deux stockages ; mobile émulé : 18 tuiles, 22 requêtes, 534 375 octets. Après un déplacement et un zoom à 7,4 : 58 tuiles, 56 requêtes, 1 023 923 octets cumulés. **Avec le calque couleur du vent affiché, le surcoût est nul** (cache partagé). Un défilement du temps ne coûte qu'une tuile par échéance de plus.

## 4. Limites et points ouverts

- Mercator, tangage 0 uniquement (matrice affine) ; pas de globe.
- Particules non accrochées à la carte pendant le geste (choix assumé, traînée effacée) ; des particules accrochées en coordonnées géographiques demanderaient une projection par particule.
- Coût réel non mesuré : le banc GPU et les seuils (12 ms, 100 px² par particule, plafond mobile 6 000) sont à calibrer sur de vrais appareils ; le nombre n'est pas ajusté en continu selon la cadence.
- Repli 8 bits : testé en logiciel seulement (le flottant aussi) ; vitesse de 0,02 px de pas minimal.
- Intégration : le fournisseur doit rester cohérent avec l'échéance et le profil du calque couleur ; l'ordre des calques (sous routes et frontières) est à la charge de l'appelant (`beforeId`). Les flèches existantes doivent être masquées tant que `visibles` est vrai.
- Pas de `dispose` des tuiles du cache partagé (le module ne détruit que ses propres textures).
