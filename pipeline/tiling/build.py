# SPDX-License-Identifier: EUPL-1.2
"""Construction des PMTiles v3 à partir d'un champ canonique .npz (contrat §3)."""
import json
import multiprocessing as mp
import os
import time

import numpy as np

from . import encode as E
from . import profiles as P
from .mercator import Grid, pixel_lonlat, tiles_covering

ZOOMS = {"arome-hd": (5, 8), "arome": (3, 7)}

_G = {}   # état partagé avec les processus fils (fork)


def _border(prof, z):
    return 0 if z >= prof.no_border_from else E.BORDER


def _zmax(prof, var, default):
    return min(prof.zmax.get(var, default), default)


def _tile_worker(job):
    z, x, y = job
    grid, fields, prof = _G["grid"], _G["fields"], _G["profil"]
    b = _border(prof, z)
    lon, lat = pixel_lonlat(z, x, y, border=b)
    out = {}
    clip = 0
    for var in _G["variables"]:
        if z > _G["zmax"][var]:
            continue
        q = None
        if var == E.WIND_VARIABLE:
            u = grid.sample(fields["wind10_u"], lon, lat)
            v = grid.sample(fields["wind10_v"], lon, lat)
            if prof.wind_mode == "wind8":
                arr = E.encode_wind8(u, v, prof.wind_vmax)
            else:
                arr = E.encode_wind12(u, v, prof.wind_drop_bits)
        else:
            samp = grid.sample(fields[var], lon, lat)
            rg = prof.ranges.get(var)
            if rg is not None:    # écrêtage compté (pixels de tuile hors plage resserrée)
                clip = int(((samp < rg[0]) | (samp > rg[1])).sum())
            arr = E.encode_scalar8(samp, var, prof.levels.get(var, E.LEVELS), rg)
            if var in prof.lossy and (arr > 0).all():
                q = prof.lossy[var]    # jamais sur une tuile contenant du « pas de donnée »
        if not arr.any():
            continue   # tuile entièrement « pas de donnée » : non écrite
        out[var] = E.to_webp(arr, lossy_quality=q)
        if var in prof.ranges:
            out[var + "#clip"] = clip
    return (z, x, y), out


def available_outputs(npz_keys, wanted=None):
    outs = [v for v in E.SCALAR8 if v in npz_keys]
    if "wind10_u" in npz_keys and "wind10_v" in npz_keys:
        outs.append(E.WIND_VARIABLE)
    if wanted:
        outs = [v for v in outs if v in wanted]
    return outs


def metadata_for(var, model, run, step, zmin, zmax, prof=P.CONTRAT):
    if var == E.WIND_VARIABLE and prof.wind_mode == "wind8":
        md = {"encoding": "wind8", "min": -prof.wind_vmax, "max": prof.wind_vmax,
              "scale": "linear", "unit": "m/s", "components": ["wind10_u", "wind10_v"],
              "quant_step": E.wind8_step(prof.wind_vmax)}
    elif var == E.WIND_VARIABLE:
        md = {"encoding": "wind12", "min": E.WIND_RANGE[0], "max": E.WIND_RANGE[1],
              "scale": "linear", "unit": "m/s", "components": ["wind10_u", "wind10_v"],
              "quant_step": E.WIND_STEP * 2 ** prof.wind_drop_bits}
    else:
        vmin, vmax, scale, unit = E.SCALAR8[var]
        lv = prof.levels.get(var, E.LEVELS)
        rg = prof.ranges.get(var)
        if rg is not None:
            vmin, vmax = rg
        md = {"encoding": "scalar8", "min": vmin, "max": vmax, "scale": scale,
              "unit": unit, "quant_step": E.scalar_step(var, lv, rg), "levels": lv}
        if var in prof.lossy:
            md["lossy_quality"] = prof.lossy[var]
    md["profile"] = prof.nom
    md["border_dropped_from_zoom"] = prof.no_border_from if prof.no_border_from < 99 else None
    md.update({"variable": var, "model": model, "run": run, "step": step,
               "border": E.BORDER, "tile_size": E.TILE, "stored_size": E.STORED,
               "format": "webp-lossless", "nodata_code": 0,
               "minzoom": zmin, "maxzoom": zmax,
               "attribution": "Source : Météo-France, Licence Ouverte v2.0"})
    return md


