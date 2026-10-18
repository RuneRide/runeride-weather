# SPDX-License-Identifier: EUPL-1.2
"""python3 -m pipeline.manifest --tiles out/tiles-reduit2 --out out/manifest/manifest.json"""
import argparse
import os

from . import build


def main(argv=None):
    ap = argparse.ArgumentParser(prog="pipeline.manifest", description=__doc__)
    ap.add_argument("--tiles", required=True, help="racine des tuiles : <modèle>/<tirage>/<variable>/<NN>.pmtiles")
    ap.add_argument("--out", default="out/manifest/manifest.json")
    ap.add_argument("--canon", default="out/canon", help="champs canoniques (polygone du domaine) ; absent : rectangle")
    ap.add_argument("--tolerance", type=float, default=0.04, help="simplification du polygone, en degrés")
    ap.add_argument("--generated", help="date de génération ISO (défaut : maintenant ; fixée pour les tests)")
    a = ap.parse_args(argv)
    canon = a.canon if os.path.isdir(a.canon) else None
    m = build.construire(a.tiles, canon, a.generated, a.tolerance)
    build.ecrire(m, a.out)
    for nom, mod in m["models"].items():
        for r in mod["runs"]:
            print("%-9s %s  complet=%-5s  %s" % (nom, r["id"], r["complete"],
                  " ".join("%s:%d" % (v, len(e["steps"])) for v, e in r["variables"].items())))
        print("%-9s polygone : %d sommets (%s)" % (nom, mod["domain"]["vertices"], mod["domain"]["source"]))
    print("manifest écrit :", a.out)


if __name__ == "__main__":
    main()
