# SPDX-License-Identifier: EUPL-1.2
"""Validations scientifiques de l'ingestion (docs/poc-j1/ingestion.md §5).

Usage : python3 -m pipeline.ingest.validations --src <dossiers GRIB> --canon out/canon --out out/canon/validations.json
Tout est mesuré sur les fichiers réels ; rien n'est déduit du sens des paramètres `unknown`.
"""
import argparse
import glob
import json
import os
import sys

import numpy as np

from . import canon, download, grib

RUN = "2026-10-17T06:00:00Z"
RUN_DIR = "2026-10-17T06Z"


# ---------------------------------------------------------------- outils de comparaison

def stats_comparaison(a, b):
    """Corrélation, biais (a−b), RMSE, écart max, sur les points valides communs."""
    ok = ~np.isnan(a) & ~np.isnan(b)
    x, y = a[ok].astype(np.float64), b[ok].astype(np.float64)
    d = x - y
    return {
        "n": int(ok.sum()),
        "correlation": float(np.corrcoef(x, y)[0, 1]),
        "biais_moyen": float(d.mean()),
        "rmse": float(np.sqrt((d ** 2).mean())),
        "ecart_abs_max": float(np.abs(d).max()),
        "ecart_abs_moyen": float(np.abs(d).mean()),
        "moyenne_a": float(x.mean()), "moyenne_b": float(y.mean()),
    }


def _poids(axe_src, axe_dst):
    """Indices i0 et poids w pour interpoler linéairement l'axe `axe_src` aux valeurs `axe_dst`."""
    n = len(axe_src)
    if axe_src[0] > axe_src[-1]:   # décroissant : on travaille sur l'indice fractionnaire
        fi = np.interp(axe_dst, axe_src[::-1], np.arange(n)[::-1])
    else:
        fi = np.interp(axe_dst, axe_src, np.arange(n))
    i0 = np.clip(np.floor(fi).astype(int), 0, n - 2)
    return i0, (fi - i0).astype(np.float32)


def bilineaire(champ, lat_s, lon_s, lat_d, lon_d):
    """Échantillonne `champ` (grille s) aux nœuds de la grille d ; NaN si un des 4 voisins est NaN."""
    i0, wi = _poids(lat_s, lat_d)
    j0, wj = _poids(lon_s, lon_d)
    out = 0.0
    for di, pi in ((0, 1 - wi), (1, wi)):
        for dj, pj in ((0, 1 - wj), (1, wj)):
            sous = champ[np.ix_(i0 + di, j0 + dj)]
            out = out + sous * (pi[:, None] * pj[None, :])
    return out.astype(np.float32)


def lisser_3x3(champ):
    """Moyenne 3×3 (les bords gardent NaN) ; NaN si un voisin est NaN (cohérent avec le contrat)."""
    s = np.zeros_like(champ[1:-1, 1:-1], dtype=np.float64)
    for di in range(3):
        for dj in range(3):
            s += champ[di:champ.shape[0] - 2 + di, dj:champ.shape[1] - 2 + dj]
    out = np.full(champ.shape, np.nan, dtype=np.float32)
    out[1:-1, 1:-1] = (s / 9.0).astype(np.float32)
    return out


def charger_canon(modele, echeance, canon_dir):
    return np.load(os.path.join(canon_dir, modele, RUN_DIR, f"{echeance:02d}.npz"))


# ---------------------------------------------------------------- (d) fenêtres

