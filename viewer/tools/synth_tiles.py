# SPDX-License-Identifier: EUPL-1.2
"""Fabrique de FAUSSES tuiles conformes au contrat PoC J1 §3 (développement du viewer).

Champs synthétiques lisses calculés directement sur les centres de pixels Web
Mercator (donc cohérents entre tuiles voisines, y compris dans la bordure d'1 px),
avec des zones « pas de donnée » (code 0) : un disque et l'extérieur de l'emprise.

Écrit out/tiles-synth/arome-hd/<tirage>/<variable>/<NN>.pmtiles. L'option --sans-bordure
produit des tuiles 256x256 (bordure 0) pour illustrer la couture ; l'option --bordure-brute
produit des tuiles 258x258 dont la bordure est simplement clonée du bord (mauvais tuilage).
Écrivain PMTiles v3 minimal intégré (aucune dépendance hors numpy et PIL).

Usage : python3 viewer/tools/synth_tiles.py [--sortie out/tiles-synth] [--echeances 6]
"""
import argparse
import gzip
import io
import json
import math
import multiprocessing as mp
import os
import struct
import sys

import numpy as np
from PIL import Image

TILE = 256
EMPRISE = (-12.0, 37.5, 16.0, 55.4)  # lon min, lat min, lon max, lat max
ZOOMS = (5, 8)
TIRAGE = "2026-10-17T06Z"

# variable -> (min, max, échelle, unité) ; contrat §3
SCALAR8 = {
    "t2m": (233.15, 333.15, "linear", "K"),
    "gust10": (0.0, 64.0, "linear", "m/s"),
    "rh2m": (0.0, 100.0, "linear", "%"),
    "cloud_total": (0.0, 100.0, "linear", "%"),
    "precip_rate": (0.0, 100.0, "sqrt", "mm/h"),
    "cape": (0.0, 5000.0, "sqrt", "J/kg"),
}


# ---------------------------------------------------------------- PMTiles v3
def zxy_to_tileid(z, x, y):
    """Identifiant de tuile PMTiles (courbe de Hilbert, tuiles des zooms inférieurs d'abord)."""
    acc = sum(4 ** i for i in range(z))
    n = 2 ** z
    d = 0
    s = n // 2
    while s > 0:
        rx = 1 if (x & s) > 0 else 0
        ry = 1 if (y & s) > 0 else 0
        d += s * s * ((3 * rx) ^ ry)
        if ry == 0:
            if rx == 1:
                x = s - 1 - x
                y = s - 1 - y
            x, y = y, x
        s //= 2
    return acc + d


def _varint(n):
    out = bytearray()
    while n >= 0x80:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    out.append(n)
    return bytes(out)


def ecrire_pmtiles(chemin, tuiles, meta, emprise, zmin, zmax):
    """tuiles : {(z, x, y): octets WebP}. PMTiles v3, répertoire racine unique gzip."""
    ids = sorted((zxy_to_tileid(*k), k) for k in tuiles)
    entrees, data, off = [], bytearray(), 0
    for tid, k in ids:
        b = tuiles[k]
        entrees.append((tid, off, len(b)))
        data += b
        off += len(b)
    d = bytearray(_varint(len(entrees)))
    prev = 0
    for tid, _, _ in entrees:
        d += _varint(tid - prev)
        prev = tid
    for _ in entrees:
        d += _varint(1)  # longueur de série
    for _, _, ln in entrees:
        d += _varint(ln)
    pe = 0
    for i, (_, o, ln) in enumerate(entrees):
        d += _varint(0 if (i > 0 and o == pe) else o + 1)
        pe = o + ln
    racine = gzip.compress(bytes(d), 6, mtime=0)
    assert len(racine) < 16384 - 127, "répertoire racine trop gros pour une seule requête initiale"
    mdj = gzip.compress(json.dumps(meta, ensure_ascii=False).encode("utf-8"), 6, mtime=0)
    e7 = lambda v: int(round(v * 1e7))
    ro, rl = 127, len(racine)
    mo, ml = ro + rl, len(mdj)
    do, dl = mo + ml, len(data)
    h = bytearray(b"PMTiles") + bytes([3])
    h += struct.pack("<11Q", ro, rl, mo, ml, 0, 0, do, dl, len(entrees), len(entrees), len(entrees))
    h += bytes([1, 2, 1, 4, zmin, zmax])  # clustered, interne gzip, tuiles non compressées, webp
    h += struct.pack("<4i", e7(emprise[0]), e7(emprise[1]), e7(emprise[2]), e7(emprise[3]))
    h += bytes([zmin]) + struct.pack("<2i", e7((emprise[0] + emprise[2]) / 2), e7((emprise[1] + emprise[3]) / 2))
    assert len(h) == 127, len(h)
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin + ".tmp", "wb") as f:
        f.write(h + racine + mdj + data)
    os.replace(chemin + ".tmp", chemin)


