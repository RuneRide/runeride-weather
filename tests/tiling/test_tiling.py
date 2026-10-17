# SPDX-License-Identifier: EUPL-1.2
"""Tests de la brique tuilage. Lancer : PYTHONPATH=<dossier pmtiles> python3 -m unittest discover -s tests/tiling -t ."""
import json
import math
import os
import shutil
import tempfile
import unittest

import numpy as np

from pipeline.tiling import build as B
from pipeline.tiling import encode as E
from pipeline.tiling.decode import TileFile
from pipeline.tiling.mercator import Grid, lonlat_to_tile, pixel_lonlat, tile_lon_lat, tiles_covering

RACINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE = os.path.join(RACINE, "fixtures", "hd_step03_crop.npz")
RNG = np.random.default_rng(12345)


def osm_tile(lon, lat, z):
    """Formule de référence du wiki OSM (slippy map), écrite indépendamment du paquet."""
    n = 2.0 ** z
    xt = int((lon + 180.0) / 360.0 * n)
    yt = int((1.0 - math.log(math.tan(math.radians(lat)) + 1 / math.cos(math.radians(lat)))
              / math.pi) / 2.0 * n)
    return xt, yt


class TestQuantification(unittest.TestCase):
    def test_aller_retour_scalar8(self):
        for var, (vmin, vmax, scale, _) in E.SCALAR8.items():
            v = RNG.uniform(vmin, vmax, 200000)
            v[:3] = (vmin, vmax, (vmin + vmax) / 2)
            back = E.decode_scalar8(E.encode_scalar8(v, var), var).astype(np.float64)
            err = np.abs(back - v)
            if scale == "linear":
                borne = (vmax - vmin) / 254 / 2
            else:   # racine carrée : demi-pas maximal en haut de plage
                borne = vmax * (1 - (253.5 / 254) ** 2)
            self.assertLessEqual(err.max(), borne * 1.001 + 1e-4, var)
            self.assertLessEqual(err.max(), E.scalar_step(var) / 2 * 1.01 + 1e-4, var)

    def test_nan_donne_code_zero_et_valide_jamais_zero(self):
        for var, (vmin, vmax, _, _) in E.SCALAR8.items():
            v = np.array([np.nan, vmin, vmax, np.inf, 0.5 * (vmin + vmax)], dtype=np.float32)
            c = E.encode_scalar8(v, var)
            self.assertEqual(c[0], 0)
            self.assertEqual(c[3], 0)
            self.assertTrue((c[[1, 2, 4]] >= 1).all())
            self.assertEqual(c[1], 1)
            self.assertEqual(c[2], 255)
            d = E.decode_scalar8(c, var)
            self.assertTrue(np.isnan(d[0]) and np.isnan(d[3]))

    def test_hors_plage_borne(self):
        c = E.encode_scalar8(np.array([-5.0, 1e9]), "gust10")
        self.assertEqual(list(c), [1, 255])

    def test_wind12_empaquetage_exact(self):
        cu = RNG.integers(1, 4096, 100000)
        cv = RNG.integers(1, 4096, 100000)
        cu[:4] = (1, 4095, 1, 4095)
        cv[:4] = (1, 4095, 4095, 1)
        rgb = np.stack([cu >> 4, ((cu & 15) << 4) | (cv >> 8), cv & 255], -1).astype(np.uint8)
        a, b = E.unpack_wind12(rgb)
        np.testing.assert_array_equal(a, cu)
        np.testing.assert_array_equal(b, cv)

    def test_wind12_aller_retour_et_nodata(self):
        u = RNG.uniform(-64, 64, (50, 50)).astype(np.float32)
        v = RNG.uniform(-64, 64, (50, 50)).astype(np.float32)
        u[0, 0] = np.nan
        v[1, 1] = np.nan
        rgb = E.encode_wind12(u, v)
        self.assertEqual(rgb.dtype, np.uint8)
        self.assertEqual(tuple(rgb[0, 0]), (0, 0, 0))
        self.assertEqual(tuple(rgb[1, 1]), (0, 0, 0))
        du, dv = E.decode_wind12(rgb)
        self.assertTrue(np.isnan(du[0, 0]) and np.isnan(dv[1, 1]) and np.isnan(du[1, 1]))
        ok = np.isfinite(u) & np.isfinite(v)
        self.assertLessEqual(np.abs(du - u)[ok].max(), E.WIND_STEP / 2 + 1e-5)
        self.assertLessEqual(np.abs(dv - v)[ok].max(), E.WIND_STEP / 2 + 1e-5)
        # le vent nul n'est pas « pas de donnée »
        z = E.encode_wind12(np.zeros((1, 1)), np.zeros((1, 1)))
        self.assertTrue(z.any())

    def test_webp_sans_perte_sans_alpha(self):
        g = RNG.integers(0, 256, (258, 258)).astype(np.uint8)
        np.testing.assert_array_equal(E.from_image(E.to_webp(g))[..., 0], g)
        rgb = RNG.integers(0, 256, (258, 258, 3)).astype(np.uint8)
        rgb[0, 0] = 0
        out = E.from_image(E.to_webp(rgb))
        self.assertEqual(out.shape, (258, 258, 3))
        np.testing.assert_array_equal(out, rgb)


