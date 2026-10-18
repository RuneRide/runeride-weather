# SPDX-License-Identifier: EUPL-1.2
"""Serveur HTTP local pour tester `rw-lambda` sans réseau ni AWS.

Il joue deux rôles sur un même port :

* source GRIB (S3 public OVH de Météo-France) : `GET|HEAD /pnt/<tirage>/arome/<res>/<paquet>/<nom>` sert le
  fichier `<nom>` trouvé dans `--source` (404 s'il n'existe pas), avec `Range` ;
* faux S3 « path-style » : `PUT|GET|HEAD /<bucket>/<clé>` stocke dans `--s3`, **vérifie la signature SigV4**
  avec le secret factice `--secret` (403 si elle est fausse), et conserve `Content-Type` / `Cache-Control`
  dans `<s3>/.meta/<bucket>/<clé>.json`.

Pannes injectables : `--tronquer-premiere-requete` (la première lecture de chaque GRIB est coupée au tiers,
pour tester la reprise par `Range`), `--s3-echecs N` (les N premiers PUT reçoivent un 503).
Chaque requête est ajoutée en JSON (une ligne) à `--journal`.

Arrêt : signal à l'identifiant de processus (jamais `pkill -f`) ; le port et le PID sont affichés au démarrage.
Usage : python3 -I serveur_local.py --source out/rust/src --s3 /tmp/s3 --journal /tmp/j.jsonl [--port 0]
"""
import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, unquote

ARGS = None
VERROU = threading.RLock()
VUS = set()
PUT_ECHECS = [0]


def journal(entree):
    if ARGS.journal:
        with VERROU, open(ARGS.journal, "a", encoding="utf-8") as f:
            f.write(json.dumps(entree, ensure_ascii=False) + "\n")


def hmac_sha(cle, msg):
    return hmac.new(cle, msg.encode(), hashlib.sha256).digest()


