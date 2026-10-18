# SPDX-License-Identifier: EUPL-1.2
"""Tests de la brique isobares. Lancer : python3 -m unittest discover -s tests/isobars -t .
(le test PMTiles est ignoré si le paquet `pmtiles` n'est pas dans PYTHONPATH)."""
import gzip
import json
import os
import tempfile
import unittest

import numpy as np

from pipeline.isobars import calcul as K
from pipeline.isobars import contours as C
from pipeline.isobars import extrema as X
from pipeline.isobars import mvt as M
from pipeline.isobars import validation as V


def grille(ny, nx):
    """Index de ligne et de colonne de chaque nœud."""
    return np.meshgrid(np.arange(ny, dtype=float), np.arange(nx, dtype=float), indexing="ij")


def tout_valide(F):
    return C.cellules_valides(np.isfinite(F))


class TestUnites(unittest.TestCase):
    def test_pa_vers_hpa(self):
        self.assertAlmostEqual(float(C.pa_vers_hpa(101325.0)), 1013.25)
        r = C.pa_vers_hpa(np.array([[100000.0, np.nan], [90000.0, 105000.0]], dtype=np.float32))
        self.assertEqual(r.dtype, np.float64)
        self.assertEqual(r[0, 0], 1000.0)
        self.assertTrue(np.isnan(r[0, 1]))
        self.assertEqual(r[1, 1], 1050.0)

    def test_niveaux(self):
        F = np.array([[1013.3, 1019.9], [1015.0, 1024.1]])
        self.assertEqual(C.niveaux_hpa(F, 2.0).tolist(), [1014.0, 1016.0, 1018.0, 1020.0, 1022.0, 1024.0])