class TestPositions(unittest.TestCase):
    def test_formule_standard(self):
        for z in (3, 5, 7, 8, 10):
            for _ in range(500):
                lon, lat = RNG.uniform(-179.9, 179.9), RNG.uniform(-84, 84)
                self.assertEqual(lonlat_to_tile(lon, lat, z), osm_tile(lon, lat, z))
        self.assertEqual(lonlat_to_tile(2.3522, 48.8566, 8), (129, 88))   # Paris

    def test_coin_nord_ouest_et_pixels(self):
        for z, x, y in ((5, 15, 11), (8, 130, 93), (3, 4, 2)):
            lon, lat = tile_lon_lat(z, x, y)
            self.assertEqual(lonlat_to_tile(lon + 1e-9, lat - 1e-9, z), (x, y))
            lons, lats = pixel_lonlat(z, x, y, border=0)
            self.assertEqual(len(lons), 256)
            self.assertAlmostEqual(lons[0], lon + (tile_lon_lat(z, x + 1, y)[0] - lon) / 512, places=9)
            self.assertTrue((np.diff(lats) < 0).all())

    def test_emprise_couverte(self):
        t = tiles_covering(-12, 37.5, 16, 55.4, 5)
        self.assertEqual(len(t), 12)                        # contrat/spec : 12 tuiles à z5
        self.assertEqual(len(tiles_covering(-12, 37.5, 16, 55.4, 7)), 110)
        self.assertEqual(len(tiles_covering(-12, 37.5, 16, 55.4, 8)), 420)

    def test_pixels_de_bordure_coincident_avec_voisine(self):
        z, x, y = 8, 130, 93
        a_lon, a_lat = pixel_lonlat(z, x, y)
        b_lon, b_lat = pixel_lonlat(z, x + 1, y + 1)
        self.assertAlmostEqual(a_lon[-1], b_lon[1], places=9)       # bordure est de A = pixel 0 de B
        self.assertAlmostEqual(a_lon[-2], b_lon[0], places=9)       # pixel 255 de A = bordure ouest de B
        self.assertAlmostEqual(a_lat[-1], b_lat[1], places=9)


class TestRechantillonnage(unittest.TestCase):
    def test_bilineaire_exact_sur_rampe_et_nan(self):
        lat = np.linspace(50, 40, 101, dtype=np.float32)
        lon = np.linspace(0, 10, 101, dtype=np.float32)
        f = (2 * lat[:, None] + 3 * lon[None, :]).astype(np.float32)
        f[50, 50] = np.nan
        g = Grid(lat, lon)
        qlon, qlat = np.array([1.234, 5.0, 12.0]), np.array([48.7, 45.0, 60.0])
        out = g.sample(f, qlon, qlat)       # produit cartésien lat x lon
        self.assertAlmostEqual(float(out[0, 0]), 2 * 48.7 + 3 * 1.234, places=3)
        self.assertTrue(np.isnan(out[1, 1]))             # voisin NaN -> NaN
        self.assertTrue(np.isnan(out[0, 2]))             # lon hors emprise
        self.assertTrue(np.isnan(out[2, 0]))             # lat hors emprise


TMP = None
SRC = None
Z0, Z1 = 7, 8


