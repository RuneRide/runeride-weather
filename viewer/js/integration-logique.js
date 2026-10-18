// SPDX-License-Identifier: EUPL-1.2
// Logique PURE de l'intégration J3 (manifest, choix du modèle, bascule sans clignotement, isobares, URL partageable).
// Aucun accès au DOM, au réseau ni à l'horloge : testable sous Node (viewer/tests/integration.test.mjs).
import { COUCHES } from './config.js';
import { AROME, AROME_HD, enMs, libelleModele, tirages, tValideDe, echeanceDe } from './modele-auto.js';

const HEURE = 3600e3;

// ------------------------------------------------------------------ manifest de repli (tuiles de test sans manifest)

/** Construit un manifest minimal depuis l'inventaire `index.json` de serve.py ({modèle: {tirage: {variable: [échéances]}}}).
 *  Sert aux jeux synthétiques (?src=synth|sans|brute) qui n'ont pas de manifest ; même schéma que pipeline/manifest. */
export function manifestDepuisIndex(idx) {
  const models = {};
  const variables = {};
  for (const [m, parTirage] of Object.entries(idx || {})) {
    const runs = Object.entries(parTirage).map(([id, vars]) => {
      const mm = /^(\d{4})-(\d{2})-(\d{2})T(\d{2})Z$/.exec(id);
      const run = mm ? `${mm[1]}-${mm[2]}-${mm[3]}T${mm[4]}:00:00Z` : id;
      const v = {};
      let complet = true;
      for (const [nom, steps] of Object.entries(vars)) {
        const premiere = ['gust10', 'precip_rate', 'snow_fraction', 'cloud_total'].includes(nom) ? 1 : 0;
        const c = steps.length > 0 && steps[0] === premiere && steps[steps.length - 1] === premiere + steps.length - 1 - premiere + premiere
          && steps.length === steps[steps.length - 1] - steps[0] + 1;
        v[nom] = { steps, complete: c };
        complet = complet && c;
        variables[nom] = { models: [...new Set([...(variables[nom]?.models || []), m])] };
      }
      const toutes = Object.values(v).flatMap(x => x.steps);
      return { run, id, path: `${m}/${id}`, complete: complet, valid_debut: run, valid_fin: new Date(enMs(run) + Math.max(0, ...toutes) * HEURE).toISOString().replace('.000Z', 'Z'), variables: v };
    });
    models[m] = { label: m === AROME_HD ? 'AROME HD' : 'AROME', resolution_deg: m === AROME_HD ? 0.01 : 0.025, runs };
  }
  const couches = {};
  for (const c of COUCHES) if (variables[c.variable]) couches[c.cle] = { nom: c.nom, variable: c.variable, models: variables[c.variable].models };
  return { version: 1, attribution: 'Source : Météo-France, Licence Ouverte v2.0', file_template: '{model}/{run_id}/{variable}/{step}.pmtiles',
    models, variables, couches, hors_raster: {}, synthetique: true };
}

// ------------------------------------------------------------------ frise en temps valide

/** Position de la frise (heures depuis `debut`) pour un temps valide `t` (ms), bornée à [debut, fin]. */
export function positionFrise(t, debut, fin) {
  return (Math.min(fin, Math.max(debut, t)) - debut) / HEURE;
}
/** Temps valide (ms) pour une position de frise (heures depuis `debut`), borné. */
export function tempsDepuisFrise(h, debut, fin) {
  return Math.min(fin, Math.max(debut, debut + h * HEURE));
}
/** Avance de `dt` secondes à la vitesse `vitesse` (1 h de prévision par seconde à ×1), avec bouclage sur [debut, fin]. */
export function avancer(t, dtSecondes, vitesse, debut, fin) {
  let n = t + dtSecondes * vitesse * HEURE;
  if (n > fin) n = debut + (n - fin);
  if (n > fin) n = fin;
  return n;
}
/** Pas clavier : arrondit à l'heure puis ajoute `pas` heures (borné). */
export function pasClavier(t, pas, debut, fin) {
  const h = Math.round((t - debut) / HEURE);
  return tempsDepuisFrise(h + pas, debut, fin);
}

