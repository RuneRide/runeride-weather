# SPDX-License-Identifier: EUPL-1.2
"""Tests des profils de réduction du poids (options ; le profil `contrat` reste le défaut)."""
import os
import shutil
import tempfile
import unittest

import numpy as np

from pipeline.tiling import build as B
from pipeline.tiling import encode as E
from pipeline.tiling import profiles as P
from pipeline.tiling.decode import TileFile
from pipeline.tiling.mercator import Grid, pixel_lonlat, tiles_covering

RACINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(RACINE, "fixtures", "hd_step03_crop.npz")
RNG = np.random.default_rng(99)


class TestEncodages(unittest.TestCase):
    def test_defaut_inchange(self):
        self.assertIs(P.get("contrat"), P.CONTRAT)
        v = RNG.uniform(233.15, 333.15, 1000)
        np.testing.assert_array_equal(E.encode_scalar8(v, "t2m"), E.encode_scalar8(v, "t2m", 254))

    def test_wind8_aller_retour_et_nodata(self):
        for vmax in (64.0, 48.0):
            u = RNG.uniform(-vmax, vmax, (60, 60)).astype(np.float32)
            v = RNG.uniform(-vmax, vmax, (60, 60)).astype(np.float32)
            u[0, 0] = np.nan
            rgb = E.encode_wind8(u, v, vmax)
            self.assertEqual(tuple(rgb[0, 0]), (0, 0, 0))
            self.assertTrue((rgb[1:, 1:, 0] >= 1).all())
            du, dv = E.decode_wind8(rgb, vmax)
            self.assertTrue(np.isnan(du[0, 0]))
            ok = np.isfinite(u)
            self.assertLessEqual(np.abs(du - u)[ok].max(), E.wind8_step(vmax) / 2 + 1e-5)
            self.assertLessEqual(np.abs(dv - v)[ok].max(), E.wind8_step(vmax) / 2 + 1e-5)
            z = E.encode_wind8(np.zeros((1, 1)), np.zeros((1, 1)), vmax)
            self.assertTrue(z.any())          # vent nul != pas de donnée

    def test_wind12_grossier_meme_format(self):
        u = RNG.uniform(-60, 60, (80, 80)).astype(np.float32)
        v = RNG.uniform(-60, 60, (80, 80)).astype(np.float32)
        for k in (2, 3):
            du, dv = E.decode_wind12(E.encode_wind12(u, v, k))
            self.assertLessEqual(np.abs(du - u).max(), E.WIND_STEP * 2 ** k / 2 + E.WIND_STEP)
            cu, _ = E.unpack_wind12(E.encode_wind12(u, v, k))
            self.assertTrue(((cu - 1) % (2 ** k) == 0).all())

    def test_scalar_niveaux_reduits(self):
        for var, (vmin, vmax, scale, _) in E.SCALAR8.items():
            for lv in (126, 100):
                v = RNG.uniform(vmin, vmax, 50000)
                c = E.encode_scalar8(v, var, lv)
                self.assertLessEqual(c.max(), lv + 1)
                self.assertGreaterEqual(c.min(), 1)
                err = np.abs(E.decode_scalar8(c, var, lv) - v).max()
                self.assertLessEqual(err, E.scalar_step(var, lv) / 2 * 1.01 + 1e-4, (var, lv))
        self.assertEqual(E.encode_scalar8(np.array([np.nan]), "t2m", 100)[0], 0)


