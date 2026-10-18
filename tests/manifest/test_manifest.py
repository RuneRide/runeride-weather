# SPDX-License-Identifier: EUPL-1.2
"""Tests du générateur de manifest : jeux synthétiques minuscules + test sur les vraies données."""
import json
import os
import shutil
import tempfile
import unittest

import numpy as np

from pipeline.manifest import build as B
from pipeline.manifest import domaine as D

RACINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _ecrire_pmtiles(chemin, meta, zmin=3, zmax=5):
    """PMTiles minimal (une tuile) avec des métadonnées choisies."""
    from pmtiles.tile import Compression, TileType, zxy_to_tileid
    from pmtiles.writer import Writer
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin, "wb") as f:
        w = Writer(f)
        w.write_tile(zxy_to_tileid(zmin, 4, 3), b"RIFF")
        w.finalize({"tile_type": TileType.WEBP, "tile_compression": Compression.NONE,
                    "min_lon_e7": -120000000, "min_lat_e7": 375000000, "max_lon_e7": 160000000,
                    "max_lat_e7": 554000000, "center_zoom": zmin, "center_lon_e7": 0, "center_lat_e7": 0},
                   dict(meta, minzoom=zmin, maxzoom=zmax))


def _jeu(racine, modele, tirage, variables):
    """variables : {nom: (échéances, métadonnées)}."""
    for v, (steps, meta) in variables.items():
        for s in steps:
            _ecrire_pmtiles(os.path.join(racine, modele, tirage, v, "%02d.pmtiles" % s), meta)


M_T2M = {"encoding": "scalar8", "min": 243.15, "max": 306.65, "scale": "linear", "unit": "K", "quant_step": 0.25}
M_WIND = {"encoding": "wind8", "min": -48.0, "max": 48.0, "scale": "linear", "unit": "m/s",
          "quant_step": 0.378, "components": ["wind10_u", "wind10_v"]}
M_GUST = {"encoding": "scalar8", "min": 0.0, "max": 63.5, "scale": "linear", "unit": "m/s", "quant_step": 0.25}


class TestDomaine(unittest.TestCase):
    def _trapeze(self):
        lat = np.arange(55.0, 45.0 - 1e-9, -0.1)
        lon = np.arange(-12.0, 16.0 + 1e-9, 0.1)
        valide = np.zeros((len(lat), len(lon)), dtype=bool)
        for i, la in enumerate(lat):
            marge = (55.0 - la) * 0.5          # le trapèze se rétrécit vers le sud
            valide[i] = (lon >= -12 + marge) & (lon <= 16 - marge)
        return lat, lon, valide

    def test_trapeze_simplifie(self):
        lat, lon, valide = self._trapeze()
        anneau = D.polygone_depuis_masque(lat, lon, valide, tol=0.15)
        self.assertEqual(anneau[0], anneau[-1])
        self.assertLessEqual(len(anneau), 12)
        self.assertGreaterEqual(len(anneau), 5)
        part_valide_dans, part_poly_valide = D.couverture(anneau, lat, lon, valide)
        self.assertGreater(part_valide_dans, 0.97)
        self.assertGreater(part_poly_valide, 0.97)

    def test_concavite_du_bord_sud(self):
        """Un bord sud concave (centre plus haut que les coins) ne doit pas être comblé."""
        lat, lon, valide = self._trapeze()
        for j, lo in enumerate(lon):
            valide[(lat < 46.0 + 0.00 * lo + (1.0 - abs(lo - 2) / 14.0)), j] = False
        anneau = D.polygone_depuis_masque(lat, lon, valide, tol=0.1)
        self.assertFalse(D.point_dans_polygone(2.0, 45.2, anneau))
        self.assertTrue(D.point_dans_polygone(2.0, 51.0, anneau))

    def test_masque_vide(self):
        with self.assertRaises(ValueError):
            D.polygone_depuis_masque([1.0, 0.0], [0.0, 1.0], np.zeros((2, 2), dtype=bool))

    def test_point_dans_polygone(self):
        carre = [[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]]
        self.assertTrue(D.point_dans_polygone(1, 1, carre))
        self.assertFalse(D.point_dans_polygone(3, 1, carre))


