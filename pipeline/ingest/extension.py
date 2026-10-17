# SPDX-License-Identifier: EUPL-1.2
"""Contrôles de l'extension 0-51 h : plages, trous, sauts, raccords de groupes 0,025°, cumuls, neige.

Usage : python3 -m pipeline.ingest.extension --src <dossiers GRIB> --canon out/canon --out out/canon/extension_0-51.json
"""
import argparse
import json
import os
import sys

import numpy as np

from . import canon, download, grib
from .validations import RUN, RUN_DIR, stats_comparaison

# plages physiques plausibles (bornes larges ; hors plage = anomalie signalée)
PLAGES = {
    "wind10_u": (-80, 80), "wind10_v": (-80, 80), "gust10": (0, 100), "t2m": (210, 330), "rh2m": (0, 100),
    "precip_rate": (0, 300), "snow_fraction": (0, 1), "cloud_total": (0, 100), "cape": (0, 10000), "mslp": (87000, 108500),
}
SEUIL_SAUT = {"t2m": 15.0, "wind10_u": 40.0, "wind10_v": 40.0, "gust10": 40.0, "rh2m": 80.0, "mslp": 1500.0}


NMAX = 51   # dernière échéance traitée (modifiable : --echeances-max)


def chemin_canon(canon_dir, modele, n):
    return os.path.join(canon_dir, modele, RUN_DIR, f"{n:02d}.npz")


def plages_et_sauts(modele, canon_dir, echeances=None):
    """Par variable : plage globale, NaN %, moyenne par échéance, plus grand saut entre échéances successives."""
    echeances = range(NMAX + 1) if echeances is None else echeances
    var = {}
    prev, noms_manquants, trous = {}, [], []
    for n in echeances:
        p = chemin_canon(canon_dir, modele, n)
        if not os.path.exists(p):
            trous.append(n)
            continue
        d = np.load(p)
        for k in d.files:
            if k in ("lat", "lon", "meta"):
                continue
            a = d[k]
            ok = ~np.isnan(a)
            v = var.setdefault(k, {"echeances": [], "min": [], "max": [], "nan_pct": [], "moyenne": [], "saut_max": {}, "saut_p999": {}})
            v["echeances"].append(n)
            v["min"].append(float(a[ok].min()))
            v["max"].append(float(a[ok].max()))
            v["nan_pct"].append(float(100 * (1 - ok.mean())))
            v["moyenne"].append(float(a[ok].mean()))
            if k in prev and prev[k][0] == n - 1:
                both = ok & ~np.isnan(prev[k][1])
                dd = np.abs(a[both].astype(np.float64) - prev[k][1][both])
                v["saut_max"][n] = float(dd.max())
                v["saut_p999"][n] = float(np.percentile(dd, 99.9))
            prev[k] = (n, a)
    res = {"echeances_absentes": trous, "variables": {}}
    for k, v in var.items():
        sm = v["saut_max"]
        pire = max(sm, key=sm.get) if sm else None
        mm = np.abs(np.diff(v["moyenne"]))
        lo, hi = PLAGES.get(k, (-np.inf, np.inf))
        res["variables"][k] = {
            "nb_echeances": len(v["echeances"]),
            "min": min(v["min"]), "max": max(v["max"]),
            "echeance_du_max": v["echeances"][int(np.argmax(v["max"]))],
            "nan_pct_min": min(v["nan_pct"]), "nan_pct_max": max(v["nan_pct"]),
            "echeances_nan_pct_different": [n for n, x in zip(v["echeances"], v["nan_pct"]) if abs(x - v["nan_pct"][-1]) > 1e-6],
            "moyenne_min": min(v["moyenne"]), "moyenne_max": max(v["moyenne"]),
            "plus_grand_saut_moyenne_entre_echeances": float(mm.max()) if len(mm) else None,
            "plus_grand_saut_local": {"valeur": sm[pire], "echeance": pire} if pire is not None else None,
            "p999_des_sauts_locaux_max_sur_echeances": max(v["saut_p999"].values()) if v["saut_p999"] else None,
            "hors_plage_physique": bool(min(v["min"]) < lo or max(v["max"]) > hi),
            "plage_physique_testee": [lo, hi] if k in PLAGES else None,
            "saut_local_au_dessus_du_seuil": ({str(n): x for n, x in sm.items() if x > SEUIL_SAUT[k]} if k in SEUIL_SAUT else None),
        }
    return res


