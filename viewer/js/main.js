// SPDX-License-Identifier: EUPL-1.2
// Page de visualisation RuneRide Météo (PoC J1) : carte MapLibre + calque WebGL de données.
import { COUCHES, TUILE, couleurPalette, paramsDecodage } from './config.js';
import { CoucheDonnees, tuilesVisibles } from './couche-gl.js';
import { SourceCompteuse, archive, demanderTuile, entreeCache, lireAuPoint, mercator, stats, surTuile } from './donnees.js';
import { Surcouche } from './fleches.js';

const T0 = performance.timeOrigin; // début de navigation, pour les mesures
const $ = id => document.getElementById(id);
const params = new URLSearchParams(location.search);
const BENCH = params.has('bench');
if (params.get('ui') === '0') document.documentElement.classList.add('sans-ui');
const mesures = { nom: params.get('nom') || 'mesures', date: new Date().toISOString(), jalons: {} };
const jalon = (nom) => { mesures.jalons[nom] = Math.round((performance.now()) * 10) / 10; };

// ---------------------------------------------------------------- état
const MONTAGES = { reduit2: '/data-reduit2', data: '/data', reduit: '/data-reduit', synth: '/data-synth', sans: '/data-sans', brute: '/data-brute' };
const ZOOMS_MODELE = { 'arome-hd': [5, 8], arome: [3, 7] };
const etat = {
  base: '', index: null, modele: 'arome-hd', tirage: null,
  couche: COUCHES[0], t: 0, lecture: false, vitesse: 1, point: null, zoomsFichier: new Map(),
};
const pad2 = n => String(n).padStart(2, '0');
const urlFichier = (variable, step) => `${etat.base}/${etat.modele}/${etat.tirage}/${variable}/${pad2(step)}.pmtiles`;
const echeances = (couche) => (etat.index?.[etat.modele]?.[etat.tirage]?.[couche.variable]) || [];

/** Pour un instant t (en heures depuis le tirage) : échéances A, B voisines et poids de mélange. */
function resoudreTemps(couche, t) {
  const S = echeances(couche);
  if (!S.length) return null;
  const tc = Math.min(S[S.length - 1], Math.max(S[0], t));
  let a = S[0];
  for (const s of S) if (s <= tc) a = s;
  const b = S.includes(a + 1) ? a + 1 : null;
  return { a, b, mix: b !== null ? Math.min(1, tc - a) : 0, S, tc };
}

// ---------------------------------------------------------------- inventaire des données
async function choisirSource() {
  const demande = { reduit2: 'reduit2', reduit: 'reduit', contrat: 'data' }[params.get('profil')] || params.get('src') || 'auto';
  const essais = demande === 'auto' ? ['reduit2', 'data', 'synth'] : [demande];
  for (const nom of essais) {
    try {
      const idx = await (await fetch(MONTAGES[nom] + '/index.json')).json();
      const modeles = Object.keys(idx);
      if (modeles.length) {
        etat.base = MONTAGES[nom]; etat.index = idx; etat.srcNom = nom;
        etat.modele = idx[params.get('modele')] ? params.get('modele') : (idx['arome-hd'] ? 'arome-hd' : modeles[0]);
        // tirage par défaut : celui qui a le plus d'échéances de température (puis le plus récent)
        const nb = r => (idx[etat.modele][r].t2m || []).length;
        const tirages = Object.keys(idx[etat.modele]).sort();
        etat.tirage = params.get('run') || tirages.reduce((m, r) => nb(r) >= nb(m) ? r : m, tirages[0]);
        return;
      }
    } catch (e) { console.warn('source', nom, e); }
  }
  throw new Error('aucune tuile trouvée (lancez le tuilage ou viewer/tools/synth_tiles.py)');
}

