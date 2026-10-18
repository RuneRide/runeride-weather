# SPDX-License-Identifier: EUPL-1.2
"""Banc de mesure Python contre Rust : GRIB -> canonique (a), tuilage (b), total (c).

Chaque commande est lancée sous `/usr/bin/time -v` (temps mur, CPU utilisateur + système, pic mémoire).
Unité mesurée : une échéance = un processus (comme une invocation Lambda) ; on somme les 5 échéances.
Les répétitions sont entrelacées (Python et Rust se suivent) ; la médiane est calculée sur les répétitions.

Usage (depuis la racine du dépôt) :
  PYTHONPATH=<eccodes>:<pmtiles>:. python3 rust/tools/bench.py --reps 3 --out out/rust/bench/resultats.json
"""
import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time

RW = "rust/target/release/rw"
MODELES = {"arome-hd": "2026-10-17T03:00:00Z", "arome": "2026-10-17T06:00:00Z"}
ECHEANCES = [0, 12, 24, 36, 48]
SRC = "out/rust/src"


def mesurer(cmd, env=None):
    t = time.time()
    p = subprocess.run(["/usr/bin/time", "-v"] + cmd, capture_output=True, text=True, env=env)
    mur = time.time() - t
    if p.returncode != 0:
        sys.exit("échec : %s\n%s" % (" ".join(cmd), p.stderr[-2000:]))
    g = lambda motif: re.search(motif, p.stderr).group(1)
    return {
        "mur_s": mur,
        "cpu_s": float(g(r"User time \(seconds\): ([\d.]+)")) + float(g(r"System time \(seconds\): ([\d.]+)")),
        "rss_mo": int(g(r"Maximum resident set size \(kbytes\): (\d+)")) / 1024.0,
        "sortie": p.stdout,
    }


def canon_py(modele, run, n):
    return "out/rust/py/canon/%s/%sZ/%02d.npz" % (modele, run[:13], n)


def commandes(modele, run, n, T):
    """{étape: commande} pour une échéance et un nombre de threads."""
    t = str(T)
    return {
        "py_a": [sys.executable, "-m", "pipeline.ingest", "--model", modele, "--run", run, "--steps", str(n),
                 "--src", SRC, "--out", "out/rust/py/canon"],
        "py_b": [sys.executable, "-m", "pipeline.tiling", "--canon", canon_py(modele, run, n), "--out", "out/rust/py/tiles",
                 "--profil", "reduit2", "--workers", t],
        "rs_a": [RW, "ingest", "--model", modele, "--run", run, "--step", str(n), "--src", SRC, "--out", "out/rust/rs/canon",
                 "--threads", t],
        "rs_a_tout": [RW, "ingest", "--model", modele, "--run", run, "--step", str(n), "--src", SRC, "--out", "out/rust/rs/canon",
                      "--threads", t, "--decode-all"],
        "rs_b": [RW, "tile", "--canon", canon_py(modele, run, n), "--out", "out/rust/rs/tiles", "--threads", t],
        "rs_b_pur": [RW, "tile", "--canon", canon_py(modele, run, n), "--out", "out/rust/rs/tiles-pur", "--threads", t,
                     "--webp", "image-webp"],
        "rs_c": [RW, "run", "--model", modele, "--run", run, "--step", str(n), "--src", SRC, "--out", "out/rust/rs/tiles",
                 "--threads", t],
        "rs_c_pur": [RW, "run", "--model", modele, "--run", run, "--step", str(n), "--src", SRC, "--out", "out/rust/rs/tiles-pur",
                     "--threads", t, "--webp", "image-webp"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--out", default="out/rust/bench/resultats.json")
    ap.add_argument("--threads", type=int, nargs="+", default=[1, 4])
    ap.add_argument("--modeles", nargs="+", default=list(MODELES))
    a = ap.parse_args()
    env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1")
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    res = []   # une ligne par (rep, modèle, threads, étape, échéance)
    for rep in range(a.reps):
        for modele in a.modeles:
            run = MODELES[modele]
            for T in a.threads:
                charge = open("/proc/loadavg").read().split()[0]
                for n in ECHEANCES:
                    # le canonique Python doit exister avant py_b / rs_b
                    for etape in ("py_a", "rs_a", "rs_a_tout", "py_b", "rs_b", "rs_b_pur", "rs_c", "rs_c_pur"):
                        m = mesurer(commandes(modele, run, n, T)[etape], env)
                        sortie = m.pop("sortie")
                        ligne = dict(rep=rep, modele=modele, threads=T, etape=etape, echeance=n, charge_1min=charge, **m)
                        if etape.startswith("rs_"):
                            try:
                                ligne["interne"] = json.loads(sortie)
                                for f in ("fichiers",):
                                    for r in ligne["interne"].get("resultats", []) + [ligne["interne"].get("tuilage", {})]:
                                        r.pop(f, None)
                            except ValueError:
                                pass
                        res.append(ligne)
                        print(rep, modele, T, n, etape, "%.2f s mur, %.2f s cpu, %.0f Mo" % (m["mur_s"], m["cpu_s"], m["rss_mo"]), flush=True)
                json.dump(res, open(a.out, "w"), indent=1)

    # synthèse : somme sur les 5 échéances par répétition, puis médiane
    synth = {}
    for modele in a.modeles:
        for T in a.threads:
            for etape in sorted({r["etape"] for r in res}):
                sommes = []
                for rep in range(a.reps):
                    l = [r for r in res if (r["rep"], r["modele"], r["threads"], r["etape"]) == (rep, modele, T, etape)]
                    sommes.append((sum(r["mur_s"] for r in l), sum(r["cpu_s"] for r in l), max(r["rss_mo"] for r in l)))
                synth["%s|T%d|%s" % (modele, T, etape)] = {
                    "mur_s_median": statistics.median(s[0] for s in sommes),
                    "cpu_s_median": statistics.median(s[1] for s in sommes),
                    "pic_mo_median": statistics.median(s[2] for s in sommes),
                    "mur_s_reps": [round(s[0], 2) for s in sommes],
                }
    json.dump({"synthese": synth, "lignes": res}, open(a.out, "w"), indent=1)
    print(json.dumps(synth, indent=1))


if __name__ == "__main__":
    main()
