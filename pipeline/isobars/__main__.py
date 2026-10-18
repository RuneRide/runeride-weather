# SPDX-License-Identifier: EUPL-1.2
"""Usage : python3 -m pipeline.isobars --canon out/canon/arome/2026-10-17T06Z --out out/isobars
Écrit <out>/<modèle><suffixe>/<tirage>/NN.geojson (un par échéance) et stats.json."""
import argparse
import glob
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor

from . import calcul as K


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--canon", required=True, help="dossier out/canon/<modèle>/<tirage>")
    ap.add_argument("--out", required=True, help="racine de sortie (ex. out/isobars)")
    ap.add_argument("--sigma", type=float, default=K.SIGMA, help="lissage gaussien en mailles (0 = aucun)")
    ap.add_argument("--pas", type=float, default=K.PAS_HPA)
    ap.add_argument("--tol-km", type=float, default=K.TOL_KM)
    ap.add_argument("--proeminence", type=float, default=K.PROEMINENCE)
    ap.add_argument("--sigma-extrema", type=float, default=K.SIGMA_EXTREMA)
    ap.add_argument("--suffixe", default="", help="ajouté au nom du modèle en sortie (variantes : -sans-lissage)")
    ap.add_argument("--echeances", default="", help="liste séparée par des virgules (défaut : toutes)")
    ap.add_argument("--jobs", type=int, default=min(16, os.cpu_count() or 1))
    a = ap.parse_args()
    canon = os.path.abspath(a.canon).rstrip(os.sep)
    tirage, modele = os.path.basename(canon), os.path.basename(os.path.dirname(canon))
    dest = os.path.join(a.out, modele + a.suffixe, tirage)
    fichiers = sorted(glob.glob(os.path.join(canon, "[0-9][0-9].npz")))
    if a.echeances:
        voulues = {int(x) for x in a.echeances.split(",")}
        fichiers = [f for f in fichiers if int(os.path.basename(f)[:2]) in voulues]
    kw = dict(sigma=a.sigma, pas=a.pas, tol_km=a.tol_km, proeminence=a.proeminence, sigma_ext=a.sigma_extrema)
    t0 = time.perf_counter()
    with ProcessPoolExecutor(a.jobs) as ex:
        stats = list(ex.map(K.tache, [((f, dest), kw) for f in fichiers]))
    dt = time.perf_counter() - t0
    stats.sort(key=lambda s: s["echeance"])
    resume = {"echeances": len(stats), "duree_murale_s": round(dt, 2), "jobs": a.jobs,
              "somme_durees_par_echeance_s": round(sum(s["t_total_s"] for s in stats), 2),
              "octets_total": sum(s["octets"] for s in stats), "parametres": kw, "par_echeance": stats}
    with open(os.path.join(dest, "stats.json"), "w") as f:
        json.dump(resume, f, indent=1)
    print("%d échéances -> %s en %.1f s (somme des durées par échéance : %.1f s), %.2f Mo" % (
        len(stats), dest, dt, resume["somme_durees_par_echeance_s"], resume["octets_total"] / 1e6))


if __name__ == "__main__":
    main()
