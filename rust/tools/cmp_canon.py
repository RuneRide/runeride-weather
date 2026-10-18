# SPDX-License-Identifier: EUPL-1.2
"""Compare deux champs canoniques .npz (Python de référence contre Rust).

Usage : python3 -I cmp_canon.py ref.npz test.npz
Écrit une ligne JSON : par variable, nombre de NaN différents, écart relatif max, écart absolu max.
"""
import json
import sys

import numpy as np


def comparer(ref, test):
    a, b = np.load(ref), np.load(test)
    res = {"fichier": test, "cles_identiques": sorted(a.files) == sorted(b.files), "variables": {}}
    for k in a.files:
        if k == "meta":
            continue
        if k not in b.files:
            res["variables"][k] = "absente"
            continue
        x, y = a[k], b[k]
        if x.shape != y.shape:
            res["variables"][k] = "forme %s contre %s" % (x.shape, y.shape)
            continue
        nx, ny = np.isnan(x), np.isnan(y)
        ok = ~nx & ~ny
        d = np.abs(x[ok].astype(np.float64) - y[ok].astype(np.float64))
        rel = d / np.maximum(np.abs(x[ok].astype(np.float64)), 1e-30)
        # relatif seulement là où |ref| > 1e-3 (sinon l'absolu compte)
        sig = np.abs(x[ok]) > 1e-3
        res["variables"][k] = {
            "nan_differents": int((nx != ny).sum()),
            "identiques_bit": bool(np.array_equal(x, y, equal_nan=True)),
            "pixels_differents": int((x[ok] != y[ok]).sum()),
            "ecart_abs_max": float(d.max()) if d.size else 0.0,
            "ecart_rel_max": float(rel[sig].max()) if sig.any() else 0.0,
        }
    meta_a, meta_b = json.loads(str(a["meta"])), json.loads(str(b["meta"]))
    res["meta_model_run_step"] = [meta_a[k] == meta_b[k] for k in ("model", "run", "step")]
    return res


if __name__ == "__main__":
    print(json.dumps(comparer(sys.argv[1], sys.argv[2]), ensure_ascii=False))
