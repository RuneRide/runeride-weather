#!/usr/bin/env python3
# SPDX-License-Identifier: EUPL-1.2
"""Serveur de test local du viewer RuneRide Météo (PoC J1).

- sert la page (dossier viewer/) à la racine ;
- monte out/tiles sous /data/ (et, si présents, out/tiles-synth sous /data-synth/,
  out/tiles-reduit sous /data-reduit/ (profil de poids reduit), out/tiles-synth-sans sous /data-sans/, out/tiles-synth-brute sous /data-brute/) ;
- gère les requêtes HTTP Range (206, 416) indispensables à PMTiles, et sert les
  .pmtiles en application/octet-stream ;
- /<montage>/index.json : inventaire dynamique {modèle: {tirage: {variable: [échéances]}}}
  (le PoC n'a pas encore de manifest, spécifications §5.3) ;
- écoute sur 127.0.0.1 UNIQUEMENT. Port par défaut 8187 (8097 et 8099 sont occupés).

Usage : python3 viewer/serve.py [--port 8187] [--mesures DOSSIER]
L'option --mesures active POST /__mesures (la page de bench y dépose son JSON).
"""
import argparse
import json
import mimetypes
import os
import re
import sys
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

RACINE = os.path.dirname(os.path.abspath(__file__))
DEPOT = os.path.dirname(RACINE)
MONTAGES = {
    "/data": os.path.join(DEPOT, "out", "tiles"),
    "/data-reduit": os.path.join(DEPOT, "out", "tiles-reduit"),
    "/data-synth": os.path.join(DEPOT, "out", "tiles-synth"),
    "/data-sans": os.path.join(DEPOT, "out", "tiles-synth-sans"),
    "/data-brute": os.path.join(DEPOT, "out", "tiles-synth-brute"),
}
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")
MESURES_DIR = None


def inventaire(dossier):
    """Parcourt <modèle>/<tirage>/<variable>/<NN>.pmtiles ; ignore tout le reste."""
    res = {}
    if not os.path.isdir(dossier):
        return res
    for modele in sorted(os.listdir(dossier)):
        for tirage in sorted(os.listdir(os.path.join(dossier, modele))) if os.path.isdir(os.path.join(dossier, modele)) else []:
            base = os.path.join(dossier, modele, tirage)
            if not os.path.isdir(base):
                continue
            for var in sorted(os.listdir(base)):
                vd = os.path.join(base, var)
                if not os.path.isdir(vd):
                    continue
                ech = sorted(int(f[:-8]) for f in os.listdir(vd)
                             if f.endswith(".pmtiles") and f[:-8].isdigit() and os.path.getsize(os.path.join(vd, f)) > 127)
                if ech:
                    res.setdefault(modele, {}).setdefault(tirage, {})[var] = ech
    return res


class Handler(SimpleHTTPRequestHandler):
    server_version = "RuneRideViewer/0.1"
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map,
                      ".pmtiles": "application/octet-stream", ".js": "text/javascript",
                      ".mjs": "text/javascript", ".json": "application/json", ".webp": "image/webp"}

    def _resoudre(self):
        """URL -> (chemin disque, montage) ; None si hors des dossiers autorisés."""
        chemin = self.path.split("?", 1)[0].split("#", 1)[0]
        from urllib.parse import unquote
        chemin = unquote(chemin)
        for pref, dossier in MONTAGES.items():
            if chemin == pref or chemin.startswith(pref + "/"):
                rel, base = chemin[len(pref):].lstrip("/"), dossier
                break
        else:
            rel, base = chemin.lstrip("/"), RACINE
        cible = os.path.realpath(os.path.join(base, rel))
        if cible != os.path.realpath(base) and not cible.startswith(os.path.realpath(base) + os.sep):
            return None
        return cible

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")  # développement : pas de cache surprise
        self.send_header("Access-Control-Allow-Origin", "*")
        super().end_headers()

    def do_GET(self):
        self._servir(corps=True)

    def do_HEAD(self):
        self._servir(corps=False)

    def do_POST(self):
        if self.path != "/__mesures" or not MESURES_DIR:
            return self.send_error(HTTPStatus.NOT_FOUND)
        n = min(int(self.headers.get("Content-Length", 0)), 1_000_000)
        donnees = self.rfile.read(n)
        try:
            obj = json.loads(donnees)
        except ValueError:
            return self.send_error(HTTPStatus.BAD_REQUEST)
        nom = re.sub(r"[^A-Za-z0-9_.-]", "_", str(obj.get("nom", "mesures")))[:60]
        os.makedirs(MESURES_DIR, exist_ok=True)
        with open(os.path.join(MESURES_DIR, nom + ".json"), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=1)
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def _servir(self, corps):
        chemin_url = self.path.split("?", 1)[0]
        for pref, dossier in MONTAGES.items():
            if chemin_url == pref + "/index.json":
                data = json.dumps(inventaire(dossier), ensure_ascii=False).encode()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                if corps:
                    self.wfile.write(data)
                return
        cible = self._resoudre()
        if cible is None:
            return self.send_error(HTTPStatus.FORBIDDEN)
        if os.path.isdir(cible):
            cible = os.path.join(cible, "index.html")
        if not os.path.isfile(cible):
            return self.send_error(HTTPStatus.NOT_FOUND)
        taille = os.path.getsize(cible)
        type_ = mimetypes.guess_type(cible)[0]
        if cible.endswith(".pmtiles"):
            type_ = "application/octet-stream"
        type_ = self.extensions_map.get(os.path.splitext(cible)[1], type_ or "application/octet-stream")
        debut, fin, statut = 0, taille - 1, HTTPStatus.OK
        entete_range = self.headers.get("Range")
        if entete_range:
            m = RANGE_RE.match(entete_range.strip())
            if not m or (m.group(1) == "" and m.group(2) == ""):
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", "bytes */%d" % taille)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if m.group(1) == "":  # suffixe : les n derniers octets
                debut, fin = max(0, taille - int(m.group(2))), taille - 1
            else:
                debut = int(m.group(1))
                fin = min(int(m.group(2)), taille - 1) if m.group(2) else taille - 1
            if debut >= taille or debut > fin:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", "bytes */%d" % taille)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            statut = HTTPStatus.PARTIAL_CONTENT
        longueur = fin - debut + 1
        self.send_response(statut)
        self.send_header("Content-Type", type_)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(longueur))
        if statut == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (debut, fin, taille))
        self.end_headers()
        if not corps:
            return
        with open(cible, "rb") as f:
            f.seek(debut)
            restant = longueur
            while restant > 0:
                bloc = f.read(min(65536, restant))
                if not bloc:
                    break
                try:
                    self.wfile.write(bloc)
                except (BrokenPipeError, ConnectionResetError):
                    break
                restant -= len(bloc)

    def log_message(self, fmt, *args):
        if os.environ.get("VIEWER_LOG"):
            super().log_message(fmt, *args)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8187)
    ap.add_argument("--mesures", help="dossier où POST /__mesures dépose les JSON de bench")
    a = ap.parse_args()
    if a.port in (8097, 8099):
        sys.exit("ports 8097 et 8099 réservés à d'autres services")
    global MESURES_DIR
    MESURES_DIR = a.mesures
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print("viewer : http://127.0.0.1:%d/  (Ctrl+C pour arrêter)" % a.port, flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
