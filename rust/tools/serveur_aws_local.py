# SPDX-License-Identifier: EUPL-1.2
"""Faux services pour tester `rw-poller` et `rw-manifeste` sans réseau ni AWS (Python standard uniquement).

Un seul port, quatre rôles :

* **S3 public de Météo-France (OVH)** : `GET /?list-type=2&prefix=pnt/...` liste les fichiers de `--ovh` (un dossier qui
  reproduit `pnt/<tirage>/arome/<res>/<paquet>/<fichier>` ; la date de modification des fichiers fait foi : les tests
  la fixent avec `os.utime`). Pas de signature. `--ovh-pages N` limite le nombre d'objets par page (test de pagination).
* **S3 « path-style »** `/<bucket>/<clé>` : PUT, GET, HEAD, DELETE et listage (`GET /<bucket>?list-type=2&prefix=&delimiter=/`,
  pagination par `continuation-token`), signature SigV4 **vérifiée** (secret factice `--secret`, 403 sinon). Les en-têtes
  `Content-Type` et `Cache-Control` des PUT sont conservés dans `<s3>/.meta/<bucket>/<clé>.json`.
* **SQS** (protocole JSON) : `POST /` avec `X-Amz-Target: AmazonSQS.SendMessageBatch`, signature SigV4 (service `sqs`)
  vérifiée ; les entrées reçues sont ajoutées à `--sqs` (JSON, une ligne par message).
* Pannes injectables : `--sqs-refuser ID` (l'entrée d'identifiant ID est refusée dans `Failed`), `--sqs-echecs N` (les N
  premiers `SendMessageBatch` reçoivent un 500), `--s3-echecs N` (les N premiers PUT reçoivent un 503).

Chaque requête est ajoutée en JSON (une ligne) à `--journal`. Arrêt : signal à l'identifiant de processus (jamais
`pkill -f`) ; le port et le PID sont affichés au démarrage (`PORT=… PID=…`).
Usage : python3 -I serveur_aws_local.py --ovh /tmp/ovh --s3 /tmp/s3 --sqs /tmp/sqs.jsonl --journal /tmp/j.jsonl [--port 0]
"""
import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote
from xml.sax.saxutils import escape

ARGS = None
VERROU = threading.RLock()
ECHECS = {"s3": 0, "sqs": 0}


def journal(entree):
    if ARGS.journal:
        with VERROU, open(ARGS.journal, "a", encoding="utf-8") as f:
            f.write(json.dumps(entree, ensure_ascii=False) + "\n")


def hmac_sha(cle, msg):
    return hmac.new(cle, msg.encode(), hashlib.sha256).digest()


