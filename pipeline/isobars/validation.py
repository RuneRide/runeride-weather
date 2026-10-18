# SPDX-License-Identifier: EUPL-1.2
"""Validation et mesures des isobares.

  python3 -m pipeline.isobars.validation --canon out/canon/arome/2026-10-17T06Z --iso out/isobars/arome/2026-10-17T06Z \
      --sortie out/isobars/validation.json

1. cohérence géométrique : 200 sommets tirés au hasard dans les GeoJSON écrits, champ lissé interpolé
   (bilinéaire) au sommet = niveau de la ligne, à une tolérance déduite de l'arrondi des coordonnées ;
2. topologie : anneaux vraiment fermés, extrémités des lignes ouvertes sur le bord du domaine valide,
   aucun croisement strict (segments de toutes les lignes, tous niveaux confondus) ;
3. bord du domaine masqué : longueur de ligne à moins de 1,5 maille du masque, avec la méthode retenue
   et avec une méthode naïve (NaN remplacés par la moyenne) ;
4. effet du lissage et de la simplification (nombre de lignes, longueur, sommets, rugosité, écart de position).
"""
import argparse
import json
import os

import numpy as np

from . import calcul as K
from . import contours as C

KM_DEG = np.pi / 180.0 * 6371.0


def interp_bilineaire(F, lat, lon, pts):
    """Valeur de F aux points (lon, lat) par interpolation bilinéaire ; NaN hors grille ou si un voisin est NaN."""
    c = np.interp(pts[:, 0], lon.astype(np.float64), np.arange(len(lon)), left=np.nan, right=np.nan)
    r = np.interp(pts[:, 1], lat[::-1].astype(np.float64), np.arange(len(lat))[::-1], left=np.nan, right=np.nan)
    i0 = np.clip(np.floor(r).astype(int), 0, len(lat) - 2)
    j0 = np.clip(np.floor(c).astype(int), 0, len(lon) - 2)
    fi, fj = r - i0, c - j0
    return ((1 - fi) * (1 - fj) * F[i0, j0] + (1 - fi) * fj * F[i0, j0 + 1]
            + fi * (1 - fj) * F[i0 + 1, j0] + fi * fj * F[i0 + 1, j0 + 1])


def lire_lignes(chemin):
    """Lit un GeoJSON : liste de (niveau, ferme, coords [N,2]) et la liste des extrema."""
    with open(chemin, encoding="utf-8") as f:
        g = json.load(f)
    lignes, extr = [], []
    for f in g["features"]:
        p = f["properties"]
        if p["type"] == "isobare":
            lignes.append((p["niveau"], p["ferme"], np.array(f["geometry"]["coordinates"], dtype=np.float64)))
        else:
            extr.append(p | {"coordinates": f["geometry"]["coordinates"]})
    return lignes, extr


def gradient_max_hpa_km(F):
    """Gradient maximal du champ (hPa/km), différences finies (maille ~2,5 km)."""
    gy, gx = np.gradient(F)
    return float(np.nanmax(np.hypot(gx, gy)) / (0.025 * KM_DEG * 0.69))   # borne haute : côté le plus court (lon à 46,5°)


def validation_sommets(F, lat, lon, lignes, n=200, graine=20261018):
    """Écart |F(sommet) - niveau| sur n sommets aléatoires, et sur n milieux de segments (effet de la simplification)."""
    rng = np.random.default_rng(graine)
    tous = [(niv, c) for niv, _, c in lignes]
    poids = np.array([len(c) for _, c in tous], dtype=float)
    ids = rng.choice(len(tous), size=n, p=poids / poids.sum())
    pts, niv = [], []
    for k in ids:
        c = tous[k][1]
        pts.append(c[rng.integers(len(c))])
        niv.append(tous[k][0])
    ecart = np.abs(interp_bilineaire(F, lat, lon, np.array(pts)) - np.array(niv))
    pts2, niv2 = [], []
    for k in ids:
        c = tous[k][1]
        j = rng.integers(len(c) - 1)
        pts2.append(0.5 * (c[j] + c[j + 1]))
        niv2.append(tous[k][0])
    ecart2 = np.abs(interp_bilineaire(F, lat, lon, np.array(pts2)) - np.array(niv2))
    return {"n": n, "ecart_max_hpa": float(np.nanmax(ecart)), "ecart_moyen_hpa": float(np.nanmean(ecart)),
            "nan": int(np.isnan(ecart).sum()), "ecart_milieux_max_hpa": float(np.nanmax(ecart2)),
            "ecart_milieux_moyen_hpa": float(np.nanmean(ecart2)), "ecart_milieux_p95_hpa": float(np.nanpercentile(ecart2, 95))}


