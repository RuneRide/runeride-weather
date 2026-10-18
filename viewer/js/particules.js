// SPDX-License-Identifier: EUPL-1.2
// Calque de particules de vent animées, calculées sur GPU (WebGL2), pour MapLibre GL JS (CustomLayerInterface).
// Module autonome : voir docs/poc-j3/particules.md pour l'API, les choix et les mesures.
//
// Principe (technique classique des particules sur GPU, cf. particules-logique.js pour les références) :
//   1. CHAMP   : les tuiles de vent visibles (mêmes PMTiles que le calque couleur) sont décodées et rendues,
//                mélangées entre deux échéances, dans une texture de la taille de l'écran (u, v en m/s).
//   2. ÉTAT    : les positions des particules (en coordonnées d'écran [0,1]²) vivent dans une texture, mise à jour
//                par un shader (deux textures en alternance) : avance selon le vent, réapparition aléatoire
//                (durée de vie exponentielle, sortie d'écran, passage hors des données).
//   3. DESSIN  : un point par particule (gl_VertexID, aucun tampon), coloré par la vitesse avec la rampe du vent
//                du viewer (km/h), dessiné sur une texture de traînée.
//   4. TRAÎNÉE : deux textures en alternance ; à chaque image l'ancienne est estompée puis recopiée, les points
//                sont dessinés dessus, puis le résultat est composé sur la carte. Effacée quand la vue change.
// Les positions sont en coordonnées d'ÉCRAN : densité constante à tout zoom par construction, mais les
// particules ne sont pas accrochées à la carte pendant un déplacement (la traînée est alors effacée).
// Limite : projection Mercator, tangage 0 (matrice affine), comme le viewer (maxPitch 0).
import { COUCHES, texturePalette } from './config.js';
import { demanderTuile, entreeCache, marquerVue } from './donnees.js';
import { tuilesVisibles } from './couche-gl.js';
import * as L from './particules-logique.js';
import * as S from './particules-shaders.js';

const MAX_TEXTURES_TUILES = 96;

function compiler(gl, type, src) {
  const s = gl.createShader(type);
  gl.shaderSource(s, src); gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error('shader : ' + gl.getShaderInfoLog(s) + '\n' + src.split('\n').map((l, i) => (i + 1) + ' ' + l).join('\n'));
  return s;
}
function programme(gl, vs, fs) {
  const p = gl.createProgram();
  gl.attachShader(p, compiler(gl, gl.VERTEX_SHADER, vs));
  gl.attachShader(p, compiler(gl, gl.FRAGMENT_SHADER, fs));
  gl.linkProgram(p);
  if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error('édition de liens : ' + gl.getProgramInfoLog(p));
  p.u = new Proxy({}, { get: (c, n) => (n in c ? c[n] : (c[n] = gl.getUniformLocation(p, n))) });
  return p;
}

/** Une texture couleur attachée à un framebuffer. */
function cible(gl, w, h, interne, format, type, donnees = null) {
  const tex = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, tex);
  gl.texImage2D(gl.TEXTURE_2D, 0, interne, w, h, 0, format, type, donnees);
  for (const [k, v] of [[gl.TEXTURE_MIN_FILTER, gl.NEAREST], [gl.TEXTURE_MAG_FILTER, gl.NEAREST],
    [gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE], [gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE]]) gl.texParameteri(gl.TEXTURE_2D, k, v);
  const fbo = gl.createFramebuffer();
  gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
  gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
  const ok = gl.checkFramebufferStatus(gl.FRAMEBUFFER) === gl.FRAMEBUFFER_COMPLETE;
  return { tex, fbo, w, h, ok };
}
function libererCible(gl, c) { if (c) { gl.deleteTexture(c.tex); gl.deleteFramebuffer(c.fbo); } }

