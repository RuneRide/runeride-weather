// SPDX-License-Identifier: EUPL-1.2
// Page de visualisation RuneRide Météo : carte MapLibre + calque WebGL de données + particules de vent + isobares,
// pilotée par le manifest (choix automatique du modèle et du tirage, frise en temps valide UTC). Voir docs/poc-j3/integration.md.
import { COUCHES, TUILE, couleurPalette, paramsDecodage } from './config.js';
import { CoucheDonnees, tuilesVisibles } from './couche-gl.js';
import { SourceCompteuse, archive, demanderTuile, entreeCache, lireAuPoint, mercator, stats, surTuile } from './donnees.js';
import { Surcouche } from './fleches.js';
import { AROME, AROME_HD, attribution, choisirTirage, cheminFichier, libelleModele, plageTemps, resoudre, tirages } from './modele-auto.js';
import * as IL from './integration-logique.js';
import * as FT from './format-temps.js';
import { ParticulesVent } from './particules.js';
import { ajouterIsobares, echeanceCourante, majEcheance, retirerIsobares } from './isobares.js';

const $ = id => document.getElementById(id);
const params = new URLSearchParams(location.search);
const BENCH = params.has('bench');
if (params.get('ui') === '0') document.documentElement.classList.add('sans-ui');
const mesures = { nom: params.get('nom') || 'mesures', date: new Date().toISOString(), jalons: {} };
const jalon = (nom) => { mesures.jalons[nom] = Math.round((performance.now()) * 10) / 10; };
const H = 3600e3;
const FUSEAU = params.get('fuseau') || FT.FUSEAU;   // fuseau de l'heure locale (Europe/Paris par défaut)

// ---------------------------------------------------------------- état
const MONTAGES = { reduit2: '/data-reduit2', data: '/data', reduit: '/data-reduit', synth: '/data-synth', sans: '/data-sans', brute: '/data-brute' };
const ZOOMS_MODELE = { 'arome-hd': [5, 8], arome: [3, 7] };
const PRESSION = { cle: 'pression', nom: 'Pression', variable: null, pseudo: true };   // pas de raster : isobares seules
const etat = {
  base: '', manifest: null, mode: IL.normaliserMode(params.get('modele')),
  couche: COUCHES[0], tValide: 0, lecture: false, vitesse: 1, point: null, zoomsVar: new Map(),
  vue: null, precedent: null, affiche: null, desire: null, enAttente: false, changementCouche: false,
  particules: false, particulesExplicite: false, isobares: false, refT: 0,
};
const nomModele = m => (m === AROME_HD ? 'AROME HD' : 'AROME');

// ---------------------------------------------------------------- manifest
async function chargerManifest() {
  const demande = { reduit2: 'reduit2', reduit: 'reduit', contrat: 'data' }[params.get('profil')] || params.get('src') || 'auto';
  const essais = demande === 'auto' ? ['reduit2', 'data', 'synth'] : [demande];
  let manifest = null;
  try { manifest = await (await fetch('/manifest/manifest.json')).json(); } catch (e) { console.warn('manifest', e); }
  for (const nom of essais) {
    try {
      const idx = await (await fetch(MONTAGES[nom] + '/index.json')).json();
      if (!Object.keys(idx).length) continue;
      etat.base = MONTAGES[nom]; etat.srcNom = nom;
      // les jeux synthétiques n'ont pas de manifest : on le déduit de l'inventaire ; sinon le manifest du pipeline fait foi
      etat.manifest = (nom === 'synth' || nom === 'sans' || nom === 'brute' || !manifest) ? IL.manifestDepuisIndex(idx) : manifest;
      etat.refT = Math.min(...Object.keys(etat.manifest.models).flatMap(m => tirages(etat.manifest, m)).map(r => Date.parse(r.run)));
      return;
    } catch (e) { console.warn('source', nom, e); }
  }
  throw new Error('aucune tuile trouvée (lancez le tuilage ou viewer/tools/synth_tiles.py)');
}
/** Plage de temps valide de la couche active (ms UTC), selon le mode : { debut, fin }. */
function plage() {
  const c = etat.couche;
  if (c.pseudo) return IL.plageIsobares(etat.manifest) || { debut: etat.refT, fin: etat.refT };
  return (etat.mode !== 'auto' && plageTemps(etat.manifest, c.cle, { modele: etat.mode })) || plageTemps(etat.manifest, c.cle) || { debut: etat.refT, fin: etat.refT };
}

