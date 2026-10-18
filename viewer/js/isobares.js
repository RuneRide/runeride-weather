// SPDX-License-Identifier: EUPL-1.2
// Couche « Pression » : isobares tous les 2 hPa avec leurs valeurs le long des lignes et repères A / D.
// Module ES autonome (PoC J3) : n'importe rien du viewer, ne touche à aucun autre fichier.
//
// Données produites par `python3 -m pipeline.isobars` : un fichier par échéance,
//   - format 'geojson' : <src>/NN.geojson   (FeatureCollection ; properties.type = 'isobare' | 'extremum')
//   - format 'mvt'     : <src>/mvt/NN.pmtiles (PMTiles de tuiles vectorielles, couches `isobares` et `extrema`)
// Le format MVT exige que la page ait enregistré le protocole : maplibregl.addProtocol('pmtiles', new pmtiles.Protocol().tile).
//
// Polices : celles du fond de carte Protomaps (« Noto Sans Medium » / « Noto Sans Regular »), servies par
// le `glyphs` du style de la carte ; la carte doit donc avoir un `glyphs` (c'est le cas du viewer).

const ETAT = new WeakMap();   // carte -> état (une seule couche Pression par carte)

const COULEURS = {
  ligne: '#e8eef4',        // trait clair, lisible sur le fond sombre Protomaps
  halo: '#0b0d10',         // liseré sombre discret sous la ligne
  texte: '#f3f6f9',
  texteHalo: '#101214',
  anticyclone: '#9fd0ff',  // A : bleu clair
  depression: '#ffab9a',   // D : rouge clair
};

/** Nom de fichier d'une échéance : 0 -> '00'. */
function nn(n) { return String(Math.round(n)).padStart(2, '0'); }

/** URL de la donnée d'une échéance selon le format. `src` ne finit pas par '/'. */
export function urlEcheance(src, n, format = 'geojson') {
  const base = src.replace(/\/+$/, '');
  if (format === 'mvt') return `pmtiles://${new URL(`${base}/mvt/${nn(n)}.pmtiles`, location.href).href}`;
  return new URL(`${base}/${nn(n)}.geojson`, location.href).href;
}

/** Définition des couches pour une génération `g` ; `sl` = noms des couches source (MVT) ou null (GeoJSON, filtre sur properties.type). */
function definirCouches(g, source, mvt, opts) {
  const id = nom => `isobares-${g}-${nom}`;
  const lignes = mvt ? { 'source-layer': 'isobares' } : { filter: ['==', ['get', 'type'], 'isobare'] };
  const points = mvt ? { 'source-layer': 'extrema' } : { filter: ['==', ['get', 'type'], 'extremum'] };
  const majeur = ['to-boolean', ['get', 'majeur']];
  const c = [
    { id: id('halo'), type: 'line', source, ...lignes, minzoom: 2,
      layout: { 'line-join': 'round', 'line-cap': 'round' },
      paint: { 'line-color': COULEURS.halo, 'line-opacity': 0.4, 'line-blur': 1,
               'line-width': ['interpolate', ['linear'], ['zoom'], 3, ['case', majeur, 2.6, 2], 10, ['case', majeur, 4.4, 3.4]] } },
    { id: id('ligne'), type: 'line', source, ...lignes, minzoom: 2,
      layout: { 'line-join': 'round', 'line-cap': 'round' },
      paint: { 'line-color': COULEURS.ligne, 'line-opacity': ['case', majeur, 0.95, 0.62],
               'line-width': ['interpolate', ['linear'], ['zoom'], 3, ['case', majeur, 1.1, 0.6], 10, ['case', majeur, 2.2, 1.2]] } },
  ];
  if (opts.valeurs) c.push({
    id: id('valeurs'), type: 'symbol', source, ...lignes, minzoom: 3.5,
    layout: {
      'symbol-placement': 'line', 'symbol-spacing': 260, 'text-max-angle': 28, 'text-keep-upright': true,
      'text-field': ['to-string', ['get', 'niveau']],
      'text-font': ['Noto Sans Medium'],
      'text-size': ['case', majeur, 12, 10.5],
      'text-padding': 6, 'text-letter-spacing': 0.02,
      'symbol-sort-key': ['case', majeur, 0, 1],      // à collision égale, les multiples de 10 gardent leur étiquette
    },
    paint: { 'text-color': COULEURS.texte, 'text-halo-color': COULEURS.texteHalo, 'text-halo-width': 1.8, 'text-halo-blur': 0.4 },
  });
  if (opts.extrema) c.push({
    id: id('extrema'), type: 'symbol', source, ...points,
    layout: {
      'text-field': ['format', ['get', 'genre'], { 'font-scale': 2 }, '\n', {},
                     ['to-string', ['round', ['get', 'pression']]], { 'font-scale': 0.95 }],
      'text-font': ['Noto Sans Medium'], 'text-size': 14, 'text-line-height': 1.05,
      'text-allow-overlap': true, 'text-ignore-placement': true,
    },
    paint: { 'text-color': ['match', ['get', 'genre'], 'A', COULEURS.anticyclone, 'D', COULEURS.depression, COULEURS.texte],
             'text-halo-color': COULEURS.texteHalo, 'text-halo-width': 2 },
  });
  return c;
}

