# SPDX-License-Identifier: EUPL-1.2
"""Précision à position quelconque : effet de la double interpolation bilinéaire.

Simulation sans quantification : la « tuile » au zoom z a pour valeur, en chaque centre de pixel, soit
la bilinéaire de la source (mode `bilineaire`, tuilage actuel), soit la maille source la plus proche
(mode `proche`, rééchantillonnage au plus proche voisin) ; le client interpole en bilinéaire entre
les quatre pixels voisins. Référence : bilinéaire directe sur la source.
Usage : python3 -m pipeline.tiling.precision --canon out/hd_step03.npz --out precision.json
"""
import argparse
import json
import math

import numpy as np

from .mercator import Grid


def pixel_value(grid, field, lon, lat, mode):
    if mode == "bilineaire":
        return float(grid.sample(field, np.array([lon]), np.array([lat]))[0, 0])
    j = int(round((lat - grid.lat[0]) / grid.dlat))
    i = int(round((lon - grid.lon[0]) / grid.dlon))
    if not (0 <= j < grid.nlat and 0 <= i < grid.nlon):
        return float("nan")
    return float(field[j, i])


def client_value(grid, field, lon, lat, z, mode):
    n = 2 ** z * 256
    gx = (lon + 180.0) / 360.0 * n - 0.5          # indice continu de pixel (centres à .5)
    gy = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n - 0.5
    i0, j0 = math.floor(gx), math.floor(gy)
    wx, wy = gx - i0, gy - j0
    v = {}
    for dj in (0, 1):
        for di in (0, 1):
            x, y = (i0 + di + 0.5) / n, (j0 + dj + 0.5) / n
            plon = x * 360.0 - 180.0
            plat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y))))
            v[di, dj] = pixel_value(grid, field, plon, plat, mode)
    return (v[0, 0] * (1 - wx) + v[1, 0] * wx) * (1 - wy) + (v[0, 1] * (1 - wx) + v[1, 1] * wx) * wy


def run(canon, n=200, seed=11):
    d = np.load(canon)
    grid = Grid(d["lat"], d["lon"])
    F = {k: d[k] for k in d.files if k not in ("lat", "lon", "meta")}
    rng = np.random.default_rng(seed)
    out = {}
    sets = {"t2m": "t2m", "rh2m": "rh2m", "cloud_total": "cloud_total", "wind10_u": "wind10_u",
            "wind10_v": "wind10_v", "gust10": "gust10", "precip_rate (cellules pluvieuses)": "precip_rate"}
    for name, key in sets.items():
        f = F[key]
        ok = np.isfinite(f)
        if "precip" in name:
            ok = ok & (f > 0.1)
        jj, ii = np.where(ok[6:-6, 6:-6])
        sel = rng.choice(len(jj), min(n, len(jj)), replace=False)
        pts = [(float(grid.lon[ii[k] + 6] + rng.uniform(0, grid.dlon)),
                float(grid.lat[jj[k] + 6] + rng.uniform(0, abs(grid.dlat)) * -1)) for k in sel]
        ref = [float(grid.sample(f, np.array([lo]), np.array([la]))[0, 0]) for lo, la in pts]
        out[name] = {"points": len(pts)}
        for mode in ("bilineaire", "proche"):
            for z in (7, 8, 9):
                e = np.array([abs(client_value(grid, f, lo, la, z, mode) - r) for (lo, la), r in zip(pts, ref)])
                e = e[np.isfinite(e)]
                out[name]["%s_z%d" % (mode, z)] = {"max": float(e.max()), "mean": float(e.mean()),
                                                   "p99": float(np.percentile(e, 99)), "n": int(len(e))}
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--canon", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    r = run(a.canon)
    if a.out:
        json.dump(r, open(a.out, "w"), indent=1)
    for k, v in r.items():
        print(k, {m: (round(x["max"], 3), round(x["mean"], 3)) for m, x in v.items() if m != "points"})