def fenetres(dossiers):
    """Inventaire des (shortName, stepType, startStep, endStep, step) pour accum / max / min, par modèle."""
    import eccodes
    res = {}
    for modele, motif in (("arome-hd", "arome__001__SP[12]__*"), ("arome", "arome__0025__SP[12]__*")):
        vus, fichiers = {}, set()
        for d in dossiers:
            for f in glob.glob(os.path.join(d, "**", motif + ".grib2"), recursive=True):
                fichiers.add(f)
        for f in sorted(fichiers):
            with open(f, "rb") as fh:
                while True:
                    h = eccodes.codes_grib_new_from_file(fh)
                    if h is None:
                        break
                    g = lambda k: eccodes.codes_get(h, k) if eccodes.codes_is_defined(h, k) else None
                    st = g("stepType")
                    if st in ("accum", "max", "min"):
                        cle = (g("shortName"), st, g("typeOfStatisticalProcessing"), g("lengthOfTimeRange"))
                        vus.setdefault(cle, set()).add((g("startStep"), g("endStep"), g("step")))
                    eccodes.codes_release(h)
        res[modele] = [{
            "nom": k[0], "stepType": k[1], "typeOfStatisticalProcessing": k[2], "lengthOfTimeRange_h": k[3],
            "fenetres_start_end": sorted({(a, b) for a, b, _ in v}),
            "regle_cumul_0_a_n": all(a == 0 and b == s for a, b, s in v) if k[1] == "accum" else None,
            "regle_derniere_heure": all(a == s - 1 and b == s for a, b, s in v) if k[1] in ("max", "min") else None,
        } for k, v in sorted(vus.items())]
    return res


# ---------------------------------------------------------------- (a) rafales

def lire_gribs(modele, dossiers, echeances, noms):
    """Lit des champs bruts {échéance: {nom: tableau}} + axes pour les paquets SP1/SP2."""
    out, axes = {}, None
    labels = sorted({download.etiquette(modele, n) for n in echeances})
    for lab in labels:
        for packet in ("SP1", "SP2"):
            chemin = download.localiser(modele, packet, lab, RUN, dossiers)
            msgs, lat, lon = grib.lire_fichier(chemin, lambda m: m["shortName"] in noms)
            axes = axes or (lat, lon)
            for n, ch in canon.indexer(msgs).items():
                if n in echeances:
                    out.setdefault(n, {}).update(ch)
    return out, axes


def rafales(dossiers, canon_dir, echeances=range(1, 7)):
    """Hypothèse gust10 HD = hypot(max_10efg, max_10nfg) contre max_i10fg d'AROME 0,025°."""
    ar, (lat_a, lon_a) = lire_gribs("arome", dossiers, set(echeances), {"max_i10fg", "max_10efg", "max_10nfg", "10u", "10v"})
    res = {"par_echeance": {}}
    for n in echeances:
        hd = charger_canon("arome-hd", n, canon_dir)
        g_hd = hd["gust10"]
        g_ar = ar[n]["max_i10fg"]
        h_ar = np.hypot(ar[n]["max_10efg"].astype(np.float64), ar[n]["max_10nfg"].astype(np.float64)).astype(np.float32)
        ponct = bilineaire(g_hd, hd["lat"], hd["lon"], lat_a, lon_a)
        lisse = bilineaire(lisser_3x3(g_hd), hd["lat"], hd["lon"], lat_a, lon_a)
        e, nn = ar[n]["max_10efg"], ar[n]["max_10nfg"]
        ok = ~np.isnan(e)
        vit = np.hypot(ar[n]["10u"].astype(np.float64), ar[n]["10v"].astype(np.float64))
        res["par_echeance"][n] = {
            "HD_ponctuel_vs_AROME_max_i10fg": stats_comparaison(ponct, g_ar),
            "HD_lisse3x3_vs_AROME_max_i10fg": stats_comparaison(lisse, g_ar),
            "AROME_hypot_composantes_vs_max_i10fg": stats_comparaison(h_ar, g_ar),
            "AROME_part_composante_est_negative": float((e[ok] < 0).mean()),
            "AROME_part_composante_nord_negative": float((nn[ok] < 0).mean()),
            "AROME_part_hypot_inferieur_a_max_i10fg": float((h_ar[ok] < g_ar[ok] - 1e-3).mean()),
            "AROME_part_rafale_inferieure_au_vent_moyen_instantane": float((g_ar[ok] < vit[ok] - 1e-3).mean()),
            "HD_part_rafale_inferieure_au_vent_moyen_instantane": float(
                np.nanmean(g_hd < np.hypot(hd["wind10_u"].astype(np.float64), hd["wind10_v"].astype(np.float64)) - 1e-3)),
        }
    # tendance globale : concaténation par échéances (moyennes des statistiques)
    cles = ["HD_ponctuel_vs_AROME_max_i10fg", "HD_lisse3x3_vs_AROME_max_i10fg", "AROME_hypot_composantes_vs_max_i10fg"]
    res["moyenne_sur_echeances"] = {c: {k: float(np.mean([res["par_echeance"][n][c][k] for n in echeances]))
                                        for k in ("correlation", "biais_moyen", "rmse")}
                                    | {"ecart_abs_max": float(max(res["par_echeance"][n][c]["ecart_abs_max"] for n in echeances))}
                                    for c in cles}
    return res


