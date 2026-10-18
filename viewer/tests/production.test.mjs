// SPDX-License-Identifier: EUPL-1.2
// Tests de la mise en production : environnement, i18n, validation du manifeste, préchargement sobre,
// autonomie du viewer (aucune ressource externe), intégrité des bibliothèques copiées.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync, readdirSync, statSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { FICHIER_FOND, HOTE_DONNEES, creerEnv } from '../js/config.js';
import { LANGUES, TEXTES, creerT, detecterLangue } from '../js/i18n.js';
import * as L from '../js/integration-logique.js';
import { delaiReessai } from '../js/donnees.js';
import { manifest, ms } from './aide.mjs';

const RACINE = join(dirname(fileURLToPath(import.meta.url)), '..');
const lire = f => readFileSync(join(RACINE, f), 'utf8');
const sri = f => 'sha384-' + createHash('sha384').update(readFileSync(join(RACINE, f))).digest('base64');

test('environnement : développement local par défaut', () => {
  const e = creerEnv({ hostname: '127.0.0.1', href: 'http://127.0.0.1:8187/', search: '' });
  assert.equal(e.nom, 'dev');
  assert.equal(e.manifeste, '/manifest/manifest.json');
  assert.equal(e.isobares, '/isobares');
  assert.equal(e.fond, 'http://127.0.0.1:8187/basemap/domaine-z9.pmtiles');
  assert.equal(e.tuiles, null);
});

test('environnement : production sur weather.runeride.fr, aucune redirection possible des données', () => {
  const e = creerEnv({ hostname: 'weather.runeride.fr', href: 'https://weather.runeride.fr/', search: '?donnees=https://pirate.example' });
  assert.equal(e.nom, 'prod');
  assert.equal(e.donnees, HOTE_DONNEES);
  assert.equal(e.manifeste, 'https://weather-data.runeride.fr/v1/manifest.json');
  assert.equal(e.tuiles, 'https://weather-data.runeride.fr/v1');
  assert.equal(e.isobares, 'https://weather-data.runeride.fr/v1/isobares');
  assert.equal(e.fond, `https://weather-data.runeride.fr/basemap/${FICHIER_FOND}`);
});

test('environnement : ?env=prod ailleurs, avec hôte de données de maquette (http(s) strict)', () => {
  const base = { hostname: '127.0.0.1', href: 'http://127.0.0.1:8187/' };
  assert.equal(creerEnv({ ...base, search: '?env=prod' }).donnees, HOTE_DONNEES);
  assert.equal(creerEnv({ ...base, search: '?env=prod&donnees=http://127.0.0.1:9000' }).manifeste, 'http://127.0.0.1:9000/v1/manifest.json');
  assert.equal(creerEnv({ ...base, search: '?env=prod&donnees=javascript:alert(1)' }).donnees, HOTE_DONNEES);
  assert.equal(creerEnv({ ...base, search: '?env=prod&donnees=http://a.example/chemin' }).donnees, HOTE_DONNEES);
  assert.equal(creerEnv(null).nom, 'dev');
});

test('i18n : langue du navigateur, forçage, repli', () => {
  assert.equal(detecterLangue(['en-GB', 'fr']), 'en');
  assert.equal(detecterLangue(['de-DE', 'fr-FR']), 'fr');
  assert.equal(detecterLangue(['de-DE', 'en-US']), 'en');
  assert.equal(detecterLangue(['de']), 'fr');
  assert.equal(detecterLangue([], null), 'fr');
  assert.equal(detecterLangue(['fr'], 'en'), 'en');
  assert.equal(detecterLangue(['en'], 'xx'), 'en');
});