export class ParticulesVent {
  /**
   * @param {object} options
   * @param {string}   [options.id='particules-vent']  identifiant du calque MapLibre.
   * @param {(t:number)=>(object|null)} options.fournisseurChamp  OBLIGATOIRE. Appelée à chaque image avec le temps t
   *   (heures depuis le début du tirage, fractionnaire) ; renvoie `{ urlA, urlB|null, mix, zmin, zmax, dec }` :
   *   fichiers PMTiles des deux échéances encadrantes, poids de B (0..1), zooms du fichier, paramètres de décodage
   *   (`paramsDecodage` de config.js : enc 'wind8' ou 'wind12', max...), ou null (pas de champ : rien n'est dessiné).
   *   Voir `fournisseurPmtiles` (particules-champ.js).
   * @param {number}   [options.nombre]  nombre de particules ; absent = adaptatif (surface d'écran, mobile, test de coût GPU).
   * @param {number}   [options.pxParMs=3]  échelle de vitesse : pixels CSS par seconde pour 1 m/s de vent.
   * @param {number}   [options.exposantZoom=0]  0 : vitesse écran indépendante du zoom ; 1 : vitesse géographique constante.
   * @param {number}   [options.vieMoyenne=2.2]  durée de vie moyenne d'une particule (s).
   * @param {number}   [options.fondu=0.955]  facteur d'estompage de la traînée par image à 60 Hz (0,9 court, 0,98 long).
   * @param {number}   [options.taillePoint=1.8]  diamètre des points en pixels CSS.
   * @param {number}   [options.opacite=0.9]  opacité des points.  @param {number} [options.opaciteTrainee=1] opacité de la composition.
   * @param {number}   [options.echelleChamp=0.5]  résolution de la texture du champ, en fraction de la taille de l'écran en pixels physiques plafonnés par `ratioMax`.
   * @param {number}   [options.ratioMax=2]  plafond du rapport pixels physiques / pixels CSS pour la traînée et le champ (économise la mémoire et le remplissage sur écrans à forte densité).
   * @param {'auto'|'flottant'|'paquete'} [options.stockage='auto']  'paquete' force le repli 8 bits (tests).
   * @param {boolean}  [options.respecterMouvementReduit=true]  désactive les particules avec prefers-reduced-motion.
   * @param {(etat:object)=>void} [options.surEtat]  rappel à chaque changement d'état (voir `etat`).
   * @param {boolean}  [options.actif=true]
   */
  constructor(options = {}) {
    if (typeof options.fournisseurChamp !== 'function') throw new Error('ParticulesVent : fournisseurChamp (fonction) est obligatoire');
    this.id = options.id || 'particules-vent';
    this.type = 'custom'; this.renderingMode = '2d';
    this.o = {
      pxParMs: 3, exposantZoom: 0, zoomRef: 6, vieMoyenne: 2.2, fondu: 0.955, taillePoint: 1.8, opacite: 0.9, opaciteTrainee: 1,
      echelleChamp: 0.5, ratioMax: 2, stockage: 'auto', respecterMouvementReduit: true, ...options,
    };
    this.fournisseur = options.fournisseurChamp;
    this._t = 0;
    this._actif = options.actif !== false;
    this._nombreDemande = options.nombre ?? null;      // null = adaptatif
    this._nombre = this._nombreDemande || 8000;
    this._mouvementReduit = false;
    this._abonnes = new Set();
    if (options.surEtat) this._abonnes.add(options.surEtat);
    this.compteurs = { images: 0, champs: 0, tuilesDessinees: 0, tuilesManquantes: 0 };
    this._tex = new Map();       // textures de tuiles : cle -> { tex, e }
    this._derniereVue = null; this._sigChamp = ''; this._graine = 1; this._derniereMs = 0;
    if (this.o.respecterMouvementReduit && typeof matchMedia === 'function') {
      this._mq = matchMedia('(prefers-reduced-motion: reduce)');
      this._mouvementReduit = this._mq.matches;
      this._ecouteMq = e => { this._mouvementReduit = e.matches; this._changement(); };
      this._mq.addEventListener ? this._mq.addEventListener('change', this._ecouteMq) : this._mq.addListener(this._ecouteMq);
    }
  }

