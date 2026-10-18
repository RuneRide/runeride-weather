// SPDX-License-Identifier: EUPL-1.2
// Accès aux tuiles de données : PMTiles (Range), décodage WebP, cache LRU, lecture CPU des valeurs.
// Les tuiles sont des données NON FIABLES : elles ne sont jamais exécutées, seulement décodées
// comme image puis lues comme octets ; toute taille inattendue est rejetée.
import { TUILE, decoderScalaire, decoderVent } from './config.js';

const LATENCE = typeof location === 'undefined' ? 0 : Number(new URLSearchParams(location.search).get('latence') || 0);

/** Compteurs de transfert (mesures). */
export const stats = {
  requetesDonnees: 0, octetsDonnees: 0, // somme des corps de réponses Range PMTiles
  requetesTuiles: 0, tuilesChargees: 0, tuilesAbsentes: 0, ms: [],
  phase: 'ouverture', parPhase: {},
};
stats.erreurs = 0;                          // tuiles de données en échec (réseau, serveur, décodage)
stats.fond = { requetes: 0, octets: 0 };   // fond de carte vectoriel (PMTiles Protomaps), compté à part
function compter(octets, cat) {
  if (cat === 'fond') { stats.fond.requetes++; stats.fond.octets += octets; return; }
  stats.requetesDonnees++; stats.octetsDonnees += octets;
  const p = (stats.parPhase[stats.phase] ||= { requetes: 0, octets: 0 });
  p.requetes++; p.octets += octets;
}

const DELAI_MS = 20000;
/** Un échec de tuile est retenté après 5 s, puis 10 s, 20 s... (plafond 60 s) tant que la tuile reste demandée. */
export const delaiReessai = essais => Math.min(60000, 5000 * 2 ** Math.max(0, essais - 1));

/** Source PMTiles qui compte les octets reçus par requête Range. */
export class SourceCompteuse {
  constructor(url, cat = 'donnees') { this.url = url; this.cat = cat; }
  getKey() { return this.url; }
  async getBytes(offset, length, signal) {
    if (LATENCE && this.cat === 'donnees') await new Promise(r => setTimeout(r, LATENCE));   // ?latence=ms : réseau lent simulé (tests de bascule)
    // délai maximal par requête (réseau mort ou serveur muet) : sans lui une tuile resterait « en chargement » indéfiniment
    const limite = typeof AbortSignal !== 'undefined' && AbortSignal.timeout ? AbortSignal.timeout(DELAI_MS) : null;
    const sig = limite && signal && AbortSignal.any ? AbortSignal.any([signal, limite]) : (signal || limite || undefined);
    const rep = await fetch(this.url, { signal: sig, headers: { Range: `bytes=${offset}-${offset + length - 1}` } });
    if (rep.status !== 206 && rep.status !== 200) throw new Error(`HTTP ${rep.status} sur ${this.url}`);
    const data = await rep.arrayBuffer();
    // 200 = le serveur a ignoré Range : on découpe (le coût réel est alors le fichier entier)
    const util = rep.status === 206 ? data : data.slice(offset, offset + length);
    compter(data.byteLength, this.cat);
    return { data: util, etag: rep.headers.get('ETag') || undefined };
  }
}

const archives = new Map(); // url -> PMTiles
export function archive(url) {
  let a = archives.get(url);
  if (!a) { a = new pmtiles.PMTiles(new SourceCompteuse(url)); archives.set(url, a); }
  return a;
}

// --- Cache de tuiles décodées (LRU borné) ---
// entrée : { etat: 'charge'|'pret'|'absent'|'erreur', px: Uint8Array, canaux, taille, bordure, tex, promesse }
const cache = new Map();
const MAX_ENTREES = 360;
let liberer = () => {}; // rappel installé par la couche GL pour détruire les textures
export function surLiberation(f) { liberer = f; }

function toucher(cle, e) { cache.delete(cle); cache.set(cle, e); }
let image = 0; // numéro de l'image courante : les tuiles vues à cette image ne sont jamais purgées
export function nouvelleImage() { return ++image; }
export function marquerVue(e) { e.vu = image; }
function purger() {
  if (cache.size <= MAX_ENTREES) return;
  for (const [cle, e] of cache) {
    if (cache.size <= MAX_ENTREES) break;
    if (e.vu === image || e.etat === 'charge') continue;
    cache.delete(cle); liberer(e);
  }
}
export function viderCache() { for (const e of cache.values()) liberer(e); cache.clear(); }

let canvasDecode;
function lirePixels(bitmap, canaux) {
  const t = bitmap.width;
  if (bitmap.height !== t || (t !== TUILE && t !== TUILE + 2)) throw new Error('taille de tuile inattendue ' + t + 'x' + bitmap.height);
  canvasDecode ||= new OffscreenCanvas(TUILE + 2, TUILE + 2);
  const ctx = canvasDecode.getContext('2d', { willReadFrequently: true, colorSpace: 'srgb', alpha: false });
  ctx.drawImage(bitmap, 0, 0);
  const rgba = ctx.getImageData(0, 0, t, t).data;
  const px = new Uint8Array(t * t * canaux);
  for (let i = 0, j = 0; i < t * t; i++) {
    px[j++] = rgba[i * 4];
    if (canaux === 3) { px[j++] = rgba[i * 4 + 1]; px[j++] = rgba[i * 4 + 2]; }
  }
  return { px, taille: t, bordure: (t - TUILE) / 2 };
}