test('i18n : mêmes clés en français et en anglais, variables, repli', () => {
  assert.deepEqual(Object.keys(TEXTES.en).sort(), Object.keys(TEXTES.fr).sort());
  for (const l of LANGUES) for (const [k, v] of Object.entries(TEXTES[l])) assert.ok(v && v.length, `${l}.${k} vide`);
  assert.match(creerT('en')('statut.ancien', { h: 40 }), /more than 40 h old/);
  assert.match(creerT('fr')('statut.ancien', { h: 40 }), /plus de 40 h/);
  assert.equal(creerT('xx')('lecture'), 'Lecture');
  assert.equal(creerT('en')('cle.inconnue'), 'cle.inconnue');
});

test('i18n : toutes les clés data-i18n de index.html existent', () => {
  const html = lire('index.html');
  const cles = [...html.matchAll(/data-i18n="([^"]+)"/g)].map(m => m[1]);
  for (const a of html.matchAll(/data-i18n-attr="([^"]+)"/g)) for (const p of a[1].split(';')) cles.push(p.split(':')[1].trim());
  assert.ok(cles.length > 15);
  for (const k of cles) assert.ok(k in TEXTES.fr, `clé absente : ${k}`);
  for (const c of ['vent', 'rafales', 't2m', 'pluie', 'nuages', 'humidite', 'neige', 'cape', 'pression']) assert.ok(`couche.${c}` in TEXTES.fr);
});

test('verifierManifeste : refuse les formes illisibles, signale l\'absence de tirage', () => {
  assert.deepEqual(L.verifierManifeste(manifest()), { ok: true });
  for (const m of [null, 42, [], {}, { models: [] }, { models: { arome: { runs: [{ id: 5, run: 'x' }] } } }, { models: { arome: { runs: [{ id: 'a', run: 'pas une date' }] } } }])
    assert.deepEqual(L.verifierManifeste(m), { ok: false, code: 'invalide' });
  assert.deepEqual(L.verifierManifeste({ models: { arome: { runs: [] }, 'arome-hd': {} } }), { ok: false, code: 'vide' });
});

test('ageTropGrand : seulement au-delà du seuil', () => {
  const m = manifest();   // tirage le plus récent : 17/10 06 h UTC
  assert.equal(L.ageTropGrand(m, ms(17, 20)), null);
  assert.equal(L.ageTropGrand(m, ms(18, 6)), null);                 // 24 h
  assert.equal(L.ageTropGrand(m, ms(18, 12)), null);                // 30 h exactement : pas encore
  assert.equal(L.ageTropGrand(m, ms(18, 14)), 32);                  // 32 h > 30 h
  assert.equal(L.ageTropGrand(m, ms(19, 6)), 48);
  assert.equal(L.ageTropGrand({ models: {} }, ms(19, 6)), null);
});

test('horizonPrecharge : sobre à l\'arrêt, nul si économie de données, masqué ou hors ligne', () => {
  assert.equal(L.horizonPrecharge({ lecture: true }), 3);
  assert.equal(L.horizonPrecharge({ lecture: false }), 1);
  assert.equal(L.horizonPrecharge({ lecture: true, economieDonnees: true }), 0);
  assert.equal(L.horizonPrecharge({ lecture: true, masque: true }), 0);
  assert.equal(L.horizonPrecharge({ lecture: true, enLigne: false }), 0);
  assert.equal(L.horizonPrecharge({ lecture: true, typeConnexion: '3g' }), 1);
  assert.equal(L.horizonPrecharge({ lecture: false, typeConnexion: 'slow-2g' }), 0);
  assert.equal(L.horizonPrecharge({ lecture: true, typeConnexion: '4g' }), 3);
});

test('delaiReessai : espacé et plafonné', () => {
  assert.deepEqual([1, 2, 3, 4, 5, 9].map(delaiReessai), [5000, 10000, 20000, 40000, 60000, 60000]);
  assert.equal(delaiReessai(0), 5000);
});

