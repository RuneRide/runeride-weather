# SPDX-License-Identifier: EUPL-1.2
"""Analyse des plages de glyphes (polices) nécessaires à l'affichage d'un extrait PMTiles.

MapLibre demande les polices par paquets de 256 points de code (`{fontstack}/{debut}-{fin}.pbf`).
On lit les noms des objets (name, name:fr, name:en, name2, name3) dans les tuiles vectorielles
(MVT, protobuf décodé à la main : aucun code de la source n'est exécuté) et on en déduit les plages.

Usage : python3 -m pipeline.basemap.glyphes extrait.pmtiles [--bbox O,S,E,N] [--zoom-max Z]
"""
import argparse
import gzip
import json
import sys

from pmtiles.reader import MmapSource, all_tiles

from . import tuiles as T

CLES_NOMS = ("name", "name:fr", "name:en", "name2", "name3")
COUCHES_TEXTE = {"places", "roads", "water", "pois", "earth", "boundaries", "landuse", "buildings"}


def _varint(b, i):
    r = s = 0
    while True:
        c = b[i]
        i += 1
        r |= (c & 0x7F) << s
        if not c & 0x80:
            return r, i
        s += 7


def _champs(b):
    """Itère (numéro, type, valeur) d'un message protobuf ; valeur = int ou octets."""
    i, n = 0, len(b)
    while i < n:
        cle, i = _varint(b, i)
        num, typ = cle >> 3, cle & 7
        if typ == 0:
            v, i = _varint(b, i)
        elif typ == 2:
            ln, i = _varint(b, i)
            v = b[i:i + ln]
            i += ln
        elif typ == 1:
            v = b[i:i + 8]
            i += 8
        elif typ == 5:
            v = b[i:i + 4]
            i += 4
        else:
            raise ValueError("type protobuf non géré")
        yield num, typ, v


def noms_de_tuile(donnees_gzip):
    """Itère (couche, cle, texte) pour les clés de nom des objets d'une tuile MVT gzip."""
    tuile = gzip.decompress(donnees_gzip)
    for num, typ, couche in _champs(tuile):
        if num != 3 or typ != 2:
            continue
        nom, cles, valeurs, objets = "", [], [], []
        for n2, t2, v2 in _champs(couche):
            if n2 == 1:
                nom = bytes(v2).decode()
            elif n2 == 2:
                objets.append(v2)
            elif n2 == 3:
                cles.append(bytes(v2).decode())
            elif n2 == 4:
                texte = ""
                for n3, t3, v3 in _champs(v2):
                    if n3 == 1:
                        texte = bytes(v3).decode()
                valeurs.append(texte)
        if nom not in COUCHES_TEXTE:
            continue
        utiles = {i for i, k in enumerate(cles) if k in CLES_NOMS}
        for o in objets:
            for n3, t3, v3 in _champs(o):
                if n3 == 2 and t3 == 2:  # tags empaquetés : paires (clé, valeur)
                    j, vals = 0, []
                    while j < len(v3):
                        x, j = _varint(v3, j)
                        vals.append(x)
                    for a, b in zip(vals[0::2], vals[1::2]):
                        if a in utiles and b < len(valeurs):
                            yield nom, cles[a], valeurs[b]


def plages_de_texte(textes):
    """Ensemble des plages de 256 points de code (début) nécessaires pour ces textes."""
    return {ord(c) // 256 * 256 for t in textes for c in t}


def nom_plage(debut):
    return f"{debut}-{debut + 255}"


def analyser(chemin, bbox=None, zoom_max=None):
    """Plages par couche pour les tuiles de l'extrait (filtrées par boîte et zoom)."""
    ids = None
    if bbox is not None:
        ids = set(T.ids_voulus(bbox, 0, zoom_max if zoom_max is not None else 15))
    from pmtiles.tile import zxy_to_tileid
    par_couche, tout = {}, set()
    nb = 0
    with open(chemin, "rb") as f:
        for (z, x, y), data in all_tiles(MmapSource(f)):
            if zoom_max is not None and z > zoom_max:
                continue
            if ids is not None and zxy_to_tileid(z, x, y) not in ids:
                continue
            nb += 1
            for couche, cle, texte in noms_de_tuile(bytes(data)):
                par_couche.setdefault(couche, set()).update(texte)
    for chars in par_couche.values():
        tout |= {ord(c) // 256 * 256 for c in chars}
    return {"tuiles_analysees": nb,
            "plages": sorted(tout),
            "par_couche": {k: sorted({ord(c) // 256 * 256 for c in v}) for k, v in sorted(par_couche.items())},
            "caracteres_hors_latin1": sorted({c for v in par_couche.values() for c in v if ord(c) > 255})}


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("fichier")
    p.add_argument("--bbox")
    p.add_argument("--zoom-max", type=int)
    a = p.parse_args(argv)
    bbox = tuple(float(v) for v in a.bbox.split(",")) if a.bbox else None
    r = analyser(a.fichier, bbox, a.zoom_max)
    r["plages_noms"] = [nom_plage(d) for d in r["plages"]]
    print(json.dumps(r, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
