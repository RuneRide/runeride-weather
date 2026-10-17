# SPDX-License-Identifier: EUPL-1.2
"""Ligne de commande : python3 -m pipeline.basemap --bbox O,S,E,N --maxzoom Z --out F.pmtiles"""
import argparse
import datetime
import json
import sys

from . import tuiles as T
from .extract import CONCURRENCE_MAX, extraire
from .reseau import Client


def main(argv=None):
    p = argparse.ArgumentParser(prog="python3 -m pipeline.basemap",
                                description="Extrait une zone d'un build quotidien Protomaps (PMTiles v3).")
    p.add_argument("--bbox", required=True, help="ouest,sud,est,nord en degrés")
    p.add_argument("--maxzoom", type=int, required=True)
    p.add_argument("--minzoom", type=int, default=0)
    p.add_argument("--out", required=True)
    p.add_argument("--date", help="forcer le build AAAAMMJJ (sinon le plus récent avec HTTP 200)")
    p.add_argument("--jours-max", type=int, default=14, help="jours à remonter pour trouver un build")
    p.add_argument("--concurrence", type=int, default=CONCURRENCE_MAX, help=f"1 à {CONCURRENCE_MAX}")
    p.add_argument("--pause", type=float, default=0.05, help="attente (s) après chaque requête, par fil")
    p.add_argument("--a-blanc", action="store_true",
                   help="ne lit que les répertoires : compte les tuiles et leur poids exact, sans les télécharger")
    p.add_argument("--mesures", help="écrit les mesures en JSON dans ce fichier")
    a = p.parse_args(argv)

    bbox = tuple(float(v) for v in a.bbox.split(","))
    client = Client()
    if a.date:
        d = datetime.datetime.strptime(a.date, "%Y%m%d").date()
        url = T.url_build(d)
    else:
        aujourdhui = datetime.datetime.now(datetime.timezone.utc).date()
        d, url = T.choisir_build(aujourdhui, client.sonde, a.jours_max)
    print(f"Build retenu : {d:%Y%m%d} ({url})", file=sys.stderr)
    m = extraire(url, bbox, a.maxzoom, a.out, zoom_min=a.minzoom, concurrence=a.concurrence,
                 pause=a.pause, a_blanc=a.a_blanc, date_build=f"{d:%Y%m%d}",
                 journal=lambda s: print(s, file=sys.stderr))
    m["requetes_sonde_build"] = client.stats.dict()
    texte = json.dumps(m, indent=2, ensure_ascii=False)
    if a.mesures:
        with open(a.mesures, "w") as f:
            f.write(texte)
    print(texte)


if __name__ == "__main__":
    main()
