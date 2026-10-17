# SPDX-License-Identifier: EUPL-1.2
"""Décodage GRIB2 (eccodes) : grille, masque 9999, conversion de longitude.

Les fichiers GRIB sont des données non fiables : on ne fait que les décoder.
"""
import numpy as np

VALEUR_MANQUANTE = 9999.0

# Clés lues pour identifier un message sans se fier au seul shortName
# (certains paramètres Météo-France ressortent « unknown »).
CLES = ("shortName", "discipline", "parameterCategory", "parameterNumber", "typeOfLevel",
        "level", "stepType", "startStep", "endStep", "step", "units", "name", "dataDate", "dataTime")


def masquer(valeurs, manquant=VALEUR_MANQUANTE):
    """Remplace la valeur GRIB manquante (9999) par NaN ; retourne un float32 neuf."""
    v = np.asarray(valeurs, dtype=np.float64)
    out = np.where(v == manquant, np.nan, v)
    return out.astype(np.float32)


def longitudes_vers_180(lon):
    """Convertit des longitudes 0..360 en −180..180 (348..360,0..16 donne −12..16).

    Retourne (lon convertie, permutation d'indices ou None). La permutation est
    nécessaire seulement si la grille traverse le méridien 180 (jamais pour AROME).
    """
    lon = np.asarray(lon, dtype=np.float64)
    conv = ((lon + 180.0) % 360.0) - 180.0
    if np.all(np.diff(conv) > 0):
        return conv, None
    perm = np.argsort(conv, kind="stable")
    return conv[perm], perm


def grille(h, eccodes):
    """Axes lat (décroissante) et lon (croissante, −180..180) d'un message `regular_ll`.

    Retourne (lat float32, lon float32, flip_lat bool, perm_lon ou None).
    """
    g = lambda k: eccodes.codes_get(h, k)
    if g("gridType") != "regular_ll":
        raise ValueError(f"grille non gérée : {g('gridType')}")
    if g("jPointsAreConsecutive"):
        raise ValueError("balayage j consécutif non géré")
    ni, nj = g("Ni"), g("Nj")
    la1, la2 = g("latitudeOfFirstGridPointInDegrees"), g("latitudeOfLastGridPointInDegrees")
    lo1 = g("longitudeOfFirstGridPointInDegrees")
    di, dj = g("iDirectionIncrementInDegrees"), g("jDirectionIncrementInDegrees")
    sens_i = -1.0 if g("iScansNegatively") else 1.0
    lon = lo1 + sens_i * di * np.arange(ni)
    lat = la1 + (1.0 if g("jScansPositively") else -1.0) * dj * np.arange(nj)
    if abs(lat[-1] - la2) > 1e-6:
        raise ValueError(f"latitude finale incohérente : {lat[-1]} contre {la2}")
    flip = bool(lat[0] < lat[-1])  # on veut nord -> sud
    if flip:
        lat = lat[::-1]
    lon, perm = longitudes_vers_180(lon)
    # arrondi à 1e-6 degré : évite les restes de calcul en virgule flottante
    return (np.round(lat, 6).astype(np.float32), np.round(lon, 6).astype(np.float32), flip, perm)


def lire_fichier(chemin, filtre=None):
    """Décode un fichier GRIB2 et retourne (messages, lat, lon).

    `messages` : liste de dicts {cle..., "valeurs": float32 [lat, lon] masqué NaN}.
    `filtre(meta) -> bool` évite de décoder les messages inutiles.
    """
    import eccodes
    messages, lat, lon = [], None, None
    with open(chemin, "rb") as fh:
        while True:
            h = eccodes.codes_grib_new_from_file(fh)
            if h is None:
                break
            try:
                meta = {}
                for k in CLES:
                    meta[k] = eccodes.codes_get(h, k) if eccodes.codes_is_defined(h, k) else None
                if filtre is not None and not filtre(meta):
                    continue
                if lat is None:
                    lat, lon, flip, perm = grille(h, eccodes)
                    ni, nj = eccodes.codes_get(h, "Ni"), eccodes.codes_get(h, "Nj")
                v = masquer(eccodes.codes_get_values(h)).reshape(nj, ni)
                if flip:
                    v = v[::-1]
                if perm is not None:
                    v = v[:, perm]
                meta["valeurs"] = np.ascontiguousarray(v)
                messages.append(meta)
            finally:
                eccodes.codes_release(h)
    return messages, lat, lon
