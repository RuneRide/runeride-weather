#!/usr/bin/env bash
# SPDX-License-Identifier: EUPL-1.2
# Construit le binaire `bootstrap` de la fonction Lambda et l'emballe dans un zip (rien n'est envoyé nulle part).
#
#   rust/tools/package-lambda.sh            # arm64 (Graviton), cible aarch64-unknown-linux-musl
#   rust/tools/package-lambda.sh x86_64     # x86_64 (musl statique, pour comparer ; non destiné à l'arm64)
#
# Sortie : rust/target/lambda/rw-lambda-<arch>.zip contenant `bootstrap` (mode 0755, à la racine du zip), plus
# la somme SHA-256. Runtime Lambda à choisir au déploiement : `provided.al2023`, architecture arm64 ou x86_64.
#
# Prérequis : rustup target add aarch64-unknown-linux-musl (une fois). Aucun compilateur C croisé n'est
# nécessaire : la pile TLS est en Rust pur (rustls + RustCrypto) et l'éditeur de liens est `rust-lld`,
# fourni avec la chaîne Rust. Si `rust-lld` n'est pas trouvé, installer `lld` ou utiliser `cargo lambda build
# --release --arm64` (cargo-lambda, zig) : non requis ici, non installé.
set -euo pipefail
ARCH=${1:-arm64}
case "$ARCH" in
  arm64)  CIBLE=aarch64-unknown-linux-musl ;;
  x86_64) CIBLE=x86_64-unknown-linux-musl ;;
  *) echo "architecture inconnue : $ARCH (arm64 | x86_64)" >&2; exit 2 ;;
esac
RACINE=$(cd "$(dirname "$0")/.." && pwd)        # …/rust
cd "$RACINE"
RUSTFLAGS="${RUSTFLAGS:-} -C linker=rust-lld" cargo build --release --locked -p rw-lambda --bin bootstrap --target "$CIBLE"
BIN="target/$CIBLE/release/bootstrap"
OUT="target/lambda"
mkdir -p "$OUT"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
install -m 0755 "$BIN" "$TMP/bootstrap"
ZIP="$PWD/$OUT/rw-lambda-$ARCH.zip"
rm -f "$ZIP"
# -X : sans horodatages ni attributs supplémentaires (zip reproductible à contenu égal) ; le mode 0755 est conservé
(cd "$TMP" && touch -d @0 bootstrap && zip -q -X -9 "$ZIP" bootstrap)
echo "binaire : $BIN ($(stat -c %s "$BIN") octets)"
echo "zip     : $ZIP ($(stat -c %s "$ZIP") octets)"
unzip -Z "$ZIP" | tail -n +3 | head -1
(cd "$OUT" && sha256sum "rw-lambda-$ARCH.zip")
