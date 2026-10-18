// SPDX-License-Identifier: EUPL-1.2
// Tests de viewer/js/modele-auto.js : lancer `node --test viewer/tests/`.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  choisirModele, choisirTirage, plageTemps, attribution, etatCouche, resoudre, dansDomaine,
  tValideDe, echeanceDe, cheminFichier, normaliserBounds, ZOOM_HD, HYSTERESIS,
} from '../js/modele-auto.js';
import { manifest, tirage, iso, ms, VUE_FRANCE } from './aide.mjs';

const M = manifest();
const choix = (o) => choisirModele({ manifest: M, couche: 't2m', bounds: VUE_FRANCE, ...o });

// ---------------------------------------------------------------- modèle : zoom et hystérésis
test('sous le zoom 7 : AROME ; à partir du zoom 7 : HD', () => {
  assert.equal(choix({ zoom: 6.99 }).modele, 'arome');
  assert.equal(choix({ zoom: 6.99 }).raison, 'zoom_faible');
  assert.equal(choix({ zoom: ZOOM_HD }).modele, 'arome-hd');
  assert.equal(choix({ zoom: 9 }).modele, 'arome-hd');
});

test('hystérésis : en HD on reste jusqu\'à 7 - marge, en AROME on attend 7', () => {
  assert.equal(choix({ zoom: 6.9, precedent: 'arome-hd' }).modele, 'arome-hd');
  assert.equal(choix({ zoom: ZOOM_HD - HYSTERESIS, precedent: 'arome-hd' }).modele, 'arome-hd');
  assert.equal(choix({ zoom: ZOOM_HD - HYSTERESIS - 0.01, precedent: 'arome-hd' }).modele, 'arome');
  assert.equal(choix({ zoom: 6.9, precedent: 'arome' }).modele, 'arome');
  assert.equal(choix({ zoom: 7.0, precedent: 'arome' }).modele, 'arome-hd');
  // le résultat précédent entier est accepté
  const p = choix({ zoom: 7.2 });
  assert.equal(choix({ zoom: 6.9, precedent: p }).modele, 'arome-hd');
});

test('zoom qui oscille autour de 7 : au plus une bascule', () => {
  let prec = null, bascules = 0;
  for (const z of [6.9, 6.95, 7.0, 6.95, 7.05, 6.9, 7.02, 6.98, 6.92, 7.01]) {
    const r = choix({ zoom: z, precedent: prec });
    if (prec && r.modele !== prec) bascules++;
    prec = r.modele;
  }
  assert.ok(bascules <= 1, `bascules : ${bascules}`);
});

// ---------------------------------------------------------------- modèle : domaine en trapèze
test('vue dans le trapèze : HD ; coin sud-ouest de la grille (hors trapèze) : AROME', () => {
  assert.equal(choix({ zoom: 8, bounds: [-1, 44, 4, 48] }).modele, 'arome-hd');
  // dans le rectangle de la grille mais hors du trapèze
  const r = choix({ zoom: 8, bounds: [-11.8, 38, -10.5, 39] });
  assert.equal(r.modele, 'arome');
  assert.equal(r.raison, 'hors_domaine');
  assert.equal(r.dansDomaine, false);
});

test('vue qui sort du trapèze en panoramique : tolérance, avec hystérésis de domaine', () => {
  // le bord ouest passe à x = -8.2 - (y-37.5) * 3.8/17.2 ; à y = 40 : x = -8.75
  const depasse = [-8.85, 39.5, -7, 41]; // dépasse d'environ 0,1 degré à l'ouest, bord sud-ouest
  assert.equal(dansDomaine(depasse, 'arome-hd', M, 0), false);
  assert.equal(choix({ zoom: 8, bounds: depasse }).modele, 'arome');                       // entrée : strict
  assert.equal(choix({ zoom: 8, bounds: depasse, precedent: 'arome-hd' }).modele, 'arome-hd'); // sortie : tolérant
  const loin = [-12.5, 39.5, -10, 41];
  assert.equal(choix({ zoom: 8, bounds: loin, precedent: 'arome-hd' }).modele, 'arome');
});