// ---------------------------------------------------------------- carte
// Fond de carte : Protomaps (OSM) en PMTiles local, style @protomaps/basemaps, thème sombre par défaut (?theme=clair),
// libellés en français. ?fond=0 : fond uni seulement (pour mesurer la part du fond).
const OPACITE = Number(params.get('opacite') || 0.85);
const AVEC_FOND = params.get('fond') !== '0';
const THEME = ['clair', 'light'].includes(params.get('theme')) ? 'light' : 'dark';
const URL_FOND = new URL('/basemap/domaine-z9.pmtiles', location.href).href;
// couches du fond placées AU-DESSUS de la couche météo : routes, frontières, libellés, points d'intérêt
// (terre, occupation du sol, eau et rivières restent EN DESSOUS : la météo se lit aussi sur la mer ; pistes d'aéroport sous la météo)
const RE_HAUT = /^(roads_(?!runway|taxiway)|boundaries|places_|pois|water_label|water_waterway|earth_label|address|buildings)/;
function construireStyle() {
  const fond = THEME === 'dark' ? '#101214' : '#f0efe9';
  if (!AVEC_FOND) return { version: 8, sources: {}, layers: [{ id: 'fond', type: 'background', paint: { 'background-color': fond } }] };
  const protocole = new pmtiles.Protocol();
  maplibregl.addProtocol('pmtiles', protocole.tile);
  protocole.add(new pmtiles.PMTiles(new SourceCompteuse(URL_FOND, 'fond')));
  return {
    version: 8, name: 'RuneRide ' + THEME,
    glyphs: 'https://protomaps.github.io/basemaps-assets/fonts/{fontstack}/{range}.pbf',
    sprite: 'https://protomaps.github.io/basemaps-assets/sprites/v4/' + THEME,
    sources: { protomaps: { type: 'vector', url: 'pmtiles://' + URL_FOND, attribution: '' } },
    layers: basemaps.layers('protomaps', basemaps.namedFlavor(THEME), { lang: 'fr' }),
  };
}
/** Identifiant de la première couche du fond qui doit rester au-dessus de la météo (null = tout en haut). */
function idPremiereCoucheHaute() {
  const l = map.getStyle().layers.find(q => RE_HAUT.test(q.id));
  return l ? l.id : undefined;
}
let map, couchegl, surcouche;

function creerCarte() {
  map = new maplibregl.Map({
    container: 'carte', style: construireStyle(), attributionControl: false, minZoom: 2.5, maxZoom: 10.5,
    bounds: [[-6, 41], [10.5, 51.5]], fitBoundsOptions: { padding: { top: 110, bottom: 190, left: 10, right: 10 } },
    pitchWithRotate: false, dragRotate: false, touchPitch: false, maxPitch: 0, fadeDuration: 0,
    canvasContextAttributes: { antialias: false },
  });
  map.touchZoomRotate.disableRotation();
  couchegl = new CoucheDonnees();
  surcouche = new Surcouche($('surcouche'), map);
  return new Promise(res => map.on('load', res));
}

// ---------------------------------------------------------------- mise à jour de l'affichage
function zoomsDe(couche) {
  const s = echeances(couche);
  const z = s.length && etat.zoomsFichier.get(urlFichier(couche.variable, s[0]));
  return z || ZOOMS_MODELE[etat.modele] || [3, 8];
}
function zoneZ() { const [a, b] = zoomsDe(etat.couche); return Math.max(a, Math.min(b, Math.round(map.getZoom() + 1))); }

let dernierPre = -1;
function appliquer() {
  const c = etat.couche, r = resoudreTemps(c, etat.t);
  if (!r) return;
  const [zmin, zmax] = zoomsDe(c);
  const urlA = urlFichier(c.variable, r.a), urlB = r.b !== null ? urlFichier(c.variable, r.b) : null;
  couchegl.definir({ couche: c, urlA, urlB, mix: r.mix, opacite: etat.opacite ?? OPACITE, zmin, zmax, debug: etat.debug, sansBordure: etat.sansBordure });
  etat.cur = { urlA, urlB, mix: r.mix, zmin, zmax, r };
  majHeure(r);
  surcouche.planifier(etatSurcouche());
  majInfo();
  if (r.a !== dernierPre && etat.pretOuverture) { dernierPre = r.a; precharger(c, r); }
}
const etatSurcouche = () => ({ couche: etat.couche, urlA: etat.cur?.urlA, urlB: etat.cur?.urlB, mix: etat.cur?.mix || 0,
  zoneZ: zoneZ(), zmin: etat.cur?.zmin || 3, point: etat.point, fleches: !etat.sansFleches });

/** Précharge les tuiles visibles des 3 prochaines échéances (spec §6.5 : ±3 h). */
function precharger(c, r) {
  const [zmin, zmax] = zoomsDe(c);
  const tv = tuilesVisibles(map, zmin, zmax);
  for (let k = 1; k <= 3; k++) {
    const s = r.a + k;
    if (!r.S.includes(s)) continue;
    for (const [z, x, y] of tv) demanderTuile(urlFichier(c.variable, s), z, x, y, c);
  }
}

const fmtJour = new Intl.DateTimeFormat('fr-FR', { weekday: 'short', day: 'numeric', month: 'short' });
const fmtHeure = new Intl.DateTimeFormat('fr-FR', { hour: '2-digit', minute: '2-digit' });
const fmtUTC = new Intl.DateTimeFormat('fr-FR', { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' });
function majHeure(r) {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2})Z$/.exec(etat.tirage);
  const debut = m ? Date.UTC(+m[1], m[2] - 1, +m[3], +m[4]) : Date.now();
  const d = new Date(debut + r.tc * 3600e3);
  $('heure-locale').textContent = `${fmtJour.format(d)} · ${fmtHeure.format(d)}`;
  $('heure-detail').textContent = `+${r.tc.toFixed(1).replace('.', ',')} h · ${fmtUTC.format(d)} UTC · tirage ${etat.tirage.slice(11, 13)} h`;
  const f = $('frise');
  if (document.activeElement !== f || etat.lecture) f.value = r.tc;
}