def croisements(lignes, taille=0.05):
    """Nombre de croisements stricts entre segments (toutes lignes). Hachage spatial en cases de `taille` degrés."""
    A, B = [], []
    for _, _, c in lignes:
        A.append(c[:-1])
        B.append(c[1:])
    A, B = np.concatenate(A), np.concatenate(B)
    n = len(A)
    lo = np.floor(np.minimum(A, B) / taille).astype(np.int64)
    hi = np.floor(np.maximum(A, B) / taille).astype(np.int64)
    cles, segs = [], []
    nx_, ny_ = hi[:, 0] - lo[:, 0] + 1, hi[:, 1] - lo[:, 1] + 1
    for dx in range(int(nx_.max())):
        for dy in range(int(ny_.max())):
            m = (nx_ > dx) & (ny_ > dy)
            ix = np.nonzero(m)[0]
            cles.append((lo[ix, 0] + dx) * 100003 + (lo[ix, 1] + dy))
            segs.append(ix)
    cles, segs = np.concatenate(cles), np.concatenate(segs)
    o = np.argsort(cles, kind="stable")
    cles, segs = cles[o], segs[o]
    deb = np.nonzero(np.r_[True, cles[1:] != cles[:-1]])[0]
    fin = np.r_[deb[1:], len(cles)]
    trouves = set()

    def orient(p, q, r):
        return (q[..., 0] - p[..., 0]) * (r[..., 1] - p[..., 1]) - (q[..., 1] - p[..., 1]) * (r[..., 0] - p[..., 0])

    for d, f in zip(deb, fin):
        if f - d < 2:
            continue
        s = segs[d:f]
        i, j = np.triu_indices(len(s), 1)
        a, b = s[i], s[j]
        o1, o2 = orient(A[a], B[a], A[b]), orient(A[a], B[a], B[b])
        o3, o4 = orient(A[b], B[b], A[a]), orient(A[b], B[b], B[a])
        ok = (o1 * o2 < 0) & (o3 * o4 < 0)
        for u, v in zip(a[ok], b[ok]):
            trouves.add((int(min(u, v)), int(max(u, v))))
    return len(trouves)


def topologie(F, lat, lon, lignes, marge=K.MARGE):
    """Anneaux bien fermés ; extrémités des lignes ouvertes au contact du bord du domaine valide ; croisements."""
    cell_ok = C.cellules_valides(np.isfinite(F), marge)
    mauvais_bord = np.pad(~cell_ok, 1, constant_values=True)
    # dilatation d'une cellule (les sommets sont arrondis, les extrémités peuvent déborder d'une maille)
    d = mauvais_bord.copy()
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            d |= np.roll(np.roll(mauvais_bord, di, 0), dj, 1)
    d = d[1:-1, 1:-1]
    anneaux_ko, ouvertes, ouvertes_ko = 0, 0, 0
    for _, ferme, c in lignes:
        if ferme:
            if not (len(c) >= 4 and np.allclose(c[0], c[-1])):
                anneaux_ko += 1
        else:
            ouvertes += 1
            for e in (c[0], c[-1]):
                r = np.interp(e[1], lat[::-1].astype(np.float64), np.arange(len(lat))[::-1])
                cc = np.interp(e[0], lon.astype(np.float64), np.arange(len(lon)))
                i = int(np.clip(np.floor(r), 0, d.shape[0] - 1))
                j = int(np.clip(np.floor(cc), 0, d.shape[1] - 1))
                if not d[i, j]:
                    ouvertes_ko += 1
    return {"lignes": len(lignes), "anneaux_mal_fermes": anneaux_ko, "lignes_ouvertes": ouvertes,
            "extremites_hors_bord": ouvertes_ko, "croisements_stricts": croisements(lignes)}


