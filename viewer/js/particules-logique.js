// SPDX-License-Identifier: EUPL-1.2
// Logique PURE des particules de vent (aucun accès à WebGL ni au DOM) : testable avec `node --test`.
// Chaque fonction a son jumeau dans les shaders (particules-shaders.js) ; les tests Node vérifient
// la version JavaScript, et l'autotest GPU (ParticulesVent.autotestGpu) vérifie que le GPU donne
// les mêmes résultats (hachage, empaquetage).
//
// Références conceptuelles (aucun code repris) : technique des particules sur GPU décrite par
// V. Agafonkin (Mapbox, « How I built a wind map with WebGL », 2017), qui stocke les positions dans une
// texture mise à jour par un shader et dessine les points en lisant cette texture ; traînée par
// deux textures alternées avec estompage, usage répandu (earth.nullschool.net, windy.com).
import { COUCHES, couleurPalette } from './config.js';

/** Circonférence de la Terre à l'équateur en mètres (Web Mercator). */
export const CIRCONFERENCE = 40075016.686;

// ------------------------------------------------------------------ hasard
/** Hachage entier 32 bits (« lowbias32 », domaine public, C. Wellons). Identique au GLSL (uint). */
export function hash32(x) {
  x = x >>> 0;
  x ^= x >>> 16; x = Math.imul(x, 0x7feb352d) >>> 0;
  x ^= x >>> 15; x = Math.imul(x, 0x846ca68b) >>> 0;
  x ^= x >>> 16;
  return x >>> 0;
}
/** Nombre aléatoire dans [0,1[ pour (identifiant de particule, graine de l'image, indice d'essai).
 *  24 bits utiles : exactement représentable en flottant 32 bits, donc identique sur GPU. */
export function alea(id, graine, essai = 0) {
  const h = hash32((Math.imul(id >>> 0, 0x9e3779b1) + Math.imul(graine >>> 0, 0x85ebca6b) + Math.imul(essai >>> 0, 0xc2b2ae35)) >>> 0);
  return (h >>> 8) / 16777216;
}

// ------------------------------------------------------------------ réapparition
/** Probabilité de réapparition d'une particule pendant dt secondes pour une durée de vie moyenne `vieMoy` (s).
 *  Loi exponentielle : indépendante du pas de temps (dt grand ou petit, même durée de vie moyenne). */
export function tauxReapparition(dt, vieMoy) {
  if (!(vieMoy > 0)) return 1;
  return 1 - Math.exp(-Math.max(0, dt) / vieMoy);
}
/** Une particule réapparaît (nouvelle position tirée au hasard dans les données) si :
 *  elle est sortie de l'écran, ou elle est tombée hors des données (code 0), ou le tirage < taux. */
export function doitReapparaitre({ x, y, valide, tirage, taux }) {
  if (!(x >= 0 && x <= 1 && y >= 0 && y <= 1)) return true;
  if (!valide) return true;
  return tirage < taux;
}

// ------------------------------------------------------------------ vitesse -> couleur
/** Rampe du vent du viewer (même objet que la légende : COUCHES[0], en km/h de 0 à 100). */
export const COUCHE_VENT = COUCHES.find(c => c.cle === 'vent');
/** Position dans la rampe [0,1] pour une vitesse en m/s (identique à couche-gl : v * mul, (d-lo)/(hi-lo)). */
export function vitesseVersT(ms, couche = COUCHE_VENT) {
  const d = ms * couche.aff.mul + couche.aff.add;
  return Math.min(1, Math.max(0, (d - couche.legende.lo) / (couche.legende.hi - couche.legende.lo)));
}
/** Indice 0..255 de la texture de palette (même arrondi que le shader : round(t*255)). */
export function vitesseVersIndice(ms, couche = COUCHE_VENT) {
  return Math.round(vitesseVersT(ms, couche) * 255);
}
/** Couleur RVB 0..255 d'une particule à la vitesse `ms` (m/s). L'opacité de la rampe est ignorée :
 *  les particules les plus lentes (alpha 0 en bas de rampe pour la couche couleur) doivent rester visibles. */
export function vitesseVersCouleur(ms, couche = COUCHE_VENT) {
  const t = vitesseVersIndice(ms, couche) / 255;
  return couleurPalette(couche.palette, t).slice(0, 3).map(Math.round);
}

// ------------------------------------------------------------------ déplacement
/** Pixels CSS par seconde pour un vent de `ms` m/s. `pxParMs` : échelle réglable (px/s par m/s),
 *  `exposantZoom` : 0 = vitesse écran indépendante du zoom (défaut), 1 = vitesse géographique constante
 *  (les particules ralentissent à l'écran quand on dézoome, et accélèrent quand on zoome). */
export function vitessePixels(ms, zoom, { pxParMs = 3, exposantZoom = 0, zoomRef = 6 } = {}) {
  return ms * pxParMs * Math.pow(2, exposantZoom * (zoom - zoomRef));
}
/** Rotation/retournement 2x2 (colonne par colonne : [r00, r10, r01, r11]) qui amène le vecteur
 *  (est, -nord) en coordonnées Mercator vers l'espace clip de l'écran, normalisée (déterminant 1 en valeur
 *  absolue) pour ne conserver que l'orientation (rotation de la carte). Matrice MapLibre 4x4 en colonnes. */