class TestMarchingSquares(unittest.TestCase):
    def test_cercle(self):
        """Champ = distance au centre : l'isoligne r = 10 est un cercle, une seule ligne fermée."""
        i, j = grille(60, 60)
        cx, cy, r = 29.7, 30.3, 10.0
        F = np.hypot(i - cy, j - cx)
        lignes = C.contour(F, r, tout_valide(F))
        self.assertEqual(len(lignes), 1)
        pts, ferme = lignes[0]
        self.assertTrue(ferme)
        self.assertTrue(np.allclose(pts[0], pts[-1]))
        d = np.hypot(pts[:, 0] - cy, pts[:, 1] - cx)
        # l'interpolation linéaire sur une maille unité d'une fonction de courbure 1/r donne une erreur <~ h²/(8r)
        self.assertLess(np.abs(d - r).max(), 0.02)
        L = np.hypot(*np.diff(pts, axis=0).T).sum()
        self.assertAlmostEqual(L / (2 * np.pi * r), 1.0, delta=0.005)

    def test_plan_incline_exact(self):
        """Champ linéaire : l'interpolation linéaire est exacte, tous les sommets satisfont F = niveau."""
        i, j = grille(40, 50)
        F = 0.5 * j + 0.2 * i
        for niv in (3.3, 7.3, 15.0):
            lignes = C.contour(F, niv, tout_valide(F))
            self.assertEqual(len(lignes), 1)
            pts, ferme = lignes[0]
            self.assertFalse(ferme)
            self.assertTrue(np.allclose(0.5 * pts[:, 1] + 0.2 * pts[:, 0], niv, atol=1e-9))
            # extrémités sur le bord de la grille
            for p in (pts[0], pts[-1]):
                self.assertTrue(p[0] in (0.0, 39.0) or p[1] in (0.0, 49.0))

    def test_niveau_hors_champ(self):
        F = np.zeros((5, 5))
        self.assertEqual(C.contour(F, 1.0, tout_valide(F)), [])
        self.assertEqual(C.contour(F + 2, 1.0, tout_valide(F)), [])

    def test_nan_pas_de_fausse_ligne(self):
        """Un bloc de NaN : aucune ligne ne touche une arête dont une extrémité est NaN, et rien ne longe le bord du masque."""
        i, j = grille(50, 60)
        F = 0.1 * j + 0.0 * i
        F[:, :10] = np.nan                      # bande masquée à gauche
        F[30:, 40:] = np.nan                    # coin masqué
        niv = 3.05                              # colonne ~30,5 : doit rester une ligne verticale franche
        lignes = C.contour(F, niv, tout_valide(F))
        self.assertGreaterEqual(len(lignes), 1)
        for pts, _ in lignes:
            for r, c in pts:
                if abs(r - round(r)) < 1e-9:    # sommet sur une arête verticale (ligne entière) : extrémités (r, floor c) et (r, floor c + 1)
                    a, b = int(round(r)), int(np.floor(c))
                    self.assertTrue(np.isfinite(F[a, b]) and np.isfinite(F[a, b + 1]))
                else:                           # sommet sur une arête horizontale
                    a, b = int(np.floor(r)), int(round(c))
                    self.assertTrue(np.isfinite(F[a, b]) and np.isfinite(F[a + 1, b]))
        # une naïve (NaN -> 0) produirait des lignes le long du masque ; ici le niveau 0,5 (entre 0 et le champ) n'existe pas du tout
        F2 = F.copy()
        self.assertEqual(C.contour(F2, 0.5, tout_valide(F2)), [] if np.nanmin(F2) > 0.5 else C.contour(F2, 0.5, tout_valide(F2)))

    def test_nan_ligne_coupee_au_bord(self):
        """Une ligne qui rencontre une zone NaN s'arrête à sa lisière (ligne ouverte)."""
        i, j = grille(30, 30)
        F = 1.0 * i
        F[:, 20:] = np.nan
        lignes = C.contour(F, 10.5, tout_valide(F))
        self.assertEqual(len(lignes), 1)
        pts, ferme = lignes[0]
        self.assertFalse(ferme)
        self.assertLessEqual(pts[:, 1].max(), 19.0 + 1e-9)

    def test_selle(self):
        """Selle : 2 segments, deux variantes selon la valeur du centre."""
        F = np.array([[1.0, 0.0], [0.0, 1.0]])          # a = 1, b = 0, c = 1, d = 0 -> cas 5
        ok = tout_valide(F)
        e0, e1 = C.segments(F, 0.4, ok)                   # centre 0,5 >= 0,4 : le haut est relié, b et d isolés
        self.assertEqual(len(e0), 2)
        lignes = C.contour(F, 0.4, ok)
        self.assertEqual(len(lignes), 2)
        # centre bas : a et c sont isolés ; segments (L-T) et (R-B), pas de segment traversant le centre en diagonale croisée
        e0, e1 = C.segments(F, 0.6, ok)
        self.assertEqual(len(e0), 2)
        P = [C.contour(F, 0.6, ok)[k][0] for k in range(2)]
        # chaque morceau est proche d'un coin haut (a ou c)
        coins = sorted([tuple(np.round(p.mean(0), 1)) for p in P])
        self.assertEqual(coins[0][0] < 0.5 and coins[0][1] < 0.5, True)
        self.assertEqual(coins[1][0] > 0.5 and coins[1][1] > 0.5, True)

    def test_validite_des_cellules(self):
        v = np.ones((5, 5), dtype=bool)
        v[2, 2] = False
        cell = C.cellules_valides(v)
        self.assertEqual(cell.shape, (4, 4))
        self.assertEqual(int((~cell).sum()), 4)            # les 4 cellules autour du coin invalide
        self.assertEqual(int((~C.cellules_valides(v, 1)).sum()), 4 + 8)  # érosion d'une maille : 8 cellules voisines s'ajoutent aux 4 d'origine (le bord de la grille n'érode pas)


