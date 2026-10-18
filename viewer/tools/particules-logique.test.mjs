// SPDX-License-Identifier: EUPL-1.2
// Tests de la logique pure des particules. Usage : node --test viewer/tools/
import test from 'node:test';
import assert from 'node:assert/strict';
import * as L from '../js/particules-logique.js';
import { resoudreEcheances } from '../js/particules-champ.js';
import { COUCHES, couleurPalette, texturePalette } from '../js/config.js';

test('hash32 : valeurs de référence (lowbias32) et déterminisme', () => {
  assert.equal(L.hash32(0), 0);
  assert.equal(L.hash32(1), 0x688990c0, 'valeur de non-régression (le GPU doit donner la même, voir autotestGpu)');
  assert.equal(L.hash32(12345), L.hash32(12345));
  assert.notEqual(L.hash32(1), L.hash32(2));
});

test('alea : dans [0,1[, quasi uniforme, représentable en float32', () => {
  const N = 20000, bins = new Array(10).fill(0);
  let somme = 0;
  for (let i = 0; i < N; i++) {
    const a = L.alea(i, 7, 0);
    assert.ok(a >= 0 && a < 1);
    assert.equal(Math.fround(a), a);
    bins[Math.floor(a * 10)]++; somme += a;
  }
  assert.ok(Math.abs(somme / N - 0.5) < 0.01);
  for (const b of bins) assert.ok(Math.abs(b - N / 10) < N / 10 * 0.1, 'histogramme plat à 10 % près');
  assert.notEqual(L.alea(5, 1, 0), L.alea(5, 2, 0));
  assert.notEqual(L.alea(5, 1, 0), L.alea(5, 1, 1));
});

test('réapparition : sortie d\'écran, hors données, tirage < taux', () => {
  const base = { x: 0.5, y: 0.5, valide: true, tirage: 0.9, taux: 0.1 };
  assert.equal(L.doitReapparaitre(base), false);
  assert.equal(L.doitReapparaitre({ ...base, x: -0.01 }), true);
  assert.equal(L.doitReapparaitre({ ...base, y: 1.2 }), true);
  assert.equal(L.doitReapparaitre({ ...base, x: NaN }), true);
  assert.equal(L.doitReapparaitre({ ...base, valide: false }), true, 'code 0 = pas de donnée : réapparition immédiate');
  assert.equal(L.doitReapparaitre({ ...base, tirage: 0.05 }), true);
  assert.equal(L.doitReapparaitre({ ...base, x: 0, y: 1 }), false, 'bords inclus');
});

test('taux de réapparition : indépendant du pas de temps (durée de vie moyenne respectée)', () => {
  const vie = 2;
  // survie après 1 s en un pas de 1 s ou en 60 pas de 1/60 s : identique
  const survieUnPas = 1 - L.tauxReapparition(1, vie);
  const survie60 = Math.pow(1 - L.tauxReapparition(1 / 60, vie), 60);
  assert.ok(Math.abs(survieUnPas - survie60) < 1e-12);
  assert.ok(Math.abs(survieUnPas - Math.exp(-0.5)) < 1e-12);
  assert.equal(L.tauxReapparition(0, vie), 0);
  assert.equal(L.tauxReapparition(1, 0), 1);
  // simulation de Monte-Carlo : durée de vie moyenne ~ vie
  let total = 0; const N = 4000, dt = 1 / 30;
  for (let i = 0; i < N; i++) { let t = 0; for (let k = 0; ; k++) { t += dt; if (L.alea(i, k, 0) < L.tauxReapparition(dt, vie)) break; } total += t; }
  assert.ok(Math.abs(total / N - vie) < 0.1, `vie moyenne ${total / N}`);
});

test('vitesse -> couleur : même rampe que la légende du vent (km/h, 0 à 100)', () => {
  const vent = COUCHES.find(c => c.cle === 'vent');
  assert.equal(L.COUCHE_VENT, vent);
  assert.equal(L.vitesseVersT(0), 0);
  assert.equal(L.vitesseVersT(100 / 3.6), 1);
  assert.equal(L.vitesseVersT(1000), 1, 'saturation au-delà de 100 km/h');
  assert.ok(Math.abs(L.vitesseVersT(50 / 3.6) - 0.5) < 1e-12);
  const tex = texturePalette(vent.palette);
  for (const kmh of [0, 5, 20, 36, 50, 72, 99, 140]) {
    const idx = L.vitesseVersIndice(kmh / 3.6);
    const c = L.vitesseVersCouleur(kmh / 3.6);
    assert.deepEqual(c, [tex[4 * idx], tex[4 * idx + 1], tex[4 * idx + 2]], `${kmh} km/h = texture de palette de la couche couleur`);
  }
  assert.deepEqual(L.vitesseVersCouleur(0), couleurPalette(vent.palette, 0).slice(0, 3).map(Math.round));
  assert.deepEqual(L.vitesseVersCouleur(100 / 3.6), vent.palette.at(-1)[1].slice(0, 3));
  // la couleur change avec la vitesse
  assert.notDeepEqual(L.vitesseVersCouleur(5), L.vitesseVersCouleur(20));
});

