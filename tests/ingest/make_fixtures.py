# SPDX-License-Identifier: EUPL-1.2
"""Fabrique les extraits légers de tests/ingest/data/ depuis les vrais GRIB AROME 0,025° (tirage 2026-10-17T06Z).

Usage : PYTHONPATH=<pylib eccodes> python3 tests/ingest/make_fixtures.py <dossiers GRIB> [--canon out/canon]
Deux extraits (Alpes du Nord, 40 × 60 points, < 1 Mo au total) :
- raccord_025.npz : cumuls tirf/tsnowp/tgrp/tp aux échéances 1-8 (le cumul de l'échéance 0 est nul par définition) (raccord 6 -> 7) et sorties canoniques attendues ;
- neige_025.npz   : cumuls et t2m aux échéances 17-21 (neige réelle) et snow_fraction/precip_rate attendus.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from pipeline.ingest import extension  # noqa: E402

LAT = (46.3, 45.3)   # nord, sud
LON = (6.4, 7.9)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    canon = "out/canon"
    cumuls = extension.lire_cumuls("arome", args, {"tirf", "tsnowp", "tgrp", "tp"}, range(0, 22))
    d0 = np.load(os.path.join(canon, "arome", "2026-10-17T06Z", "07.npz"))
    ii = np.where((d0["lat"] <= LAT[0]) & (d0["lat"] >= LAT[1]))[0]
    jj = np.where((d0["lon"] >= LON[0]) & (d0["lon"] <= LON[1]))[0]
    sel = np.ix_(ii, jj)
    out = os.path.join(os.path.dirname(__file__), "data")
    for nom, echeances, extra in (("raccord_025", range(1, 9), ()), ("neige_025", range(17, 22), ("t2m",))):
        pack = {"lat": d0["lat"][ii], "lon": d0["lon"][jj], "echeances": np.array(list(echeances))}
        for n in echeances:
            c = np.load(os.path.join(canon, "arome", "2026-10-17T06Z", f"{n:02d}.npz"))
            for k in ("tirf", "tsnowp", "tgrp", "tp"):
                pack[f"{k}_{n}"] = cumuls[n][k][sel]
            for k in extra:
                pack[f"{k}_{n}"] = c[k][sel]
            for k in ("precip_rate", "snow_fraction"):
                if k in c.files:
                    pack[f"attendu_{k}_{n}"] = c[k][sel]
        np.savez_compressed(os.path.join(out, nom + ".npz"), **pack)
        print(nom, os.path.getsize(os.path.join(out, nom + ".npz")), "octets")


if __name__ == "__main__":
    main()
