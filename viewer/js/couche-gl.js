// SPDX-License-Identifier: EUPL-1.2
// Calque WebGL2 personnalisé (CustomLayerInterface de MapLibre) : le décodage des tuiles
// (scalar8 / wind12), l'interpolation bilinéaire, le mélange temporel entre deux échéances
// et la rampe de couleurs sont tous faits dans le fragment shader.
import { TUILE, texturePalette } from './config.js';
import { demanderTuile, entreeCache, marquerVue, nouvelleImage, surLiberation, mercator } from './donnees.js';

const VS = `#version 300 es
in vec2 a_pos;
uniform mat4 u_matrix;
uniform vec4 u_rect;   // x0, y0, largeur, hauteur en coordonnées Mercator [0,1]
out vec2 v_f;
void main() {
  v_f = a_pos;
  gl_Position = u_matrix * vec4(u_rect.xy + a_pos * u_rect.zw, 0.0, 1.0);
}`;

const FS = `#version 300 es
precision highp float;
precision highp int;
in vec2 v_f;
uniform sampler2D u_a;      // échéance h
uniform sampler2D u_b;      // échéance h+1
uniform sampler2D u_pal;    // rampe 256x1 RGBA
uniform vec4 u_uv;          // sous-rectangle de la tuile de données couvert par ce quad (repli sur un parent)
uniform float u_mix;        // 0 = échéance A, 1 = échéance B
uniform int u_hasB;
uniform int u_wind;         // 0 = scalar8, 1 = wind12, 2 = wind8 (profil reduit)
uniform int u_sqrt;         // échelle racine carrée (scalar8)
uniform float u_quant, u_levels;   // pas de quantification et nombre d'intervalles (métadonnées PMTiles)
uniform float u_min, u_max, u_mul, u_add, u_lo, u_hi, u_seuil, u_opacite, u_border;
uniform int u_palsqrt;
uniform int u_ignorebord;   // 1 = ignore la bordure (clamp aux bords utiles) : émule des tuiles sans bordure, pour mesurer la couture
uniform int u_debug;        // 1 = sortie brute (position dans la rampe en niveaux de gris) pour vérifier le shader
out vec4 o_color;

const float T = 256.0;

// Décode un texel en valeur physique. Renvoie false si code 0 (pas de donnée).
bool decode(vec3 rgb, out vec2 v) {
  vec3 c = floor(rgb * 255.0 + 0.5);
  if (u_wind == 2) {   // wind8 : u dans R, v dans G, B inutilisé ; 0 = pas de donnée
    if (c.r < 0.5 || c.g < 0.5) return false;
    v = (c.rg - 1.0) / 254.0 * 2.0 * u_max - u_max;
    return true;
  }
  if (u_wind == 1) {
    if (c.r + c.g + c.b < 0.5) return false;
    float gh = floor(c.g / 16.0);
    float cu = c.r * 16.0 + gh;
    float cv = (c.g - gh * 16.0) * 256.0 + c.b;
    v = vec2((cu - 1.0) / 4094.0 * 128.0 - 64.0, (cv - 1.0) / 4094.0 * 128.0 - 64.0);
    return true;
  }
  if (c.r < 0.5) return false;
  float fl = (c.r - 1.0) / u_levels;
  v = vec2(u_sqrt == 1 ? u_min + (u_max - u_min) * fl * fl : u_min + (c.r - 1.0) * u_quant, 0.0);
  return true;
}

// Interpolation bilinéaire APRÈS décodage ; un voisin sans donnée => pas de donnée (contrat §3).
bool lire(sampler2D t, vec2 s, out vec2 res) {
  ivec2 sz = textureSize(t, 0);
  vec2 p = s * T - 0.5 + u_border;           // centre de pixel = indice entier
  vec2 fl = floor(p);
  vec2 w = p - fl;
  ivec2 i0 = ivec2(fl);
  ivec2 lo = (u_ignorebord == 1) ? ivec2(int(u_border)) : ivec2(0);
  ivec2 hi = sz - 1 - lo;
  vec2 v00, v10, v01, v11;
  bool ok = decode(texelFetch(t, clamp(i0, lo, hi), 0).rgb, v00);
  ok = decode(texelFetch(t, clamp(i0 + ivec2(1, 0), lo, hi), 0).rgb, v10) && ok;
  ok = decode(texelFetch(t, clamp(i0 + ivec2(0, 1), lo, hi), 0).rgb, v01) && ok;
  ok = decode(texelFetch(t, clamp(i0 + ivec2(1, 1), lo, hi), 0).rgb, v11) && ok;
  if (!ok) return false;
  res = mix(mix(v00, v10, w.x), mix(v01, v11, w.x), w.y);
  return true;
}

void main() {
  vec2 s = u_uv.xy + v_f * u_uv.zw;
  vec2 a, b;
  bool okA = lire(u_a, s, a);
  bool okB = (u_hasB == 1) && lire(u_b, s, b);
  if (!okA && !okB) discard;
  vec2 v = (okA && okB) ? mix(a, b, u_mix) : (okA ? a : b);   // mélange temporel (vent : sur u et v)
  float x = (u_wind > 0) ? length(v) : v.x;
  float d = x * u_mul + u_add;
  if (d < u_seuil) discard;
  float t = clamp((d - u_lo) / (u_hi - u_lo), 0.0, 1.0);
  if (u_palsqrt == 1) t = sqrt(t);
  if (u_debug == 1) { o_color = vec4(vec3(t), 1.0); return; }
  vec4 c = texture(u_pal, vec2((t * 255.0 + 0.5) / 256.0, 0.5));
  float al = c.a * u_opacite;
  o_color = vec4(c.rgb * al, al);                               // alpha pré-multiplié (blend MapLibre)
}`;

