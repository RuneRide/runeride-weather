# SPDX-License-Identifier: EUPL-1.2
"""Contrôle 2 : AROME 0,025° contre ARPEGE 0,1° (SP1), zone commune, échéances 3 et 6.

Rééchantillonnage : MOYENNE PAR BLOCS pondérée. Les nœuds ARPEGE (multiples de 0,1°) tombent
exactement sur des nœuds AROME (grille 0,025° alignée, mesuré par `verifier_alignement`) : la cellule
ARPEGE de ±0,05° autour du nœud contient 5 x 5 nœuds AROME de poids [0,5 1 1 1 0,5]² (trapèze), normalisés.
Une cellule dont un nœud AROME est NaN est exclue. On compare aussi l'échantillonnage PONCTUEL (nœud AROME
coïncident), pour séparer l'écart d'échelle de l'écart de modèle.
Rejouer : PYTHONPATH=<pylib> python3 tests/validation/controle_arpege.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import commun as c  # noqa: E402
from controle_vent import dir_meteo, ecart_angle  # noqa: E402

W1 = np.array([0.5, 1.0, 1.0, 1.0, 0.5])
W2 = np.outer(W1, W1) / np.outer(W1, W1).sum()


def indices_communs(lat_a, lon_a, lat_p, lon_p):
    """Pour chaque nœud ARPEGE inclus (marge de 2 nœuds AROME), indices (ligne, colonne) AROME du centre.

    Retourne (ip, jp, ia, ja) : indices ARPEGE (lignes, colonnes) et AROME correspondants, + erreur max d'alignement (degrés).
    """
    ia_f = (lat_a[0] - lat_p) / 0.025       # ligne AROME du nœud ARPEGE (fractionnaire)
    ja_f = (lon_p - lon_a[0]) / 0.025
    ia, ja = np.rint(ia_f).astype(int), np.rint(ja_f).astype(int)
    ok_i = (np.abs(ia_f - ia) < 1e-3) & (ia >= 2) & (ia <= len(lat_a) - 3)
    ok_j = (np.abs(ja_f - ja) < 1e-3) & (ja >= 2) & (ja <= len(lon_a) - 3)
    ip, jp = np.where(ok_i)[0], np.where(ok_j)[0]
    err = max(np.abs(ia_f - ia)[ip].max(), np.abs(ja_f - ja)[jp].max()) * 0.025
    return ip, jp, ia[ip], ja[jp], float(err)


def bloc(champ_a, ia, ja):
    """Moyenne pondérée 5x5 de `champ_a` centrée sur les nœuds (ia[:,None], ja[None,:]) ; NaN si un nœud est NaN."""
    a = champ_a.astype(np.float64)
    out = np.zeros((len(ia), len(ja)))
    for di in range(-2, 3):
        for dj in range(-2, 3):
            out += W2[di + 2, dj + 2] * a[np.ix_(ia + di, ja + dj)]
    return out


def ponctuel(champ_a, ia, ja):
    return champ_a[np.ix_(ia, ja)].astype(np.float64)


def mesurer(echeances=(3, 6)):
    sp1a, arp = c.grib_arome("SP1"), c.grib_arpege()
    if sp1a is None or arp is None or c.eccodes is None:
        return None
    A, lat_a, lon_a = c.lire_grib(sp1a, {"10u", "10v", "prmsl", "tp", "2t", "2r", "10si", "10wdir"})
    P, lat_p, lon_p = c.lire_grib(arp, {"10u", "10v", "prmsl", "tp", "2t", "2r", "10si", "10wdir"})
    ip, jp, ia, ja, err_align = indices_communs(lat_a, lon_a, lat_p, lon_p)
    res = {"fichier_arpege": os.path.basename(arp), "taille_arpege": os.path.getsize(arp),
           "alignement_max_deg": err_align, "cellules_arpege_zone_commune": [int(len(ip)), int(len(jp))],
           "zone": {"lat": [float(lat_p[ip].min()), float(lat_p[ip].max())], "lon": [float(lon_p[jp].min()), float(lon_p[jp].max())]},
           "methode": "moyenne par blocs 5x5 AROME (poids trapèze) ; ponctuel = nœud coïncident",
           "echeances": {}}
    lon_a_d = lon_a
    for n in echeances:
        e = {}
        def cmp(nom, na, ka, np_, kp, facteur=1.0, masque_min=None):
            a_b = bloc(c.champ(A, na, ka), ia, ja) * facteur
            a_p = ponctuel(c.champ(A, na, ka), ia, ja) * facteur
            p = c.champ(P, np_, kp)[np.ix_(ip, jp)].astype(np.float64)
            s = c.stats_ecart(a_b, p)
            s["ponctuel"] = c.stats_ecart(a_p, p)
            s["moyenne_arome"] = float(np.nanmean(a_b[np.isfinite(a_b) & np.isfinite(p)]))
            s["moyenne_arpege"] = float(np.nanmean(p[np.isfinite(a_b) & np.isfinite(p)]))
            return s, a_b, p
        s_u, ub, pu = cmp("u", "10u", n, "10u", n)
        s_v, vb, pv = cmp("v", "10v", n, "10v", n)
        e["u"], e["v"] = s_u, s_v
        # vitesse issue des composantes moyennées par blocs
        m = np.isfinite(ub) & np.isfinite(vb) & np.isfinite(pu) & np.isfinite(pv)
        vit_a, vit_p = np.hypot(ub, vb), np.hypot(pu, pv)
        e["vitesse"] = c.stats_ecart(np.where(m, vit_a, np.nan), np.where(m, vit_p, np.nan))
        fort = m & (vit_p >= 3.0) & (vit_a >= 3.0)
        da = ecart_angle(dir_meteo(ub, vb), dir_meteo(pu, pv))
        e["direction_ecart_AROME_moins_ARPEGE_deg_vitesses_ge_3"] = {
            "n": int(fort.sum()), "moy_abs": float(np.abs(da[fort]).mean()), "mediane_abs": float(np.median(np.abs(da[fort]))),
            "biais_signe": float(da[fort].mean()), "p90_abs": float(np.percentile(np.abs(da[fort]), 90))}
        # hypothèse de signe : u,v ARPEGE inversés donneraient une corrélation négative
        e["corr_u_avec_u_ARPEGE_inverse"] = -s_u["corr"]
        s_p, _, _ = cmp("mslp", "prmsl", n, "prmsl", n)
        e["mslp_Pa"] = s_p
        s_t, tb, tp_ = cmp("t2m", "2t", n, "2t", n)
        e["t2m_K"] = s_t
        s_r, _, _ = cmp("rh2m", "2r", n, "2r", n)
        e["rh2m_pct"] = s_r
        s_pr, prb, prp = cmp("tp", "tp", n, "tp", n)
        m = np.isfinite(prb) & np.isfinite(prp)
        s_pr["somme_AROME_mm"] = float(prb[m].sum())
        s_pr["somme_ARPEGE_mm"] = float(prp[m].sum())
        s_pr["rapport_sommes_AROME_sur_ARPEGE"] = float(prb[m].sum() / prp[m].sum()) if prp[m].sum() > 0 else None
        s_pr["max_AROME_mm"], s_pr["max_ARPEGE_mm"] = float(np.nanmax(prb)), float(np.nanmax(prp))
        pluie = m & ((prb > 0.1) | (prp > 0.1))
        s_pr["points_pluvieux"] = int(pluie.sum())
        s_pr["sur_points_pluvieux"] = c.stats_ecart(np.where(pluie, prb, np.nan), np.where(pluie, prp, np.nan))
        # fraction de points secs/pluvieux discordants
        s_pr["frac_AROME_pluie_ARPEGE_sec"] = float((m & (prb > 0.1) & (prp <= 0.1)).sum() / max(1, (m & (prb > 0.1)).sum()))
        s_pr["frac_ARPEGE_pluie_AROME_sec"] = float((m & (prp > 0.1) & (prb <= 0.1)).sum() / max(1, (m & (prp > 0.1)).sum()))
        e["tp_mm"] = s_pr
        res["echeances"][n] = e
    # convention de direction dans le GRIB ARPEGE lui-même (indépendant d'AROME)
    n = 3
    u, v = c.champ(P, "10u", n).astype(np.float64), c.champ(P, "10v", n).astype(np.float64)
    si, wd = c.champ(P, "10si", n).astype(np.float64), c.champ(P, "10wdir", n).astype(np.float64)
    ok = np.isfinite(u) & np.isfinite(wd) & (si >= 2.0)
    res["arpege_10u10v_vs_10wdir"] = {"echeance": n, "n": int(ok.sum()),
        "direction_ecart_max_deg": float(np.abs(ecart_angle(dir_meteo(u, v), wd)[ok]).max()),
        "direction_ecart_moy_deg": float(np.abs(ecart_angle(dir_meteo(u, v), wd)[ok]).mean()),
        "vitesse_ecart_max_ms": float(np.nanmax(np.abs(np.hypot(u, v) - si)))}
    return res


if __name__ == "__main__":
    import json
    r = mesurer()
    if r is None:
        print("GRIB AROME/ARPEGE ou eccodes indisponible")
        sys.exit(0)
    c.ecrire_json("arome_vs_arpege.json", r)
    print(json.dumps(r, indent=1))
