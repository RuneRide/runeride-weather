#!/usr/bin/env bash
# SPDX-License-Identifier: EUPL-1.2
# Reproduit le banc Python / Rust du PoC (docs/poc-rust/synthese.md).
# À lancer depuis la racine du dépôt. Variables à définir :
#   PYLIB_ECCODES : dossier contenant le paquet Python `eccodes`
#   PYLIB_PMTILES : dossier contenant le paquet Python `pmtiles` (v3.x)
#   GRIB_00H06H   : dossier contenant arome__0025__SP{1,2}__00H06H__2026-10-17T06:00:00Z.grib2
# Les GRIB attendus sont dans out/grib (voir pipeline/ingest/download.py pour les retélécharger).
set -euo pipefail
: "${PYLIB_ECCODES:?}" "${PYLIB_PMTILES:?}" "${GRIB_00H06H:?}"
HD=2026-10-17T03:00:00Z
AR=2026-10-17T06:00:00Z

(cd rust && cargo build --release && cargo test --release)

# 1. dossier de sources : liens vers les seuls GRIB nécessaires (échéances 0, 12, 24, 36, 48 et leurs n-1)
mkdir -p out/rust/src && cd out/rust/src
for n in 0 11 12 23 24 35 36 47 48; do
  for p in SP1 SP2; do
    f=arome__001__${p}__$(printf %02d "$n")H__$HD.grib2
    ln -sf "../../grib/arome-hd/$HD/$f" "$f"
  done
done
for l in 07H12H 19H24H 31H36H 43H48H; do
  for p in SP1 SP2; do
    f=arome__0025__${p}__${l}__$AR.grib2
    ln -sf "../../grib/arome/$AR/$f" "$f"
  done
done
for p in SP1 SP2; do
  f=arome__0025__${p}__00H06H__$AR.grib2
  ln -sf "$GRIB_00H06H/$f" "$f"
done
cd ../../..

# 2. banc (3 répétitions, 1 et 4 threads) puis comparaison des résultats
export PYTHONPATH="$PYLIB_ECCODES:$PYLIB_PMTILES:."
mkdir -p out/rust/bench
python3 rust/tools/bench.py --reps 3 --out out/rust/bench/resultats.json | tee out/rust/bench/bench.log
python3 rust/tools/comparer_tout.py > out/rust/bench/ecarts.json
python3 rust/tools/comparer_tout.py --tuiles-dir tiles-pur > out/rust/bench/ecarts-pur.json