export function rotationDepuisMatrice(m) {
  const a = m[0], c = m[1], b = m[4], d = m[5];       // clip.x = a*mx + b*my ; clip.y = c*mx + d*my
  const s = Math.sqrt(Math.abs(a * d - b * c)) || 1;
  return [a / s, c / s, b / s, d / s];
}
/** Déplacement en coordonnées de position d'écran [0,1]² pendant dt, pour un vent (u est, v nord) en m/s. */
export function deplacementPosition(u, v, dt, rot, largeurCss, hauteurCss, zoom, opts) {
  const vitesse = Math.hypot(u, v);
  if (vitesse === 0) return [0, 0];
  const px = vitessePixels(vitesse, zoom, opts) * dt;         // longueur du trajet en pixels CSS
  const gx = u / vitesse, gy = -v / vitesse;                  // direction en Mercator (y vers le sud)
  const cx = rot[0] * gx + rot[2] * gy, cy = rot[1] * gx + rot[3] * gy;
  const n = Math.hypot(cx, cy) || 1;
  // clip -> position [0,1] : divisé par 2, et clip [-1,1] = largeur en pixels : dp = dpx / taille
  return [cx / n * px / largeurCss, cy / n * px / hauteurCss];
}

// ------------------------------------------------------------------ empaquetage 8 bits (repli sans textures flottantes)
/** Valeur 0..65535 -> deux octets [haut, bas]. */
export function empaqueter16(c) { c = Math.min(65535, Math.max(0, Math.round(c))); return [c >> 8, c & 255]; }
export function depaqueter16(hi, lo) { return (hi << 8) | lo; }
/** Position [0,1] <-> 16 bits (pas de 1/65535 de l'écran, soit 0,02 px sur 1 500 px). */
export function empaqueterPosition(p) { return empaqueter16(Math.min(1, Math.max(0, p)) * 65535); }
export function depaqueterPosition(hi, lo) { return depaqueter16(hi, lo) / 65535; }
/** Vent (m/s) <-> 16 bits sur ±VMAX. Le code 0 est réservé : « pas de donnée » (comme les tuiles). */
export const VMAX = 64;
export function codeVent16(x) { return 1 + Math.round((Math.min(VMAX, Math.max(-VMAX, x)) + VMAX) / (2 * VMAX) * 65534); }
export function ventDepuisCode16(c) { return (c - 1) / 65534 * 2 * VMAX - VMAX; }
export function empaqueterVent(u, v) { return [...empaqueter16(codeVent16(u)), ...empaqueter16(codeVent16(v))]; }
/** Renvoie [u, v] ou null (pas de donnée) pour 4 octets. */
export function depaqueterVent(b0, b1, b2, b3) {
  const cu = depaqueter16(b0, b1), cv = depaqueter16(b2, b3);
  if (cu === 0 || cv === 0) return null;
  return [ventDepuisCode16(cu), ventDepuisCode16(cv)];
}

// ------------------------------------------------------------------ estompage de la traînée
/** Facteur multiplicatif d'estompage pour un pas dt : `fondu` est le facteur à 60 images/s (16,67 ms). */
export function facteurFondu(fondu, dt) { return Math.pow(fondu, dt * 60); }
/** Estompage en 8 bits : multiplier ne suffit pas (un canal < 1/(1-f) ne décroît jamais, d'où des « fantômes »
 *  permanents) ; on retranche en plus un minimum. Renvoie la nouvelle valeur 0..255 entière (modèle du shader). */
export function estomper8(valeur, facteur, minimum = 1.5) {
  return Math.max(0, Math.round(valeur * facteur - minimum));
}

// ------------------------------------------------------------------ nombre de particules
/** Nombre de particules par défaut : proportionnel à la surface de l'écran (densité constante), plus bas sur
 *  mobile, réduit si le test de coût GPU est lent.
 *  - pxParParticule : surface CSS par particule (défaut 100 px² : 1280x800 donne ~10 000) ;
 *  - mobile : plafond 6 000 et densité /1,5 ;
 *  - msBanc : durée (ms) du test rapide sur `msRef` ms de budget ; facteur = msRef/msBanc borné à [0,25 ; 1]. */
export function nombreParDefaut({ largeur, hauteur, mobile = false, msBanc = null, msRef = 12, pxParParticule = 100, min = 1000, max = 40000 }) {
  let n = (largeur * hauteur) / pxParParticule / (mobile ? 1.5 : 1);
  if (mobile) n = Math.min(n, 6000);
  if (msBanc != null && msBanc > msRef) n *= Math.max(0.25, msRef / msBanc);
  return Math.max(min, Math.min(max, Math.round(n / 100) * 100));
}
/** Détection simple d'un appareil mobile (largeur d'écran + pointeur tactile grossier). */
export function estMobile(largeurCss, tactile) { return largeurCss < 700 || (tactile && largeurCss < 900); }

// ------------------------------------------------------------------ changement de vue
/** Vrai si la matrice de projection a changé de plus que `eps` (relatif) : la traînée doit être effacée. */
export function vueChangee(avant, apres, eps = 1e-6) {
  if (!avant || !apres) return true;
  let d = 0, n = 0;
  for (let i = 0; i < 16; i++) { d += Math.abs(avant[i] - apres[i]); n += Math.abs(apres[i]); }
  return d > eps * (n || 1);
}
