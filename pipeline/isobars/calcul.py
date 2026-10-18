# SPDX-License-Identifier: EUPL-1.2
"""Chaîne complète pour une échéance : mslp (Pa) -> hPa -> lissage -> isobares + extrema -> GeoJSON."""
import json
import os
import time

import numpy as np

from . import contours as C
from . import extrema as X

# Paramètres par défaut (justifiés dans docs/poc-j3/isobares.md)
SIGMA = 4.0          # mailles (10 km à 0,025°)
PAS_HPA = 2.0
TOL_KM = 0.1         # Douglas-Peucker
MARGE = 1            # mailles d'érosion du masque pour les cellules de contour
PROEMINENCE = 1.0    # hPa (demi-pas d'isobare, voir la doc)
SIGMA_EXTREMA = 8.0  # mailles : lissage plus fort pour repérer les centres d'action (efface le bruit de réduction au niveau de la mer sur le relief)
ANNEAU_MIN_KM = 12.0  # anneaux fermés plus courts : bruit, supprimés
DECIMALES = 4        # degrés : 1e-4 ° ~ 11 m


def charger(chemin):
    """Charge un .npz canonique AROME : renvoie (lat, lon, mslp en hPa, méta)."""
    d = np.load(chemin)
    meta = json.loads(str(d["meta"]))
    if "mslp" not in d.files:
        raise KeyError("pas de variable mslp dans %s (AROME HD n'en a pas)" % chemin)
    return d["lat"], d["lon"], C.pa_vers_hpa(d["mslp"]), meta


def isobares(F, lat, lon, pas=PAS_HPA, tol_km=TOL_KM, marge=MARGE, anneau_min_km=ANNEAU_MIN_KM):
    """Isobares du champ F (hPa, déjà lissé ou non). Renvoie (lignes, stats).

    lignes : liste de dicts {niveau, majeur, ferme, coords [N,2] lon/lat}.
    """
    valide = np.isfinite(F)
    cell_ok = C.cellules_valides(valide, marge)
    lignes, n_brut, n_pts_brut, rejets = [], 0, 0, 0
    for niv in C.niveaux_hpa(F, pas):
        for pts, ferme in C.contour(F, niv, cell_ok):
            ll = C.vers_lonlat(pts, lat, lon)
            n_brut += 1
            n_pts_brut += len(ll)
            if ferme and C.longueur_km(ll) < anneau_min_km:
                rejets += 1
                continue
            ll2 = C.simplifier(ll, ferme, tol_km)
            lignes.append({"niveau": float(niv), "majeur": bool(round(niv) % 10 == 0),
                           "ferme": bool(ferme), "coords": ll2, "longueur_km": C.longueur_km(ll)})
    stats = {"lignes_brutes": n_brut, "sommets_avant_simplification": n_pts_brut,
             "anneaux_rejetes": rejets, "lignes": len(lignes),
             "sommets": int(sum(len(l["coords"]) for l in lignes)),
             "longueur_km": float(sum(l["longueur_km"] for l in lignes))}
    return lignes, stats


def extrema(F, lat, lon, seuil=PROEMINENCE, F_ext=None):
    """Extrema A / D : liste de dicts {genre, lon, lat, pression, proeminence}.

    Détection (position, proéminence) sur `F_ext` (champ plus lissé, défaut : F) ; la pression affichée
    est la valeur de `F` (celle des isobares tracées) au point trouvé.
    """
    mx, mn = X.detecter(F if F_ext is None else F_ext, seuil)
    sortie = []
    for genre, liste in (("A", mx), ("D", mn)):
        for e in liste:
            sortie.append({"genre": genre, "lon": float(lon[e["colonne"]]), "lat": float(lat[e["ligne"]]),
                           "pression": round(float(F[e["ligne"], e["colonne"]]), 1), "proeminence": round(e["proeminence"], 1)})
    return sortie


LIBELLES = {"A": "Anticyclone", "D": "Dépression"}


def vers_geojson(lignes, extr, entete, decimales=DECIMALES):
    """FeatureCollection : lignes (properties.type = « isobare ») puis extrema (« extremum »)."""
    r = lambda c: [[round(float(x), decimales), round(float(y), decimales)] for x, y in c]
    feats = []
    for l in sorted(lignes, key=lambda l: (l["niveau"], -l["longueur_km"])):
        feats.append({"type": "Feature",
                      "properties": {"type": "isobare", "niveau": int(round(l["niveau"])),
                                     "majeur": l["majeur"], "ferme": l["ferme"]},
                      "geometry": {"type": "LineString", "coordinates": r(l["coords"])}})
    for e in extr:
        feats.append({"type": "Feature",
                      "properties": {"type": "extremum", "genre": e["genre"], "pression": e["pression"],
                                     "libelle": LIBELLES[e["genre"]], "proeminence": e["proeminence"]},
                      "geometry": {"type": "Point", "coordinates": [round(e["lon"], decimales), round(e["lat"], decimales)]}})
    return {"type": "FeatureCollection", "proprietes": entete, "features": feats}


def traiter(chemin_npz, dossier_sortie, sigma=SIGMA, pas=PAS_HPA, tol_km=TOL_KM, proeminence=PROEMINENCE,
            marge=MARGE, ecrire=True, sigma_ext=SIGMA_EXTREMA):
    """Traite une échéance. Écrit <dossier_sortie>/NN.geojson ; renvoie les mesures."""
    t0 = time.perf_counter()
    lat, lon, brut, meta = charger(chemin_npz)
    F = C.lisser(brut, sigma)
    t1 = time.perf_counter()
    lignes, st = isobares(F, lat, lon, pas, tol_km, marge)
    t2 = time.perf_counter()
    extr = extrema(F, lat, lon, proeminence, C.lisser(brut, sigma_ext) if sigma_ext != sigma else F)
    t3 = time.perf_counter()
    step = int(meta["step"])
    entete = {"modele": meta["model"], "tirage": meta["run"], "echeance": step, "unite": "hPa",
              "pas_hpa": pas, "lissage_sigma_mailles": sigma, "tolerance_km": tol_km,
              "proeminence_hpa": proeminence, "lissage_extrema_sigma_mailles": sigma_ext, "source": "mslp (Pa) / 100"}
    gj = vers_geojson(lignes, extr, entete)
    octets = 0
    if ecrire:
        os.makedirs(dossier_sortie, exist_ok=True)
        chemin = os.path.join(dossier_sortie, "%02d.geojson" % step)
        with open(chemin + ".tmp", "w", encoding="utf-8") as f:
            json.dump(gj, f, ensure_ascii=False, separators=(",", ":"))
        os.replace(chemin + ".tmp", chemin)
        octets = os.path.getsize(chemin)
    st.update({"echeance": step, "octets": octets, "extrema": len(extr),
               "anticyclones": sum(e["genre"] == "A" for e in extr),
               "depressions": sum(e["genre"] == "D" for e in extr),
               "t_lissage_s": round(t1 - t0, 3), "t_contours_s": round(t2 - t1, 3),
               "t_extrema_s": round(t3 - t2, 3), "t_total_s": round(time.perf_counter() - t0, 3),
               "pression_min_hpa": float(np.nanmin(F)), "pression_max_hpa": float(np.nanmax(F))})
    return st


def tache(a):
    """Enveloppe picklable pour ProcessPoolExecutor : a = ((chemin, dossier), kwargs)."""
    return traiter(*a[0], **a[1])