def lire_cumuls(modele, dossiers, noms, echeances=None):
    """{échéance: {nom: tableau}} pour toute la plage, tous groupes confondus (SP1 + SP2)."""
    out = {}
    echeances = range(NMAX + 1) if echeances is None else echeances
    labels = sorted({download.etiquette(modele, n) for n in echeances}, key=lambda s: download.bornes_groupe(s)[0])
    for lab in labels:
        for packet in ("SP1", "SP2"):
            c = download.localiser(modele, packet, lab, RUN, dossiers)
            msgs, _, _ = grib.lire_fichier(c, lambda m: m["shortName"] in noms)
            for n, ch in canon.indexer(msgs).items():
                out.setdefault(n, {}).update(ch)
    return out


def debit_depuis(cumul_n, cumul_p):
    return np.maximum(cumul_n - cumul_p, np.float32(0.0))


def raccords_et_cumuls(modele, dossiers, echeances=None):
    """Continuité du débit aux raccords, somme des débits = cumul sur 0-51 h, recoupement tp (AROME)."""
    noms = {"tirf", "tsnowp", "tgrp", "tp"}
    echeances = range(1, NMAX + 1) if echeances is None else echeances
    c = lire_cumuls(modele, dossiers, noms, range(NMAX + 1))
    res = {"par_echeance": {}, "raccords": {}}
    tot = lambda ch: ch["tirf"] + ch["tsnowp"] + ch["tgrp"]
    somme = None
    somme_tp = None
    for n in echeances:
        t_n = tot(c[n])
        t_p = tot(c[n - 1]) if n > 1 else np.float32(0.0)
        r = debit_depuis(t_n, t_p)
        somme = r.astype(np.float64) if somme is None else somme + r
        ok = ~np.isnan(r)
        d = somme - t_n
        e = {"debit_moyen": float(r[ok].mean()), "debit_max": float(r[ok].max()),
             "debit_p999": float(np.percentile(r[ok], 99.9)),
             "ecart_somme_vs_cumul_max": float(np.nanmax(np.abs(d))), "ecart_somme_vs_cumul_moyen": float(np.nanmean(np.abs(d))),
             "cumul_total_max": float(np.nanmax(t_n))}
        if modele == "arome":
            tp_n = c[n]["tp"]
            tp_p = c[n - 1]["tp"] if n > 1 else np.float32(0.0)
            r2 = debit_depuis(tp_n, tp_p)
            somme_tp = r2.astype(np.float64) if somme_tp is None else somme_tp + r2
            s = stats_comparaison(r, r2)
            e["tp_vs_tirf+tsnowp+tgrp"] = {"correlation": s["correlation"], "biais": s["biais_moyen"], "rmse": s["rmse"],
                                           "ecart_max": s["ecart_abs_max"], "debit_moyen_tp": float(np.nanmean(r2)),
                                           "ecart_somme_tp_vs_cumul_tp_max": float(np.nanmax(np.abs(somme_tp - tp_n)))}
            e["ecart_cumul_tp_vs_total_max"] = float(np.nanmax(np.abs(tp_n - t_n)))
        res["par_echeance"][n] = e
    if modele == "arome":
        # raccords : n = 7, 13, ..., 49. Le débit à n est calculé avec n−1 du groupe précédent.
        for n in (7, 13, 19, 25, 31, 37, 43, 49):
            ref = [res["par_echeance"][k] for k in (n - 1, n, n + 1) if k in res["par_echeance"]]
            # contre-épreuve : ce que donnerait un cumul précédent oublié (0) — débit = cumul entier depuis 0
            faux = debit_depuis(tot(c[n]), np.float32(0.0))
            vrai = debit_depuis(tot(c[n]), tot(c[n - 1]))
            sg = lambda ch: ch["tirf"]
            res["raccords"][n] = {
                "debit_moyen_n-1_n_n+1": [x["debit_moyen"] for x in ref],
                "debit_max_n-1_n_n+1": [x["debit_max"] for x in ref],
                "cumul_n_moins_cumul_n-1_min": float(np.nanmin(tot(c[n]) - tot(c[n - 1]))),
                "debit_moyen_si_cumul_precedent_oublie": float(np.nanmean(faux)),
                "debit_moyen_correct": float(np.nanmean(vrai)),
                "fenetre_cumul_tirf": "0->n (contrôlée à la lecture)",
            }
    # synthèse
    pe = res["par_echeance"]
    res["synthese"] = {
        "ecart_somme_vs_cumul_max_0_51": max(x["ecart_somme_vs_cumul_max"] for x in pe.values()),
        "ecart_somme_vs_cumul_moyen_a_51": pe[51]["ecart_somme_vs_cumul_moyen"] if 51 in pe else None,
        "cumul_total_max_a_51": pe[51]["cumul_total_max"] if 51 in pe else None,
        "echeances_dans_dernier_groupe": sorted(n for n in c if n >= 49) if modele == "arome" else None,
    }
    return res