def longueur_pres_du_masque(lignes, valide, lat, lon, rayon_mailles=1.5):
    """Longueur (km) de ligne dont les segments sont à moins de `rayon_mailles` du masque (NaN ou bord de grille)."""
    nv = np.pad(~valide, 1, constant_values=True)
    proche = nv.copy()
    r = int(np.ceil(rayon_mailles))
    for di in range(-r, r + 1):
        for dj in range(-r, r + 1):
            if di * di + dj * dj <= rayon_mailles ** 2:
                proche |= np.roll(np.roll(nv, di, 0), dj, 1)
    proche = proche[1:-1, 1:-1]
    tot = pres = 0.0
    for _, _, c in lignes:
        if len(c) < 2:
            continue
        m = 0.5 * (c[:-1] + c[1:])
        rr = np.interp(m[:, 1], lat[::-1].astype(np.float64), np.arange(len(lat))[::-1])
        cc = np.interp(m[:, 0], lon.astype(np.float64), np.arange(len(lon)))
        i = np.clip(np.rint(rr).astype(int), 0, valide.shape[0] - 1)
        j = np.clip(np.rint(cc).astype(int), 0, valide.shape[1] - 1)
        L = np.hypot(*np.diff(C.vers_km(c), axis=0).T)
        tot += L.sum()
        pres += L[proche[i, j]].sum()
    return {"longueur_km": tot, "pres_du_masque_km": pres, "part": pres / tot if tot else 0.0}


def bord_naif(brut, lat, lon, sigma):
    """Méthode naïve : NaN remplacés par la moyenne, toutes les cellules valides. Renvoie les lignes (même format)."""
    F = C.lisser(np.where(np.isfinite(brut), brut, np.nanmean(brut)), sigma)
    ok = np.ones((F.shape[0] - 1, F.shape[1] - 1), dtype=bool)
    out = []
    for niv in C.niveaux_hpa(F, 2.0):
        for pts, ferme in C.contour(F, niv, ok):
            out.append((niv, ferme, C.vers_lonlat(pts, lat, lon)))
    return out


def rugosite(lignes):
    """Somme des |angles de virage| (rad) par km de ligne : mesure du crénelage."""
    ang = lon_tot = 0.0
    for _, _, c in lignes:
        if len(c) < 3:
            continue
        p = C.vers_km(c)
        d = np.diff(p, axis=0)
        a = np.arctan2(d[:, 1], d[:, 0])
        da = np.abs((np.diff(a) + np.pi) % (2 * np.pi) - np.pi)
        ang += da.sum()
        lon_tot += np.hypot(*d.T).sum()
    return ang / lon_tot if lon_tot else 0.0


def ecart_positions(ref, autre, n=1500, graine=1):
    """Distance (km) de n sommets de `autre` à l'ensemble des sommets de `ref` (approximation par sommets, ref dense)."""
    rng = np.random.default_rng(graine)
    P = np.concatenate([C.vers_km(c) for _, _, c in autre])
    Q = np.concatenate([C.vers_km(c) for _, _, c in ref])
    # densifier ref : sommets tous les ~0,7 km environ (milieux de segments ajoutés)
    Q = np.concatenate([Q, 0.5 * (Q[:-1] + Q[1:])])
    P = P[rng.choice(len(P), size=min(n, len(P)), replace=False)]
    d = np.array([np.sqrt(((Q - p) ** 2).sum(1)).min() for p in P])
    return {"moyenne_km": float(d.mean()), "p95_km": float(np.percentile(d, 95)), "max_km": float(d.max())}