/** Attend que la source `idSource` ait chargé ses données (ou une erreur la concernant). */
function attendreSource(map, idSource, delaiMs = 20000) {
  return new Promise((resolve, reject) => {
    const fin = (err) => { map.off('sourcedata', surSource); map.off('error', surErreur); clearTimeout(t); err ? reject(err) : resolve(); };
    const surSource = e => { if (e.sourceId === idSource && map.isSourceLoaded(idSource)) fin(); };
    const surErreur = e => { if (e.sourceId === idSource) fin(e.error || new Error('erreur de source ' + idSource)); };
    const t = setTimeout(() => fin(new Error('délai dépassé : ' + idSource)), delaiMs);
    map.on('sourcedata', surSource); map.on('error', surErreur);
    if (map.getSource(idSource) && map.isSourceLoaded(idSource)) fin();
  });
}

function ajouterGeneration(map, etat, n) {
  const g = ++etat.gen, source = `isobares-src-${g}`;
  const mvt = etat.format === 'mvt';
  map.addSource(source, mvt
    ? { type: 'vector', url: urlEcheance(etat.src, n, 'mvt'), attribution: '' }
    : { type: 'geojson', data: urlEcheance(etat.src, n, 'geojson'), maxzoom: 10, buffer: 64, tolerance: 0.15 });
  const couches = definirCouches(g, source, mvt, etat.opts);
  for (const l of couches) map.addLayer(l, etat.avant && map.getLayer(etat.avant) ? etat.avant : undefined);
  return { g, source, couches: couches.map(l => l.id) };
}

function retirerGeneration(map, gen) {
  if (!gen) return;
  for (const l of gen.couches) if (map.getLayer(l)) map.removeLayer(l);
  if (map.getSource(gen.source)) map.removeSource(gen.source);
}

/**
 * Ajoute la couche Pression à la carte.
 * @param {maplibregl.Map} map  carte dont le style est chargé.
 * @param {object} o
 * @param {string} o.src     URL de base des données d'un tirage, ex. '/isobares/arome/2026-10-17T06Z'.
 * @param {string} [o.avant] id d'une couche du style devant laquelle insérer (ex. la première couche de noms de lieux) ; par défaut au-dessus de tout.
 * @param {number} [o.echeance=0]  échéance initiale (heures depuis le tirage).
 * @param {'geojson'|'mvt'} [o.format='geojson']
 * @param {boolean} [o.valeurs=true]  valeurs en hPa le long des lignes.
 * @param {boolean} [o.extrema=true]  repères A / D avec leur valeur.
 * @returns {Promise<void>} résolue quand les données de l'échéance sont chargées.
 */
export function ajouterIsobares(map, { src, avant, echeance = 0, format = 'geojson', valeurs = true, extrema = true } = {}) {
  if (!src) throw new Error('ajouterIsobares : `src` est obligatoire');
  retirerIsobares(map);
  const etat = { src, avant, format, opts: { valeurs, extrema }, gen: 0, courant: null, echeance };
  ETAT.set(map, etat);
  etat.courant = ajouterGeneration(map, etat, echeance);
  return attendreSource(map, etat.courant.source);
}

/**
 * Change d'échéance. GeoJSON : remplace les données de la source (l'ancien tracé reste affiché jusqu'à l'arrivée du nouveau).
 * MVT : charge une nouvelle source cachée derrière l'ancienne puis retire celle-ci (pas de clignotement).
 * @returns {Promise<void>} résolue quand les nouvelles données sont prêtes ; rejetée si le fichier est absent.
 */
export async function majEcheance(map, n) {
  const etat = ETAT.get(map);
  if (!etat) throw new Error('majEcheance : appelez d\'abord ajouterIsobares');
  etat.echeance = n;
  const jeton = (etat.jeton = (etat.jeton || 0) + 1);       // une réponse tardive d'une échéance périmée est ignorée
  if (etat.format === 'geojson') {
    const src = map.getSource(etat.courant.source);
    src.setData(urlEcheance(etat.src, n, 'geojson'));
    await new Promise((res, rej) => {
      const fin = (err) => { map.off('sourcedata', ok); map.off('error', ko); err ? rej(err) : res(); };
      const ok = e => { if (e.sourceId === etat.courant.source && e.dataType === 'source' && map.isSourceLoaded(e.sourceId)) fin(); };
      const ko = e => { if (e.sourceId === etat.courant.source) fin(e.error); };
      map.on('sourcedata', ok); map.on('error', ko);
    });
    return;
  }
  const ancien = etat.courant;
  const nouveau = ajouterGeneration(map, etat, n);
  try { await attendreSource(map, nouveau.source); }
  catch (e) { retirerGeneration(map, nouveau); throw e; }
  if (jeton !== etat.jeton) { retirerGeneration(map, nouveau); return; }
  etat.courant = nouveau;
  retirerGeneration(map, ancien);
}

/** Retire toutes les couches et sources de la Pression (sans effet si absentes). */
export function retirerIsobares(map) {
  const etat = ETAT.get(map);
  if (!etat) return;
  retirerGeneration(map, etat.courant);
  ETAT.delete(map);
}

/** Échéance actuellement affichée (null si la couche est retirée). */
export function echeanceCourante(map) { const e = ETAT.get(map); return e ? e.echeance : null; }

/** Identifiants des couches actuelles (halo, ligne, valeurs, extrema) : pour les tests ou un contrôle de visibilité. */
export function couchesIsobares(map) { const e = ETAT.get(map); return e ? [...e.courant.couches] : []; }