class TestConstruction(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        t = os.path.join(self.tmp, "tuiles")
        # AROME : 06Z complet. HD : 03Z complet, 06Z tronqué à 0-18 (cas R11).
        complet = {"t2m": (range(0, 52), M_T2M), "wind10": (range(0, 52), M_WIND), "gust10": (range(1, 52), M_GUST)}
        _jeu(t, "arome", "2026-10-17T06Z", complet)
        _jeu(t, "arome-hd", "2026-10-17T03Z", complet)
        _jeu(t, "arome-hd", "2026-10-17T06Z",
             {"t2m": (range(0, 19), M_T2M), "wind10": (range(0, 19), M_WIND), "gust10": (range(1, 19), M_GUST)})
        self.t = t
        self.m = B.construire(t, None, "2026-10-17T16:00:00Z")

    def test_en_tete(self):
        self.assertEqual(self.m["version"], 1)
        self.assertEqual(self.m["generated"], "2026-10-17T16:00:00Z")
        self.assertEqual(self.m["attribution"], "Source : Météo-France, Licence Ouverte v2.0")
        self.assertEqual(set(self.m["models"]), {"arome", "arome-hd"})

    def test_completude_par_variable_et_par_tirage(self):
        runs = {r["id"]: r for r in self.m["models"]["arome-hd"]["runs"]}
        self.assertTrue(runs["2026-10-17T03Z"]["complete"])
        r6 = runs["2026-10-17T06Z"]
        self.assertFalse(r6["complete"])
        self.assertFalse(r6["variables"]["t2m"]["complete"])
        self.assertEqual(r6["variables"]["t2m"]["steps"], list(range(0, 19)))
        self.assertEqual(r6["variables"]["gust10"]["steps"], list(range(1, 19)))
        self.assertEqual(r6["valid_debut"], "2026-10-17T06:00:00Z")
        self.assertEqual(r6["valid_fin"], "2026-10-18T00:00:00Z")
        self.assertTrue(runs["2026-10-17T03Z"]["variables"]["gust10"]["complete"])  # 1..51 attendu

    def test_date_de_traitement(self):
        # `traite` = date de modification du fichier le plus récent du tirage (traitement chez nous, pas publication Météo-France)
        for modele, runs in self.m["models"].items():
            for r in runs["runs"]:
                self.assertRegex(r["traite"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        import calendar, glob, time
        fichiers = glob.glob(os.path.join(self.t, "arome", "2026-10-17T06Z", "*", "*.pmtiles"))
        attendu = max(os.path.getmtime(f) for f in fichiers)
        r = self.m["models"]["arome"]["runs"][0]
        self.assertLess(abs(calendar.timegm(time.strptime(r["traite"], "%Y-%m-%dT%H:%M:%SZ")) - attendu), 1.5)

    def test_gust10_sans_echeance_zero_est_complet(self):
        r = self.m["models"]["arome"]["runs"][0]
        self.assertTrue(r["variables"]["gust10"]["complete"])
        self.assertTrue(r["complete"])

    def test_variables_et_couches(self):
        v = self.m["variables"]["t2m"]
        self.assertEqual((v["encoding"], v["unit"], v["quant_step"]), ("scalar8", "K", 0.25))
        self.assertTrue(v["per_file_range"])
        self.assertEqual(v["models"], ["arome", "arome-hd"])
        self.assertEqual(self.m["variables"]["wind10"]["components"], ["wind10_u", "wind10_v"])
        self.assertEqual(self.m["couches"]["vent"]["variable"], "wind10")
        self.assertNotIn("pluie", self.m["couches"])     # variable absente du jeu
        self.assertNotIn("pression", self.m["couches"])  # jamais en raster
        self.assertIn("pression", self.m["hors_raster"])

    def test_zooms_et_emprise(self):
        mod = self.m["models"]["arome-hd"]
        self.assertEqual(mod["zooms"]["t2m"], [3, 5])
        self.assertEqual(mod["grid"], [-12.0, 37.5, 16.0, 55.4])
        self.assertEqual(mod["domain"]["polygon"][0], mod["domain"]["polygon"][-1])

    def test_chemin_relatif(self):
        r = self.m["models"]["arome"]["runs"][0]
        self.assertEqual(r["path"], "arome/2026-10-17T06Z")
        chemin = self.m["file_template"].format(model="arome", run_id=r["id"], variable="t2m", step="05")
        self.assertTrue(os.path.exists(os.path.join(self.t, chemin)))

    def test_ecriture_json(self):
        sortie = os.path.join(self.tmp, "sortie", "manifest.json")
        B.ecrire(self.m, sortie)
        with open(sortie, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["version"], 1)

    def test_polygone_depuis_canon(self):
        canon = os.path.join(self.tmp, "canon")
        os.makedirs(os.path.join(canon, "arome", "2026-10-17T06Z"))
        lat = np.arange(55.4, 37.5 - 1e-6, -0.5).astype(np.float32)
        lon = np.arange(-12.0, 16.0 + 1e-6, 0.5).astype(np.float32)
        champ = np.ones((len(lat), len(lon)), dtype=np.float32)
        champ[:, lon < -8] = np.nan       # bande ouest masquée
        np.savez(os.path.join(canon, "arome", "2026-10-17T06Z", "03.npz"), lat=lat, lon=lon, t2m=champ)
        m = B.construire(self.t, canon, "2026-10-17T16:00:00Z")
        poly = m["models"]["arome"]["domain"]["polygon"]
        self.assertTrue(m["models"]["arome"]["domain"]["source"].startswith("canon/arome"))
        self.assertGreaterEqual(min(p[0] for p in poly), -8.0)
        # le HD n'a pas de canon : repli sur le rectangle
        self.assertIn("rectangle", m["models"]["arome-hd"]["domain"]["source"])

    def test_dossier_vide_ou_etranger_ignore(self):
        os.makedirs(os.path.join(self.t, "autre", "2026-10-17T06Z", "t2m"))
        os.makedirs(os.path.join(self.t, "arome", "2026-10-17T09Z", "t2m"))   # tirage sans fichier
        m = B.construire(self.t, None, "x")
        self.assertEqual(len(m["models"]["arome"]["runs"]), 1)


@unittest.skipUnless(os.path.isdir(os.path.join(RACINE, "out", "tiles-reduit2", "arome-hd")), "tuiles réelles absentes")
class TestDonneesReelles(unittest.TestCase):
    def test_reel(self):
        try:
            import pmtiles  # noqa: F401
        except ImportError:
            self.skipTest("paquet pmtiles absent (PYTHONPATH)")
        m = B.construire(os.path.join(RACINE, "out", "tiles-reduit2"), os.path.join(RACINE, "out", "canon"), "x")
        hd = {r["id"]: r for r in m["models"]["arome-hd"]["runs"]}
        if "2026-10-17T06Z" in hd:  # cas d'incomplétude du 17/10
            self.assertFalse(hd["2026-10-17T06Z"]["complete"])
            self.assertEqual(hd["2026-10-17T06Z"]["variables"]["t2m"]["steps"][-1], 18)
        if "2026-10-17T03Z" in hd:
            self.assertTrue(hd["2026-10-17T03Z"]["complete"])
        for mod in m["models"].values():
            self.assertLessEqual(mod["domain"]["vertices"], 60)
            self.assertGreaterEqual(mod["domain"]["vertices"], 5)


if __name__ == "__main__":
    unittest.main()
