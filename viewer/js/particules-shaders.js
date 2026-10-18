// SPDX-License-Identifier: EUPL-1.2
// Shaders GLSL ES 3.00 des particules de vent. Sans tampon de sommets : les quads et les points sont
// engendrés à partir de gl_VertexID. Deux variantes selon le stockage :
//   - flottant (EXT_color_buffer_float) : état RGBA32F (x, y), champ RGBA16F (u, v, validité) ;
//   - paquete  (repli) : tout en RGBA8, nombres sur 16 bits répartis sur deux octets
//     (particules-logique.js : empaqueterPosition, empaqueterVent ; le code 0 = pas de donnée).
// Le hachage et l'empaquetage sont les jumeaux exacts de particules-logique.js (vérifiés par l'autotest GPU).

const ENTETE = (paquete) => `#version 300 es
precision highp float;
precision highp int;
precision highp sampler2D;
${paquete ? '#define PAQUETE 1' : ''}
`;

// ---- bibliothèque commune : hasard, position, champ
const LIB = `
uint hash32(uint x) {
  x ^= x >> 16; x *= 0x7feb352du;
  x ^= x >> 15; x *= 0x846ca68bu;
  x ^= x >> 16;
  return x;
}
float alea(uint id, uint g, uint e) {
  return float(hash32(id * 0x9e3779b1u + g * 0x85ebca6bu + e * 0xc2b2ae35u) >> 8) / 16777216.0;
}
// 16 bits <-> deux octets (valeurs 0..255 en flottant)
vec2 enc16(float c) { c = clamp(floor(c + 0.5), 0.0, 65535.0); float h = floor(c / 256.0); return vec2(h, c - h * 256.0); }
float dec16(float h, float l) { return h * 256.0 + l; }

vec2 lirePos(vec4 t) {
#ifdef PAQUETE
  vec4 b = floor(t * 255.0 + 0.5);
  return vec2(dec16(b.r, b.g), dec16(b.b, b.a)) / 65535.0;
#else
  return t.xy;
#endif
}
vec4 ecrirePos(vec2 p) {
#ifdef PAQUETE
  p = clamp(p, 0.0, 1.0) * 65535.0;
  return vec4(enc16(p.x), enc16(p.y)) / 255.0;
#else
  return vec4(p, 0.0, 1.0);
#endif
}
`;

const CHAMP = `
uniform sampler2D u_champ;
// Un texel du champ : (u, v, validité) en m/s. Pas de donnée => validité 0.
vec3 tapChamp(ivec2 i) {
  vec4 t = texelFetch(u_champ, i, 0);
#ifdef PAQUETE
  vec4 b = floor(t * 255.0 + 0.5);
  float cu = dec16(b.r, b.g), cv = dec16(b.b, b.a);
  if (cu < 0.5 || cv < 0.5) return vec3(0.0);
  return vec3((cu - 1.0) / 65534.0 * 128.0 - 64.0, (cv - 1.0) / 65534.0 * 128.0 - 64.0, 1.0);
#else
  return t.w > 0.5 ? vec3(t.xy, 1.0) : vec3(0.0);
#endif
}
// Vent à la position d'écran p dans [0,1]^2 : bilinéaire pondérée par la validité ; faux si le texel le
// plus proche n'a pas de donnée (aucune particule ne vit hors des données).
bool champ(vec2 p, out vec2 w) {
  ivec2 sz = textureSize(u_champ, 0);
  vec2 q = p * vec2(sz) - 0.5;
  vec2 fl = floor(q), f = q - fl;
  ivec2 i0 = ivec2(fl), hi = sz - 1;
  ivec2 ic = clamp(i0 + ivec2(f.x >= 0.5 ? 1 : 0, f.y >= 0.5 ? 1 : 0), ivec2(0), hi);
  if (tapChamp(ic).z < 0.5) return false;
  vec3 a = tapChamp(clamp(i0, ivec2(0), hi));
  vec3 b = tapChamp(clamp(i0 + ivec2(1, 0), ivec2(0), hi));
  vec3 c = tapChamp(clamp(i0 + ivec2(0, 1), ivec2(0), hi));
  vec3 d = tapChamp(clamp(i0 + ivec2(1, 1), ivec2(0), hi));
  float wa = (1.0 - f.x) * (1.0 - f.y) * a.z, wb = f.x * (1.0 - f.y) * b.z;
  float wc = (1.0 - f.x) * f.y * c.z, wd = f.x * f.y * d.z;
  float s = wa + wb + wc + wd;
  w = (a.xy * wa + b.xy * wb + c.xy * wc + d.xy * wd) / s;
  return true;
}
`;