def signature_valide(h, methode, chemin_brut, requete):
    """Recalcule la signature SigV4 de la requête et la compare à celle reçue."""
    auth = h.headers.get("Authorization", "")
    m = re.match(r"AWS4-HMAC-SHA256 Credential=([^/]+)/(\d{8})/([^/]+)/s3/aws4_request, SignedHeaders=([^,]+), Signature=([0-9a-f]{64})$", auth)
    if not m:
        return False, "Authorization absent ou mal formé"
    cle, jour, region, signes, signature = m.groups()
    if cle != ARGS.cle_acces or region != ARGS.region:
        return False, "clé d'accès ou région inattendue"
    noms = signes.split(";")
    entetes = "".join("%s:%s\n" % (n, " ".join((h.headers.get(n) or "").split())) for n in noms)
    date = h.headers.get("x-amz-date", "")
    if not date.startswith(jour):
        return False, "date incohérente"
    charge = h.headers.get("x-amz-content-sha256", "")
    canon = "\n".join([methode, chemin_brut, requete, entetes, signes, charge])
    portee = "%s/%s/s3/aws4_request" % (jour, region)
    a_signer = "\n".join(["AWS4-HMAC-SHA256", date, portee, hashlib.sha256(canon.encode()).hexdigest()])
    k = hmac_sha(("AWS4" + ARGS.secret).encode(), jour)
    for part in (region, "s3", "aws4_request"):
        k = hmac_sha(k, part)
    attendu = hmac.new(k, a_signer.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(attendu, signature), "signature différente"


class Gestionnaire(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "serveur-local-rw/1"

    def log_message(self, *a):  # silence
        pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionError, TimeoutError):  # client parti (coupures simulées comprises)
            self.close_connection = True

    # --- utilitaires
    def repondre(self, code, corps=b"", entetes=None, tete_seule=False, longueur=None):
        self.send_response(code)
        for k, v in (entetes or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(corps) if longueur is None else longueur))
        self.end_headers()
        if not tete_seule and corps:
            self.wfile.write(corps)

    def note(self, code, extra=None):
        e = {"methode": self.command, "chemin": self.path, "statut": code, "user_agent": self.headers.get("User-Agent"),
             "range": self.headers.get("Range")}
        e.update(extra or {})
        journal(e)

    # --- source GRIB
    def source(self, tete):
        chemin = unquote(self.path.split("?")[0])
        m = re.match(r"^/pnt/(\d{4}-\d{2}-\d{2}T\d{2}:00:00Z)/arome/(0025|001)/(SP1|SP2|SP3|HP1)/([A-Za-z0-9_.:-]+\.grib2)$", chemin)
        fichier = os.path.join(ARGS.source, m.group(4)) if m else None
        if not m or not os.path.isfile(fichier):
            self.note(404)
            return self.repondre(404, b"<Error><Code>NoSuchKey</Code></Error>", {"Content-Type": "application/xml"}, tete)
        taille = os.path.getsize(fichier)
        debut, fin, code = 0, taille - 1, 200
        rg = self.headers.get("Range")
        entetes = {"Content-Type": "application/octet-stream", "Accept-Ranges": "bytes"}
        if rg:
            r = re.match(r"bytes=(\d+)-(\d*)$", rg)
            if not r or int(r.group(1)) >= taille:
                self.note(416)
                return self.repondre(416, b"", {"Content-Range": "bytes */%d" % taille}, tete)
            debut = int(r.group(1))
            fin = int(r.group(2)) if r.group(2) else taille - 1
            code = 206
            entetes["Content-Range"] = "bytes %d-%d/%d" % (debut, fin, taille)
        n = fin - debut + 1
        self.send_response(code)
        for k, v in entetes.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(n))
        self.end_headers()
        coupe = False
        with VERROU:
            if ARGS.tronquer_premiere_requete and not tete and fichier not in VUS:
                VUS.add(fichier)
                coupe = True
        self.note(code, {"octets_annonces": n, "coupe": coupe})
        if tete:
            return
        a_envoyer = n // 3 if coupe else n
        with open(fichier, "rb") as f:
            f.seek(debut)
            while a_envoyer > 0:
                bloc = f.read(min(1 << 20, a_envoyer))
                if not bloc:
                    break
                self.wfile.write(bloc)
                a_envoyer -= len(bloc)
        if coupe:
            self.wfile.flush()
            self.close_connection = True
            self.connection.shutdown(2)

    # --- faux S3
    def s3(self, methode):
        brut, _, requete = self.path.partition("?")
        ok, raison = signature_valide(self, methode, brut, requete)
        if not ok:
            self.note(403, {"raison": raison})
            return self.repondre(403, ("<Error><Code>SignatureDoesNotMatch</Code><Message>%s</Message></Error>" % raison).encode(),
                                 {"Content-Type": "application/xml"}, methode == "HEAD")
        chemin = unquote(brut)
        bucket, _, cle = chemin.lstrip("/").partition("/")
        if not cle or ".." in cle.split("/") or bucket != ARGS.bucket:
            self.note(400)
            return self.repondre(400, b"<Error><Code>InvalidRequest</Code></Error>", None, methode == "HEAD")
        fichier = os.path.join(ARGS.s3, bucket, cle)
        meta = os.path.join(ARGS.s3, ".meta", bucket, cle + ".json")
        if methode == "PUT":
            n = int(self.headers.get("Content-Length", "0"))
            corps = self.rfile.read(n)
            with VERROU:
                if PUT_ECHECS[0] > 0:
                    PUT_ECHECS[0] -= 1
                    self.note(503, {"cle": cle})
                    return self.repondre(503, b"<Error><Code>SlowDown</Code></Error>")
            if hashlib.sha256(corps).hexdigest() != self.headers.get("x-amz-content-sha256"):
                self.note(400, {"cle": cle, "raison": "x-amz-content-sha256 différent du corps"})
                return self.repondre(400, b"<Error><Code>XAmzContentSHA256Mismatch</Code></Error>")
            os.makedirs(os.path.dirname(fichier), exist_ok=True)
            os.makedirs(os.path.dirname(meta), exist_ok=True)
            tmp = fichier + ".tmp"
            with open(tmp, "wb") as f:
                f.write(corps)
            os.replace(tmp, fichier)
            with open(meta, "w") as f:
                json.dump({"content-type": self.headers.get("Content-Type"), "cache-control": self.headers.get("Cache-Control"),
                           "x-amz-security-token": self.headers.get("x-amz-security-token"), "octets": n}, f)
            self.note(200, {"cle": cle, "octets": n, "cache_control": self.headers.get("Cache-Control"),
                            "content_type": self.headers.get("Content-Type")})
            return self.repondre(200, b"", {"ETag": '"%s"' % hashlib.md5(corps).hexdigest()})
        if not os.path.isfile(fichier):
            self.note(404, {"cle": cle})
            return self.repondre(404, b"<Error><Code>NoSuchKey</Code></Error>", {"Content-Type": "application/xml"}, methode == "HEAD")
        with open(fichier, "rb") as f:
            corps = f.read()
        self.note(200, {"cle": cle})
        self.repondre(200, corps, {"Content-Type": "application/octet-stream"}, methode == "HEAD", longueur=len(corps))

    def route(self, methode):
        if self.path.startswith("/pnt/") and methode in ("GET", "HEAD"):
            return self.source(methode == "HEAD")
        return self.s3(methode)

    def do_GET(self):
        self.route("GET")

    def do_HEAD(self):
        self.route("HEAD")

    def do_PUT(self):
        self.route("PUT")


def main():
    global ARGS
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--source", required=True, help="dossier contenant les .grib2 (liens acceptés)")
    ap.add_argument("--s3", required=True, help="racine du faux S3")
    ap.add_argument("--bucket", default="bucket-test")
    ap.add_argument("--region", default="eu-north-1")
    ap.add_argument("--cle-acces", default="AKIDEXEMPLEFACTICE")
    ap.add_argument("--secret", default="secret-factice-pour-les-tests")
    ap.add_argument("--journal", default="")
    ap.add_argument("--tronquer-premiere-requete", action="store_true")
    ap.add_argument("--s3-echecs", type=int, default=0)
    ARGS = ap.parse_args()
    PUT_ECHECS[0] = ARGS.s3_echecs
    srv = ThreadingHTTPServer(("127.0.0.1", ARGS.port), Gestionnaire)
    srv.daemon_threads = True
    print("PORT=%d PID=%d" % (srv.server_address[1], os.getpid()), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
