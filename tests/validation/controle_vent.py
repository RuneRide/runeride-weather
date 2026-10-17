# SPDX-License-Identifier: EUPL-1.2
"""Contrôle 1 : composantes u/v canoniques contre vitesse (10si) et direction météo (10wdir) du modèle.

Convention testée : direction météo = d'où vient le vent, degrés dans le sens horaire depuis le nord ;
dir = (270 - atan2(v, u) en degrés) mod 360.
Rejouer : PYTHONPATH=<pylib> python3 tests/validation/controle_vent.py
"""
import sys

import numpy as np

sys.path.insert(0, __import__("os").path.dirname(__file__))
import commun as c  # noqa: E402


def dir_meteo(u, v):
    return np.mod(270.0 - np.degrees(np.arctan2(v, u)), 360.0)


def ecart_angle(a, b):
    """Écart signé a-b ramené dans [-180, 180]."""
    return np.mod(a - b + 180.0, 360.0) - 180.0


def mesurer(echeances=range(0, 7), modele="arome"):
    sp1 = c.grib_arome("SP1")
    if sp1 is None or c.eccodes is None:
        return None
    ch, lat, lon = c.lire_grib(sp1, {"10si", "10wdir", "10u", "10v"})
    res = {"fichier": sp1.split("/")[-1], "modele": modele, "echeances": {}}
    for n in echeances:
        k = c.canon(modele, n)
        if k is None:
            continue
        # la grille canonique doit être celle du GRIB (même lat/lon)
        assert np.allclose(k["lat"], lat, atol=1e-4) and np.allclose(k["lon"], lon, atol=1e-4)
        u, v = k["wind10_u"].astype(np.float64), k["wind10_v"].astype(np.float64)
        si, wd = c.champ(ch, "10si", n).astype(np.float64), c.champ(ch, "10wdir", n).astype(np.float64)
        gu, gv = c.champ(ch, "10u", n).astype(np.float64), c.champ(ch, "10v", n).astype(np.float64)
        ok = np.isfinite(u) & np.isfinite(v) & np.isfinite(si) & np.isfinite(wd)
        vit = np.hypot(u, v)
        dv = vit - si
        d_dir = ecart_angle(dir_meteo(u, v), wd)
        # direction signée pour les hypothèses alternatives (convention inversée : "vers où")
        d_inv = ecart_angle(dir_meteo(-u, -v), wd)             # u,v de signe opposé
        d_vers = ecart_angle(np.mod(90.0 - np.degrees(np.arctan2(v, u)), 360.0), wd)  # cap vers lequel va le vent ("90 - atan2")
        d_uv = ecart_angle(np.mod(270.0 - np.degrees(np.arctan2(u, v)), 360.0), wd)   # u/v permutés
        faible = ok & (si < 1.0)
        fort = ok & (si >= 2.0)
        e = {
            "n_valides": int(ok.sum()),
            "n_total": int(ok.size),
            "vitesse_ecart_max_ms": float(np.abs(dv[ok]).max()),
            "vitesse_ecart_moy_ms": float(np.abs(dv[ok]).mean()),
            "vitesse_biais_ms": float(dv[ok].mean()),
            "direction_ecart_max_deg_tous": float(np.abs(d_dir[ok]).max()),
            "direction_ecart_moy_deg_tous": float(np.abs(d_dir[ok]).mean()),
            "direction_ecart_max_deg_vitesse_ge_2": float(np.abs(d_dir[fort]).max()),
            "direction_ecart_moy_deg_vitesse_ge_2": float(np.abs(d_dir[fort]).mean()),
            "direction_biais_signe_deg_vitesse_ge_2": float(d_dir[fort].mean()),
            "direction_p99_deg_vitesse_ge_2": float(np.percentile(np.abs(d_dir[fort]), 99)),
            "direction_p99_9_deg_vitesse_ge_2": float(np.percentile(np.abs(d_dir[fort]), 99.9)),
            "n_vitesse_lt_1": int(faible.sum()),
            "direction_ecart_max_deg_vitesse_lt_1": float(np.abs(d_dir[faible]).max()) if faible.any() else None,
            "direction_ecart_moy_deg_vitesse_lt_1": float(np.abs(d_dir[faible]).mean()) if faible.any() else None,
            # hypothèses alternatives : leur écart moyen doit être grand si la convention testée est la bonne
            "alt_signes_opposes_ecart_moy_deg": float(np.abs(d_inv[fort]).mean()),
            "alt_cap_vers_ecart_moy_deg": float(np.abs(d_vers[fort]).mean()),
            "alt_u_v_permutes_ecart_moy_deg": float(np.abs(d_uv[fort]).mean()),
            # cohérence canon <-> GRIB 10u/10v bruts (le canon ne doit rien changer)
            "canon_vs_grib_u_ecart_max": float(np.nanmax(np.abs(u - gu))),
            "canon_vs_grib_v_ecart_max": float(np.nanmax(np.abs(v - gv))),
        }
        # 10si/10wdir portés par le GRIB : leur propre cohérence interne avec le GRIB 10u/10v
        okg = np.isfinite(gu) & np.isfinite(gv) & np.isfinite(si) & np.isfinite(wd) & (si >= 2.0)
        e["grib_10u10v_vs_10si10wdir_dir_ecart_max_deg_vitesse_ge_2"] = float(np.abs(ecart_angle(dir_meteo(gu, gv), wd)[okg]).max())
        classes = {}
        for lo, hi in ((0, 0.1), (0.1, 0.5), (0.5, 1), (1, 2), (2, 5), (5, 100)):
            mm = ok & (si >= lo) & (si < hi)
            if mm.any():
                classes[f"{lo}-{hi}"] = {"n": int(mm.sum()), "ecart_dir_max_deg": float(np.abs(d_dir[mm]).max()), "ecart_dir_moy_deg": float(np.abs(d_dir[mm]).mean())}
        e["par_classe_de_vitesse_ms"] = classes
        res["echeances"][n] = e
    return res


