# SPDX-License-Identifier: EUPL-1.2
"""Tests de la brique ingestion. Lancer : PYTHONPATH=<pylib eccodes> python3 -m unittest discover -s tests/ingest -v"""
import json
import os
import sys
import tempfile
import unittest

import numpy as np

RACINE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, RACINE)

from pipeline.ingest import canon, download, grib, verifier  # noqa: E402

try:
    import eccodes
except ImportError:  # eccodes absent : les tests GRIB sont ignorés
    eccodes = None

NAN = np.nan
f32 = np.float32


class TestMasque(unittest.TestCase):
    def test_9999_devient_nan(self):
        v = grib.masquer([1.0, 9999.0, -3.5, 9999.0])
        self.assertEqual(v.dtype, np.float32)
        self.assertTrue(np.array_equal(np.isnan(v), [False, True, False, True]))
        self.assertEqual(v[0], 1.0)
        self.assertEqual(v[2], f32(-3.5))

    def test_valeur_proche_non_masquee(self):
        self.assertFalse(np.isnan(grib.masquer([9998.99])).any())


class TestLongitude(unittest.TestCase):
    def test_348_16_vers_moins12_16(self):
        lon = 348.0 + 0.01 * np.arange(2801)       # 348 -> 376 dans le GRIB, soit 348..360 puis 0..16
        conv, perm = grib.longitudes_vers_180(lon)
        self.assertIsNone(perm)
        self.assertAlmostEqual(conv[0], -12.0, places=9)
        self.assertAlmostEqual(conv[-1], 16.0, places=9)
        self.assertTrue(np.all(np.diff(conv) > 0))

    def test_traversee_antimeridien_donne_une_permutation(self):
        lon = np.array([170.0, 175.0, 180.0, 185.0, 190.0])
        conv, perm = grib.longitudes_vers_180(lon)
        self.assertTrue(np.all(np.diff(conv) > 0))
        self.assertEqual(sorted(perm.tolist()), [0, 1, 2, 3, 4])
        self.assertAlmostEqual(conv[0], -180.0)


