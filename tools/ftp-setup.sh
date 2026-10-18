#!/usr/bin/env bash
# SPDX-License-Identifier: EUPL-1.2
#
# Prépare l'hébergement du viewer sur o2switch (cPanel) :
#   1. le sous-domaine weather.runeride.fr avec sa propre racine web ~/weather (créé seulement s'il n'existe pas) ;
#   2. le compte FTP de déploiement deploy-weather@runeride.fr, enfermé dans ~/weather ;
#   3. les secrets GitHub Actions FTP_SERVER, FTP_USERNAME, FTP_PASSWORD du dépôt RuneRide/runeride-weather.
# Même méthode que RuneRide/website (tools/ftp-setup.sh) : le jeton cPanel (AWS Secrets Manager : runeride/cpanel)
# et le mot de passe FTP généré ne sont jamais affichés, jamais écrits sur disque et jamais passés en argument de
# commande (ils transitent par stdin). Racine dédiée : rien de ce qui sert déjà runeride.fr n'est touché.
#
# Usage (à lancer par le propriétaire ; l'assistant ne l'exécute pas) :
#   AWS_PROFILE=bike360 tools/ftp-setup.sh            # simulation : liste les racines web et les comptes FTP, annonce le plan
#   AWS_PROFILE=bike360 tools/ftp-setup.sh --apply    # crée sous-domaine et compte, teste la connexion, pose les secrets
#   AWS_PROFILE=bike360 tools/ftp-setup.sh --apply --rotate   # change le mot de passe d'un compte existant et remet les secrets
#
# Prérequis : aws (profil avec accès à Secrets Manager), gh (connecté, droit d'écrire les secrets), curl, jq, openssl.
# NON TESTÉ : écrit sans appel à cPanel. La première exécution se fait en simulation ; l'appel API2 SubDomain/addsubdomain
# est celui de la documentation cPanel et peut demander un ajustement.
set -euo pipefail

SECRET_ID="runeride/cpanel"; REGION="eu-north-1"; DOMAIN="runeride.fr"
SUB="weather"; FQDN="${SUB}.${DOMAIN}"
FTP_USER="deploy-weather"; HOMEDIR="weather"; REPO="RuneRide/runeride-weather"
LOGIN="${FTP_USER}@${DOMAIN}"

APPLY=0; ROTATE=0
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    --rotate) ROTATE=1 ;;
    *) echo "Option inconnue : $arg" >&2; exit 2 ;;
  esac
done