class TestAssemblage(unittest.TestCase):
    def test_chaines_et_anneau(self):
        # chaîne ouverte 1-2-3-4, anneau 10-11-12, paire isolée 20-21 ; segments mélangés et orientations mélangées
        segs = [(3, 4), (2, 1), (11, 10), (3, 2), (20, 21), (12, 11), (10, 12)]
        e0 = [s[0] for s in segs]
        e1 = [s[1] for s in segs]
        res = C.assembler(e0, e1)
        self.assertEqual(len(res), 3)
        ouvertes = [ids.tolist() for ids, ferme in res if not ferme]
        anneaux = [ids.tolist() for ids, ferme in res if ferme]
        self.assertEqual(len(anneaux), 1)
        self.assertEqual(len(ouvertes), 2)
        longue = max(ouvertes, key=len)
        self.assertIn(longue, ([1, 2, 3, 4], [4, 3, 2, 1]))
        paire = min(ouvertes, key=len)
        self.assertEqual(sorted(paire), [20, 21])
        anneau = anneaux[0]
        self.assertEqual(anneau[0], anneau[-1])
        self.assertEqual(sorted(set(anneau)), [10, 11, 12])
        self.assertEqual(len(anneau), 4)

    def test_degre_superieur_a_deux(self):
        with self.assertRaises(ValueError):
            C.assembler([1, 1, 1], [2, 3, 4])

    def test_vide(self):
        self.assertEqual(C.assembler([], []), [])

    def test_continuite_sur_un_vrai_contour(self):
        """Chaque point de la ligne assemblée est bien voisin du suivant (distance < 1,5 maille)."""
        rng = np.random.default_rng(3)
        i, j = grille(80, 80)
        F = C.lisser(rng.normal(size=(80, 80)), 3.0)
        for niv in (-0.02, 0.0, 0.02):
            for pts, _ in C.contour(F, niv, tout_valide(F)):
                self.assertLess(np.hypot(*np.diff(pts, axis=0).T).max(), 1.5)


class TestLissage(unittest.TestCase):
    def test_constante_et_nan(self):
        F = np.full((30, 30), 1013.0)
        F[:8, :8] = np.nan
        S = C.lisser(F, 3.0)
        self.assertTrue(np.array_equal(np.isnan(S), np.isnan(F)))
        self.assertTrue(np.allclose(S[np.isfinite(S)], 1013.0, atol=1e-9))     # la normalisation par le masque évite tout biais de bord

    def test_bruit_attenue(self):
        rng = np.random.default_rng(1)
        B = rng.normal(size=(120, 120))
        S = C.lisser(B, 3.0)
        # variance d'un bruit blanc après noyau gaussien 2D : ~ 1 / (4 pi sigma²) ≈ 0,0088 -> écart-type ≈ 0,094
        self.assertLess(S[20:-20, 20:-20].std(), 0.12)
        self.assertGreater(S[20:-20, 20:-20].std(), 0.07)

    def test_sigma_nul(self):
        F = np.random.default_rng(2).normal(size=(10, 10))
        self.assertTrue(np.array_equal(C.lisser(F, 0), F))

    def test_plan_conserve(self):
        i, j = grille(60, 60)
        F = 0.3 * j + 0.1 * i
        S = C.lisser(F, 2.0)
        self.assertTrue(np.allclose(S[10:-10, 10:-10], F[10:-10, 10:-10], atol=1e-9))