test('formats d\'emprise acceptés', () => {
  const ref = { w: -1, s: 44, e: 4, n: 48 };
  assert.deepEqual(normaliserBounds([-1, 44, 4, 48]), ref);
  assert.deepEqual(normaliserBounds([[-1, 44], [4, 48]]), ref);
  assert.deepEqual(normaliserBounds({ west: -1, south: 44, east: 4, north: 48 }), ref);
  assert.deepEqual(normaliserBounds({ toArray: () => [[-1, 44], [4, 48]] }), ref);
  assert.equal(normaliserBounds(null), null);
  assert.equal(choix({ zoom: 8, bounds: null }).modele, 'arome-hd'); // pas d'emprise : pas de refus
});

test('polygone concave : un coin dans le polygone ne suffit pas', () => {
  const m = manifest();
  // en U : le creux (x entre 4 et 6, y >= 2) est hors domaine
  m.models['arome-hd'].domain.polygon = [[0, 0], [10, 0], [10, 10], [6, 10], [6, 2], [4, 2], [4, 10], [0, 10], [0, 0]];
  assert.equal(dansDomaine([1, 3, 9, 9], 'arome-hd', m), false); // les 4 coins sont dedans, le creux traverse
  assert.equal(dansDomaine([1, 3, 3, 9], 'arome-hd', m), true);
});

// ---------------------------------------------------------------- modes forcés
test('modes forcés', () => {
  assert.equal(choix({ zoom: 3, mode: 'arome-hd' }).modele, 'arome-hd');
  assert.equal(choix({ zoom: 3, mode: 'arome-hd' }).raison, 'force');
  assert.equal(choix({ zoom: 10, mode: 'arome' }).modele, 'arome');
});

// ---------------------------------------------------------------- repli par couche (variable absente)
test('pression : absente du HD, bascule sur AROME sans erreur, même en mode HD forcé', () => {
  const auto = choix({ zoom: 9, couche: 'pression' });
  assert.equal(auto.modele, 'arome');
  assert.equal(auto.repli, true);
  assert.equal(auto.raison, 'variable_absente_hd');
  assert.equal(auto.demande, 'arome-hd');
  assert.equal(choix({ zoom: 9, couche: 'pression', mode: 'arome-hd' }).modele, 'arome');
  assert.equal(choix({ zoom: 4, couche: 'pression' }).repli, false);
  // le vent, lui, reste en HD
  assert.equal(choix({ zoom: 9, couche: 'vent' }).modele, 'arome-hd');
});

test('couche inconnue, ou variable qu\'aucun modèle ne fournit', () => {
  assert.equal(choix({ zoom: 9, couche: 'foudre' }).modele, null);
  assert.equal(choix({ zoom: 9, couche: 'foudre' }).raison, 'couche_inconnue');
  const m = manifest({ arome: [tirage(17, 6, { t2m: [0, 51] })] }); // plus de mslp nulle part
  const r = choisirModele({ zoom: 5, bounds: VUE_FRANCE, couche: 'pression', manifest: m });
  assert.equal(r.modele, null);
  assert.equal(r.raison, 'variable_absente');
});

test('HD sans aucun tirage publié : AROME', () => {
  const m = manifest({ hd: [] });
  assert.equal(choisirModele({ zoom: 9, bounds: VUE_FRANCE, couche: 't2m', manifest: m }).modele, 'arome');
});

test('une couche peut être désignée par sa variable', () => {
  assert.equal(choix({ zoom: 9, couche: 'wind10' }).variable, 'wind10');
});

// ---------------------------------------------------------------- temps valide <-> (tirage, échéance)
test('conversions temps valide / échéance', () => {
  assert.equal(tValideDe(iso(17, 3), 6), ms(17, 9));
  assert.equal(echeanceDe(iso(17, 6), ms(17, 9)), 3);
  assert.equal(echeanceDe(iso(17, 6), ms(17, 9, 30)), 3.5);
  assert.equal(echeanceDe(iso(17, 6), new Date(ms(17, 5))), -1);
  assert.equal(echeanceDe(iso(17, 6), '2026-10-17T09:00:00Z'), 3);
});

test('deux tirages à 3 h d\'écart : même instant, échéances différentes', () => {
  const m = manifest({ hd: [tirage(17, 3, { t2m: [0, 51] })], arome: [tirage(17, 6, { t2m: [0, 51] })] });
  const t = ms(17, 9);
  const hd = choisirTirage({ modele: 'arome-hd', variable: 't2m', tValide: t, manifest: m });
  const ar = choisirTirage({ modele: 'arome', variable: 't2m', tValide: t, manifest: m });
  assert.equal(hd.id, '2026-10-17T03Z');
  assert.equal(hd.step, 6);
  assert.equal(ar.id, '2026-10-17T06Z');
  assert.equal(ar.step, 3);
  assert.equal(tValideDe(hd.run, hd.step), tValideDe(ar.run, ar.step));
  assert.equal(cheminFichier(m, 'arome', ar.id, 't2m', ar.step), 'arome/2026-10-17T06Z/t2m/03.pmtiles');
});

