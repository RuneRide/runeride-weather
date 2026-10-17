# SPDX-License-Identifier: EUPL-1.2
"""Calcul des variables canoniques (contrat §2) à partir des champs GRIB décodés.

Toutes les fonctions de calcul sont pures (numpy) pour être testables sans GRIB.
"""
import json
import numpy as np

# shortName eccodes -> champ lu (les autres paramètres, dont les `unknown`, sont ignorés)
PARAMETRES = {
    "2t": "2t", "2r": "2r", "10u": "10u", "10v": "10v",
    "max_10efg": "max_10efg", "max_10nfg": "max_10nfg", "max_i10fg": "max_i10fg",
    "prmsl": "prmsl", "tirf": "tirf", "tsnowp": "tsnowp", "tgrp": "tgrp", "tp": "tp",
    "lcc": "lcc", "mcc": "mcc", "hcc": "hcc", "CAPE_INS": "CAPE_INS",
}
CUMULS = ("tirf", "tsnowp", "tgrp", "tp")          # accum, fenêtre 0 -> n
MAXIMA = ("max_10efg", "max_10nfg", "max_i10fg")   # max, fenêtre n-1 -> n

SEUIL_NEIGE_MM = 0.05

UNITES = {
    "wind10_u": "m/s", "wind10_v": "m/s", "gust10": "m/s", "t2m": "K", "rh2m": "%",
    "precip_rate": "mm/h", "snow_fraction": "1", "cloud_total": "%", "cape": "J/kg", "mslp": "Pa",
}


def filtre_message(meta):
    """Garde seulement les messages utiles (niveau 2 m / 10 m / surface / mer attendu)."""
    return meta["shortName"] in PARAMETRES


def verifier_fenetre(meta):
    """Vérifie la fenêtre temporelle annoncée par GRIB (contrat : cumul 0->n, max n-1->n)."""
    nom, n = meta["shortName"], meta["step"]
    if nom in CUMULS and not (meta["stepType"] == "accum" and meta["startStep"] == 0 and meta["endStep"] == n):
        raise ValueError(f"{nom} : fenêtre inattendue {meta['stepType']} {meta['startStep']}-{meta['endStep']} (échéance {n})")
    if nom in MAXIMA and not (meta["stepType"] == "max" and meta["startStep"] == n - 1 and meta["endStep"] == n):
        raise ValueError(f"{nom} : fenêtre inattendue {meta['stepType']} {meta['startStep']}-{meta['endStep']} (échéance {n})")


def indexer(messages):
    """Liste de messages -> {échéance: {nom: tableau}}. Contrôle les fenêtres et les doublons."""
    out = {}
    for m in messages:
        nom = PARAMETRES.get(m["shortName"])
        if nom is None:
            continue
        verifier_fenetre(m)
        d = out.setdefault(m["step"], {})
        if nom in d:
            raise ValueError(f"{nom} présent deux fois à l'échéance {m['step']}")
        d[nom] = m["valeurs"]
    return out


def debit_precip(cumul_n, cumul_prev):
    """Débit (mm/h) par différence de deux cumuls (kg/m² = mm), minimum 0 (NaN conservé)."""
    return np.maximum(cumul_n - cumul_prev, 0.0)


def rafale_hd(max_est, max_nord):
    """Hypothèse HD : module du vecteur rafale. Les composantes peuvent être négatives."""
    return np.hypot(max_est, max_nord)


def nebulosite_totale(bas, moyen, haut):
    """Nébulosité totale en % depuis bas/moyen/haut en %, recouvrement aléatoire.

    Calcul en float32 comme la fixture de référence (le float64 en diffère de ~1e-5 %).
    Pas de bornage : les cumuls GRIB bas/moyen/haut peuvent dépasser 100 de quelques 1e-3
    (la fixture contient 100,0033).
    """
    c = np.float32(100.0)
    return c * (1 - (1 - bas / c) * (1 - moyen / c) * (1 - haut / c))