test('échelle de déplacement : proportionnelle à la vitesse du vent, réglable, exposant de zoom', () => {
  const o = { pxParMs: 3, exposantZoom: 0, zoomRef: 6 };
  assert.equal(L.vitessePixels(10, 6, o), 30);
  assert.equal(L.vitessePixels(20, 6, o), 2 * L.vitessePixels(10, 6, o));
  assert.equal(L.vitessePixels(10, 9, o), 30, 'exposant 0 : indépendant du zoom');
  assert.equal(L.vitessePixels(10, 7, { ...o, exposantZoom: 1 }), 60);
  assert.equal(L.vitessePixels(10, 6, { ...o, pxParMs: 6 }), 60);
  assert.equal(L.vitessePixels(0, 6, o), 0);
});

test('déplacement : direction (est/nord) -> écran avec matrice MapLibre non pivotée', () => {
  // mercator -> clip : x = 2^z*k * mx, y = -2^z*k * my (y Mercator vers le sud)
  const k = 200;
  const M = new Float64Array(16); M[0] = k; M[5] = -k; M[10] = 1; M[15] = 1;
  const rot = L.rotationDepuisMatrice(M);
  assert.deepEqual(rot.map(Math.abs).map(v => Math.round(v * 1e9) / 1e9), [1, 0, 0, 1]);
  const W = 1000, H = 500;
  let [dx, dy] = L.deplacementPosition(10, 0, 1, rot, W, H, 6);       // vent d'ouest : vers l'est, droite
  assert.ok(Math.abs(dx - 30 / W) < 1e-12 && Math.abs(dy) < 1e-12);
  [dx, dy] = L.deplacementPosition(0, 10, 1, rot, W, H, 6);           // vent du sud : vers le nord, haut (y clip +)
  assert.ok(Math.abs(dx) < 1e-12 && Math.abs(dy - 30 / H) < 1e-12);
  [dx, dy] = L.deplacementPosition(3, 4, 2, rot, W, H, 6);            // 5 m/s pendant 2 s = 30 px
  assert.ok(Math.abs(Math.hypot(dx * W, dy * H) - 30) < 1e-9);
  assert.deepEqual(L.deplacementPosition(0, 0, 1, rot, W, H, 6), [0, 0]);
  // carte tournée de 90 degres : l'est géographique va vers le haut de l'écran
  const R = new Float64Array(16); R[0] = 0; R[1] = k; R[4] = k; R[5] = 0; R[15] = 1;   // clip.x = k*my, clip.y = k*mx
  const r2 = L.rotationDepuisMatrice(R);
  [dx, dy] = L.deplacementPosition(10, 0, 1, r2, W, H, 6);
  assert.ok(Math.abs(dx) < 1e-12 && dy > 0);
});

test('empaquetage 16 bits des positions : aller-retour à 1/65535 près, bornes', () => {
  for (const p of [0, 1, 0.5, 0.123456, 0.999]) {
    const [h, l] = L.empaqueterPosition(p);
    assert.ok(h >= 0 && h <= 255 && l >= 0 && l <= 255);
    assert.ok(Math.abs(L.depaqueterPosition(h, l) - p) <= 0.5 / 65535 + 1e-12);
  }
  assert.deepEqual(L.empaqueterPosition(-3), [0, 0]);
  assert.deepEqual(L.empaqueterPosition(3), [255, 255]);
  for (let c = 0; c <= 65535; c += 257) assert.equal(L.depaqueter16(...L.empaqueter16(c)), c);
});

