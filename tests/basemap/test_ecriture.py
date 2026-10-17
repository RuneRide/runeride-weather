# SPDX-License-Identifier: EUPL-1.2
"""Écriture/relecture locale d'un PMTiles synthétique et journal de reprise (sans réseau)."""
import os
import tempfile
import unittest

from pmtiles.reader import MmapSource, Reader, all_tiles
from pmtiles.tile import Compression, TileType, zxy_to_tileid

from pipeline.basemap.extract import EcrivainPMTiles, Reprise


class TestEcriture(unittest.TestCase):
    def test_aller_retour_et_dedoublonnage(self):
        with tempfile.TemporaryDirectory() as d:
            chemin = os.path.join(d, "t.pmtiles")
            with open(chemin, "wb") as f:
                w = EcrivainPMTiles(f, d)
                contenus = {(0, 0, 0): b"A" * 50, (1, 0, 0): b"B" * 60, (1, 1, 0): b"B" * 60, (1, 1, 1): b"C" * 7}
                for zxy in sorted(contenus, key=lambda t: zxy_to_tileid(*t)):
                    w.write_tile(zxy_to_tileid(*zxy), contenus[zxy])
                w.finalize({"version": 3, "tile_compression": Compression.GZIP, "tile_type": TileType.MVT,
                            "min_lon_e7": -10, "min_lat_e7": -20, "max_lon_e7": 30, "max_lat_e7": 40,
                            "center_zoom": 1, "center_lon_e7": 0, "center_lat_e7": 0}, {"attribution": "x"})
                w.nettoyer()
            with open(chemin, "rb") as f:
                r = Reader(MmapSource(f))
                h = r.header()
                self.assertEqual((h["min_zoom"], h["max_zoom"]), (0, 1))
                self.assertEqual(h["addressed_tiles_count"], 4)
                self.assertEqual(h["tile_contents_count"], 3)   # B dédoublonné
                self.assertEqual(r.metadata(), {"attribution": "x"})
                for zxy, data in contenus.items():
                    self.assertEqual(bytes(r.get(*zxy)), data)
                self.assertEqual(len(list(all_tiles(MmapSource(f)))), 4)

    def test_journal_de_reprise(self):
        with tempfile.TemporaryDirectory() as d:
            dossier = os.path.join(d, "rep")
            cle = {"url": "u", "zoom_max": 3}
            r = Reprise(dossier, cle)
            r.ajouter(100, 3, b"abc")
            r.ajouter(500, 2, b"de")
            r.fermer()
            with open(os.path.join(dossier, "index.jsonl"), "a") as f:
                f.write("[900, 4, 5")          # ligne tronquée par une interruption
            r2 = Reprise(dossier, cle)
            self.assertEqual(r2.deja, 2)
            self.assertEqual(r2.lire(500, 2), b"de")
            r2.ajouter(900, 4, b"wxyz")
            self.assertEqual(r2.lire(900, 4), b"wxyz")
            r2.fermer()
            r3 = Reprise(dossier, {"url": "autre", "zoom_max": 3})   # clé différente : repart de zéro
            self.assertEqual(r3.deja, 0)
            r3.fermer()


if __name__ == "__main__":
    unittest.main()
