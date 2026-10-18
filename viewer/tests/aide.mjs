// SPDX-License-Identifier: EUPL-1.2
// Manifests synthétiques pour les tests de modele-auto.js (même schéma que pipeline/manifest).

export const iso = (jour, heure) => `2026-10-${String(jour).padStart(2, '0')}T${String(heure).padStart(2, '0')}:00:00Z`;
export const ms = (jour, heure, minute = 0) => Date.UTC(2026, 9, jour, heure, minute);
export const intervalle = (a, b) => Array.from({ length: b - a + 1 }, (_, i) => a + i);

/** Trapèze simplifié : large au nord, étroit au sud (comme le vrai domaine AROME). */
export const TRAPEZE = [[-12, 54.7], [16, 54.7], [12.2, 37.5], [-8.2, 37.5], [-12, 54.7]];

/** variables : { nom: [premiere, derniere] } ; tirage complet si derniere = 51. */
export function tirage(jour, heure, variables) {
  const vars = {};
  let complet = true;
  for (const [v, [a, b]] of Object.entries(variables)) {
    const attendu = v === 'gust10' ? 1 : 0;
    vars[v] = { steps: intervalle(a, b), complete: a === attendu && b === 51 };
    complet = complet && vars[v].complete;
  }
  const id = `2026-10-${String(jour).padStart(2, '0')}T${String(heure).padStart(2, '0')}Z`;
  return { run: iso(jour, heure), id, complete: complet, path: id, variables: vars };
}

const COMPLET = { t2m: [0, 51], wind10: [0, 51], gust10: [1, 51] };

/** Manifest de référence : HD 03Z complet, HD 06Z tronqué (0-18), AROME 06Z complet + pression. */
export function manifest({ hd = [tirage(17, 3, COMPLET), tirage(17, 6, { t2m: [0, 18], wind10: [0, 18], gust10: [1, 18] })],
  arome = [tirage(17, 6, { ...COMPLET, mslp: [0, 51] })] } = {}) {
  const modele = (label, res, runs) => ({
    label, resolution_deg: res, grid: [-12, 37.5, 16, 55.4], horizon_h: 51,
    domain: { polygon: TRAPEZE, vertices: 4, source: 'synthétique', tolerance_deg: 0.04 }, runs,
  });
  return {
    version: 1, generated: iso(17, 16), attribution: 'Source : Météo-France, Licence Ouverte v2.0',
    file_template: '{model}/{run_id}/{variable}/{step}.pmtiles',
    models: { arome: modele('AROME', 0.025, arome), 'arome-hd': modele('AROME HD', 0.01, hd) },
    variables: { t2m: {}, wind10: {}, gust10: {}, mslp: {} },
    couches: {
      vent: { nom: 'Vent', variable: 'wind10' }, rafales: { nom: 'Rafales', variable: 'gust10' },
      t2m: { nom: 'Température', variable: 't2m' }, pression: { nom: 'Pression', variable: 'mslp' },
    },
  };
}

/** Vue centrée sur la France (largement dans le trapèze). */
export const VUE_FRANCE = [-1, 44, 4, 48];