// ---------------------------------------------------------------- carte
// Fond de carte : Protomaps (OSM) en PMTiles local, style @protomaps/basemaps, thème sombre par défaut (?theme=clair),
// libellés en français. ?fond=0 : fond uni seulement (pour mesurer la part du fond).
const OPACITE = Number(params.get('opacite') || 0.85);
const AVEC_FOND = params.get('fond') !== '0';
const THEME = ['clair', 'light'].includes(params.get('theme')) ? 'light' : 'dark';
const URL_FOND = new URL('/basemap/domaine-z9.pmtiles', location.href).href;
// couches du fond placées AU-DESSUS de la météo : routes, frontières, libellés, points d'intérêt
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
/** Identifiant de la première couche du fond qui doit rester au-dessus de la météo (undefined = tout en haut). */
function idPremiereCoucheHaute() {
  const l = map.getStyle().layers.find(q => RE_HAUT.test(q.id));
  return l ? l.id : undefined;
}
let map, couchegl, surcouche, particules = null;

function creerCarte() {
  map = new maplibregl.Map({
    container: 'carte', style: construireStyle(), attributionControl: false, minZoom: 2.5, maxZoom: 10.5,
    bounds: [[-6, 41], [10.5, 51.5]], fitBoundsOptions: { padding: { top: 150, bottom: 190, left: 10, right: 10 } },
    pitchWithRotate: false, dragRotate: false, touchPitch: false, maxPitch: 0, fadeDuration: 0,
    canvasContextAttributes: { antialias: false },
  });
  map.touchZoomRotate.disableRotation();
  couchegl = new CoucheDonnees();
  surcouche = new Surcouche($('surcouche'), map);
  return new Promise(res => map.on('load', res));
}
/** Vue utilisée pour le choix du modèle : mise à jour en fin de geste seulement (hystérésis, pas à chaque image). */
function majVue() { etat.vue = { zoom: map.getZoom(), bounds: map.getBounds().toArray() }; }

// ---------------------------------------------------------------- résolution (modèle, tirage, fichiers)
const zoomsDe = (modele, variable) => etat.manifest.models?.[modele]?.zooms?.[variable] || etat.zoomsVar.get(`${modele}/${variable}`) || ZOOMS_MODELE[modele] || [3, 8];
const fichier = (t, step) => `${etat.base}/${cheminFichier(etat.manifest, t.modele, t.id, t.variable, step)}`;
/** Fichiers A, B et poids de mélange d'un tirage résolu. */
function fichiersDe(t) { return { urlA: fichier(t, t.step), urlB: t.stepSuivant != null ? fichier(t, t.stepSuivant) : null, mix: t.fraction }; }
/** Résolution complète pour la couche active à l'instant `tValide` (modèle D7 puis tirage D8, repli compris). */
function calculerDesire(tValide = etat.tValide) {
  const c = etat.couche;
  if (c.pseudo || !etat.vue) return null;
  return resoudre({ zoom: etat.vue.zoom, bounds: etat.vue.bounds, couche: c.cle, mode: etat.mode, manifest: etat.manifest, precedent: etat.precedent, tValide });
}
/** Les tuiles visibles de la résolution `res` sont-elles toutes là (ou absentes du fichier) ? Demande celles qui manquent. */
function pretPour(res) {
  const c = etat.couche, { urlA, urlB } = fichiersDe(res.tirage);
  const [zmin, zmax] = zoomsDe(res.modele, c.variable);
  let pret = true;
  for (const [z, x, y] of tuilesVisibles(map, zmin, zmax)) {
    for (const u of [urlA, urlB]) {
      if (!u) continue;
      const e = demanderTuile(u, z, x, y, c);
      if (e.etat === 'charge') pret = false;
    }
  }
  return pret;
}
function zoneZ() { const cur = etat.cur; const [a, b] = cur ? [cur.zmin, cur.zmax] : [3, 8]; return Math.max(a, Math.min(b, Math.round(map.getZoom() + 1))); }

