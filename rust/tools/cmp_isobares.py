# SPDX-License-Identifier: EUPL-1.2
"""Compare les isobares Rust (rw-isobares) au Python de référence (pipeline.isobars) et mesure les temps.

Usage (depuis la racine du dépôt ; PYTHONPATH doit contenir la racine et, si besoin, les dossiers pylib / pylib2) :
  PYTHONPATH=.:<scratchpad>/pylib2:<scratchpad>/pylib python3 rust/tools/cmp_isobares.py \
      --canon out/canon/arome/2026-10-17T06Z --bin rust/target/release/rw-isobares \
      --out <dossier de travail> --echeances 0,12,24,36,48 [--mesures-52]

Pour chaque échéance : écrit la sortie Python (<out>/py/NN.geojson) et la sortie Rust (<out>/rs/NN.geojson),
puis compare : octets identiques ? sinon nombre de lignes, niveaux, drapeaux, nombre de sommets, écart maximal
de position des sommets (en mètres, distance équirectangulaire sur la sphère de 6371 km), extrema (genre,
position, pression, proéminence). Sortie : JSON sur la sortie standard ; code de retour 1 si une échéance
dépasse la tolérance (--tol-m, défaut 1 m).
"""
import argparse
import json
import math
import os
import subprocess
import sys
import time

from pipeline.isobars import calcul as K

R = 6371008.8


def metres(a, b):
    dlat = math.radians(b[1] - a[1]) * R
    dlon = math.radians(b[0] - a[0]) * R * math.cos(math.radians((a[1] + b[1]) / 2))
    return math.hypot(dlat, dlon)


