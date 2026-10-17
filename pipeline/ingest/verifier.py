# SPDX-License-Identifier: EUPL-1.2
"""Comparaison d'un .npz canonique avec une référence (fixture complète ou extrait)."""
import numpy as np


def localiser_extrait(lat, lon, lat_ref, lon_ref):
    """Indices (i0, j0) de l'extrait (lat_ref, lon_ref) dans la grille (lat, lon), ou ValueError."""
    i0 = int(np.argmin(np.abs(lat - lat_ref[0])))
    j0 = int(np.argmin(np.abs(lon - lon_ref[0])))
    ok = (np.array_equal(lat[i0:i0 + len(lat_ref)], lat_ref)
          and np.array_equal(lon[j0:j0 + len(lon_ref)], lon_ref))
    if not ok:
        raise ValueError("l'extrait n'est pas un sous-ensemble exact de la grille")
    return i0, j0


def comparer(courant, ref):
    """courant / ref : mappings (npz). Retourne {variable: dict de mesures} ; extrait détecté si formes différentes."""
    lat, lon = courant["lat"], courant["lon"]
    if ref["lat"].shape == lat.shape and ref["lon"].shape == lon.shape:
        sel = (slice(None), slice(None))
        axes = bool(np.array_equal(lat, ref["lat"]) and np.array_equal(lon, ref["lon"]))
    else:
        i0, j0 = localiser_extrait(lat, lon, ref["lat"], ref["lon"])
        sel = (slice(i0, i0 + len(ref["lat"])), slice(j0, j0 + len(ref["lon"])))
        axes = True
    res = {"_axes_identiques": axes}
    for k in ref.files if hasattr(ref, "files") else ref:
        if k in ("lat", "lon", "meta"):
            continue
        if k not in courant.files if hasattr(courant, "files") else k not in courant:
            res[k] = {"absente": True}
            continue
        a, b = np.asarray(courant[k])[sel], np.asarray(ref[k])
        masque_nan_egal = bool(np.array_equal(np.isnan(a), np.isnan(b)))
        ok = ~np.isnan(a) & ~np.isnan(b)
        d = np.abs(a[ok].astype(np.float64) - b[ok].astype(np.float64))
        res[k] = {
            "identique_bit_a_bit": bool(np.array_equal(a, b, equal_nan=True)),
            "masque_nan_egal": masque_nan_egal,
            "ecart_max": float(d.max()) if d.size else 0.0,
            "nb_valeurs_differentes": int((a[ok] != b[ok]).sum()),
            "ecart_max_ulp_relatif": float((d / np.maximum(np.abs(b[ok].astype(np.float64)), 1e-30)).max()) if d.size else 0.0,
        }
    return res