def lisser(a, k):
    """Moyenne par blocs k x k (NaN si un point du bloc est NaN), renvoie le tableau réduit."""
    nj, ni = (a.shape[0] // k) * k, (a.shape[1] // k) * k
    return a[:nj, :ni].astype(np.float64).reshape(nj // k, k, ni // k, k).mean(axis=(1, 3))


def geostrophie(modele="arome", echeances=(0, 3, 6), k=20):
    """Contrôle physique indépendant des signes : le vent suit approximativement l'équilibre géostrophique
    (hémisphère nord) u ~ -(1/rho f) dp/dy, v ~ +(1/rho f) dp/dx, avec p = mslp et x vers l'est, y vers le nord.
    Un signe de u ou de v inversé ferait passer ces corrélations de positives à négatives. Les vents sont
    lissés par blocs de k x k points (0,5 deg pour k = 20) pour éliminer le relief et la couche limite fine.
    """
    res = {}
    for n in echeances:
        z = c.canon(modele, n)
        if z is None or "mslp" not in z:
            continue
        lat = z["lat"].astype(np.float64)
        lat_s = lisser(np.repeat(lat[:, None], z["lon"].size, axis=1), k)[:, 0]
        p, u, v = lisser(z["mslp"], k), lisser(z["wind10_u"], k), lisser(z["wind10_v"], k)
        dy = 111195.0 * 0.025 * k          # m entre deux cellules en latitude
        dpdy = -np.gradient(p, axis=0) / dy  # lat décroissante vers le bas : y nord = -axe 0
        dx = (111195.0 * np.cos(np.radians(lat_s)) * 0.025 * k)[:, None]
        dpdx = np.gradient(p, axis=1) / dx
        f = 2 * 7.292e-5 * np.sin(np.radians(lat_s))[:, None]
        ug, vg = -dpdy / (1.2 * f), dpdx / (1.2 * f)
        m = np.isfinite(ug) & np.isfinite(vg) & np.isfinite(u) & np.isfinite(v)
        m[:2], m[-2:], m[:, :2], m[:, -2:] = False, False, False, False
        res[n] = {"n_cellules": int(m.sum()),
                  "corr_u_vs_ug": float(np.corrcoef(u[m], ug[m])[0, 1]), "corr_v_vs_vg": float(np.corrcoef(v[m], vg[m])[0, 1]),
                  "pente_u_sur_ug": float(np.polyfit(ug[m], u[m], 1)[0]), "pente_v_sur_vg": float(np.polyfit(vg[m], v[m], 1)[0])}
    return res


if __name__ == "__main__":
    import json
    r = mesurer()
    if r is None:
        print("GRIB ou eccodes indisponible")
        sys.exit(0)
    r["geostrophie"] = geostrophie()
    c.ecrire_json("vent_direction.json", r)
    print(json.dumps(r, indent=1))