def comparer(py, rs):
    """Compare deux FeatureCollection (dict). Renvoie un dict de mesures et d'écarts."""
    res = {"entete_identique": py["proprietes"] == rs["proprietes"]}
    li = lambda g: [f for f in g["features"] if f["properties"]["type"] == "isobare"]
    ex = lambda g: [f for f in g["features"] if f["properties"]["type"] == "extremum"]
    lp, lr = li(py), li(rs)
    res["lignes"] = [len(lp), len(lr)]
    res["niveaux_identiques"] = [f["properties"]["niveau"] for f in lp] == [f["properties"]["niveau"] for f in lr]
    res["proprietes_identiques"] = [f["properties"] for f in lp] == [f["properties"] for f in lr]
    res["sommets"] = [sum(len(f["geometry"]["coordinates"]) for f in lp), sum(len(f["geometry"]["coordinates"]) for f in lr)]
    ecart, pire, sommets_diff, lignes_diff = 0.0, None, 0, 0
    if len(lp) == len(lr):
        for k, (a, b) in enumerate(zip(lp, lr)):
            ca, cb = a["geometry"]["coordinates"], b["geometry"]["coordinates"]
            if len(ca) != len(cb):
                lignes_diff += 1
                continue
            d = [metres(p, q) for p, q in zip(ca, cb)]
            sommets_diff += sum(1 for x in d if x > 0)
            if d and max(d) > ecart:
                ecart, pire = max(d), (k, a["properties"]["niveau"])
    res["lignes_de_longueur_differente"] = lignes_diff
    res["sommets_differents"] = sommets_diff
    res["ecart_max_sommet_m"] = ecart
    res["ligne_la_plus_ecartee"] = pire
    ep, er = ex(py), ex(rs)
    res["extrema"] = [len(ep), len(er)]
    res["extrema_identiques"] = [f["properties"] for f in ep] == [f["properties"] for f in er] and \
        [f["geometry"] for f in ep] == [f["geometry"] for f in er]
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--canon", required=True)
    ap.add_argument("--bin", required=True, help="binaire rw-isobares (cargo build --release -p rw-isobares --features cli)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--echeances", default="0,12,24,36,48")
    ap.add_argument("--tol-m", type=float, default=1.0)
    ap.add_argument("--repetitions", type=int, default=3, help="répétitions de chronométrage (meilleure valeur)")
    ap.add_argument("--mesures-52", action="store_true", help="chronomètre aussi toutes les échéances, Python séquentiel puis Rust 1 fil et tous fils")
    a = ap.parse_args()
    ech = [int(x) for x in a.echeances.split(",")]
    py_dir, rs_dir = os.path.join(a.out, "py"), os.path.join(a.out, "rs")
    os.makedirs(py_dir, exist_ok=True)
    os.makedirs(rs_dir, exist_ok=True)
    liste = ",".join(map(str, ech))
    # Python (chronométré par échéance, meilleure de N répétitions ; la dernière écrit le fichier)
    t_py = {}
    for n in ech:
        f = os.path.join(a.canon, "%02d.npz" % n)
        best = 1e9
        for _ in range(a.repetitions):
            t = time.perf_counter()
            K.traiter(f, py_dir)
            best = min(best, time.perf_counter() - t)
        t_py[n] = best
    # Rust
    r = subprocess.run([a.bin, "--canon", a.canon, "--out", rs_dir, "--echeances", liste, "--repetitions", str(a.repetitions)],
                       capture_output=True, text=True, check=True)
    rust = json.loads(r.stdout)
    t_rs = {e["echeance"]: e["t_s"] for e in rust["par_echeance"]}
    r1 = subprocess.run([a.bin, "--canon", a.canon, "--out", rs_dir, "--echeances", liste, "--repetitions", str(a.repetitions),
                         "--fils", "1", "--sans-ecriture"], capture_output=True, text=True, check=True)
    t_rs1 = {e["echeance"]: e["t_s"] for e in json.loads(r1.stdout)["par_echeance"]}
    sortie, ok = {"echeances": {}}, True
    for n in ech:
        pf, rf = os.path.join(py_dir, "%02d.geojson" % n), os.path.join(rs_dir, "%02d.geojson" % n)
        bp, br = open(pf, "rb").read(), open(rf, "rb").read()
        c = {"octets_python": len(bp), "octets_rust": len(br), "octets_identiques": bp == br}
        c.update(comparer(json.loads(bp), json.loads(br)))
        c["t_python_s"], c["t_rust_s"], c["t_rust_1_fil_s"] = round(t_py[n], 4), round(t_rs[n], 4), round(t_rs1[n], 4)
        c["acceleration_1_fil"] = round(t_py[n] / t_rs1[n], 1)
        bon = c["octets_identiques"] or (c["niveaux_identiques"] and c["lignes"][0] == c["lignes"][1] and c["lignes_de_longueur_differente"] == 0
                                         and c["ecart_max_sommet_m"] <= a.tol_m and c["extrema_identiques"])
        c["conforme"] = bool(bon)
        ok &= bool(bon)
        sortie["echeances"][n] = c
    sortie["tous_conformes"] = ok
    sortie["ecart_max_sommet_m"] = max(c["ecart_max_sommet_m"] for c in sortie["echeances"].values())
    sortie["tolerance_m"] = a.tol_m
    if a.mesures_52:
        fichiers = sorted(f for f in os.listdir(a.canon) if f.endswith(".npz") and f[:2].isdigit())
        t = time.perf_counter()
        for f in fichiers:
            K.traiter(os.path.join(a.canon, f), os.path.join(a.out, "py52"))
        t_py52 = time.perf_counter() - t
        m = {"echeances": len(fichiers), "python_sequentiel_s": round(t_py52, 2)}
        for nom, extra in (("rust_1_fil", ["--fils", "1"]), ("rust_tous_fils", [])):
            t = time.perf_counter()
            rr = subprocess.run([a.bin, "--canon", a.canon, "--out", os.path.join(a.out, "rs52")] + extra, capture_output=True, text=True, check=True)
            m[nom + "_processus_s"] = round(time.perf_counter() - t, 3)
            m[nom + "_calcul_s"] = round(json.loads(rr.stdout)["somme_t_s"], 3)
        sortie["mesures_52"] = m
    json.dump(sortie, sys.stdout, indent=1, ensure_ascii=False)
    print()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