def fraction_neige(d_neige, d_total):
    """Δneige / Δtotal si Δtotal > 0,05 mm, sinon 0, bornée à [0,1] (NaN conservé là où l'entrée est NaN)."""
    with np.errstate(invalid="ignore", divide="ignore"):
        f = np.where(d_total > SEUIL_NEIGE_MM, d_neige / np.where(d_total > 0, d_total, 1.0), 0.0)
    f = np.clip(f, 0.0, 1.0)   # les cumuls GRIB ont un pas de codage : Δneige peut être légèrement < 0 ou > Δtotal
    return np.where(np.isnan(d_total) | np.isnan(d_neige), np.nan, f)


def cumul_total(champs):
    """tirf + tsnowp + tgrp en float32, comme la fixture (None si un des trois manque)."""
    if all(k in champs for k in ("tirf", "tsnowp", "tgrp")):
        return champs["tirf"] + champs["tsnowp"] + champs["tgrp"]
    return None


def construire(model, step, champs, champs_prev, bornes=True):
    """Variables canoniques de l'échéance `step`.

    champs / champs_prev : {nom: tableau float32 masqué} aux échéances n et n−1
    (champs_prev peut être None à l'échéance 0 ; à l'échéance 1 les cumuls de
    l'échéance 0 valent 0 par définition, les fichiers n'en contiennent pas).
    bornes : borne rh2m et cloud_total à [0,100] (contrat révisé). bornes=False
    reproduit les anciennes sorties non bornées.
    Retourne un dict {variable: float32}. Les variables non calculables sont absentes.
    """
    f64 = lambda a: a.astype(np.float64)
    out = {}
    if "10u" in champs and "10v" in champs:
        out["wind10_u"], out["wind10_v"] = champs["10u"], champs["10v"]
    if "2t" in champs:
        out["t2m"] = champs["2t"]
    if "2r" in champs:
        out["rh2m"] = np.clip(champs["2r"], 0.0, 100.0) if bornes else champs["2r"]
    if "CAPE_INS" in champs:
        out["cape"] = champs["CAPE_INS"]
    if "prmsl" in champs and model == "arome":
        out["mslp"] = champs["prmsl"]
    if step >= 1:
        if model == "arome-hd" and "max_10efg" in champs and "max_10nfg" in champs:
            out["gust10"] = rafale_hd(f64(champs["max_10efg"]), f64(champs["max_10nfg"])).astype(np.float32)
        elif model == "arome" and "max_i10fg" in champs:
            out["gust10"] = champs["max_i10fg"]
        tot_n = cumul_total(champs)
        if tot_n is not None:
            if step == 1:
                tot_p, sn_p = np.float32(0.0), np.float32(0.0)
            else:
                tot_p = cumul_total(champs_prev) if champs_prev else None
                sn_p = champs_prev.get("tsnowp") if champs_prev else None
            if tot_p is not None and sn_p is not None:
                d_tot = debit_precip(tot_n, tot_p)
                out["precip_rate"] = d_tot
                d_sn = champs["tsnowp"] - sn_p
                out["snow_fraction"] = fraction_neige(d_sn, d_tot)
        if all(k in champs for k in ("lcc", "mcc", "hcc")):
            c = nebulosite_totale(champs["lcc"], champs["mcc"], champs["hcc"])
            out["cloud_total"] = np.clip(c, np.float32(0.0), np.float32(100.0)) if bornes else c
    return {k: np.ascontiguousarray(v, dtype=np.float32) for k, v in out.items()}


def ecrire(chemin, lat, lon, variables, model, run, step, sources, note=""):
    """Écrit le .npz canonique (clés lat, lon, meta, variables)."""
    import os
    meta = {
        "model": model, "run": run, "step": step,
        "units": {k: UNITES[k] for k in variables},
        "source": sources, "note": note,
    }
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    tmp = chemin + ".tmp.npz"
    np.savez_compressed(tmp, lat=lat.astype(np.float32), lon=lon.astype(np.float32),
                        meta=np.array(json.dumps(meta)), **variables)
    os.replace(tmp, chemin)
