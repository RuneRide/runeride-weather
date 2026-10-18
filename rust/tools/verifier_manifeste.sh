#!/usr/bin/env bash
# SPDX-License-Identifier: EUPL-1.2
# Vérifie `rw-manifeste` sur de vraies données, sans AWS : le worker `rw-lambda` produit les PMTiles et les états
# d'échéance (HD 03Z et AROME 06Z du 17/10/2026, échéances 0, 12, 24, 36, 48) dans un dossier ; le faux S3 local
# (serveur_aws_local.py, listage et SigV4 vérifiés) sert ce dossier à `rw-manifeste`, dont le manifeste est comparé à celui du
# générateur Python de référence (`python3 -m pipeline.manifest`) lancé sur les mêmes tuiles.
# À lancer depuis la racine du dépôt. Variables : PYLIB_PMTILES (dossier du paquet Python `pmtiles`, v3.x),
# OUT (dossier de travail, défaut /tmp/rw-manifeste-verif). Prérequis : out/rust/src (GRIB, voir reproduire.sh).
set -euo pipefail
: "${PYLIB_PMTILES:?dossier contenant le paquet pmtiles}"
OUT=$(realpath -m "${OUT:-/tmp/rw-manifeste-verif}")
HD=2026-10-17T03:00:00Z
AR=2026-10-17T06:00:00Z
(cd rust && cargo build --release -p rw-lambda -p rw-manifeste)
rm -rf "$OUT" && mkdir -p "$OUT/s3/bucket-test" "$OUT/ovh"

# 1. le worker écrit directement dans le dossier du faux S3 (source GRIB : dossier local)
export RW_SOURCE="dossier:$PWD/out/rust/src" RW_SORTIE="local:$OUT/s3/bucket-test" RW_ECRIRE_CANON=1 RW_ECHEANCE_DOMAINE=0
evenement() { # modèle tirage échéance
  printf '{"Records":[{"messageId":"m","receiptHandle":"r","body":"{\\"modele\\":\\"%s\\",\\"tirage\\":\\"%s\\",\\"echeance\\":%s}","attributes":{},"messageAttributes":{},"md5OfBody":"x","eventSource":"aws:sqs","eventSourceARN":"arn:aws:sqs:eu-north-1:000000000000:fictive","awsRegion":"eu-north-1"}]}' "$1" "$2" "$3"
}
for step in 0 12 24 36 48; do
  for mt in "arome-hd $HD" "arome $AR"; do
    set -- $mt
    evenement "$1" "$2" "$step" > "$OUT/evt.json"
    rust/target/release/bootstrap --evenement "$OUT/evt.json" 2> "$OUT/log-$1-$step.jsonl" | grep -qx '{"batchItemFailures":\[\]}'
  done
done
echo "échéances traitées par le worker : 10"

# 2. vues pour le générateur Python : tuiles seules (sans _etat, _canon…) et champs canoniques
mkdir -p "$OUT/tuiles" "$OUT/canon"
for mt in "arome-hd 2026-10-17T03Z" "arome 2026-10-17T06Z"; do
  set -- $mt
  mkdir -p "$OUT/tuiles/$1/$2" "$OUT/canon/$1"
  for d in "$OUT/s3/bucket-test/v1/$1/$2"/*/; do
    n=$(basename "$d"); case "$n" in _canon) ln -sfn "${d%/}" "$OUT/canon/$1/$2";; _*) ;; *) ln -sfn "${d%/}" "$OUT/tuiles/$1/$2/$n";; esac
  done
done
PYTHONPATH="$PYLIB_PMTILES:$PWD" python3 -m pipeline.manifest --tiles "$OUT/tuiles" --canon "$OUT/canon" --out "$OUT/manifest-python.json" --generated 2026-10-17T20:00:00Z > "$OUT/python.log"

# 3. le faux S3 sert le dossier (PID noté, arrêt par PID jamais par nom) ; rw-manifeste reconstruit le manifeste
python3 -I rust/tools/serveur_aws_local.py --ovh "$OUT/ovh" --s3 "$OUT/s3" --sqs "$OUT/sqs.jsonl" --journal "$OUT/journal.jsonl" > "$OUT/serveur.log" 2>&1 &
PID=$!
trap 'kill "$PID" 2>/dev/null || true' EXIT
until grep -q '^PORT=' "$OUT/serveur.log"; do sleep 0.2; done
PORT=$(sed -n 's/^PORT=\([0-9]*\).*/\1/p' "$OUT/serveur.log")
echo '{"maintenant": "2026-10-17T20:00:00Z"}' > "$OUT/evt-manifeste.json"
env -i RW_BUCKET=bucket-test AWS_REGION=eu-north-1 AWS_ACCESS_KEY_ID=AKIDEXEMPLEFACTICE AWS_SECRET_ACCESS_KEY=secret-factice-pour-les-tests \
  RW_S3_ENDPOINT="http://127.0.0.1:$PORT" rust/target/release/rw-manifeste --evenement "$OUT/evt-manifeste.json" > "$OUT/bilan.json" 2> "$OUT/manifeste.log"
cp "$OUT/s3/bucket-test/v1/manifest.json" "$OUT/manifest-rust.json"

# 4. comparaison : hors `generated`, `traite` (date de fichier côté Python, date de l'état côté Rust), `latest_complete`
#    (champ ajouté) et texte de `domain.source`, les deux manifestes doivent être identiques
python3 -I - "$OUT" <<'PY'
import json, sys
o = sys.argv[1]
r = json.load(open(o + "/manifest-rust.json")); p = json.load(open(o + "/manifest-python.json"))
def norm(m):
    m = json.loads(json.dumps(m)); m.pop("generated")
    for mod in m["models"].values():
        mod.pop("latest_complete", None); mod["domain"].pop("source")
        for run in mod["runs"]: run.pop("traite")
    return m
a, b = norm(r), norm(p)
print("modèles :", sorted(r["models"]), "| tirages :", {k: [x["id"] for x in v["runs"]] for k, v in r["models"].items()})
print("latest_complete :", {k: v.get("latest_complete") for k, v in r["models"].items()})
print("sommets du polygone :", {k: v["domain"]["vertices"] for k, v in r["models"].items()}, "| sources :", {k: v["domain"]["source"] for k, v in r["models"].items()})
print("clés de premier niveau dans le même ordre :", list(r) == list(p))
print("MANIFESTES IDENTIQUES (hors generated, traite, latest_complete, domain.source) :", a == b)
if a != b:
    for k in a:
        if a[k] != b[k]:
            print("  différence dans", k)
    sys.exit(1)
PY