class TestCumuls(unittest.TestCase):
    def test_debit_difference_et_plancher(self):
        n = np.array([[1.0, 2.0, NAN, 0.5]], dtype=f32)
        p = np.array([[0.5, 2.5, 1.0, 0.5]], dtype=f32)
        d = canon.debit_precip(n, p)
        self.assertEqual(d[0, 0], 0.5)
        self.assertEqual(d[0, 1], 0.0)     # cumul qui décroît (arrondi GRIB) : plancher à 0
        self.assertTrue(np.isnan(d[0, 2]))
        self.assertEqual(d[0, 3], 0.0)

    def _champs(self, tirf, tsn, tgrp):
        a = lambda x: np.full((2, 2), x, dtype=f32)
        return {"tirf": a(tirf), "tsnowp": a(tsn), "tgrp": a(tgrp)}

    def test_debit_somme_trois_cumuls(self):
        out = canon.construire("arome-hd", 3, self._champs(3.0, 1.0, 0.5), self._champs(1.0, 0.5, 0.25))
        self.assertAlmostEqual(float(out["precip_rate"][0, 0]), 2.75, places=6)

    def test_echeance_1_cumul_precedent_nul(self):
        out = canon.construire("arome-hd", 1, self._champs(2.0, 0.0, 0.0), None)
        self.assertAlmostEqual(float(out["precip_rate"][0, 0]), 2.0)

    def test_echeance_0_sans_debit_ni_rafale_ni_nuage(self):
        champs = {"2t": np.ones((2, 2), f32), "10u": np.ones((2, 2), f32), "10v": np.ones((2, 2), f32),
                  "max_10efg": np.ones((2, 2), f32), "max_10nfg": np.ones((2, 2), f32)}
        out = canon.construire("arome-hd", 0, champs, None)
        self.assertEqual(sorted(out), ["t2m", "wind10_u", "wind10_v"])

    def test_pas_de_prev_apres_echeance_1_pas_de_debit(self):
        out = canon.construire("arome-hd", 4, self._champs(1.0, 0, 0), None)
        self.assertNotIn("precip_rate", out)

    def test_fraction_neige(self):
        d_tot = np.array([[1.0, 0.04, 2.0, NAN]], dtype=f32)
        d_sn = np.array([[0.5, 0.04, 2.0, 1.0]], dtype=f32)
        f = canon.fraction_neige(d_sn, d_tot)
        self.assertAlmostEqual(float(f[0, 0]), 0.5)
        self.assertEqual(float(f[0, 1]), 0.0)    # sous le seuil de 0,05 mm
        self.assertEqual(float(f[0, 2]), 1.0)
        self.assertTrue(np.isnan(f[0, 3]))

    def test_jonction_groupes_025(self):
        """L'échéance 7 (groupe 07H12H) exige l'échéance 6 (groupe 00H06H)."""
        self.assertEqual(download.groupe_025(6), "00H06H")
        self.assertEqual(download.groupe_025(7), "07H12H")
        self.assertEqual(download.groupe_025(12), "07H12H")
        self.assertEqual(download.groupe_025(49), "49H51H")
        self.assertEqual(download.groupe_025(51), "49H51H")
        besoins = download.fichiers_requis("arome", [7], "2026-10-17T06:00:00Z")
        self.assertEqual(sorted({lab for _, lab in besoins}), ["00H06H", "07H12H"])
        self.assertEqual(sorted({p for p, _ in besoins}), ["SP1", "SP2"])
        besoins = download.fichiers_requis("arome", [8], "2026-10-17T06:00:00Z")
        self.assertEqual({lab for _, lab in besoins}, {"07H12H"})
        # débit à l'échéance 7 calculé avec le cumul de l'échéance 6
        out = canon.construire("arome", 7, self._champs(5.0, 0, 0), self._champs(4.0, 0, 0))
        self.assertAlmostEqual(float(out["precip_rate"][0, 0]), 1.0)

    def test_fichiers_requis_hd_inclut_n_moins_1(self):
        besoins = download.fichiers_requis("arome-hd", [3], "2026-10-17T06:00:00Z")
        self.assertEqual(sorted(besoins), [("SP1", "02H"), ("SP1", "03H"), ("SP2", "02H"), ("SP2", "03H")])

    def test_fenetre_inattendue_refusee(self):
        base = {"shortName": "tirf", "step": 3, "stepType": "accum", "startStep": 0, "endStep": 3}
        canon.verifier_fenetre(base)
        with self.assertRaises(ValueError):
            canon.verifier_fenetre({**base, "startStep": 2})
        with self.assertRaises(ValueError):
            canon.verifier_fenetre({"shortName": "max_i10fg", "step": 3, "stepType": "max", "startStep": 0, "endStep": 3})
        canon.verifier_fenetre({"shortName": "max_i10fg", "step": 3, "stepType": "max", "startStep": 2, "endStep": 3})


class TestRafales(unittest.TestCase):
    def test_hd_hypot_avec_composantes_negatives(self):
        e = np.array([[-3.0, 3.0, 0.0]], dtype=f32)
        n = np.array([[-4.0, 4.0, -2.0]], dtype=f32)
        r = canon.rafale_hd(e, n)
        self.assertTrue(np.allclose(r, [[5.0, 5.0, 2.0]]))

    def test_gust10_selon_le_modele(self):
        a = lambda x: np.full((2, 2), x, dtype=f32)
        hd = canon.construire("arome-hd", 2, {"max_10efg": a(-3), "max_10nfg": a(-4)}, None)
        self.assertAlmostEqual(float(hd["gust10"][0, 0]), 5.0, places=5)
        ar = canon.construire("arome", 2, {"max_i10fg": a(7.5), "max_10efg": a(-3), "max_10nfg": a(-4)}, None)
        self.assertEqual(float(ar["gust10"][0, 0]), 7.5)