test('instant entre deux heures : échéances voisines du même tirage et poids', () => {
  const r = choisirTirage({ modele: 'arome', variable: 't2m', tValide: ms(17, 9, 30), manifest: M });
  assert.equal(r.step, 3);
  assert.equal(r.stepSuivant, 4);
  assert.equal(r.fraction, 0.5);
  assert.equal(r.tValide, ms(17, 9, 30));
});

test('avant le premier instant du tirage, et variable sans échéance 0 (rafales)', () => {
  assert.equal(choisirTirage({ modele: 'arome', variable: 't2m', tValide: ms(17, 5), manifest: M }), null);
  assert.equal(choisirTirage({ modele: 'arome', variable: 'gust10', tValide: ms(17, 6), manifest: M }), null);
  assert.equal(choisirTirage({ modele: 'arome', variable: 'gust10', tValide: ms(17, 7), manifest: M }).step, 1);
});

// ---------------------------------------------------------------- tirage HD incomplet (R11)
test('HD 06Z (0-18 h) : le plus récent tant qu\'il a l\'échéance, puis repli sur HD 03Z', () => {
  const pris = (t) => choisirTirage({ modele: 'arome-hd', variable: 't2m', tValide: t, manifest: M });
  assert.equal(pris(ms(17, 10)).id, '2026-10-17T06Z');   // 06Z + 4 h
  assert.equal(pris(ms(17, 10)).plusRecent, true);
  assert.equal(pris(ms(18, 0)).id, '2026-10-17T06Z');    // 06Z + 18 h : dernière échéance
  const apres = pris(ms(18, 1));                          // 06Z + 19 h : absente
  assert.equal(apres.id, '2026-10-17T03Z');
  assert.equal(apres.step, 22);
  assert.equal(apres.plusRecent, false);
  assert.equal(apres.complete, true);
  // avant 06Z, seul le 03Z existe
  assert.equal(pris(ms(17, 5)).id, '2026-10-17T03Z');
});

test('instant fractionnaire à la frontière d\'un tirage incomplet : jamais deux tirages dans une image', () => {
  // 17h30 après 06Z = 18/10 00:30 : 06Z a l'échéance 18 mais pas 19 ; 03Z a 21 et 22
  const r = choisirTirage({ modele: 'arome-hd', variable: 't2m', tValide: ms(18, 0, 30), manifest: M });
  assert.equal(r.id, '2026-10-17T03Z');
  assert.equal(r.step, 21);
  assert.equal(r.stepSuivant, 22);
  // à 06Z + 18 h pile : le tirage récent suffit
  assert.equal(choisirTirage({ modele: 'arome-hd', variable: 't2m', tValide: ms(18, 0), manifest: M }).id, '2026-10-17T06Z');
});

test('repli sur AROME quand le HD n\'a plus l\'échéance', () => {
  const t = ms(19, 8); // HD 03Z s'arrête à 03Z + 51 h = 19/10 06:00 ; AROME 06Z va jusqu'à 19/10 09:00
  const r = resoudre({ zoom: 9, bounds: VUE_FRANCE, couche: 't2m', manifest: M, tValide: t });
  assert.equal(r.choix.modele, 'arome-hd');
  assert.equal(r.modele, 'arome');
  assert.equal(r.repliTemps, true);
  assert.equal(r.tirage.id, '2026-10-17T06Z');
  assert.equal(r.tirage.step, 50);
  // dans la plage HD : pas de repli
  const ok = resoudre({ zoom: 9, bounds: VUE_FRANCE, couche: 't2m', manifest: M, tValide: ms(17, 12) });
  assert.equal(ok.modele, 'arome-hd');
  assert.equal(ok.repliTemps, false);
});

