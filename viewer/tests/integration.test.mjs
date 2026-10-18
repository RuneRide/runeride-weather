// SPDX-License-Identifier: EUPL-1.2
// Tests de la logique pure de l'intégration J3 (viewer/js/integration-logique.js).
import test from 'node:test';
import assert from 'node:assert/strict';
import * as L from '../js/integration-logique.js';
import { manifest, ms } from './aide.mjs';
import { resoudre } from '../js/modele-auto.js';

const M = manifest();

test('manifestDepuisIndex : structure, tirage incomplet, couches', () => {
  const m = L.manifestDepuisIndex({ 'arome-hd': { '2026-10-17T06Z': { t2m: [0, 1, 2, 3], gust10: [1, 2, 3], wind10: [0, 1, 2, 3] } } });
  const r = m.models['arome-hd'].runs[0];
  assert.equal(r.run, '2026-10-17T06:00:00Z');
  assert.equal(r.variables.t2m.complete, true);
  assert.equal(r.valid_fin, '2026-10-17T09:00:00Z');
  assert.deepEqual(Object.keys(m.couches).sort(), ['rafales', 't2m', 'vent']);
  assert.equal(m.couches.vent.variable, 'wind10');
});

test('frise : conversions, bornes, bouclage, pas clavier', () => {
  const d = ms(17, 3), f = ms(19, 6);
  assert.equal(L.positionFrise(ms(17, 5, 30), d, f), 2.5);
  assert.equal(L.positionFrise(ms(10, 0), d, f), 0);
  assert.equal(L.tempsDepuisFrise(2.5, d, f), ms(17, 5, 30));
  assert.equal(L.tempsDepuisFrise(1000, d, f), f);
  assert.equal(L.avancer(ms(17, 3), 1, 4, d, f), ms(17, 7));
  assert.equal(L.avancer(f - 3600e3, 2, 1, d, f), d + 3600e3);       // dépasse la fin : reboucle
  assert.equal(L.pasClavier(ms(17, 5, 40), 1, d, f), ms(17, 7));
  assert.equal(L.pasClavier(ms(17, 5), -6, d, f), d);
});

test('formaterTemps : heure locale avec décalage, UTC en infobulle', () => {
  const t = Date.UTC(2026, 9, 17, 12, 30);
  const p = L.formaterTemps(t, 'Europe/Paris');
  assert.match(p.local, /14:30/);
  assert.match(p.decalage, /UTC\+2/);
  assert.equal(p.utc, '12:30');
  assert.match(p.infobulle, /12:30 UTC/);
  const h = L.formaterTemps(Date.UTC(2026, 11, 17, 12, 30), 'Europe/Paris');   // hiver : UTC+1, pas d'ambiguïté
  assert.match(h.local, /13:30/);
  assert.match(h.decalage, /UTC\+1/);
});

test('hash : aller-retour, ancien format ignoré, mode normalisé', () => {
  const s = L.ecrireHash({ couche: 'vent', tValide: Date.UTC(2026, 9, 17, 14, 30), centre: { lat: 46.5, lng: 2.25 }, zoom: 6.234, mode: 'arome-hd', particules: true, isobares: false });
  assert.equal(s, 'couche=vent&t=2026-10-17T14:30&c=46.500,2.250&z=6.23&m=arome-hd&p=1&i=0');
  const h = L.lireHash('#' + s);
  assert.equal(h.tValide, Date.UTC(2026, 9, 17, 14, 30));
  assert.deepEqual(h.centre, { lat: 46.5, lng: 2.25 });
  assert.equal(h.mode, 'arome-hd'); assert.equal(h.particules, true); assert.equal(h.isobares, false);
  const vieux = L.lireHash('#couche=vent&t=3.50&c=46,2&z=6');
  assert.equal(vieux.tValide, null);
  assert.equal(vieux.mode, null);
  assert.equal(L.normaliserMode('HD'), 'arome-hd'); assert.equal(L.normaliserMode('x'), 'auto');
});