let dernierPre = -1;
function appliquer() {
  const c = etat.couche;
  majHeure();
  if (c.pseudo) {
    couchegl.masquer(); etat.cur = null; etat.affiche = null; etat.desire = null; etat.enAttente = false;
    surcouche.planifier(etatSurcouche()); majInfo(); majAffichageModele(); majParticulesChamp(); majIsobares();
    return;
  }
  if (!c.dec) return;                      // paramètres de décodage pas encore lus (changement de couche en cours)
  const d = calculerDesire();
  etat.desire = d;
  if (!d) return;
  if (d.choix && d.choix.modele) etat.precedent = d.choix;
  if (!d.tirage) { majAffichageModele(); return; }               // instant non publié : on garde l'affichage
  // bascule sans clignotement : tant que les tuiles du nouveau calque ne sont pas là, l'ancien reste affiché
  const ancien = etat.affiche && etat.affiche.tirage ? { modele: etat.affiche.modele, tirage: choisirTirage({ modele: etat.affiche.modele, variable: c.variable, tValide: etat.tValide, manifest: etat.manifest }) } : null;
  const decision = IL.decisionBascule({
    affiche: etat.affiche, desire: d, pret: pretPour(d), changementCouche: etat.changementCouche,
    ancienValide: !!(ancien && ancien.tirage),
  });
  if (decision === 'echanger') { etat.affiche = d; etat.changementCouche = false; etat.enAttente = false; }
  else { etat.enAttente = true; }
  const res = etat.enAttente && ancien ? { ...etat.affiche, ...ancien } : etat.affiche;
  const t = res.tirage, f = fichiersDe(t), [zmin, zmax] = zoomsDe(res.modele, c.variable);
  couchegl.definir({ couche: c, urlA: f.urlA, urlB: f.urlB, mix: f.mix, opacite: etat.opacite ?? OPACITE, zmin, zmax, debug: etat.debug, sansBordure: etat.sansBordure });
  etat.cur = { urlA: f.urlA, urlB: f.urlB, mix: f.mix, zmin, zmax, res, modele: res.modele };
  surcouche.planifier(etatSurcouche());
  majInfo(); majAffichageModele(); majParticulesChamp(); majIsobares();
  if (t.step !== dernierPre && etat.pretOuverture) { dernierPre = t.step; precharger(c, res); }
}
const flechesVisibles = () => !etat.sansFleches && !(etat.particules && particules && particules.visibles);
const etatSurcouche = () => ({ couche: etat.couche, urlA: etat.cur?.urlA, urlB: etat.cur?.urlB, mix: etat.cur?.mix || 0,
  zoneZ: zoneZ(), zmin: etat.cur?.zmin || 3, point: etat.point, fleches: flechesVisibles() });

/** Précharge les tuiles visibles des 3 heures suivantes (spec §6.5), dans le modèle affiché. */
function precharger(c, res) {
  const [zmin, zmax] = zoomsDe(res.modele, c.variable);
  const tv = tuilesVisibles(map, zmin, zmax);
  for (let k = 1; k <= 3; k++) {
    const t = choisirTirage({ modele: res.modele, variable: c.variable, tValide: etat.tValide + k * H, manifest: etat.manifest });
    if (!t) continue;
    const u = fichier(t, t.step);
    for (const [z, x, y] of tv) demanderTuile(u, z, x, y, c);
  }
}