const DIRS = ['N', 'NE', 'E', 'SE', 'S', 'SO', 'O', 'NO'];
function majInfo() {
  const box = $('info');
  if (!etat.point || !etat.cur) { box.hidden = true; return; }
  const { lng, lat } = etat.point, c = etat.couche, cur = etat.cur;
  const a = lireAuPoint(cur.urlA, c, lng, lat, zoneZ(), cur.zmin);
  let b = cur.urlB && cur.mix > 0 ? lireAuPoint(cur.urlB, c, lng, lat, zoneZ(), cur.zmin) : null;
  box.hidden = false;
  const pos = `${lat.toFixed(2)}°N ${lng.toFixed(2)}°E`;
  let va = a && a.valeur;
  if (va && b && b.valeur) va = va.map((x, i) => x + (b.valeur[i] - x) * cur.mix);
  else if (!va && b && b.valeur) va = b.valeur;
  if (!a) { $('info-valeur').textContent = '…'; $('info-detail').textContent = pos + ' · chargement'; return; }
  if (!va) { $('info-valeur').textContent = '—'; $('info-detail').textContent = pos + ' · pas de donnée'; return; }
  if (c.dec.enc !== 'scalar8') {
    const [u, v] = va, vit = Math.hypot(u, v), dir = (270 - Math.atan2(v, u) * 180 / Math.PI + 360) % 360;
    $('info-valeur').textContent = `${(vit * 3.6).toFixed(0)} km/h`;
    $('info-detail').textContent = `${vit.toFixed(1)} m/s · ${(vit * 1.944).toFixed(0)} nd · de ${DIRS[Math.round(dir / 45) % 8]} ${dir.toFixed(0)}°  ${pos}`;
  } else {
    const d = va[0] * c.aff.mul + c.aff.add;
    $('info-valeur').textContent = `${d.toFixed(c.aff.dec).replace('.', ',')} ${c.aff.unite}`;
    $('info-detail').textContent = `${c.nom} · ${pos}`;
  }
}

// ---------------------------------------------------------------- légende
function majLegende() {
  const c = etat.couche, leg = c.legende;
  $('leg-titre').textContent = `${c.nom} (${c.aff.unite})`;
  const cv = $('leg-barre'), ctx = cv.getContext('2d');
  const img = ctx.createImageData(256, 1);
  for (let i = 0; i < 256; i++) {
    const col = couleurPalette(c.palette, i / 255), a = col[3] / 255, k = i * 4;
    img.data[k] = col[0]; img.data[k + 1] = col[1]; img.data[k + 2] = col[2]; img.data[k + 3] = Math.round(255 * Math.max(a, 0.0));
  }
  ctx.putImageData(img, 0, 0);
  const ticks = $('leg-ticks'); ticks.textContent = '';
  for (const v of leg.ticks) {
    let f = (v - leg.lo) / (leg.hi - leg.lo);
    if (leg.axe === 'sqrt') f = Math.sqrt(Math.max(0, f));
    const s = document.createElement('span');
    s.style.left = (Math.min(1, Math.max(0, f)) * 100) + '%';
    s.textContent = String(v).replace('.', ',');
    ticks.appendChild(s);
  }
}

// ---------------------------------------------------------------- interface
function construireCouches() {
  const nav = $('couches');
  nav.textContent = '';
  for (const c of COUCHES) {
    const b = document.createElement('button');
    b.textContent = c.nom; b.dataset.cle = c.cle;
    b.disabled = echeances(c).length === 0;
    b.setAttribute('aria-pressed', String(c === etat.couche));
    b.addEventListener('click', () => choisirCouche(c));
    nav.appendChild(b);
  }
}
async function choisirCouche(c) {
  if (!echeances(c).length) return;
  etat.couche = c;
  for (const b of $('couches').children) {
    b.setAttribute('aria-pressed', String(b.dataset.cle === c.cle));
    if (b.dataset.cle === c.cle) b.scrollIntoView({ inline: 'nearest', block: 'nearest' });
  }
  await lireEntetes(c);
  const S = echeances(c);
  const f = $('frise'); f.min = S[0]; f.max = S[S.length - 1];
  etat.t = Math.min(S[S.length - 1], Math.max(S[0], etat.t));
  majLegende();
  dernierPre = -1;
  appliquer();
  ecrireHash();
}
/** Lit l'en-tête PMTiles (zooms) de la 1re échéance : même requête initiale que les tuiles, mise en cache. */
async function lireEntetes(c) {
  const url = urlFichier(c.variable, echeances(c)[0]);
  if (etat.zoomsFichier.has(url) && c.dec) return;
  const a = archive(url);
  const [h, meta] = await Promise.all([a.getHeader(), a.getMetadata().catch(() => null)]);
  // ?decodage=ancien : formule min/max du contrat initial (sert à la non-régression et à montrer l'erreur sur reduit)
  c.dec = paramsDecodage(c, params.get('decodage') === 'ancien' ? null : meta);   // quant_step, levels, max, encoding : lus dans les métadonnées (contrat révision 2)
  etat.zoomsFichier.set(url, [h.minZoom, h.maxZoom]);
}