/** Demande une tuile ; renvoie l'entrée de cache (peut être en cours de chargement). */
export function demanderTuile(urlFichier, z, x, y, couche) {
  const cle = `${urlFichier}|${z}/${x}/${y}`;
  let e = cache.get(cle);
  let essais = 0;
  if (e && e.etat === 'erreur' && performance.now() - e.tErreur >= delaiReessai(e.essais)) { essais = e.essais; cache.delete(cle); liberer(e); e = null; }   // nouvel essai espacé
  if (e) { toucher(cle, e); return e; }
  e = { etat: 'charge', cle, canaux: couche.dec.enc === 'scalar8' ? 1 : 3, vu: 0, essais };
  cache.set(cle, e);
  stats.requetesTuiles++;
  const t0 = performance.now();
  e.promesse = (async () => {
    try {
      const rep = await archive(urlFichier).getZxy(z, x, y);
      if (!rep) { e.etat = 'absent'; stats.tuilesAbsentes++; return; }
      const bmp = await createImageBitmap(new Blob([rep.data], { type: 'image/webp' }),
        { premultiplyAlpha: 'none', colorSpaceConversion: 'none' });
      Object.assign(e, lirePixels(bmp, e.canaux));
      bmp.close();
      e.etat = 'pret'; stats.tuilesChargees++; stats.ms.push(performance.now() - t0);
      surEtatReseau(true);
    } catch (err) {
      e.etat = 'erreur'; e.erreur = String(err); e.essais++; e.tErreur = performance.now(); stats.erreurs++;
      console.warn('tuile', cle, err);
      surEtatReseau(false);
    }
    surChangement();
  })();
  purger();
  return e;
}

let surChangement = () => {};
let surEtatReseau = () => {};
/** Rappel appelé avec true (tuile reçue) ou false (échec) : l'interface en tire un message discret. */
export function surReseau(f) { surEtatReseau = f; }
export function surTuile(f) { surChangement = f; }

// --- Lecture CPU au point (miroir du shader) ---
function mercator(lng, lat) {
  const x = (lng + 180) / 360;
  const s = Math.sin(lat * Math.PI / 180);
  return [x, 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)];
}

/** Valeur interpolée (bilinéaire) d'une entrée à la position fractionnaire (fx, fy) de la tuile.
 *  Retourne null si un des 4 voisins est « pas de donnée » (code 0), comme le shader. */
function bilineaire(e, couche, fx, fy) {
  const T = e.taille, b = e.bordure;
  const px = fx * TUILE - 0.5 + b, py = fy * TUILE - 0.5 + b;
  const x0 = Math.floor(px), y0 = Math.floor(py), tx = px - x0, ty = py - y0;
  const cl = v => Math.min(T - 1, Math.max(0, v));
  const val = [];
  for (const [dx, dy] of [[0, 0], [1, 0], [0, 1], [1, 1]]) {
    const i = (cl(y0 + dy) * T + cl(x0 + dx)) * e.canaux;
    if (e.canaux === 3) {
      if (!e.px[i] && !e.px[i + 1] && !e.px[i + 2]) return null;
      if (couche.dec.enc === 'wind8' && (!e.px[i] || !e.px[i + 1])) return null;
      val.push(decoderVent(e.px[i], e.px[i + 1], e.px[i + 2], couche.dec));
    } else {
      if (!e.px[i]) return null;
      val.push([decoderScalaire(e.px[i], couche.dec)]);
    }
  }
  const n = val[0].length, out = [];
  for (let k = 0; k < n; k++) {
    out.push((val[0][k] * (1 - tx) + val[1][k] * tx) * (1 - ty) + (val[2][k] * (1 - tx) + val[3][k] * tx) * ty);
  }
  return out;
}

/** Valeur au point pour un fichier : cherche la meilleure tuile déjà chargée (zoom décroissant). */
export function lireAuPoint(urlFichier, couche, lng, lat, zmax, zmin) {
  const [mx, my] = mercator(lng, lat);
  for (let z = zmax; z >= zmin; z--) {
    const n = 2 ** z, x = Math.floor(mx * n), y = Math.floor(my * n);
    const e = cache.get(`${urlFichier}|${z}/${x}/${y}`);
    if (e && e.etat === 'pret') {
      toucher(e.cle, e);
      return { valeur: bilineaire(e, couche, mx * n - x, my * n - y), z };
    }
    if (e && e.etat === 'absent') return { valeur: null, z };
  }
  return null; // rien de chargé
}

export function entreeCache(urlFichier, z, x, y) { return cache.get(`${urlFichier}|${z}/${x}/${y}`); }
export { mercator };