test('bascule : on garde l’ancien calque tant que le nouveau n’est pas prêt', () => {
  const res = (modele, id) => ({ modele, tirage: { id, variable: 't2m' } });
  const arome = res('arome', '2026-10-17T06Z'), hd = res('arome-hd', '2026-10-17T03Z');
  assert.equal(L.decisionBascule({ affiche: null, desire: hd, pret: false }), 'echanger');            // premier affichage
  assert.equal(L.decisionBascule({ affiche: arome, desire: hd, pret: false }), 'attendre');
  assert.equal(L.decisionBascule({ affiche: arome, desire: hd, pret: true }), 'echanger');
  assert.equal(L.decisionBascule({ affiche: hd, desire: hd, pret: false }), 'echanger');              // même source : suit l'échéance
  assert.equal(L.decisionBascule({ affiche: arome, desire: hd, pret: false, changementCouche: true }), 'echanger');
  assert.equal(L.decisionBascule({ affiche: arome, desire: hd, pret: false, ancienValide: false }), 'echanger');
  assert.equal(L.decisionBascule({ affiche: arome, desire: null, pret: true }), 'garder');
});

test('pastille et avis de repli', () => {
  const vue = { zoom: 8, bounds: [-1, 45, 1, 47], couche: 't2m', manifest: M, tValide: ms(17, 9) };
  const hd = resoudre({ ...vue, tValide: ms(17, 4) });             // avant le tirage 06 h : seul le 03 h existe
  assert.match(L.textePastille(hd, M), /AROME HD.*tirage 03 h UTC/);
  assert.equal(L.avisRepli(hd, { manifest: M }), '');
  assert.match(L.textePastille(resoudre(vue), M), /AROME HD.*tirage 06 h UTC/);   // 09 h : le 06 h plus récent a l'échéance
  const arome = resoudre({ ...vue, zoom: 5 });
  assert.match(L.textePastille(arome, M), /AROME.*tirage 06 h UTC/);
  const pression = resoudre({ ...vue, couche: 'vent', mode: 'arome-hd', tValide: ms(17, 9) });
  assert.equal(pression.modele, 'arome-hd');
  // tirage de repli : HD 06Z tronqué à 18 h, instant 17/10 23:00 + 4 h = 18/10 03:00 -> HD 03Z, le 06Z est plus récent mais sans l'échéance
  const repli = resoudre({ ...vue, tValide: ms(18, 3) });
  assert.equal(repli.tirage.id, '2026-10-17T03Z');
  assert.equal(repli.tirage.plusRecent, false);
  assert.match(L.avisRepli(repli, { manifest: M }), /Tirage 03 h.*plus récent/);
  const horsHD = resoudre({ ...vue, bounds: [-12.5, 37.0, -11.5, 37.3] });
  assert.equal(horsHD.modele, 'arome');
  assert.match(L.avisRepli(horsHD, { manifest: M }), /hors du domaine HD/);
});

test('avis de repli par couche (variable absente du HD)', () => {
  const m = manifest({ hd: [], arome: undefined });
  const res = resoudre({ zoom: 9, bounds: [-1, 45, 1, 47], couche: 't2m', manifest: m, tValide: ms(17, 9) });
  assert.equal(res.modele, 'arome');
  assert.match(L.avisRepli(res, { manifest: m }), /absente de AROME HD/);
});

test('isobares : échéance AROME la plus proche, bornes, plage', () => {
  const e = L.echeanceIsobares(M, ms(17, 9, 20));
  assert.deepEqual([e.id, e.echeance, e.src, e.hors], ['2026-10-17T06Z', 3, 'arome/2026-10-17T06Z', false]);
  assert.equal(L.echeanceIsobares(M, ms(17, 9, 40)).echeance, 4);
  const avant = L.echeanceIsobares(M, ms(17, 3));
  assert.equal(avant.echeance, 0); assert.equal(avant.hors, true);
  assert.equal(L.echeanceIsobares(M, ms(25, 0)).echeance, 51);
  assert.deepEqual(L.plageIsobares(M), { debut: ms(17, 6), fin: ms(17, 6) + 51 * 3600e3 });
  assert.equal(L.echeanceIsobares({ models: {} }, ms(17, 6)), null);
});
