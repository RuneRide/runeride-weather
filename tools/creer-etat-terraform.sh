#!/usr/bin/env bash
# SPDX-License-Identifier: EUPL-1.2
#
# Crée le compartiment S3 qui contient l'état Terraform du module météo (privé, chiffré, versionné,
# sans accès public). Simulation par défaut : aucune ressource n'est créée sans --apply.
#
# Usage :
#   AWS_PROFILE=bike360 tools/creer-etat-terraform.sh           # simulation
#   AWS_PROFILE=bike360 tools/creer-etat-terraform.sh --apply   # crée le compartiment
#
# Le nom contient le numéro de compte (unicité mondiale) : runeride-tfstate-<compte>. Aucun secret n'est lu ni affiché.
set -euo pipefail

REGION="eu-north-1"
APPLY=0
for a in "$@"; do
  case "$a" in
    --apply) APPLY=1 ;;
    *) echo "Option inconnue : $a" >&2; exit 2 ;;
  esac
done

compte=$(aws sts get-caller-identity --region "$REGION" --query Account --output text)
bucket="runeride-tfstate-${compte}"
echo "Compte : ${compte} ; région : ${REGION}"
echo "Compartiment d'état : ${bucket}"

if aws s3api head-bucket --bucket "$bucket" --region "$REGION" 2>/dev/null; then
  echo "Le compartiment existe déjà : rien à faire."
  echo "terraform init -backend-config=\"bucket=${bucket}\" -backend-config=\"key=meteo/production.tfstate\""
  exit 0
fi

echo "À créer : compartiment privé, accès public bloqué, versionnage activé, chiffrement SSE-S3."
if [[ $APPLY -ne 1 ]]; then echo "(simulation : ajouter --apply pour exécuter)"; exit 0; fi

aws s3api create-bucket --bucket "$bucket" --region "$REGION" \
  --create-bucket-configuration "LocationConstraint=${REGION}" >/dev/null
aws s3api put-public-access-block --bucket "$bucket" --region "$REGION" \
  --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
aws s3api put-bucket-versioning --bucket "$bucket" --region "$REGION" --versioning-configuration "Status=Enabled"
aws s3api put-bucket-encryption --bucket "$bucket" --region "$REGION" \
  --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
aws s3api put-bucket-tagging --bucket "$bucket" --region "$REGION" \
  --tagging 'TagSet=[{Key=projet,Value=runeride},{Key=usage,Value=etat-terraform}]'

echo "== Compartiment créé : ${bucket} =="
echo "Étape suivante, depuis le dossier du module (runeride-cloud-meteo/terraform/meteo) :"
echo "  terraform init -backend-config=\"bucket=${bucket}\" -backend-config=\"key=meteo/production.tfstate\""
