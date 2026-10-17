# SPDX-License-Identifier: EUPL-1.2
"""Mesure des profils de réduction du poids (étape 2 du PoC J1).

Pour chaque profil : poids par variable, temps, erreur des pixels de tuile décodés contre la source
rééchantillonnée (flottante, avant quantification) et erreur à 200 positions quelconques
(bilinéaire client sur les tuiles décodées contre bilinéaire directe sur la source).
Usage : python3 -m pipeline.tiling.profils_mesure --canon out/hd_step03.npz --out mesures.json [profils...]
"""
import argparse
import json
import os
import shutil
import tempfile
import time

import numpy as np

from . import build as B
from . import encode as E
from . import profiles as P
from .decode import TileFile
from .mercator import Grid, lonlat_to_tile, pixel_lonlat, tiles_covering


def random_points(grid, valid, n=200, seed=7):
    rng = np.random.default_rng(seed)
    jj, ii = np.where(valid[6:-6, 6:-6])
    k = rng.choice(len(jj), n, replace=False)
    lon = grid.lon[ii[k] + 6] + rng.uniform(0, abs(grid.dlon), n)
    lat = grid.lat[jj[k] + 6] + rng.uniform(0, abs(grid.dlat), n) * -1
    return lon, lat


def stats(err):
    err = np.asarray(err, dtype=np.float64)
    err = err[np.isfinite(err)]
    if not len(err):
        return {"n": 0}
    return {"n": int(len(err)), "max": float(err.max()), "mean": float(err.mean()),
            "p99": float(np.percentile(err, 99))}


def measure_profile(canon, prof, workdir, grid, fields, pts, every=3):
    t0 = time.time()
    rep = B.build(canon, workdir, profil=prof)
    sec = time.time() - t0
    res = {"profil": prof.nom, "secondes": sec, "poids": {}, "tuile": {}, "position": {}}
    run_rel = os.path.join(rep[0]["model"], rep[0]["run"])
    for r in rep:
        var = r["variable"]
        res["poids"][var] = r["bytes"]
        path = r["path"]
        with TileFile(path) as t:
            z = t.header["max_zoom"]
            errs = [[], []]
            for (x, y) in sorted(tiles_covering(*grid.bounds, z))[::every]:
                b = (t.read(z, x, y)[0].shape[0] - 256) // 2 if var == "wind10" else (t.read(z, x, y).shape[0] - 256) // 2
                lon, lat = pixel_lonlat(z, x, y, border=b)
                if var == "wind10":
                    got = t.read(z, x, y)
                    ref = [grid.sample(fields["wind10_u"], lon, lat), grid.sample(fields["wind10_v"], lon, lat)]
                    for k in (0, 1):
                        errs[k].append(np.abs(got[k] - ref[k]).ravel())
                else:
                    got = t.read(z, x, y)
                    ref = grid.sample(fields[var], lon, lat)
                    nanmis = (np.isnan(got) != np.isnan(ref)).sum()
                    errs[0].append(np.abs(got - ref).ravel())
                    res["tuile"].setdefault(var + "_nan_incoherents", 0)
                    res["tuile"][var + "_nan_incoherents"] += int(nanmis)
            e = np.concatenate(errs[0] + errs[1]) if var == "wind10" else np.concatenate(errs[0])
            res["tuile"][var] = {**stats(e), "zoom": z, "pas": t.meta["quant_step"]}
            pe = []
            for lon, lat in zip(*pts):
                got = t.sample(lon, lat, z)
                if var == "wind10":
                    ru = grid.sample(fields["wind10_u"], np.array([lon]), np.array([lat]))[0, 0]
                    rv = grid.sample(fields["wind10_v"], np.array([lon]), np.array([lat]))[0, 0]
                    pe += [abs(got[0] - ru), abs(got[1] - rv)]
                else:
                    pe.append(abs(got - grid.sample(fields[var], np.array([lon]), np.array([lat]))[0, 0]))
            res["position"][var] = stats(pe)
    res["total"] = sum(res["poids"].values())
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--canon", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("profils", nargs="*")
    a = ap.parse_args()
    d = np.load(a.canon)
    grid = Grid(d["lat"], d["lon"])
    fields = {k: d[k] for k in d.files if k not in ("lat", "lon", "meta")}
    pts = random_points(grid, np.isfinite(fields["t2m"]))
    out = []
    for nom in a.profils or list(P.PROFILS):
        w = tempfile.mkdtemp(prefix="profil_")
        try:
            out.append(measure_profile(a.canon, P.get(nom), w, grid, fields, pts))
        finally:
            shutil.rmtree(w, ignore_errors=True)
        print(nom, round(out[-1]["total"] / 1e6, 2), "Mo", flush=True)
        json.dump(out, open(a.out, "w"), indent=1, default=float)


if __name__ == "__main__":
    main()
