# SPDX-License-Identifier: EUPL-1.2
"""Joue l'API Runtime de Lambda (faux serveur local) pour vérifier le binaire `bootstrap` **avec** `lambda_runtime`.

Usage : python3 -I essai_runtime_lambda.py ./bootstrap evenement1.json [evenement2.json …]
Chaque fichier est servi comme une invocation (GET /2018-06-01/runtime/invocation/next) ; la réponse que le
binaire POSTe (/response ou /error) est affichée en JSON, une ligne par invocation. L'environnement
(RW_*, AWS_REGION, identifiants factices…) est celui du processus appelant. Le binaire est arrêté par son PID.
Code de sortie 0 si chaque invocation a reçu une réponse (et non une erreur de runtime).
"""
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

EVENEMENTS = []
REPONSES = []
SUIVANT = [0]
VERROU = threading.Condition()


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (ConnectionError, TimeoutError):  # le binaire est arrêté de force à la fin
            self.close_connection = True

    def do_GET(self):
        if not self.path.endswith("/invocation/next"):
            self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
        with VERROU:
            while SUIVANT[0] >= len(EVENEMENTS):
                if not VERROU.wait(timeout=30):
                    self.send_response(204); self.send_header("Content-Length", "0"); self.end_headers(); return
            i = SUIVANT[0]
            SUIVANT[0] += 1
        corps = EVENEMENTS[i]
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Lambda-Runtime-Aws-Request-Id", "req-%d" % i)
        self.send_header("Lambda-Runtime-Deadline-Ms", str(int(time.time() * 1000) + 120000))
        self.send_header("Lambda-Runtime-Invoked-Function-Arn", "arn:aws:lambda:eu-north-1:000000000000:function:rw-worker")
        self.send_header("Lambda-Runtime-Trace-Id", "Root=1-00000000-000000000000000000000000")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        corps = self.rfile.read(n)
        with VERROU:
            REPONSES.append({"chemin": self.path, "corps": corps.decode()})
            VERROU.notify_all()
        self.send_response(202)
        self.send_header("Content-Length", "0")
        self.end_headers()


def main():
    binaire, fichiers = sys.argv[1], sys.argv[2:]
    for f in fichiers:
        with open(f, "rb") as fh:
            EVENEMENTS.append(fh.read())
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    env = dict(os.environ)
    env.update({
        "AWS_LAMBDA_RUNTIME_API": "127.0.0.1:%d" % srv.server_address[1],
        "AWS_LAMBDA_FUNCTION_NAME": "rw-worker", "AWS_LAMBDA_FUNCTION_VERSION": "$LATEST",
        "AWS_LAMBDA_FUNCTION_MEMORY_SIZE": "1769", "AWS_LAMBDA_LOG_GROUP_NAME": "/aws/lambda/rw-worker",
        "AWS_LAMBDA_LOG_STREAM_NAME": "essai", "_HANDLER": "bootstrap",
    })
    p = subprocess.Popen([binaire], env=env)
    code = 0
    try:
        with VERROU:
            fin = time.time() + 300
            while len(REPONSES) < len(EVENEMENTS) and time.time() < fin and p.poll() is None:
                VERROU.wait(timeout=1)
    finally:
        p.kill()
        p.wait()
        srv.shutdown()
    for r in REPONSES:
        print(json.dumps(r, ensure_ascii=False))
        if "/error" in r["chemin"]:
            code = 1
    if len(REPONSES) != len(EVENEMENTS):
        code = 2
    sys.exit(code)


main()