// ---------------------------------------------------------------- heure, pastille, attribution
function majHeure() {
  const e = FT.etiquetteFrise(etat.tValide, FUSEAU);
  $('heure-locale').textContent = e.local; $('heure-decalage').textContent = '· ' + e.utc;
  $('heure').title = e.infobulle;
  const t = etat.affiche && etat.affiche.tirage;
  $('heure-detail').textContent = t ? `${FT.formatEcheance(Date.parse(t.run), etat.tValide)} · tirage du ${FT.dateNumerique(Date.parse(t.run), 'UTC')} à ${FT.heureCourte(Date.parse(t.run), 'UTC')} UTC` : '';
  const { debut, fin } = plage();
  const f = $('frise');
  f.min = 0; f.max = (fin - debut) / H;
  if (document.activeElement !== f || etat.lecture) f.value = IL.positionFrise(etat.tValide, debut, fin);
}
/** Lignes du détail du tirage (infobulle et volet tactile). */
function lignesDetail(a, avis) {
  const c = etat.couche, maintenant = Date.now(), lignes = [];
  const run = m => tirages(etat.manifest, m.modele).find(r => r.id === m.id);
  const libelle = m => libelleModele(m, etat.manifest);
  if (c.pseudo || (a && a.tirage)) {
    const modele = c.pseudo ? AROME : a.modele, r = c.pseudo ? IL.tirageIsobares(etat.manifest) : run({ modele, id: a.tirage.id });
    if (r) {
      const d = FT.decrireTirage({ run: r.run, tValide: etat.tValide, modele, libelle: libelle(modele) });
      lignes.push(['modele', libelle(modele)], ['', `${d.tirage} · ${d.echeance}`]);
      if (r.traite) lignes.push(['fraicheur', FT.formatTraite(r.traite, maintenant, FUSEAU)]);
    }
  }
  if ((etat.isobares && !c.pseudo)) {
    const r = IL.tirageIsobares(etat.manifest);
    if (r) lignes.push(['autre', FT.ligneAutreSource({ nom: 'Pression', libelle: libelle(AROME), run: r.run, tValide: etat.tValide, tz: FUSEAU })]);
  }
  if (etat.particules && !c.pseudo && c.cle !== 'vent') {
    const v = champVent();
    if (v && v.tirage && !(a && a.tirage && v.tirage.modele === a.modele && v.tirage.id === a.tirage.id)) {
      lignes.push(['autre', FT.ligneAutreSource({ nom: 'Particules', libelle: libelle(v.tirage.modele), run: v.tirage.run, tValide: etat.tValide, tz: FUSEAU })]);
    }
  }
  if (avis) lignes.push(['autre', avis]);
  return lignes;
}
function majAffichageModele() {
  const a = etat.affiche, c = etat.couche, vue = etat.vue;
  let long, court, avis = '';
  const decrire = (r, modele, id) => FT.decrireTirage({ run: r.run, tValide: etat.tValide, modele, libelle: libelleModele(modele, etat.manifest) });
  if (c.pseudo) {
    const r = IL.tirageIsobares(etat.manifest);
    const d = r && decrire(r, AROME);
    long = d ? d.longue : 'Pression'; court = d ? d.compacte : 'Pression';
  } else if (a && a.tirage) {
    const r = tirages(etat.manifest, a.modele).find(q => q.id === a.tirage.id) || { run: a.tirage.run };
    const d = decrire(r, a.modele);
    long = d.longue; court = d.compacte;
    avis = etat.enAttente
      ? `Bascule vers ${nomModele(etat.desire.modele)} : chargement…`
      : IL.avisRepli(a, { mode: etat.mode, manifest: etat.manifest, zoom: vue?.zoom ?? null });
  } else { long = court = IL.textePastille(a, etat.manifest); }
  if (!a && etat.desire && !etat.desire.tirage) avis = 'Échéance non publiée pour cette couche.';
  // pression d'un autre modèle ou tirage que la pastille : à dire pour cette couche
  const iso = etat.isobares || c.pseudo, ri = IL.tirageIsobares(etat.manifest);
  if (iso && ri && !c.pseudo && !(a && a.modele === AROME && a.tirage && a.tirage.id === ri.id)) {
    avis = (avis ? avis + ' ' : '') + `Pression : AROME, tirage du ${FT.dateNumerique(Date.parse(ri.run), 'UTC')} à ${FT.heureCourte(Date.parse(ri.run), 'UTC')} UTC.`;
  }
  const lignes = lignesDetail(a, avis);
  $('pastille').querySelector('.long').textContent = long;
  $('pastille').querySelector('.court').textContent = court;
  $('pastille').dataset.modele = a ? a.modele : '';
  $('pastille').title = lignes.map(l => l[1]).join('\n') || long;
  const det = $('detail-tirage');
  if (!det.hidden) rendreDetail(lignes);
  etat.lignesDetail = lignes;
  $('avis').textContent = avis; $('avis').hidden = !avis; $('avis').title = avis;
  const modeleAttrib = c.pseudo ? AROME : (a && a.modele);
  $('attrib-modele').textContent = attribution(modeleAttrib, etat.manifest);
  for (const b of $('modeles').children) b.setAttribute('aria-pressed', String(b.dataset.m === etat.mode));
}
function rendreDetail(lignes) {
  const det = $('detail-tirage'); det.textContent = '';
  for (const [cls, txt] of lignes) { const p = document.createElement('p'); if (cls) p.className = cls; p.textContent = txt; det.appendChild(p); }
}

const DIRS = ['N', 'NE', 'E', 'SE', 'S', 'SO', 'O', 'NO'];
function majInfo() {
  const box = $('info');
  if (!etat.point || !etat.cur || etat.couche.pseudo) { box.hidden = true; return; }
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
  const c = etat.couche;
  const cv = $('leg-barre'), ticks = $('leg-ticks');
  if (c.pseudo) {
    $('leg-titre').textContent = 'Pression au niveau de la mer (hPa)';
    cv.hidden = true; ticks.textContent = 'Isobares tous les 2 hPa · A anticyclone · D dépression'; ticks.classList.add('texte');
    return;
  }
  cv.hidden = false; ticks.classList.remove('texte');
  const leg = c.legende;
  $('leg-titre').textContent = `${c.nom} (${c.aff.unite})`;
  const ctx = cv.getContext('2d');
  const img = ctx.createImageData(256, 1);
  for (let i = 0; i < 256; i++) {
    const col = couleurPalette(c.palette, i / 255), a = col[3] / 255, k = i * 4;
    img.data[k] = col[0]; img.data[k + 1] = col[1]; img.data[k + 2] = col[2]; img.data[k + 3] = Math.round(255 * Math.max(a, 0.0));
  }
  ctx.putImageData(img, 0, 0);
  ticks.textContent = '';
  for (const v of leg.ticks) {
    let f = (v - leg.lo) / (leg.hi - leg.lo);
    if (leg.axe === 'sqrt') f = Math.sqrt(Math.max(0, f));
    const s = document.createElement('span');
    s.style.left = (Math.min(1, Math.max(0, f)) * 100) + '%';
    s.textContent = String(v).replace('.', ',');
    ticks.appendChild(s);
  }
}