class TestSimplification(unittest.TestCase):
    def test_alignes(self):
        p = np.column_stack([np.linspace(0, 10, 50), np.linspace(0, 5, 50)])
        self.assertEqual(C.douglas_peucker(p, 1e-6).tolist(), [0, 49])

    def test_coin_conserve(self):
        p = np.array([[0, 0], [5, 0], [10, 0], [10, 5], [10, 10]], dtype=float)
        self.assertEqual(C.douglas_peucker(p, 0.1).tolist(), [0, 2, 4])

    def test_tolerance(self):
        p = np.array([[0, 0], [5, 0.5], [10, 0]], dtype=float)
        self.assertEqual(len(C.douglas_peucker(p, 1.0)), 2)
        self.assertEqual(len(C.douglas_peucker(p, 0.1)), 3)

    def test_anneau_garde_ferme(self):
        t = np.linspace(0, 2 * np.pi, 200)
        ll = np.column_stack([2 + np.cos(t), 46 + 0.7 * np.sin(t)])
        ll[-1] = ll[0]
        s = C.simplifier(ll, True, 1.0)
        self.assertLess(len(s), 60)
        self.assertTrue(np.allclose(s[0], s[-1]))

    def test_vers_lonlat_latitude_decroissante(self):
        lat = np.array([50.0, 49.0, 48.0])
        lon = np.array([0.0, 1.0, 2.0, 3.0])
        ll = C.vers_lonlat(np.array([[0.5, 2.25]]), lat, lon)
        self.assertTrue(np.allclose(ll, [[2.25, 49.5]]))


def bosse(shape, ci, cj, amp, sig):
    i, j = grille(*shape)
    return amp * np.exp(-((i - ci) ** 2 + (j - cj) ** 2) / (2 * sig ** 2))


class TestExtrema(unittest.TestCase):
    def champ(self):
        F = np.full((120, 160), 1015.0)
        F += bosse(F.shape, 50, 60, 12.0, 10.0)           # anticyclone, 1027 hPa
        F -= bosse(F.shape, 85, 120, 9.0, 9.0)            # dépression, 1006 hPa
        F += bosse(F.shape, 25, 25, 0.4, 5.0)             # petite bosse de 0,4 hPa : bruit
        F += bosse(F.shape, 3, 100, 6.0, 8.0)             # sommet collé au bord de la grille : pas un extremum
        return F

    def test_detection(self):
        mx, mn = X.detecter(self.champ(), 1.0, pas=2)
        self.assertEqual(len(mx), 1, mx)
        self.assertEqual(len(mn), 1, mn)
        self.assertLessEqual(abs(mx[0]["ligne"] - 50) + abs(mx[0]["colonne"] - 60), 3)
        self.assertLessEqual(abs(mn[0]["ligne"] - 85) + abs(mn[0]["colonne"] - 120), 3)
        self.assertAlmostEqual(mx[0]["valeur"], 1027.0, delta=0.3)
        self.assertAlmostEqual(mn[0]["valeur"], 1006.0, delta=0.3)
        self.assertGreater(mx[0]["proeminence"], 10.0)

    def test_seuil_et_bruit(self):
        mx, _ = X.detecter(self.champ(), 0.1, pas=2)
        self.assertGreaterEqual(len(mx), 2)                # la bosse de 0,4 hPa apparaît à seuil bas
        mx, _ = X.detecter(self.champ(), 1.0, pas=2)
        self.assertEqual(len(mx), 1)

    def test_bord_nan_exclu(self):
        F = self.champ()
        F[:, 50:75] = np.nan                               # masque qui coupe l'anticyclone (sa moitié ouest reste)
        mx, _ = X.detecter(F, 1.0, pas=2)
        self.assertEqual(len(mx), 0)                       # le sommet colle au masque : pas d'extremum fiable

    def test_champ_plan(self):
        i, j = grille(60, 60)
        mx, mn = X.detecter(0.1 * i + 0.05 * j, 0.5, pas=2)
        self.assertEqual((mx, mn), ([], []))

    def test_libelles_geojson(self):
        lat = np.linspace(55, 37, 120)
        lon = np.linspace(-12, 16, 160)
        e = K.extrema(self.champ(), lat, lon, 1.0)
        genres = sorted(x["genre"] for x in e)
        self.assertEqual(genres, ["A", "D"])
        gj = K.vers_geojson([], e, {})
        libs = {f["properties"]["genre"]: f["properties"]["libelle"] for f in gj["features"]}
        self.assertEqual(libs, {"A": "Anticyclone", "D": "Dépression"})


