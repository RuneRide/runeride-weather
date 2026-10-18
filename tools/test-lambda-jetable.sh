#!/usr/bin/env bash
# SPDX-License-Identifier: EUPL-1.2
#
# Test jetable de la Lambda `rw-lambda` (worker) sur un compte AWS réel : crée, mesure, vérifie, puis SUPPRIME tout.
# Détails et lecture des résultats : docs/test-lambda-jetable.md.
#
# Usage :
#   AWS_PROFILE=bike360 tools/test-lambda-jetable.sh                     # simulation : affiche ce qui serait créé (ne crée rien)
#   AWS_PROFILE=bike360 tools/test-lambda-jetable.sh --apply             # crée, mesure, vérifie, nettoie (environ 5 à 10 min)
#   AWS_PROFILE=bike360 tools/test-lambda-jetable.sh --apply --garder    # idem, mais ne nettoie pas (à nettoyer ensuite)
#   AWS_PROFILE=bike360 tools/test-lambda-jetable.sh --nettoyer rw-test-20261018-143000          # simulation du nettoyage
#   AWS_PROFILE=bike360 tools/test-lambda-jetable.sh --nettoyer rw-test-20261018-143000 --apply  # nettoyage a posteriori
#   options : --zip <fichier> (défaut rust/target/lambda/rw-lambda-arm64.zip, produit par rust/tools/package-lambda.sh)
#             --tirage <AAAA-MM-JJTHH:00:00Z> et --echeance <N> (défaut : tirage récent trouvé sur le S3 public d'OVH, échéance 12)
#             --hors-ligne (simulation sans aucun appel AWS ; la recherche du tirage sur le S3 public d'OVH reste faite)
#
# Ressources créées (région eu-north-1, préfixe unique rw-test-<AAAAMMJJ-HHMMSS>, tags projet=runeride-weather,temporaire=oui) :
# un bucket S3 (privé, chiffré SSE-S3, accès public bloqué, expiration des objets à 1 jour), un groupe de journaux (rétention 1 jour),
# une politique IAM et un rôle d'exécution de moindre privilège (docs/poc-rust/lambda.md §10.1, restreint au bucket de test),
# une fonction Lambda arm64 provided.al2023 (1 769 Mo, délai 120 s, /tmp 2 048 Mo). Pas de file SQS : l'événement SQS est simulé
# (aws lambda invoke). Le tout est supprimé à la fin, y compris sur erreur ou interruption (trap), sauf avec --garder.
#
# Coût attendu : quelques centimes au plus. Seul ce qui est sûr est chiffré : la Lambda est facturée en durée x mémoire
# (1,769 Go ; arm64 : 0,0000133334 $ par Go-seconde, docs/architecture-aws.md §4.1). Le script fait environ 15 invocations ;
# même au délai maximal de 120 s chacune, cela fait au plus ≈ 3 200 Go-s ≈ 0,04 $ ; en pratique quelques dizaines de secondes
# en tout (≈ 0,001 à 0,01 $), peut-être couvert par le niveau gratuit. S3, CloudWatch Logs, IAM : quelques requêtes et quelques Ko.
# Les téléchargements depuis OVH sont de l'entrée de données depuis Internet (gratuite). Ni NAT, ni VPC, ni SQS.
#
# Sécurité : aucune valeur secrète dans ce script ; aucun identifiant n'est lu, affiché ni écrit (seul l'identifiant de compte,
# non secret, est affiché et utilisé dans les ARN). Les identifiants du rôle d'exécution sont fournis par Lambda elle-même.
set -euo pipefail

REGION="eu-north-1"
PROFIL_PAR_DEFAUT="bike360"
HOTE_OVH="https://meteofrance-pnt.s3.rbx.io.cloud.ovh.net"
BASE_OVH="$HOTE_OVH/pnt"
MEMOIRE=1769; DELAI=120; TMP_MO=2048
TAGS_KV="projet=runeride-weather,temporaire=oui"
RACINE=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SORTIE_DIR="$RACINE/out/test-lambda"

APPLY=0; GARDER=0; HORS_LIGNE=0; NETTOYER=""; ZIP=""; TIRAGE_FORCE=""; ECHEANCE=12
PID_PRINCIPAL=$$; PREFIXE=""; COMPTE=""; CREE=0; ZIP_LOCAL=""; TMP=""; ECHECS=0; NETTOYAGE_FAIT=0
TIRAGE=""; TIRAGE_FUTUR=""