  // ------------------------------------------------------------ API publique
  /** Temps courant (heures depuis le début du tirage, fractionnaire). */
  setTemps(t) { this._t = t; this._repeindre(); }
  /** Nombre de particules (fixe) ; `null`/`undefined` revient au calcul adaptatif. */
  setNombre(n) {
    this._nombreDemande = n || null;
    this._nombre = n ? Math.max(100, Math.min(250000, Math.round(n))) : this._nombreAdaptatif();
    if (this.gl) this._allouerEtat();
    this._changement();
  }
  /** Active l'animation (sans effet visible si le mouvement réduit est demandé). */
  activer() { if (!this._actif) { this._actif = true; this._effacerTrainee = true; this._changement(); this._repeindre(); } }
  /** Désactive : plus aucun calcul ni repeinte continue. */
  desactiver() { if (this._actif) { this._actif = false; this._changement(); this._repeindre(); } }
  /** Force l'état « mouvement réduit » (par défaut lu dans prefers-reduced-motion et suivi en direct). */
  reduireMouvement(bool) { this._mouvementReduit = !!bool; this._effacerTrainee = true; this._changement(); this._repeindre(); }
  /** Modifie des options d'affichage à chaud (pxParMs, vieMoyenne, fondu, taillePoint, opacite, opaciteTrainee, exposantZoom). */
  regler(opts) { Object.assign(this.o, opts); this._repeindre(); }
  /** Abonnement aux changements d'état ; renvoie la fonction de désabonnement. */
  abonner(f) { this._abonnes.add(f); return () => this._abonnes.delete(f); }
  /** Vrai quand les particules sont réellement animées. Si faux alors que `actif`, l'interface doit afficher les flèches. */
  get visibles() { return this._actif && !this._mouvementReduit && !this._indisponible; }
  /** État exposé à l'interface. */
  get etat() {
    return { actif: this._actif, mouvementReduit: this._mouvementReduit, visibles: this.visibles, indisponible: this._indisponible || null,
      stockage: this.stockage || null, nombre: this._nombre, adaptatif: this._nombreDemande === null, msBanc: this._msBanc ?? null };
  }

  // ------------------------------------------------------------ CustomLayerInterface
  onAdd(map, gl) {
    this.map = map; this.gl = gl;
    try {
      this._initGl();
    } catch (err) {
      this._indisponible = String(err.message || err).split('\n')[0];
      console.warn('particules indisponibles :', err);
      this._changement();
      return;
    }
    if (this._nombreDemande === null) { this._msBanc = this._banc(); this._nombre = this._nombreAdaptatif(); }
    this._allouerEtat();
    this._changement();
  }
  onRemove() {
    const gl = this.gl;
    if (this._mq && this._ecouteMq) { this._mq.removeEventListener ? this._mq.removeEventListener('change', this._ecouteMq) : this._mq.removeListener(this._ecouteMq); }
    if (!gl) return;
    for (const { tex } of this._tex.values()) gl.deleteTexture(tex);
    this._tex.clear();
    for (const c of [this._champ, this._etat?.[0], this._etat?.[1], this._tr?.[0], this._tr?.[1]]) libererCible(gl, c);
    gl.deleteTexture(this._pal);
    this.gl = null;
  }

  _repeindre() { if (this.map) this.map.triggerRepaint(); }
  _changement() { const e = this.etat; for (const f of this._abonnes) { try { f(e); } catch (x) { console.warn(x); } } }