function lecture(on) {
  etat.lecture = on;
  $('lecture').textContent = on ? '❚❚' : '▶';
  $('lecture').setAttribute('aria-label', on ? 'Pause' : 'Lecture');
  if (on) { dernierT = performance.now(); requestAnimationFrame(boucle); }
}
let dernierT = 0;
function boucle(now) {
  if (!etat.lecture) return;
  const dt = (now - dernierT) / 1000; dernierT = now;
  const S = echeances(etat.couche), lo = S[0], hi = S[S.length - 1];
  etat.t += dt * etat.vitesse; // ×1 = 1 h de prévision par seconde
  if (etat.t > hi) etat.t = lo + (etat.t - hi);
  appliquer();
  requestAnimationFrame(boucle);
}

function ecrireHash() {
  const c = map.getCenter();
  history.replaceState(null, '', location.search + `#couche=${etat.couche.cle}&t=${etat.t.toFixed(2)}&c=${c.lat.toFixed(3)},${c.lng.toFixed(3)}&z=${map.getZoom().toFixed(2)}`);
}
function lireHash() {
  const h = new URLSearchParams(location.hash.slice(1));
  return { couche: h.get('couche'), t: h.has('t') ? parseFloat(h.get('t')) : null,
    c: h.get('c')?.split(',').map(Number), z: h.has('z') ? parseFloat(h.get('z')) : null };
}

function brancherInterface() {
  // la légende se place juste au-dessus de la barre du bas, quelle que soit sa hauteur (mobile : attribution sur 2 lignes)
  new ResizeObserver(() => document.documentElement.style.setProperty('--h-bas', $('bas').offsetHeight + 'px')).observe($('bas'));
  $('lecture').addEventListener('click', () => lecture(!etat.lecture));
  $('vitesse').addEventListener('click', () => {
    etat.vitesse = etat.vitesse === 1 ? 2 : etat.vitesse === 2 ? 4 : 1;
    $('vitesse').textContent = '×' + etat.vitesse;
  });
  $('frise').addEventListener('input', e => { etat.t = parseFloat(e.target.value); appliquer(); });
  $('frise').addEventListener('change', ecrireHash);
  $('info-fermer').addEventListener('click', () => { etat.point = null; majInfo(); surcouche.planifier(etatSurcouche()); });
  map.on('mousemove', e => { if (!etat.point?.epingle) { etat.point = { lng: e.lngLat.lng, lat: e.lngLat.lat, epingle: false }; majInfo(); } });
  map.on('click', e => { etat.point = { lng: e.lngLat.lng, lat: e.lngLat.lat, epingle: true }; majInfo(); surcouche.planifier(etatSurcouche()); });
  map.on('move', () => surcouche.planifier(etatSurcouche()));
  map.on('moveend', () => { if (etat.pretOuverture && etat.cur) precharger(etat.couche, etat.cur.r); ecrireHash(); });
  window.addEventListener('resize', () => { surcouche.redimensionner(); surcouche.planifier(etatSurcouche()); });
  window.addEventListener('keydown', e => {
    if (e.target.tagName === 'BUTTON' && e.key === ' ') return;
    const pas = e.shiftKey ? 6 : 1, S = echeances(etat.couche);
    if (e.key === ' ') { lecture(!etat.lecture); e.preventDefault(); }
    else if (e.key === 'ArrowRight') { etat.t = Math.min(S[S.length - 1], Math.round(etat.t) + pas); appliquer(); }
    else if (e.key === 'ArrowLeft') { etat.t = Math.max(S[0], Math.round(etat.t) - pas); appliquer(); }
    else if (e.key === 'Home') { etat.t = S[0]; appliquer(); }
    else if (e.key === 'End') { etat.t = S[S.length - 1]; appliquer(); }
  });
}