def write_pmtiles(path, tiles, md, bounds, zmin, zmax):
    """tiles : {(z, x, y): octets}. Écrit un PMTiles v3 (paquet `pmtiles`)."""
    from pmtiles.tile import Compression, TileType, zxy_to_tileid
    from pmtiles.writer import Writer
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    e7 = lambda d: int(round(d * 1e7))
    with open(tmp, "wb") as f:
        w = Writer(f)
        for (z, x, y), data in sorted(tiles.items(), key=lambda kv: zxy_to_tileid(*kv[0])):
            w.write_tile(zxy_to_tileid(z, x, y), data)
        w.finalize({
            "tile_type": TileType.WEBP, "tile_compression": Compression.NONE,
            "min_lon_e7": e7(bounds[0]), "min_lat_e7": e7(bounds[1]),
            "max_lon_e7": e7(bounds[2]), "max_lat_e7": e7(bounds[3]),
            "center_zoom": zmin, "center_lon_e7": e7((bounds[0] + bounds[2]) / 2),
            "center_lat_e7": e7((bounds[1] + bounds[3]) / 2),
        }, md)
    os.replace(tmp, path)


def build(canon_path, out_root, variables=None, zooms=None, workers=None, profil=None):
    """Tuile un .npz canonique ; renvoie la liste des mesures par fichier produit."""
    t0 = time.time()
    p = os.path.abspath(canon_path).split(os.sep)
    d = np.load(canon_path)
    meta = json.loads(str(d["meta"]))
    # tirage et échéance : méta d'abord (robuste hors arborescence out/canon), chemin sinon
    step = "%02d" % int(meta["step"]) if "step" in meta else os.path.splitext(p[-1])[0]
    run = p[-2]
    if meta.get("run"):
        r = meta["run"]      # 2026-10-17T06:00:00Z -> 2026-10-17T06Z
        run = r[:13] + "Z" if len(r) > 13 else r
    model = meta.get("model") or p[-3]
    if model not in ZOOMS:
        model = p[-3] if p[-3] in ZOOMS else "arome-hd"
    prof = profil or P.CONTRAT
    zmin, zmax = zooms or ZOOMS[model]
    grid = Grid(d["lat"], d["lon"])
    outs = available_outputs(set(d.files), variables)
    fields = {k: d[k] for k in d.files if k not in ("lat", "lon", "meta")}
    jobs = [(z, x, y) for z in range(zmin, zmax + 1) for x, y in tiles_covering(*grid.bounds, z)]
    _G.update(grid=grid, fields=fields, variables=outs, profil=prof,
              zmax={v: _zmax(prof, v, zmax) for v in outs})
    t1 = time.time()
    workers = workers or os.cpu_count() or 1
    if workers > 1:
        with mp.get_context("fork").Pool(workers) as pool:
            results = pool.map(_tile_worker, jobs, chunksize=4)
    else:
        results = [_tile_worker(j) for j in jobs]
    t2 = time.time()
    report = []
    for var in outs:
        tiles = {k: o[var] for k, o in results if var in o}
        clipped = sum(o.get(var + "#clip", 0) for _, o in results)
        vzmax = _G["zmax"][var]
        path = os.path.join(out_root, model, run, var, "%s.pmtiles" % step)
        md = metadata_for(var, model, run, step, zmin, vzmax, prof)
        write_pmtiles(path, tiles, md, grid.bounds, zmin, vzmax)
        per_zoom = {z: sum(1 for k in tiles if k[0] == z) for z in range(zmin, vzmax + 1)}
        report.append({"model": model, "run": run, "step": step, "variable": var, "path": path,
                       "bytes": os.path.getsize(path), "tiles": len(tiles), "profil": prof.nom, "pixels_ecretes": clipped, "tiles_per_zoom": per_zoom,
                       "bytes_tiles": sum(len(b) for b in tiles.values())})
    t3 = time.time()
    total = t3 - t0
    for r in report:     # temps du fichier = temps total partagé (rééchantillonnage commun)
        r["seconds_file_total"] = total
        r["seconds_resample_encode_all_vars"] = t2 - t1
    return report