class TestGeoJSON(unittest.TestCase):
    def test_chaine_complete(self):
        """Mini champ canonique : isobares écrites, sommets sur le bon niveau, majeurs aux multiples de 10."""
        lat = np.linspace(48.0, 44.0, 161).astype(np.float32)
        lon = np.linspace(0.0, 6.0, 241).astype(np.float32)
        i, j = grille(161, 241)
        P = (101000.0 + 12.0 * j + 5.0 * i + 300.0 * np.sin(i / 20.0)).astype(np.float32)
        P[:20, :20] = np.nan
        P[-15:, -30:] = np.nan
        meta = {"model": "arome", "run": "2026-10-17T06:00:00Z", "step": 7}
        with tempfile.TemporaryDirectory() as d:
            np.savez(os.path.join(d, "07.npz"), lat=lat, lon=lon, mslp=P, meta=json.dumps(meta))
            st = K.traiter(os.path.join(d, "07.npz"), d, sigma=2.0, sigma_ext=2.0)
            with open(os.path.join(d, "07.geojson"), encoding="utf-8") as f:
                g = json.load(f)
            self.assertEqual(g["proprietes"]["echeance"], 7)
            lignes, _ = V.lire_lignes(os.path.join(d, "07.geojson"))
            self.assertEqual(len(lignes), st["lignes"])
            F = C.lisser(C.pa_vers_hpa(P), 2.0)
            v = V.validation_sommets(F, lat, lon, lignes, n=200)
            self.assertEqual(v["nan"], 0)
            self.assertLess(v["ecart_max_hpa"], 0.01)
            majeurs = {f["properties"]["niveau"] for f in g["features"] if f["properties"]["type"] == "isobare" and f["properties"]["majeur"]}
            self.assertTrue(all(n % 10 == 0 for n in majeurs))
            self.assertTrue(all(f["properties"]["niveau"] % 2 == 0 for f in g["features"] if f["properties"]["type"] == "isobare"))
            t = V.topologie(F, lat, lon, lignes)
            self.assertEqual(t["croisements_stricts"], 0)
            self.assertEqual(t["extremites_hors_bord"], 0)
            self.assertEqual(t["anneaux_mal_fermes"], 0)

    def test_hd_sans_mslp(self):
        with tempfile.TemporaryDirectory() as d:
            np.savez(os.path.join(d, "03.npz"), lat=np.zeros(2), lon=np.zeros(2), t2m=np.zeros((2, 2)), meta="{}")
            with self.assertRaises(KeyError):
                K.charger(os.path.join(d, "03.npz"))


class TestValidation(unittest.TestCase):
    def test_croisements(self):
        x = [(1, False, np.array([[0.0, 0.0], [1.0, 1.0]])), (1, False, np.array([[0.0, 1.0], [1.0, 0.0]]))]
        self.assertEqual(V.croisements(x), 1)
        par = [(1, False, np.array([[0.0, 0.0], [1.0, 0.0]])), (2, False, np.array([[0.0, 0.5], [1.0, 0.5]]))]
        self.assertEqual(V.croisements(par), 0)
        tou = [(1, False, np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 1.0]]))]        # segments consécutifs : pas un croisement
        self.assertEqual(V.croisements(tou), 0)

    def test_interpolation(self):
        lat = np.array([3.0, 2.0, 1.0, 0.0])
        lon = np.array([10.0, 11.0, 12.0])
        LA, LO = np.meshgrid(lat, lon, indexing="ij")
        F = 2.0 * LO + 3.0 * LA
        pts = np.array([[10.5, 1.25], [11.9, 2.9]])
        self.assertTrue(np.allclose(V.interp_bilineaire(F, lat, lon, pts), 2 * pts[:, 0] + 3 * pts[:, 1]))


