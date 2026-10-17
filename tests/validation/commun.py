# SPDX-License-Identifier: EUPL-1.2
"""Outils communs de la validation croisée.

Volontairement INDÉPENDANT de `pipeline/` : le décodage GRIB est réécrit ici
(eccodes brut) pour que les contrôles ne partagent pas le code testé.
Les fichiers GRIB sont des données non fiables : on les décode, on ne les exécute pas.
"""
import glob
import json
import os
import sys

import numpy as np

RACINE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SORTIE = os.path.join(RACINE, "out", "validation")
TIRAGE = "2026-10-17T06:00:00Z"
TIRAGE_C = "2026-10-17T06Z"
MANQUANT = 9999.0

# Emplacements de recherche (variables d'environnement prioritaires).
RACINES_GRIB = [p for p in (os.environ.get("RUNERIDE_GRIB_SRC"),) if p] + [
    os.path.join(RACINE, "out", "grib"),
    "/tmp/claude-1000/-home-philippe-github-com-runeride/93bcb318-8a08-4043-adfc-8845d03af2b4/scratchpad/grib",
]
RACINES_ARP = [p for p in (os.environ.get("RUNERIDE_ARPEGE_SRC"),) if p] + [
    "/tmp/claude-1000/-home-philippe-github-com-runeride/93bcb318-8a08-4043-adfc-8845d03af2b4/scratchpad/arp",
    os.path.join(RACINE, "out", "grib", "arpege"),
]

try:  # eccodes éventuellement dans PYTHONPATH
    import eccodes  # noqa: F401
except ImportError:
    eccodes = None


def trouver(motif, racines):
    """Premier fichier correspondant au motif (glob récursif) dans les racines, ou None."""
    for r in racines:
        for f in sorted(glob.glob(os.path.join(r, "**", motif), recursive=True)):
            if os.path.isfile(f):
                return f
    return None


def grib_arome(paquet, groupe="00H06H"):
    return trouver(f"arome__0025__{paquet}__{groupe}__{TIRAGE}.grib2", RACINES_GRIB)


def grib_arpege():
    return trouver(f"arpege__01__SP1__000H012H__{TIRAGE}.grib2", RACINES_ARP)


def lire_grib(chemin, noms=None):
    """Retourne ({(shortName, typeOfStatistics/stepType, startStep, endStep): tableau float32 NaN=manquant}, lat, lon).

    lat décroissante (nord -> sud), lon en -180..180 croissante. `noms` filtre sur shortName
    (les paramètres `unknown` sont identifiés par discipline/catégorie/numéro : "unknown:0.6.1").
    """
    import eccodes as ec
    champs, lat, lon = {}, None, None
    with open(chemin, "rb") as fh:
        while True:
            h = ec.codes_grib_new_from_file(fh)
            if h is None:
                break
            try:
                g = lambda k: ec.codes_get(h, k) if ec.codes_is_defined(h, k) else None
                nom = g("shortName")
                if nom == "unknown":
                    nom = f"unknown:{g('discipline')}.{g('parameterCategory')}.{g('parameterNumber')}"
                if noms is not None and nom not in noms:
                    continue
                ni, nj = g("Ni"), g("Nj")
                if lat is None:
                    la1 = g("latitudeOfFirstGridPointInDegrees")
                    lo1 = g("longitudeOfFirstGridPointInDegrees")
                    di, dj = g("iDirectionIncrementInDegrees"), g("jDirectionIncrementInDegrees")
                    assert g("gridType") == "regular_ll" and not g("iScansNegatively") and not g("jPointsAreConsecutive")
                    sj = 1.0 if g("jScansPositively") else -1.0
                    lat = la1 + sj * dj * np.arange(nj)
                    lon = ((lo1 + di * np.arange(ni) + 180.0) % 360.0) - 180.0
                    flip = lat[0] < lat[-1]
                    if flip:
                        lat = lat[::-1]
                    assert np.all(np.diff(lon) > 0)
                v = np.asarray(ec.codes_get_values(h), dtype=np.float64)
                v = np.where(v == MANQUANT, np.nan, v).reshape(nj, ni)
                if flip:
                    v = v[::-1]
                champs[(nom, g("stepType"), g("startStep"), g("endStep"))] = v.astype(np.float32)
            finally:
                ec.codes_release(h)
    return champs, lat, lon


def champ(champs, nom, fin, debut=None):
    """Sélectionne un champ par nom et échéance de fin (et de début pour les fenêtres)."""
    for (n, t, d, f), v in champs.items():
        if n == nom and f == fin and (debut is None or d == debut):
            return v
    raise KeyError((nom, fin, debut))


def canon(modele, echeance):
    chemin = os.path.join(RACINE, "out", "canon", modele, TIRAGE_C, f"{echeance:02d}.npz")
    if not os.path.isfile(chemin):
        return None
    z = np.load(chemin)
    return {k: z[k] for k in z.files if k != "meta"}


def ecrire_json(nom, obj):
    os.makedirs(SORTIE, exist_ok=True)
    with open(os.path.join(SORTIE, nom), "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, ensure_ascii=False)


def stats_ecart(a, b):
    """Écarts a-b sur les points valides des deux tableaux : biais, rmse, max abs, n, corrélation."""
    m = np.isfinite(a) & np.isfinite(b)
    d = (a[m].astype(np.float64) - b[m].astype(np.float64))
    return {"n": int(m.sum()), "biais": float(d.mean()), "rmse": float(np.sqrt((d ** 2).mean())),
            "max_abs": float(np.abs(d).max()), "moy_abs": float(np.abs(d).mean()),
            "corr": float(np.corrcoef(a[m].astype(np.float64), b[m].astype(np.float64))[0, 1])}
