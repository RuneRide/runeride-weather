# SPDX-License-Identifier: EUPL-1.2
"""python3 -m pipeline.tiling --canon <.npz...> --out out/tiles [--variables ...]"""
import argparse
import json
import sys

from .build import build


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pipeline.tiling", description=__doc__)
    ap.add_argument("--canon", nargs="+", required=True, help="fichier(s) .npz canonique(s)")
    ap.add_argument("--out", default="out/tiles")
    ap.add_argument("--variables", nargs="*", help="sous-ensemble : t2m gust10 rh2m ... wind10")
    ap.add_argument("--zooms", nargs=2, type=int, metavar=("ZMIN", "ZMAX"))
    ap.add_argument("--workers", type=int, default=None, help="défaut : nombre de cœurs")
    ap.add_argument("--webp-method", type=int, default=None, help="effort WebP 0-6 (défaut 4)")
    ap.add_argument("--json", help="écrit les mesures dans ce fichier JSON")
    a = ap.parse_args(argv)
    if a.webp_method is not None:
        from . import encode
        encode.WEBP_METHOD = a.webp_method
    allrep = []
    for c in a.canon:
        rep = build(c, a.out, a.variables, tuple(a.zooms) if a.zooms else None, a.workers)
        for r in rep:
            print("%-9s %s %s %-14s %5d tuiles %9.2f Mo" % (
                r["model"], r["run"], r["step"], r["variable"], r["tiles"], r["bytes"] / 1e6))
        print("  temps total %.1f s" % rep[0]["seconds_file_total"] if rep else "  rien à écrire")
        allrep += rep
    if a.json:
        with open(a.json, "w") as f:
            json.dump(allrep, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