class TestMVT(unittest.TestCase):
    @staticmethod
    def lire(b):
        """Mini décodeur protobuf : {champ: [valeurs]} (varint -> int, len -> bytes)."""
        out, i = {}, 0
        while i < len(b):
            cle, i = M_varint(b, i)
            num, wire = cle >> 3, cle & 7
            if wire == 0:
                v, i = M_varint(b, i)
            elif wire == 2:
                n, i = M_varint(b, i)
                v, i = b[i:i + n], i + n
            elif wire == 1:
                v, i = b[i:i + 8], i + 8
            else:
                raise ValueError(wire)
            out.setdefault(num, []).append(v)
        return out

    def test_tuile_lonlat(self):
        p = M.lonlat_vers_tuile(np.array([[0.0, 0.0], [-180.0, 85.0511287798]]), 1)
        self.assertTrue(np.allclose(p[0], [1.0, 1.0]))
        self.assertTrue(np.allclose(p[1], [0.0, 0.0], atol=1e-6))

    def test_decoupe(self):
        p = np.array([[-10.0, 5.0], [20.0, 5.0], [20.0, 30.0]])
        parts = M.decouper(p, 0, 0, 10, 10)
        self.assertEqual(len(parts), 1)
        self.assertTrue(np.allclose(parts[0], [[0, 5], [10, 5]]))
        # un tracé qui sort puis rentre donne deux morceaux
        q = np.array([[2.0, 2.0], [20.0, 2.0], [20.0, 8.0], [3.0, 8.0]])
        self.assertEqual(len(M.decouper(q, 0, 0, 10, 10)), 2)
        self.assertEqual(M.decouper(np.array([[20.0, 20.0], [30.0, 30.0]]), 0, 0, 10, 10), [])

    def test_aller_retour_geometrie(self):
        couche = M.Couche("isobares")
        couche.ajouter(2, M.geom_lignes([np.array([[10, 20], [15, 18], [15, 30]])]), {"niveau": 1020, "majeur": True})
        c = self.lire(couche.encoder())
        self.assertEqual(c[1][0], b"isobares")
        self.assertEqual(c[5][0], 4096)
        e = self.lire(c[2][0])
        self.assertEqual(e[3][0], 2)
        cmds, i, vals = [], 0, []
        g = e[4][0]
        while i < len(g):
            v, i = M_varint(g, i)
            vals.append(v)
        dz = lambda n: (n >> 1) ^ -(n & 1)
        self.assertEqual(vals[0], 9)            # MoveTo x1
        self.assertEqual((dz(vals[1]), dz(vals[2])), (10, 20))
        self.assertEqual(vals[3], (2 | (2 << 3)))   # LineTo x2
        self.assertEqual([dz(v) for v in vals[4:]], [5, -2, 0, 12])

    def test_tuiles_et_pmtiles(self):
        ll = np.array([[2.0, 46.0], [2.5, 46.2], [3.0, 46.0]])
        t = M.construire_tuiles([(1020, True, ll)], [{"genre": "A", "pression": 1030.2, "lon": 2.5, "lat": 46.1}], 3, 5)
        self.assertTrue(all(z in (3, 4, 5) for z, _, _ in t))
        for z, x, y in t:
            tuile = self.lire(gzip.decompress(t[(z, x, y)]))
            self.assertIn(3, tuile)               # au moins une couche
        try:
            from pmtiles.reader import MmapSource, Reader
        except ImportError:
            self.skipTest("paquet pmtiles absent")
        with tempfile.TemporaryDirectory() as d:
            ch = os.path.join(d, "x.pmtiles")
            M.ecrire_pmtiles(ch, t, {"name": "t"})
            with open(ch, "rb") as f:
                r = Reader(MmapSource(f))
                h = r.header()
                self.assertEqual((h["min_zoom"], h["max_zoom"]), (3, 5))
                z, x, y = sorted(t)[0]
                self.assertEqual(r.get(z, x, y), t[(z, x, y)])


def M_varint(b, i):
    n = s = 0
    while True:
        c = b[i]
        i += 1
        n |= (c & 0x7F) << s
        if not c & 0x80:
            return n, i
        s += 7


if __name__ == "__main__":
    unittest.main()