  // ------------------------------------------------------------ initialisation
  _initGl() {
    const gl = this.gl;
    // Stockage : flottant si les cibles RGBA32F (état) et RGBA16F (champ) sont réellement rendables.
    let flottant = false;
    if (this.o.stockage !== 'paquete' && gl.getExtension('EXT_color_buffer_float')) {
      const a = cible(gl, 4, 4, gl.RGBA32F, gl.RGBA, gl.FLOAT), b = cible(gl, 4, 4, gl.RGBA16F, gl.RGBA, gl.HALF_FLOAT);
      flottant = a.ok && b.ok;
      libererCible(gl, a); libererCible(gl, b);
    }
    this.stockage = flottant ? 'flottant' : 'paquete';
    const pq = !flottant;
    this._prog = {
      champ: programme(gl, S.champVS(pq), S.champFS(pq)),
      maj: programme(gl, S.plein(pq), S.majFS(pq)),
      point: programme(gl, S.pointVS(pq), S.pointFS(pq)),
      fondu: programme(gl, S.plein(false), S.fonduFS),
      compo: programme(gl, S.plein(false), S.compoFS),
    };
    this._pal = gl.createTexture();           // rampe du vent du viewer (légende), 256x1, lue au sommet
    gl.bindTexture(gl.TEXTURE_2D, this._pal);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, 256, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, texturePalette(L.COUCHE_VENT.palette));
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    this._vao = gl.createVertexArray();
  }

  _nombreAdaptatif() {
    const c = this.map ? this.map.getCanvas() : { clientWidth: 1280, clientHeight: 800 };
    const tactile = typeof matchMedia === 'function' && matchMedia('(pointer: coarse)').matches;
    return L.nombreParDefaut({ largeur: c.clientWidth, hauteur: c.clientHeight, mobile: L.estMobile(c.clientWidth, tactile), msBanc: this._msBanc ?? null });
  }

  /** Test rapide de coût GPU : quelques mises à jour + dessins de 16 384 particules, durée (ms) mesurée après synchronisation. */
  _banc() {
    const gl = this.gl;
    try {
      const cote = 128, N = cote * cote, ref = this._nombre;
      this._nombre = N;
      this._dims = { W: 512, H: 512, cw: 512, ch: 512, TW: 512, TH: 512 };
      this._etat = null; this._tr = null; this._champ = null;
      this._allouerEtat(true);
      const t0 = performance.now();
      for (let i = 0; i < 6; i++) { this._passeMaj(1 / 60, i + 1, 1, [1, 0, 0, 1], 512, 512, 6); this._passePoints(512, 512); }
      const px = new Uint8Array(4); gl.readPixels(0, 0, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, px);   // synchronise
      const ms = performance.now() - t0;
      this._nombre = ref;
      libererCible(gl, this._etat[0]); libererCible(gl, this._etat[1]); libererCible(gl, this._tr[0]); libererCible(gl, this._tr[1]); libererCible(gl, this._champ);
      this._etat = null; this._tr = null; this._champ = null; this._dims = null;
      return ms;
    } catch (e) { console.warn('banc GPU', e); return null; }
  }

  /** (Ré)alloue la texture d'état (positions aléatoires) pour le nombre courant ; `banc` crée aussi des cibles factices. */
  _allouerEtat(banc = false) {
    const gl = this.gl, pq = this.stockage === 'paquete';
    const cote = Math.ceil(Math.sqrt(this._nombre));
    for (const c of this._etat || []) libererCible(gl, c);
    const n = cote * cote;
    let init;
    if (pq) { init = new Uint8Array(n * 4); for (let i = 0; i < init.length; i++) init[i] = Math.floor(Math.random() * 256); }
    else { init = new Float32Array(n * 4); for (let i = 0; i < n; i++) { init[4 * i] = Math.random(); init[4 * i + 1] = Math.random(); } }
    const mk = () => pq ? cible(gl, cote, cote, gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE, init) : cible(gl, cote, cote, gl.RGBA32F, gl.RGBA, gl.FLOAT, init);
    this._etat = [mk(), mk()]; this._ei = 0; this._cote = cote;
    if (banc) {   // cibles factices pour le test de coût
      this._champ = pq ? cible(gl, 64, 64, gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE) : cible(gl, 64, 64, gl.RGBA16F, gl.RGBA, gl.HALF_FLOAT);
      this._tr = [cible(gl, 512, 512, gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE), cible(gl, 512, 512, gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE)]; this._ti = 0;
    }
  }

  _dimensionner(W, H, cw, ch) {
    const gl = this.gl, d = this._dims;
    if (d && d.W === W && d.H === H && d.cw === cw && d.ch === ch) return;
    const r = Math.min(this.o.ratioMax, W / cw);            // résolution de la traînée : <= ratioMax pixels par pixel CSS
    const TW = Math.max(64, Math.round(cw * r)), TH = Math.max(64, Math.round(ch * r));
    this._dims = { W, H, cw, ch, TW, TH };
    libererCible(gl, this._tr?.[0]); libererCible(gl, this._tr?.[1]); libererCible(gl, this._champ);
    this._tr = [0, 1].map(() => {
      const c = cible(gl, TW, TH, gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR); gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      return c;
    });
    this._ti = 0;
    const fw = Math.max(64, Math.round(TW * this.o.echelleChamp)), fh = Math.max(64, Math.round(TH * this.o.echelleChamp));
    this._champ = this.stockage === 'paquete' ? cible(gl, fw, fh, gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE) : cible(gl, fw, fh, gl.RGBA16F, gl.RGBA, gl.HALF_FLOAT);
    this._sigChamp = ''; this._effacerTrainee = true;
    if (this._nombreDemande === null && this._msBanc !== undefined) {
      const n = this._nombreAdaptatif();
      if (n !== this._nombre) { this._nombre = n; this._allouerEtat(); this._changement(); }
    }
  }

  // ------------------------------------------------------------ textures des tuiles (propres au module)
  _textureTuile(e) {
    const gl = this.gl;
    let t = this._tex.get(e.cle);
    if (t) { this._tex.delete(e.cle); this._tex.set(e.cle, t); return t.tex; }
    const tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB8, e.taille, e.taille, 0, gl.RGB, gl.UNSIGNED_BYTE, e.px);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    this._tex.set(e.cle, { tex, e });
    while (this._tex.size > MAX_TEXTURES_TUILES) {
      const [k, v] = this._tex.entries().next().value;
      this._tex.delete(k); gl.deleteTexture(v.tex);
    }
    return tex;
  }

  // ------------------------------------------------------------ passes
  _rendreChamp(M, ch) {
    const gl = this.gl, P = this._prog.champ, U = P.u, c = this._champ;
    gl.bindFramebuffer(gl.FRAMEBUFFER, c.fbo);
    gl.viewport(0, 0, c.w, c.h);
    gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT);
    gl.disable(gl.BLEND);
    gl.useProgram(P); gl.bindVertexArray(this._vao);
    gl.uniformMatrix4fv(U.u_matrix, false, M);
    const D = ch.dec;
    gl.uniform1i(U.u_a, 0); gl.uniform1i(U.u_b, 1);
    gl.uniform1i(U.u_wind, D.enc === 'wind8' ? 2 : 1);
    gl.uniform1f(U.u_max, D.max);
    let dessinees = 0, manquantes = 0;
    const couche = { dec: D };
    for (const [z, x, y] of tuilesVisibles(this.map, ch.zmin, ch.zmax)) {
      // même repli que couche-gl.js : tuile voulue, sinon ancêtre déjà chargé
      let choix = null;
      for (let Lz = z; Lz >= ch.zmin && !choix; Lz--) {
        const k = z - Lz, xl = x >> k, yl = y >> k;
        const eA = Lz === z ? demanderTuile(ch.urlA, Lz, xl, yl, couche) : entreeCache(ch.urlA, Lz, xl, yl);
        const eB = ch.urlB ? (Lz === z ? demanderTuile(ch.urlB, Lz, xl, yl, couche) : entreeCache(ch.urlB, Lz, xl, yl)) : null;
        for (const q of [eA, eB]) if (q && q.etat === 'charge' && q.promesse && !q.rep) { q.rep = true; q.promesse.then(() => { q.rep = false; this._sigChamp = ''; this._repeindre(); }); }
        if (eA && eA.etat === 'pret') choix = { eA, eB: eB && eB.etat === 'pret' ? eB : null, k };
        else if (eA && eA.etat === 'absent' && Lz === z) break;
      }
      if (!choix) { manquantes++; continue; }
      const { eA, eB, k } = choix;
      marquerVue(eA); if (eB) marquerVue(eB);
      const n = 2 ** z, d = 2 ** k;
      gl.uniform4f(U.u_rect, x / n, y / n, 1 / n, 1 / n);
      gl.uniform4f(U.u_uv, (x % d) / d, (y % d) / d, 1 / d, 1 / d);
      gl.uniform1f(U.u_border, eA.bordure);
      gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, this._textureTuile(eA));
      gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, this._textureTuile(eB || eA));
      gl.uniform1i(U.u_hasB, eB ? 1 : 0);
      gl.uniform1f(U.u_mix, ch.mix);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      dessinees++;
    }
    this.compteurs.champs++; this.compteurs.tuilesDessinees = dessinees; this.compteurs.tuilesManquantes = manquantes;
    gl.activeTexture(gl.TEXTURE0);
  }

  _passeMaj(dt, graine, taux, rot, wCss, hCss, zoom) {
    const gl = this.gl, P = this._prog.maj, U = P.u;
    const src = this._etat[this._ei], dst = this._etat[1 - this._ei];
    gl.bindFramebuffer(gl.FRAMEBUFFER, dst.fbo);
    gl.viewport(0, 0, dst.w, dst.h);
    gl.disable(gl.BLEND);
    gl.useProgram(P); gl.bindVertexArray(this._vao);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, src.tex);
    gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, this._champ.tex);
    gl.uniform1i(U.u_etat, 0); gl.uniform1i(U.u_champ, 1);
    gl.uniform1i(U.u_cote, this._cote); gl.uniform1f(U.u_dt, dt); gl.uniform1ui(U.u_graine, graine >>> 0);
    gl.uniform1f(U.u_taux, taux);
    gl.uniformMatrix2fv(U.u_rot, false, rot);
    gl.uniform2f(U.u_taille, wCss, hCss);
    gl.uniform1f(U.u_pxms, L.vitessePixels(1, zoom, this.o));
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    this._ei = 1 - this._ei;
  }

  _passePoints(W, H) {
    const gl = this.gl, P = this._prog.point, U = P.u, c = this._champ;
    gl.useProgram(P); gl.bindVertexArray(this._vao);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, this._etat[this._ei].tex);
    gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, c.tex);
    gl.activeTexture(gl.TEXTURE2); gl.bindTexture(gl.TEXTURE_2D, this._pal);
    gl.uniform1i(U.u_etat, 0); gl.uniform1i(U.u_champ, 1); gl.uniform1i(U.u_pal, 2);
    gl.uniform1i(U.u_cote, this._cote);
    gl.uniform1f(U.u_taillePoint, Math.max(1, this.o.taillePoint * (this._dims.TW / this._dims.cw)));
    const vent = L.COUCHE_VENT;
    gl.uniform1f(U.u_vmul, vent.aff.mul); gl.uniform1f(U.u_vlo, vent.legende.lo); gl.uniform1f(U.u_vhi, vent.legende.hi);
    gl.uniform1f(P.u.u_alpha, this.o.opacite);
    gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    const tr = this._tr[this._ti];
    gl.bindFramebuffer(gl.FRAMEBUFFER, tr.fbo); gl.viewport(0, 0, tr.w, tr.h);
    gl.drawArrays(gl.POINTS, 0, Math.min(this._nombre, this._cote * this._cote));
    gl.activeTexture(gl.TEXTURE0);
  }

  render(gl, args) {
    if (!this.gl || !this.visibles) return;
    const M = Float64Array.from(args.defaultProjectionData.mainMatrix);
    const ch = this.fournisseur(this._t);
    if (!ch || !ch.urlA) {                            // pas encore de champ : rien dessiné ; on réessaie dans 250 ms
      if (!this._attente) { this._attente = setTimeout(() => { this._attente = null; this._repeindre(); }, 250); }
      return;
    }
    const fbPrec = gl.getParameter(gl.FRAMEBUFFER_BINDING), vp = gl.getParameter(gl.VIEWPORT);
    const W = gl.drawingBufferWidth, H = gl.drawingBufferHeight;
    const cv = this.map.getCanvas();
    this._dimensionner(W, H, cv.clientWidth || W, cv.clientHeight || H);
    gl.disable(gl.DEPTH_TEST); gl.disable(gl.STENCIL_TEST); gl.disable(gl.CULL_FACE); gl.disable(gl.SCISSOR_TEST);

    // temps de simulation (borné : un onglet en pause ne téléporte pas les particules)
    const maintenant = performance.now();
    const dt = this._derniereMs ? Math.min(0.05, Math.max(0.001, (maintenant - this._derniereMs) / 1000)) : 1 / 60;
    this._derniereMs = maintenant;

    // vue changée : traînée effacée (pas de fantômes)
    if (L.vueChangee(this._derniereVue, M)) this._effacerTrainee = true;
    this._derniereVue = M;
    if (this._effacerTrainee) {
      for (const t of this._tr) { gl.bindFramebuffer(gl.FRAMEBUFFER, t.fbo); gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT); }
      this._effacerTrainee = false;
    }

    // 1. champ (seulement si la vue, le temps ou les tuiles chargées ont changé)
    const sig = `${ch.urlA}|${ch.urlB}|${ch.mix.toFixed(4)}|${Array.from(M).join(',')}`;
    if (sig !== this._sigChamp || this._rafraichirChamp) { this._rendreChamp(M, ch); this._sigChamp = sig; this._rafraichirChamp = false; }

    // 2. positions
    const rot = L.rotationDepuisMatrice(M);
    this._passeMaj(dt, (this._graine = (this._graine + 1) >>> 0), L.tauxReapparition(dt, this.o.vieMoyenne), rot, this._dims.cw, this._dims.ch, this.map.getZoom());

    // 3. traînée : ancienne -> estompée -> nouvelle, puis points
    const prec = this._tr[this._ti], nouv = this._tr[1 - this._ti];
    gl.disable(gl.BLEND);
    gl.bindFramebuffer(gl.FRAMEBUFFER, nouv.fbo); gl.viewport(0, 0, nouv.w, nouv.h);
    let P = this._prog.fondu;
    gl.useProgram(P); gl.bindVertexArray(this._vao);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, prec.tex);
    gl.uniform1i(P.u.u_prev, 0);
    gl.uniform1f(P.u.u_f, L.facteurFondu(this.o.fondu, dt)); gl.uniform1f(P.u.u_min, 1.5 / 255);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
    this._ti = 1 - this._ti;
    this._passePoints(W, H);

    // 4. composition sur la carte
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbPrec);
    gl.viewport(vp[0], vp[1], vp[2], vp[3]);
    gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    P = this._prog.compo;
    gl.useProgram(P);
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, this._tr[this._ti].tex);
    gl.uniform1i(P.u.u_trace, 0); gl.uniform2f(P.u.u_origine, vp[0], vp[1]); gl.uniform2f(P.u.u_vp, vp[2], vp[3]); gl.uniform1f(P.u.u_op, this.o.opaciteTrainee);
    gl.drawArrays(gl.TRIANGLES, 0, 3);

    gl.bindVertexArray(null);
    gl.activeTexture(gl.TEXTURE0);
    this.compteurs.images++;
    this.map.triggerRepaint();                        // animation continue tant que visibles
  }

  // ------------------------------------------------------------ diagnostics et autotests (pour les vérifications)
  /** Lit le champ à l'écran : renvoie { w, h, valide: Uint8Array (1 = donnée), vitesseMoy } en m/s. */
  lireChamp() {
    const gl = this.gl, c = this._champ;
    gl.bindFramebuffer(gl.FRAMEBUFFER, c.fbo);
    const n = c.w * c.h, valide = new Uint8Array(n), uv = new Float32Array(2 * n);
    let somme = 0, nb = 0;
    if (this.stockage === 'paquete') {
      const b = new Uint8Array(n * 4); gl.readPixels(0, 0, c.w, c.h, gl.RGBA, gl.UNSIGNED_BYTE, b);
      for (let i = 0; i < n; i++) { const v = L.depaqueterVent(b[4 * i], b[4 * i + 1], b[4 * i + 2], b[4 * i + 3]); if (v) { valide[i] = 1; uv[2 * i] = v[0]; uv[2 * i + 1] = v[1]; somme += Math.hypot(v[0], v[1]); nb++; } }
    } else {
      const f = new Float32Array(n * 4); gl.readPixels(0, 0, c.w, c.h, gl.RGBA, gl.FLOAT, f);
      for (let i = 0; i < n; i++) if (f[4 * i + 3] > 0.5) { valide[i] = 1; uv[2 * i] = f[4 * i]; uv[2 * i + 1] = f[4 * i + 1]; somme += Math.hypot(f[4 * i], f[4 * i + 1]); nb++; }
    }
    return { w: c.w, h: c.h, valide, uv, vitesseMoy: nb ? somme / nb : 0, fractionValide: nb / n };
  }
  /** Positions des particules (stockage flottant uniquement), [x0, y0, x1, y1...] dans [0,1]², y vers le haut. */
  lireEtat() {
    if (this.stockage !== 'flottant') return null;
    const gl = this.gl, e = this._etat[this._ei], f = new Float32Array(e.w * e.h * 4);
    gl.bindFramebuffer(gl.FRAMEBUFFER, e.fbo); gl.readPixels(0, 0, e.w, e.h, gl.RGBA, gl.FLOAT, f);
    const out = new Float32Array(this._nombre * 2);
    for (let i = 0; i < this._nombre; i++) { out[2 * i] = f[4 * i]; out[2 * i + 1] = f[4 * i + 1]; }
    return out;
  }
  /** Compte les pixels allumés de la traînée et ceux situés hors des données (à plus de `marge` pixels du champ valide). */
  controleHorsDonnees(marge = 3) {
    const gl = this.gl, T = this._tr[this._ti], ch = this.lireChamp();
    gl.bindFramebuffer(gl.FRAMEBUFFER, T.fbo);
    const px = new Uint8Array(T.w * T.h * 4); gl.readPixels(0, 0, T.w, T.h, gl.RGBA, gl.UNSIGNED_BYTE, px);
    let allumes = 0, hors = 0;
    for (let y = 0; y < T.h; y++) for (let x = 0; x < T.w; x++) {
      if (px[4 * (y * T.w + x) + 3] < 8) continue;
      allumes++;
      const fx = Math.floor(x * ch.w / T.w), fy = Math.floor(y * ch.h / T.h);
      let ok = false;
      for (let dy = -marge; dy <= marge && !ok; dy++) for (let dx = -marge; dx <= marge && !ok; dx++) {
        const xx = fx + dx, yy = fy + dy;
        if (xx >= 0 && yy >= 0 && xx < ch.w && yy < ch.h && ch.valide[yy * ch.w + xx]) ok = true;
      }
      if (!ok) hors++;
    }
    return { allumes, horsDonnees: hors, fractionChampValide: ch.fractionValide, taille: [T.w, T.h] };
  }
  /** Compare GPU et JavaScript : hachage et empaquetage 16 bits. Renvoie les écarts maximaux (attendus : 0 et <= 1 code). */
  autotestGpu() {
    const gl = this.gl, N = 256;
    const fb = gl.getParameter(gl.FRAMEBUFFER_BINDING), vp = gl.getParameter(gl.VIEWPORT);
    const c = cible(gl, N, 1, gl.RGBA8, gl.RGBA, gl.UNSIGNED_BYTE);
    const P = programme(gl, S.plein(false), S.testFS);
    gl.bindFramebuffer(gl.FRAMEBUFFER, c.fbo); gl.viewport(0, 0, N, 1); gl.disable(gl.BLEND);
    gl.useProgram(P); gl.bindVertexArray(this._vao);
    const lire = () => { const b = new Uint8Array(N * 4); gl.readPixels(0, 0, N, 1, gl.RGBA, gl.UNSIGNED_BYTE, b); return b; };
    const graine = 12345;
    gl.uniform1ui(P.u.u_graine, graine); gl.uniform1i(P.u.u_mode, 0); gl.drawArrays(gl.TRIANGLES, 0, 3);
    let b = lire(), ecartHash = 0;
    for (let i = 0; i < N; i++) {
      const attendu = Math.round(L.alea(i, graine, 1) * 16777216);
      const vu = (b[4 * i] << 16) | (b[4 * i + 1] << 8) | b[4 * i + 2];
      ecartHash = Math.max(ecartHash, Math.abs(attendu - vu));
    }
    gl.uniform1i(P.u.u_mode, 1); gl.drawArrays(gl.TRIANGLES, 0, 3);
    b = lire();
    let ecartCode = 0;
    for (let i = 0; i < N; i++) {
      const attendu = L.codeVent16(i / 255 * 128 - 64);
      ecartCode = Math.max(ecartCode, Math.abs(attendu - L.depaqueter16(b[4 * i], b[4 * i + 1])));
    }
    gl.bindFramebuffer(gl.FRAMEBUFFER, fb); gl.viewport(vp[0], vp[1], vp[2], vp[3]);
    libererCible(gl, c); gl.deleteProgram(P);
    return { ecartHash, ecartCode16: ecartCode };
  }
  /** Résumé de l'état interne (compteurs, tailles). */
  diagnostic() {
    return { stockage: this.stockage, nombre: this._nombre, cote: this._cote, dims: this._dims, compteurs: { ...this.compteurs },
      champ: this._champ ? [this._champ.w, this._champ.h] : null, msBanc: this._msBanc ?? null, etat: this.etat };
  }
}
