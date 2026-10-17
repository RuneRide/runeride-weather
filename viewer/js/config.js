// SPDX-License-Identifier: EUPL-1.2
// Définition des couches, palettes et encodages (contrat PoC J1 §3).
// Les valeurs min/max/échelle sont celles du contrat ; les métadonnées PMTiles
// sont relues après l'ouverture pour vérifier qu'elles concordent (voir main.js).

export const TUILE = 256;

// Palettes : arrêts [position 0..1, [r, g, b, a]] (a = opacité 0..255).
// Choix « scientifiques » : monotones en luminance quand c'est pertinent, jamais la couleur de marque.
const P = {
  // vitesse du vent : séquentielle claire -> sombre chaude (lisible sur fond sombre)
  vent: [[0, [38, 70, 140, 0]], [0.06, [48, 99, 168, 150]], [0.2, [38, 160, 190, 215]], [0.35, [60, 190, 130, 225]],
    [0.5, [190, 215, 70, 230]], [0.65, [245, 190, 50, 235]], [0.8, [240, 110, 40, 240]], [0.92, [215, 40, 70, 245]], [1, [150, 30, 140, 250]]],
  // température : bleu profond -> cyan -> vert -> jaune -> rouge -> pourpre
  temp: [[0, [80, 40, 160, 235]], [0.15, [50, 90, 205, 235]], [0.3, [60, 170, 225, 235]], [0.42, [90, 200, 160, 235]],
    [0.52, [170, 215, 90, 235]], [0.62, [250, 225, 70, 235]], [0.75, [245, 150, 45, 235]], [0.88, [220, 55, 50, 235]], [1, [140, 20, 90, 240]]],
  pluie: [[0, [120, 190, 240, 140]], [0.2, [70, 150, 235, 200]], [0.45, [50, 90, 210, 225]], [0.65, [110, 70, 200, 235]],
    [0.82, [200, 60, 190, 240]], [1, [250, 120, 150, 250]]],
  humidite: [[0, [190, 120, 60, 200]], [0.3, [220, 190, 110, 200]], [0.55, [150, 205, 160, 205]], [0.8, [60, 160, 190, 220]], [1, [30, 70, 160, 235]]],
  nuages: [[0, [235, 235, 240, 0]], [0.15, [225, 228, 235, 40]], [0.6, [205, 210, 222, 140]], [1, [235, 238, 245, 215]]],
  neige: [[0, [150, 190, 235, 170]], [0.5, [200, 225, 250, 220]], [1, [255, 255, 255, 250]]],
  cape: [[0, [250, 225, 90, 120]], [0.25, [250, 190, 60, 210]], [0.5, [240, 120, 40, 230]], [0.75, [215, 40, 60, 240]], [1, [160, 30, 160, 250]]],
};