// ---------------------------------------------------------------- particules et isobares
/** Le vent à l'instant courant dans le modèle affiché : { urlA, urlB, mix, zmin, zmax, dec } ou null. */
function champVent() {
  const cv = COUCHES.find(q => q.cle === 'vent');
  if (!cv.dec) return null;
  const modele = etat.affiche?.modele || etat.desire?.modele || etat.precedent?.modele || AROME_HD;
  let t = etat.couche.cle === 'vent' && etat.affiche?.tirage ? etat.affiche.tirage : null;
  if (!t) t = choisirTirage({ modele, variable: 'wind10', tValide: etat.tValide, manifest: etat.manifest });
  if (!t) { const autre = modele === AROME_HD ? AROME : AROME_HD; t = choisirTirage({ modele: autre, variable: 'wind10', tValide: etat.tValide, manifest: etat.manifest }); }
  if (!t) return null;
  const f = fichiersDe(t), [zmin, zmax] = zoomsDe(t.modele, 'wind10');
  return { ...f, zmin, zmax, dec: cv.dec, tirage: t };
}
function majParticulesChamp() {
  if (!particules) return;
  if (etat.particules) { particules.activer(); particules.setTemps(etat.tValide); } else particules.desactiver();
  majBoutons();
}
async function creerParticules() {
  const cv = COUCHES.find(q => q.cle === 'vent');
  if (!cv.dec) await lireEntetes(cv);
  particules = new ParticulesVent({ fournisseurChamp: () => champVent(), actif: etat.particules, surEtat: () => { majBoutons(); surcouche.planifier(etatSurcouche()); } });
  map.addLayer(particules, idPremiereCoucheHaute());
  document.addEventListener('visibilitychange', () => {      // onglet masqué : pause totale, reprise au retour
    if (document.hidden) particules.desactiver(); else if (etat.particules) particules.activer();
  });
}
let isoEnCours = false;
async function majIsobares() {
  const on = etat.isobares || etat.couche.pseudo;
  if (!on) { if (echeanceCourante(map) !== null) retirerIsobares(map); return; }
  const e = IL.echeanceIsobares(etat.manifest, etat.tValide);
  if (!e || isoEnCours) return;
  try {
    if (echeanceCourante(map) === null) {
      isoEnCours = true;
      await ajouterIsobares(map, { src: `/isobares/${e.src}`, avant: etat.idHaut, echeance: e.echeance, valeurs: AVEC_FOND, extrema: true });
      etat.isoSrc = e.src;
    } else if (e.echeance !== echeanceCourante(map)) {
      isoEnCours = true;
      await majEcheance(map, e.echeance);
    }
  } catch (err) { console.warn('isobares', err); }
  finally { isoEnCours = false; }
}
function majBoutons() {
  const p = $('opt-particules'), i = $('opt-isobares');
  if (!p) return;
  p.setAttribute('aria-pressed', String(!!etat.particules));
  const reduit = particules && particules.etat.mouvementReduit;
  p.title = reduit ? 'Mouvement réduit demandé : flèches à la place des particules' : 'Particules de vent animées';
  p.dataset.reduit = reduit ? '1' : '';
  i.setAttribute('aria-pressed', String(!!(etat.isobares || etat.couche.pseudo)));
}

// ---------------------------------------------------------------- interface
function construireCouches() {
  const nav = $('couches');
  nav.textContent = '';
  for (const c of [...COUCHES, PRESSION]) {
    const b = document.createElement('button');
    b.textContent = c.nom; b.dataset.cle = c.cle;
    b.disabled = c.pseudo ? !IL.tirageIsobares(etat.manifest) : !plageTemps(etat.manifest, c.cle);
    b.setAttribute('aria-pressed', String(c === etat.couche));
    b.addEventListener('click', () => choisirCouche(c));
    nav.appendChild(b);
  }
}
async function choisirCouche(c) {
  if (!c) return;
  const ancienne = etat.couche;
  etat.couche = c; etat.changementCouche = true;
  for (const b of $('couches').children) {
    b.setAttribute('aria-pressed', String(b.dataset.cle === c.cle));
    if (b.dataset.cle === c.cle) b.scrollIntoView({ inline: 'nearest', block: 'nearest' });
  }
  if (!c.pseudo) await lireEntetes(c);
  if (!etat.particulesExplicite) etat.particules = c.cle === 'vent';   // particules par défaut sur la couche Vent seulement
  const { debut, fin } = plage();
  etat.tValide = Math.min(fin, Math.max(debut, etat.tValide));
  majLegende();
  dernierPre = -1;
  if (c.pseudo || ancienne.pseudo) etat.affiche = null;
  appliquer();
  ecrireHash();
}
/** Lit en-tête et métadonnées PMTiles du premier fichier de la couche (modèle préféré d'abord) : paramètres de décodage et zooms. */
async function lireEntetes(c) {
  const preferes = [etat.precedent?.modele, AROME_HD, AROME].filter(Boolean);
  for (const m of preferes) {
    const r = tirages(etat.manifest, m).find(q => q.variables?.[c.variable]?.steps?.length);
    if (!r) continue;
    const step = r.variables[c.variable].steps[0];
    const url = `${etat.base}/${cheminFichier(etat.manifest, m, r.id, c.variable, step)}`;
    if (c.dec && etat.zoomsVar.has(`${m}/${c.variable}`)) return;
    const a = archive(url);
    const [h, meta] = await Promise.all([a.getHeader(), a.getMetadata().catch(() => null)]);
    // ?decodage=ancien : formule min/max du contrat initial (non-régression, démonstration de l'erreur sur reduit)
    if (!c.dec) c.dec = paramsDecodage(c, params.get('decodage') === 'ancien' ? null : meta);   // quant_step, levels, max, encoding lus dans les métadonnées
    etat.zoomsVar.set(`${m}/${c.variable}`, [h.minZoom, h.maxZoom]);
    return;
  }
}

