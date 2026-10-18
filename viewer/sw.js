// SPDX-License-Identifier: EUPL-1.2
// Service worker sobre : met en cache la COQUILLE de l'application (page, code, bibliothèques, icônes, polices et sprites
// du fond) pour qu'elle s'ouvre hors ligne et se recharge vite. Il ne touche jamais aux données (autre origine :
// weather-data.runeride.fr), ni aux requêtes Range, ni aux tuiles : le cache des prévisions est celui du navigateur.
//  - code (page, js, css, manifeste web) : réseau d'abord, repli sur le cache (un déploiement s'applique tout de suite) ;
//  - vendor/, assets/ : cache d'abord (fichiers qui ne changent qu'avec une nouvelle version de cache : incrémenter VERSION).
const VERSION = 'augures-v1';
const COQUILLE = ['./', 'index.html', 'css/style.css', 'js/main.js', 'manifest.webmanifest', 'assets/icones/icon-192.png',
  'vendor/maplibre-gl/maplibre-gl.js', 'vendor/maplibre-gl/maplibre-gl.css', 'vendor/pmtiles/pmtiles.js',
  'vendor/protomaps-basemaps/basemaps.js', 'assets/fond/sprites/v4/dark.json', 'assets/fond/sprites/v4/dark.png',
  'assets/fond/sprites/v4/light.json', 'assets/fond/sprites/v4/light.png',
  'assets/fond/fonts/Noto%20Sans%20Regular/0-255.pbf', 'assets/fond/fonts/Noto%20Sans%20Medium/0-255.pbf',
  'assets/fond/fonts/Noto%20Sans%20Italic/0-255.pbf'];

self.addEventListener('install', ev => {
  ev.waitUntil(caches.open(VERSION).then(c => c.addAll(COQUILLE)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', ev => {
  ev.waitUntil(caches.keys().then(noms => Promise.all(noms.filter(n => n !== VERSION).map(n => caches.delete(n)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', ev => {
  const req = ev.request, url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== self.location.origin || req.headers.has('range')) return;
  const immuable = /\/(vendor|assets)\//.test(url.pathname);
  ev.respondWith(immuable ? cacheDAbord(req) : reseauDAbord(req));
});
async function cacheDAbord(req) {
  const c = await caches.open(VERSION), hit = await c.match(req);
  if (hit) return hit;
  const rep = await fetch(req);
  if (rep.ok) c.put(req, rep.clone());
  return rep;
}
async function reseauDAbord(req) {
  const c = await caches.open(VERSION);
  try {
    const rep = await fetch(req);
    if (rep.ok) c.put(req, rep.clone());
    return rep;
  } catch (e) {
    const hit = await c.match(req, { ignoreSearch: true }) || (req.mode === 'navigate' ? await c.match('index.html') : null);
    if (hit) return hit;
    throw e;
  }
}