class TestNebulosite(unittest.TestCase):
    def test_valeurs_connues(self):
        v = lambda *x: np.array([x], dtype=f32)
        self.assertAlmostEqual(float(canon.nebulosite_totale(v(0), v(0), v(0))[0, 0]), 0.0)
        self.assertAlmostEqual(float(canon.nebulosite_totale(v(100), v(0), v(0))[0, 0]), 100.0)
        self.assertAlmostEqual(float(canon.nebulosite_totale(v(50), v(50), v(0))[0, 0]), 75.0, places=4)
        self.assertAlmostEqual(float(canon.nebulosite_totale(v(50), v(50), v(50))[0, 0]), 87.5, places=4)

    def test_propagation_nan_et_dtype(self):
        v = lambda *x: np.array([x], dtype=f32)
        r = canon.nebulosite_totale(v(10, NAN), v(10, 10), v(10, 10))
        self.assertEqual(r.dtype, np.float32)
        self.assertTrue(np.isnan(r[0, 1]))

    def test_total_au_moins_la_couche_max(self):
        rng = np.random.default_rng(1)
        l, m, h = (rng.uniform(0, 100, (50, 50)).astype(f32) for _ in range(3))
        t = canon.nebulosite_totale(l, m, h)
        self.assertTrue(np.all(t >= np.maximum(np.maximum(l, m), h) - 1e-3))

    def test_borne_nebulosite(self):
        a = lambda x: np.array([[x]], dtype=f32)
        champs = {"lcc": a(100.0033), "mcc": a(0.0), "hcc": a(0.0)}
        brut = canon.construire("arome-hd", 1, champs, None, bornes=False)["cloud_total"]
        borne = canon.construire("arome-hd", 1, champs, None)["cloud_total"]
        self.assertLessEqual(float(borne.max()), 100.0)
        self.assertGreaterEqual(float(borne.min()), 0.0)
        self.assertEqual(borne.dtype, np.float32)
        self.assertGreater(float(brut.max()), 100.0)

    def test_borne_humidite(self):
        champs = {"2r": np.array([[-1.0, 50.0, 100.0166]], dtype=f32)}
        self.assertEqual(float(canon.construire("arome-hd", 0, champs, None)["rh2m"].max()), 100.0)
        self.assertGreater(float(canon.construire("arome-hd", 0, champs, None, bornes=False)["rh2m"].max()), 100.0)


DONNEES = os.path.join(RACINE, "tests", "ingest", "data")


def _champs(d, n):
    return {k: d[f"{k}_{n}"] for k in ("tirf", "tsnowp", "tgrp", "tp")}


class TestRaccordGroupes025(unittest.TestCase):
    """Extrait réel (Alpes du Nord, AROME 0,025°) autour du raccord 6 -> 7 (groupes 00H06H et 07H12H)."""

    @classmethod
    def setUpClass(cls):
        cls.d = np.load(os.path.join(DONNEES, "raccord_025.npz"))

    def test_debit_a_7_exact_avec_cumul_du_groupe_precedent(self):
        d = self.d
        out = canon.construire("arome", 7, _champs(d, 7), _champs(d, 6))
        self.assertTrue(np.array_equal(out["precip_rate"], d["attendu_precip_rate_7"], equal_nan=True))
        self.assertTrue(np.array_equal(out["snow_fraction"], d["attendu_snow_fraction_7"], equal_nan=True))
        self.assertGreater(float(np.nanmax(out["precip_rate"])), 1.0)   # l'extrait contient de la vraie pluie

    def test_continuite_du_debit_au_raccord(self):
        d = self.d
        m = {n: float(np.nanmean(canon.construire("arome", n, _champs(d, n), _champs(d, n - 1))["precip_rate"])) for n in (5, 6, 7, 8)}
        self.assertLess(m[7], 3 * max(m[6], m[8]))
        self.assertGreater(m[7], min(m[6], m[8]) / 3)
        # contre-épreuve : si l'on oubliait le cumul précédent, le débit à 7 serait le cumul entier depuis 0
        oublie = canon.construire("arome", 7, _champs(d, 7), None)
        self.assertNotIn("precip_rate", oublie)
        entier = d["tirf_7"] + d["tsnowp_7"] + d["tgrp_7"]
        ecart = float(np.nanmax(np.abs(entier - d["attendu_precip_rate_7"])))
        self.assertGreater(ecart, 1.0)    # le cumul depuis 0 ne se confond pas avec le débit

    def test_somme_des_debits_egale_cumul(self):
        d = self.d
        somme = sum(canon.construire("arome", n, _champs(d, n), _champs(d, n - 1) if n > 1 else None)["precip_rate"].astype(np.float64)
                    for n in range(1, 9))
        total = (d["tirf_8"] + d["tsnowp_8"] + d["tgrp_8"]).astype(np.float64)
        self.assertLess(float(np.nanmax(np.abs(somme - total))), 0.01)    # écart = plancher à 0 des décréments de codage

    def test_tp_recoupe_la_somme(self):
        d = self.d
        r = canon.construire("arome", 7, _champs(d, 7), _champs(d, 6))["precip_rate"]
        r_tp = canon.debit_precip(d["tp_7"], d["tp_6"])
        self.assertLess(float(np.nanmax(np.abs(r - r_tp))), 0.005)

    def test_dernier_groupe_49H51H(self):
        run = "2026-10-17T06:00:00Z"
        self.assertEqual({lab for _, lab in download.fichiers_requis("arome", [49, 50, 51], run)}, {"43H48H", "49H51H"})
        self.assertEqual({lab for _, lab in download.fichiers_requis("arome", [50, 51], run)}, {"49H51H"})
        self.assertEqual(download.bornes_groupe("49H51H"), (49, 51))
        for n in (7, 13, 19, 25, 31, 37, 43, 49):    # chaque raccord charge le groupe précédent
            labs = {lab for _, lab in download.fichiers_requis("arome", [n], run)}
            self.assertEqual(len(labs), 2, n)


