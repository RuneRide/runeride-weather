# SPDX-License-Identifier: EUPL-1.2
"""Mesures du PoC J1 (aller-retour, couture, 20 points, WebP/PNG, temps par variable).

Usage : python3 -m pipeline.tiling.measure --canon out/hd_step03.npz --tiles out/tiles [--out mesures.json]
Les résultats sont écrits en JSON et résumés sur la sortie standard.
"""
import argparse
import json
import math
import os
import shutil
import tempfile
import time

import numpy as np

from . import build as B
from . import encode as E
from .decode import TileFile
from .mercator import Grid, lonlat_to_tile, pixel_lonlat, tiles_covering


def roundtrip(fields):
    """Erreur de quantification pure sur la grille source complète (valeurs valides seulement)."""
    res = {}
    for var in E.SCALAR8:
        if var not in fields:
            continue
        v = fields[var]
        ok = np.isfinite(v)
        back = E.decode_scalar8(E.encode_scalar8(v, var), var)
        err = np.abs(back[ok].astype(np.float64) - v[ok])
        res[var] = {"max": float(err.max()), "mean": float(err.mean()), "step": E.scalar_step(var),
                    "demi_pas": E.scalar_step(var) / 2, "n": int(ok.sum()),
                    "hors_plage": int(((v[ok] < E.SCALAR8[var][0]) | (v[ok] > E.SCALAR8[var][1])).sum())}
    if "wind10_u" in fields:
        u, v = fields["wind10_u"], fields["wind10_v"]
        ok = np.isfinite(u) & np.isfinite(v)
        du, dv = E.decode_wind12(E.encode_wind12(u, v))
        for nm, a, b in (("wind10_u", u, du), ("wind10_v", v, dv)):
            err = np.abs(b[ok].astype(np.float64) - a[ok])
            res[nm] = {"max": float(err.max()), "mean": float(err.mean()), "step": E.WIND_STEP,
                       "demi_pas": E.WIND_STEP / 2, "n": int(ok.sum()),
                       "hors_plage": int((np.abs(a[ok]) > 64).sum())}
    return res


def _bilin(a, fi, fj, clamp):
    """Bilinéaire client sur un tableau ; indices continus (fi colonne, fj ligne)."""
    h, w = a.shape
    i0, j0 = int(math.floor(fi)), int(math.floor(fj))
    wx, wy = fi - i0, fj - j0
    ii = [min(max(i0, 0), w - 1), min(max(i0 + 1, 0), w - 1)] if clamp else [i0, i0 + 1]
    jj = [min(max(j0, 0), h - 1), min(max(j0 + 1, 0), h - 1)] if clamp else [j0, j0 + 1]
    s = a[np.ix_(jj, ii)].astype(np.float64)
    return float((s[0, 0] * (1 - wx) + s[0, 1] * wx) * (1 - wy) + (s[1, 0] * (1 - wx) + s[1, 1] * wx) * wy)


def seam(tiles_dir, grid, fields, run_rel, z, vars_=("t2m", "gust10", "wind10_u")):
    """Écart de valeur interpolée à cheval sur une jonction verticale entre deux tuiles voisines.

    Pour chaque paire (x, x+1) et 9 hauteurs, on lit la valeur sur la ligne de jonction
    (bord de pixel x_local = 256 pour A, 0 pour B) : avec bordure (texture 258 entière) et sans
    bordure (texture 256, bord répété). Vérité : source rééchantillonnée au même point.
    """
    res = {}
    for var in vars_:
        fv = "wind10" if var.startswith("wind10") else var
        path = os.path.join(tiles_dir, run_rel, fv, "03.pmtiles")
        d_with, d_without, e_with, e_without = [], [], [], []
        with TileFile(path) as t:
            ts = set(tiles_covering(*grid.bounds, z))
            cache = {}

            def get(x, y):
                if (x, y) not in cache:
                    r = t.read(z, x, y)
                    cache[(x, y)] = r[0] if var == "wind10_u" else (r[1] if var == "wind10_v" else r)
                return cache[(x, y)]
            for (x, y) in sorted(ts):
                if (x + 1, y) not in ts:
                    continue
                A, Bt = get(x, y), get(x + 1, y)
                n = 2 ** z * E.TILE
                for py in np.linspace(8.5, 247.5, 9):
                    fj = py + 0.5 + 0.0   # bord de pixel py -> indice continu py - 0.5 + 1
                    fj = py - 0.5 + E.BORDER
                    # avec bordure
                    a_w = _bilin(A, 256 + E.BORDER - 0.5, fj, False)
                    b_w = _bilin(Bt, 0 + E.BORDER - 0.5, fj, False)
                    # sans bordure : texture 256 (indices 1..256 de la 258), bord répété
                    a_n = _bilin(A[1:257, 1:257], 256 - 0.5, py - 0.5, True)
                    b_n = _bilin(Bt[1:257, 1:257], 0 - 0.5, py - 0.5, True)
                    if any(np.isnan([a_w, b_w, a_n, b_n])):
                        continue
                    lon = (x + 1) * E.TILE / n * 360.0 - 180.0
                    gy = (y * E.TILE + py) / n
                    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * gy))))
                    ref = grid.sample(fields[var], np.array([lon]), np.array([lat]))[0, 0]
                    if np.isnan(ref):
                        continue
                    d_with.append(abs(a_w - b_w))
                    d_without.append(abs(a_n - b_n))
                    e_with += [abs(a_w - ref), abs(b_w - ref)]
                    e_without += [abs(a_n - ref), abs(b_n - ref)]
        f = lambda x: {"max": float(np.max(x)), "mean": float(np.mean(x))}
        res[var] = {"zoom": z, "points": len(d_with), "ecart_AB_avec_bordure": f(d_with),
                    "ecart_AB_sans_bordure": f(d_without), "ecart_vrai_avec": f(e_with),
                    "ecart_vrai_sans": f(e_without)}
    return res


