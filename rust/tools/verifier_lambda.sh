#!/usr/bin/env bash
# SPDX-License-Identifier: EUPL-1.2
# Vérifie `rw-lambda` de bout en bout, sans AWS : source GRIB et S3 simulés par serveur_local.py, puis
# comparaison des tuiles et des champs canoniques avec la référence Python (cmp_tiles.py, cmp_canon.py).
# À lancer depuis la racine du dépôt. Variables : PYLIB_PMTILES (dossier du paquet Python `pmtiles`, v3.x),
# OUT (dossier de travail, défaut /tmp/rw-lambda-verif). Prérequis : out/rust/src, out/rust/py (voir reproduire.sh).
set -euo pipefail
: "${PYLIB_PMTILES:?dossier contenant le paquet pmtiles}"
OUT=$(realpath -m "${OUT:-/tmp/rw-lambda-verif}")
HD=2026-10-17T03:00:00Z
AR=2026-10-17T06:00:00Z
(cd rust && cargo build --release -p rw-lambda)
BOOT=rust/target/release/bootstrap
rm -rf "$OUT" && mkdir -p "$OUT/s3"

python3 -I rust/tools/serveur_local.py --source out/rust/src --s3 "$OUT/s3" --journal "$OUT/journal.jsonl" > "$OUT/serveur.log" 2>&1 &
PID=$!
trap 'kill "$PID" 2>/dev/null || true' EXIT   # arrêt par identifiant de processus, jamais par nom
until grep -q '^PORT=' "$OUT/serveur.log"; do sleep 0.2; done
PORT=$(sed -n 's/^PORT=\([0-9]*\).*/\1/p' "$OUT/serveur.log")

export RW_SOURCE="http://127.0.0.1:$PORT/pnt" RW_S3_ENDPOINT="http://127.0.0.1:$PORT" RW_BUCKET=bucket-test
export AWS_REGION=eu-north-1 AWS_ACCESS_KEY_ID=AKIDEXEMPLEFACTICE AWS_SECRET_ACCESS_KEY=secret-factice-pour-les-tests
export RW_ECRIRE_CANON=1 RW_PAUSE_MS=100

evenement() { # modèle tirage échéance
  printf '{"Records":[{"messageId":"m","receiptHandle":"r","body":"{\\"modele\\":\\"%s\\",\\"tirage\\":\\"%s\\",\\"echeance\\":%s}","attributes":{},"messageAttributes":{},"md5OfBody":"x","eventSource":"aws:sqs","eventSourceARN":"arn:aws:sqs:eu-north-1:000000000000:fictive","awsRegion":"eu-north-1"}]}' "$1" "$2" "$3"
}
for step in 0 12 24 36 48; do
  for mt in "arome-hd $HD" "arome $AR"; do
    set -- $mt
    evenement "$1" "$2" "$step" > "$OUT/evt.json"
    "$BOOT" --evenement "$OUT/evt.json" 2> "$OUT/log-$1-$step.jsonl" | grep -qx '{"batchItemFailures":\[\]}'
  done
done
echo "échéances traitées ; durées (s) :"
cat "$OUT"/log-*.jsonl | python3 -I -c 'import sys,json
for l in sys.stdin:
    d=json.loads(l); print(d["modele"], d["echeance"], d["statut"], round(d["duree_s"],2))'

# vue des seules variables tuilées (sans _etat, _canon, _isobares…) dans la disposition attendue par cmp_tiles.py
for mt in "arome-hd 2026-10-17T03Z" "arome 2026-10-17T06Z"; do
  set -- $mt
  mkdir -p "$OUT/tuiles/$1/$2"
  for d in "$OUT/s3/bucket-test/v1/$1/$2"/*/; do
    n=$(basename "$d"); case "$n" in _*) ;; *) ln -sfn "${d%/}" "$OUT/tuiles/$1/$2/$n";; esac
  done
done
for step in 0 12 24 36 48; do
  for mt in "arome-hd 2026-10-17T03Z" "arome 2026-10-17T06Z"; do
    set -- $mt
    PYTHONPATH="$PYLIB_PMTILES:$PWD" python3 rust/tools/cmp_tiles.py out/rust/py/tiles "$OUT/tuiles" "$1" "$2" "$step" > "$OUT/cmp-tuiles-$1-$step.json"
    python3 rust/tools/cmp_canon.py "out/rust/py/canon/$1/$2/$(printf %02d "$step").npz" "$OUT/s3/bucket-test/v1/$1/$2/_canon/$(printf %02d "$step").npz" > "$OUT/cmp-canon-$1-$step.json"
  done
done
python3 -I - "$OUT" <<'PY'
import glob, json, sys
pire_codes, pire_pct, canon_ok, n = 0, 100.0, True, 0
for f in glob.glob(sys.argv[1] + "/cmp-tuiles-*.json"):
    for var, r in json.load(open(f)).items():
        if isinstance(r, str):  # variable sans fichier à cette échéance (gust10, precip_rate… à l'échéance 0) : rien à comparer
            continue
        n += 1
        pire_codes = max(pire_codes, r["ecart_max_codes"]); pire_pct = min(pire_pct, r["pct_codes_identiques"])
        assert r["metadonnees_identiques"] and not r["entete_diff"], (f, var, r)
for f in glob.glob(sys.argv[1] + "/cmp-canon-*.json"):
    for var, r in json.load(open(f))["variables"].items():
        canon_ok &= isinstance(r, dict) and r["identiques_bit"]
print("tuiles comparées (modèle, échéance, variable) : %d ; pourcentage minimal de codes identiques : %.4f ; écart max : %d code(s)" % (n, pire_pct, pire_codes))
print("champs canoniques identiques bit à bit :", canon_ok)
sys.exit(0 if canon_ok and pire_codes == 0 else 1)
PY