// ---------------------------------------------------------------- 1. champ de vent à l'écran
export const champVS = (paquete) => ENTETE(paquete) + `
uniform mat4 u_matrix;
uniform vec4 u_rect;   // x0, y0, largeur, hauteur de la tuile en coordonnées Mercator [0,1]
out vec2 v_f;
void main() {
  vec2 a = vec2(float(gl_VertexID & 1), float(gl_VertexID >> 1));
  v_f = a;
  gl_Position = u_matrix * vec4(u_rect.xy + a * u_rect.zw, 0.0, 1.0);
}`;

// Décodage et interpolation : recopiés de couche-gl.js (calque couleur) pour garantir les mêmes valeurs :
// bilinéaire APRÈS décodage, un voisin au code 0 => pas de donnée, mélange temporel sur u et v.
export const champFS = (paquete) => ENTETE(paquete) + LIB + `
in vec2 v_f;
uniform sampler2D u_a, u_b;
uniform vec4 u_uv;
uniform float u_mix;
uniform int u_hasB;
uniform int u_wind;      // 1 = wind12, 2 = wind8
uniform float u_max;     // portée de wind8 (m/s), lue dans les métadonnées PMTiles
uniform float u_border;
out vec4 o;
const float T = 256.0;
bool decode(vec3 rgb, out vec2 v) {
  vec3 c = floor(rgb * 255.0 + 0.5);
  if (u_wind == 2) {
    if (c.r < 0.5 || c.g < 0.5) return false;
    v = (c.rg - 1.0) / 254.0 * 2.0 * u_max - u_max;
    return true;
  }
  if (c.r + c.g + c.b < 0.5) return false;
  float gh = floor(c.g / 16.0);
  float cu = c.r * 16.0 + gh;
  float cv = (c.g - gh * 16.0) * 256.0 + c.b;
  v = vec2((cu - 1.0) / 4094.0 * 128.0 - 64.0, (cv - 1.0) / 4094.0 * 128.0 - 64.0);
  return true;
}
bool lire(sampler2D t, vec2 s, out vec2 res) {
  ivec2 sz = textureSize(t, 0);
  vec2 p = s * T - 0.5 + u_border;
  vec2 fl = floor(p);
  vec2 w = p - fl;
  ivec2 i0 = ivec2(fl);
  ivec2 lo = ivec2(0), hi = sz - 1;
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
  if (!okA && !okB) { o = vec4(0.0); return; }
  vec2 v = (okA && okB) ? mix(a, b, u_mix) : (okA ? a : b);
#ifdef PAQUETE
  float cu = floor(1.0 + (clamp(v.x, -64.0, 64.0) + 64.0) / 128.0 * 65534.0 + 0.5);
  float cv = floor(1.0 + (clamp(v.y, -64.0, 64.0) + 64.0) / 128.0 * 65534.0 + 0.5);
  o = vec4(enc16(cu), enc16(cv)) / 255.0;
#else
  o = vec4(v, 0.0, 1.0);
#endif
}`;

// ---------------------------------------------------------------- 2. mise à jour des positions
export const plein = (paquete) => ENTETE(paquete) + `
void main() {
  vec2 a = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));   // triangle plein écran
  gl_Position = vec4(a * 2.0 - 1.0, 0.0, 1.0);
}`;