def points20(tiles_dir, grid, fields, run_rel, z):
    """20 positions réparties (dont 8 proches des bords du domaine) : décodé contre source."""
    # le domaine AROME n'est pas un rectangle : 17 % de la grille est NaN (coins) ; les points
    # « de bord » sont donc pris à 3 mailles à l'intérieur du bord du domaine valide.
    valid = np.isfinite(fields["t2m"])
    pts = []
    for j in (int(0.17 * grid.nlat), int(0.5 * grid.nlat), int(0.83 * grid.nlat)):
        cols = np.where(valid[j])[0]
        pts.append((float(grid.lon[cols[0] + 3]), float(grid.lat[j])))      # bord ouest
        pts.append((float(grid.lon[cols[-1] - 3]), float(grid.lat[j])))     # bord est
    for j in (4, grid.nlat - 5):                                             # bords nord et sud
        cols = np.where(valid[j])[0]
        pts.append((float(grid.lon[(cols[0] + cols[-1]) // 2]) + 0.0037, float(grid.lat[j]) - 0.0043))
    rng = np.random.default_rng(2026)
    jj, ii = np.where(valid[5:-5, 5:-5])
    for k in rng.choice(len(jj), 12, replace=False):
        pts.append((float(grid.lon[ii[k] + 5] + rng.uniform(0, 0.01)), float(grid.lat[jj[k] + 5] - rng.uniform(0, 0.01))))
    out = {}
    for var in list(E.SCALAR8) + ["wind10"]:
        path = os.path.join(tiles_dir, run_rel, var, "03.pmtiles")
        if not os.path.exists(path):
            continue
        rows, rows_c = [], []
        with TileFile(path) as t:
            for lon, lat in pts:
                # (b) au centre du pixel de tuile le plus proche : quantification + tuilage seuls
                x, y = lonlat_to_tile(lon, lat, z)
                plon, plat = pixel_lonlat(z, x, y)
                i, j = int(np.argmin(np.abs(plon - lon))), int(np.argmin(np.abs(plat - lat)))
                comps_c = ("wind10_u", "wind10_v") if var == "wind10" else (var,)
                ref_c = [float(grid.sample(fields[c], plon[i:i + 1], plat[j:j + 1])[0, 0]) for c in comps_c]
                tile = t.read(z, x, y)
                got_c = [float(a[j, i]) for a in tile] if var == "wind10" else [float(tile[j, i])]
                rows_c.append((ref_c, got_c))
                got = t.sample(lon, lat, z)
                comps = ("wind10_u", "wind10_v") if var == "wind10" else (var,)
                ref = [float(grid.sample(fields[c], np.array([lon]), np.array([lat]))[0, 0]) for c in comps]
                got = list(got) if var == "wind10" else [got]
                step = E.WIND_STEP if var == "wind10" else E.scalar_step(var)
                diffs = [abs(g - r) for g, r in zip(got, ref)]
                rows.append({"lon": lon, "lat": lat, "ref": ref, "got": got, "ecart": diffs, "pas": step,
                             "nan_ref": any(np.isnan(ref)), "nan_got": any(np.isnan(got))})
        valid = [r for r in rows if not r["nan_ref"] and not r["nan_got"]]
        worst = max((max(r["ecart"]) / r["pas"] for r in valid), default=float("nan"))
        vc = [(r, g) for r, g in rows_c if not any(np.isnan(r)) and not any(np.isnan(g))]
        step = rows[0]["pas"]
        centre_max = max((abs(a - b) for r, g in vc for a, b in zip(r, g)), default=float("nan"))
        out[var] = {"centre_pixel_compares": len(vc), "centre_pixel_ecart_max": centre_max,
                    "centre_pixel_ecart_max_sur_pas": centre_max / step,
                    "centre_pixel_nan_incoherents": sum(1 for r, g in rows_c if any(np.isnan(r)) != any(np.isnan(g))),
                    "points": len(rows), "compares": len(valid),
                    "nan_des_deux_cotes": sum(1 for r in rows if r["nan_ref"] and r["nan_got"]),
                    "nan_incoherents": sum(1 for r in rows if r["nan_ref"] != r["nan_got"]),
                    "ecart_max_sur_pas": worst,
                    "ecart_max": max((max(r["ecart"]) for r in valid), default=float("nan")),
                    "pas": rows[0]["pas"], "rows": rows}
    return out


def webp_vs_png(tiles_dir, grid, run_rel, z, every=4):
    """Poids d'un échantillon de tuiles : WebP sans perte (effort 4 et 6) contre PNG optimisé."""
    res = {}
    for var in ("t2m", "wind10"):
        path = os.path.join(tiles_dir, run_rel, var, "03.pmtiles")
        sizes = {"webp_defaut": 0, "webp4": 0, "webp6": 0, "png": 0}
        n = 0
        with TileFile(path) as t:
            for (x, y) in sorted(tiles_covering(*grid.bounds, z))[::every]:
                data = t.raw(z, x, y)
                if data is None:
                    continue
                arr = E.from_image(data)
                if var != "wind10":
                    arr = arr[..., 0]
                sizes["webp_defaut"] += len(E.to_webp(arr))
                sizes["webp4"] += len(E.to_webp(arr, method=4))
                sizes["webp6"] += len(E.to_webp(arr, method=6))
                sizes["png"] += len(E.to_webp(arr, "PNG"))
                n += 1
        res[var] = {"tuiles": n, "zoom": z, **sizes}
    return res


def timing_per_variable(canon, workers):
    out = {}
    d = tempfile.mkdtemp(prefix="mesure_tps_")
    try:
        t = time.time()
        B.build(canon, d, variables=["__aucune__"], workers=workers)
        out["_base_sans_variable"] = time.time() - t
        for var in E.OUTPUT_VARIABLES:
            t = time.time()
            rep = B.build(canon, d, variables=[var], workers=workers)
            out[var] = {"secondes": time.time() - t, "octets": rep[0]["bytes"] if rep else 0}
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return out


def per_zoom_stats(tiles_dir, run_rel, zooms):
    res = {}
    for var in E.OUTPUT_VARIABLES:
        path = os.path.join(tiles_dir, run_rel, var, "03.pmtiles")
        if not os.path.exists(path):
            continue
        from pmtiles.reader import MmapSource, Reader, all_tiles
        with open(path, "rb") as f:
            src = MmapSource(f)
            cnt, byt = {}, {}
            for (z, x, y), data in all_tiles(src):
                cnt[z] = cnt.get(z, 0) + 1
                byt[z] = byt.get(z, 0) + len(data)
        res[var] = {"tuiles": cnt, "octets_tuiles": byt, "fichier": os.path.getsize(path)}
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--canon", required=True)
    ap.add_argument("--tiles", default="out/tiles")
    ap.add_argument("--run-rel", default="arome-hd/2026-10-17T06Z")
    ap.add_argument("--zoom", type=int, default=8)
    ap.add_argument("--zmin", type=int, default=5)
    ap.add_argument("--out")
    ap.add_argument("--with-timing", action="store_true")
    a = ap.parse_args()
    d = np.load(a.canon)
    grid = Grid(d["lat"], d["lon"])
    fields = {k: d[k] for k in d.files if k not in ("lat", "lon", "meta")}
    r = {"aller_retour": roundtrip(fields),
         "couture_z%d" % a.zoom: seam(a.tiles, grid, fields, a.run_rel, a.zoom),
         "couture_z%d" % a.zmin: seam(a.tiles, grid, fields, a.run_rel, a.zmin),
         "points20": points20(a.tiles, grid, fields, a.run_rel, a.zoom),
         "webp_png_z7": webp_vs_png(a.tiles, grid, a.run_rel, 7),
         "par_zoom": per_zoom_stats(a.tiles, a.run_rel, range(a.zmin, a.zoom + 1))}
    if a.with_timing:
        r["temps_par_variable"] = timing_per_variable(a.canon, None)
    if a.out:
        with open(a.out, "w") as f:
            json.dump(r, f, indent=1, default=float)
    print(json.dumps({k: v for k, v in r.items() if k != "points20"}, indent=1, default=float))
    for var, v in r["points20"].items():
        print("points20", var, {k: v[k] for k in v if k != "rows"})


if __name__ == "__main__":
    main()