function lecture(on) {
  etat.lecture = on;
  $('lecture').textContent = on ? '❚❚' : '▶';
  $('lecture').setAttribute('aria-label', on ? 'Pause' : 'Lecture');
  if (!on) ecrireHash();
  if (on) { dernierT = performance.now(); requestAnimationFrame(boucle); }
}
let dernierT = 0;
function boucle(now) {
  if (!etat.lecture) return;
  const dt = (now - dernierT) / 1000; dernierT = now;
  const { debut, fin } = plage();
  etat.tValide = IL.avancer(etat.tValide, dt, etat.vitesse, debut, fin);   // ×1 = 1 h de prévision par seconde
  appliquer();
  requestAnimationFrame(boucle);
}

function ecrireHash() {
  if (!map || !etat.vue) return;
  history.replaceState(null, '', location.search + '#' + IL.ecrireHash({ couche: etat.couche.cle, tValide: etat.tValide, centre: map.getCenter(),
    zoom: map.getZoom(), mode: etat.mode, particules: etat.particules, isobares: etat.isobares }));
}

/** Applique un hash modifié dans le même onglet (lien collé, retour arrière) : vue, couche, temps, modèle, superpositions. */
async function appliquerHash(h) {
  if (h.mode) etat.mode = h.mode;
  if (h.particules !== null) { etat.particules = h.particules; etat.particulesExplicite = true; }
  if (h.isobares !== null) etat.isobares = h.isobares;
  if (h.centre && h.zoom != null) { map.jumpTo({ center: [h.centre.lng, h.centre.lat], zoom: h.zoom }); majVue(); }
  const cible = h.couche === 'pression' ? PRESSION : COUCHES.find(c => c.cle === h.couche);
  if (cible && cible !== etat.couche) await choisirCouche(cible);
  const { debut, fin } = plage();
  if (h.tValide !== null) etat.tValide = Math.min(fin, Math.max(debut, h.tValide));
  appliquer(); majBoutons();
}

