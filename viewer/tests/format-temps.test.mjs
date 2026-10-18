// SPDX-License-Identifier: EUPL-1.2
// Tests du formatage des dates (viewer/js/format-temps.js) : dates fixes, heure d'été / d'hiver de Paris comprises.
import test from 'node:test';
import assert from 'node:assert/strict';
import * as F from '../js/format-temps.js';

const u = (m, j, h, mi = 0) => Date.UTC(2026, m - 1, j, h, mi);

test('tirage complet : UTC, heure locale, échéance, modèle', () => {
  const d = F.decrireTirage({ run: '2026-10-17T03:00:00Z', tValide: u(10, 18, 14), modele: 'arome-hd', libelle: 'AROME HD 0,01°' });
  assert.equal(d.longue, 'AROME HD 0,01° · tirage du sam. 17 oct. à 03 h UTC (05 h Paris) · +35 h');
  assert.equal(d.compacte, 'HD · 17/10 03 h UTC · +35 h');
  assert.equal(d.tirage, 'Tirage du sam. 17 oct. à 03 h UTC (05 h Paris)');
  const a = F.decrireTirage({ run: '2026-10-17T06:00:00Z', tValide: u(10, 18, 14), modele: 'arome', libelle: 'AROME 0,025°' });
  assert.equal(a.compacte, 'AROME · 17/10 06 h UTC · +32 h');
  assert.match(a.longue, /07 h UTC|06 h UTC \(08 h Paris\)/);
});

test('échéance fractionnaire', () => {
  assert.equal(F.formatEcheance(u(10, 17, 3), u(10, 18, 14, 30)), '+35,5 h');
  assert.equal(F.formatEcheance(u(10, 17, 3), u(10, 17, 3)), '+0 h');
});

test('frise : jour et heure complets, local puis UTC', () => {
  const e = F.etiquetteFrise(u(10, 18, 12));
  assert.equal(e.complet, 'dim. 18 oct. 14 h Paris · 12 h UTC');
  assert.equal(F.etiquetteFrise(u(10, 18, 12, 30)).local, 'dim. 18 oct. 14 h 30 Paris');
  // le jour affiché est celui de l'heure LOCALE : 23 h UTC le 17 = 01 h Paris le 18
  assert.equal(F.etiquetteFrise(u(10, 17, 23)).complet, 'dim. 18 oct. 01 h Paris · 23 h UTC');
});

test('heure d’été : jamais d’heure locale fausse (UTC+2 avant le 25/10 à 01 h UTC, UTC+1 ensuite)', () => {
  const att = [
    [u(10, 17, 3), '05 h Paris'], [u(10, 24, 23, 30), '01 h 30 Paris'],
    [u(10, 25, 2), '03 h Paris'], [u(10, 25, 3), '04 h Paris'], [u(10, 25, 12), '13 h Paris'],
    [u(3, 29, 0, 30), '01 h 30 Paris'], [u(3, 29, 1), '03 h Paris'], [u(3, 29, 3), '05 h Paris'],     // printemps : 02 h n'existe pas
    [u(1, 15, 12), '13 h Paris'], [u(7, 15, 12), '14 h Paris'],
  ];
  for (const [ms, h] of att) assert.equal(F.heureLocale(ms), h, new Date(ms).toISOString());
});

test('nuit du passage à l’heure d’hiver : 02 h existe deux fois, le décalage lève l’ambiguïté', () => {
  assert.equal(F.heureLocale(u(10, 25, 0, 30)), '02 h 30 Paris (UTC+2)');
  assert.equal(F.heureLocale(u(10, 25, 1, 30)), '02 h 30 Paris (UTC+1)');
  assert.notEqual(F.etiquetteFrise(u(10, 25, 0, 30)).complet, F.etiquetteFrise(u(10, 25, 1, 30)).complet);
  assert.equal(F.heureAmbigue(u(10, 24, 23, 30)), false);
  assert.equal(F.heureAmbigue(u(10, 25, 3, 30)), false);
  assert.equal(F.heureAmbigue(u(7, 1, 12)), false);
});

test('décalage et fuseau explicite', () => {
  assert.equal(F.decalage(u(10, 18, 12)), 'UTC+2');
  assert.equal(F.decalage(u(10, 25, 12)), 'UTC+1');
  assert.equal(F.heureLocale(u(10, 18, 12), 'America/New_York'), '08 h New York');
  assert.equal(F.nomVille('Europe/Paris'), 'Paris');
});

test('âge relatif et fraîcheur', () => {
  const t = u(10, 17, 13, 40);
  assert.equal(F.ageRelatif(t, t + 20e3), "à l'instant");
  assert.equal(F.ageRelatif(t, t + 35 * 60e3), 'il y a 35 min');
  assert.equal(F.ageRelatif(t, t + 21 * 3600e3), 'il y a 21 h');
  assert.equal(F.ageRelatif(t, t + 47 * 3600e3), 'il y a 47 h');
  assert.equal(F.ageRelatif(t, t + 3 * 24 * 3600e3), 'il y a 3 j');
  assert.equal(F.ageRelatif(t, t - 5 * 60e3), "à l'instant");               // horloge locale en retard : jamais « dans le futur »
  assert.equal(F.formatTraite('2026-10-17T13:40:00Z', u(10, 18, 11)), 'traité le 17/10 à 13 h 40 UTC (il y a 21 h)');
  assert.equal(F.formatTraite('pas une date', 0), '');
});

test('autre source (couche utilisant un autre tirage ou modèle)', () => {
  const l = F.ligneAutreSource({ nom: 'Pression', libelle: 'AROME 0,025°', run: '2026-10-17T06:00:00Z', tValide: u(10, 18, 12) });
  assert.equal(l, 'Pression : AROME 0,025°, tirage du 17/10 à 06 h UTC (08 h Paris), +30 h');
});