def setUpModule():
    global TMP, SRC
    TMP = tempfile.mkdtemp(prefix="tuilage_test_")
    SRC = dict(np.load(FIXTURE))
    B.build(FIXTURE, TMP, zooms=(Z0, Z1), workers=4)


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


def chemin(var):
    return os.path.join(TMP, "arome-hd", "2026-10-17T06Z", var, "03.pmtiles")


class TestFixture(unittest.TestCase):
    def test_arborescence_et_metadonnees(self):
        self.assertEqual(sorted(os.listdir(os.path.join(TMP, "arome-hd", "2026-10-17T06Z"))),
                         sorted(list(E.SCALAR8) + ["wind10"]))
        with TileFile(chemin("wind10")) as t:
            m = t.meta
            self.assertEqual((m["encoding"], m["border"], m["tile_size"], m["unit"]), ("wind12", 1, 256, "m/s"))
            self.assertEqual((m["min"], m["max"]), (-64, 64))
            self.assertEqual(t.header["min_zoom"], Z0)
        with TileFile(chemin("precip_rate")) as t:
            self.assertEqual((t.meta["encoding"], t.meta["scale"], t.meta["max"]), ("scalar8", "sqrt", 100.0))

    def test_tuiles_presentes_seulement_sur_l_emprise(self):
        g = Grid(SRC["lat"], SRC["lon"])
        with TileFile(chemin("t2m")) as t:
            for z in (Z0, Z1):
                attendues = tiles_covering(*g.bounds, z)
                for x, y in attendues:
                    self.assertIsNotNone(t.raw(z, x, y), (z, x, y))
                self.assertIsNone(t.raw(z, attendues[0][0] - 3, attendues[0][1]))

    def test_valeurs_decodees_contre_source_demi_pas(self):
        g = Grid(SRC["lat"], SRC["lon"])
        z = Z1
        for var in list(E.SCALAR8) + ["wind10"]:
            with TileFile(chemin(var)) as t:
                for x, y in tiles_covering(*g.bounds, z)[::5]:
                    lon, lat = pixel_lonlat(z, x, y)
                    if var == "wind10":
                        ref = [g.sample(SRC["wind10_u"], lon, lat), g.sample(SRC["wind10_v"], lon, lat)]
                        got = list(t.read(z, x, y))
                        borne = E.WIND_STEP / 2 + 1e-5
                        nanref = np.isnan(ref[0]) | np.isnan(ref[1])
                    else:
                        ref = [g.sample(SRC[var], lon, lat)]
                        got = [t.read(z, x, y)]
                        borne = E.scalar_step(var) / 2 * 1.01 + 1e-5
                        nanref = np.isnan(ref[0])
                    for r, o in zip(ref, got):
                        np.testing.assert_array_equal(np.isnan(o), nanref)   # code 0 <=> NaN
                        ok = ~nanref
                        if ok.any():
                            self.assertLessEqual(np.abs(o - r)[ok].max(), borne, (var, x, y))

    def test_bordure_coherente_entre_voisines(self):
        z = Z1
        g = Grid(SRC["lat"], SRC["lon"])
        paires = 0
        for var in ("t2m", "wind10"):
            with TileFile(chemin(var)) as t:
                tuiles = set(tiles_covering(*g.bounds, z))
                for (x, y) in sorted(tuiles):
                    if (x + 1, y) in tuiles:
                        a, b = t.read(z, x, y), t.read(z, x + 1, y)
                        a, b = (a, b) if var == "t2m" else (a[0], b[0])
                        # bordure est de A = pixel 0 de B ; pixel 255 de A = bordure ouest de B
                        np.testing.assert_array_equal(a[:, 257], b[:, 1])
                        np.testing.assert_array_equal(a[:, 256], b[:, 0])
                        paires += 1
                    if (x, y + 1) in tuiles:
                        a, b = t.read(z, x, y), t.read(z, x, y + 1)
                        a, b = (a, b) if var == "t2m" else (a[0], b[0])
                        np.testing.assert_array_equal(a[257, :], b[1, :])
                        np.testing.assert_array_equal(a[256, :], b[0, :])
                        paires += 1
        self.assertGreater(paires, 10)

    def test_images_sans_alpha(self):
        from PIL import Image
        import io
        for var in ("t2m", "wind10"):
            with TileFile(chemin(var)) as t:
                x, y = tiles_covering(*Grid(SRC["lat"], SRC["lon"]).bounds, Z1)[10]
                im = Image.open(io.BytesIO(t.raw(Z1, x, y)))
                self.assertEqual(im.size, (258, 258))
                self.assertIn(im.mode, ("RGB", "L"))

    def test_point_decode_pres_du_bord_de_tuile(self):
        # échantillonnage client (bilinéaire dans la texture 258) à cheval sur une jonction
        z = Z1
        g = Grid(SRC["lat"], SRC["lon"])
        tuiles = sorted(tiles_covering(*g.bounds, z))
        verifies = 0
        with TileFile(chemin("t2m")) as t:
            for (x, y) in tuiles:
                if (x + 1, y) not in tuiles:
                    continue
                lon_j, lat_h = tile_lon_lat(z, x + 1, y)
                lat_b = tile_lon_lat(z, x + 1, y + 1)[1]
                for fr in (0.2, 0.5, 0.8):
                    lat = lat_h + fr * (lat_b - lat_h)
                    a = t.sample(lon_j - 1e-7, lat, z)      # côté tuile x
                    b = t.sample(lon_j + 1e-7, lat, z)      # côté tuile x+1
                    if np.isnan(a) or np.isnan(b):
                        continue
                    self.assertAlmostEqual(a, b, delta=0.01)
                    ref = float(g.sample(SRC["t2m"], np.array([lon_j]), np.array([lat]))[0, 0])
                    self.assertAlmostEqual(a, ref, delta=E.scalar_step("t2m"))
                    verifies += 1
        self.assertGreater(verifies, 3)