class TestReduit2(unittest.TestCase):
    def test_metadonnees_et_pas(self):
        for nom, t0 in (("reduit2", 243.15), ("reduit2-ete", 258.15), ("reduit2-hiver", 233.15)):
            pr = P.get(nom)
            self.assertEqual(pr.levels["t2m"], 254)
            self.assertEqual(pr.ranges["t2m"], (t0, t0 + 63.5))
            self.assertAlmostEqual(E.scalar_step("t2m", 254, pr.ranges["t2m"]), 0.25)
            self.assertAlmostEqual(E.scalar_step("gust10", 254, pr.ranges["gust10"]), 0.25)
            self.assertEqual(pr.wind_mode, "wind8")
            self.assertEqual(pr.zmax["t2m"], 7)

    def test_aller_retour_plage_resserree_et_ecretage(self):
        rg = (243.15, 306.65)
        v = RNG.uniform(rg[0], rg[1], 100000)
        c = E.encode_scalar8(v, "t2m", 254, rg)
        self.assertLessEqual(c.max(), 255)
        err = np.abs(E.decode_scalar8(c, "t2m", 254, rg) - v).max()
        self.assertLessEqual(err, 0.125 + 1e-3)
        c = E.encode_scalar8(np.array([200.0, 400.0, np.nan]), "t2m", 254, rg)
        self.assertEqual(list(c), [1, 255, 0])      # écrêtage aux bornes, jamais 0

    def test_construction_reduit2_relecture(self):
        d = tempfile.mkdtemp(prefix="tuilage_r2_")
        try:
            src = dict(np.load(FIXTURE))
            grid = Grid(src["lat"], src["lon"])
            rep = B.build(FIXTURE, d, zooms=(6, 7), workers=4, variables=["t2m", "gust10"], profil=P.get("reduit2"))
            self.assertTrue(all(r["pixels_ecretes"] == 0 for r in rep))
            for var in ("t2m", "gust10"):
                with TileFile(os.path.join(d, "arome-hd", "2026-10-17T06Z", var, "03.pmtiles")) as t:
                    self.assertAlmostEqual(t.meta["quant_step"], 0.25)
                    self.assertEqual(t.meta["levels"], 254)
                    for x, y in tiles_covering(*grid.bounds, 7):
                        lon, lat = pixel_lonlat(7, x, y)
                        r = grid.sample(src[var], lon, lat)
                        g = t.read(7, x, y)
                        ok = np.isfinite(r)
                        np.testing.assert_array_equal(np.isfinite(g), ok)
                        self.assertLessEqual(np.abs(g - r)[ok].max(), 0.125 + 1e-3)
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestConstruction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="tuilage_prof_")
        cls.src = dict(np.load(FIXTURE))
        cls.grid = Grid(cls.src["lat"], cls.src["lon"])
        prof = P.get("reduit")
        B.build(FIXTURE, cls.tmp, zooms=(6, 8), workers=4, profil=prof)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def chemin(self, var):
        return os.path.join(self.tmp, "arome-hd", "2026-10-17T06Z", var, "03.pmtiles")

    def test_plafond_de_zoom_par_variable(self):
        with TileFile(self.chemin("t2m")) as t:
            self.assertEqual(t.header["max_zoom"], 7)
            self.assertEqual(t.meta["profile"], "reduit")
            self.assertEqual(t.meta["levels"], 200)
        with TileFile(self.chemin("precip_rate")) as t:
            self.assertEqual(t.header["max_zoom"], 8)
        with TileFile(self.chemin("wind10")) as t:
            self.assertEqual((t.meta["encoding"], t.meta["max"], t.header["max_zoom"]), ("wind8", 48.0, 7))

    def test_valeurs_reduit_dans_le_demi_pas(self):
        z = 7
        for var in ("t2m", "wind10"):
            with TileFile(self.chemin(var)) as t:
                for x, y in tiles_covering(*self.grid.bounds, z):
                    lon, lat = pixel_lonlat(z, x, y)
                    if var == "wind10":
                        got = t.read(z, x, y)
                        ref = [self.grid.sample(self.src["wind10_u"], lon, lat), self.grid.sample(self.src["wind10_v"], lon, lat)]
                        step = t.meta["quant_step"]
                        for g, r in zip(got, ref):
                            ok = np.isfinite(r)
                            np.testing.assert_array_equal(np.isfinite(g), ok)
                            self.assertLessEqual(np.abs(g - r)[ok].max(), step / 2 + 1e-5)
                    else:
                        g = t.read(z, x, y)
                        r = self.grid.sample(self.src[var], lon, lat)
                        ok = np.isfinite(r)
                        np.testing.assert_array_equal(np.isfinite(g), ok)
                        self.assertLessEqual(np.abs(g - r)[ok].max(), t.meta["quant_step"] / 2 * 1.01)

    def test_sans_bordure_et_decodeur(self):
        d = tempfile.mkdtemp(prefix="tuilage_nb_")
        try:
            B.build(FIXTURE, d, zooms=(7, 8), workers=4, variables=["t2m"], profil=P.get("sans-bordure-z8"))
            p = os.path.join(d, "arome-hd", "2026-10-17T06Z", "t2m", "03.pmtiles")
            with TileFile(p) as t:
                x, y = tiles_covering(*self.grid.bounds, 8)[10]
                self.assertEqual(t.read(8, x, y).shape, (256, 256))
                x7, y7 = tiles_covering(*self.grid.bounds, 7)[3]
                self.assertEqual(t.read(7, x7, y7).shape, (258, 258))
                # l'échantillonnage client reste défini aux bords (bord répété)
                lon, lat = pixel_lonlat(8, x, y)
                v = t.sample(float(lon[0]), float(lat[100]), 8)
                self.assertTrue(np.isfinite(v) or np.isnan(v))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_lossy_jamais_sur_tuile_a_trous(self):
        d = tempfile.mkdtemp(prefix="tuilage_ly_")
        try:
            B.build(FIXTURE, d, zooms=(8, 8), workers=4, variables=["t2m"], profil=P.get("lossy-t2m"))
            p = os.path.join(d, "arome-hd", "2026-10-17T06Z", "t2m", "03.pmtiles")
            from PIL import Image
            import io
            with TileFile(p) as t:
                lossy = lossless = 0
                for x, y in tiles_covering(*self.grid.bounds, 8):
                    raw = t.raw(8, x, y)
                    if raw is None:
                        continue
                    arr = E.from_image(raw)[..., 0]
                    est_avec_trou = (arr == 0).any()
                    info = raw[12:16]             # "VP8 " (avec perte) ou "VP8L" (sans perte)
                    if est_avec_trou:
                        self.assertEqual(info, b"VP8L")
                        lossless += 1
                    else:
                        lossy += int(info == b"VP8 ")
                self.assertGreater(lossy, 0)
                self.assertGreater(lossless, 0)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