export const majFS = (paquete) => ENTETE(paquete) + LIB + CHAMP + `
uniform sampler2D u_etat;
uniform int u_cote;
uniform float u_dt;          // secondes simulées
uniform uint u_graine;
uniform float u_taux;        // probabilité de réapparition pendant dt
uniform mat2 u_rot;          // orientation (est, -nord) Mercator -> écran, voir rotationDepuisMatrice
uniform vec2 u_taille;       // taille de l'écran en pixels CSS
uniform float u_pxms;        // pixels CSS par seconde et par m/s
out vec4 o;
void main() {
  ivec2 ij = ivec2(gl_FragCoord.xy);
  uint id = uint(ij.y * u_cote + ij.x);
  vec2 p = lirePos(texelFetch(u_etat, ij, 0));
  vec2 w = vec2(0.0);
  bool ok = p.x >= 0.0 && p.x <= 1.0 && p.y >= 0.0 && p.y <= 1.0 && champ(p, w);
  if (ok && alea(id, u_graine, 0u) >= u_taux) {
    float sp = length(w);
    if (sp > 1e-4) {
      vec2 c = u_rot * vec2(w.x, -w.y) / sp;
      p += c / max(length(c), 1e-6) * (sp * u_pxms * u_dt) / u_taille;
    }
  } else {
    // réapparition : jusqu'à 4 tirages uniformes sur l'écran, le premier qui tombe dans les données est retenu
    vec2 q = p;
    for (uint e = 1u; e <= 4u; e++) {
      q = vec2(alea(id, u_graine, 2u * e), alea(id, u_graine, 2u * e + 1u));
      if (champ(q, w)) break;
    }
    p = q;
  }
  o = ecrirePos(p);
}`;

// ---------------------------------------------------------------- 3. dessin des points
export const pointVS = (paquete) => ENTETE(paquete) + LIB + CHAMP + `
uniform sampler2D u_etat, u_pal;
uniform int u_cote;
uniform float u_taillePoint, u_vmul, u_vlo, u_vhi;
out vec3 v_col;
void main() {
  ivec2 ij = ivec2(gl_VertexID % u_cote, gl_VertexID / u_cote);
  vec2 p = lirePos(texelFetch(u_etat, ij, 0));
  vec2 w;
  if (!(p.x >= 0.0 && p.x <= 1.0 && p.y >= 0.0 && p.y <= 1.0) || !champ(p, w)) {
    gl_Position = vec4(2.0, 2.0, 2.0, 1.0); gl_PointSize = 0.0; v_col = vec3(0.0); return;   // hors des données : pas dessiné
  }
  float t = clamp((length(w) * u_vmul - u_vlo) / (u_vhi - u_vlo), 0.0, 1.0);
  v_col = texelFetch(u_pal, ivec2(int(t * 255.0 + 0.5), 0), 0).rgb;
  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
  gl_PointSize = u_taillePoint;
}`;
export const pointFS = (paquete) => ENTETE(paquete) + `
in vec3 v_col;
uniform float u_alpha;
out vec4 o;
void main() {
  vec2 d = gl_PointCoord - 0.5;
  if (dot(d, d) > 0.25) discard;
  o = vec4(v_col * u_alpha, u_alpha);   // alpha pré-multiplié
}`;

// ---------------------------------------------------------------- 4. estompage et composition
export const fonduFS = ENTETE(false) + `
uniform sampler2D u_prev;
uniform float u_f, u_min;
out vec4 o;
void main() {
  vec4 c = texelFetch(u_prev, ivec2(gl_FragCoord.xy), 0);
  o = max(c * u_f - u_min, 0.0);   // le retrait minimal évite les fantômes permanents en 8 bits
}`;
export const compoFS = ENTETE(false) + `
uniform sampler2D u_trace;
uniform vec2 u_origine, u_vp;   // origine et taille du viewport de la carte (pixels physiques)
uniform float u_op;
out vec4 o;
void main() {
  o = texture(u_trace, (gl_FragCoord.xy - u_origine) / u_vp) * u_op;   // LINEAR : la traînée peut avoir une résolution plus basse
}`;

// ---------------------------------------------------------------- autotest GPU : hachage et empaquetage
export const testFS = ENTETE(false) + LIB + `
uniform uint u_graine;
uniform int u_mode;    // 0 : hasard (24 bits sur RVB), 1 : vent empaqueté 16 bits (u dans RG, v = u dans BA)
out vec4 o;
void main() {
  uint id = uint(gl_FragCoord.x);
  if (u_mode == 0) {
    uint h = hash32(id * 0x9e3779b1u + u_graine * 0x85ebca6bu + 1u * 0xc2b2ae35u) >> 8;
    o = vec4(float((h >> 16) & 255u), float((h >> 8) & 255u), float(h & 255u), 255.0) / 255.0;
  } else {
    float x = float(id) / 255.0 * 128.0 - 64.0;
    float c = floor(1.0 + (clamp(x, -64.0, 64.0) + 64.0) / 128.0 * 65534.0 + 0.5);
    o = vec4(enc16(c), enc16(c)) / 255.0;
  }
}`;