def etude_lissage(canon, echeances=(0, 24, 48), sigmas=(0, 1, 2, 3, 4, 6, 8), tol_km=K.TOL_KM):
    """Tableau lignes / longueur / sommets / rugosité / écart au tracé brut, par sigma, sans suppression d'anneaux."""
    res = []
    for ech in echeances:
        lat, lon, brut, _ = K.charger(os.path.join(canon, "%02d.npz" % ech))
        ref = None
        for s in sigmas:
            F = C.lisser(brut, s)
            lignes, st = K.isobares(F, lat, lon, tol_km=0.0, anneau_min_km=0.0)
            petits = sum(1 for l in lignes if l["ferme"] and l["longueur_km"] < K.ANNEAU_MIN_KM)
            ll = [(l["niveau"], l["ferme"], l["coords"]) for l in lignes]
            if s == 0:
                ref = ll
            ecart = ecart_positions(ref, ll) if s > 0 else None
            diff = F - brut
            res.append({"echeance": ech, "sigma": s, "lignes": st["lignes"], "anneaux_courts": petits,
                        "longueur_km": round(st["longueur_km"]), "sommets": st["sommets"],
                        "rugosite_rad_par_km": round(rugosite(ll), 4),
                        "ecart_position": ecart,
                        "ecart_champ_rms_hpa": round(float(np.sqrt(np.nanmean(diff ** 2))), 3),
                        "ecart_champ_max_hpa": round(float(np.nanmax(np.abs(diff))), 2)})
    return res


def etude_simplification(canon, ech=24, sigma=K.SIGMA, tolerances=(0, 0.05, 0.1, 0.25, 0.5, 1.0)):
    """Sommets conservés et erreur en hPa (milieux de segments) selon la tolérance de Douglas-Peucker."""
    lat, lon, brut, _ = K.charger(os.path.join(canon, "%02d.npz" % ech))
    F = C.lisser(brut, sigma)
    res = []
    for t in tolerances:
        lignes, st = K.isobares(F, lat, lon, tol_km=t, anneau_min_km=0.0)
        mids, niv = [], []
        for l in lignes:
            c = l["coords"]
            mids.append(0.5 * (c[:-1] + c[1:]))
            niv += [l["niveau"]] * (len(c) - 1)
        mids = np.concatenate(mids)
        e = np.abs(interp_bilineaire(F, lat, lon, mids) - np.array(niv))
        res.append({"tolerance_km": t, "sommets": st["sommets"], "ecart_milieux_max_hpa": round(float(np.nanmax(e)), 3),
                    "ecart_milieux_p95_hpa": round(float(np.nanpercentile(e, 95)), 3),
                    "ecart_milieux_moyen_hpa": round(float(np.nanmean(e)), 4)})
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--canon", required=True)
    ap.add_argument("--iso", required=True, help="dossier des GeoJSON (out/isobars/<modèle>/<tirage>)")
    ap.add_argument("--sortie", required=True)
    ap.add_argument("--echeances", default="0,24,48")
    ap.add_argument("--sigma", type=float, default=K.SIGMA)
    a = ap.parse_args()
    ech = [int(x) for x in a.echeances.split(",")]
    out = {"par_echeance": [], "lissage": etude_lissage(a.canon, tuple(ech)),
           "simplification": etude_simplification(a.canon, ech[min(1, len(ech) - 1)], a.sigma)}
    for e in ech:
        lat, lon, brut, _ = K.charger(os.path.join(a.canon, "%02d.npz" % e))
        F = C.lisser(brut, a.sigma)
        lignes, _ = lire_lignes(os.path.join(a.iso, "%02d.geojson" % e))
        g = gradient_max_hpa_km(F)
        # arrondi des coordonnées à 1e-4° : erreur de position <= 0,5e-4 * sqrt(2) degré ; en km : * KM_DEG
        tol = g * 0.5e-4 * np.sqrt(2) * KM_DEG + 1e-6
        v = validation_sommets(F, lat, lon, lignes)
        v.update({"gradient_max_hpa_par_km": g, "tolerance_hpa": tol, "ok": v["ecart_max_hpa"] <= tol and v["nan"] == 0})
        valide = np.isfinite(F)
        naif = bord_naif(brut, lat, lon, a.sigma)
        out["par_echeance"].append({
            "echeance": e, "sommets": v, "topologie": topologie(F, lat, lon, lignes),
            "bord": {"methode_retenue": longueur_pres_du_masque([(n, f, c) for n, f, c in lignes], valide, lat, lon),
                     "methode_naive_nan_remplaces_par_la_moyenne": longueur_pres_du_masque(naif, valide, lat, lon)}})
    json.dump(out, open(a.sortie, "w"), indent=1, default=float)
    print(json.dumps(out["par_echeance"], indent=1, default=float))
    print(json.dumps(out["lissage"], default=float))
    print(json.dumps(out["simplification"], default=float))


if __name__ == "__main__":
    main()