class TestNeigeReelle(unittest.TestCase):
    """Extrait réel (Alpes du Nord, échéances 17-21, neige à haute altitude) pour snow_fraction."""

    @classmethod
    def setUpClass(cls):
        cls.d = np.load(os.path.join(DONNEES, "neige_025.npz"))

    def _sortie(self, n):
        return canon.construire("arome", n, _champs(self.d, n), _champs(self.d, n - 1))

    def test_reproduit_les_valeurs_attendues(self):
        for n in range(18, 22):
            out = self._sortie(n)
            self.assertTrue(np.array_equal(out["snow_fraction"], self.d[f"attendu_snow_fraction_{n}"], equal_nan=True))
            self.assertTrue(np.array_equal(out["precip_rate"], self.d[f"attendu_precip_rate_{n}"], equal_nan=True))

    def test_pas_nulle_partout_et_dans_0_1(self):
        for n in range(18, 22):
            sf = self._sortie(n)["snow_fraction"]
            self.assertGreater(float(np.nanmax(sf)), 0.9)
            self.assertGreater(int((sf > 0.5).sum()), 10)
            self.assertGreaterEqual(float(np.nanmin(sf)), 0.0)
            self.assertLessEqual(float(np.nanmax(sf)), 1.0)

    def test_neige_seulement_par_temperature_basse(self):
        for n in range(18, 22):
            out = self._sortie(n)
            neige = (out["snow_fraction"] > 0.5) & (out["precip_rate"] > 0.05)
            self.assertTrue(neige.any())
            self.assertLess(float(np.nanmax(self.d[f"t2m_{n}"][neige] - 273.15)), 2.0)

    def test_zero_la_ou_il_ne_precipite_pas(self):
        out = self._sortie(20)
        sec = out["precip_rate"] <= 0.05
        self.assertTrue(np.all(out["snow_fraction"][sec] == 0.0))

    def test_borne_0_1_malgre_decrements_de_codage(self):
        a = lambda x: np.array([[x]], dtype=f32)
        # Δneige > Δtotal (cumuls arrondis différemment) et Δneige < 0
        self.assertEqual(float(canon.fraction_neige(a(1.2), a(1.0))[0, 0]), 1.0)
        self.assertEqual(float(canon.fraction_neige(a(-0.001), a(1.0))[0, 0]), 0.0)


@unittest.skipIf(eccodes is None, "eccodes indisponible")
class TestGribSynthetique(unittest.TestCase):
    """Fabrique un mini GRIB2 (grille 4 × 3, longitudes 359,9..0,2, un point manquant) et le relit."""

    def _ecrire(self, chemin, j_positif):
        h = eccodes.codes_grib_new_from_samples("regular_ll_sfc_grib2")
        cles = dict(Ni=4, Nj=3, longitudeOfFirstGridPointInDegrees=359.9, longitudeOfLastGridPointInDegrees=0.2,
                    iDirectionIncrementInDegrees=0.1, jDirectionIncrementInDegrees=0.1)
        if j_positif:
            cles.update(latitudeOfFirstGridPointInDegrees=55.2, latitudeOfLastGridPointInDegrees=55.4, jScansPositively=1)
        else:
            cles.update(latitudeOfFirstGridPointInDegrees=55.4, latitudeOfLastGridPointInDegrees=55.2)
        for k, v in cles.items():
            eccodes.codes_set(h, k, v)
        eccodes.codes_set(h, "shortName", "2t")
        eccodes.codes_set(h, "bitmapPresent", 1)
        eccodes.codes_set(h, "missingValue", 9999.0)
        eccodes.codes_set_values(h, np.array([1, 2, 3, 9999, 5, 6, 7, 8, 9, 10, 11, 12], dtype=float))
        with open(chemin, "wb") as f:
            f.write(eccodes.codes_get_message(h))
        eccodes.codes_release(h)

    def test_lecture_nord_sud(self):
        with tempfile.TemporaryDirectory() as d:
            c = os.path.join(d, "t.grib2")
            self._ecrire(c, False)
            m, lat, lon = grib.lire_fichier(c)
        self.assertTrue(np.allclose(lat, [55.4, 55.3, 55.2]))
        self.assertTrue(np.allclose(lon, [-0.1, 0.0, 0.1, 0.2]))
        v = m[0]["valeurs"]
        self.assertEqual(v.shape, (3, 4))
        self.assertTrue(np.isnan(v[0, 3]))
        self.assertEqual(int(np.isnan(v).sum()), 1)
        self.assertEqual(v[1, 0], 5.0)

    def test_balayage_sud_nord_retourne(self):
        with tempfile.TemporaryDirectory() as d:
            c = os.path.join(d, "t.grib2")
            self._ecrire(c, True)
            m, lat, _ = grib.lire_fichier(c)
        self.assertTrue(np.allclose(lat, [55.4, 55.3, 55.2]))     # toujours décroissante
        self.assertEqual(m[0]["valeurs"][2, 0], 1.0)              # la 1re ligne GRIB (sud) est en bas


