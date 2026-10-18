// SPDX-License-Identifier: EUPL-1.2
// Pilote minimal de Chrome sans interface via le protocole CDP (aucune dépendance : Node >= 22).
// Usage : node viewer/tools/pilote_chrome.mjs scenario.json
// scenario.json (option "reseau": "fichier.json" : octets réellement transférés par URL, via CDP Network) : { "url": "...", "largeur": 1280, "hauteur": 800, "dpr": 1, "etapes": [
//    {"eval": "expression JS (promesse attendue)"}, {"capture": "chemin.png"},
//    {"attendre_dom": "#mesures[data-pret]", "ms": 60000}, {"dom": "#mesures", "vers": "fichier.txt"}, {"pause": 500} ] }
// Chrome sans interface utilise un WebGL LOGICIEL (SwiftShader) : les images/s mesurées ne
// sont pas représentatives d'un téléphone.
import { spawn } from 'node:child_process';
import { mkdtempSync, readFileSync, writeFileSync, existsSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const sc = JSON.parse(readFileSync(process.argv[2], 'utf8'));
const prof = mkdtempSync(join(tmpdir(), 'chrome-viewer-'));
const chrome = spawn('google-chrome', ['--headless=new', '--no-sandbox', '--enable-unsafe-swiftshader', '--use-angle=swiftshader',
  '--use-gl=angle', '--ignore-gpu-blocklist', '--disable-gpu-vsync', '--remote-debugging-port=0', `--user-data-dir=${prof}`,
  '--no-first-run', '--no-default-browser-check', `--window-size=${sc.largeur || 1280},${sc.hauteur || 800}`, 'about:blank'],
  { stdio: ['ignore', 'ignore', 'pipe'] });
let errChrome = '';
chrome.stderr.on('data', d => { errChrome += d; });
const pause = ms => new Promise(r => setTimeout(r, ms));

async function port() {
  for (let i = 0; i < 100; i++) {
    const f = join(prof, 'DevToolsActivePort');
    if (existsSync(f)) return readFileSync(f, 'utf8').split('\n')[0];
    await pause(100);
  }
  throw new Error('Chrome ne démarre pas : ' + errChrome.slice(-500));
}
try {
  const p = await port();
  const cibles = await (await fetch(`http://127.0.0.1:${p}/json`)).json();
  const ws = new WebSocket(cibles.find(c => c.type === 'page').webSocketDebuggerUrl);
  await new Promise(r => ws.addEventListener('open', r));
  let n = 0; const att = new Map(); const logs = []; const urls = new Map(); const reseau = [];
  ws.addEventListener('message', ev => {
    const m = JSON.parse(ev.data);
    if (m.id && att.has(m.id)) { att.get(m.id)(m); att.delete(m.id); }
    else if (m.method === 'Network.responseReceived') urls.set(m.params.requestId, { url: m.params.response.url, status: m.params.response.status });
    else if (m.method === 'Network.loadingFinished') { const u = urls.get(m.params.requestId); if (u) reseau.push({ ...u, octets: m.params.encodedDataLength, t: m.params.timestamp }); }
    else if (m.method === 'Runtime.consoleAPICalled') logs.push(m.params.type + ': ' + m.params.args.map(a => a.value ?? a.description).join(' '));
    else if (m.method === 'Runtime.exceptionThrown') logs.push('EXCEPTION: ' + (m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text));
  });
  const cdp = (method, params = {}) => new Promise((res, rej) => {
    const id = ++n; att.set(id, m => m.error ? rej(new Error(method + ' : ' + m.error.message)) : res(m.result));
    ws.send(JSON.stringify({ id, method, params }));
  });
  await cdp('Runtime.enable'); await cdp('Page.enable'); await cdp('Network.enable');
  await cdp('Emulation.setDeviceMetricsOverride', { width: sc.largeur || 1280, height: sc.hauteur || 800,
    deviceScaleFactor: sc.dpr || 1, mobile: !!sc.mobile });
  if (sc.mobile) await cdp('Emulation.setTouchEmulationEnabled', { enabled: true });
  if (sc.mouvementReduit) await cdp('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] });
  await cdp('Page.navigate', { url: sc.url });
  await pause(500);
  const evalJs = async (expr) => {
    const r = await cdp('Runtime.evaluate', { expression: expr, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error('eval : ' + (r.exceptionDetails.exception?.description || r.exceptionDetails.text));
    return r.result.value;
  };
  for (const e of sc.etapes) {
    if (e.pause) await pause(e.pause);
    else if (e.eval) { const v = await evalJs(e.eval); console.log('eval ->', JSON.stringify(v)); }
    else if (e.attendre_dom) {
      const t = Date.now();
      while (!(await evalJs(`!!document.querySelector(${JSON.stringify(e.attendre_dom)})`))) {
        if (Date.now() - t > (e.ms || 30000)) throw new Error('délai dépassé : ' + e.attendre_dom);
        await pause(250);
      }
    } else if (e.dom) {
      const txt = await evalJs(`document.querySelector(${JSON.stringify(e.dom)})?.textContent`);
      if (e.vers) writeFileSync(e.vers, txt ?? ''); else console.log(txt);
    } else if (e.capture) {
      const r = await cdp('Page.captureScreenshot', { format: 'png' });
      writeFileSync(e.capture, Buffer.from(r.data, 'base64')); console.log('capture', e.capture);
    }
  }
  if (sc.reseau) writeFileSync(sc.reseau, JSON.stringify(reseau));
  if (logs.length) console.log('--- console ---\n' + logs.slice(0, 40).join('\n'));
  ws.close();
} catch (err) {
  console.error('ERREUR', err.message);
  process.exitCode = 1;
} finally {
  chrome.kill('SIGKILL');
  await pause(300);
  try { rmSync(prof, { recursive: true, force: true }); } catch {}
}
