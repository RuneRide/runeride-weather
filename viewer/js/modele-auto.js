// SPDX-License-Identifier: EUPL-1.2
// Choix automatique du modèle (AROME / AROME HD) et du tirage, à partir du manifest.
//
// Module PUR : aucune dépendance, aucun accès au DOM, au réseau ni à l'horloge. Toutes les
// fonctions prennent le manifest et l'état courant en arguments et renvoient de nouveaux
// objets. Règles : spécifications §3.1 (D7, repli par couche) et §4 (D8, publication par
// variable et par échéance, risque R11). Format du manifest : docs/poc-j3/manifest.md.
//
// Conventions
//  - Temps valide : instant UTC, en millisecondes depuis l'époque (number), Date ou chaîne ISO.
//  - Emprise de vue : [ouest, sud, est, nord] en degrés, ou [[ouest, sud], [est, nord]],
//    ou { west, south, east, north }, ou un objet MapLibre `LngLatBounds` (méthode toArray()).
//  - « couche » : clé de manifest.couches (vent, rafales, t2m, pluie, nuages, humidite, neige,
//    cape) ou, à défaut, nom d'une variable tuilée (wind10, t2m, ...).

/** Modèles, du plus fin au plus large. */
export const AROME = 'arome';
export const AROME_HD = 'arome-hd';

/** Zoom carte à partir duquel on passe en HD (D7). */
export const ZOOM_HD = 7;
/** Hystérésis : une fois en HD, on y reste tant que zoom >= ZOOM_HD - HYSTERESIS. */
export const HYSTERESIS = 0.25;
/** Tolérance (degrés) de sortie de la vue hors du polygone pour ENTRER en HD. */
export const TOLERANCE_ENTREE = 0.05;
/** Tolérance plus large pour RESTER en HD (évite de basculer pour un léger dépassement). */
export const TOLERANCE_SORTIE = 0.25;

const HEURE = 3600e3;
const EPS = 1e-9;

// ------------------------------------------------------------------ temps

/** Convertit un temps (ms, Date ou ISO) en millisecondes UTC ; NaN si illisible. */
export function enMs(t) {
  if (typeof t === 'number') return t;
  if (t instanceof Date) return t.getTime();
  return Date.parse(t);
}

/** Temps valide (ms) de l'échéance `echeance` (heures) du tirage `run` (ISO). */
export function tValideDe(run, echeance) {
  return enMs(run) + echeance * HEURE;
}

/** Échéance (heures, éventuellement fractionnaire) du temps valide `tValide` pour le tirage `run`. */
export function echeanceDe(run, tValide) {
  return (enMs(tValide) - enMs(run)) / HEURE;
}

/** Décompose une échéance fractionnaire : { a: échéance entière inférieure, fraction: [0,1[ }. */
function decouper(h) {
  let a = Math.floor(h + EPS);
  let fraction = h - a;
  if (fraction < EPS) fraction = 0;
  return { a, fraction };
}

// ------------------------------------------------------------------ accès au manifest

/** Variable tuilée d'une couche ; null si inconnue. */
export function variableDeCouche(manifest, couche) {
  const c = manifest?.couches?.[couche];
  if (c) return c.variable;
  if (manifest?.variables?.[couche]) return couche;
  return null;
}

/** Tirages d'un modèle (tableau, possiblement vide), du plus récent au plus ancien. */
export function tirages(manifest, modele) {
  const runs = manifest?.models?.[modele]?.runs || [];
  return runs.slice().sort((x, y) => enMs(y.run) - enMs(x.run));
}

/** Vrai si le modèle publie la variable dans au moins un tirage (sinon : absente du modèle). */
export function modeleFournit(manifest, modele, variable) {
  return tirages(manifest, modele).some(r => r.variables?.[variable]?.steps?.length > 0);
}

/** Échéances publiées de `variable` dans le tirage `run` ([] si absente). */
function echeances(run, variable) {
  return run.variables?.[variable]?.steps || [];
}

// ------------------------------------------------------------------ géométrie