# ---------------------------------------------------------------- géométrie
def lonlat_tuile(z, x, y, bordure):
    """Longitudes et latitudes des centres de pixels (avec bordure), comme le contrat."""
    n = 2 ** z * TILE
    i = np.arange(-bordure, TILE + bordure, dtype=np.float64) + 0.5
    lon = (x * TILE + i) / n * 360.0 - 180.0
    lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * (y * TILE + i) / n))))
    return lon, lat


def tuiles_couvrantes(z):
    n = 2 ** z
    def xy(lon, lat):
        return (int(math.floor((lon + 180) / 360 * n)),
                int(math.floor((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)))
    x0, y1 = xy(EMPRISE[0], EMPRISE[1])
    x1, y0 = xy(EMPRISE[2], EMPRISE[3])
    return [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]


# ---------------------------------------------------------------- champs
def champs(lon, lat, h):
    """Champs physiques (SI) sur une grille [lat, lon] à l'échéance h ; NaN = pas de donnée."""
    X, Y = np.meshgrid(lon, lat)
    ph = h * 0.35
    # température : gradient nord-sud, cycle diurne, ondulations, petite structure fine
    t = (288.0 - 0.75 * (Y - 46.0) + 4.0 * np.sin(X * 0.45 + ph) * np.cos(Y * 0.5)
         + 3.0 * np.sin(2 * math.pi * (h - 3) / 24.0) + 1.2 * np.sin(X * 6.0) * np.sin(Y * 7.0))
    # vent : deux tourbillons qui dérivent vers l'est
    cx = -6.0 + 2.0 * h
    r2 = (X - cx) ** 2 + (Y - 47.5) ** 2
    g = np.exp(-r2 / 18.0)
    u = 6.0 + 14.0 * g * -(Y - 47.5) / 3.0 + 5.0 * np.sin(Y * 0.8 + ph)
    v = 2.0 + 14.0 * g * (X - cx) / 3.0 + 4.0 * np.cos(X * 0.7 - ph)
    vit = np.hypot(u, v)
    rafale = vit * 1.4 + 3.0 + 2.0 * np.sin(X * 3 + Y * 2)
    hum = 55.0 + 35.0 * np.sin(X * 0.5 - ph * 0.5) * np.cos(Y * 0.6)
    nuages = 50.0 + 60.0 * np.sin(X * 0.35 + Y * 0.4 + ph * 0.6)
    pluie = 18.0 * np.exp(-((X - (1.0 + 1.5 * h)) ** 2 / 6.0 + (Y - 45.0) ** 2 / 4.0)) * (0.6 + 0.4 * np.sin(X * 4 + Y * 5))
    cape = 3500.0 * np.exp(-((X - 4.0) ** 2 / 10.0 + (Y - 43.5 - 0.3 * h) ** 2 / 3.0))
    # zones sans donnée : un disque (mer d'Irlande fictive) et l'extérieur de l'emprise
    trou = (X - 2.0) ** 2 + (Y - 49.0) ** 2 < 1.1 ** 2
    hors = (X < EMPRISE[0]) | (X > EMPRISE[2]) | (Y < EMPRISE[1]) | (Y > EMPRISE[3])
    masque = trou | hors
    out = {
        "t2m": t, "gust10": np.clip(rafale, 0, 64), "rh2m": np.clip(hum, 0, 100),
        "cloud_total": np.clip(nuages, 0, 100), "precip_rate": np.clip(pluie, 0, 100),
        "cape": np.clip(cape, 0, 5000), "u": u, "v": v,
    }
    for k in out:
        out[k] = np.where(masque, np.nan, out[k]).astype(np.float32)
    return out


def coder_scalaire(val, var):
    vmin, vmax, echelle, _ = SCALAR8[var]
    nan = ~np.isfinite(val)
    f = np.clip((np.where(nan, vmin, val) - vmin) / (vmax - vmin), 0, 1)
    if echelle == "sqrt":
        f = np.sqrt(f)
    c = np.clip(1 + np.rint(f * 254), 1, 255).astype(np.uint8)
    c[nan] = 0
    return np.stack([c, c, c], axis=-1)


def coder_vent(u, v):
    nan = ~(np.isfinite(u) & np.isfinite(v))
    cu = np.clip(1 + np.rint((np.nan_to_num(u) + 64) / 128 * 4094), 1, 4095).astype(np.uint16)
    cv = np.clip(1 + np.rint((np.nan_to_num(v) + 64) / 128 * 4094), 1, 4095).astype(np.uint16)
    r = (cu >> 4).astype(np.uint8)
    g = (((cu & 15) << 4) | (cv >> 8)).astype(np.uint8)
    b = (cv & 255).astype(np.uint8)
    img = np.stack([r, g, b], axis=-1)
    img[nan] = 0
    return img


def webp(arr):
    buf = io.BytesIO()
    Image.fromarray(arr, "RGB").save(buf, "WEBP", lossless=True, quality=100, method=4)
    return buf.getvalue()


def _tache(arg):
    z, x, y, echeances, mode = arg
    bordure = 0 if mode == "sans" else 1
    lon, lat = lonlat_tuile(z, x, y, bordure)
    res = {}
    for h in echeances:
        c = champs(lon, lat, h)
        for var in list(SCALAR8) + ["wind10"]:
            if var in ("gust10", "precip_rate") and h == 0:
                continue  # absentes à l'échéance 0 (contrat §2)
            arr = coder_vent(c["u"], c["v"]) if var == "wind10" else coder_scalaire(c[var], var)
            if mode == "brute":  # bordure clonée du bord au lieu de venir du voisin
                arr = np.pad(arr[1:-1, 1:-1], ((1, 1), (1, 1), (0, 0)), mode="edge")
            if not arr.any():
                continue
            res[(var, h)] = webp(arr)
    return (z, x, y), res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sortie", default="out/tiles-synth")
    ap.add_argument("--echeances", type=int, default=6, help="nombre d'échéances (0..n-1)")
    ap.add_argument("--sans-bordure", action="store_true", help="tuiles 256x256, bordure 0")
    ap.add_argument("--bordure-brute", action="store_true", help="258x258 mais bordure clonée du bord")
    a = ap.parse_args()
    mode = "sans" if a.sans_bordure else ("brute" if a.bordure_brute else "ok")
    ech = list(range(a.echeances))
    jobs = [(z, x, y, ech, mode) for z in range(ZOOMS[0], ZOOMS[1] + 1) for x, y in tuiles_couvrantes(z)]
    print("%d tuiles x %d échéances, mode bordure=%s" % (len(jobs), len(ech), mode), file=sys.stderr)
    with mp.Pool(os.cpu_count()) as p:
        resultats = p.map(_tache, jobs, chunksize=2)
    bordure = 0 if mode == "sans" else 1
    total = 0
    for var in list(SCALAR8) + ["wind10"]:
        for h in ech:
            tuiles = {k: r[(var, h)] for k, r in resultats if (var, h) in r}
            if not tuiles:
                continue
            if var == "wind10":
                meta = {"encoding": "wind12", "min": -64.0, "max": 64.0, "scale": "linear", "unit": "m/s"}
            else:
                vmin, vmax, ech_, unit = SCALAR8[var]
                meta = {"encoding": "scalar8", "min": vmin, "max": vmax, "scale": ech_, "unit": unit}
            meta.update({"variable": var, "model": "arome-hd", "run": TIRAGE, "step": "%02d" % h,
                         "border": bordure, "tile_size": TILE, "stored_size": TILE + 2 * bordure,
                         "format": "webp-lossless", "nodata_code": 0, "minzoom": ZOOMS[0],
                         "maxzoom": ZOOMS[1], "synthetic": True,
                         "attribution": "Données SYNTHÉTIQUES (développement du viewer)"})
            chemin = os.path.join(a.sortie, "arome-hd", TIRAGE, var, "%02d.pmtiles" % h)
            ecrire_pmtiles(chemin, tuiles, meta, EMPRISE, ZOOMS[0], ZOOMS[1])
            total += os.path.getsize(chemin)
    print("écrit : %.1f Mo dans %s" % (total / 1e6, a.sortie), file=sys.stderr)


if __name__ == "__main__":
    main()
