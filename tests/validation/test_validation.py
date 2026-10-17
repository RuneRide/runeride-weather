# SPDX-License-Identifier: EUPL-1.2
"""Tests de validation croisée. Lancer :
    PYTHONPATH=<scratchpad>/pylib python3 -m unittest discover -s tests/validation -v
Les tests qui exigent des GRIB (AROME, ARPEGE) ou eccodes sont IGNORÉS proprement s'ils sont absents.
Seuils : choisis d'après les mesures du 17/10/2026 avec une marge (voir docs/poc-j1/validation-croisee.md) ;
ils détectent une inversion de signe, une permutation u/v ou un facteur d'unité, pas une dérive fine.
"""
import functools
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
import commun as c  # noqa: E402
import controle_arpege  # noqa: E402
import controle_plausibilite  # noqa: E402
import controle_precip  # noqa: E402
import controle_vent  # noqa: E402


@functools.lru_cache(maxsize=None)
def _vent():
    return controle_vent.mesurer()


@functools.lru_cache(maxsize=None)
def _arpege():
    return controle_arpege.mesurer()


@functools.lru_cache(maxsize=None)
def _precip():
    return controle_precip.mesurer()


@functools.lru_cache(maxsize=None)
def _plausi(modele):
    return controle_plausibilite.mesurer(modele)


class TestDirectionVent(unittest.TestCase):
    def setUp(self):
        self.r = _vent()
        if self.r is None or not self.r["echeances"]:
            self.skipTest("GRIB AROME SP1, canon ou eccodes absent")

    def test_fonctions_pures(self):
        # vent d'ouest (u>0, v=0) : vient de 270 ; vent du sud (v>0) : vient de 180 ; vent du nord (v<0) : 0 ; d'est (u<0) : 90
        import numpy as np
        d = controle_vent.dir_meteo(np.array([5.0, 0.0, 0.0, -5.0]), np.array([0.0, 5.0, -5.0, 0.0]))
        self.assertTrue(np.allclose(d, [270.0, 180.0, 0.0, 90.0]))

    def test_vitesse_egale_hypot(self):
        for n, e in self.r["echeances"].items():
            self.assertLess(e["vitesse_ecart_max_ms"], 0.02, n)

    def test_direction_meteo_convention(self):
        for n, e in self.r["echeances"].items():
            self.assertLess(e["direction_ecart_max_deg_vitesse_ge_2"], 0.5, n)
            self.assertLess(abs(e["direction_biais_signe_deg_vitesse_ge_2"]), 0.01, n)
            # les conventions alternatives doivent être rejetées nettement
            self.assertGreater(e["alt_signes_opposes_ecart_moy_deg"], 170.0, n)
            self.assertGreater(e["alt_u_v_permutes_ecart_moy_deg"], 30.0, n)

    def test_equilibre_geostrophique_signes(self):
        g = controle_vent.geostrophie()
        if not g:
            self.skipTest("mslp canonique absente")
        for n, e in g.items():
            self.assertGreater(e["corr_u_vs_ug"], 0.2, n)   # négatif si le signe de u était inversé
            self.assertGreater(e["corr_v_vs_vg"], 0.2, n)

    def test_canon_egal_grib(self):
        for n, e in self.r["echeances"].items():
            self.assertEqual(e["canon_vs_grib_u_ecart_max"], 0.0, n)
            self.assertEqual(e["canon_vs_grib_v_ecart_max"], 0.0, n)