function brancherInterface() {
  // la légende se place juste au-dessus de la barre du bas, quelle que soit sa hauteur (mobile : attribution sur 2 lignes)
  new ResizeObserver(() => document.documentElement.style.setProperty('--h-bas', $('bas').offsetHeight + 'px')).observe($('bas'));
  $('lecture').addEventListener('click', () => lecture(!etat.lecture));
  $('vitesse').addEventListener('click', () => {
    etat.vitesse = etat.vitesse === 1 ? 2 : etat.vitesse === 2 ? 4 : 1;
    $('vitesse').textContent = '×' + etat.vitesse;
  });
  $('frise').addEventListener('input', e => { const { debut, fin } = plage(); etat.tValide = IL.tempsDepuisFrise(parseFloat(e.target.value), debut, fin); appliquer(); });
  $('frise').addEventListener('change', ecrireHash);
  $('pastille').addEventListener('click', () => {
    const det = $('detail-tirage'); det.hidden = !det.hidden;
    $('pastille').setAttribute('aria-expanded', String(!det.hidden));
    if (!det.hidden) rendreDetail(etat.lignesDetail || []);
  });
  window.addEventListener('hashchange', () => appliquerHash(IL.lireHash(location.hash)));
  for (const b of $('modeles').children) b.addEventListener('click', () => { etat.mode = b.dataset.m; appliquer(); ecrireHash(); });
  $('opt-particules').addEventListener('click', () => { etat.particules = !etat.particules; etat.particulesExplicite = true; majParticulesChamp(); surcouche.planifier(etatSurcouche()); ecrireHash(); });
  $('opt-isobares').addEventListener('click', () => {
    if (etat.couche.pseudo) return;              // la couche Pression est toujours avec isobares
    etat.isobares = !etat.isobares; majBoutons(); majIsobares(); majAffichageModele(); ecrireHash();
  });
  $('info-fermer').addEventListener('click', () => { etat.point = null; majInfo(); surcouche.planifier(etatSurcouche()); });
  map.on('mousemove', e => { if (!etat.point?.epingle) { etat.point = { lng: e.lngLat.lng, lat: e.lngLat.lat, epingle: false }; majInfo(); } });
  map.on('click', e => { etat.point = { lng: e.lngLat.lng, lat: e.lngLat.lat, epingle: true }; majInfo(); surcouche.planifier(etatSurcouche()); });
  map.on('move', () => surcouche.planifier(etatSurcouche()));
  map.on('moveend', () => { majVue(); appliquer(); if (etat.pretOuverture && etat.cur && !etat.couche.pseudo) precharger(etat.couche, etat.cur.res); ecrireHash(); });
  window.addEventListener('resize', () => { surcouche.redimensionner(); surcouche.planifier(etatSurcouche()); });
  window.addEventListener('keydown', e => {
    if (e.target.tagName === 'BUTTON' && e.key === ' ') return;
    const pas = e.shiftKey ? 6 : 1, { debut, fin } = plage();
    if (e.key === ' ') { lecture(!etat.lecture); e.preventDefault(); }
    else if (e.key === 'ArrowRight') { etat.tValide = IL.pasClavier(etat.tValide, pas, debut, fin); appliquer();  ecrireHash(); }
    else if (e.key === 'ArrowLeft') { etat.tValide = IL.pasClavier(etat.tValide, -pas, debut, fin); appliquer();  ecrireHash(); }
    else if (e.key === 'Home') { etat.tValide = debut; appliquer();  ecrireHash(); }
    else if (e.key === 'End') { etat.tValide = fin; appliquer();  ecrireHash(); }
  });
}

// ---------------------------------------------------------------- attente « tout est chargé »
function toutCharge() {
  const cur = etat.cur;
  if (etat.couche.pseudo) return true;
  if (!cur || etat.enAttente) return false;
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
    if (etat.enAttente) appliquer();
    if (etat.couche.pseudo) { await attendre(300); return true; }
    if (toutCharge() && couchegl.derniere.tuiles > 0) { await attendre(120); if (toutCharge()) return true; }
    await attendre(50);
  }
  return false;
}