# ---------------------------------------------------------------- (b) cumuls

def cumuls(dossiers, canon_dir, echeances=range(0, 7)):
    out = {}
    for modele in ("arome-hd", "arome"):
        noms = {"tirf", "tsnowp", "tgrp", "tp"}
        champs, _ = lire_gribs(modele, dossiers, set(echeances), noms)
        rep = {"monotonie": {}, "somme_debits_vs_cumul": {}}
        for nom in sorted(noms):
            if nom not in champs.get(6, {}):
                continue
            neg_pires, nb_neg = [], 0
            prev = np.zeros_like(champs[1][nom])
            for n in range(1, 7):
                d = champs[n][nom].astype(np.float64) - prev
                ok = ~np.isnan(d)
                neg_pires.append(float(d[ok].min()))
                nb_neg += int((d[ok] < -1e-6).sum())
                prev = champs[n][nom]
            rep["monotonie"][nom] = {"plus_petite_difference_mm": min(neg_pires), "nb_points_decroissants_(<-1e-6)": nb_neg,
                                     "cumul_max_a_6h_mm": float(np.nanmax(champs[6][nom])), "unite": "kg m-2 = mm"}
        # somme des débits canoniques (clippés à 0) contre cumul total à n
        somme = None
        for n in range(1, 7):
            r = charger_canon(modele, n, canon_dir)["precip_rate"].astype(np.float64)
            somme = r if somme is None else somme + r
            tot = (champs[n]["tirf"] + champs[n]["tsnowp"] + champs[n]["tgrp"]).astype(np.float64)
            d = somme - tot
            ok = ~np.isnan(d)
            rep["somme_debits_vs_cumul"][n] = {"ecart_abs_max_mm": float(np.abs(d[ok]).max()),
                                               "ecart_abs_moyen_mm": float(np.abs(d[ok]).mean()),
                                               "cumul_total_max_mm": float(tot[ok].max())}
        if modele == "arome":
            ecarts = {}
            for n in (1, 3, 6):
                tot = (champs[n]["tirf"] + champs[n]["tsnowp"] + champs[n]["tgrp"]).astype(np.float64)
                ecarts[n] = stats_comparaison(tot.astype(np.float32), champs[n]["tp"])
            rep["tirf_tsnowp_tgrp_vs_tp"] = ecarts
        out[modele] = rep
    return out


# ---------------------------------------------------------------- (c) nébulosité

def nebulosite(dossiers, canon_dir, echeances=range(1, 7)):
    out = {}
    for modele in ("arome-hd", "arome"):
        champs, _ = lire_gribs(modele, dossiers, set(echeances), {"lcc", "mcc", "hcc"})
        rep = {}
        for n in echeances:
            c = charger_canon(modele, n, canon_dir)["cloud_total"]
            l, m, h = champs[n]["lcc"], champs[n]["mcc"], champs[n]["hcc"]
            ok = ~np.isnan(c)
            borne_bas = np.maximum(np.maximum(l, m), h)   # le total ne peut pas être inférieur à la couche la plus nuageuse
            somme = np.minimum(l + m + h, 100.0)            # ... ni supérieur à la somme (recouvrement maximal)
            rep[n] = {
                "min": float(c[ok].min()), "max": float(c[ok].max()), "moyenne": float(c[ok].mean()),
                "part_plus_de_100": float((c[ok] > 100).mean()),
                "lcc_mcc_hcc_max": [float(np.nanmax(l)), float(np.nanmax(m)), float(np.nanmax(h))],
                "lcc_mcc_hcc_min": [float(np.nanmin(l)), float(np.nanmin(m)), float(np.nanmin(h))],
                "moyennes_lcc_mcc_hcc": [float(np.nanmean(l)), float(np.nanmean(m)), float(np.nanmean(h))],
                "part_total_sous_max_des_couches": float((c[ok] < borne_bas[ok] - 1e-3).mean()),
                "part_total_sur_somme_bornee": float((c[ok] > somme[ok] + 1e-3).mean()),
                "part_ciel_clair_(<5%)": float((c[ok] < 5).mean()),
                "part_ciel_couvert_(>95%)": float((c[ok] > 95).mean()),
                "histogramme_10_classes": np.histogram(np.clip(c[ok], 0, 100), bins=10, range=(0, 100))[0].tolist(),
            }
        out[modele] = rep
    # Recoupement numérique (sans interpréter le paramètre) : le champ `unknown` (0, 6, 1) de SP1 0,025°
    # a-t-il les mêmes valeurs que notre cloud_total ?
    chemin = download.localiser("arome", "SP1", "00H06H", RUN, dossiers)
    msgs, _, _ = grib.lire_fichier(chemin, lambda m: (m["discipline"], m["parameterCategory"], m["parameterNumber"]) == (0, 6, 1))
    rec = {}
    for m in msgs:
        n = m["step"]
        c = charger_canon("arome", n, canon_dir)["cloud_total"]
        rec[n] = stats_comparaison(c, m["valeurs"])
        rec[n]["identique_bit_a_bit"] = bool(np.array_equal(c, m["valeurs"], equal_nan=True))
    out["arome_recoupement_inconnu_0_6_1_vs_cloud_total"] = rec
    return out