BACS_T = [(-99, -2), (-2, 0), (0, 2), (2, 4), (4, 6), (6, 99)]   # °C
ZONES = {"Alpes": (43.5, 47.5, 5.5, 11.0), "Pyrénées": (42.0, 43.2, -2.0, 3.2)}


def neige(modele, dossiers, canon_dir, echeances=None, seuil_mm=0.05):
    """Cohérence de snow_fraction avec t2m et le lieu, sur les points où il pleut/neige (débit > seuil_mm mm/h)."""
    echeances = range(1, NMAX + 1) if echeances is None else echeances
    n_pts = np.zeros(len(BACS_T))
    somme_sf = np.zeros(len(BACS_T))
    sf_sup_05 = np.zeros(len(BACS_T))
    hors = {"sf_hors_0_1": 0, "sf_nan_avec_debit": 0}
    zones = {z: 0 for z in ZONES} | {"ailleurs": 0}
    echant, par_ech = [], {}
    for n in echeances:
        d = np.load(chemin_canon(canon_dir, modele, n))
        if "snow_fraction" not in d.files:
            continue
        sf, pr, t = d["snow_fraction"], d["precip_rate"], d["t2m"] - np.float32(273.15)
        ok = ~np.isnan(sf) & ~np.isnan(pr) & (pr > seuil_mm)
        hors["sf_hors_0_1"] += int(((sf < 0) | (sf > 1)).sum())
        hors["sf_nan_avec_debit"] += int((np.isnan(sf) & ~np.isnan(pr) & (pr > seuil_mm)).sum())
        for b, (lo, hi) in enumerate(BACS_T):
            m = ok & (t > lo) & (t <= hi)
            n_pts[b] += m.sum()
            somme_sf[b] += sf[m].sum()
            sf_sup_05[b] += (sf[m] > 0.5).sum()
        m = ok & (sf > 0.5)
        par_ech[n] = int(m.sum())
        ii, jj = np.where(m)
        la, lo_ = d["lat"][ii], d["lon"][jj]
        rest = np.ones(len(ii), bool)
        for z, (a, b_, c_, e_) in ZONES.items():
            mz = (la >= a) & (la <= b_) & (lo_ >= c_) & (lo_ <= e_)
            zones[z] += int(mz.sum())
            rest &= ~mz
        zones["ailleurs"] += int(rest.sum())
    cumul = lire_cumuls(modele, dossiers, {"tsnowp"}, [echeances[-1]])[echeances[-1]]["tsnowp"]
    i = np.unravel_index(np.nanargmax(cumul), cumul.shape)
    d = np.load(chemin_canon(canon_dir, modele, echeances[-1]))
    return {
        "par_tranche_t2m_degC": [{"tranche": [lo, hi], "nb_points_precipitants": int(n_pts[b]),
                                  "snow_fraction_moyenne": float(somme_sf[b] / n_pts[b]) if n_pts[b] else None,
                                  "part_sf_sup_0_5": float(sf_sup_05[b] / n_pts[b]) if n_pts[b] else None}
                                 for b, (lo, hi) in enumerate(BACS_T)],
        "hors_plage": hors,
        "points_sf_sup_0_5_par_zone_cumules_sur_echeances": zones,
        "points_sf_sup_0_5_par_echeance": par_ech,
        "cumul_tsnowp_max_mm_a_derniere_echeance": {"valeur": float(cumul[i]), "lat": float(d["lat"][i[0]]), "lon": float(d["lon"][i[1]])},
    }


def main(argv=None):
    global RUN, RUN_DIR, NMAX
    p = argparse.ArgumentParser(prog="python3 -m pipeline.ingest.extension")
    p.add_argument("--src", nargs="+", required=True)
    p.add_argument("--canon", default="out/canon")
    p.add_argument("--out", default="out/canon/extension_0-51.json")
    p.add_argument("--modeles", nargs="+", default=["arome", "arome-hd"])
    p.add_argument("--run", default=RUN, help="tirage, ex. 2026-10-17T03:00:00Z")
    p.add_argument("--echeances-max", type=int, default=51)
    a = p.parse_args(argv)
    RUN = a.run
    NMAX = a.echeances_max
    RUN_DIR = RUN[:13].replace(":", "") + "Z"
    res = {}
    for m in a.modeles:
        print("...", m, file=sys.stderr)
        res[m] = {"plages": plages_et_sauts(m, a.canon), "cumuls": raccords_et_cumuls(m, a.src), "neige": neige(m, a.src, a.canon)}
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1, ensure_ascii=False, default=lambda o: int(o) if isinstance(o, np.integer) else str(o))
    return 0


if __name__ == "__main__":
    sys.exit(main())
