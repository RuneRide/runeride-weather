# SPDX-License-Identifier: EUPL-1.2
"""Contrôle 3 : cumul total `tp` (SP1) contre tirf (SP2) + tsnowp (SP1) + tgrp (SP1), AROME 0,025°, échéances 1 à 6.

Valide la formule du débit de précipitation du contrat (§2) : precip_rate = max(0, Δ(tirf + tsnowp + tgrp)).
Rejouer : PYTHONPATH=<pylib> python3 tests/validation/controle_precip.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import commun as c  # noqa: E402


def mesurer(echeances=range(1, 7)):
    sp1, sp2 = c.grib_arome("SP1"), c.grib_arome("SP2")
    if sp1 is None or sp2 is None or c.eccodes is None:
        return None
    A, _, _ = c.lire_grib(sp1, {"tp", "tsnowp", "tgrp"})
    B, _, _ = c.lire_grib(sp2, {"tirf"})
    res = {"echeances": {}}
    for n in echeances:
        tp = c.champ(A, "tp", n, 0).astype(np.float64)
        sn = c.champ(A, "tsnowp", n, 0).astype(np.float64)
        gr = c.champ(A, "tgrp", n, 0).astype(np.float64)
        rf = c.champ(B, "tirf", n, 0).astype(np.float64)
        m = np.isfinite(tp) & np.isfinite(sn) & np.isfinite(gr) & np.isfinite(rf)
        somme = rf + sn + gr
        d = (tp - somme)[m]
        e = {"n": int(m.sum()),
             "tp_max_mm": float(tp[m].max()), "tp_moyen_mm": float(tp[m].mean()),
             "tirf_max_mm": float(rf[m].max()), "tsnowp_max_mm": float(sn[m].max()), "tgrp_max_mm": float(gr[m].max()),
             "tp_moins_somme_biais_mm": float(d.mean()), "tp_moins_somme_ecart_max_abs_mm": float(np.abs(d).max()),
             "tp_moins_somme_rmse_mm": float(np.sqrt((d ** 2).mean())),
             "tp_moins_somme_min_mm": float(d.min()), "tp_moins_somme_max_mm": float(d.max()),
             "points_ecart_gt_1e-3_mm": int((np.abs(d) > 1e-3).sum()),
             "corr": float(np.corrcoef(tp[m], somme[m])[0, 1]),
             "somme_tp_mm": float(tp[m].sum()), "somme_tirf_plus_sn_plus_gr_mm": float(somme[m].sum())}
        # hypothèses alternatives : l'écart maximal doit être plus grand si une espèce manque
        mg = m & (gr > 0.005)
        e["points_graupel_gt_0.005mm"] = int(mg.sum())
        if mg.any():
            e["sur_points_graupel"] = {
                "ecart_max_tp_moins_tirf_mm": float(np.abs(tp - rf)[mg].max()),
                "ecart_max_tp_moins_tirf_plus_neige_mm": float(np.abs(tp - rf - sn)[mg].max()),
                "ecart_max_tp_moins_somme3_mm": float(np.abs(tp - somme)[mg].max()),
                "ecart_moyen_tp_moins_tirf_mm": float(np.abs(tp - rf)[mg].mean()),
                "ecart_moyen_tp_moins_somme3_mm": float(np.abs(tp - somme)[mg].mean())}
        # pas de quantification GRIB observé sur tp : plus petit écart non nul entre valeurs distinctes (échantillon)
        v = np.unique(tp[m][:2_000_000])
        e["tp_plus_petit_pas_observe_mm"] = float(np.diff(v).min()) if len(v) > 1 else None
        # signature de l'écart : proportionnel aux espèces ?
        e["frac_points_tp_egal_somme_a_1e-6"] = float((np.abs(d) <= 1e-6).mean())
        res["echeances"][n] = e
    # monotonie du cumul total dans le temps (intervalles décroissants de tp)
    mono = {}
    for n in range(2, 7):
        a, b = c.champ(A, "tp", n - 1, 0).astype(np.float64), c.champ(A, "tp", n, 0).astype(np.float64)
        d = (b - a)[np.isfinite(a) & np.isfinite(b)]
        mono[n] = {"min_mm": float(d.min()), "n_decroissants_lt_-1e-6": int((d < -1e-6).sum())}
    res["monotonie_tp"] = mono
    return res


if __name__ == "__main__":
    import json
    r = mesurer()
    if r is None:
        print("GRIB AROME ou eccodes indisponible")
        sys.exit(0)
    c.ecrire_json("precip_tp_vs_somme.json", r)
    print(json.dumps(r, indent=1))
