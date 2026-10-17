# SPDX-License-Identifier: EUPL-1.2
"""Tests de la logique pure de pipeline.basemap (aucun accès réseau).

Lancer : PYTHONPATH=<dossier pylib2 contenant pmtiles>:. python3 -m unittest discover -s tests/basemap -t .
"""
import datetime
import gzip
import math
import unittest

from pmtiles.tile import tileid_to_zxy, zxy_to_tileid

from pipeline.basemap import glyphes as G
from pipeline.basemap import reseau as R
from pipeline.basemap import serveur as S
from pipeline.basemap import tuiles as T

DOMAINE = (-12.0, 37.5, 16.0, 55.4)


class TestTuilesBoite(unittest.TestCase):
    def test_zoom0_une_tuile(self):
        self.assertEqual(T.etendue_zoom(DOMAINE, 0), (0, 0, 0, 0))

    def test_point_connu(self):
        # Paris (2.35, 48.85) : tuile z10 = (518, 352)
        self.assertEqual(T.lonlat_vers_tuile(2.35, 48.85, 10), (518, 352))
        # Toulouse z9
        self.assertEqual(T.lonlat_vers_tuile(1.444, 43.6, 9), (258, 186))

    def test_etendue_domaine_connue(self):
        # Calcul de référence indépendant (log(tan+sec)) pour les coins de la boîte, z5
        def ref(lon, lat, z):
            n = 2 ** z
            r = math.radians(lat)
            return int((lon + 180) / 360 * n), int((1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * n)
        x0, y1 = ref(-12, 37.5, 5)
        x1, y0 = ref(16, 55.4, 5)
        self.assertEqual(T.etendue_zoom(DOMAINE, 5), (x0, x1, y0, y1))
        self.assertEqual((x0, x1, y0, y1), (14, 17, 10, 12))

    def test_nombre_de_tuiles_domaine(self):
        # Mesuré sur le build du 2026-10-17 : toutes les tuiles voulues existent.
        self.assertEqual(T.compter_tuiles(DOMAINE, 0, 7), 165)
        self.assertEqual(T.compter_tuiles(DOMAINE, 0, 8), 585)
        self.assertEqual(T.compter_tuiles(DOMAINE, 0, 9), 2184)
        self.assertEqual(T.compter_tuiles(DOMAINE, 0, 10), 8340)

    def test_ids_voulus_tries_uniques_et_dans_la_boite(self):
        ids = T.ids_voulus(DOMAINE, 0, 6)
        self.assertEqual(ids, sorted(set(ids)))
        self.assertEqual(len(ids), T.compter_tuiles(DOMAINE, 0, 6))
        for i in ids:
            z, x, y = tileid_to_zxy(i)
            x0, x1, y0, y1 = T.etendue_zoom(DOMAINE, z)
            self.assertTrue(x0 <= x <= x1 and y0 <= y <= y1)

    def test_tuile_hors_boite_exclue(self):
        # Tuile z6 de l'Amérique du Sud : absente.
        ids = set(T.ids_voulus(DOMAINE, 0, 6))
        self.assertNotIn(zxy_to_tileid(6, 20, 40), ids)
        # Tuile z6 de Paris : présente.
        x, y = T.lonlat_vers_tuile(2.35, 48.85, 6)
        self.assertIn(zxy_to_tileid(6, x, y), ids)

    def test_boite_minuscule_une_tuile_par_zoom(self):
        petite = (2.30, 48.84, 2.31, 48.85)
        self.assertEqual(T.compter_tuiles(petite, 0, 8), 9)

    def test_bords_inclus(self):
        # Une boîte alignée exactement sur la grille z1 (lon 0) inclut la tuile qui la touche.
        x0, x1, _, _ = T.etendue_zoom((-10, 10, 0, 20), 1)
        self.assertEqual((x0, x1), (0, 1))

    def test_bbox_invalide(self):
        for mauvaise in [(10, 0, -10, 5), (0, 10, 5, 0), (-200, 0, 0, 5), (0, -91, 5, 5)]:
            with self.assertRaises(ValueError):
                T.valider_bbox(mauvaise)

    def test_latitude_bornee_mercator(self):
        x, y = T.lonlat_vers_tuile(0, 89.9, 5)
        self.assertEqual(y, 0)
        x, y = T.lonlat_vers_tuile(0, -89.9, 5)
        self.assertEqual(y, 31)


class TestFiltrageZoom(unittest.TestCase):
    def test_plage_ids_du_zoom(self):
        for z in range(0, 12):
            bas, haut = T.plage_ids_du_zoom(z)
            self.assertEqual(T.zoom_de_id(bas), z)
            self.assertEqual(T.zoom_de_id(haut), z)
            self.assertEqual(tileid_to_zxy(bas)[0], z)
            if z:
                self.assertEqual(bas, T.plage_ids_du_zoom(z - 1)[1] + 1)

    def test_zoom_max_respecte(self):
        ids = T.ids_voulus(DOMAINE, 0, 7)
        self.assertEqual({T.zoom_de_id(i) for i in ids}, set(range(0, 8)))
        self.assertTrue(all(T.zoom_de_id(i) <= 7 for i in ids))

    def test_zoom_min(self):
        ids = T.ids_voulus(DOMAINE, 5, 7)
        self.assertEqual({T.zoom_de_id(i) for i in ids}, {5, 6, 7})

    def test_zooms_inverses(self):
        with self.assertRaises(ValueError):
            T.ids_voulus(DOMAINE, 5, 3)

    def test_intersecte_et_ids_dans(self):
        ids = [3, 10, 11, 40]
        self.assertTrue(T.intersecte(ids, 0, 4))
        self.assertFalse(T.intersecte(ids, 4, 10))      # borne haute exclue
        self.assertTrue(T.intersecte(ids, 4, 11))
        self.assertFalse(T.intersecte(ids, 41, None))
        self.assertTrue(T.intersecte(ids, 41 - 2, None))
        self.assertEqual(T.ids_dans(ids, 10, 41), [10, 11, 40])
        self.assertEqual(T.ids_dans(ids, 11, 12), [11])
        self.assertEqual(T.ids_dans(ids, 12, 40), [])

    def test_en_tete(self):
        e7 = T.bbox_vers_e7(DOMAINE)
        self.assertEqual(e7, (-120000000, 375000000, 160000000, 554000000))
        self.assertEqual(T.zoom_centre(DOMAINE, 9), 5)
        self.assertEqual(T.zoom_centre((0, 0, 0.001, 0.001), 9), 9)
        self.assertEqual(T.zoom_centre((-180, -80, 180, 80), 9), 2)


class TestFusionPlages(unittest.TestCase):
    def test_fusion_contigus(self):
        r = T.fusionner_plages([(0, 10), (10, 5), (15, 5)], 0, 1000)
        self.assertEqual(r, [(0, 20, [(0, 10), (10, 5), (15, 5)])])

    def test_ecart_tolere_et_refuse(self):
        blocs = [(0, 10), (30, 10), (1000, 10)]
        r = T.fusionner_plages(blocs, 20, 10_000)
        self.assertEqual([(p[0], p[1]) for p in r], [(0, 40), (1000, 10)])
        r = T.fusionner_plages(blocs, 5, 10_000)
        self.assertEqual(len(r), 3)

    def test_taille_max(self):
        r = T.fusionner_plages([(0, 60), (60, 60), (120, 60)], 0, 100)
        self.assertEqual(len(r), 3)           # un bloc plus gros que la moitié : jamais fusionné
        r = T.fusionner_plages([(0, 40), (40, 40), (80, 40)], 0, 100)
        self.assertEqual([(p[0], p[1]) for p in r], [(0, 80), (80, 40)])

    def test_desordre_accepte_chevauchement_refuse(self):
        r = T.fusionner_plages([(10, 5), (0, 10)], 0, 100)
        self.assertEqual(r[0][:2], (0, 15))
        with self.assertRaises(ValueError):
            T.fusionner_plages([(0, 10), (5, 10)], 0, 100)

    def test_vide(self):
        self.assertEqual(T.fusionner_plages([], 0, 100), [])


class TestChoixBuild(unittest.TestCase):
    def test_aujourdhui_disponible(self):
        appels = []

        def sonde(u):
            appels.append(u)
            return 200
        d, u = T.choisir_build(datetime.date(2026, 10, 17), sonde)
        self.assertEqual(d, datetime.date(2026, 10, 17))
        self.assertEqual(u, "https://build.protomaps.com/20261017.pmtiles")
        self.assertEqual(len(appels), 1)

    def test_remonte_les_jours(self):
        dispo = {"20261015"}
        appels = []

        def sonde(u):
            appels.append(u)
            return 200 if u.rsplit("/", 1)[1][:8] in dispo else 404
        d, u = T.choisir_build(datetime.date(2026, 10, 17), sonde)
        self.assertEqual(d, datetime.date(2026, 10, 15))
        self.assertEqual([a[-16:-8] for a in appels], ["20261017", "20261016", "20261015"])

    def test_passage_de_mois_et_d_annee(self):
        d, _ = T.choisir_build(datetime.date(2027, 1, 2),
                               lambda u: 200 if "20261231" in u else 404)
        self.assertEqual(d, datetime.date(2026, 12, 31))

    def test_aucun_build(self):
        with self.assertRaises(LookupError):
            T.choisir_build(datetime.date(2026, 10, 17), lambda u: 404, jours_max=3)

    def test_non_200_ignore(self):
        d, _ = T.choisir_build(datetime.date(2026, 10, 17), lambda u: 206 if "20261017" in u else 200)
        self.assertEqual(d, datetime.date(2026, 10, 16))


class TestAttente(unittest.TestCase):
    def test_croissante_et_plafonnee(self):
        att = [R.delai_attente(t, gigue=0.0) for t in range(8)]
        self.assertEqual(att, [1, 2, 4, 8, 16, 32, 60, 60])

    def test_gigue_bornee(self):
        self.assertAlmostEqual(R.delai_attente(2, gigue=0.25, alea=lambda: 1.0), 5.0)
        self.assertAlmostEqual(R.delai_attente(2, gigue=0.25, alea=lambda: 0.0), 4.0)


class TestRangeServeur(unittest.TestCase):
    def test_cas(self):
        self.assertIsNone(S.analyser_range(None, 100))
        self.assertEqual(S.analyser_range("bytes=0-9", 100), (0, 9))
        self.assertEqual(S.analyser_range("bytes=90-", 100), (90, 99))
        self.assertEqual(S.analyser_range("bytes=90-500", 100), (90, 99))
        self.assertEqual(S.analyser_range("bytes=-10", 100), (90, 99))
        self.assertEqual(S.analyser_range("bytes=-500", 100), (0, 99))
        for mauvais in ("bytes=100-", "bytes=5-2", "bytes=-", "bytes=0-1,5-6", "items=0-1"):
            with self.assertRaises(ValueError):
                S.analyser_range(mauvais, 100)

    def test_ports_interdits(self):
        for p in (8187, 8097, 8099):
            with self.assertRaises(ValueError):
                S.creer_serveur(".", ".", p)


def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _champ(num, typ, v):
    cle = _varint(num << 3 | typ)
    if typ == 0:
        return cle + _varint(v)
    return cle + _varint(len(v)) + v


class TestGlyphes(unittest.TestCase):
    def test_plages(self):
        self.assertEqual(G.plages_de_texte(["Paris", "Orléans"]), {0})
        self.assertEqual(G.plages_de_texte(["Cœur", "L’Isle"]), {0, 256, 8192})
        self.assertEqual(G.nom_plage(256), "256-511")

    def test_noms_de_tuile_synthetique(self):
        valeur = _champ(1, 2, "Œuvre".encode())
        valeur2 = _champ(1, 2, "ignoré".encode())
        tags = _varint(0) + _varint(0) + _varint(1) + _varint(1)
        objet = _champ(2, 2, tags)
        couche = (_champ(1, 2, b"places") + _champ(2, 2, objet) + _champ(3, 2, b"name:fr")
                  + _champ(3, 2, b"population") + _champ(4, 2, valeur) + _champ(4, 2, valeur2))
        tuile = gzip.compress(_champ(3, 2, couche))
        self.assertEqual(list(G.noms_de_tuile(tuile)), [("places", "name:fr", "Œuvre")])


if __name__ == "__main__":
    unittest.main()
