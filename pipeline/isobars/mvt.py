# SPDX-License-Identifier: EUPL-1.2
"""Évaluation : isobares en tuiles vectorielles MVT dans un PMTiles (encodeur MVT minimal, sans dépendance).

  PYTHONPATH=<dossier pmtiles> python3 -m pipeline.isobars.mvt --iso out/isobars/arome/2026-10-17T06Z \
      --out out/isobars/arome/2026-10-17T06Z/mvt --echeances 0,6,12,24,36,51

Un PMTiles par échéance (couches `isobares` : lignes {niveau, majeur} et `extrema` : points {genre, pression, libelle}),
construit à partir du GeoJSON de la même échéance (mêmes lignes, déjà lissées et simplifiées).
Spécification MVT 2.1 : https://github.com/mapbox/vector-tile-spec/tree/master/2.1
"""
import argparse
import gzip
import json
import math
import os
import time

import numpy as np

from . import contours as C

EXTENT = 4096
TAMPON = 64          # unités de tuile de recouvrement : les libellés et traits ne sont pas coupés au joint
TOL_UNITES = 4.0     # Douglas-Peucker en unités de tuile (1/1024 de tuile, soit ~0,5 px à 512 px)

# --------------------------------------------------------------------- protobuf minimal


def _varint(n):
    out = bytearray()
    n &= (1 << 64) - 1
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _cle(num, wire):
    return _varint((num << 3) | wire)


def _champ_len(num, data):
    return _cle(num, 2) + _varint(len(data)) + data


def _champ_var(num, n):
    return _cle(num, 0) + _varint(n)


def _zigzag(n):
    return (n << 1) ^ (n >> 63)


def _valeur(v):
    """Message Value : chaîne (1), double (3), entier (4), booléen (7)."""
    if isinstance(v, bool):
        return _champ_var(7, int(v))
    if isinstance(v, int):
        return _champ_var(4, v)
    if isinstance(v, float):
        import struct
        return _cle(3, 1) + struct.pack("<d", v)
    return _champ_len(1, str(v).encode("utf-8"))


class Couche:
    """Une couche MVT : accumule des entités puis s'encode."""

    def __init__(self, nom):
        self.nom, self.entites, self.cles, self.valeurs = nom, [], {}, {}

    def _idx(self, table, x):
        k = (type(x).__name__, x)
        if k not in table:
            table[k] = len(table)
        return table[k]

    def ajouter(self, type_geom, geometrie, props):
        tags = []
        for k, v in props.items():
            tags += [self._idx(self.cles, k), self._idx(self.valeurs, v)]
        self.entites.append((type_geom, geometrie, tags))

    def encoder(self):
        out = _champ_var(15, 2) + _champ_len(1, self.nom.encode())
        for t, geom, tags in self.entites:
            e = _champ_len(2, b"".join(_varint(x) for x in tags)) + _champ_var(3, t) + \
                _champ_len(4, b"".join(_varint(x) for x in geom))
            out += _champ_len(2, e)
        for (_, k) in self.cles:
            out += _champ_len(3, k.encode())
        for (_, v) in self.valeurs:
            out += _champ_len(4, _valeur(v))
        return out + _champ_var(5, EXTENT)


def geom_lignes(parties):
    """Commandes MVT d'une liste de polylignes (entiers [N,2]) : MoveTo, LineTo, curseur relatif."""
    cmds, cx, cy = [], 0, 0
    for p in parties:
        x, y = int(p[0][0]), int(p[0][1])
        cmds += [(1 & 7) | (1 << 3), _zigzag(x - cx), _zigzag(y - cy)]
        cx, cy = x, y
        cmds.append((2 & 7) | ((len(p) - 1) << 3))
        for q in p[1:]:
            x, y = int(q[0]), int(q[1])
            cmds += [_zigzag(x - cx), _zigzag(y - cy)]
            cx, cy = x, y
    return cmds


def geom_point(x, y):
    return [(1 & 7) | (1 << 3), _zigzag(int(x)), _zigzag(int(y))]