// ---------------------------------------------------------------- attente « tout est chargé »
function toutCharge() {
  const cur = etat.cur;
  if (!cur) return false;
  for (const [z, x, y] of tuilesVisibles(map, cur.zmin, cur.zmax || 8)) {
    for (const u of [cur.urlA, cur.urlB]) {
      if (!u) continue;
      const e = entreeCache(u, z, x, y);
      if (!e || e.etat === 'charge') return false;
    }
  }
  return true;
}
const attendre = ms => new Promise(r => setTimeout(r, ms));
async function attendreCharge(max = 20000) {
  const t = performance.now();
  while (performance.now() - t < max) {
    await new Promise(r => requestAnimationFrame(r));
    if (toutCharge() && couchegl.derniere.tuiles > 0) { await attendre(120); if (toutCharge()) return true; }
    await attendre(50);
  }
  return false;
}

// ---------------------------------------------------------------- API pour les captures et le banc d'essai
window.__app = {
  get surcouche() { return surcouche; },
  carte() { return map; },
  etat,
  async aller({ couche, t, centre, zoom }) {
    if (centre || zoom != null) map.jumpTo({ center: centre ? [centre[1], centre[0]] : map.getCenter(), zoom: zoom ?? map.getZoom() });
    if (couche) await choisirCouche(COUCHES.find(c => c.cle === couche));
    if (t != null) { etat.t = t; appliquer(); }
    const ok = await attendreCharge();
    appliquer();   // redessine la surcouche avec l'état final, puis laisse deux images se rendre
    await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
    return ok;
  },
  /** Charge TOUTE la frise (toutes les échéances de la couche) pour les tuiles visibles et renvoie les octets/requêtes mesurés. */
  async mesurerFrise(cleCouche) {
    const c = COUCHES.find(q => q.cle === cleCouche);
    await choisirCouche(c); await attendreCharge();
    const [zmin, zmax] = zoomsDe(c), tv = tuilesVisibles(map, zmin, zmax);
    const o0 = stats.octetsDonnees, r0 = stats.requetesDonnees, t0 = performance.now(), S = echeances(c);
    let pas = 0;
    for (let i = 0; i < S.length; i += 6) {
      await Promise.all(S.slice(i, i + 6).flatMap(s => tv.map(([z, x, y]) => demanderTuile(urlFichier(c.variable, s), z, x, y, c).promesse)));
      pas++;
    }
    return { couche: cleCouche, echeances: S.length, tuiles_visibles_par_echeance: tv.length, octets: stats.octetsDonnees - o0,
      requetes: stats.requetesDonnees - r0, ms: Math.round(performance.now() - t0), z: tv[0][0] };
  },
  fleches(on) { etat.sansFleches = !on; surcouche.planifier(etatSurcouche()); },
  /** Alignement fond / météo : sur une bande de lignes traversant une côte, position de la plus forte variation du fond
   *  (eau/terre, couche météo à opacité 0) et de la couche t2m rendue en niveaux de gris (mode debug), en pixels écran. */
  async alignement(centre, zoom, t = 3, demiBande = 20) {
    await this.aller({ couche: 't2m', t, centre, zoom });
    const lire = () => new Promise(res => { map.once('render', () => {
      const cv = map.getCanvas(), q = document.createElement('canvas'); q.width = cv.width; q.height = cv.height;
      const g = q.getContext('2d', { willReadFrequently: true }); g.drawImage(cv, 0, 0); res(g.getImageData(0, 0, q.width, q.height));
    }); map.triggerRepaint(); });
    etat.opacite = 0; appliquer(); await attendreCharge(); const base = await lire();
    etat.opacite = 1; etat.debug = true; appliquer(); await attendreCharge(); const meteo = await lire();
    etat.debug = false; etat.opacite = OPACITE; appliquer();
    const W = base.width, H = base.height, y0 = Math.floor(H / 2) - demiBande, y1 = Math.floor(H / 2) + demiBande;
    const profil = img => { const p = new Float64Array(W); for (let y = y0; y < y1; y++) for (let x = 0; x < W; x++) { const i = (y * W + x) * 4; p[x] += (img.data[i] * 0.3 + img.data[i + 1] * 0.59 + img.data[i + 2] * 0.11) / (y1 - y0); } return p; };
    const pic = p => { let m = 0, xm = 0; for (let x = 3; x < W - 3; x++) { const d = Math.abs(p[x + 2] - p[x - 2]); if (d > m) { m = d; xm = x; } } return { x: xm, amplitude: +m.toFixed(1) }; };
    const b = pic(profil(base)), m = pic(profil(meteo));
    return { zoom, fond_x: b.x, meteo_x: m.x, ecart_px: m.x - b.x, ampl_fond: b.amplitude, ampl_meteo: m.amplitude, dpr: W / map.getContainer().clientWidth };
  },
  /** Alignement géométrique sur les tuiles SYNTHÉTIQUES (disque sans donnée centré 2°E 49°N, rayon 1,1° en lon/lat) :
   *  bord gauche attendu à lon 0,9° sur la ligne lat 49° ; comparé à la position projetée par MapLibre (map.project). */
  async alignementSynth(zoom) {
    await this.aller({ couche: 't2m', t: 2, centre: [49.0, 0.9], zoom });
    etat.opacite = 1; etat.debug = true; appliquer(); await attendreCharge();
    const img = await new Promise(res => { map.once('render', () => {
      const cv = map.getCanvas(), q = document.createElement('canvas'); q.width = cv.width; q.height = cv.height;
      const g = q.getContext('2d', { willReadFrequently: true }); g.drawImage(cv, 0, 0); res(g.getImageData(0, 0, q.width, q.height));
    }); map.triggerRepaint(); });
    etat.debug = false; etat.opacite = OPACITE; appliquer();
    const d = img.width / map.getContainer().clientWidth, yv = map.project([0.9, 49.0]).y, xv = map.project([0.9, 49.0]).x;
    const y = Math.round(yv * d), W = img.width;
    // trou = pixels de fond (#101214 -> gris 16..20) ; on cherche la transition donnée -> trou en parcourant la ligne
    const gris = x => img.data[(y * W + x) * 4];
    let bord = null;
    for (let x = 5; x < W - 5; x++) if (gris(x - 1) > 40 && gris(x) < 30 && gris(x + 1) < 30 && gris(x + 3) < 30) { bord = x; break; }
    const pixelDonnee = 360 / (256 * 2 ** Math.min(8, Math.round(zoom + 1))) * (512 * 2 ** zoom / 360) / 1;   // pixels écran d'un pixel de donnée
    return { zoom, attendu_px: +(xv * d).toFixed(1), mesure_px: bord, ecart_px: bord === null ? null : +(bord - xv * d).toFixed(1), pixel_donnee_en_px_ecran: +(pixelDonnee * d).toFixed(1) };
  },
  opacite(o) { etat.opacite = o; appliquer(); },
  epingler(lat, lng) { etat.point = { lat, lng, epingle: true }; majInfo(); surcouche.planifier(etatSurcouche()); },
  pret: attendreCharge,
  /** Compare le décodage CPU du viewer à des valeurs de référence (décodeur Python) : pts = [{url, variable, lng, lat, z, ref:[..]}] */
  async comparer(pts) {
    const res = {};
    const metas = {};
    for (const q of pts) {
      if (!metas[q.url]) {
        const m = await archive(q.url).getMetadata();
        const base = COUCHES.find(c => c.variable === q.variable) || { enc: m.encoding, min: m.min, max: m.max, echelle: m.scale };
        metas[q.url] = { dec: paramsDecodage(base, m) };
      }
      const [lng, lat] = [q.lng, q.lat], n = 2 ** q.z, [mx, my] = mercator(lng, lat);
      const e = demanderTuile(q.url, q.z, Math.floor(mx * n), Math.floor(my * n), metas[q.url]);
      await e.promesse;
      const r = lireAuPoint(q.url, metas[q.url], lng, lat, q.z, q.z);
      const v = r && r.valeur;
      const ecart = v ? Math.max(...v.map((x, i) => Math.abs(x - q.ref[i]))) : null;
      (res[q.variable] ||= []).push(ecart);
    }
    const out = {};
    for (const [k, a] of Object.entries(res)) {
      const ok = a.filter(x => x !== null);
      out[k] = { n: ok.length, sans_valeur: a.length - ok.length, ecart_max: Math.max(...ok) };
    }
    return out;
  },
  /** Vérifie le shader contre le décodage CPU : n points au hasard, comparaison de la position dans la rampe. */
  /** Couture entre tuiles : rend la couche en niveaux de gris (position dans la rampe) avec puis sans la bordure
   *  d'1 px (clamp aux bords utiles) et compare les deux images pixel à pixel. */
  async couture(couche, centre, zoom, t) {
    await this.aller({ couche, centre, zoom, t });
    const lire = () => new Promise(res => { map.once('render', () => {
      const cv = map.getCanvas(), q = document.createElement('canvas'); q.width = cv.width; q.height = cv.height;
      const g = q.getContext('2d', { willReadFrequently: true }); g.drawImage(cv, 0, 0); res(g.getImageData(0, 0, q.width, q.height));
    }); map.triggerRepaint(); });
    etat.debug = true; etat.sansBordure = false; appliquer(); await attendreCharge(); const avec = await lire();
    etat.sansBordure = true; appliquer(); await attendreCharge(); const sans = await lire();
    etat.debug = false; etat.sansBordure = false; appliquer();
    const W = avec.width, H = avec.height, A = avec.data, B = sans.data;
    const d = new Uint8Array(W * H); let nb = 0, max = 0, somme = 0, valides = 0;
    const colonnes = new Map(), lignes = new Map();
    for (let i = 0; i < W * H; i++) {
      if (A[i * 4 + 3] !== 255) continue;
      valides++;
      const e = Math.abs(A[i * 4] - B[i * 4]);
      if (e) { nb++; somme += e; if (e > max) max = e; const x = i % W, y = (i / W) | 0;
        colonnes.set(x, (colonnes.get(x) || 0) + 1); lignes.set(y, (lignes.get(y) || 0) + 1); }
    }
    const top = m => [...m.entries()].sort((a, b) => b[1] - a[1]).slice(0, 6);
    return { couche, zoom, pixels_vus: valides, pixels_differents: nb, part_pct: +(100 * nb / valides).toFixed(3), ecart_max_niveaux: max,
      ecart_moyen_niveaux_sur_differents: +(somme / (nb || 1)).toFixed(2), colonnes_les_plus_touchees: top(colonnes), lignes_les_plus_touchees: top(lignes), echelle_px_par_pixel_donnee: +(2 ** (zoom + 1) * 256 / 2 ** 8 / 256 * 256 / 256 * 1).toFixed(2) };
  },
  async verifierShader(n = 40) {
    etat.debug = true; appliquer(); await attendreCharge();
    const c = etat.couche, cur = etat.cur, leg = c.legende, W = map.getCanvas().width / map.getContainer().clientWidth;
    const lecture = await new Promise(res => { map.once('render', () => {
      const cv = map.getCanvas(), t = document.createElement('canvas'); t.width = cv.width; t.height = cv.height;
      const g = t.getContext('2d'); g.drawImage(cv, 0, 0); res(g);
    }); map.triggerRepaint(); });
    const w = map.getContainer().clientWidth, h = map.getContainer().clientHeight, out = [];
    let tries = 0;
    while (out.length < n && tries++ < n * 40) {
      const x = Math.floor(10 + Math.random() * (w - 20)) + 0.5, y = Math.floor(120 + Math.random() * (h - 320)) + 0.5, ll = map.unproject([x, y]);
      const a = lireAuPoint(cur.urlA, c, ll.lng, ll.lat, zoneZ(), cur.zmin);
      if (!a || !a.valeur) continue;
      let v = a.valeur;
      if (cur.urlB && cur.mix > 0) { const b = lireAuPoint(cur.urlB, c, ll.lng, ll.lat, zoneZ(), cur.zmin); if (!b || !b.valeur) continue; v = v.map((q, i) => q + (b.valeur[i] - q) * cur.mix); }
      const x0 = c.dec.enc !== 'scalar8' ? Math.hypot(v[0], v[1]) : v[0];
      if (x0 * c.aff.mul + c.aff.add < c.seuil) continue;   // sous le seuil d'affichage : transparent par construction
      let tt = Math.min(1, Math.max(0, (x0 * c.aff.mul + c.aff.add - leg.lo) / (leg.hi - leg.lo)));
      if (leg.axe === 'sqrt') tt = Math.sqrt(tt);
      const px = lecture.getImageData(Math.floor(x * W), Math.floor(y * W), 1, 1).data;
      if (px[3] !== 255) continue;                  // pas de donnée au GPU
      out.push({ cpu: tt, gpu: px[0] / 255 });
    }
    etat.debug = false; appliquer();
    const err = out.map(o => Math.abs(o.cpu - o.gpu));
    const tri = err.slice().sort((a, b) => a - b);
    return { couche: c.cle, t: etat.t, n: out.length, ecart_max: Math.max(...err), ecart_p95: tri[Math.floor(tri.length * 0.95)], ecart_median: tri[Math.floor(tri.length / 2)], lsb: 1 / 255 };
  },
  mesures,
};