function compiler(gl, type, src) {
  const s = gl.createShader(type);
  gl.shaderSource(s, src); gl.compileShader(s);
  if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
  return s;
}

/** Liste des tuiles (z, x, y) qui recouvrent la vue. */
export function tuilesVisibles(map, zmin, zmax) {
  const z = Math.max(zmin, Math.min(zmax, Math.round(map.getZoom() + 1)));
  const b = map.getBounds();
  const n = 2 ** z;
  const [x0, y0] = mercator(Math.max(-180, b.getWest()), Math.min(85, b.getNorth()));
  const [x1, y1] = mercator(Math.min(180, b.getEast()), Math.max(-85, b.getSouth()));
  const res = [];
  const ix0 = Math.max(0, Math.floor(x0 * n)), ix1 = Math.min(n - 1, Math.floor(x1 * n));
  const iy0 = Math.max(0, Math.floor(y0 * n)), iy1 = Math.min(n - 1, Math.floor(y1 * n));
  for (let y = iy0; y <= iy1; y++) for (let x = ix0; x <= ix1; x++) res.push([z, x, y]);
  return res;
}

export class CoucheDonnees {
  constructor(id = 'donnees') {
    this.id = id; this.type = 'custom'; this.renderingMode = '2d';
    this.p = null;             // paramètres courants (voir definir)
    this.surPremiere = null;   // rappel : première image avec au moins une tuile dessinée
    this.derniere = { tuiles: 0, manquantes: 0 };
    this.paletteDirty = true;
  }

  /** p : { couche, urlA, urlB|null, mix, opacite, zmin, zmax } */
  definir(p) {
    if (!this.p || this.p.couche !== p.couche) this.paletteDirty = true;
    this.p = p;
    this.map && this.map.triggerRepaint();
  }

  /** N'affiche plus rien (couche Pression seule). */
  masquer() { this.p = null; this.derniere = { tuiles: 0, manquantes: 0 }; this.map && this.map.triggerRepaint(); }

