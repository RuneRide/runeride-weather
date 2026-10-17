# SPDX-License-Identifier: EUPL-1.2
"""Géométrie Web Mercator XYZ et rééchantillonnage bilinéaire d'une grille lat/lon."""
import math

import numpy as np

from .encode import BORDER, STORED, TILE

MAX_LAT = 85.0511287798066


def lonlat_to_tile(lon, lat, z):
    """Formule XYZ standard (OSM) : (x, y) entiers de la tuile contenant le point."""
    n = 2 ** z
    x = int(math.floor((lon + 180.0) / 360.0 * n))
    lr = math.radians(lat)
    y = int(math.floor((1.0 - math.asinh(math.tan(lr)) / math.pi) / 2.0 * n))
    return min(max(x, 0), n - 1), min(max(y, 0), n - 1)


def tile_lon_lat(z, x, y):
    """Coin nord-ouest de la tuile (lon, lat) en degrés."""
    n = 2 ** z
    lon = x / n * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    return lon, lat


def tiles_covering(lon_min, lat_min, lon_max, lat_max, z):
    """Tuiles (x, y) du zoom z qui recoupent l'emprise (bornes incluses)."""
    x0, y1 = lonlat_to_tile(lon_min, lat_min, z)
    x1, y0 = lonlat_to_tile(lon_max, lat_max, z)
    return [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]


def pixel_lonlat(z, x, y, border=BORDER):
    """Longitudes [w] et latitudes [h] des centres de pixels d'une tuile avec bordure.

    Indices de pixel -border .. 255+border ; centre du pixel i en x_global = (x*256 + i + 0.5).
    """
    n = 2 ** z * TILE
    i = np.arange(-border, TILE + border, dtype=np.float64) + 0.5
    gx = (x * TILE + i) / n
    gy = (y * TILE + i) / n
    lon = gx * 360.0 - 180.0
    lat = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * gy))))
    return lon, lat


class Grid:
    """Grille régulière lat (décroissante) / lon (croissante), et indices bilinéaires."""

    def __init__(self, lat, lon):
        self.lat = np.asarray(lat, dtype=np.float64)
        self.lon = np.asarray(lon, dtype=np.float64)
        self.nlat, self.nlon = len(self.lat), len(self.lon)
        self.dlat = (self.lat[-1] - self.lat[0]) / (self.nlat - 1)   # < 0
        self.dlon = (self.lon[-1] - self.lon[0]) / (self.nlon - 1)
        if (np.abs(np.diff(self.lat) - self.dlat).max() > 1e-4 * abs(self.dlat) + 1e-5
                or np.abs(np.diff(self.lon) - self.dlon).max() > 1e-4 * abs(self.dlon) + 1e-5):
            raise ValueError("grille non régulière")
        self.bounds = (self.lon[0], self.lat[-1], self.lon[-1], self.lat[0])  # lon_min, lat_min, lon_max, lat_max

    def axis_weights(self, coord, axis):
        """(i0, frac, valide) pour des coordonnées 1D ; valide = dans l'emprise de la grille."""
        if axis == "lat":
            f = (coord - self.lat[0]) / self.dlat
            n = self.nlat
        else:
            f = (coord - self.lon[0]) / self.dlon
            n = self.nlon
        eps = 1e-9
        valid = (f >= -eps) & (f <= n - 1 + eps)
        f = np.clip(f, 0, n - 1)
        i0 = np.minimum(np.floor(f).astype(np.int64), n - 2)
        return i0, f - i0, valid

    def sample(self, field, lon, lat):
        """Bilinéaire de `field` [nlat, nlon] sur le produit cartésien lat x lon.

        NaN si hors emprise ou si l'un des quatre voisins est NaN.
        """
        j0, fy, vy = self.axis_weights(lat, "lat")
        i0, fx, vx = self.axis_weights(lon, "lon")
        J, I = j0[:, None], i0[None, :]
        FY, FX = fy[:, None], fx[None, :]
        a = field[J, I]
        b = field[J, I + 1]
        c = field[J + 1, I]
        d = field[J + 1, I + 1]
        out = (a * (1 - FX) + b * FX) * (1 - FY) + (c * (1 - FX) + d * FX) * FY
        bad = np.isnan(a) | np.isnan(b) | np.isnan(c) | np.isnan(d)
        out = out.astype(np.float32)
        out[bad] = np.nan
        out[~(vy[:, None] & vx[None, :])] = np.nan
        return out