// ---------------------------------------------------------------- banc d'essai (?bench=1)
async function banc() {
  const rep = (cle, v) => { mesures[cle] = v; };
  await attendreCharge();
  await attendre(300);
  // contrôle des métadonnées PMTiles (hors compte d'ouverture : phase séparée)
  stats.phase = 'verif';
  try {
    const m = await archive(etat.cur.urlA).getMetadata();
    rep('metadonnees', m);
  } catch (e) { rep('metadonnees', String(e)); }
  // animation : vent, ×4, 8 s, on compte les images réellement rendues par MapLibre
  stats.phase = 'animation';
  const dur = Number(params.get('duree') || 8000);
  await choisirCouche(COUCHES.find(c => c.cle === (params.get('couche') || 'vent')));
  etat.vitesse = 4; etat.t = echeances(etat.couche)[0]; appliquer();
  await attendreCharge();
  const ts = []; const surRendu = () => ts.push(performance.now());
  map.on('render', surRendu);
  const o0 = stats.octetsDonnees, r0 = stats.requetesDonnees;
  const t0 = performance.now();
  lecture(true);
  await attendre(dur);
  lecture(false);
  map.off('render', surRendu);
  const dt = (performance.now() - t0) / 1000;
  const iv = ts.slice(1).map((x, i) => x - ts[i]).sort((a, b) => a - b);
  rep('animation', { duree_s: +dt.toFixed(2), images: ts.length, fps_moyen: +(ts.length / dt).toFixed(1),
    intervalle_median_ms: +(iv[Math.floor(iv.length / 2)] || 0).toFixed(1), intervalle_p95_ms: +(iv[Math.floor(iv.length * 0.95)] || 0).toFixed(1),
    octets_pendant: stats.octetsDonnees - o0, requetes_pendant: stats.requetesDonnees - r0, fleches: surcouche.nbFleches });
  rep('tuiles_ms', { n: stats.ms.length, moyen: +(stats.ms.reduce((a, b) => a + b, 0) / (stats.ms.length || 1)).toFixed(1) });
  rep('fond', { actif: AVEC_FOND, ...stats.fond });
  rep('total', { octets: stats.octetsDonnees, requetes: stats.requetesDonnees, par_phase: stats.parPhase });
  // poids des bibliothèques et du fond de carte (ressources externes)
  rep('ressources', performance.getEntriesByType('resource').filter(e => !e.name.includes('.pmtiles'))
    .map(e => ({ url: e.name.replace(location.origin, ''), transfert: e.transferSize, corps: e.encodedBodySize })));
  rep('webgl', (() => { const g = map.painter.context.gl, d = g.getExtension('WEBGL_debug_renderer_info');
    return d ? g.getParameter(d.UNMASKED_RENDERER_WEBGL) : 'inconnu'; })());
  rep('ua', navigator.userAgent);
  finBanc();
}
function finBanc() {
  const pre = $('mesures'); pre.hidden = false; pre.textContent = JSON.stringify(mesures, null, 1);
  pre.dataset.pret = '1';
  fetch('/__mesures', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(mesures) }).catch(() => {});
}