# ---------------------------------------------------------------- (e) HD contre AROME

def hd_contre_arome(canon_dir, echeances=range(0, 7)):
    res = {}
    for n in echeances:
        hd, ar = charger_canon("arome-hd", n, canon_dir), charger_canon("arome", n, canon_dir)
        lat_a, lon_a = ar["lat"], ar["lon"]
        r = {}
        for nom, fn in (("vent_u", lambda d: d["wind10_u"]), ("vent_v", lambda d: d["wind10_v"]),
                        ("vitesse_vent", lambda d: np.hypot(d["wind10_u"].astype(np.float64), d["wind10_v"].astype(np.float64)).astype(np.float32)),
                        ("t2m", lambda d: d["t2m"]), ("rh2m", lambda d: d["rh2m"])):
            hd_f = fn(hd)
            r[nom] = {"ponctuel": stats_comparaison(bilineaire(hd_f, hd["lat"], hd["lon"], lat_a, lon_a), fn(ar)),
                      "lisse3x3": stats_comparaison(bilineaire(lisser_3x3(hd_f), hd["lat"], hd["lon"], lat_a, lon_a), fn(ar))}
        res[n] = r
    return res


def arome_interpole_vers_hd(canon_dir, echeances=(1, 3, 6)):
    """Sens inverse : AROME 0,025° interpolé bilinéairement sur la grille HD contre HD. Mesure le détail propre à HD.

    Les points HD dont les 4 voisins AROME existent sont comparés ; on sépare les points situés exactement
    sur un nœud AROME (écart attendu nul si HD en dérive) des autres.
    """
    res = {}
    for n in echeances:
        hd, ar = charger_canon("arome-hd", n, canon_dir), charger_canon("arome", n, canon_dir)
        r = {}
        for nom, fn in (("vitesse_vent", lambda d: np.hypot(d["wind10_u"].astype(np.float64), d["wind10_v"].astype(np.float64)).astype(np.float32)),
                        ("t2m", lambda d: d["t2m"]), ("gust10", lambda d: d["gust10"])):
            interp = bilineaire(fn(ar), ar["lat"], ar["lon"], hd["lat"], hd["lon"])
            r[nom] = stats_comparaison(fn(hd), interp)
            # nœuds AROME : 1 point HD sur 5 en latitude et en longitude coïncide avec un nœud AROME (0,025 = 2,5 × 0,01),
            # on prend les indices pairs multiples de 5 repérés par la valeur de latitude/longitude
            ilat = np.where(np.isclose((hd["lat"][:, None] - ar["lat"][None, :]), 0, atol=1e-5).any(axis=1))[0]
            ilon = np.where(np.isclose((hd["lon"][:, None] - ar["lon"][None, :]), 0, atol=1e-5).any(axis=1))[0]
            a, b = fn(hd)[np.ix_(ilat, ilon)], interp[np.ix_(ilat, ilon)]
            r[nom + "_sur_noeuds_AROME"] = stats_comparaison(a, b)
            r[nom + "_sur_noeuds_AROME"]["nb_noeuds"] = int(len(ilat) * len(ilon))
        res[n] = r
    return res


