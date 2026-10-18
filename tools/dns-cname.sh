#!/usr/bin/env bash
# SPDX-License-Identifier: EUPL-1.2
#
# Ajoute des CNAME dans la zone runeride.fr (cPanel, o2switch). Jamais d'écrasement : un nom déjà présent avec une
# autre valeur n'est pas modifié. Le jeton (AWS Secrets Manager : runeride/cpanel) n'est jamais affiché.
#
# Usage :
#   AWS_PROFILE=bike360 tools/dns-cname.sh NOM=CIBLE [NOM=CIBLE ...]            # simulation
#   AWS_PROFILE=bike360 tools/dns-cname.sh --apply NOM=CIBLE [NOM=CIBLE ...]    # écrit
# NOM est relatif à runeride.fr (ex. weather-data) ; CIBLE est le nom pointé (ex. d1234.cloudfront.net.).
set -euo pipefail

SECRET_ID="runeride/cpanel"; REGION="eu-north-1"; DOMAIN="runeride.fr"
APPLY=0
if [[ "${1:-}" == "--apply" ]]; then APPLY=1; shift; fi
[[ $# -ge 1 ]] || { echo "Usage : tools/dns-cname.sh [--apply] NOM=CIBLE ..." >&2; exit 2; }

secret=$(aws secretsmanager get-secret-value --region "$REGION" --secret-id "$SECRET_ID" --query SecretString --output text)
pick() { jq -r --arg keys "$1" '[ ($keys | split(",")[]) as $k | .[$k]? | select(. != null and . != "") ] | first // empty' <<<"$secret"; }
host=$(pick host,hostname,server,url,cpanel_host)
user=$(pick user,username,login,cpanel_user)
token=$(pick token,api_token,apitoken,key,secret)
host=${host#*://}; host=${host%%/*}; host=${host%%:*}
[[ -n "$host" && -n "$user" && -n "$token" ]] || { echo "Secret inattendu (hôte, utilisateur ou jeton manquant)." >&2; exit 1; }
unset secret

# ZoneEdit n'existe qu'en API v2 de cPanel. Le jeton passe par la configuration lue sur stdin.
api2() {
  local fn="$1"; shift
  {
    printf 'header = "Authorization: cpanel %s:%s"\n' "$user" "$token"
    printf 'url = "https://%s:2083/json-api/cpanel"\nget\n' "$host"
    for kv in "cpanel_jsonapi_apiversion=2" "cpanel_jsonapi_module=ZoneEdit" "cpanel_jsonapi_func=$fn" "$@"; do
      printf 'data-urlencode = "%s"\n' "$kv"
    done
  } | curl -fsS -K -
}
require_ok() {
  jq -e '.cpanelresult.data' >/dev/null 2>&1 <<<"$1" || { echo "Réponse cPanel inattendue." >&2; jq -r '.cpanelresult.error // "(pas de message)"' <<<"$1" 2>/dev/null >&2 || true; exit 1; }
}

zone=$(api2 fetchzone_records "domain=$DOMAIN" "customonly=1")
require_ok "$zone"
echo "== Zone $DOMAIN : $(jq '.cpanelresult.data | length' <<<"$zone") enregistrements personnalisés =="

for paire in "$@"; do
  name="${paire%%=*}"; value="${paire#*=}"
  [[ "$name" != "$paire" && -n "$name" && -n "$value" ]] || { echo "Argument invalide : $paire" >&2; exit 2; }
  cur=$(jq -r --arg n "$name.$DOMAIN." '.cpanelresult.data[]? | select(.name==$n and (.type=="CNAME" or .type=="A" or .type=="AAAA" or .type=="TXT")) | [.type, (.cname // .address // .txtdata // "")] | @tsv' <<<"$zone" | head -1)
  if [[ -n "$cur" ]]; then
    if [[ "$cur" == "CNAME"$'\t'"$value" || "$cur" == "CNAME"$'\t'"${value%.}" ]]; then echo "= CNAME $name : déjà présent, rien à faire"; continue; fi
    echo "! $name existe déjà ($cur) : non modifié" >&2; continue
  fi
  echo "+ CNAME $name -> $value"
  [[ $APPLY -eq 1 ]] || continue
  res=$(api2 add_zone_record "domain=$DOMAIN" "name=$name" "type=CNAME" "cname=$value" "ttl=300")
  require_ok "$res"
  [[ "$(jq -r '.cpanelresult.data[0].result.status' <<<"$res")" == "1" ]] || { echo "  échec de l'ajout : $(jq -r '.cpanelresult.data[0].result.statusmsg // ""' <<<"$res")" >&2; exit 1; }
  echo "  ajouté"
done
unset token
[[ $APPLY -eq 1 ]] || echo "(simulation : ajouter --apply pour écrire)"