// ---------------------------------------------------------------- démarrage
async function demarrer() {
  jalon('module_charge');
  await choisirSource();
  jalon('index_lu');
  const h = lireHash();
  const depart = COUCHES.find(c => c.cle === (h.couche || params.get('couche') || 'vent') && echeances(c).length) || COUCHES.find(c => echeances(c).length);
  etat.couche = depart;
  if (h.t != null) etat.t = h.t;
  construireCouches();
  $('modele').textContent = etat.modele === 'arome-hd' ? 'AROME HD' : 'AROME';
  $('attrib-src').textContent = `· tuiles : ${etat.srcNom === 'data' ? 'profil contrat' : etat.srcNom === 'reduit' ? 'profil reduit' : etat.srcNom === 'reduit2' ? 'profil reduit2' : 'SYNTHÉTIQUES (' + etat.srcNom + ')'}`;
  await creerCarte();
  jalon('carte_prete');
  surcouche.redimensionner();
  if (h.c && h.z != null) map.jumpTo({ center: [h.c[1], h.c[0]], zoom: h.z });
  await lireEntetes(depart);
  jalon('entete_lue');
  const S = echeances(depart);
  const f = $('frise'); f.min = S[0]; f.max = S[S.length - 1];
  etat.t = Math.min(S[S.length - 1], Math.max(S[0], etat.t));
  brancherInterface();
  surTuile(() => { map.triggerRepaint(); majInfo(); surcouche.planifier(etatSurcouche()); });
  couchegl.surPremiere = () => { requestAnimationFrame(() => { jalon('premiere_couche_visible'); mesures.premiere_couche_ms = mesures.jalons.premiere_couche_visible; }); };
  map.addLayer(couchegl, idPremiereCoucheHaute());
  map.once('idle', () => { jalon('carte_complete_idle'); });
  majLegende();
  appliquer();
  attendreCharge().then(() => {
    jalon('tout_charge_ouverture');
    mesures.ouverture = { octets: stats.octetsDonnees, requetes: stats.requetesDonnees, tuiles_chargees: stats.tuilesChargees,
      tuiles_absentes: stats.tuilesAbsentes, tuiles_dessinees: couchegl.derniere.tuiles, fond: { ...stats.fond } };
    etat.pretOuverture = true; stats.phase = 'precharge'; if (!BENCH) appliquer(); });
  if (BENCH) banc();
}
demarrer().catch(e => {
  console.error(e);
  const pre = $('mesures'); pre.hidden = false; pre.textContent = 'ERREUR : ' + e.message; pre.dataset.erreur = '1';
});
