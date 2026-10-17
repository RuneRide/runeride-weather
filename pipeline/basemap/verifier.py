# SPDX-License-Identifier: EUPL-1.2
"""Vérification d'un extrait : cohérence de l'en-tête, couverture exacte des tuiles voulues,
relecture de N tuiles tirées au hasard et comparaison octet pour octet avec la source distante.

Usage : python3 -m pipeline.basemap.verifier extrait.pmtiles --source URL [--n 50] [--graine 1]
"""
import argparse
import json
import os
import random
import sys

from pmtiles.reader import MmapSource, Reader, all_tiles
from pmtiles.tile import tileid_to_zxy

from . import tuiles as T
from .extract import lire_metadonnees
from .reseau import Client


def source_http(client, url):
    """get_bytes distant avec cache (la bibliothèque relit l'en-tête à chaque tuile)."""
    cache = {}

    def get_bytes(offset, length):
        k = (offset, length)
        if k not in cache:
            cache[k] = client.plage(url, offset, length)
        return cache[k]
    return get_bytes


def verifier(chemin, url, n=50, graine=1, journal=print):
    client = Client(pause=0.05)
    res = {"controles": [], "ok": True}

    def ctrl(nom, ok, detail=""):
        res["controles"].append({"nom": nom, "ok": bool(ok), "detail": detail})
        res["ok"] &= bool(ok)
        journal(f"[{'OK' if ok else 'ÉCHEC'}] {nom} {detail}")

    with open(chemin, "rb") as f:
        taille = os.fstat(f.fileno()).st_size
        local = Reader(MmapSource(f))
        h = local.header()
        meta = local.metadata()
        src = Reader(source_http(client, url))
        hs = src.header()
        ms = lire_metadonnees(client, url, hs)

        # (a) cohérence de l'en-tête
        ctrl("version 3", h["version"] == 3)
        ctrl("type de tuile conservé", h["tile_type"] == hs["tile_type"], str(h["tile_type"]))
        ctrl("compression des tuiles conservée", h["tile_compression"] == hs["tile_compression"],
             str(h["tile_compression"]))
        zooms = set()
        ids = []
        for (z, x, y), _ in all_tiles(MmapSource(f)):
            ids.append((z, x, y))
            zooms.add(z)
        ctrl("zooms min/max de l'en-tête = zooms réels", (h["min_zoom"], h["max_zoom"]) == (min(zooms), max(zooms)),
             f"en-tête {h['min_zoom']}-{h['max_zoom']}, réel {min(zooms)}-{max(zooms)}")
        bbox = tuple(v for v in meta["runeride:extraction"]["bbox"])
        voulus = T.ids_voulus(bbox, h["min_zoom"], h["max_zoom"])
        from pmtiles.tile import zxy_to_tileid
        obtenus = sorted(zxy_to_tileid(*t) for t in ids)
        ctrl("ensemble des tuiles = celles recoupant la boîte", voulus == obtenus,
             f"{len(obtenus)} présentes, {len(voulus)} attendues")
        ctrl("addressed_tiles_count = nombre de tuiles", h["addressed_tiles_count"] == len(obtenus),
             str(h["addressed_tiles_count"]))
        ctrl("tile_contents_count <= addressed_tiles_count", 0 < h["tile_contents_count"] <= h["addressed_tiles_count"],
             str(h["tile_contents_count"]))
        e7 = (h["min_lon_e7"], h["min_lat_e7"], h["max_lon_e7"], h["max_lat_e7"])
        attendu = T.bbox_vers_e7(bbox)
        ctrl("emprise de l'en-tête = boîte demandée", e7 == attendu, f"{[v / 1e7 for v in e7]}")
        ctrl("emprise : ouest<est et sud<nord", e7[0] < e7[2] and e7[1] < e7[3])
        ctrl("centre dans l'emprise", e7[0] <= h["center_lon_e7"] <= e7[2] and e7[1] <= h["center_lat_e7"] <= e7[3],
             f"z{h['center_zoom']}")
        fin = h["tile_data_offset"] + h["tile_data_length"]
        ctrl("sections contiguës et fichier de la bonne taille",
             h["root_offset"] + h["root_length"] == h["metadata_offset"]
             and h["metadata_offset"] + h["metadata_length"] == h["leaf_directory_offset"]
             and h["leaf_directory_offset"] + h["leaf_directory_length"] == h["tile_data_offset"]
             and fin == taille, f"{taille} octets")
        ctrl("répertoire racine <= 16384-127 octets", h["root_length"] <= 16384 - 127, str(h["root_length"]))
        ctrl("clustered", h["clustered"])
        m2 = {k: v for k, v in meta.items() if k != "runeride:extraction"}
        ctrl("métadonnées de la source conservées (attribution, vector_layers...)", m2 == ms,
             f"attribution={meta.get('attribution')!r}, {len(meta.get('vector_layers', []))} couches")

        # (b) 50 tuiles au hasard, octet pour octet
        rnd = random.Random(graine)
        echantillon = rnd.sample(ids, min(n, len(ids)))
        diff = []
        for z, x, y in echantillon:
            a = local.get(z, x, y)
            b = src.get(z, x, y)
            if a is None or b is None or bytes(a) != bytes(b):
                diff.append((z, x, y))
        ctrl(f"{len(echantillon)} tuiles aléatoires identiques à la source (octet pour octet)", not diff,
             f"graine {graine} ; zooms {sorted({t[0] for t in echantillon})} ; différences : {diff}")
        res["requetes_verification"] = client.stats.dict()
    return res


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("fichier")
    p.add_argument("--source", required=True)
    p.add_argument("--n", type=int, default=50)
    p.add_argument("--graine", type=int, default=random.SystemRandom().randrange(10**6))
    a = p.parse_args(argv)
    r = verifier(a.fichier, a.source, a.n, a.graine, journal=lambda s: print(s, file=sys.stderr))
    print(json.dumps(r, indent=2, ensure_ascii=False))
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