test('empaquetage 16 bits du vent : pas de 0,002 m/s, code 0 réservé = pas de donnée', () => {
  assert.equal(L.depaqueterVent(0, 0, 0, 0), null);
  assert.equal(L.depaqueterVent(0, 0, 3, 4), null, 'u absent');
  assert.equal(L.depaqueterVent(3, 4, 0, 0), null, 'v absent');
  for (const [u, v] of [[0, 0], [10.5, -3.25], [-63.9, 63.9], [0.38, -0.38], [64, -64]]) {
    const b = L.empaqueterVent(u, v);
    const r = L.depaqueterVent(...b);
    assert.ok(r, 'un vent valide (même nul) n\'est jamais confondu avec « pas de donnée »');
    assert.ok(Math.abs(r[0] - u) < 0.002 && Math.abs(r[1] - v) < 0.002, `${u},${v} -> ${r}`);
  }
  assert.ok(L.codeVent16(-64) >= 1, 'le code minimal est 1');
  assert.equal(L.codeVent16(-1000), 1);
  assert.equal(L.codeVent16(1000), 65535);
  // la quantification wind8 du viewer (0,38 m/s) est reproduite sans perte par les 16 bits
  const wind8 = c => (c - 1) / 254 * 96 - 48;
  for (let c = 1; c <= 255; c++) { const r = L.depaqueterVent(...L.empaqueterVent(wind8(c), wind8(c))); assert.ok(Math.abs(r[0] - wind8(c)) < 0.002); }
});

test('estompage 8 bits : la traînée disparaît complètement (pas de fantômes)', () => {
  const f = L.facteurFondu(0.965, 1 / 60);
  assert.ok(Math.abs(f - 0.965) < 1e-12);
  assert.ok(Math.abs(L.facteurFondu(0.965, 1 / 30) - 0.965 ** 2) < 1e-12, 'indépendant de la cadence');
  let v = 255, n = 0;
  while (v > 0 && n < 1000) { v = L.estomper8(v, f); n++; }
  assert.equal(v, 0); assert.ok(n < 200, `disparaît en ${n} images`);
  // sans retrait minimal, une valeur basse resterait bloquée : démonstration du défaut évité
  let w = 10; for (let i = 0; i < 1000; i++) w = Math.round(w * f);
  assert.ok(w > 0, 'multiplication seule : valeur bloquée > 0');
});

test('nombre de particules : densité constante, mobile plus bas, test de coût', () => {
  const bureau = L.nombreParDefaut({ largeur: 1280, hauteur: 800 });
  const mobile = L.nombreParDefaut({ largeur: 390, hauteur: 844, mobile: true });
  assert.equal(bureau, 10200);
  assert.ok(mobile < bureau / 2);
  assert.ok(mobile <= 6000);
  assert.ok(L.nombreParDefaut({ largeur: 2560, hauteur: 1440 }) > bureau, 'proportionnel à la surface');
  assert.ok(L.nombreParDefaut({ largeur: 1280, hauteur: 800, msBanc: 48 }) < bureau, 'GPU lent : moins');
  assert.equal(L.nombreParDefaut({ largeur: 1280, hauteur: 800, msBanc: 5 }), bureau, 'GPU rapide : inchangé');
  assert.equal(L.nombreParDefaut({ largeur: 1280, hauteur: 800, msBanc: 100000 }), Math.round(bureau * 0.25 / 100) * 100, 'plancher 25 %');
  assert.equal(L.nombreParDefaut({ largeur: 100, hauteur: 100 }), 1000, 'minimum');
  assert.equal(L.nombreParDefaut({ largeur: 8000, hauteur: 8000 }), 40000, 'maximum');
  assert.equal(L.estMobile(390, true), true); assert.equal(L.estMobile(1280, false), false);
});

test('changement de vue : détecté, pas de faux positif', () => {
  const m = new Float64Array(16).fill(0); m[0] = 1000; m[5] = -1000; m[12] = 3; m[13] = 4; m[15] = 1;
  assert.equal(L.vueChangee(null, m), true);
  assert.equal(L.vueChangee(m, Float64Array.from(m)), false);
  const m2 = Float64Array.from(m); m2[12] += 0.01;
  assert.equal(L.vueChangee(m, m2), true, 'déplacement de 0,01 unité détecté');
  const m3 = Float64Array.from(m); m3[0] *= 1.001;
  assert.equal(L.vueChangee(m, m3), true, 'zoom détecté');
});

test('résolution des échéances (interpolation temporelle)', () => {
  const S = Array.from({ length: 52 }, (_, i) => i);
  assert.deepEqual(resoudreEcheances(3.25, S), { a: 3, b: 4, mix: 0.25 });
  assert.deepEqual(resoudreEcheances(3, S), { a: 3, b: null, mix: 0 });
  assert.deepEqual(resoudreEcheances(51, S), { a: 51, b: null, mix: 0 });
  assert.equal(resoudreEcheances(51.5, S), null);
  assert.equal(resoudreEcheances(-1, S), null);
  assert.equal(resoudreEcheances(5.5, [5]), null, 'après la dernière échéance : pas de champ');
});
