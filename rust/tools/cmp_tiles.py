# SPDX-License-Identifier: EUPL-1.2
"""Compare les tuiles PMTiles Python (référence) et Rust, en codes de quantification et en valeurs décodées.

Usage : PYTHONPATH=<pmtiles>:<racine du dépôt> python3 cmp_tiles.py REF_ROOT TEST_ROOT MODELE TIRAGE ECHEANCE
Les deux racines contiennent <modèle>/<tirage>/<variable>/<échéance>.pmtiles.
Écrit un JSON : par variable, tuiles présentes de chaque côté, % de pixels de code identique,
écart max en codes, écart max en valeur physique, métadonnées et en-tête identiques ou non.
"""
import io
import json
import os
import sys

import numpy as np
from PIL import Image
from pmtiles.reader import MmapSource, Reader
from pmtiles.tile import deserialize_directory, tileid_to_zxy

from pipeline.tiling import decode as D


def tuiles(path):
    """{(z,x,y): octets} d'un PMTiles v3 (parcourt racine et feuilles)."""
    f = open(path, "rb")
    src = MmapSource(f)
    r = Reader(src)
    h = r.header()
    out = {}

    def parcourir(off, ln, feuille):
        for e in deserialize_directory(src(off, ln)):
            if e.run_length == 0:
                parcourir(h["leaf_directory_offset"] + e.offset, e.length, True)
            else:
                for k in range(e.run_length):
                    out[tileid_to_zxy(e.tile_id + k)] = src(h["tile_data_offset"] + e.offset, e.length)

    parcourir(h["root_offset"], h["root_length"], False)
    return out, h, r.metadata()


def codes(octets):
    im = Image.open(io.BytesIO(octets))
    assert im.mode == "RGB", im.mode
    return np.array(im)


def comparer(ref, test):
    tr, hr, mr = tuiles(ref)
    tt, ht, mt = tuiles(test)
    res = {"tuiles_ref": len(tr), "tuiles_test": len(tt), "tuiles_seulement_ref": len(set(tr) - set(tt)),
           "tuiles_seulement_test": len(set(tt) - set(tr)), "metadonnees_identiques": mr == mt,
           "octets_ref": os.path.getsize(ref), "octets_test": os.path.getsize(test)}
    if mr != mt:
        res["metadonnees_diff"] = {k: [mr.get(k), mt.get(k)] for k in set(mr) | set(mt) if mr.get(k) != mt.get(k)}
    res["entete_diff"] = {k: [hr[k], ht[k]] for k in hr
                          if hr[k] != ht[k] and k not in ("root_length", "metadata_length", "leaf_directory_offset",
                                                         "leaf_directory_length", "tile_data_offset", "tile_data_length",
                                                         "metadata_offset")}
    n = same = 0
    dmax = 0
    vmax = 0.0
    nodata_diff = 0
    for k in set(tr) & set(tt):
        a, b = codes(tr[k]), codes(tt[k])
        # canaux utiles : R (scalaire) ; R et G (vent wind8)
        canaux = (0, 1) if mr["encoding"] == "wind8" else (0,)
        for c in canaux:
            x, y = a[..., c].astype(np.int32), b[..., c].astype(np.int32)
            n += x.size
            same += int((x == y).sum())
            nodata_diff += int(((x == 0) != (y == 0)).sum())
            both = (x > 0) & (y > 0)
            if both.any():
                dmax = max(dmax, int(np.abs(x - y)[both].max()))
        # valeurs décodées avec la règle du contrat v = min + (code-1) * quant_step
        step = mr["quant_step"]
        if mr["encoding"] == "scalar8" and mr["scale"] == "linear":
            va = np.where(a[..., 0] > 0, mr["min"] + (a[..., 0].astype(np.float64) - 1) * step, np.nan)
            vb = np.where(b[..., 0] > 0, mr["min"] + (b[..., 0].astype(np.float64) - 1) * step, np.nan)
            ok = ~np.isnan(va) & ~np.isnan(vb)
            if ok.any():
                vmax = max(vmax, float(np.abs(va - vb)[ok].max()))
        elif mr["encoding"] == "wind8":
            for c in (0, 1):
                va = np.where(a[..., c] > 0, -48.0 + (a[..., c].astype(np.float64) - 1) * step, np.nan)
                vb = np.where(b[..., c] > 0, -48.0 + (b[..., c].astype(np.float64) - 1) * step, np.nan)
                ok = ~np.isnan(va) & ~np.isnan(vb)
                if ok.any():
                    vmax = max(vmax, float(np.abs(va - vb)[ok].max()))
    res.update({"pixels_comparés": n, "pct_codes_identiques": 100.0 * same / n if n else None,
                "ecart_max_codes": dmax, "pixels_nodata_differents": nodata_diff,
                "ecart_max_valeur_lineaire": vmax, "quant_step": mr["quant_step"]})
    # relecture par la bibliothèque de référence du dépôt (décodeur de contrat)
    with D.TileFile(test) as t:
        z, x, y = sorted(tt)[len(tt) // 2]
        t.read(z, x, y)
    res["lecture_decodeur_depot"] = "ok"
    return res


if __name__ == "__main__":
    ref_root, test_root, modele, tirage, ech = sys.argv[1:6]
    tot = {}
    for var in sorted(os.listdir(os.path.join(test_root, modele, tirage))):
        p = "%s/%s/%s/%02d.pmtiles" % (modele, tirage, var, int(ech))
        if not os.path.exists(os.path.join(ref_root, p)):
            tot[var] = "absent de la référence"
            continue
        tot[var] = comparer(os.path.join(ref_root, p), os.path.join(test_root, p))
    print(json.dumps(tot, ensure_ascii=False, indent=1))