usage() { sed -n '4,15p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; }
info() { printf '%s\n' "$*"; }
titre() { printf '\n== %s ==\n' "$*"; }
avert() { printf 'Attention : %s\n' "$*" >&2; }
die() { printf 'Erreur : %s\n' "$*" >&2; exit 1; }
A() { aws --region "$REGION" "$@"; }

# ---------------------------------------------------------------------------------------------------------------------
# Noms dérivés du préfixe
# ---------------------------------------------------------------------------------------------------------------------
noms() { # préfixe -> variables globales
  PREFIXE=$1
  FN=$PREFIXE; BUCKET=$PREFIXE; GROUPE="/aws/lambda/$PREFIXE"; ROLE="$PREFIXE-role"; POLITIQUE="$PREFIXE-policy"
}
arn_politique() { printf 'arn:aws:iam::%s:policy/%s' "$COMPTE" "$POLITIQUE"; }
prefixe_valide() { [[ "$1" =~ ^rw-test-[0-9]{8}-[0-9]{6}$ ]]; }

# ---------------------------------------------------------------------------------------------------------------------
# Compte, région, inventaire
# ---------------------------------------------------------------------------------------------------------------------
verifier_compte() {
  export AWS_PAGER=""
  local r
  for r in "${AWS_REGION:-}" "${AWS_DEFAULT_REGION:-}"; do
    [[ -z "$r" || "$r" == "$REGION" ]] || die "la région de l'environnement ($r) n'est pas $REGION : refus."
  done
  export AWS_DEFAULT_REGION="$REGION"
  export AWS_PROFILE="${AWS_PROFILE:-$PROFIL_PAR_DEFAUT}"
  info "Profil AWS : $AWS_PROFILE ; région : $REGION"
  if [[ $HORS_LIGNE -eq 1 ]]; then COMPTE="<COMPTE>"; info "Compte : (non vérifié, --hors-ligne)"; return 0; fi
  command -v aws >/dev/null || die "commande aws introuvable."
  local id
  if ! id=$(A sts get-caller-identity --query Account --output text 2>&1); then
    if [[ $APPLY -eq 1 || -n "$NETTOYER" ]]; then
      printf '%s\n' "$id" | head -3 >&2
      die "impossible d'obtenir l'identité du profil $AWS_PROFILE (session expirée ? essayer : aws login --profile $AWS_PROFILE)."
    fi
    avert "compte non vérifié en simulation ($(printf '%s' "$id" | head -1))"; COMPTE="<COMPTE>"; return 0
  fi
  [[ "$id" =~ ^[0-9]{12}$ ]] || die "identifiant de compte inattendu."
  COMPTE=$id
  info "Compte AWS : $COMPTE"
}

# Liste « type:nom » des ressources dont le nom commence par $1 (rien ne doit rester après nettoyage).
inventaire() {
  local p=$1 s
  s=$(A lambda list-functions --query "Functions[?starts_with(FunctionName, '$p')].FunctionName" --output text) || return 1
  tr '\t' '\n' <<<"$s" | sed '/^$/d; s/^/lambda:/'
  s=$(A logs describe-log-groups --log-group-name-prefix "/aws/lambda/$p" --query 'logGroups[].logGroupName' --output text) || return 1
  tr '\t' '\n' <<<"$s" | sed '/^$/d; s/^/journaux:/'
  s=$(A iam list-roles --query "Roles[?starts_with(RoleName, '$p')].RoleName" --output text) || return 1
  tr '\t' '\n' <<<"$s" | sed '/^$/d; s/^/role:/'
  s=$(A iam list-policies --scope Local --query "Policies[?starts_with(PolicyName, '$p')].PolicyName" --output text) || return 1
  tr '\t' '\n' <<<"$s" | sed '/^$/d; s/^/politique:/'
  s=$(A s3api list-buckets --query "Buckets[?starts_with(Name, '$p')].Name" --output text) || return 1
  tr '\t' '\n' <<<"$s" | sed '/^$/d; s/^/bucket:/'
}

# ---------------------------------------------------------------------------------------------------------------------
# Source Météo-France (S3 public OVH, HTTP sans clé) : tirage récent complet pour l'échéance voulue
# ---------------------------------------------------------------------------------------------------------------------
groupe_025() { # échéance -> 00H06H, 07H12H, …, 49H51H
  local n=$1 d f
  if (( n <= 6 )); then printf '00H06H'; return; fi
  d=$(( 7 + 6 * ((n - 7) / 6) )); f=$(( d + 5 )); (( f > 51 )) && f=51
  printf '%02dH%02dH' "$d" "$f"
}
etiquette() { if [[ "$1" == arome-hd ]]; then printf '%02dH' "$2"; else groupe_025 "$2"; fi; } # modèle échéance
fichiers_requis() { # modèle tirage_long échéance -> URL de chaque GRIB nécessaire (SP1, SP2 ; échéance n-1 si utile)
  local m=$1 t=$2 n=$3 res p e l
  [[ "$m" == arome-hd ]] && res=001 || res=0025
  local etiq=() ; l=$(etiquette "$m" "$n"); etiq+=("$l")
  if (( n >= 2 )) || { [[ "$m" == arome ]] && (( n >= 1 )); }; then
    e=$(etiquette "$m" $(( n - 1 ))); [[ "$e" == "$l" ]] || etiq+=("$e")
  fi
  for l in "${etiq[@]}"; do
    for p in SP1 SP2; do printf '%s/%s/arome/%s/%s/arome__%s__%s__%s__%s.grib2\n' "$BASE_OVH" "$t" "$res" "$p" "$res" "$p" "$l" "$t"; done
  done
}
taille_http() { curl -fsSI --max-time 30 "$1" 2>/dev/null | tr -d '\r' | sed -n 's/^[Cc]ontent-[Ll]ength: *//p' | tail -1; }
tirage_complet() { # tirage_long -> 0 si tous les GRIB des deux modèles pour l'échéance existent ; total d'octets dans TOTAL_OCTETS
  local t=$1 m u s
  TOTAL_OCTETS=0
  for m in arome-hd arome; do
    while IFS= read -r u; do
      s=$(taille_http "$u") || return 1
      [[ "$s" =~ ^[0-9]+$ ]] || return 1
      TOTAL_OCTETS=$(( TOTAL_OCTETS + s ))
    done < <(fichiers_requis "$m" "$t" "$ECHEANCE")
  done
}
choisir_tirage() {
  if [[ -n "$TIRAGE_FORCE" ]]; then
    [[ "$TIRAGE_FORCE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:00:00Z$ ]] || die "--tirage attendu au format 2026-10-17T03:00:00Z"
    tirage_complet "$TIRAGE_FORCE" || die "GRIB manquants à la source pour $TIRAGE_FORCE, échéance $ECHEANCE."
    TIRAGE=$TIRAGE_FORCE
  else
    local liste t
    liste=$(curl -fsS --max-time 60 "$HOTE_OVH/?list-type=2&prefix=pnt/&delimiter=/&max-keys=1000") || { avert "listage du S3 OVH impossible"; return 1; }
    grep -q '<IsTruncated>true</IsTruncated>' <<<"$liste" && { avert "listage OVH tronqué : utiliser --tirage"; return 1; }
    TIRAGE=""
    for t in $(grep -o '<Prefix>pnt/[0-9T:Z-]*/</Prefix>' <<<"$liste" | sed 's#<Prefix>pnt/##; s#/</Prefix>##' | sort -r | head -16); do
      if tirage_complet "$t"; then TIRAGE=$t; break; fi
    done
    [[ -n "$TIRAGE" ]] || { avert "aucun tirage récent complet trouvé à la source."; return 1; }
  fi
  # tirage dans le futur (échéance absente à la source) : demain + 1 jour, arrondi au multiple de 3 h
  local h; h=$(date -u -d '+36 hours' +%H)
  TIRAGE_FUTUR=$(printf '%s%02d:00:00Z' "$(date -u -d '+36 hours' +%Y-%m-%dT)" $(( 10#$h / 3 * 3 )))
  TIRAGE_COURT=${TIRAGE:0:13}Z
  FUTUR_COURT=${TIRAGE_FUTUR:0:13}Z
  info "Tirage de test : $TIRAGE, échéance $ECHEANCE (HD et AROME ; $(( TOTAL_OCTETS / 1000000 )) Mo de GRIB au total pour une passe de chaque modèle)"
}

# ---------------------------------------------------------------------------------------------------------------------
# Documents JSON (jq ; rien de secret)
# ---------------------------------------------------------------------------------------------------------------------
politique_confiance() {
  jq -cn '{Version:"2012-10-17",Statement:[{Effect:"Allow",Principal:{Service:"lambda.amazonaws.com"},Action:"sts:AssumeRole"}]}'
}
politique_permissions() {
  # lambda.md §10.1 sans SQS (pas de file ici), restreinte au bucket de test. Écart assumé avec §10.1 : la clé GeoJSON des isobares
  # est v1/isobares/<modèle>/… (traitement.rs, Cles::isobares_geojson), hors de v1/arome/* : ajoutée ici (sinon 403 pour l'AROME).
  jq -cn --arg b "$BUCKET" --arg g "arn:aws:logs:${REGION}:${COMPTE}:log-group:${GROUPE}:*" '
    {Version:"2012-10-17",Statement:[
      {Sid:"EcrireEtRelireLesSorties",Effect:"Allow",Action:["s3:PutObject","s3:GetObject"],
       Resource:["arn:aws:s3:::\($b)/v1/arome/*","arn:aws:s3:::\($b)/v1/arome-hd/*","arn:aws:s3:::\($b)/v1/isobares/*"]},
      {Sid:"DistinguerAbsentEtInterdit",Effect:"Allow",Action:"s3:ListBucket",Resource:"arn:aws:s3:::\($b)"},
      {Sid:"Journaux",Effect:"Allow",Action:["logs:CreateLogStream","logs:PutLogEvents"],Resource:$g}]}'
}
env_lambda() { # forcer(0|1) marqueur_demarrage_a_froid -> {"Variables":{…}}
  jq -cn --arg b "$BUCKET" --arg src "$BASE_OVH" --arg f "$1" --arg c "$2" '
    {Variables: ({RW_BUCKET:$b, RW_PREFIXE:"v1", RW_SOURCE:$src, RW_COLD:$c} + (if $f == "1" then {RW_FORCER:"1"} else {} end))}'
}
corps_message() { jq -cn --arg m "$1" --arg t "$2" --argjson e "$3" '{modele:$m,tirage:$t,echeance:$e}'; } # modèle tirage échéance
evenement_sqs() { # identifiant corps
  jq -cn --arg id "$1" --arg body "$2" --arg arn "arn:aws:sqs:${REGION}:${COMPTE}:${PREFIXE}-fictive" --arg reg "$REGION" '
    {Records:[{messageId:$id,receiptHandle:"test",body:$body,
      attributes:{ApproximateReceiveCount:"1",SentTimestamp:"0",SenderId:"test",ApproximateFirstReceiveTimestamp:"0"},
      messageAttributes:{},md5OfBody:"",eventSource:"aws:sqs",eventSourceARN:$arn,awsRegion:$reg}]}'
}

# ---------------------------------------------------------------------------------------------------------------------
# Nettoyage
# ---------------------------------------------------------------------------------------------------------------------
# etape <libellé> <commande…> : « fait », « déjà absent » ou « ÉCHEC »
etape() {
  local lib=$1 err; shift
  if err=$("$@" 2>&1 >/dev/null); then info "  - $lib : supprimé"
  elif grep -qiE 'NotFound|NoSuchEntity|NoSuchBucket|does not exist|Not Found|404' <<<"$err"; then info "  - $lib : déjà absent"
  else info "  - $lib : ÉCHEC ($(head -c 200 <<<"$err" | tr '\n' ' '))"; return 1; fi
}
supprimer_ressources() {
  local pa bad=0
  pa=$(arn_politique)
  etape "fonction Lambda $FN" A lambda delete-function --function-name "$FN" || bad=1
  etape "groupe de journaux $GROUPE" A logs delete-log-group --log-group-name "$GROUPE" || bad=1
  etape "politique détachée du rôle" A iam detach-role-policy --role-name "$ROLE" --policy-arn "$pa" || bad=1
  etape "rôle IAM $ROLE" A iam delete-role --role-name "$ROLE" || bad=1
  etape "politique IAM $POLITIQUE" A iam delete-policy --policy-arn "$pa" || bad=1
  etape "objets du bucket $BUCKET" A s3 rm "s3://$BUCKET" --recursive --quiet || bad=1
  etape "bucket $BUCKET" A s3api delete-bucket --bucket "$BUCKET" || bad=1
  return $bad
}
# Supprime, puis vérifie qu'il ne reste rien du préfixe (retourne 0 si propre)
nettoyer_prefixe() {
  info "Nettoyage du préfixe $PREFIXE :"
  local restes
  supprimer_ressources || true
  # le groupe de journaux peut être recréé par une écriture tardive de la fonction : second passage
  A logs delete-log-group --log-group-name "$GROUPE" >/dev/null 2>&1 || true
  if ! restes=$(inventaire "$PREFIXE"); then info "Vérification impossible (erreur AWS) : contrôler à la main avec --nettoyer $PREFIXE"; return 1; fi
  if [[ -n "$restes" ]]; then
    info "RESTES (à supprimer) :"; sed 's/^/  /' <<<"$restes"
    info "Relancer : AWS_PROFILE=${AWS_PROFILE:-$PROFIL_PAR_DEFAUT} tools/test-lambda-jetable.sh --nettoyer $PREFIXE --apply"
    return 1
  fi
  info "Vérification : plus aucune ressource ne porte le préfixe $PREFIXE."
}
sortie_finale() {
  local code=$?
  # un sous-shell (substitution de commande) qui reçoit le signal hérite du trap : il ne doit rien nettoyer (ni afficher)
  [[ "$BASHPID" == "$PID_PRINCIPAL" ]] || exit "$code"
  exec >&3 2>&4               # le signal peut arriver pendant une commande dont la sortie est redirigée vers un fichier
  trap '' INT TERM            # ne pas laisser un second Ctrl-C interrompre le nettoyage
  set +e +u
  if [[ $CREE -eq 1 && $NETTOYAGE_FAIT -eq 0 ]]; then
    if [[ $GARDER -eq 1 ]]; then
      titre "Ressources conservées (--garder)"
      info "Elles coûtent peu mais ne disparaissent pas toutes seules (seuls les objets S3 expirent après 1 jour)."
      info "Nettoyer : AWS_PROFILE=$AWS_PROFILE tools/test-lambda-jetable.sh --nettoyer $PREFIXE --apply"
    else
      titre "Nettoyage"
      NETTOYAGE_FAIT=1
      if nettoyer_prefixe; then nett="ok"; else nett="restes"; [[ $code -eq 0 ]] && code=1; fi
      [[ -n "${FICHIER_JSON:-}" && -f "$FICHIER_JSON" ]] && jq --arg n "$nett" '.nettoyage = $n' "$FICHIER_JSON" > "$FICHIER_JSON.tmp" && mv "$FICHIER_JSON.tmp" "$FICHIER_JSON"
    fi
  fi
  [[ -n "$TMP" && -d "$TMP" ]] && rm -rf "$TMP"
  exit $code
}

# ---------------------------------------------------------------------------------------------------------------------
# Création
# ---------------------------------------------------------------------------------------------------------------------
creer() {
  titre "Création des ressources ($PREFIXE)"
  CREE=1
  local lc="$TMP/lifecycle.json"
  info "  - bucket S3 $BUCKET"
  A s3api create-bucket --bucket "$BUCKET" --create-bucket-configuration "LocationConstraint=$REGION" >/dev/null
  A s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
  A s3api put-bucket-encryption --bucket "$BUCKET" --server-side-encryption-configuration \
    '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
  A s3api put-bucket-tagging --bucket "$BUCKET" --tagging 'TagSet=[{Key=projet,Value=runeride-weather},{Key=temporaire,Value=oui}]'
  jq -cn '{Rules:[{ID:"expiration-1-jour",Status:"Enabled",Filter:{Prefix:""},Expiration:{Days:1},AbortIncompleteMultipartUpload:{DaysAfterInitiation:1}}]}' > "$lc"
  A s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" --lifecycle-configuration "file://$lc"

  info "  - groupe de journaux $GROUPE (rétention 1 jour)"
  A logs create-log-group --log-group-name "$GROUPE" --tags "$TAGS_KV"
  A logs put-retention-policy --log-group-name "$GROUPE" --retention-in-days 1

  info "  - politique IAM $POLITIQUE et rôle $ROLE"
  politique_permissions > "$TMP/permissions.json"; politique_confiance > "$TMP/confiance.json"
  A iam create-policy --policy-name "$POLITIQUE" --policy-document "file://$TMP/permissions.json" \
    --tags Key=projet,Value=runeride-weather Key=temporaire,Value=oui >/dev/null
  A iam create-role --role-name "$ROLE" --assume-role-policy-document "file://$TMP/confiance.json" \
    --tags Key=projet,Value=runeride-weather Key=temporaire,Value=oui >/dev/null
  A iam attach-role-policy --role-name "$ROLE" --policy-arn "$(arn_politique)"

  info "  - fonction Lambda $FN (arm64, provided.al2023, ${MEMOIRE} Mo, ${DELAI} s, /tmp ${TMP_MO} Mo)"
  local arn_role="arn:aws:iam::${COMPTE}:role/${ROLE}" essai err
  for essai in 1 2 3 4 5 6 7 8; do
    if err=$(A lambda create-function --function-name "$FN" --runtime provided.al2023 --architectures arm64 --handler bootstrap \
        --role "$arn_role" --zip-file "fileb://$ZIP_LOCAL" --memory-size "$MEMOIRE" --timeout "$DELAI" \
        --ephemeral-storage "Size=$TMP_MO" --environment "$(env_lambda 1 initial)" --tags "$TAGS_KV" --output json 2>&1 >/dev/null); then break; fi
    if grep -q 'cannot be assumed' <<<"$err" && (( essai < 8 )); then info "    (propagation IAM, nouvel essai $essai/8 dans 8 s)"; sleep 8; continue; fi
    printf '%s\n' "$err" >&2; die "création de la fonction impossible."
  done
  A lambda wait function-active-v2 --function-name "$FN"
  info "  Fonction prête."
}

# ---------------------------------------------------------------------------------------------------------------------
# Invocation et analyse du journal
# ---------------------------------------------------------------------------------------------------------------------
# Analyse un journal (--log-type Tail décodé) : ligne REPORT + dernière ligne applicative JSON -> objet JSON
analyser_journal() {
  local log=$1 report app
  report=$(grep -a '^REPORT' <<<"$log" | tail -1 || true)
  app=$(jq -Rc 'fromjson? | select(type == "object" and .evenement != null)' <<<"$log" 2>/dev/null | tail -1 || true)
  jq -n --arg r "$report" --arg a "${app:-null}" '
    def champs: ($r | split("\t") | map(select(test(": ")) | capture("^(?<k>[^:]+): (?<v>.*)$") | {(.k): .v}) | add) // {};
    def nb(x): x | if . == null then null else (sub(" (ms|MB)$"; "") | tonumber) end;
    (champs) as $c | ($a | fromjson) as $j |
    { duree_ms: nb($c["Duration"]), facture_ms: nb($c["Billed Duration"]), memoire_mo: nb($c["Memory Size"]),
      memoire_max_mo: nb($c["Max Memory Used"]), init_ms: nb($c["Init Duration"]),
      report_present: ($r != ""),
      app: ($j // null) }'
}

# invoquer <phase> <modèle> <tirage> <échéance> <identifiant> -> ajoute à $TMP/mesures.jsonl ; réponse dans $REPONSE
invoquer() {
  local phase=$1 modele=$2 tirage=$3 ech=$4 id=$5 n meta log t0 t1 corps ana invo
  NB_INVOC=$(( ${NB_INVOC:-0} + 1 )); n=$NB_INVOC
  meta="$TMP/inv-$n.meta.json"; REPONSE="$TMP/inv-$n.reponse.json"
  corps=$(corps_message "$modele" "$tirage" "$ech")
  evenement_sqs "$id" "$corps" > "$TMP/inv-$n.evenement.json"
  t0=$(date +%s%N)
  A lambda invoke --function-name "$FN" --payload "fileb://$TMP/inv-$n.evenement.json" --cli-binary-format raw-in-base64-out \
    --cli-read-timeout 0 --log-type Tail --output json "$REPONSE" > "$meta"
  t1=$(date +%s%N)
  log=$(jq -r '.LogResult // empty' "$meta" | base64 -d 2>/dev/null || true)
  printf '%s\n' "$log" > "$TMP/inv-$n.journal.txt"
  ana=$(analyser_journal "$log")
  invo=$(jq -nc --arg phase "$phase" --arg modele "$modele" --arg tirage "$tirage" --argjson ech "$ech" --arg id "$id" \
      --argjson wall "$(( (t1 - t0) / 1000000 ))" --slurpfile rep "$REPONSE" --slurpfile meta "$meta" --argjson ana "$ana" '
    { phase: $phase, modele: $modele, tirage: $tirage, echeance: $ech, message: $id, mur_ms: $wall,
      http: $meta[0].StatusCode, erreur_fonction: ($meta[0].FunctionError // null),
      echecs_lot: ($rep[0].batchItemFailures? // null), reponse: ($rep[0]) } + $ana' ) || invo=""
  if [[ -z "$invo" ]]; then avert "réponse de l'invocation $n illisible"; return 0; fi
  printf '%s\n' "$invo" >> "$TMP/mesures.jsonl"
  jq -r '"  [\(.phase)] \(.modele) \(.echeance) : \(.duree_ms // "?") ms (facturé \(.facture_ms // "?") ms), mémoire max \(.memoire_max_mo // "?") Mo, init \(if .init_ms then "\(.init_ms) ms" else "-" end), statut \(.app.statut // .app.classe // "?")\(if .erreur_fonction then " ERREUR_FONCTION=" + .erreur_fonction else "" end)"' <<<"$invo"
}

changer_env() { # forcer marqueur
  A lambda update-function-configuration --function-name "$FN" --environment "$(env_lambda "$1" "$2")" --output json >/dev/null
  A lambda wait function-updated-v2 --function-name "$FN"
}

# ---------------------------------------------------------------------------------------------------------------------
# Vérifications
# ---------------------------------------------------------------------------------------------------------------------
verif() { # ok|ko <nom> <détail>
  local etat=$1 nom=$2 detail=${3:-}
  jq -cn --arg e "$etat" --arg n "$nom" --arg d "$detail" '{controle:$n,ok:($e == "ok"),detail:$d}' >> "$TMP/verifs.jsonl"
  if [[ "$etat" == ok ]]; then info "  [ok] $nom${detail:+ : $detail}"; else info "  [ÉCHEC] $nom${detail:+ : $detail}"; ECHECS=$(( ECHECS + 1 )); fi
}
liste_objets() { A s3api list-objects-v2 --bucket "$BUCKET" --output json | jq -c '[.Contents // [] | .[] | {Key, Size, ETag, LastModified}] | sort_by(.Key)'; }

# Lecture d'un PMTiles : en-tête 'PMTiles' + version 3 ; bibliothèque pmtiles si disponible
verifier_entete_pmtiles() { # fichier -> 0 si valide
  local f=$1 magie version
  magie=$(head -c 7 "$f" 2>/dev/null | tr -d '\000' || true)
  version=$(od -An -tu1 -j7 -N1 "$f" 2>/dev/null | tr -d ' ' || true)
  [[ "$magie" == "PMTiles" && "$version" == "3" ]]
}
lire_pmtiles_lib() { # fichier -> JSON {lib:false} ou {lib:true,min_zoom,max_zoom,tuiles}
  local f=$1 whl py="$TMP/lire_pmtiles.py"
  whl=$(ls "$RACINE"/pmtiles-*.whl 2>/dev/null | head -1 || true)
  cat > "$py" <<'PY'
import json, sys
whl, f = sys.argv[1], sys.argv[2]
try:
    from pmtiles.reader import Reader, MmapSource
except ImportError:
    if not whl:
        print(json.dumps({"lib": False})); sys.exit(0)
    sys.path.insert(0, whl)
    from pmtiles.reader import Reader, MmapSource
with open(f, "rb") as fh:
    h = Reader(MmapSource(fh)).header()
print(json.dumps({"lib": True, "min_zoom": h["min_zoom"], "max_zoom": h["max_zoom"], "tuiles": h.get("addressed_tiles_count")}))
PY
  python3 -I "$py" "$whl" "$f" 2>/dev/null || echo '{"lib":false}'
}

# verifier_sorties <modèle> <tirage_court> <échéance> : liste, état, un PMTiles
verifier_sorties() {
  local modele=$1 court=$2 ech=$3 nn dossier liste etat cle
  nn=$(printf '%02d' "$ech"); dossier="v1/$modele/$court"
  titre "Vérification des sorties : $modele $court échéance $nn"
  liste=$(liste_objets); echo "$liste" > "$TMP/liste-$modele.json"
  jq -r --arg d "$dossier/" --arg i "v1/isobares/$modele/$court/" '.[] | select(.Key | startswith($d) or startswith($i)) | "  \(.Size)\t\(.Key)"' <<<"$liste" | column -t -s $'\t' || true
  etat="$TMP/etat-$modele.json"
  if ! A s3api get-object --bucket "$BUCKET" --key "$dossier/_etat/$nn.json" "$etat" >/dev/null 2>&1; then verif ko "état $modele" "fichier $dossier/_etat/$nn.json absent"; return 0; fi
  verif ok "état $modele" "$(wc -c < "$etat") octets"
  jq -e --arg m "$modele" --arg t "$court" --argjson e "$ech" '.schema == 1 and .statut == "complete" and .modele == $m and .tirage == $t and .echeance == $e' "$etat" >/dev/null \
    && verif ok "contenu de l'état $modele" "schema 1, statut complete, modèle/tirage/échéance conformes" \
    || verif ko "contenu de l'état $modele" "$(jq -c '{schema,statut,modele,tirage,echeance}' "$etat")"
  # chaque fichier cité par l'état existe avec la taille inscrite
  local manque
  manque=$(jq -r --slurpfile l "$TMP/liste-$modele.json" '
      [ (.variables // {} | to_entries[] | .value | {cle, octets}), (.isobares // empty | select(. != null) | {cle, octets}),
        (.isobares_geojson // empty | select(. != null) | {cle, octets}), (.domaine // empty | select(. != null) | {cle, octets}) ]
      | map(select(.cle != null)) | map(. as $f | ($l[0] | map(select(.Key == $f.cle)) | first) as $o
          | if $o == null then "absent : \($f.cle)" elif $o.Size != $f.octets then "taille \($o.Size) != \($f.octets) : \($f.cle)" else empty end) | .[]' "$etat")
  local nvar nfichiers
  nvar=$(jq '.variables | length' "$etat")
  nfichiers=$(jq --arg d "$dossier/" '[.[] | select(.Key | startswith($d) and endswith(".pmtiles"))] | length' <<<"$liste")
  if [[ -z "$manque" ]]; then verif ok "fichiers cités par l'état $modele" "tous présents avec la taille inscrite"; else verif ko "fichiers cités par l'état $modele" "$manque"; fi
  [[ "$nvar" -eq 8 ]] && verif ok "nombre de variables $modele" "8" || verif ko "nombre de variables $modele" "$nvar au lieu de 8 (échéance $nn)"
  [[ "$nfichiers" -eq "$nvar" ]] && verif ok "PMTiles dans le bucket $modele" "$nfichiers = variables de l'état" || verif ko "PMTiles dans le bucket $modele" "$nfichiers fichiers pour $nvar variables"
  # contenu de l'état : mesures présentes
  jq -e '.mesures.telechargement_s != null and .mesures.tuilage_s != null' "$etat" >/dev/null && verif ok "mesures dans l'état $modele" "$(jq -c '.mesures | {telechargement_s,decodage_grib_s,tuilage_s,ecriture_s,total_s,fils}' "$etat")" \
    || verif ko "mesures dans l'état $modele" "absentes"
  # un PMTiles (t2m) : taille, en-tête, bibliothèque
  cle=$(jq -r '.variables.t2m.cle // empty' "$etat")
  if [[ -n "$cle" ]]; then
    local pm="$TMP/t2m-$modele.pmtiles" attendu mz xz lib
    A s3api get-object --bucket "$BUCKET" --key "$cle" "$pm" >/dev/null
    attendu=$(jq -r '.variables.t2m.octets' "$etat"); mz=$(jq -r '.variables.t2m.minzoom' "$etat"); xz=$(jq -r '.variables.t2m.maxzoom' "$etat")
    [[ "$(wc -c < "$pm")" == "$attendu" ]] && verif ok "taille du PMTiles t2m $modele" "$attendu octets" || verif ko "taille du PMTiles t2m $modele" "$(wc -c < "$pm") au lieu de $attendu"
    verifier_entete_pmtiles "$pm" && verif ok "en-tête PMTiles t2m $modele" "'PMTiles', version 3" || verif ko "en-tête PMTiles t2m $modele" "magie ou version invalide"
    lib=$(lire_pmtiles_lib "$pm")
    if [[ "$(jq -r '.lib' <<<"$lib")" == true ]]; then
      if [[ "$(jq -r '.min_zoom' <<<"$lib")" == "$mz" && "$(jq -r '.max_zoom' <<<"$lib")" == "$xz" ]]; then
        verif ok "ouverture du PMTiles avec la bibliothèque pmtiles ($modele)" "zooms $mz-$xz, $(jq -r '.tuiles' <<<"$lib") tuiles adressées (état : $(jq -r '.variables.t2m.tuiles' "$etat"))"
      else verif ko "ouverture du PMTiles avec la bibliothèque pmtiles ($modele)" "zooms $(jq -c '[.min_zoom,.max_zoom]' <<<"$lib") au lieu de [$mz,$xz]"; fi
    else info "  (bibliothèque pmtiles indisponible : seul l'en-tête a été vérifié)"; fi
    local ent
    ent=$(A s3api head-object --bucket "$BUCKET" --key "$cle" --output json | jq -c '{ContentType,CacheControl}')
    [[ "$(jq -r .ContentType <<<"$ent")" == "application/vnd.pmtiles" && "$(jq -r .CacheControl <<<"$ent")" == *immutable* ]] \
      && verif ok "en-têtes HTTP du PMTiles $modele" "$ent" || verif ko "en-têtes HTTP du PMTiles $modele" "$ent"
  else verif ko "PMTiles t2m $modele" "variable t2m absente de l'état"; fi
  ent=$(A s3api head-object --bucket "$BUCKET" --key "$dossier/_etat/$nn.json" --output json | jq -c '{ContentType,CacheControl}')
  [[ "$(jq -r .CacheControl <<<"$ent")" == "no-cache" ]] && verif ok "en-têtes HTTP de l'état $modele" "$ent" || verif ko "en-têtes HTTP de l'état $modele" "$ent"
}

# Idempotence : reconfigurer sans RW_FORCER, rejouer (démarrage à froid), vérifier « deja_fait », rapidité, aucun nouvel écrit
verifier_idempotence() { # modèle tirage_long
  local modele=$1 tirage=$2 avant apres derniere
  titre "Idempotence : $modele (sans RW_FORCER)"
  changer_env 0 "idem-$modele"
  avant=$(liste_objets)
  invoquer idempotence "$modele" "$tirage" "$ECHEANCE" "msg-idem-$modele-1"
  invoquer idempotence-chaud "$modele" "$tirage" "$ECHEANCE" "msg-idem-$modele-2"
  apres=$(liste_objets)
  derniere=$(tail -2 "$TMP/mesures.jsonl")
  [[ "$(jq -rs 'map(.app.statut) | unique | join(",")' <<<"$derniere")" == "deja_fait" ]] \
    && verif ok "idempotence $modele : statut" "deja_fait aux deux invocations" \
    || verif ko "idempotence $modele : statut" "$(jq -rs 'map(.app.statut // .app.classe) | join(",")' <<<"$derniere")"
  jq -rs 'map(.echecs_lot | length) | add' <<<"$derniere" | grep -qx 0 && verif ok "idempotence $modele : réponse" "batchItemFailures vide" || verif ko "idempotence $modele : réponse" "échecs renvoyés"
  [[ "$avant" == "$apres" ]] && verif ok "idempotence $modele : aucun nouvel écrit" "mêmes clés, ETag et dates de modification" || verif ko "idempotence $modele : aucun nouvel écrit" "la liste des objets a changé"
  local d; d=$(jq -rs 'last | .duree_ms' <<<"$derniere")
  if [[ "$d" != null ]] && awk -v d="$d" 'BEGIN{exit !(d < 2000)}'; then verif ok "idempotence $modele : retour rapide" "${d} ms à chaud"; else verif ko "idempotence $modele : retour rapide" "${d} ms à chaud (attendu < 2 000 ms)"; fi
}

# Source absente : tirage futur -> 404 -> batchItemFailures, aucun état écrit
verifier_source_absente() {
  titre "Source absente : tirage $TIRAGE_FUTUR (futur)"
  invoquer source-absente arome-hd "$TIRAGE_FUTUR" "$ECHEANCE" "msg-absent"
  local d; d=$(tail -1 "$TMP/mesures.jsonl")
  [[ "$(jq -r '.echecs_lot[0].itemIdentifier // empty' <<<"$d")" == "msg-absent" ]] \
    && verif ok "source absente : batchItemFailures" "le message est rendu (itemIdentifier msg-absent)" \
    || verif ko "source absente : batchItemFailures" "réponse $(jq -c '.reponse' <<<"$d") ; journal : $(jq -c '.app' <<<"$d")"
  [[ "$(jq -r '.app.classe // empty' <<<"$d")" == "transitoire" ]] && verif ok "source absente : classe" "transitoire" || verif ko "source absente : classe" "$(jq -c '.app.classe' <<<"$d")"
  local n; n=$(liste_objets | jq --arg p "v1/arome-hd/$FUTUR_COURT/" '[.[] | select(.Key | startswith($p))] | length')
  [[ "$n" -eq 0 ]] && verif ok "source absente : aucun état écrit" "0 objet sous $FUTUR_COURT" || verif ko "source absente : aucun état écrit" "$n objets sous $FUTUR_COURT"
}

# ---------------------------------------------------------------------------------------------------------------------
# Synthèse
# ---------------------------------------------------------------------------------------------------------------------
tableau() {
  titre "Mesures par invocation"
  {
    printf 'phase\tmodèle\téch\tdurée ms\tfacturée ms\tmém. max Mo\tinit ms\ttéléch. s\tdécod. s\ttuilage s\técrit. s\ttotal s\tMo/s\tstatut\n'
    jq -rs '.[] | (.app.mesures // {}) as $m | [ .phase, .modele, .echeance, (.duree_ms // "-"), (.facture_ms // "-"), (.memoire_max_mo // "-"), (.init_ms // "-"),
        ($m.telechargement_s // "-" | if type == "number" then (. * 100 | round / 100) else . end),
        ($m.decodage_grib_s // "-" | if type == "number" then (. * 100 | round / 100) else . end),
        ($m.tuilage_s // "-" | if type == "number" then (. * 100 | round / 100) else . end),
        ($m.ecriture_s // "-" | if type == "number" then (. * 100 | round / 100) else . end),
        ($m.total_s // "-" | if type == "number" then (. * 100 | round / 100) else . end),
        (if ($m.octets_telecharges // 0) > 0 and ($m.telechargement_s // 0) > 0 then (($m.octets_telecharges / 1000000 / $m.telechargement_s) * 10 | round / 10) else "-" end),
        (.app.statut // .app.classe // "?") ] | map(tostring) | join("\t")' "$TMP/mesures.jsonl"
  } | column -t -s $'\t'
  titre "Moyennes (durée en ms) par phase"
  jq -rs 'map(.famille = (.phase | sub("-[0-9]+$"; ""))) | group_by([.famille, .modele]) | .[] | "  \(.[0].famille) \(.[0].modele) : \(length) invocation(s), durée moyenne \((map(.duree_ms // 0) | add / length) | round) ms, facturée moyenne \((map(.facture_ms // 0) | add / length) | round) ms, mémoire max \(map(.memoire_max_mo // 0) | max) Mo, init moyen \((map(.init_ms // empty) | if length > 0 then (add / length | round | tostring) + " ms" else "-" end))"' "$TMP/mesures.jsonl"
  info "Remarque : en phase « froid », l'Init Duration est celle du démarrage ; en « chaud » il n'y en a pas. La durée facturée inclut l'init pour un binaire personnalisé (provided.al2023)."
}

ecrire_resultats() {
  mkdir -p "$SORTIE_DIR"
  FICHIER_JSON="$SORTIE_DIR/$PREFIXE.json"
  jq -n --arg p "$PREFIXE" --arg r "$REGION" --arg date "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --arg zip "$(basename "$ZIP")" \
        --arg sha "$(sha256sum "$ZIP_LOCAL" | cut -d' ' -f1)" --argjson taille "$(wc -c < "$ZIP_LOCAL")" \
        --arg tirage "$TIRAGE" --arg futur "$TIRAGE_FUTUR" --argjson ech "$ECHEANCE" \
        --argjson mem "$MEMOIRE" --argjson delai "$DELAI" --argjson tmp "$TMP_MO" \
        --slurpfile inv "$TMP/mesures.jsonl" --slurpfile ver "$TMP/verifs.jsonl" --argjson echecs "$ECHECS" '
    { prefixe: $p, region: $r, date: $date,
      zip: {nom: $zip, octets: $taille, sha256: $sha},
      fonction: {architecture: "arm64", runtime: "provided.al2023", memoire_mo: $mem, delai_s: $delai, tmp_mo: $tmp},
      source: {tirage: $tirage, echeance: $ech, tirage_futur_absent: $futur},
      invocations: $inv, verifications: $ver, verifications_en_echec: $echecs, nettoyage: "en attente" }' > "$FICHIER_JSON"
  info "Résultats enregistrés : $FICHIER_JSON"
}

# ---------------------------------------------------------------------------------------------------------------------
# Plan (simulation) et exécution
# ---------------------------------------------------------------------------------------------------------------------
afficher_plan() {
  titre "Ce qui serait créé (région $REGION)"
  cat <<EOF
Préfixe unique   : $PREFIXE
Tags             : $TAGS_KV
Bucket S3        : $BUCKET (privé, accès public bloqué, SSE-S3, expiration des objets à 1 jour)
Groupe de journaux : $GROUPE (rétention 1 jour)
Politique IAM    : $POLITIQUE ; rôle : $ROLE (confiance : lambda.amazonaws.com)
Fonction Lambda  : $FN (arm64, provided.al2023, handler bootstrap, ${MEMOIRE} Mo, délai ${DELAI} s, /tmp ${TMP_MO} Mo, hors VPC)
Zip              : ${ZIP} ($( [[ -f "$ZIP" ]] && echo "$(wc -c < "$ZIP") octets" || echo "ABSENT : lancer rust/tools/package-lambda.sh" ))
Variables d'environnement de la fonction :
$(env_lambda 1 initial | jq -r '.Variables | to_entries[] | "  \(.key)=\(.value)"')
Source GRIB      : S3 public OVH ($BASE_OVH), tirage ${TIRAGE:-<à déterminer>}, échéance $ECHEANCE
Politique de permissions (moindre privilège, restreinte au bucket de test) :
$(politique_permissions | jq '.' | sed 's/^/  /')
Événement SQS simulé (exemple) : $(corps_message arome-hd "${TIRAGE:-2026-10-17T03:00:00Z}" "$ECHEANCE")

Déroulement avec --apply :
  1. création des ressources ci-dessus (tout est supprimé à la fin, sauf --garder) ;
  2. mesures, RW_FORCER=1 pour retraiter à chaque fois : HD échéance $ECHEANCE : 3 invocations à froid (variable RW_COLD modifiée entre chaque
     essai) puis 3 à chaud ; AROME échéance $ECHEANCE : 1 à froid puis 2 à chaud ; relevé de la ligne REPORT (durée, durée facturée,
     mémoire max, Init Duration) et des mesures de l'état/journal applicatif (téléchargement, décodage, tuilage) ;
  3. vérifications : objets produits, état et PMTiles téléchargés et comparés, idempotence (sans RW_FORCER, aucun nouvel écrit),
     source absente (tirage futur $( [[ -n "$TIRAGE_FUTUR" ]] && echo "$TIRAGE_FUTUR" || echo "<demain>" ) -> batchItemFailures) ;
  4. tableau et JSON dans out/test-lambda/$PREFIXE.json ;
  5. nettoyage (trap : aussi sur erreur ou interruption), puis contrôle qu'il ne reste rien du préfixe.
EOF
  info "(simulation : ajouter --apply pour exécuter)"
}

preparer_zip() {
  [[ -f "$ZIP" ]] || die "zip introuvable : $ZIP (le produire avec rust/tools/package-lambda.sh, ou indiquer --zip)."
  unzip -Z1 "$ZIP" | grep -qx 'bootstrap' || die "le zip ne contient pas 'bootstrap' à sa racine."
  unzip -Zs "$ZIP" bootstrap | grep -q '^-..x' || die "'bootstrap' n'est pas exécutable dans le zip."
  # copie de travail (le zip d'origine peut être reconstruit pendant le test), puis contrôle de l'architecture (ELF e_machine 0xB7 = aarch64)
  ZIP_LOCAL="$TMP/rw-lambda.zip"; cp "$ZIP" "$ZIP_LOCAL"
  [[ "$(unzip -p "$ZIP_LOCAL" bootstrap | head -c 20 | od -An -tx1 -j18 -N2 | tr -d ' ')" == "b700" ]] \
    || die "bootstrap n'est pas un exécutable ELF arm64 (aarch64) : refus."
  info "Zip : $ZIP ($(wc -c < "$ZIP_LOCAL") octets, sha256 $(sha256sum "$ZIP_LOCAL" | cut -c1-16)…)"
}

principal() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --apply) APPLY=1 ;;
      --garder) GARDER=1 ;;
      --hors-ligne) HORS_LIGNE=1 ;;
      --zip) [[ $# -ge 2 ]] || die "--zip attend un fichier."; ZIP="$2"; shift ;;
      --tirage) [[ $# -ge 2 ]] || die "--tirage attend une date."; TIRAGE_FORCE="$2"; shift ;;
      --echeance) [[ $# -ge 2 ]] || die "--echeance attend un entier."; ECHEANCE="$2"; shift ;;
      --nettoyer) [[ $# -ge 2 ]] || die "--nettoyer attend un préfixe (rw-test-AAAAMMJJ-HHMMSS)."; NETTOYER="$2"; shift ;;
      -h|--aide|--help) usage; exit 0 ;;
      *) echo "Option inconnue : $1 (voir --aide)" >&2; exit 2 ;;
    esac
    shift
  done
  [[ "$ECHEANCE" =~ ^[0-9]+$ ]] && (( ECHEANCE <= 51 )) || die "--echeance : entier de 0 à 51."
  [[ -n "$ZIP" ]] || ZIP="$RACINE/rust/target/lambda/rw-lambda-arm64.zip"
  for c in jq curl; do command -v "$c" >/dev/null || die "$c est requis."; done
  TMP=$(mktemp -d)
  exec 3>&1 4>&2
  trap sortie_finale EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM

  titre "Test Lambda jetable rw-lambda"
  verifier_compte

  if [[ -n "$NETTOYER" ]]; then
    [[ $HORS_LIGNE -eq 1 && $APPLY -eq 1 ]] && die "--hors-ligne est incompatible avec --apply."
    prefixe_valide "$NETTOYER" || die "préfixe invalide : $NETTOYER (attendu rw-test-AAAAMMJJ-HHMMSS)."
    noms "$NETTOYER"
    if [[ $APPLY -ne 1 ]]; then
      titre "Ressources qui seraient supprimées (simulation)"
      if [[ $HORS_LIGNE -eq 1 ]]; then info "(hors ligne : inventaire non consulté)"
      else inventaire "$PREFIXE" | sed 's/^/  /' || avert "inventaire impossible"; fi
      info "(simulation : ajouter --apply pour supprimer)"; exit 0
    fi
    nettoyer_prefixe || exit 1
    exit 0
  fi

  noms "rw-test-$(date -u +%Y%m%d-%H%M%S)"
  if choisir_tirage; then :; elif [[ $APPLY -eq 1 ]]; then die "pas de tirage utilisable (voir --tirage)."; fi

  if [[ $APPLY -ne 1 ]]; then
    if [[ $HORS_LIGNE -eq 0 && "$COMPTE" != "<COMPTE>" ]]; then
      local autres; autres=$(inventaire "rw-test-" 2>/dev/null || true)
      [[ -z "$autres" ]] || { avert "ressources rw-test-* déjà présentes (reste d'un essai précédent ?) :"; sed 's/^/  /' <<<"$autres" >&2; }
    fi
    afficher_plan; exit 0
  fi

  titre "Contrôles préalables"
  local deja autres
  deja=$(inventaire "$PREFIXE") || die "inventaire impossible."
  [[ -z "$deja" ]] || { sed 's/^/  /' <<<"$deja" >&2; die "des ressources portent déjà le préfixe $PREFIXE : refus."; }
  autres=$(inventaire "rw-test-" 2>/dev/null || true)
  [[ -z "$autres" ]] || { avert "ressources rw-test-* d'un essai précédent (non touchées) :"; sed 's/^/  /' <<<"$autres" >&2; }
  info "Aucune ressource ne porte le préfixe $PREFIXE."
  preparer_zip
  : > "$TMP/mesures.jsonl"; : > "$TMP/verifs.jsonl"
  info "Nettoyage a posteriori si besoin : AWS_PROFILE=$AWS_PROFILE tools/test-lambda-jetable.sh --nettoyer $PREFIXE --apply"

  creer

  titre "Mesures (RW_FORCER=1 : l'échéance est retraitée à chaque invocation)"
  local i
  for i in 1 2 3; do changer_env 1 "hd-froid-$i"; invoquer "froid-$i" arome-hd "$TIRAGE" "$ECHEANCE" "msg-hd-froid-$i"; done
  for i in 1 2 3; do invoquer "chaud-$i" arome-hd "$TIRAGE" "$ECHEANCE" "msg-hd-chaud-$i"; done
  changer_env 1 "arome-froid-1"
  invoquer "froid-1" arome "$TIRAGE" "$ECHEANCE" "msg-arome-froid-1"
  for i in 1 2; do invoquer "chaud-$i" arome "$TIRAGE" "$ECHEANCE" "msg-arome-chaud-$i"; done

  verifier_sorties arome-hd "$TIRAGE_COURT" "$ECHEANCE"
  verifier_sorties arome "$TIRAGE_COURT" "$ECHEANCE"
  verifier_idempotence arome-hd "$TIRAGE"
  verifier_idempotence arome "$TIRAGE"
  verifier_source_absente

  tableau
  titre "Bilan des vérifications"
  jq -rs '(map(select(.ok | not))) as $ko | if ($ko | length) == 0 then "  toutes les vérifications sont passées (\(length))" else ($ko[] | "  ÉCHEC : \(.controle) : \(.detail)") end' "$TMP/verifs.jsonl"
  ecrire_resultats
  [[ $ECHECS -eq 0 ]] || { avert "$ECHECS vérification(s) en échec."; exit 1; }
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then principal "$@"; fi