// ---------------------------------------------------------------- frise
test('plageTemps : du premier au dernier instant, tous modèles ou un seul', () => {
  const p = plageTemps(M, 't2m');
  assert.equal(p.debut, ms(17, 3));                 // HD 03Z échéance 0
  assert.equal(p.fin, ms(19, 9));                   // AROME 06Z + 51 h
  assert.deepEqual(p.trous, []);
  assert.deepEqual(p.modeles.sort(), ['arome', 'arome-hd']);
  assert.equal(plageTemps(M, 't2m', { modele: 'arome-hd' }).fin, ms(19, 6));
  assert.equal(plageTemps(M, 't2m', { modele: 'arome' }).debut, ms(17, 6));
  assert.equal(plageTemps(M, 'rafales').debut, ms(17, 4));   // pas d'échéance 0 pour les rafales
  assert.equal(plageTemps(M, 'pression').debut, ms(17, 6));  // seulement AROME
  assert.equal(plageTemps(M, 't2m', { depuis: ms(17, 12) }).debut, ms(17, 12));
  assert.equal(plageTemps(M, 'inconnue'), null);
});

test('plageTemps : un trou de publication est signalé', () => {
  const m = manifest({ hd: [], arome: [tirage(17, 6, { t2m: [0, 5] }), tirage(17, 12, { t2m: [0, 51] })] });
  // 06Z couvre jusqu'à 11:00, 12Z commence à 12:00 : pas de trou ; on perce 11:00
  m.models.arome.runs[0].variables.t2m.steps = [0, 1, 2, 3, 4];
  const p = plageTemps(m, 't2m');
  assert.deepEqual(p.trous, [ms(17, 11)]);
});

test('toute la frise est servie, du premier au dernier instant, à tous les zooms', () => {
  const p = plageTemps(M, 't2m');
  for (const zoom of [4, 8]) {
    for (let t = p.debut; t <= p.fin; t += 3600e3) {
      const r = resoudre({ zoom, bounds: VUE_FRANCE, couche: 't2m', manifest: M, tValide: t });
      assert.ok(r.tirage, `zoom ${zoom}, t = ${new Date(t).toISOString()} : ${r.etat.raison}`);
      assert.equal(tValideDe(r.tirage.run, r.tirage.step), t);
    }
  }
  // au-delà de la fin : indisponible, avec la cause
  const apres = resoudre({ zoom: 8, bounds: VUE_FRANCE, couche: 't2m', manifest: M, tValide: p.fin + 3600e3 });
  assert.equal(apres.tirage, null);
  assert.equal(apres.etat.raison, 'echeance_non_publiee');
  assert.equal(apres.etat.detail, 'apres_derniere_echeance');
});

// ---------------------------------------------------------------- attribution
test('attribution', () => {
  assert.equal(attribution('arome-hd', M), 'Source : Météo-France, Licence Ouverte v2.0 · modèle AROME HD 0,01°');
  assert.match(attribution('arome', M), /AROME 0,025°/);
  assert.match(attribution('arome'), /Météo-France, Licence Ouverte v2\.0/);
  assert.equal(attribution(null, M), 'Source : Météo-France, Licence Ouverte v2.0');
});

// ---------------------------------------------------------------- etatCouche
test('etatCouche : pourquoi une couche est indisponible', () => {
  const base = { manifest: M, tValide: ms(17, 12), bounds: VUE_FRANCE };
  const ok = etatCouche({ ...base, couche: 'vent', modele: 'arome-hd' });
  assert.equal(ok.disponible, true);
  assert.equal(ok.raison, 'ok');
  assert.equal(ok.tirage.id, '2026-10-17T06Z');
  assert.equal(ok.partiel, false);

  const abs = etatCouche({ ...base, couche: 'pression', modele: 'arome-hd' });
  assert.deepEqual([abs.disponible, abs.raison], [false, 'variable_absente_modele']);
  assert.ok(abs.message.length > 0);

  assert.equal(etatCouche({ ...base, couche: 'foudre', modele: 'arome' }).raison, 'couche_inconnue');

  const hors = etatCouche({ ...base, couche: 'vent', modele: 'arome', bounds: [-30, 20, -25, 25] });
  assert.equal(hors.raison, 'hors_domaine');

  const partiel = etatCouche({ ...base, couche: 'vent', modele: 'arome', bounds: [-20, 44, -5, 48] });
  assert.equal(partiel.disponible, true);
  assert.equal(partiel.partiel, true);

  const avant = etatCouche({ ...base, couche: 'vent', modele: 'arome', tValide: ms(17, 4) });
  assert.deepEqual([avant.raison, avant.detail], ['echeance_non_publiee', 'avant_premiere_echeance']);
  const apres = etatCouche({ ...base, couche: 'vent', modele: 'arome-hd', tValide: ms(19, 7) });
  assert.deepEqual([apres.raison, apres.detail], ['echeance_non_publiee', 'apres_derniere_echeance']);
});