/** Normalise une emprise de vue en { w, s, e, n } ; null si inutilisable. */
export function normaliserBounds(b) {
  if (!b) return null;
  if (typeof b.toArray === 'function') b = b.toArray();
  let w, s, e, n;
  if (Array.isArray(b) && b.length === 4) [w, s, e, n] = b;
  else if (Array.isArray(b) && b.length === 2) [[w, s], [e, n]] = b;
  else if (typeof b === 'object' && 'west' in b) ({ west: w, south: s, east: e, north: n } = b);
  else return null;
  if (![w, s, e, n].every(Number.isFinite)) return null;
  return { w: Math.min(w, e), s: Math.min(s, n), e: Math.max(w, e), n: Math.max(s, n) };
}

/** Test pair-impair d'un point [x, y] dans un anneau (liste de [x, y], fermé ou non). */
export function pointDansPolygone(x, y, anneau) {
  let dedans = false;
  for (let i = 0, j = anneau.length - 1; i < anneau.length; j = i++) {
    const [xi, yi] = anneau[i], [xj, yj] = anneau[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) dedans = !dedans;
  }
  return dedans;
}

function croise(p1, p2, p3, p4) {
  // intersection stricte de deux segments (les contacts aux extrémités ne comptent pas)
  const d = (a, b, c) => (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
  const d1 = d(p3, p4, p1), d2 = d(p3, p4, p2), d3 = d(p1, p2, p3), d4 = d(p1, p2, p4);
  return d1 * d2 < 0 && d3 * d4 < 0;
}

/** Rectangle { w, s, e, n } entièrement contenu dans l'anneau (le bord compte comme dedans). */
export function rectangleDansPolygone(r, anneau) {
  const coins = [[r.w, r.s], [r.e, r.s], [r.e, r.n], [r.w, r.n]];
  if (!coins.every(([x, y]) => pointDansPolygone(x, y, anneau))) return false;
  // un coin dedans ne suffit pas pour un polygone concave : aucun bord ne doit traverser le rectangle
  for (let i = 0; i < anneau.length; i++) {
    const a = anneau[i], b = anneau[(i + 1) % anneau.length];
    for (let k = 0; k < 4; k++) if (croise(a, b, coins[k], coins[(k + 1) % 4])) return false;
    if (a[0] > r.w && a[0] < r.e && a[1] > r.s && a[1] < r.n) return false;
  }
  return true;
}

/** Vrai si le rectangle et l'anneau ont au moins un point commun. */
export function rectangleToucheLePolygone(r, anneau) {
  const coins = [[r.w, r.s], [r.e, r.s], [r.e, r.n], [r.w, r.n]];
  if (coins.some(([x, y]) => pointDansPolygone(x, y, anneau))) return true;
  if (anneau.some(([x, y]) => x >= r.w && x <= r.e && y >= r.s && y <= r.n)) return true;
  for (let i = 0; i < anneau.length; i++) {
    const a = anneau[i], b = anneau[(i + 1) % anneau.length];
    for (let k = 0; k < 4; k++) if (croise(a, b, coins[k], coins[(k + 1) % 4])) return true;
  }
  return false;
}

/** Anneau du domaine d'un modèle : polygone du manifest, sinon rectangle de la grille, sinon null. */
export function anneauDuDomaine(manifest, modele) {
  const m = manifest?.models?.[modele];
  if (m?.domain?.polygon?.length >= 4) return m.domain.polygon;
  if (m?.grid?.length === 4) {
    const [w, s, e, n] = m.grid;
    return [[w, n], [e, n], [e, s], [w, s], [w, n]];
  }
  return null;
}

/**
 * La vue est-elle « dans le domaine » du modèle ? Le rectangle de vue, RÉTRÉCI de `tolerance`
 * degrés de chaque côté (la vue peut donc dépasser du polygone d'au plus `tolerance`), doit
 * être contenu dans le polygone. Sans emprise ou sans domaine connu : vrai (pas de raison de refuser).
 */
export function dansDomaine(bounds, modele, manifest, tolerance = 0) {
  const r = normaliserBounds(bounds);
  const anneau = anneauDuDomaine(manifest, modele);
  if (!r || !anneau) return true;
  const t = Math.min(tolerance, (r.e - r.w) / 2, (r.n - r.s) / 2);
  return rectangleDansPolygone({ w: r.w + t, s: r.s + t, e: r.e - t, n: r.n - t }, anneau);
}

// ------------------------------------------------------------------ choix du modèle

function nomPrecedent(precedent) {
  return typeof precedent === 'string' ? precedent : precedent?.modele || null;
}

/**
 * Choisit le modèle à afficher pour une couche (D7).
 *
 * @param {object} p
 * @param {number} p.zoom         zoom carte courant
 * @param {*}      p.bounds       emprise de la vue (voir en-tête)
 * @param {string} p.couche       couche (ou variable)
 * @param {string} [p.mode]       'auto' (défaut) | 'arome' | 'arome-hd'
 * @param {object} p.manifest
 * @param {string|object} [p.precedent] modèle précédemment affiché (nom ou résultat précédent) :
 *        active l'hystérésis ; sans lui, le seuil est ZOOM_HD sans marge.
 * @returns {{modele: (string|null), raison: string, repli: boolean, dansDomaine: boolean,
 *            demande: string, variable: (string|null)}}
 *  raison : 'zoom' (HD choisi), 'zoom_faible', 'hors_domaine', 'force', 'variable_absente_hd',
 *           'variable_absente_arome', 'couche_inconnue', 'variable_absente'.
 *  repli : vrai si la couche a dû changer de modèle faute de variable (sans erreur, D7).
 *  demande : modèle voulu avant le repli par couche.
 */
export function choisirModele({ zoom, bounds, couche, mode = 'auto', manifest, precedent = null }) {
  const variable = variableDeCouche(manifest, couche);
  const prec = nomPrecedent(precedent);
  const enHD = prec === AROME_HD;
  const base = { variable, repli: false, dansDomaine: true, demande: AROME };
  if (!variable) return { ...base, modele: null, raison: 'couche_inconnue', demande: null };

  // 1. modèle voulu
  let demande, raison;
  const dans = dansDomaine(bounds, AROME_HD, manifest, enHD ? TOLERANCE_SORTIE : TOLERANCE_ENTREE);
  base.dansDomaine = dans;
  if (mode === AROME || mode === AROME_HD) {
    demande = mode;
    raison = 'force';
  } else {
    const seuil = enHD ? ZOOM_HD - HYSTERESIS : ZOOM_HD;
    if (!(zoom >= seuil)) { demande = AROME; raison = 'zoom_faible'; }
    else if (!dans) { demande = AROME; raison = 'hors_domaine'; }
    else { demande = AROME_HD; raison = 'zoom'; }
  }

  // 2. repli par couche : la variable doit exister dans le modèle voulu
  const autre = demande === AROME_HD ? AROME : AROME_HD;
  if (modeleFournit(manifest, demande, variable)) return { ...base, modele: demande, raison, demande };
  if (modeleFournit(manifest, autre, variable)) {
    return { ...base, modele: autre, raison: demande === AROME_HD ? 'variable_absente_hd' : 'variable_absente_arome', repli: true, demande };
  }
  return { ...base, modele: null, raison: 'variable_absente', demande };
}

// ------------------------------------------------------------------ choix du tirage

/**
 * Dernier tirage dont la variable existe À CET INSTANT VALIDE (D8). Un tirage incomplet est toléré :
 * seule compte l'existence de l'échéance demandée, par variable. Le HD 06Z publié 0-18 h sert donc
 * jusqu'à 18 h, puis le HD 03Z prend le relais.
 *
 * Pour un instant entre deux heures, l'échéance suivante doit venir du MÊME tirage (jamais deux
 * tirages dans une image) : on préfère le tirage le plus récent qui a les deux échéances ; à défaut
 * le plus récent qui a l'échéance inférieure (fraction ramenée à 0, comme viewer/js/main.js).
 *
 * @returns {null | {modele, variable, run, id, path, complete, step, stepSuivant, fraction,
 *                   tValide, plusRecent}}
 *  step : échéance entière inférieure ; stepSuivant : échéance entière suivante ou null ;
 *  fraction : poids du mélange vers stepSuivant ; plusRecent : faux si un tirage plus récent existe
 *  pour cette variable (mais sans l'échéance demandée) : à signaler à l'utilisateur.
 */
export function choisirTirage({ modele, variable, tValide, manifest }) {
  const t = enMs(tValide);
  if (!Number.isFinite(t)) return null;
  const runs = tirages(manifest, modele).filter(r => echeances(r, variable).length > 0);
  let choix = null, seulementA = null;
  for (const r of runs) {
    const { a, fraction } = decouper(echeanceDe(r.run, t));
    const S = echeances(r, variable);
    if (!S.includes(a)) continue;
    if (fraction === 0 || S.includes(a + 1)) { choix = { r, a, fraction }; break; }
    if (!seulementA) seulementA = { r, a, fraction: 0 };
  }
  choix = choix || seulementA;
  if (!choix) return null;
  const { r, a, fraction } = choix;
  const S = echeances(r, variable);
  return {
    modele, variable, run: r.run, id: r.id, path: r.path, complete: !!r.complete,
    step: a, stepSuivant: fraction > 0 && S.includes(a + 1) ? a + 1 : null,
    fraction: fraction > 0 && S.includes(a + 1) ? fraction : 0,
    tValide: t, plusRecent: runs[0] === r,
  };
}

/** Chemin relatif d'un fichier PMTiles (selon manifest.file_template). */
export function cheminFichier(manifest, modele, id, variable, step) {
  const gabarit = manifest.file_template || '{model}/{run_id}/{variable}/{step}.pmtiles';
  return gabarit.replace('{model}', modele).replace('{run_id}', id).replace('{variable}', variable)
    .replace('{step}', String(step).padStart(2, '0'));
}

// ------------------------------------------------------------------ frise

/**
 * Plage de temps utilisable pour une couche : de la première à la dernière heure pour laquelle au
 * moins un tirage (de n'importe quel modèle qui fournit la variable, ou de `modele` si précisé)
 * publie l'échéance.
 *
 * @param {object} manifest
 * @param {string} couche
 * @param {{modele?: string, depuis?: *}} [opts] depuis : ignore les instants antérieurs (ex. « maintenant »)
 * @returns {null | {debut: number, fin: number, trous: number[], modeles: string[]}}
 *  debut, fin : ms UTC ; trous : heures (ms) comprises entre debut et fin sans aucune donnée.
 */
export function plageTemps(manifest, couche, opts = {}) {
  const variable = variableDeCouche(manifest, couche);
  if (!variable) return null;
  const modeles = (opts.modele ? [opts.modele] : Object.keys(manifest.models || {}))
    .filter(m => modeleFournit(manifest, m, variable));
  const heures = new Set();
  for (const m of modeles) {
    for (const r of tirages(manifest, m)) {
      for (const s of echeances(r, variable)) heures.add(tValideDe(r.run, s));
    }
  }
  const depuis = opts.depuis === undefined ? -Infinity : enMs(opts.depuis);
  const utiles = [...heures].filter(h => h >= depuis).sort((x, y) => x - y);
  if (!utiles.length) return null;
  const debut = utiles[0], fin = utiles[utiles.length - 1];
  const trous = [];
  for (let h = debut; h <= fin; h += HEURE) if (!heures.has(h)) trous.push(h);
  return { debut, fin, trous, modeles };
}

// ------------------------------------------------------------------ attribution

const LIBELLES = { [AROME]: 'AROME 0,025°', [AROME_HD]: 'AROME HD 0,01°' };

/** Libellé court d'un modèle pour l'interface. */
export function libelleModele(modele, manifest) {
  const m = manifest?.models?.[modele];
  if (m?.label && m?.resolution_deg) return `${m.label} ${String(m.resolution_deg).replace('.', ',')}°`;
  return LIBELLES[modele] || String(modele);
}

/** Mention de source à afficher sur la carte pour le modèle actif (licence : obligation légale). */
export function attribution(modele, manifest) {
  const base = manifest?.attribution || 'Source : Météo-France, Licence Ouverte v2.0';
  return modele ? `${base} · modèle ${libelleModele(modele, manifest)}` : base;
}

// ------------------------------------------------------------------ état d'une couche

const MESSAGES = {
  ok: '',
  couche_inconnue: "Cette couche n'existe pas.",
  variable_absente_modele: "Cette couche n'est pas fournie par ce modèle.",
  hors_domaine: 'La vue est hors du domaine des prévisions.',
  echeance_non_publiee: "Cette échéance n'est pas encore publiée pour cette couche.",
};

/**
 * Explique pourquoi une couche est (in)disponible pour un modèle, un instant et une vue.
 * Ordre des causes : couche inconnue, variable absente du modèle, hors domaine, échéance non publiée.
 *
 * @param {{couche: string, modele: string, tValide: *, manifest: object, bounds?: *}} p
 * @returns {{disponible: boolean, raison: string, detail: (string|null), message: string,
 *            tirage: (object|null), partiel: boolean, variable: (string|null)}}
 *  raison : 'ok' | 'couche_inconnue' | 'variable_absente_modele' | 'hors_domaine' | 'echeance_non_publiee'
 *  detail (échéance non publiée) : 'avant_premiere_echeance' | 'apres_derniere_echeance' | 'trou'
 *  partiel : la vue dépasse du domaine sans être entièrement dehors (données affichées partielles).
 */
export function etatCouche({ couche, modele, tValide, manifest, bounds = null }) {
  const variable = variableDeCouche(manifest, couche);
  const fin = (raison, extra = {}) => ({
    disponible: raison === 'ok', raison, detail: null, message: MESSAGES[raison], tirage: null,
    partiel: false, variable, ...extra,
  });
  if (!variable) return fin('couche_inconnue');
  if (!modeleFournit(manifest, modele, variable)) return fin('variable_absente_modele');

  const r = normaliserBounds(bounds);
  const anneau = anneauDuDomaine(manifest, modele);
  let partiel = false;
  if (r && anneau) {
    if (!rectangleToucheLePolygone(r, anneau)) return fin('hors_domaine');
    partiel = !rectangleDansPolygone(r, anneau);
  }

  const tirage = choisirTirage({ modele, variable, tValide, manifest });
  if (tirage) return fin('ok', { tirage, partiel });

  const plage = plageTemps(manifest, couche, { modele });
  const t = enMs(tValide);
  const detail = t < plage.debut ? 'avant_premiere_echeance' : t > plage.fin ? 'apres_derniere_echeance' : 'trou';
  return fin('echeance_non_publiee', { detail, partiel });
}

// ------------------------------------------------------------------ point d'entrée combiné

/**
 * Résolution complète : modèle (D7) puis tirage (D8), avec repli sur l'autre modèle si le modèle
 * choisi n'a pas l'échéance (ex. HD au-delà de 03Z + 51 h alors qu'AROME 06Z va plus loin).
 *
 * @param {object} p  mêmes champs que choisirModele, plus `tValide`
 * @returns {{modele: (string|null), tirage: (object|null), choix: object, etat: object,
 *            repliTemps: boolean}}
 *  choix : résultat de choisirModele ; etat : etatCouche du modèle finalement retenu ;
 *  repliTemps : vrai si le modèle a changé faute d'échéance (à signaler comme le repli par couche).
 */
export function resoudre({ zoom, bounds, couche, mode = 'auto', manifest, precedent = null, tValide }) {
  const choix = choisirModele({ zoom, bounds, couche, mode, manifest, precedent });
  if (!choix.modele) {
    return { modele: null, tirage: null, choix, repliTemps: false,
      etat: etatCouche({ couche, modele: choix.demande || AROME, tValide, manifest, bounds }) };
  }
  const ordre = [choix.modele, choix.modele === AROME_HD ? AROME : AROME_HD];
  for (const m of ordre) {
    if (!modeleFournit(manifest, m, choix.variable)) continue;
    const etat = etatCouche({ couche, modele: m, tValide, manifest, bounds });
    if (etat.disponible) return { modele: m, tirage: etat.tirage, choix, etat, repliTemps: m !== choix.modele };
  }
  return { modele: choix.modele, tirage: null, choix, repliTemps: false,
    etat: etatCouche({ couche, modele: choix.modele, tValide, manifest, bounds }) };
}