# ---------------------------------------------------------------- (f) paramètres unknown

def inconnus(dossiers):
    """Liste des paramètres `unknown` par fichier : (discipline, catégorie, numéro) sans interprétation."""
    import eccodes
    fichiers = set()
    for d in dossiers:
        for f in glob.glob(os.path.join(d, "**", "*.grib2"), recursive=True):
            fichiers.add(f)
    res = {}
    for f in sorted(fichiers):
        base = os.path.basename(f)
        lot = base.split("__")
        if len(lot) < 3 or not (lot[2].startswith(("SP", "HP", "IP")) or "part" in base):
            continue
        with open(f, "rb") as fh:
            while True:
                try:
                    h = eccodes.codes_grib_new_from_file(fh)
                except Exception:   # fichier partiel tronqué
                    break
                if h is None:
                    break
                try:
                    g = lambda k: eccodes.codes_get(h, k) if eccodes.codes_is_defined(h, k) else None
                    if g("shortName") == "unknown":
                        cle = (lot[1], lot[2], g("discipline"), g("parameterCategory"), g("parameterNumber"))
                        d = res.setdefault(cle, {"fichiers": set(), "niveaux": set(), "stepTypes": set(), "echeances": set(),
                                                 "productDefinitionTemplateNumber": g("productDefinitionTemplateNumber"),
                                                 "typeOfLevel": g("typeOfLevel"), "unites_eccodes": g("units"),
                                                 "localTablesVersion": g("localTablesVersion")})
                        d["fichiers"].add(base.split("__")[0] + "__" + lot[1] + "__" + lot[2])
                        d["niveaux"].add(g("level"))
                        d["stepTypes"].add(g("stepType"))
                        d["echeances"].add(g("step"))
                        if "valeurs" not in d:
                            v = grib.masquer(eccodes.codes_get_values(h))
                            d["valeurs"] = {"min": float(np.nanmin(v)), "max": float(np.nanmax(v)), "moyenne": float(np.nanmean(v))}
                finally:
                    eccodes.codes_release(h)
    return [{"resolution": k[0], "paquet": k[1], "discipline": k[2], "categorie": k[3], "numero": k[4],
             "typeOfLevel": v["typeOfLevel"], "niveaux": sorted(x for x in v["niveaux"] if x is not None),
             "stepTypes": sorted(v["stepTypes"]), "echeances": sorted(v["echeances"]),
             "productDefinitionTemplateNumber": v["productDefinitionTemplateNumber"],
             "unites_eccodes": v["unites_eccodes"], "localTablesVersion": v["localTablesVersion"],
             "valeurs_premier_message": v["valeurs"]}
            for k, v in sorted(res.items(), key=lambda kv: tuple(str(x) for x in kv[0]))]


def main(argv=None):
    p = argparse.ArgumentParser(prog="python3 -m pipeline.ingest.validations")
    p.add_argument("--src", nargs="+", required=True)
    p.add_argument("--canon", default="out/canon")
    p.add_argument("--out", default="out/canon/validations.json")
    a = p.parse_args(argv)
    res = {}
    for nom, fn in (("d_fenetres", lambda: fenetres(a.src)), ("a_rafales", lambda: rafales(a.src, a.canon)),
                    ("b_cumuls", lambda: cumuls(a.src, a.canon)), ("c_nebulosite", lambda: nebulosite(a.src, a.canon)),
                    ("e_hd_contre_arome", lambda: hd_contre_arome(a.canon)),
                    ("e2_arome_interpole_vers_hd", lambda: arome_interpole_vers_hd(a.canon)), ("f_inconnus", lambda: inconnus(a.src))):
        print("...", nom, file=sys.stderr)
        res[nom] = fn()
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1, ensure_ascii=False, default=lambda o: int(o) if isinstance(o, np.integer) else str(o))
    return 0


if __name__ == "__main__":
    sys.exit(main())