def _dossiers_grib():
    dossiers = [d for d in os.environ.get("RUNERIDE_GRIB_SRC", "").split(os.pathsep) if d]
    dossiers.append(os.path.join(RACINE, "out", "grib"))
    return dossiers


def _fichiers_hd_03():
    run = "2026-10-17T06:00:00Z"
    f = {}
    for p, lab in (("SP1", "03H"), ("SP2", "03H"), ("SP2", "02H")):
        c = download.localiser("arome-hd", p, lab, run, _dossiers_grib())
        if c is None:
            return None
        f[(p, lab)] = c
    return f


@unittest.skipIf(eccodes is None, "eccodes indisponible")
class TestReproductionFixture(unittest.TestCase):
    """Reproduit fixtures/hd_step03_crop.npz (et out/hd_step03.npz s'il existe) depuis les GRIB réels."""

    @classmethod
    def setUpClass(cls):
        cls.fichiers = _fichiers_hd_03()
        if cls.fichiers is None:
            raise unittest.SkipTest("GRIB HD 03H absents (définir RUNERIDE_GRIB_SRC)")
        cls.tmp = tempfile.TemporaryDirectory()
        from pipeline.ingest import cli
        cli.traiter("arome-hd", "2026-10-17T06:00:00Z", [3], _dossiers_grib(), False, cls.tmp.name,
                    jouer=lambda *_: None)
        cls.npz = np.load(os.path.join(cls.tmp.name, "arome-hd", "2026-10-17T06Z", "03.npz"))

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "tmp", None):
            cls.tmp.cleanup()

    def _verifier(self, ref):
        res = verifier.comparer(self.npz, ref)
        self.assertTrue(res.pop("_axes_identiques"))
        for k, r in res.items():
            self.assertTrue(r["identique_bit_a_bit"], f"{k} : {r}")

    def test_extrait_exact(self):
        self._verifier(np.load(os.path.join(RACINE, "fixtures", "hd_step03_crop.npz")))

    def test_grille_complete_exacte(self):
        ref = os.path.join(RACINE, "out", "hd_step03.npz")
        if not os.path.exists(ref):
            self.skipTest("out/hd_step03.npz absent")
        self._verifier(np.load(ref))

    def test_forme_du_contrat(self):
        d = self.npz
        self.assertEqual(d["lat"].dtype, np.float32)
        self.assertTrue(np.all(np.diff(d["lat"]) < 0))
        self.assertTrue(np.all(np.diff(d["lon"]) > 0))
        self.assertGreaterEqual(float(d["lon"][0]), -180)
        meta = json.loads(str(d["meta"]))
        self.assertEqual((meta["model"], meta["step"], meta["run"]), ("arome-hd", 3, "2026-10-17T06:00:00Z"))
        for k in d.files:
            if k not in ("lat", "lon", "meta"):
                self.assertEqual(d[k].dtype, np.float32)
                self.assertEqual(d[k].shape, (len(d["lat"]), len(d["lon"])))
                self.assertFalse((d[k] == 9999).any(), f"{k} contient 9999")
                self.assertTrue(np.isnan(d[k]).any(), f"{k} devrait avoir des NaN hors domaine")


if __name__ == "__main__":
    unittest.main()
