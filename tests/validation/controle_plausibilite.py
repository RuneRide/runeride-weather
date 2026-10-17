# SPDX-License-Identifier: EUPL-1.2
"""Contrôle 4 : plausibilité physique des champs canoniques (échéances 0 à 6), AROME et AROME HD.

Les SEUILS sont des choix de jugement (justifiés dans docs/poc-j1/validation-croisee.md), pas des
valeurs normatives : le script rapporte les extrêmes mesurés ET le nombre de points hors seuil.
Rejouer : python3 tests/validation/controle_plausibilite.py   (numpy seul, pas besoin d'eccodes)
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import commun as c  # noqa: E402

# bornes absolues (violation = erreur certaine d'unité ou de lecture)
BORNES = {"t2m": (200.0, 330.0), "rh2m": (0.0, 100.0), "cloud_total": (0.0, 100.0), "cape": (0.0, 8000.0),
          "mslp": (87000.0, 109000.0), "precip_rate": (0.0, 100.0), "snow_fraction": (0.0, 1.0),
          "gust10": (0.0, 80.0), "wind10_u": (-80.0, 80.0), "wind10_v": (-80.0, 80.0)}
# plages « climatologiques » attendues pour un 17 octobre en Europe de l'Ouest (suspect, pas certain)
PLAGES_SUSPECTES = {"t2m": (255.0, 305.0), "mslp": (96000.0, 105500.0)}
# variations maximales d'une heure à l'autre avant de signaler un point (jugement)
SEUILS_DT = {"t2m": 8.0, "rh2m": 60.0, "mslp": 600.0, "vent_vecteur": 20.0, "gust10": 25.0}


def exemples(champ, masque, lat, lon, k=5, ordre=None, extra=None):
    idx = np.argwhere(masque)
    if len(idx) == 0:
        return []
    if ordre is not None:
        o = np.argsort(-ordre[masque])[:k]
        idx = idx[o]
    else:
        idx = idx[:k]
    out = []
    for i, j in idx:
        d = {"lat": round(float(lat[i]), 3), "lon": round(float(lon[j]), 3), "valeur": float(champ[i, j])}
        if extra:
            d.update({n: float(t[i, j]) for n, t in extra.items()})
        out.append(d)
    return out


def mesurer(modele, echeances=range(0, 7)):
    K = {n: c.canon(modele, n) for n in echeances}
    K = {n: k for n, k in K.items() if k is not None}
    if not K:
        return None
    lat, lon = K[min(K)]["lat"], K[min(K)]["lon"]
    res = {"modele": modele, "echeances_lues": sorted(K), "bornes": {}, "gust_vs_vent": {}, "masque": {}, "temporel": {}}
    # masque : même ensemble de points valides pour toutes les variables / échéances ?
    ref = np.isfinite(K[min(K)]["wind10_u"])
    res["masque"]["points_valides_ref"] = int(ref.sum())
    incoh = {}
    for n, k in K.items():
        for nom, a in k.items():
            if nom in ("lat", "lon") or a.ndim != 2:
                continue
            d = int((np.isfinite(a) != ref).sum())
            if d:
                incoh[f"{nom}@{n}"] = d
    res["masque"]["variables_dont_le_masque_differe"] = incoh
    # bornes par variable et échéance
    for nom, (lo, hi) in BORNES.items():
        r = {}
        for n, k in K.items():
            if nom not in k:
                continue
            a = k[nom]
            f = np.isfinite(a)
            mn, mx = float(a[f].min()), float(a[f].max())
            hors = f & ((a < lo) | (a > hi))
            e = {"min": mn, "max": mx, "moy": float(a[f].mean()), "n_hors_bornes": int(hors.sum()), "n_nan": int((~f).sum())}
            if nom in PLAGES_SUSPECTES:
                l2, h2 = PLAGES_SUSPECTES[nom]
                sus = f & ((a < l2) | (a > h2))
                e["n_hors_plage_suspecte"] = int(sus.sum())
                if sus.any():
                    e["exemples_suspects"] = exemples(a, sus, lat, lon)
            if hors.any():
                e["exemples"] = exemples(a, hors, lat, lon)
            r[n] = e
        res["bornes"][nom] = r
    # rafale contre vent moyen
    for n, k in K.items():
        if "gust10" not in k:
            continue
        vit = np.hypot(k["wind10_u"].astype(np.float64), k["wind10_v"].astype(np.float64))
        g = k["gust10"].astype(np.float64)
        f = np.isfinite(g) & np.isfinite(vit)
        moins = f & (g < vit)
        deficit = (vit - g)
        res["gust_vs_vent"][n] = {
            "n": int(f.sum()), "n_gust_lt_vent": int(moins.sum()), "pct_gust_lt_vent": float(100.0 * moins.sum() / f.sum()),
            "deficit_max_ms": float(deficit[moins].max()) if moins.any() else 0.0,
            "deficit_moy_ms_sur_ces_points": float(deficit[moins].mean()) if moins.any() else 0.0,
            "pct_gust_lt_vent_moins_1ms": float(100.0 * (f & (g < vit - 1.0)).sum() / f.sum()),
            "gust_moy": float(g[f].mean()), "vent_moy": float(vit[f].mean()), "rapport_moyen_gust_sur_vent_vent_ge_3": float((g[f & (vit >= 3)] / vit[f & (vit >= 3)]).mean()),
            "exemples_pires": exemples(g, moins, lat, lon, ordre=deficit, extra={"vent": vit}),
        }
    # cohérence temporelle
    pas = sorted(K)
    for n0, n1 in zip(pas[:-1], pas[1:]):
        a, b = K[n0], K[n1]
        e = {}
        def var(nom, d, seuil):
            f = np.isfinite(d)
            ad = np.abs(d)
            hors = f & (ad > seuil)
            e[nom] = {"max_abs": float(ad[f].max()), "p99_9": float(np.percentile(ad[f], 99.9)), "moy_abs": float(ad[f].mean()),
                      "seuil": seuil, "n_hors_seuil": int(hors.sum())}
            if hors.any():
                e[nom]["exemples"] = exemples(d, hors, lat, lon, ordre=ad)
        var("t2m_K", b["t2m"].astype(np.float64) - a["t2m"], SEUILS_DT["t2m"])
        var("rh2m_pct", b["rh2m"].astype(np.float64) - a["rh2m"], SEUILS_DT["rh2m"])
        if "mslp" in a and "mslp" in b:
            var("mslp_Pa", b["mslp"].astype(np.float64) - a["mslp"], SEUILS_DT["mslp"])
        dv = np.hypot(b["wind10_u"].astype(np.float64) - a["wind10_u"], b["wind10_v"].astype(np.float64) - a["wind10_v"])
        var("vent_vecteur_ms", dv, SEUILS_DT["vent_vecteur"])
        if "gust10" in a and "gust10" in b:
            var("gust10_ms", b["gust10"].astype(np.float64) - a["gust10"], SEUILS_DT["gust10"])
        res["temporel"][f"{n0}->{n1}"] = e
    return res


def hr_depuis_td(modele_paquet_sp2=None, echeances=range(0, 7)):
    """Contrôle indépendant : humidité relative recalculée (Magnus) à partir de 2t et 2d (GRIB SP2/SP1) contre 2r."""
    sp1, sp2 = c.grib_arome("SP1"), c.grib_arome("SP2")
    if sp1 is None or sp2 is None or c.eccodes is None:
        return None
    A, _, _ = c.lire_grib(sp1, {"2t", "2r"})
    B, _, _ = c.lire_grib(sp2, {"2d"})
    res = {}
    for n in echeances:
        t = c.champ(A, "2t", n).astype(np.float64) - 273.15
        td = c.champ(B, "2d", n).astype(np.float64) - 273.15
        r = c.champ(A, "2r", n).astype(np.float64)
        f = np.isfinite(t) & np.isfinite(td) & np.isfinite(r)
        a, b = 17.625, 243.04   # Magnus (Alduchov-Eskridge), sur l'eau
        rh = 100.0 * np.exp(a * td / (b + td)) / np.exp(a * t / (b + t))
        d = (rh - r)[f]
        res[n] = {"n": int(f.sum()), "ecart_moy_pct": float(d.mean()), "ecart_abs_moy_pct": float(np.abs(d).mean()),
                  "ecart_abs_max_pct": float(np.abs(d).max()), "p99_abs_pct": float(np.percentile(np.abs(d), 99)),
                  "td_sup_t_n": int((td[f] > t[f] + 0.01).sum()), "corr": float(np.corrcoef(rh[f], r[f])[0, 1])}
    return res


if __name__ == "__main__":
    import json
    out = {}
    for m in ("arome", "arome-hd"):
        r = mesurer(m)
        if r is not None:
            out[m] = r
    h = hr_depuis_td()
    if h is not None:
        out["hr_magnus_vs_2r_arome"] = h
    c.ecrire_json("plausibilite.json", out)
    print(json.dumps(out, indent=1)[:3000])