test('etatCouche : trou intermédiaire', () => {
  const m = manifest({ hd: [], arome: [tirage(17, 6, { t2m: [0, 51] })] });
  m.models.arome.runs[0].variables.t2m.steps = [0, 1, 2, 4, 5];
  const e = etatCouche({ couche: 't2m', modele: 'arome', tValide: ms(17, 9), manifest: m });
  assert.equal(e.detail, 'trou');
});

test('resoudre : couche absente de tous les modèles', () => {
  const r = resoudre({ zoom: 8, bounds: VUE_FRANCE, couche: 'foudre', manifest: M, tValide: ms(17, 12) });
  assert.equal(r.modele, null);
  assert.equal(r.etat.raison, 'couche_inconnue');
});

// ---------------------------------------------------------------- vrai manifest (généré par pipeline.manifest)
const CHEMIN_REEL = new URL('../../out/manifest/manifest.json', import.meta.url);
let reel = null;
try { reel = JSON.parse(readFileSync(CHEMIN_REEL, 'utf8')); } catch { /* absent : tests ignorés */ }
const sansReel = { skip: reel ? false : 'out/manifest/manifest.json absent (lancer python3 -m pipeline.manifest)' };

test('vrai manifest : structure et tirage HD 06Z incomplet', sansReel, () => {
  assert.equal(reel.version, 1);
  assert.match(reel.attribution, /Licence Ouverte v2\.0/);
  assert.ok(reel.couches.vent && reel.couches.t2m);
  assert.equal(reel.couches.pression, undefined);
  const hd = Object.fromEntries(reel.models['arome-hd'].runs.map(r => [r.id, r]));
  if (hd['2026-10-17T06Z'] && hd['2026-10-17T03Z']) {
    assert.equal(hd['2026-10-17T06Z'].complete, false);
    assert.equal(hd['2026-10-17T03Z'].complete, true);
    const pris = (t) => choisirTirage({ modele: 'arome-hd', variable: 't2m', tValide: t, manifest: reel });
    assert.equal(pris(ms(17, 10)).id, '2026-10-17T06Z');
    assert.equal(pris(ms(18, 0)).id, '2026-10-17T06Z');
    assert.equal(pris(ms(18, 1)).id, '2026-10-17T03Z');
    assert.equal(pris(ms(18, 1)).step, 22);
  }
});

test('vrai manifest : domaine réel (trapèze à bords courbes) et choix du modèle', sansReel, () => {
  const v = (bounds, zoom = 8, precedent = null) => choisirModele({ zoom, bounds, couche: 'vent', manifest: reel, precedent });
  assert.equal(v([1.5, 48.5, 3.5, 49.5]).modele, 'arome-hd');          // Île-de-France
  assert.equal(v([-4, 47.5, -3, 48.2]).modele, 'arome-hd');            // Bretagne
  assert.equal(v([-11.8, 38, -10.5, 39]).modele, 'arome');              // coin sud-ouest de la grille, hors domaine
  assert.equal(v([13, 38, 15, 39.5]).modele, 'arome');                  // coin sud-est
  assert.equal(v([1.5, 48.5, 3.5, 49.5], 5).modele, 'arome');
  const sommets = reel.models['arome-hd'].domain.polygon.length;
  assert.ok(sommets >= 5 && sommets <= 60, `sommets : ${sommets}`);
});

test('vrai manifest : frise de bout en bout', sansReel, () => {
  const p = plageTemps(reel, 'vent');
  assert.ok(p.fin > p.debut);
  assert.deepEqual(p.trous, []);
  for (const couche of Object.keys(reel.couches)) {
    const pc = plageTemps(reel, couche);
    for (let t = pc.debut; t <= pc.fin; t += 3600e3) {
      for (const zoom of [5, 8]) {
        const r = resoudre({ zoom, bounds: VUE_FRANCE, couche, manifest: reel, tValide: t });
        assert.ok(r.tirage, `${couche} zoom ${zoom} ${new Date(t).toISOString()} : ${r.etat.raison}`);
        assert.equal(tValideDe(r.tirage.run, r.tirage.step), t);
      }
    }
  }
});
