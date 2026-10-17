// SPDX-License-Identifier: EUPL-1.2
// Capture d'écran de la page de test avec Chrome sans interface, pilotée par le protocole
// DevTools (CDP) : attend l'évènement MapLibre « idle » (titre contenant [ok]) puis capture.
// Écart au cahier des charges : `--screenshot --virtual-time-budget` seul prend la photo avant
// l'arrivée des tuiles (le temps virtuel n'attend pas les workers de MapLibre) ; voir
// docs/poc-j1/fond-de-carte.md.
// Usage : node capture.mjs URL sortie.png [reseau.json] [largeur hauteur]
import { spawn } from 'node:child_process';
import { mkdtempSync, readFileSync, writeFileSync, rmSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const [url, sortie, fichierReseau, L = '1280', H = '800'] = process.argv.slice(2);
const dossier = mkdtempSync(join(process.env.CAPTURE_TMP || tmpdir(), 'chrome-'));
const chrome = spawn('google-chrome', ['--headless=new', '--no-sandbox', '--enable-unsafe-swiftshader',
  '--hide-scrollbars', '--remote-debugging-port=0', `--user-data-dir=${dossier}`, `--window-size=${L},${H}`,
  'about:blank'], { stdio: 'ignore' });
const dormir = (ms) => new Promise((r) => setTimeout(r, ms));
try {
  let port;
  for (let i = 0; i < 100 && !port; i++) {
    await dormir(200);
    const f = join(dossier, 'DevToolsActivePort');
    if (existsSync(f)) port = readFileSync(f, 'utf8').split('\n')[0];
  }
  const cibles = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
  const ws = new WebSocket(cibles.find((c) => c.type === 'page').webSocketDebuggerUrl);
  await new Promise((r) => (ws.onopen = r));
  let n = 0; const attente = new Map(); const reseau = new Map();
  ws.onmessage = (m) => {
    const d = JSON.parse(m.data);
    if (d.id && attente.has(d.id)) { attente.get(d.id)(d); attente.delete(d.id); return; }
    if (d.method === 'Network.requestWillBeSent') reseau.set(d.params.requestId, { url: d.params.request.url, octets: 0, statut: null, plage: d.params.request.headers.Range || null });
    if (d.method === 'Network.responseReceived') { const r = reseau.get(d.params.requestId); if (r) { r.statut = d.params.response.status; r.type = d.params.type; } }
    if (d.method === 'Network.loadingFinished') { const r = reseau.get(d.params.requestId); if (r) r.octets = d.params.encodedDataLength; }
  };
  const cdp = (method, params = {}) => new Promise((r) => { const id = ++n; attente.set(id, r); ws.send(JSON.stringify({ id, method, params })); });
  await cdp('Network.enable'); await cdp('Page.enable');
  await cdp('Emulation.setDeviceMetricsOverride', { width: +L, height: +H, deviceScaleFactor: 1, mobile: false });
  await cdp('Page.navigate', { url });
  let titre = '';
  for (let i = 0; i < 240; i++) {   // 120 s au plus
    await dormir(500);
    titre = (await cdp('Runtime.evaluate', { expression: 'document.title' })).result.result.value || '';
    if (titre.includes('[ok]') || titre.includes('[erreur]')) break;
  }
  await dormir(1500);   // laisse finir le fondu et les étiquettes
  const png = (await cdp('Page.captureScreenshot', { format: 'png' })).result.data;
  writeFileSync(sortie, Buffer.from(png, 'base64'));
  if (fichierReseau) writeFileSync(fichierReseau, JSON.stringify([...reseau.values()], null, 1));
  console.log(`titre : ${titre} ; ${reseau.size} requêtes ; ${sortie}`);
  ws.close();
} finally {
  chrome.kill();
  await dormir(500);
  try { rmSync(dossier, { recursive: true, force: true }); } catch {}
}
