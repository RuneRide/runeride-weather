# SPDX-License-Identifier: EUPL-1.2
"""Quantification et empaquetage (contrat PoC J1, §3). Pur numpy, sans E/S."""
import io

import numpy as np
from PIL import Image

# variable -> (min, max, échelle, unité). Échelle : "linear" ou "sqrt".
SCALAR8 = {
    "t2m": (233.15, 333.15, "linear", "K"),
    "gust10": (0.0, 64.0, "linear", "m/s"),
    "rh2m": (0.0, 100.0, "linear", "%"),
    "cloud_total": (0.0, 100.0, "linear", "%"),
    "snow_fraction": (0.0, 1.0, "linear", "1"),
    "precip_rate": (0.0, 100.0, "sqrt", "mm/h"),
    "cape": (0.0, 5000.0, "sqrt", "J/kg"),
}
WIND_RANGE = (-64.0, 64.0)
WIND_VARIABLE = "wind10"          # fichier wind12 : composantes wind10_u + wind10_v
OUTPUT_VARIABLES = list(SCALAR8) + [WIND_VARIABLE]
TILE = 256
BORDER = 1
STORED = TILE + 2 * BORDER        # 258


def scalar_step(var):
    """Pas de quantification (unité physique) ; pour sqrt, pas maximal (en haut de plage)."""
    vmin, vmax, scale, _ = SCALAR8[var]
    if scale == "linear":
        return (vmax - vmin) / 254.0
    return vmax * (1 - (253 / 254.0) ** 2)


def encode_scalar8(values, var):
    """float32 -> uint8 ; NaN -> 0 ; 1..255 sinon."""
    vmin, vmax, scale, _ = SCALAR8[var]
    v = np.asarray(values, dtype=np.float64)
    nan = ~np.isfinite(v)
    f = np.clip((np.where(nan, vmin, v) - vmin) / (vmax - vmin), 0.0, 1.0)
    if scale == "sqrt":
        f = np.sqrt(f)
    code = np.clip(1 + np.rint(f * 254.0), 1, 255)
    code[nan] = 0
    return code.astype(np.uint8)


def decode_scalar8(codes, var):
    """uint8 -> float32 ; 0 -> NaN."""
    vmin, vmax, scale, _ = SCALAR8[var]
    c = np.asarray(codes, dtype=np.float64)
    f = (c - 1) / 254.0
    if scale == "sqrt":
        f = f * f
    out = vmin + f * (vmax - vmin)
    out[c == 0] = np.nan
    return out.astype(np.float32)


def _wind_code(x):
    return np.clip(1 + np.rint((x + 64.0) / 128.0 * 4094.0), 1, 4095).astype(np.int64)


def encode_wind12(u, v):
    """(u, v) float32 -> uint8 [h, w, 3] ; NaN dans u ou v -> (0, 0, 0)."""
    u = np.asarray(u, dtype=np.float64)
    v = np.asarray(v, dtype=np.float64)
    nan = ~(np.isfinite(u) & np.isfinite(v))
    cu = _wind_code(np.where(nan, 0.0, u))
    cv = _wind_code(np.where(nan, 0.0, v))
    rgb = np.stack([cu >> 4, ((cu & 15) << 4) | (cv >> 8), cv & 255], axis=-1)
    rgb[nan] = 0
    return rgb.astype(np.uint8)


def unpack_wind12(rgb):
    """uint8 [h, w, 3] -> (cu, cv) entiers 12 bits (0 = pas de donnée)."""
    r, g, b = (rgb[..., i].astype(np.int64) for i in range(3))
    return (r << 4) | (g >> 4), ((g & 15) << 8) | b


def decode_wind12(rgb):
    """uint8 [h, w, 3] -> (u, v) float32 ; (0, 0, 0) -> NaN."""
    cu, cv = unpack_wind12(rgb)
    nodata = (cu == 0) | (cv == 0)
    u = (cu - 1) / 4094.0 * 128.0 - 64.0
    v = (cv - 1) / 4094.0 * 128.0 - 64.0
    u[nodata] = np.nan
    v[nodata] = np.nan
    return u.astype(np.float32), v.astype(np.float32)


WIND_STEP = 128.0 / 4094.0


# Effort de compression WebP sans perte (0-6), choisi d'après les mesures (docs/poc-j1/tuilage.md) :
# scalar8 : 4 (le 6 gagne ~6 % mais coûte ~45 fois plus de temps) ; wind12 : 3 (le 4-6 sont plus lents
# et NON plus petits sur le bruit RVB). None = valeur par défaut selon l'encodage.
WEBP_METHOD = None
WEBP_METHOD_SCALAR8 = 4
WEBP_METHOD_WIND12 = 3


def to_webp(arr, lossless_format="WEBP", method=None):
    """uint8 [h, w] (L) ou [h, w, 3] (RGB) -> octets WebP sans perte, sans alpha."""
    img = Image.fromarray(np.ascontiguousarray(arr), "L" if arr.ndim == 2 else "RGB")
    buf = io.BytesIO()
    if method is None:
        method = WEBP_METHOD
    if method is None:
        method = WEBP_METHOD_WIND12 if arr.ndim == 3 else WEBP_METHOD_SCALAR8
    if lossless_format == "WEBP":
        img.save(buf, "WEBP", lossless=True, quality=100, method=method, exact=True)
    else:
        img.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def from_image(data):
    """Octets WebP/PNG -> uint8 [h, w] ou [h, w, 3]."""
    img = Image.open(io.BytesIO(data))
    if img.mode not in ("L", "RGB"):
        raise ValueError("mode d'image inattendu : %s (alpha interdit)" % img.mode)
    return np.array(img)
