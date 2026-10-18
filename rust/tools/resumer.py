# SPDX-License-Identifier: EUPL-1.2
"""Tableaux Markdown à partir de out/rust/bench/resultats.json (sortie de bench.py).

Somme sur les 5 échéances mesurées (médiane des répétitions) et extrapolation à 52 échéances
(CALCULÉE : échéance 0 + 51 × moyenne des échéances 12, 24, 36, 48).
Usage : python3 rust/tools/resumer.py out/rust/bench/resultats.json
"""
import json
import statistics
import sys

ETAPES = {
    "a": {"Python": "py_a", "Rust": "rs_a", "Rust (décode tout)": "rs_a_tout"},
    "b": {"Python": "py_b", "Rust (libwebp)": "rs_b", "Rust (image-webp)": "rs_b_pur"},
}
ECH = [0, 12, 24, 36, 48]


def med(l):
    return statistics.median(l)


def main(chemin):
    d = json.load(open(chemin))["lignes"]
    reps = sorted({r["rep"] for r in d})

    def valeurs(modele, T, etape, n, cle):
        return [r[cle] for r in d if (r["modele"], r["threads"], r["etape"], r["echeance"]) == (modele, T, etape, n)]

    def somme5(modele, T, etapes, cle):
        """Médiane sur les répétitions de la somme sur les 5 échéances (étapes additionnées)."""
        tot = []
        for rep in reps:
            s = 0.0
            for e in etapes:
                s += sum(r[cle] for r in d if (r["rep"], r["modele"], r["threads"], r["etape"]) == (rep, modele, T, e))
            tot.append(s)
        return med(tot)

    def pic(modele, T, etapes):
        return max(med([r["rss_mo"] for r in d if (r["modele"], r["threads"], r["etape"], r["echeance"]) == (modele, T, e, n)])
                   for e in etapes for n in ECH)

    def extrapole(modele, T, etapes, cle):
        par = {n: sum(med(valeurs(modele, T, e, n, cle)) for e in etapes) for n in ECH}
        return par[0] + 51 * statistics.mean(par[n] for n in ECH[1:])

    out = {}
    lignes = []
    for modele in ("arome-hd", "arome"):
        for T in (1, 4):
            blocs = [
                ("(a) GRIB -> canonique", {"Python": ["py_a"], "Rust": ["rs_a"], "Rust, décode tout (comme Python)": ["rs_a_tout"]}),
                ("(b) tuilage", {"Python": ["py_b"], "Rust libwebp": ["rs_b"], "Rust image-webp": ["rs_b_pur"]}),
                ("(c) total", {"Python (a)+(b)": ["py_a", "py_b"], "Rust (a)+(b) via npz": ["rs_a", "rs_b"],
                               "Rust en mémoire (libwebp)": ["rs_c"], "Rust en mémoire (image-webp)": ["rs_c_pur"]}),
            ]
            for titre, variantes in blocs:
                for nom, etapes in variantes.items():
                    k = (modele, T, titre, nom)
                    out[k] = {
                        "mur_5": somme5(modele, T, etapes, "mur_s"), "cpu_5": somme5(modele, T, etapes, "cpu_s"),
                        "pic_mo": pic(modele, T, etapes),
                        "mur_52": extrapole(modele, T, etapes, "mur_s"), "cpu_52": extrapole(modele, T, etapes, "cpu_s"),
                    }
    for modele in ("arome-hd", "arome"):
        for T in (1, 4):
            print("\n### %s, %d thread(s)\n" % (modele, T))
            print("| Étape | Variante | Mur 5 éch. (s) | CPU 5 éch. (s) | Pic mém. (Mo) | Mur 52 éch. (s, calculé) | CPU 52 éch. (s, calculé) |")
            print("|---|---|---:|---:|---:|---:|---:|")
            for (m, t, titre, nom), v in out.items():
                if (m, t) == (modele, T):
                    print("| %s | %s | %.1f | %.1f | %.0f | %.0f | %.0f |" % (titre, nom, v["mur_5"], v["cpu_5"], v["pic_mo"], v["mur_52"], v["cpu_52"]))

    # détail interne de Rust (médianes par échéance, moyenne sur les 5 échéances)
    print("\n### Détail interne Rust (secondes, médiane des répétitions, somme des 5 échéances)\n")
    print("| Modèle | Threads | lecture GRIB | décodage AEC | calcul canonique | écriture npz | rééchantillonnage + WebP (libwebp, `run`) | rééch. + WebP (image-webp) | assemblage PMTiles |")
    print("|---|---|---:|---:|---:|---:|---:|---:|---:|")
    for modele in ("arome-hd", "arome"):
        for T in (1, 4):
            def interne(etape, chemin_cles):
                tot = []
                for rep in reps:
                    s = 0.0
                    for r in d:
                        if (r["rep"], r["modele"], r["threads"], r["etape"]) == (rep, modele, T, etape):
                            x = r["interne"]
                            for c in chemin_cles:
                                x = x.get(c, {}) if isinstance(x, dict) else {}
                            s += x if isinstance(x, (int, float)) else 0.0
                    tot.append(s)
                return med(tot)
            print("| %s | %d | %.2f | %.2f | %.2f | %.2f | %.2f | %.2f | %.2f |" % (
                modele, T, interne("rs_a", ["ingestion", "lecture_fichiers_s"]), interne("rs_a", ["ingestion", "decodage_grib_s"]),
                interne("rs_a", ["ingestion", "calcul_canonique_s"]), interne("rs_a", ["ingestion", "ecriture_npz_s"]),
                interne("rs_c", ["tuilage", "dont_rechantillonnage_encodage_s"]),
                interne("rs_c_pur", ["tuilage", "dont_rechantillonnage_encodage_s"]),
                interne("rs_c", ["tuilage", "dont_assemblage_pmtiles_s"])))
    json.dump({"|".join(map(str, k)): v for k, v in out.items()}, open(chemin.replace(".json", "-synthese.json"), "w"), indent=1)


if __name__ == "__main__":
    main(sys.argv[1])