test('autonomie : aucune ressource externe dans la page ni dans le code', () => {
  const html = lire('index.html');
  const chargees = [...html.matchAll(/\s(?:src|href)="(https?:)?\/\/([^"]+)"/g)].map(m => m[0]);
  // seuls des liens <a> de navigation (sources, licences) peuvent sortir ; ni script, ni feuille de style, ni image
  for (const bal of html.matchAll(/<(script|link|img|iframe|source)\b[^>]*>/g)) {
    if (bal[1] === 'link' && /rel="canonical"/.test(bal[0])) continue;   // simple indication, rien n'est chargé
    assert.doesNotMatch(bal[0], /(?:src|href)="(?:https?:)?\/\//, `ressource externe : ${bal[0]}`);
  }
  assert.ok(chargees.length > 0);
  for (const f of readdirSync(join(RACINE, 'js'))) {
    const code = lire('js/' + f);
    assert.doesNotMatch(code, /protomaps\.github\.io|cdn\.jsdelivr|unpkg\.com|fonts\.googleapis/, `dépendance externe dans js/${f}`);
  }
});

test('bibliothèques copiées : empreintes SHA-384 identiques à celles qui étaient déclarées', () => {
  assert.equal(sri('vendor/maplibre-gl/maplibre-gl.js'), 'sha384-5+cfbwT0iiub6VsQAdn6yz16nr6sDiQoHx6tm4O8OVYXHYOxcffFmCJBL0dgdvGp');
  assert.equal(sri('vendor/maplibre-gl/maplibre-gl.css'), 'sha384-uTttxo/aOKbdE5RlD/SPzSDoDmNvGlUYPjONi2MN/b7c9HPSvW07OIuyP7uL6jxK');
  assert.equal(sri('vendor/pmtiles/pmtiles.js'), 'sha384-pbKNZbDshUsLnZMIVsn/t+j3xEY9PGfuX5Z22l5rcy/obXz3EwaMx1F5d9sRsJMn');
  assert.equal(sri('vendor/protomaps-basemaps/basemaps.js'), 'sha384-fzuU7k7ZWwfZAe1+3h7plrSA6dNazJaM2NsKVwcrReJLK0gwVwyOSyPwROl9m1M0');
});

test('fond de carte auto-hébergé : 27 plages de polices, 8 sprites, licences', () => {
  const polices = ['Noto Sans Regular', 'Noto Sans Medium', 'Noto Sans Italic'];
  const plages = ['0-255', '256-511', '512-767', '768-1023', '1024-1279', '1536-1791', '7680-7935', '8192-8447', '11520-11775'];
  for (const p of polices) for (const r of plages) assert.ok(statSync(join(RACINE, 'assets/fond/fonts', p, `${r}.pbf`)).size > 0, `${p} ${r}`);
  for (const t of ['light', 'dark']) for (const f of [`${t}.json`, `${t}.png`, `${t}@2x.json`, `${t}@2x.png`]) assert.ok(statSync(join(RACINE, 'assets/fond/sprites/v4', f)).size > 0);
  assert.ok(existsSync(join(RACINE, 'assets/fond/fonts/OFL.txt')));
  assert.ok(existsSync(join(RACINE, 'assets/fond/sprites/LICENSE-tangrams-icons.md')));
  // le style du fond demande exactement ces chemins
  const main = lire('js/main.js');
  assert.match(main, /RACINE_FOND \+ 'fonts\/\{fontstack\}\/\{range\}\.pbf'/);
  assert.match(main, /RACINE_FOND \+ 'sprites\/v4\/' \+ THEME/);
});

test('PWA et métadonnées : manifeste web, icônes, service worker sans données', () => {
  const m = JSON.parse(lire('manifest.webmanifest'));
  assert.equal(m.display, 'standalone');
  for (const i of m.icons) assert.ok(existsSync(join(RACINE, i.src)), i.src);
  const html = lire('index.html');
  for (const motif of [/<title>[^<]+<\/title>/, /name="description"/, /rel="icon"/, /rel="apple-touch-icon"/, /rel="manifest"/, /<html lang=/]) assert.match(html, motif);
  const sw = lire('sw.js');
  assert.match(sw, /url\.origin !== self\.location\.origin/);     // jamais les données (autre origine)
  assert.match(sw, /headers\.has\('range'\)/);                      // jamais les requêtes Range
});