  onAdd(map, gl) {
    this.map = map; this.gl = gl;
    const prog = gl.createProgram();
    gl.attachShader(prog, compiler(gl, gl.VERTEX_SHADER, VS));
    gl.attachShader(prog, compiler(gl, gl.FRAGMENT_SHADER, FS));
    gl.linkProgram(prog);
    if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(prog));
    this.prog = prog;
    this.u = {};
    for (const n of ['u_matrix', 'u_rect', 'u_a', 'u_b', 'u_pal', 'u_uv', 'u_mix', 'u_hasB', 'u_wind', 'u_sqrt', 'u_min', 'u_max',
      'u_mul', 'u_add', 'u_lo', 'u_hi', 'u_seuil', 'u_opacite', 'u_border', 'u_palsqrt', 'u_debug', 'u_ignorebord', 'u_quant', 'u_levels']) this.u[n] = gl.getUniformLocation(prog, n);
    this.vao = gl.createVertexArray();
    gl.bindVertexArray(this.vao);
    this.vbo = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, this.vbo);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([0, 0, 1, 0, 0, 1, 1, 1]), gl.STATIC_DRAW);
    const loc = gl.getAttribLocation(prog, 'a_pos');
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
    gl.bindVertexArray(null);
    this.texPal = gl.createTexture();
    surLiberation(e => { if (e.tex) { gl.deleteTexture(e.tex); e.tex = null; } });
  }

  onRemove() { /* la page ne retire jamais ce calque ; ressources libérées à la fermeture */ }

  _texture(e) {
    const gl = this.gl;
    if (e.tex) return e.tex;
    const t = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, t);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
    if (e.canaux === 3) gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB8, e.taille, e.taille, 0, gl.RGB, gl.UNSIGNED_BYTE, e.px);
    else gl.texImage2D(gl.TEXTURE_2D, 0, gl.R8, e.taille, e.taille, 0, gl.RED, gl.UNSIGNED_BYTE, e.px);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);  // interpolation faite dans le shader
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    e.tex = t;
    return t;
  }

  render(gl, args) {
    const p = this.p;
    if (!p || !p.urlA) return;
    nouvelleImage();
    const c = p.couche;
    gl.useProgram(this.prog);
    gl.bindVertexArray(this.vao);
    gl.disable(gl.DEPTH_TEST); gl.disable(gl.STENCIL_TEST); gl.disable(gl.CULL_FACE);
    gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.uniformMatrix4fv(this.u.u_matrix, false, args.defaultProjectionData.mainMatrix);

    gl.activeTexture(gl.TEXTURE2);
    gl.bindTexture(gl.TEXTURE_2D, this.texPal);
    if (this.paletteDirty) {
      gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA8, 256, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, texturePalette(c.palette));
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      this.paletteDirty = false;
    }
    const U = this.u;
    gl.uniform1i(U.u_a, 0); gl.uniform1i(U.u_b, 1); gl.uniform1i(U.u_pal, 2);
    const D = c.dec;
    gl.uniform1i(U.u_wind, D.enc === 'wind12' ? 1 : D.enc === 'wind8' ? 2 : 0);
    gl.uniform1i(U.u_sqrt, D.echelle === 'sqrt' ? 1 : 0);
    gl.uniform1f(U.u_min, D.min); gl.uniform1f(U.u_max, D.max);
    gl.uniform1f(U.u_quant, D.quant); gl.uniform1f(U.u_levels, D.levels);
    gl.uniform1f(U.u_mul, c.aff.mul); gl.uniform1f(U.u_add, c.aff.add);
    gl.uniform1f(U.u_lo, c.legende.lo); gl.uniform1f(U.u_hi, c.legende.hi);
    gl.uniform1i(U.u_palsqrt, c.legende.axe === 'sqrt' ? 1 : 0);
    gl.uniform1i(U.u_debug, p.debug ? 1 : 0);
    gl.uniform1i(U.u_ignorebord, p.sansBordure ? 1 : 0);
    gl.uniform1f(U.u_seuil, c.seuil); gl.uniform1f(U.u_opacite, p.opacite ?? 0.85);

    let dessinees = 0, manquantes = 0;
    for (const [z, x, y] of tuilesVisibles(this.map, p.zmin, p.zmax)) {
      // choisit le niveau de zoom utilisable : la tuile voulue, sinon un ancêtre déjà chargé
      let choix = null;
      for (let L = z; L >= p.zmin && !choix; L--) {
        const k = z - L, xl = x >> k, yl = y >> k;
        const eA = L === z ? demanderTuile(p.urlA, L, xl, yl, c) : entreeCache(p.urlA, L, xl, yl);
        const eB = p.urlB ? (L === z ? demanderTuile(p.urlB, L, xl, yl, c) : entreeCache(p.urlB, L, xl, yl)) : null;
        if (eA && eA.etat === 'pret') choix = { eA, eB: eB && eB.etat === 'pret' ? eB : null, k, xl, yl };
        else if (eA && eA.etat === 'absent' && L === z) break; // tuile vide (aucune donnée) : rien à dessiner
      }
      if (!choix) { manquantes++; continue; }
      const { eA, eB, k } = choix;
      marquerVue(eA); if (eB) marquerVue(eB);
      const n = 2 ** z;
      gl.uniform4f(U.u_rect, x / n, y / n, 1 / n, 1 / n);
      const d = 2 ** k;  // sous-rectangle dans la tuile ancêtre
      gl.uniform4f(U.u_uv, (x % d) / d, (y % d) / d, 1 / d, 1 / d);
      gl.uniform1f(U.u_border, eA.bordure);
      gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, this._texture(eA));
      gl.activeTexture(gl.TEXTURE1); gl.bindTexture(gl.TEXTURE_2D, this._texture(eB || eA));
      gl.uniform1i(U.u_hasB, eB ? 1 : 0);
      gl.uniform1f(U.u_mix, p.mix);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
      dessinees++;
    }
    gl.bindVertexArray(null);
    gl.activeTexture(gl.TEXTURE0);
    this.derniere = { tuiles: dessinees, manquantes };
    if (dessinees && this.surPremiere) { const f = this.surPremiere; this.surPremiere = null; f(); }
  }
}