// Couches. « legende » : domaine d'affichage [lo, hi] dans l'unité affichée ; « axe » = linear|sqrt
// (l'axe sqrt répartit les couleurs en racine carrée de la valeur, pour la pluie et la CAPE).
// « aff » : conversion valeur native -> valeur affichée = v * mul + add.
export const COUCHES = [
  { cle: 'vent', nom: 'Vent', variable: 'wind10', enc: 'wind12', min: -64, max: 64, echelle: 'linear',
    aff: { mul: 3.6, add: 0, unite: 'km/h', dec: 0 }, legende: { lo: 0, hi: 100, axe: 'linear', ticks: [0, 20, 40, 60, 80, 100] },
    palette: P.vent, seuil: -1, fleches: true },
  { cle: 'rafales', nom: 'Rafales', variable: 'gust10', enc: 'scalar8', min: 0, max: 64, echelle: 'linear',
    aff: { mul: 3.6, add: 0, unite: 'km/h', dec: 0 }, legende: { lo: 0, hi: 130, axe: 'linear', ticks: [0, 30, 60, 90, 130] },
    palette: P.vent, seuil: 0.5 },
  { cle: 't2m', nom: 'Température', variable: 't2m', enc: 'scalar8', min: 233.15, max: 333.15, echelle: 'linear',
    aff: { mul: 1, add: -273.15, unite: '°C', dec: 1 }, legende: { lo: -20, hi: 45, axe: 'linear', ticks: [-20, -10, 0, 10, 20, 30, 40] },
    palette: P.temp, seuil: -999 },
  { cle: 'pluie', nom: 'Pluie', variable: 'precip_rate', enc: 'scalar8', min: 0, max: 100, echelle: 'sqrt',
    aff: { mul: 1, add: 0, unite: 'mm/h', dec: 1 }, legende: { lo: 0, hi: 50, axe: 'sqrt', ticks: [0.5, 2, 5, 10, 25, 50] },
    palette: P.pluie, seuil: 0.1 },
  { cle: 'nuages', nom: 'Nuages', variable: 'cloud_total', enc: 'scalar8', min: 0, max: 100, echelle: 'linear',
    aff: { mul: 1, add: 0, unite: '%', dec: 0 }, legende: { lo: 0, hi: 100, axe: 'linear', ticks: [0, 25, 50, 75, 100] },
    palette: P.nuages, seuil: -1 },
  { cle: 'humidite', nom: 'Humidité', variable: 'rh2m', enc: 'scalar8', min: 0, max: 100, echelle: 'linear',
    aff: { mul: 1, add: 0, unite: '%', dec: 0 }, legende: { lo: 0, hi: 100, axe: 'linear', ticks: [0, 25, 50, 75, 100] },
    palette: P.humidite, seuil: -1 },
  { cle: 'neige', nom: 'Neige (part)', variable: 'snow_fraction', enc: 'scalar8', min: 0, max: 1, echelle: 'linear',
    aff: { mul: 100, add: 0, unite: '% neige', dec: 0 }, legende: { lo: 0, hi: 100, axe: 'linear', ticks: [0, 25, 50, 75, 100] },
    palette: P.neige, seuil: 10 },
  { cle: 'cape', nom: 'Orages (CAPE)', variable: 'cape', enc: 'scalar8', min: 0, max: 5000, echelle: 'sqrt',
    aff: { mul: 1, add: 0, unite: 'J/kg', dec: 0 }, legende: { lo: 0, hi: 3000, axe: 'sqrt', ticks: [100, 500, 1000, 2000, 3000] },
    palette: P.cape, seuil: 50 },
];

/** Échantillonne la palette (arrêts) à t dans [0,1] -> [r,g,b,a]. */
export function couleurPalette(arrets, t) {
  t = Math.min(1, Math.max(0, t));
  for (let i = 1; i < arrets.length; i++) {
    if (t <= arrets[i][0]) {
      const [t0, c0] = arrets[i - 1], [t1, c1] = arrets[i];
      const f = (t - t0) / (t1 - t0 || 1);
      return c0.map((v, k) => v + (c1[k] - v) * f);
    }
  }
  return arrets[arrets.length - 1][1].slice();
}

/** Palette sur 256 entrées RGBA8 (non pré-multipliée) pour la texture 1D. */
export function texturePalette(arrets) {
  const out = new Uint8Array(256 * 4);
  for (let i = 0; i < 256; i++) {
    const c = couleurPalette(arrets, i / 255);
    for (let k = 0; k < 4; k++) out[i * 4 + k] = Math.round(c[k]);
  }
  return out;
}

// --- Décodage CPU (miroir exact du shader et du contrat §3, révision 2) ---
/** Paramètres de décodage d'une couche : lus dans les métadonnées PMTiles (quant_step, levels, max, encoding),
 *  repli sur le contrat (254 intervalles, vent wind12 +-64) si une clé manque. */
export function paramsDecodage(couche, meta) {
  meta = meta || {};
  const enc = meta.encoding || couche.enc;
  const min = meta.min ?? couche.min, max = meta.max ?? couche.max, echelle = meta.scale || couche.echelle;
  const levels = meta.levels || 254;
  const quant = meta.quant_step ?? (enc === 'wind8' ? 2 * max / 254 : (max - min) / levels);
  return { enc, min, max, echelle, levels, quant };
}
export function decoderScalaire(code, dec) {
  if (dec.echelle === 'sqrt') { const f = (code - 1) / dec.levels; return dec.min + (dec.max - dec.min) * f * f; }
  return dec.min + (code - 1) * dec.quant;   // règle unique du contrat (révision 2) : pas lu dans les métadonnées
}
export function decoderVent(r, g, b, dec) {
  if (dec.enc === 'wind8') return [(r - 1) / 254 * 2 * dec.max - dec.max, (g - 1) / 254 * 2 * dec.max - dec.max];
  const cu = (r << 4) | (g >> 4), cv = ((g & 15) << 8) | b;
  return [(cu - 1) / 4094 * 128 - 64, (cv - 1) / 4094 * 128 - 64];
}