/** Libellé d'heure : heure locale avec décalage (« 14:30 UTC+2 »), UTC en détail. `fuseau` : fuseau IANA (défaut : celui du navigateur). */
export function formaterTemps(t, fuseau) {
  const d = new Date(t);
  const opt = fuseau ? { timeZone: fuseau } : {};
  const jour = new Intl.DateTimeFormat('fr-FR', { weekday: 'short', day: 'numeric', month: 'short', ...opt }).format(d);
  const heure = new Intl.DateTimeFormat('fr-FR', { hour: '2-digit', minute: '2-digit', ...opt }).format(d);
  const dec = new Intl.DateTimeFormat('fr-FR', { timeZoneName: 'short', ...opt }).formatToParts(d).find(p => p.type === 'timeZoneName')?.value || '';
  const utc = new Intl.DateTimeFormat('fr-FR', { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }).format(d);
  const jourUtc = new Intl.DateTimeFormat('fr-FR', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' }).format(d);
  return { local: `${jour} · ${heure}`, decalage: dec, utc, infobulle: `${utc} UTC, ${jourUtc}` };
}

// ------------------------------------------------------------------ URL partageable (hash)

const MODES = ['auto', AROME, AROME_HD];
/** Normalise un mode de modèle saisi (« hd », « arome-hd », « AROME »...) ; 'auto' si inconnu. */
export function normaliserMode(m) {
  const s = String(m || '').toLowerCase();
  if (s === 'hd' || s === 'arome-hd' || s === 'aromehd') return AROME_HD;
  if (s === 'arome') return AROME;
  return MODES.includes(s) ? s : 'auto';
}

/** Hash de l'URL : #couche=vent&t=2026-10-17T14:30&c=lat,lng&z=6.2&m=auto&p=1&i=0 */
export function ecrireHash({ couche, tValide, centre, zoom, mode, particules, isobares }) {
  const p = new URLSearchParams();
  p.set('couche', couche);
  p.set('t', new Date(tValide).toISOString().slice(0, 16));
  p.set('c', `${centre.lat.toFixed(3)},${centre.lng.toFixed(3)}`);
  p.set('z', zoom.toFixed(2));
  p.set('m', mode);
  p.set('p', particules ? '1' : '0');
  p.set('i', isobares ? '1' : '0');
  return p.toString().replace(/%3A/g, ':').replace(/%2C/g, ',');
}
/** Lit le hash ; champs absents = null. Un `t` numérique (ancien format : heures) est ignoré. */
export function lireHash(chaine) {
  const h = new URLSearchParams(String(chaine || '').replace(/^#/, ''));
  const t = h.get('t');
  const ms = t && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(t) ? Date.parse(t.length === 16 ? t + ':00Z' : t) : NaN;
  const c = h.get('c')?.split(',').map(Number);
  return {
    couche: h.get('couche'), tValide: Number.isFinite(ms) ? ms : null,
    centre: c && c.length === 2 && c.every(Number.isFinite) ? { lat: c[0], lng: c[1] } : null,
    zoom: h.has('z') && Number.isFinite(parseFloat(h.get('z'))) ? parseFloat(h.get('z')) : null,
    mode: h.has('m') ? normaliserMode(h.get('m')) : null,
    particules: h.has('p') ? h.get('p') === '1' : null,
    isobares: h.has('i') ? h.get('i') === '1' : null,
  };
}

// ------------------------------------------------------------------ bascule sans clignotement

/** Clé d'identité de ce qui est dessiné : changer de clé = changer de source de données (modèle, tirage, variable). */
export function cleAffichage(res) {
  return res && res.tirage ? `${res.modele}/${res.tirage.id}/${res.tirage.variable}` : null;
}

/**
 * Décide s'il faut échanger le calque affiché contre le calque voulu.
 *  - rien d'affiché, ou changement de couche demandé par l'utilisateur : échange immédiat ;
 *  - même source : on suit la nouvelle échéance (le préchargement la couvre) ;
 *  - source différente (bascule HD / AROME, nouveau tirage) : on GARDE l'ancien calque tant que les tuiles visibles
 *    du nouveau ne sont pas toutes arrivées (`pret`), puis on échange en une seule image ;
 *  - l'ancien calque ne peut plus servir (instant hors de sa plage : `ancienValide` faux) : échange immédiat.
 */
export function decisionBascule({ affiche, desire, pret, changementCouche = false, ancienValide = true }) {
  if (!desire) return 'garder';
  if (!affiche || changementCouche) return 'echanger';
  if (cleAffichage(affiche) === cleAffichage(desire)) return 'echanger';
  if (!ancienValide) return 'echanger';
  return pret ? 'echanger' : 'attendre';
}

// ------------------------------------------------------------------ pastille et avis

/** Texte de la pastille : « AROME HD 0,01° · tirage 03 h UTC ». */
export function textePastille(res, manifest) {
  if (!res || !res.modele) return 'Aucune donnée';
  const t = res.tirage;
  return t ? `${libelleModele(res.modele, manifest)} · tirage ${t.id.slice(11, 13)} h UTC` : libelleModele(res.modele, manifest);
}

/** Message discret (ou '') quand l'affichage n'est pas celui que l'utilisateur attend. */
export function avisRepli(res, { mode = 'auto', manifest, zoom = null } = {}) {
  if (!res || !res.choix) return '';
  const ch = res.choix;
  const nomDe = m => libelleModele(m, manifest);
  if (!res.modele) return 'Couche indisponible.';
  if (res.repliTemps) return `Échéance hors de la plage du modèle demandé : ${nomDe(res.modele)}.`;
  if (ch.repli) return ch.raison === 'variable_absente_hd'
    ? `Couche absente de ${nomDe(AROME_HD)} : ${nomDe(AROME)} affiché.`
    : `Couche absente de ${nomDe(AROME)} : ${nomDe(AROME_HD)} affiché.`;
  // tirage plus récent existant mais sans cette échéance, alors que l'instant le concerne (sinon : simple avant-début du tirage récent)
  const recent = tirages(manifest, res.modele)[0];
  if (res.tirage && res.tirage.plusRecent === false && recent && enMs(recent.run) <= enMs(res.tirage.tValide)) return `Tirage ${res.tirage.id.slice(11, 13)} h : le tirage plus récent n'a pas cette échéance.`;
  if (mode === AROME_HD && zoom !== null && zoom < 5) return 'Zoom trop faible pour les tuiles HD : zoomez.';
  if (mode === 'auto' && ch.raison === 'hors_domaine') return 'Vue hors du domaine HD : AROME affiché.';
  return '';
}

// ------------------------------------------------------------------ isobares

/** Tirage AROME le plus récent (celui dont dérivent les isobares) ou null. */
export function tirageIsobares(manifest) {
  const t = tirages(manifest, AROME);
  return t.length ? t[0] : null;
}

/**
 * Échéance des isobares pour un temps valide : celle de l'AROME le plus proche, bornée à la plage du tirage.
 * @returns {null | {id, echeance, src, hors: boolean, ecartH}} hors : le temps valide est hors de la plage (échéance bornée).
 */
export function echeanceIsobares(manifest, tValide, { horizon = 51 } = {}) {
  const r = tirageIsobares(manifest);
  if (!r) return null;
  const h = echeanceDe(r.run, tValide);
  const ech = Math.min(horizon, Math.max(0, Math.round(h)));
  return { id: r.id, echeance: ech, src: `arome/${r.id}`, hors: h < -0.5 || h > horizon + 0.5, ecartH: Math.abs(h - ech) };
}

/** Plage de temps valide des isobares : [début du tirage AROME, début + horizon], ou null. */
export function plageIsobares(manifest, { horizon = 51 } = {}) {
  const r = tirageIsobares(manifest);
  return r ? { debut: tValideDe(r.run, 0), fin: tValideDe(r.run, horizon) } : null;
}