class TestSynthetique(unittest.TestCase):
    def test_rampe_et_trou_de_donnees(self):
        d = tempfile.mkdtemp(prefix="tuilage_syn_")
        try:
            lat = np.linspace(46.0, 44.0, 201, dtype=np.float32)
            lon = np.linspace(0.0, 3.0, 301, dtype=np.float32)
            t2m = (270 + 2 * (lat[:, None] - 44) + 3 * lon[None, :]).astype(np.float32)
            t2m[80:120, 100:140] = np.nan
            u = np.full_like(t2m, 12.5)
            v = np.full_like(t2m, -7.25)
            np.savez(os.path.join(d, "x.npz"), lat=lat, lon=lon, t2m=t2m, wind10_u=u, wind10_v=v,
                     mslp=t2m * 0 + 1e5,
                     meta=json.dumps({"model": "arome", "run": "2026-10-17T06:00:00Z", "step": 9}))
            rep = B.build(os.path.join(d, "x.npz"), d, workers=2)
            self.assertEqual(sorted(r["variable"] for r in rep), ["t2m", "wind10"])   # mslp ignoré
            self.assertEqual(rep[0]["model"], "arome")
            p = os.path.join(d, "arome", "2026-10-17T06Z", "t2m", "09.pmtiles")
            self.assertTrue(os.path.exists(p))
            with TileFile(p) as t:
                self.assertEqual(t.header["min_zoom"], 3)
                self.assertEqual(t.header["max_zoom"], 7)
                z = 7
                x, y = lonlat_to_tile(0.5, 45.5, z)
                a = t.read(z, x, y)
                lons, lats = pixel_lonlat(z, x, y)
                ref = 270 + 2 * (lats[:, None] - 44) + 3 * lons[None, :]
                ok = ~np.isnan(a)
                self.assertGreater(ok.mean(), 0.3)
                self.assertLessEqual(np.abs(a - ref)[ok].max(), E.scalar_step("t2m") / 2 + 0.02)
                # le trou (lat 44.8..45.2 ~ lon 1.0..1.4) donne du NaN
                self.assertTrue(np.isnan(t.sample(1.2, 45.0, z)))
            with TileFile(os.path.join(d, "arome", "2026-10-17T06Z", "wind10", "09.pmtiles")) as t:
                u0, v0 = t.sample(1.0, 45.0, 7)
                self.assertAlmostEqual(u0, 12.5, delta=E.WIND_STEP)
                self.assertAlmostEqual(v0, -7.25, delta=E.WIND_STEP)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