# --------------------------------------------------------------------- géométrie de tuilage


def lonlat_vers_tuile(ll, z):
    """(lon, lat) -> coordonnées de tuile flottantes (x, y) au zoom z (Web Mercator, y vers le sud)."""
    n = 2.0 ** z
    x = (ll[:, 0] + 180.0) / 360.0 * n
    la = np.radians(ll[:, 1])
    y = (1.0 - np.log(np.tan(la) + 1.0 / np.cos(la)) / np.pi) / 2.0 * n
    return np.column_stack([x, y])


def decouper(p, xmin, ymin, xmax, ymax):
    """Découpe une polyligne [N,2] au rectangle (Liang-Barsky par segment) ; renvoie une liste de polylignes."""
    a, b = p[:-1], p[1:]
    d = b - a
    t0 = np.zeros(len(a))
    t1 = np.ones(len(a))
    ok = np.ones(len(a), dtype=bool)
    for dim, lo, hi in ((0, xmin, xmax), (1, ymin, ymax)):
        dd = d[:, dim]
        with np.errstate(divide="ignore", invalid="ignore"):
            ta = (lo - a[:, dim]) / dd
            tb = (hi - a[:, dim]) / dd
        tmin = np.where(dd == 0, -np.inf, np.minimum(ta, tb))
        tmax = np.where(dd == 0, np.inf, np.maximum(ta, tb))
        dedans = (a[:, dim] >= lo) & (a[:, dim] <= hi)
        ok &= np.where(dd == 0, dedans, True)
        t0 = np.maximum(t0, tmin)
        t1 = np.minimum(t1, tmax)
    ok &= t0 <= t1
    idx = np.nonzero(ok)[0]
    if idx.size == 0:
        return []
    s = a[idx] + d[idx] * t0[idx, None]
    e = a[idx] + d[idx] * t1[idx, None]
    parties, cur = [], [s[0], e[0]]
    for k in range(1, len(idx)):
        continu = idx[k] == idx[k - 1] + 1 and t1[idx[k - 1]] == 1.0 and t0[idx[k]] == 0.0
        if continu:
            cur.append(e[k])
        else:
            parties.append(np.array(cur))
            cur = [s[k], e[k]]
    parties.append(np.array(cur))
    return parties


def simplifier_entiers(p, tol):
    """Arrondi à l'entier, Douglas-Peucker, suppression des doublons consécutifs."""
    q = np.rint(p)
    if len(q) > 1:
        q = q[np.r_[True, np.any(q[1:] != q[:-1], axis=1)]]
    if len(q) > 2 and tol > 0:
        q = q[C.douglas_peucker(q, tol)]
    return q


def construire_tuiles(lignes, extrema, zmin, zmax):
    """{(z, x, y): octets MVT gzip}. lignes : [(niveau, majeur, coords lon/lat)], extrema : [dict]."""
    tuiles = {}
    for z in range(zmin, zmax + 1):
        n = 2 ** z
        couches = {}
        for niv, majeur, ll in lignes:
            p = lonlat_vers_tuile(ll, z)
            tx0, ty0 = np.floor(p.min(0) - TAMPON / EXTENT).astype(int)
            tx1, ty1 = np.floor(p.max(0) + TAMPON / EXTENT).astype(int)
            for tx in range(max(0, tx0), min(n - 1, tx1) + 1):
                for ty in range(max(0, ty0), min(n - 1, ty1) + 1):
                    loc = (p - [tx, ty]) * EXTENT
                    parties = decouper(loc, -TAMPON, -TAMPON, EXTENT + TAMPON, EXTENT + TAMPON)
                    parties = [simplifier_entiers(q, TOL_UNITES) for q in parties]
                    parties = [q for q in parties if len(q) >= 2]
                    if parties:
                        c = couches.setdefault((tx, ty), {}).setdefault("isobares", Couche("isobares"))
                        c.ajouter(2, geom_lignes(parties), {"niveau": int(niv), "majeur": bool(majeur)})
        for e in extrema:
            p = lonlat_vers_tuile(np.array([[e["lon"], e["lat"]]]), z)[0]
            tx, ty = int(math.floor(p[0])), int(math.floor(p[1]))
            c = couches.setdefault((tx, ty), {}).setdefault("extrema", Couche("extrema"))
            c.ajouter(1, geom_point((p[0] - tx) * EXTENT, (p[1] - ty) * EXTENT),
                      {"genre": e["genre"], "pression": float(e["pression"]),
                       "libelle": "Anticyclone" if e["genre"] == "A" else "Dépression"})
        for (tx, ty), cs in couches.items():
            tuiles[(z, tx, ty)] = gzip.compress(b"".join(_champ_len(3, c.encoder()) for c in cs.values()), 9, mtime=0)
    return tuiles