def signature_valide(h, methode, chemin_brut, requete, service, corps):
    """Recalcule la signature SigV4 de la requête et la compare à celle reçue."""
    auth = h.headers.get("Authorization", "")
    m = re.match(r"AWS4-HMAC-SHA256 Credential=([^/]+)/(\d{8})/([^/]+)/([^/]+)/aws4_request, SignedHeaders=([^,]+), Signature=([0-9a-f]{64})$", auth)
    if not m:
        return False, "Authorization absent ou mal formé"
    cle, jour, region, svc, signes, signature = m.groups()
    if cle != ARGS.cle_acces or region != ARGS.region or svc != service:
        return False, "clé d'accès, région ou service inattendu"
    entetes = "".join("%s:%s\n" % (n, " ".join((h.headers.get(n) or "").split())) for n in signes.split(";"))
    date = h.headers.get("x-amz-date", "")
    if not date.startswith(jour):
        return False, "date incohérente"
    charge = h.headers.get("x-amz-content-sha256") or hashlib.sha256(corps).hexdigest()
    if service == "s3" and charge != hashlib.sha256(corps).hexdigest():
        return False, "x-amz-content-sha256 différent du corps"
    canon = "\n".join([methode, chemin_brut, requete, entetes, signes, charge])
    portee = "%s/%s/%s/aws4_request" % (jour, region, service)
    a_signer = "\n".join(["AWS4-HMAC-SHA256", date, portee, hashlib.sha256(canon.encode()).hexdigest()])
    k = hmac_sha(("AWS4" + ARGS.secret).encode(), jour)
    for part in (region, service, "aws4_request"):
        k = hmac_sha(k, part)
    attendu = hmac.new(k, a_signer.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(attendu, signature), "signature différente"


def iso(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def xml_liste(entrees_objets, prefixes, max_keys, jeton, truncated, suivant):
    x = ['<?xml version="1.0" encoding="UTF-8"?>\n<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">']
    x.append("<Name>faux</Name><KeyCount>%d</KeyCount><MaxKeys>%d</MaxKeys>" % (len(entrees_objets) + len(prefixes), max_keys))
    x.append("<IsTruncated>%s</IsTruncated>" % ("true" if truncated else "false"))
    if suivant:
        x.append("<NextContinuationToken>%s</NextContinuationToken>" % escape(suivant))
    for cle, taille, mtime, etag in entrees_objets:
        x.append("<Contents><Key>%s</Key><LastModified>%s</LastModified><ETag>&quot;%s&quot;</ETag><Size>%d</Size><StorageClass>STANDARD</StorageClass></Contents>"
                 % (escape(cle), iso(mtime), etag, taille))
    for p in prefixes:
        x.append("<CommonPrefixes><Prefix>%s</Prefix></CommonPrefixes>" % escape(p))
    x.append("</ListBucketResult>")
    return "".join(x).encode()


def lister(racine, prefixe, delimiteur, max_keys, jeton, relatif_a=""):
    """Entrées triées (objets et préfixes communs) sous `racine`, page à partir de `jeton`."""
    objets, prefixes = {}, set()
    for dossier, _dirs, fichiers in os.walk(racine):
        for f in fichiers:
            chemin = os.path.join(dossier, f)
            cle = os.path.relpath(chemin, racine).replace(os.sep, "/")
            if f.endswith(".tmp") or not cle.startswith(prefixe):
                continue
            reste = cle[len(prefixe):]
            if delimiteur and delimiteur in reste:
                prefixes.add(prefixe + reste.split(delimiteur)[0] + delimiteur)
                continue
            st = os.stat(chemin)
            etag = hashlib.md5(("%s-%d" % (cle, st.st_size)).encode()).hexdigest() if st.st_size > 1 << 22 else hashlib.md5(open(chemin, "rb").read()).hexdigest()
            objets[cle] = (cle, st.st_size, int(st.st_mtime), etag)
    tout = sorted([(k, "o") for k in objets] + [(p, "p") for p in prefixes])
    debut = 0
    if jeton:
        debut = next((i for i, (k, _) in enumerate(tout) if k > jeton), len(tout))
    page = tout[debut:debut + max_keys]
    truncated = debut + max_keys < len(tout)
    suivant = page[-1][0] if truncated and page else None
    return (xml_liste([objets[k] for k, t in page if t == "o"], [k for k, t in page if t == "p"], max_keys, jeton, truncated, suivant))


class Gestionnaire(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "serveur-aws-local-rw/1"

    def log_message(self, *a):
        pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionError, TimeoutError):
            self.close_connection = True

    def repondre(self, code, corps=b"", entetes=None, tete_seule=False, longueur=None):
        self.send_response(code)
        for k, v in (entetes or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(corps) if longueur is None else longueur))
        self.end_headers()
        if not tete_seule and corps:
            self.wfile.write(corps)

    def note(self, code, service, extra=None):
        e = {"service": service, "methode": self.command, "chemin": self.path, "statut": code, "user_agent": self.headers.get("User-Agent")}
        e.update(extra or {})
        journal(e)

    def corps(self):
        return self.rfile.read(int(self.headers.get("Content-Length", "0")))

    # --- OVH : listage public
    def ovh(self, requete):
        q = parse_qs(requete, keep_blank_values=True)
        prefixe = q.get("prefix", [""])[0]
        if ".." in prefixe.split("/"):
            self.note(400, "ovh")
            return self.repondre(400, b"<Error/>")
        max_keys = min(int(q.get("max-keys", ["1000"])[0]), ARGS.ovh_pages or 1000)
        jeton = q.get("continuation-token", [""])[0]
        corps = lister(ARGS.ovh, prefixe, q.get("delimiter", [""])[0], max_keys, jeton)
        self.note(200, "ovh", {"prefixe": prefixe})
        self.repondre(200, corps, {"Content-Type": "application/xml"})

    # --- S3
    def s3(self, methode):
        brut, _, requete = self.path.partition("?")
        corps = self.corps() if methode in ("PUT", "POST") else b""
        ok, raison = signature_valide(self, methode, brut, requete, "s3", corps)
        if not ok:
            self.note(403, "s3", {"raison": raison})
            return self.repondre(403, ("<Error><Code>SignatureDoesNotMatch</Code><Message>%s</Message></Error>" % raison).encode(), {"Content-Type": "application/xml"}, methode == "HEAD")
        bucket, _, cle = unquote(brut).lstrip("/").partition("/")
        if bucket != ARGS.bucket or ".." in cle.split("/"):
            self.note(400, "s3")
            return self.repondre(400, b"<Error><Code>InvalidRequest</Code></Error>", None, methode == "HEAD")
        racine = os.path.join(ARGS.s3, bucket)
        if not cle and methode == "GET":
            q = parse_qs(requete, keep_blank_values=True)
            if q.get("list-type") != ["2"]:
                self.note(400, "s3")
                return self.repondre(400, b"<Error><Code>InvalidArgument</Code></Error>")
            corps = lister(racine, q.get("prefix", [""])[0], q.get("delimiter", [""])[0], int(q.get("max-keys", ["1000"])[0]), q.get("continuation-token", [""])[0])
            self.note(200, "s3", {"liste": q.get("prefix", [""])[0]})
            return self.repondre(200, corps, {"Content-Type": "application/xml"})
        fichier = os.path.join(racine, cle)
        meta = os.path.join(ARGS.s3, ".meta", bucket, cle + ".json")
        if methode == "PUT":
            with VERROU:
                if ECHECS["s3"] > 0:
                    ECHECS["s3"] -= 1
                    self.note(503, "s3", {"cle": cle})
                    return self.repondre(503, b"<Error><Code>SlowDown</Code></Error>")
            os.makedirs(os.path.dirname(fichier), exist_ok=True)
            os.makedirs(os.path.dirname(meta), exist_ok=True)
            tmp = fichier + ".tmp"
            with open(tmp, "wb") as f:
                f.write(corps)
            os.replace(tmp, fichier)
            with open(meta, "w") as f:
                json.dump({"content-type": self.headers.get("Content-Type"), "cache-control": self.headers.get("Cache-Control"),
                           "x-amz-security-token": self.headers.get("x-amz-security-token"), "octets": len(corps)}, f)
            self.note(200, "s3", {"cle": cle, "octets": len(corps), "cache_control": self.headers.get("Cache-Control")})
            return self.repondre(200, b"", {"ETag": '"%s"' % hashlib.md5(corps).hexdigest()})
        if methode == "DELETE":
            for p in (fichier, meta):
                try:
                    os.remove(p)
                except FileNotFoundError:
                    pass
            self.note(204, "s3", {"cle": cle})
            return self.repondre(204)
        if not os.path.isfile(fichier):
            self.note(404, "s3", {"cle": cle})
            return self.repondre(404, b"<Error><Code>NoSuchKey</Code></Error>", {"Content-Type": "application/xml"}, methode == "HEAD")
        with open(fichier, "rb") as f:
            donnees = f.read()
        self.note(200, "s3", {"cle": cle})
        self.repondre(200, donnees, {"Content-Type": "application/octet-stream"}, methode == "HEAD", longueur=len(donnees))

    # --- SQS
    def sqs(self):
        corps = self.corps()
        brut, _, requete = self.path.partition("?")
        ok, raison = signature_valide(self, "POST", brut, requete, "sqs", corps)
        if not ok:
            self.note(403, "sqs", {"raison": raison})
            return self.repondre(403, json.dumps({"__type": "InvalidSignatureException", "message": raison}).encode())
        if self.headers.get("X-Amz-Target") != "AmazonSQS.SendMessageBatch" or "json" not in (self.headers.get("Content-Type") or ""):
            self.note(400, "sqs", {"cible": self.headers.get("X-Amz-Target")})
            return self.repondre(400, b'{"__type":"UnknownOperationException"}')
        with VERROU:
            if ECHECS["sqs"] > 0:
                ECHECS["sqs"] -= 1
                self.note(500, "sqs")
                return self.repondre(500, b'{"__type":"InternalFailure"}')
        req = json.loads(corps)
        entrees = req.get("Entries", [])
        ids = [e["Id"] for e in entrees]
        if not entrees or len(entrees) > 10 or len(set(ids)) != len(ids) or any(not re.match(r"^[A-Za-z0-9_-]{1,80}$", i) for i in ids):
            self.note(400, "sqs", {"raison": "lot invalide"})
            return self.repondre(400, b'{"__type":"BatchEntryIdsNotDistinct"}')
        if req.get("QueueUrl", "").endswith(".fifo") and any("MessageGroupId" not in e or "MessageDeduplicationId" not in e for e in entrees):
            self.note(400, "sqs", {"raison": "FIFO sans groupe ou déduplication"})
            return self.repondre(400, b'{"__type":"MissingParameter"}')
        reussis, refuses = [], []
        for e in entrees:
            if e["Id"] in ARGS.sqs_refuser:
                refuses.append({"Id": e["Id"], "SenderFault": False, "Code": "InternalError", "Message": "refus injecté"})
                continue
            reussis.append({"Id": e["Id"], "MessageId": "m-%s" % hashlib.md5(e["MessageBody"].encode()).hexdigest()[:12], "MD5OfMessageBody": hashlib.md5(e["MessageBody"].encode()).hexdigest()})
            with VERROU, open(ARGS.sqs, "a", encoding="utf-8") as f:
                f.write(json.dumps({"file": req.get("QueueUrl"), "id": e["Id"], "corps": json.loads(e["MessageBody"]),
                                    "groupe": e.get("MessageGroupId"), "deduplication": e.get("MessageDeduplicationId"),
                                    "jeton": self.headers.get("x-amz-security-token")}, ensure_ascii=False) + "\n")
        self.note(200, "sqs", {"entrees": len(entrees), "refuses": len(refuses)})
        self.repondre(200, json.dumps({"Successful": reussis, "Failed": refuses}).encode(), {"Content-Type": "application/x-amz-json-1.0"})

    def route(self, methode):
        brut, _, requete = self.path.partition("?")
        if methode == "POST" and brut == "/":
            return self.sqs()
        if methode == "GET" and brut == "/" and "list-type=2" in requete:
            return self.ovh(requete)
        return self.s3(methode)

    def do_GET(self):
        self.route("GET")

    def do_HEAD(self):
        self.route("HEAD")

    def do_PUT(self):
        self.route("PUT")

    def do_DELETE(self):
        self.route("DELETE")

    def do_POST(self):
        self.route("POST")


def main():
    global ARGS
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--ovh", required=True, help="dossier reproduisant le S3 public (contient pnt/…)")
    ap.add_argument("--ovh-pages", type=int, default=0, help="objets par page au plus (0 = 1000)")
    ap.add_argument("--s3", required=True, help="racine du faux S3")
    ap.add_argument("--sqs", required=True, help="fichier JSON-lignes recevant les messages SQS")
    ap.add_argument("--bucket", default="bucket-test")
    ap.add_argument("--region", default="eu-north-1")
    ap.add_argument("--cle-acces", default="AKIDEXEMPLEFACTICE")
    ap.add_argument("--secret", default="secret-factice-pour-les-tests")
    ap.add_argument("--journal", default="")
    ap.add_argument("--sqs-refuser", action="append", default=[], help="identifiant d'entrée refusé par SendMessageBatch")
    ap.add_argument("--sqs-echecs", type=int, default=0)
    ap.add_argument("--s3-echecs", type=int, default=0)
    ARGS = ap.parse_args()
    ECHECS["s3"], ECHECS["sqs"] = ARGS.s3_echecs, ARGS.sqs_echecs
    os.makedirs(os.path.join(ARGS.s3, ARGS.bucket), exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", ARGS.port), Gestionnaire)
    srv.daemon_threads = True
    print("PORT=%d PID=%d" % (srv.server_address[1], os.getpid()), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
