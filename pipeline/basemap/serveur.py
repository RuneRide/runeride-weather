# SPDX-License-Identifier: EUPL-1.2
"""Petit serveur local pour la page de test : sert demo/ et les .pmtiles, avec gestion de Range.

Écoute 127.0.0.1 sur un port libre (port 0), jamais 8187, 8097 ni 8099.
Usage : python3 -m pipeline.basemap.serveur [--dossier-tuiles out/basemap] [--port N]
  /            -> pipeline/basemap/demo/
  /tuiles/     -> dossier des .pmtiles
Les requêtes sont journalisées sur stderr (méthode, chemin, Range, code).
"""
import argparse
import os
import re
import sys
import threading
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

PORTS_INTERDITS = {8187, 8097, 8099}
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


def analyser_range(entete, taille):
    """Interprète un en-tête Range « bytes=a-b » (une seule plage).

    Retourne (debut, fin_incluse), None si pas de Range, ou lève ValueError (416)."""
    if not entete:
        return None
    m = RANGE_RE.match(entete.strip())
    if not m:
        raise ValueError("Range non géré")
    a, b = m.groups()
    if a == "" and b == "":
        raise ValueError("Range vide")
    if a == "":  # suffixe : les b derniers octets
        n = int(b)
        if n == 0:
            raise ValueError("suffixe nul")
        return (max(0, taille - n), taille - 1)
    debut = int(a)
    fin = taille - 1 if b == "" else min(int(b), taille - 1)
    if debut >= taille or debut > fin:
        raise ValueError("hors fichier")
    return (debut, fin)


class Gestionnaire(SimpleHTTPRequestHandler):
    dossier_demo = None
    dossier_tuiles = None

    def translate_path(self, path):
        chemin = path.split("?", 1)[0].split("#", 1)[0]
        if chemin.startswith("/tuiles/"):
            base, rel = self.dossier_tuiles, chemin[len("/tuiles/"):]
        else:
            base, rel = self.dossier_demo, chemin.lstrip("/") or "index.html"
        cible = os.path.realpath(os.path.join(base, rel))
        if not (cible == base or cible.startswith(base + os.sep)):
            return os.path.join(base, "__interdit__")
        return cible

    def guess_type(self, path):
        if path.endswith(".pmtiles"):
            return "application/vnd.pmtiles"
        return super().guess_type(path)

    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def send_head(self):
        chemin = self.translate_path(self.path)
        if os.path.isdir(chemin) or not os.path.isfile(chemin):
            return super().send_head()
        taille = os.path.getsize(chemin)
        try:
            plage = analyser_range(self.headers.get("Range"), taille)
        except ValueError:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{taille}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return None
        f = open(chemin, "rb")
        if plage is None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Length", str(taille))
            self.restant = None
        else:
            debut, fin = plage
            f.seek(debut)
            self.send_response(HTTPStatus.PARTIAL_CONTENT)
            self.send_header("Content-Range", f"bytes {debut}-{fin}/{taille}")
            self.send_header("Content-Length", str(fin - debut + 1))
            self.restant = fin - debut + 1
        self.send_header("Content-Type", self.guess_type(chemin))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        return f

    def copyfile(self, source, sortie):
        restant = getattr(self, "restant", None)
        while True:
            n = 65536 if restant is None else min(65536, restant)
            if n == 0:
                break
            bloc = source.read(n)
            if not bloc:
                break
            try:
                sortie.write(bloc)
            except (BrokenPipeError, ConnectionResetError):
                break
            if restant is not None:
                restant -= len(bloc)
        self.restant = None

    def log_message(self, fmt, *args):
        sys.stderr.write("%s Range=%s %s\n" % (self.command, self.headers.get("Range"), fmt % args))


def creer_serveur(dossier_demo, dossier_tuiles, port=0):
    if port in PORTS_INTERDITS:
        raise ValueError(f"port {port} interdit")
    Gestionnaire.dossier_demo = os.path.realpath(dossier_demo)
    Gestionnaire.dossier_tuiles = os.path.realpath(dossier_tuiles)
    srv = ThreadingHTTPServer(("127.0.0.1", port), Gestionnaire)
    if srv.server_address[1] in PORTS_INTERDITS:
        srv.server_close()
        return creer_serveur(dossier_demo, dossier_tuiles, 0)
    return srv


def main(argv=None):
    ici = os.path.dirname(os.path.abspath(__file__))
    racine = os.path.dirname(os.path.dirname(ici))
    p = argparse.ArgumentParser()
    p.add_argument("--dossier-tuiles", default=os.path.join(racine, "out", "basemap"))
    p.add_argument("--port", type=int, default=0, help="0 = port libre choisi par le système")
    a = p.parse_args(argv)
    srv = creer_serveur(os.path.join(ici, "demo"), a.dossier_tuiles, a.port)
    print(f"http://127.0.0.1:{srv.server_address[1]}/", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
