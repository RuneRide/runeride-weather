# SPDX-License-Identifier: EUPL-1.2
"""Comparaison Python / Rust sur les 5 échéances des deux modèles : champs canoniques et tuiles.

Prérequis : bench.py (ou les commandes de reproduction) a produit out/rust/py/{canon,tiles} et out/rust/rs/{canon,tiles[,-pur]}.
Usage : PYTHONPATH=<pmtiles>:<eccodes>:. python3 rust/tools/comparer_tout.py [--tuiles-dir tiles|tiles-pur] > out/rust/bench/ecarts.json
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import cmp_canon  # noqa: E402
import cmp_tiles  # noqa: E402

MODELES = {"arome-hd": "2026-10-17T03Z", "arome": "2026-10-17T06Z"}
ECHEANCES = [0, 12, 24, 36, 48]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tuiles-dir", default="tiles")
    a = ap.parse_args()
    sortie = {"canon": {}, "tuiles": {}}
    for modele, run in MODELES.items():
        for n in ECHEANCES:
            ref = "out/rust/py/canon/%s/%s/%02d.npz" % (modele, run, n)
            test = "out/rust/rs/canon/%s/%s/%02d.npz" % (modele, run, n)
            c = cmp_canon.comparer(ref, test)
            for k, v in c["variables"].items():
                agg = sortie["canon"].setdefault(modele, {}).setdefault(k, {"echeances": 0, "identiques_bit": 0, "ecart_rel_max": 0.0,
                                                                         "ecart_abs_max": 0.0, "nan_differents": 0})
                agg["echeances"] += 1
                agg["identiques_bit"] += int(v["identiques_bit"])
                agg["ecart_rel_max"] = max(agg["ecart_rel_max"], v["ecart_rel_max"])
                agg["ecart_abs_max"] = max(agg["ecart_abs_max"], v["ecart_abs_max"])
                agg["nan_differents"] += v["nan_differents"]
            t = cmp_tiles.tuiles  # noqa: F841  (module chargé)
            racine_ref, racine_test = "out/rust/py/tiles", "out/rust/rs/" + a.tuiles_dir
            for var in sorted(os.listdir(os.path.join(racine_test, modele, run))):
                p = "%s/%s/%s/%02d.pmtiles" % (modele, run, var, n)
                if not os.path.exists(os.path.join(racine_ref, p)):
                    sortie.setdefault("absents_ref", []).append("%s/%s/%02d" % (modele, var, n))
                    continue
                r = cmp_tiles.comparer(os.path.join(racine_ref, p), os.path.join(racine_test, p))
                g = sortie["tuiles"].setdefault(modele, {}).setdefault(var, {
                    "fichiers": 0, "pixels": 0, "pixels_identiques": 0, "ecart_max_codes": 0, "ecart_max_valeur": 0.0,
                    "tuiles_ref": 0, "tuiles_test": 0, "tuiles_seulement_ref": 0, "tuiles_seulement_test": 0,
                    "metadonnees_identiques": True, "entete_diff": False, "octets_ref": 0, "octets_test": 0,
                    "pixels_nodata_differents": 0, "quant_step": r["quant_step"]})
                g["fichiers"] += 1
                g["pixels"] += r["pixels_comparés"]
                g["pixels_identiques"] += round(r["pixels_comparés"] * r["pct_codes_identiques"] / 100.0) if r["pixels_comparés"] else 0
                g["ecart_max_codes"] = max(g["ecart_max_codes"], r["ecart_max_codes"])
                g["ecart_max_valeur"] = max(g["ecart_max_valeur"], r["ecart_max_valeur_lineaire"])
                for k in ("tuiles_ref", "tuiles_test", "tuiles_seulement_ref", "tuiles_seulement_test", "octets_ref", "octets_test",
                          "pixels_nodata_differents"):
                    g[k] += r[k]
                g["metadonnees_identiques"] &= r["metadonnees_identiques"]
                g["entete_diff"] |= bool(r["entete_diff"])
    for modele in sortie["tuiles"]:
        for var, g in sortie["tuiles"][modele].items():
            if "pixels" in g and g["pixels"]:
                g["pct_pixels_identiques"] = 100.0 * g["pixels_identiques"] / g["pixels"]
                g["rapport_octets_rust_python"] = g["octets_test"] / g["octets_ref"]
    print(json.dumps(sortie, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