class TestAromeArpege(unittest.TestCase):
    def setUp(self):
        self.r = _arpege()
        if self.r is None:
            self.skipTest("GRIB ARPEGE SP1 (ou AROME SP1) absent : téléchargement non fait")

    def test_alignement_des_grilles(self):
        self.assertLess(self.r["alignement_max_deg"], 1e-9)

    def test_vent(self):
        for n, e in self.r["echeances"].items():
            for k in ("u", "v"):
                self.assertGreater(e[k]["corr"], 0.9, (n, k))
                self.assertLess(abs(e[k]["biais"]), 0.5, (n, k))
                self.assertLess(e[k]["rmse"], 1.5, (n, k))
            self.assertLess(abs(e["vitesse"]["biais"]), 0.3, n)

    def test_mslp_pas_de_facteur_unite(self):
        for n, e in self.r["echeances"].items():
            self.assertGreater(e["mslp_Pa"]["corr"], 0.995, n)
            self.assertLess(abs(e["mslp_Pa"]["biais"]), 100.0, n)  # 1 hPa

    def test_t2m(self):
        for n, e in self.r["echeances"].items():
            self.assertLess(abs(e["t2m_K"]["biais"]), 1.0, n)
            self.assertGreater(e["t2m_K"]["corr"], 0.95, n)

    def test_precip(self):
        for n, e in self.r["echeances"].items():
            p = e["tp_mm"]
            self.assertGreater(p["rapport_sommes_AROME_sur_ARPEGE"], 0.5, n)
            self.assertLess(p["rapport_sommes_AROME_sur_ARPEGE"], 2.0, n)
            self.assertGreater(p["corr"], 0.3, n)

    def test_arpege_convention_direction(self):
        self.assertLess(self.r["arpege_10u10v_vs_10wdir"]["direction_ecart_max_deg"], 1.0)


class TestPrecip(unittest.TestCase):
    def setUp(self):
        self.r = _precip()
        if self.r is None:
            self.skipTest("GRIB AROME SP1/SP2 ou eccodes absent")

    def test_tp_egal_somme_des_especes(self):
        for n, e in self.r["echeances"].items():
            # un pas de codage de tp vaut 2^-10 mm (~0,00098) : l'écart ne doit pas le dépasser
            self.assertLess(e["tp_moins_somme_ecart_max_abs_mm"], 1.5e-3, n)
            self.assertEqual(e["points_ecart_gt_1e-3_mm"], 0, n)

    def test_graupel_est_une_espece_a_part(self):
        e = self.r["echeances"].get(6, {})
        if "sur_points_graupel" not in e:
            self.skipTest("pas de graupel mesurable dans ce jeu")
        g = e["sur_points_graupel"]
        self.assertGreater(g["ecart_max_tp_moins_tirf_mm"], 10 * g["ecart_max_tp_moins_somme3_mm"])


class TestPlausibilite(unittest.TestCase):
    def _modele(self, modele):
        r = _plausi(modele)
        if r is None:
            self.skipTest(f"champs canoniques {modele} absents")
        return r

    def check(self, modele):
        r = self._modele(modele)
        for nom, par_ech in r["bornes"].items():
            for n, e in par_ech.items():
                if nom == "snow_fraction":   # arrondi flottant : -4e-10 mesuré en HD éch. 6
                    self.assertGreater(e["min"], -1e-6, (nom, n))
                    self.assertLess(e["max"], 1 + 1e-6, (nom, n))
                else:
                    self.assertEqual(e["n_hors_bornes"], 0, (nom, n, e.get("exemples")))
                self.assertEqual(e.get("n_hors_plage_suspecte", 0), 0, (nom, n))
        for n, g in r["gust_vs_vent"].items():
            self.assertLess(g["pct_gust_lt_vent"], 2.0, n)
            self.assertLess(g["pct_gust_lt_vent_moins_1ms"], 0.1, n)
        for paire, e in r["temporel"].items():
            for nom, s in e.items():
                # tolérance : 5 points hors seuil au plus sur plusieurs millions (le jeu HD en a 1)
                self.assertLessEqual(s["n_hors_seuil"], 5, (paire, nom))

    def test_arome(self):
        self.check("arome")

    def test_arome_hd(self):
        self.check("arome-hd")

    def test_humidite_magnus(self):
        h = controle_plausibilite.hr_depuis_td()
        if h is None:
            self.skipTest("GRIB AROME SP1/SP2 ou eccodes absent")
        for n, e in h.items():
            self.assertLess(e["ecart_abs_moy_pct"], 0.5, n)
            self.assertEqual(e["td_sup_t_n"], 0, n)


if __name__ == "__main__":
    unittest.main()