def ecrire_pmtiles(chemin, tuiles, meta):
    from pmtiles.tile import Compression, TileType, zxy_to_tileid
    from pmtiles.writer import Writer
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    e7 = lambda d: int(round(d * 1e7))
    with open(chemin + ".tmp", "wb") as f:
        w = Writer(f)
        for (z, x, y), data in sorted(tuiles.items(), key=lambda kv: zxy_to_tileid(*kv[0])):
            w.write_tile(zxy_to_tileid(z, x, y), data)
        w.finalize({"tile_type": TileType.MVT, "tile_compression": Compression.GZIP,
                    "min_lon_e7": e7(-12), "min_lat_e7": e7(37.5), "max_lon_e7": e7(16), "max_lat_e7": e7(55.4),
                    "center_zoom": 5, "center_lon_e7": e7(2.5), "center_lat_e7": e7(46.5)}, meta)
    os.replace(chemin + ".tmp", chemin)


def lire_geojson(chemin):
    with open(chemin, encoding="utf-8") as f:
        g = json.load(f)
    lignes, extr = [], []
    for f in g["features"]:
        p = f["properties"]
        if p["type"] == "isobare":
            lignes.append((p["niveau"], p["majeur"], np.array(f["geometry"]["coordinates"], dtype=np.float64)))
        else:
            c = f["geometry"]["coordinates"]
            extr.append({"genre": p["genre"], "pression": p["pression"], "lon": c[0], "lat": c[1]})
    return lignes, extr, g.get("proprietes", {})


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--iso", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--echeances", default="0,6,12,24,36,51")
    ap.add_argument("--zmin", type=int, default=3)
    ap.add_argument("--zmax", type=int, default=8)
    a = ap.parse_args()
    mesures = []
    for e in [int(x) for x in a.echeances.split(",")]:
        t0 = time.perf_counter()
        lignes, extr, entete = lire_geojson(os.path.join(a.iso, "%02d.geojson" % e))
        tuiles = construire_tuiles(lignes, extr, a.zmin, a.zmax)
        meta = {"name": "isobares", "format": "pbf", "minzoom": a.zmin, "maxzoom": a.zmax,
                "attribution": "Météo-France, Licence Ouverte v2.0", "proprietes": entete,
                "vector_layers": [
                    {"id": "isobares", "minzoom": a.zmin, "maxzoom": a.zmax, "fields": {"niveau": "Number", "majeur": "Boolean"}},
                    {"id": "extrema", "minzoom": a.zmin, "maxzoom": a.zmax, "fields": {"genre": "String", "pression": "Number", "libelle": "String"}}]}
        chemin = os.path.join(a.out, "%02d.pmtiles" % e)
        ecrire_pmtiles(chemin, tuiles, meta)
        mesures.append({"echeance": e, "tuiles": len(tuiles), "octets": os.path.getsize(chemin),
                        "octets_geojson": os.path.getsize(os.path.join(a.iso, "%02d.geojson" % e)),
                        "octets_geojson_gzip": len(gzip.compress(open(os.path.join(a.iso, "%02d.geojson" % e), "rb").read(), 6)),
                        "duree_s": round(time.perf_counter() - t0, 2)})
        print(mesures[-1])
    json.dump(mesures, open(os.path.join(a.out, "mesures.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
