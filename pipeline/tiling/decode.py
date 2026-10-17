# SPDX-License-Identifier: EUPL-1.2
"""Décodeur de référence : relit une tuile PMTiles et restitue les valeurs physiques.

Sert aux tests et à la validation croisée avec la visualisation (le client WebGL doit
retrouver les mêmes valeurs). Les valeurs « pas de donnée » (code 0) donnent NaN.
"""
import json
import math

import numpy as np

from . import encode as E


class TileFile:
    """Lecture d'un fichier PMTiles v3 produit par ce paquet."""

    def __init__(self, path):
        from pmtiles.reader import MmapSource, Reader
        self._f = open(path, "rb")
        self._r = Reader(MmapSource(self._f))
        self.header = self._r.header()
        self.meta = self._r.metadata()
        self.encoding = self.meta["encoding"]
        self.variable = self.meta["variable"]
        self.border = int(self.meta["border"])
        self.tile = int(self.meta["tile_size"])

    def close(self):
        self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def raw(self, z, x, y):
        """Octets WebP de la tuile, ou None si absente."""
        return self._r.get(z, x, y)

    def read(self, z, x, y):
        """Tuile décodée, bordure comprise : float32 [258, 258] (scalar8) ou (u, v) (wind12).

        Une tuile absente du fichier est entièrement « pas de donnée » (NaN).
        """
        n = self.tile + 2 * self.border      # tuile absente : taille nominale du fichier
        data = self.raw(z, x, y)
        if data is None:
            nan = np.full((n, n), np.nan, np.float32)
            return (nan, nan.copy()) if self.encoding in ("wind12", "wind8") else nan
        img = E.from_image(data)
        # la taille réelle (256 ou 258) fait foi : un profil peut supprimer la bordure à haut zoom
        if self.encoding == "wind12":
            return E.decode_wind12(img)
        if self.encoding == "wind8":
            return E.decode_wind8(img, float(self.meta["max"]))
        gray = img if img.ndim == 2 else img[..., 0]
        return E.decode_scalar8(gray, self.variable, int(self.meta.get("levels", E.LEVELS)),
                                (self.meta["min"], self.meta["max"]))

    def sample(self, lon, lat, z):
        """Valeur au point (lon, lat) par bilinéaire sur la texture de la tuile (comme le client).

        Utilise la bordure pour les points proches du bord de tuile. Renvoie un flottant (scalar8)
        ou (u, v) (wind12) ; NaN si un des quatre voisins est « pas de donnée ».
        """
        n = 2 ** z * self.tile
        gx = (lon + 180.0) / 360.0 * n
        gy = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
        x, y = int(gx // self.tile), int(gy // self.tile)
        px, py = gx - x * self.tile, gy - y * self.tile          # bord de pixel, 0..256
        fields = self.read(z, x, y)
        comps = fields if self.encoding in ("wind12", "wind8") else (fields,)
        b = (comps[0].shape[0] - self.tile) // 2    # bordure réelle de cette tuile (0 ou 1)
        fi, fj = px + b - 0.5, py + b - 0.5       # indice continu dans le tableau
        i0, j0 = int(math.floor(fi)), int(math.floor(fj))
        wx, wy = fi - i0, fj - j0
        res = []
        for a in comps:
            h = a.shape[0]
            ii = np.clip([i0, i0 + 1], 0, h - 1)     # sans bordure : bord répété (clamp), comme WebGL
            jj = np.clip([j0, j0 + 1], 0, h - 1)
            sub = a[np.ix_(jj, ii)].astype(np.float64)
            v = (sub[0, 0] * (1 - wx) + sub[0, 1] * wx) * (1 - wy) + (sub[1, 0] * (1 - wx) + sub[1, 1] * wx) * wy
            res.append(float(v))
        return tuple(res) if self.encoding in ("wind12", "wind8") else res[0]


def read_tile(path, z, x, y):
    with TileFile(path) as t:
        return t.read(z, x, y)