secret=$(aws secretsmanager get-secret-value --region "$REGION" --secret-id "$SECRET_ID" --query SecretString --output text)
pick() { jq -r --arg keys "$1" '[ ($keys | split(",")[]) as $k | .[$k]? | select(. != null and . != "") ] | first // empty' <<<"$secret"; }
host=$(pick host,hostname,server,url,cpanel_host)
user=$(pick user,username,login,cpanel_user)
token=$(pick token,api_token,apitoken,key,secret)
host=${host#*://}; host=${host%%/*}; host=${host%%:*}
if [[ -z "$host" || -z "$user" || -z "$token" ]]; then
  echo "Secret inattendu : il faut un hôte, un utilisateur et un jeton. Clés présentes :" >&2
  jq -r 'if type=="object" then keys | join(", ") else "(pas un objet JSON)" end' <<<"$secret" >&2
  exit 1
fi
unset secret

# Appel cPanel. Le jeton et les valeurs passent par la configuration lue sur stdin (pas dans argv).
curl_call() {
  local url="$1"; shift
  {
    printf 'header = "Authorization: cpanel %s:%s"\n' "$user" "$token"
    printf 'url = "%s"\n' "$url"
    printf 'get\n'
    for kv in "$@"; do printf 'data-urlencode = "%s"\n' "$kv"; done
  } | curl -fsS -K -
}
cp_call() { local fn="$1"; shift; curl_call "https://${host}:2083/execute/${fn}" "$@"; }
api2() { local mod="$1" fn="$2"; shift 2; curl_call "https://${host}:2083/json-api/cpanel" "cpanel_jsonapi_apiversion=2" "cpanel_jsonapi_module=${mod}" "cpanel_jsonapi_func=${fn}" "$@"; }
ok() { jq -e '.status == 1' >/dev/null 2>&1 <<<"$1"; }
fail() { echo "Échec cPanel :" >&2; jq -r '(.errors // ["réponse inattendue"]) | join(" ")' <<<"$1" 2>/dev/null >&2 || true; exit 1; }

echo "== Racines web des domaines (cPanel : $host) =="
doms=$(cp_call DomainInfo/domains_data "format=hash")
ok "$doms" || fail "$doms"
jq -r '.data | ([.main_domain] + (.sub_domains // []) + (.addon_domains // []))[]? | [.domain, .documentroot] | @tsv' <<<"$doms"
sub_existe=$(jq -r --arg d "$FQDN" '[.data.sub_domains[]? | select(.domain == $d)] | length' <<<"$doms")

echo "== Comptes FTP existants =="
ftps=$(cp_call Ftp/list_ftp)
ok "$ftps" || fail "$ftps"
jq -r '.data[]? | [(.user // .serverlogin // ""), (.homedir // "")] | @tsv' <<<"$ftps"
exists=$(jq -r --arg l "$LOGIN" '[.data[]? | select((.user // .serverlogin // "") == $l)] | length' <<<"$ftps")

echo "== Plan =="
FAIRE_COMPTE=1
if [[ "$sub_existe" -gt 0 ]]; then echo "= le sous-domaine $FQDN existe déjà (sa racine web n'est pas modifiée)"
else echo "+ création du sous-domaine $FQDN, racine ~/$HOMEDIR"; fi
if [[ "$exists" -gt 0 && $ROTATE -eq 0 ]]; then
  FAIRE_COMPTE=0
  echo "= le compte $LOGIN existe déjà : rien à créer (ajouter --rotate pour changer son mot de passe et remettre les secrets)"
elif [[ "$exists" -gt 0 ]]; then echo "+ rotation du mot de passe de $LOGIN"
else echo "+ création de $LOGIN, dossier ~/$HOMEDIR, quota illimité"; fi
[[ $FAIRE_COMPTE -eq 0 ]] || echo "+ secrets GitHub FTP_SERVER, FTP_USERNAME, FTP_PASSWORD sur $REPO"
if [[ $APPLY -ne 1 ]]; then echo "(simulation : ajouter --apply pour exécuter)"; exit 0; fi

if [[ "$sub_existe" -eq 0 ]]; then
  res=$(api2 SubDomain addsubdomain "domain=$SUB" "rootdomain=$DOMAIN" "dir=$HOMEDIR" "canoff=0" "disallowdot=1")
  jq -e '(.cpanelresult.data[0].result // 0) == 1' >/dev/null 2>&1 <<<"$res" || { echo "Échec de la création du sous-domaine :" >&2; jq -r '.cpanelresult.data[0].reason // .cpanelresult.error // "réponse inattendue"' <<<"$res" >&2; exit 1; }
  echo "sous-domaine créé : $FQDN -> ~/$HOMEDIR"
fi

[[ $FAIRE_COMPTE -eq 1 ]] || { echo "compte FTP déjà présent : rien d'autre à faire"; exit 0; }

pass=$(openssl rand -base64 48 | tr -dc 'A-Za-z0-9' | cut -c1-32)
if [[ "$exists" -gt 0 ]]; then
  res=$(cp_call Ftp/passwd "user=$FTP_USER" "pass=$pass" "domain=$DOMAIN")
else
  res=$(cp_call Ftp/add_ftp "user=$FTP_USER" "pass=$pass" "homedir=$HOMEDIR" "quota=0" "domain=$DOMAIN")
fi
ok "$res" || fail "$res"
echo "compte FTP prêt : $LOGIN"

# Test de connexion FTPS explicite (identifiants via stdin)
if printf 'user = "%s:%s"\nssl-reqd\nlist-only\nurl = "ftp://%s/"\n' "$LOGIN" "$pass" "$host" | curl -fsS -K - >/dev/null 2>&1; then
  echo "connexion FTPS réussie sur $host"
else
  echo "! connexion FTPS impossible sur $host (certificat, pare-feu ou délai de création) : secrets posés quand même, à vérifier" >&2
fi

printf '%s' "$host"  | gh secret set FTP_SERVER   --repo "$REPO" >/dev/null
printf '%s' "$LOGIN" | gh secret set FTP_USERNAME --repo "$REPO" >/dev/null
printf '%s' "$pass"  | gh secret set FTP_PASSWORD --repo "$REPO" >/dev/null
unset pass token
echo "secrets GitHub posés sur $REPO"
echo "Lancer le déploiement : gh workflow run deploy.yml --repo $REPO"
echo "Certificat HTTPS de $FQDN : AutoSSL de cPanel le délivre seul après la création du sous-domaine (quelques minutes)."