// ---------------------------------------------------------------- API pour les captures et le banc d'essai
window.__app = {
  get surcouche() { return surcouche; },
  get particules() { return particules; },
  stats,
  get couchegl() { return couchegl; },
  carte() { return map; },
  etat,
  /** Va à une vue : couche, t (heures depuis le plus ancien tirage) ou tValide (ISO/ms), centre [lat, lng], zoom, mode, particules, isobares. */
  async aller({ couche, t, tValide, centre, zoom, mode, particules: p, isobares }) {
    if (mode) etat.mode = IL.normaliserMode(mode);
    if (p !== undefined) { etat.particules = !!p; etat.particulesExplicite = true; }
    if (isobares !== undefined) etat.isobares = !!isobares;
    if (centre || zoom != null) map.jumpTo({ center: centre ? [centre[1], centre[0]] : map.getCenter(), zoom: zoom ?? map.getZoom() });
    majVue();
    if (couche) await choisirCouche(couche === 'pression' ? PRESSION : COUCHES.find(c => c.cle === couche));
    if (tValide != null) etat.tValide = typeof tValide === 'string' ? Date.parse(tValide) : tValide;
    if (t != null) etat.tValide = etat.refT + t * H;
    appliquer(); majBoutons();
    const ok = await attendreCharge();
    appliquer();   // redessine la surcouche avec l'état final, puis laisse deux images se rendre
    await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
    return ok;
  },
  /** Charge TOUTE la frise de la couche (modèle affiché) pour les tuiles visibles et renvoie les octets/requêtes mesurés. */
  async mesurerFrise(cleCouche) {
    await choisirCouche(COUCHES.find(q => q.cle === cleCouche)); await attendreCharge();
    const c = etat.couche, modele = etat.affiche.modele, [zmin, zmax] = zoomsDe(modele, c.variable), tv = tuilesVisibles(map, zmin, zmax);
    const o0 = stats.octetsDonnees, r0 = stats.requetesDonnees, t0 = performance.now(), { debut, fin } = plage();
    const heures = []; for (let h = debut; h <= fin; h += H) heures.push(h);
    let n = 0;
    for (let i = 0; i < heures.length; i += 6) {
      await Promise.all(heures.slice(i, i + 6).flatMap(h => {
        const t = choisirTirage({ modele, variable: c.variable, tValide: h, manifest: etat.manifest });
        if (!t) return [];
        n++;
        return tv.map(([z, x, y]) => demanderTuile(fichier(t, t.step), z, x, y, c).promesse);
      }));
    }
    return { couche: cleCouche, modele, echeances: n, tuiles_visibles_par_echeance: tv.length, octets: stats.octetsDonnees - o0,
      requetes: stats.requetesDonnees - r0, ms: Math.round(performance.now() - t0), z: tv[0][0] };
  },
  texte() { return { pastille: $('pastille').querySelector('.long').textContent, compacte: $('pastille').querySelector('.court').textContent, detail: (etat.lignesDetail || []).map(l => l[1]), titre: $('pastille').title, avis: $('avis').textContent, attribution: $('attrib').textContent, heure: $('heure-locale').textContent,
    heureDetail: $('heure-detail').textContent, titre: $('heure').title }; },
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
    return { couche: c.cle, t: etat.tValide, n: out.length, ecart_max: Math.max(...err), ecart_p95: tri[Math.floor(tri.length * 0.95)], ecart_median: tri[Math.floor(tri.length / 2)], lsb: 1 / 255 };
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
  // animation : ×4, `duree` ms, on compte les images réellement rendues par MapLibre
  stats.phase = 'animation';
  const dur = Number(params.get('duree') || 8000);
  await choisirCouche(COUCHES.find(c => c.cle === (params.get('couche') || 'vent')));
  etat.vitesse = 4; etat.tValide = plage().debut; appliquer();
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
  await chargerManifest();
  jalon('manifest_lu');
  const h = IL.lireHash(location.hash);
  const cleDepart = h.couche || params.get('couche') || 'vent';
  const depart = cleDepart === 'pression' ? PRESSION : (COUCHES.find(c => c.cle === cleDepart && plageTemps(etat.manifest, c.cle)) || COUCHES.find(c => plageTemps(etat.manifest, c.cle)));
  etat.couche = depart;
  if (h.mode) etat.mode = h.mode;
  if (h.particules !== null) { etat.particules = h.particules; etat.particulesExplicite = true; }
  else if (params.has('particules')) { etat.particules = params.get('particules') !== '0'; etat.particulesExplicite = true; }
  else etat.particules = depart.cle === 'vent';
  etat.isobares = h.isobares !== null ? h.isobares : params.get('isobares') === '1';
  const pl = plage();
  etat.tValide = h.tValide ?? (params.has('t') ? etat.refT + Number(params.get('t')) * H : Math.floor(Date.now() / H) * H)   // par défaut : l'heure courante, bornée à la frise;
  etat.tValide = Math.min(pl.fin, Math.max(pl.debut, etat.tValide));
  construireCouches();
  $('attrib-src').textContent = `· tuiles : ${etat.srcNom === 'data' ? 'profil contrat' : etat.srcNom === 'reduit' ? 'profil reduit' : etat.srcNom === 'reduit2' ? 'profil reduit2' : 'SYNTHÉTIQUES (' + etat.srcNom + ')'}`;
  await creerCarte();
  jalon('carte_prete');
  surcouche.redimensionner();
  if (h.centre && h.zoom != null) map.jumpTo({ center: [h.centre.lng, h.centre.lat], zoom: h.zoom });
  majVue();
  if (!depart.pseudo) await lireEntetes(depart);
  jalon('entete_lue');
  brancherInterface();
  surTuile(() => { map.triggerRepaint(); if (etat.enAttente) appliquer(); else majInfo(); surcouche.planifier(etatSurcouche()); });
  couchegl.surPremiere = () => { requestAnimationFrame(() => { jalon('premiere_couche_visible'); mesures.premiere_couche_ms = mesures.jalons.premiere_couche_visible; }); };
  etat.idHaut = idPremiereCoucheHaute();
  map.addLayer(couchegl, etat.idHaut);
  if (params.get('particules') !== '0' && etat.manifest.couches?.vent) await creerParticules();
  map.once('idle', () => { jalon('carte_complete_idle'); });
  etat.changementCouche = true;
  majLegende();
  appliquer();
  attendreCharge().then(() => {
    jalon('tout_charge_ouverture');
    mesures.ouverture = { octets: stats.octetsDonnees, requetes: stats.requetesDonnees, tuiles_chargees: stats.tuilesChargees,
      tuiles_absentes: stats.tuilesAbsentes, tuiles_dessinees: couchegl.derniere.tuiles, fond: { ...stats.fond } };
    if (params.get('precharge') !== '0') { etat.pretOuverture = true; stats.phase = 'precharge'; if (!BENCH) appliquer(); }   // ?precharge=0 : mesure de l'ouverture seule
  });
  if (BENCH) banc();
}
demarrer().catch(e => {
  console.error(e);
  const pre = $('mesures'); pre.hidden = false; pre.textContent = 'ERREUR : ' + e.message; pre.dataset.erreur = '1';
});
