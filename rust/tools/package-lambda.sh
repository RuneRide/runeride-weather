#!/usr/bin/env bash
# SPDX-License-Identifier: EUPL-1.2
# Construit le binaire d'une fonction Lambda et l'emballe dans un zip (rien n'est envoyé nulle part).
#
#   rust/tools/package-lambda.sh                      # worker rw-lambda, arm64 (Graviton), cible aarch64-unknown-linux-musl
#   rust/tools/package-lambda.sh x86_64               # worker, x86_64 (musl statique, pour comparer ; non destiné à l'arm64)
#   rust/tools/package-lambda.sh arm64 poller         # poller (rw-poller)
#   rust/tools/package-lambda.sh arm64 manifeste      # manifeste (rw-manifeste)
#   rust/tools/package-lambda.sh arm64 tout           # les trois fonctions
#
# Sortie : rust/target/lambda/<crate>-<arch>.zip (rw-lambda-arm64.zip, rw-poller-arm64.zip, rw-manifeste-arm64.zip)
# contenant `bootstrap` (mode 0755, à la racine du zip, quel que soit le nom du binaire dans Cargo : `bootstrap` pour le
# worker, `rw-poller` et `rw-manifeste` pour les deux autres afin d'éviter une collision de noms dans `target/`), plus la
# somme SHA-256. Runtime Lambda à choisir au déploiement : `provided.al2023`, architecture arm64 ou x86_64.
#
# Prérequis : rustup target add aarch64-unknown-linux-musl (une fois). Aucun compilateur C croisé n'est
# nécessaire : la pile TLS est en Rust pur (rustls + RustCrypto) et l'éditeur de liens est `rust-lld`,
# fourni avec la chaîne Rust. Si `rust-lld` n'est pas trouvé, installer `lld` ou utiliser `cargo lambda build
# --release --arm64` (cargo-lambda, zig) : non requis ici, non installé.
set -euo pipefail
ARCH=${1:-arm64}
FONCTION=${2:-worker}
case "$ARCH" in
  arm64)  CIBLE=aarch64-unknown-linux-musl ;;
  x86_64) CIBLE=x86_64-unknown-linux-musl ;;
  *) echo "architecture inconnue : $ARCH (arm64 | x86_64)" >&2; exit 2 ;;
esac
case "$FONCTION" in
  worker|poller|manifeste|tout) ;;
  *) echo "fonction inconnue : $FONCTION (worker | poller | manifeste | tout)" >&2; exit 2 ;;
esac
RACINE=$(cd "$(dirname "$0")/.." && pwd)        # …/rust
cd "$RACINE"

empaqueter() { # crate binaire
  local CRATE=$1 BINAIRE=$2
  # `-p` : un `cargo build` à la racine de l'espace de travail active aussi libwebp (C) par unification des fonctionnalités
  RUSTFLAGS="${RUSTFLAGS:-} -C linker=rust-lld" cargo build --release --locked -p "$CRATE" --bin "$BINAIRE" --target "$CIBLE"
  local BIN="target/$CIBLE/release/$BINAIRE"
  local OUT="target/lambda"
  mkdir -p "$OUT"
  local TMP
  TMP=$(mktemp -d)
  install -m 0755 "$BIN" "$TMP/bootstrap"
  local ZIP="$PWD/$OUT/$CRATE-$ARCH.zip"
  rm -f "$ZIP"
  # -X : sans horodatages ni attributs supplémentaires (zip reproductible à contenu égal) ; le mode 0755 est conservé
  (cd "$TMP" && touch -d @0 bootstrap && zip -q -X -9 "$ZIP" bootstrap)
  rm -rf "$TMP"
  echo "binaire : $BIN ($(stat -c %s "$BIN") octets)"
  echo "zip     : $ZIP ($(stat -c %s "$ZIP") octets)"
  unzip -Z "$ZIP" | tail -n +3 | head -1
  (cd "$OUT" && sha256sum "$CRATE-$ARCH.zip")
}

case "$FONCTION" in
  worker|tout) empaqueter rw-lambda bootstrap ;;
esac
case "$FONCTION" in
  poller|tout) empaqueter rw-poller rw-poller ;;
esac
case "$FONCTION" in
  manifeste|tout) empaqueter rw-manifeste rw-manifeste ;;
esac
