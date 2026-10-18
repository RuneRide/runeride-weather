<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# Mise en production : runbook

Statut au 18 octobre 2026 : **rien n'est déployé.** Ce document ordonne tout ce qu'il reste à faire pour ouvrir `weather.runeride.fr`, en séparant ce que l'assistant (IA) a préparé de ce que **seul le propriétaire** peut ou doit faire (comptes, jetons, `terraform apply`, DNS chez o2switch, secrets GitHub, envoi des fichiers). Il ne contient aucun secret. Architecture : [architecture-aws.md](architecture-aws.md) (§4 diffusion, §10 lancement à moins de 1 $/mois). Module Terraform (rédigé par ailleurs, **non appliqué**) : `runeride-cloud/terraform/meteo/` et son `README.md` : ce runbook y renvoie sans le recopier.

Légende : **[IA]** déjà fait ou faisable par l'assistant sans droit particulier ; **[TOI]** action du propriétaire ; `- [x]` fait, `- [ ]` à faire. Les commandes se lancent depuis la racine du dépôt `runeride-weather` sauf mention contraire. Profil AWS utilisé dans les exemples : `bike360` (région `eu-north-1`).

## 0. Vue d'ensemble

```
Navigateur ──► weather.runeride.fr  (o2switch, statique, FTPS depuis GitHub Actions : viewer/)
     │
     └──────► weather-data.runeride.fr  (CloudFront + S3 privé, certificat ACM us-east-1)
                 ├─ /v1/manifest.json                                  (TTL 30 s)
                 ├─ /v1/{modele}/{tirage}/{variable}/{NN}.pmtiles       (immuable, Range)
                 ├─ /v1/isobares/arome/{tirage}/{NN}.geojson            (isobares)
                 └─ /basemap/domaine-z9-20261017.pmtiles                (fond de carte, 175 Mo, Range)
```

Le viewer ne dépend d'**aucune autre origine** à l'exécution : bibliothèques (MapLibre, pmtiles, @protomaps/basemaps), polices et sprites du fond de carte sont servis par `weather.runeride.fr` lui-même ; seules les données viennent de `weather-data.runeride.fr`. La politique CSP (`viewer/.htaccess`) n'autorise que ces deux origines.

### 0.1 Contrat de publication attendu par le viewer

| Ressource | URL | Écrite par |
|---|---|---|
| Manifeste | `https://weather-data.runeride.fr/v1/manifest.json` | Lambda `manifeste` (ou à la main, §7) |
| Tuiles d'une échéance | `…/v1/` + `file_template` du manifeste, aujourd'hui `{model}/{run_id}/{variable}/{step}.pmtiles` | Lambda `worker` |
| Isobares | `…/v1/isobares/arome/{tirage}/{NN}.geojson` (clé produite par `rw-lambda`) | Lambda `worker` |
| Fond de carte | `…/basemap/domaine-z9-20261017.pmtiles` | **à la main, une fois** (§6) |

Le fond de carte est volontairement **hors du préfixe `v1/`** : il n'est pas concerné par l'expiration des tirages ni par les notifications du manifeste. Son nom porte la date du build Protomaps (constante `FICHIER_FOND` dans `viewer/js/config.js`) : un nouveau fond = un nouveau nom, jamais un écrasement (le fichier est servi `immutable` un an).

## 1. Préparation (déjà faite par l'IA)

- [x] **[IA]** Viewer de production : `viewer/js/config.js` (`creerEnv`, hôte `https://weather-data.runeride.fr`), mode développement local inchangé (`python3 viewer/serve.py`).
- [x] **[IA]** Autonomie : bibliothèques dans `viewer/vendor/` (empreintes SHA-384 identiques à celles de l'ancien SRI, voir `viewer/vendor/LISEZMOI.md`), 27 plages de polices Noto Sans et 8 sprites dans `viewer/assets/fond/` (35 fichiers, 2,6 Mo, licences jointes), icônes RuneRide copiées depuis `identity/` (lecture seule).
- [x] **[IA]** Erreurs côté utilisateur, préchargement sobre, « À propos et sources », FR/EN, métadonnées, PWA minimale (`manifest.webmanifest`, `sw.js`).
- [x] **[IA]** `viewer/.htaccess` (HTTPS, CSP, cache, compression), `.github/workflows/{ci,deploy,release}.yml`, `tools/ftp-setup.sh` (**écrit, jamais exécuté**).
- [x] **[IA]** Tests : `node --test viewer/tests viewer/tools` (72 tests dont 14 de production), tests Python et Rust passés en local (voir §12).
- [ ] **[TOI]** Relire et valider ces fichiers, puis les committer (rien n'est committé). Le dépôt GitHub est `RuneRide/runeride-weather`.

## 2. Comptes, jetons et outils (une seule fois)

- [ ] **[TOI]** AWS : profil `bike360` utilisable (`aws sts get-caller-identity --profile bike360`). Pas de jeton à créer pour ce runbook si le profil existe déjà (il sert à Terraform et à l'envoi S3).
- [ ] **[TOI]** GitHub : `gh auth status` ; droit d'écrire les secrets de `RuneRide/runeride-weather`.
- [ ] **[TOI]** cPanel o2switch : le jeton d'API est déjà stocké dans AWS Secrets Manager (`runeride/cpanel`, utilisé par `website/tools/ftp-setup.sh`) ; rien à créer, seulement vérifier qu'il existe : `aws secretsmanager describe-secret --secret-id runeride/cpanel --region eu-north-1 --profile bike360 --query Name`.
- [ ] **[TOI]** Outils locaux : `aws`, `gh`, `jq`, `curl`, `openssl`, `terraform`, Node 24, Python 3.
- [ ] **[TOI]** Décisions de produit encore ouvertes (architecture-aws.md §8) : profil de lancement L-A (2 tirages/jour, tout le domaine) ou L-B ; rétention 2 jours ; forfait CloudFront Free ou paiement à l'usage.

## 3. Hébergement du viewer : compte FTP et secrets GitHub (o2switch)

Sous-domaine `weather.runeride.fr`, racine dédiée `~/weather`, compte FTP `deploy-weather@runeride.fr` enfermé dans ce dossier : comme la vitrine, rien de ce qui sert déjà `runeride.fr` n'est touché.

- [ ] **[TOI]** Simulation (liste les racines web et les comptes FTP, annonce le plan, ne change rien) :
  ```sh
  AWS_PROFILE=bike360 tools/ftp-setup.sh
  ```
- [ ] **[TOI]** Si le plan est correct :
  ```sh
  AWS_PROFILE=bike360 tools/ftp-setup.sh --apply
  ```
  Il crée le sous-domaine s'il n'existe pas, le compte FTP, teste la connexion FTPS et pose les secrets `FTP_SERVER`, `FTP_USERNAME`, `FTP_PASSWORD` sur `RuneRide/runeride-weather` (le mot de passe ne s'affiche jamais). **Non testé** : si l'appel de création du sous-domaine échoue, créer le sous-domaine à la main dans cPanel (« Domaines », racine `weather`) et relancer le script.
- [ ] **[TOI]** Vérifier les secrets : `gh secret list --repo RuneRide/runeride-weather` (attendu : les trois noms).
- [ ] **[TOI]** Attendre qu'AutoSSL de cPanel délivre le certificat de `weather.runeride.fr` (quelques minutes) : `curl -sI https://weather.runeride.fr/ | head -1`.
- Rotation du mot de passe FTP plus tard : `AWS_PROFILE=bike360 tools/ftp-setup.sh --apply --rotate`.

## 4. Infrastructure AWS : module `runeride-cloud/terraform/meteo`

Le module est en cours de rédaction par ailleurs et **n'a jamais été planifié ni appliqué**. Suivre son `README.md` (« Ordre d'application recommandé ») : `terraform init`, `terraform plan` (à lire), puis chaque `apply` avec accord explicite. Points qui concernent ce runbook :

- [ ] **[TOI]** `terraform apply -target=aws_acm_certificate.donnees` (certificat ACM en `us-east-1`).
- [ ] **[TOI]** Créer chez o2switch (cPanel, « Éditeur de zone DNS ») le **CNAME de validation ACM** donné par la sortie `terraform output dns` ; attendre l'émission du certificat.
- [ ] **[TOI]** Passer `certificat_valide = true`, `terraform apply` (le zip du worker, §5, doit exister).
- [ ] **[TOI]** Créer le **CNAME `weather-data`** vers le nom CloudFront (dernier élément de `terraform output dns`, ou `terraform output cloudfront`). Vérifier : `dig +short CNAME weather-data.runeride.fr`.
- [ ] **[TOI]** Confirmer les e-mails d'abonnement SNS (alarmes).
- [ ] **[TOI]** Vérifier dans `variables.tf` que `origines_autorisees` contient `https://weather.runeride.fr` (la politique CORS ne doit autoriser que lui et le viewer local `http://localhost:8187`).
- [ ] **[TOI]** Relever le nom du bucket : `BUCKET=$(terraform -chdir=../runeride-cloud/terraform/meteo output -raw bucket)`.
- [ ] **[TOI]** Contrôler que la politique du bucket et le comportement CloudFront par défaut couvrent aussi `basemap/*` (en dehors de `v1/`) : si le module restreint la lecture au seul préfixe `v1/`, l'étendre avant le §6.

## 5. Fonctions Lambda : zips arm64

Le workflow `release.yml` construit le zip `bootstrap` arm64 de `rw-lambda`, `rw-poller` et `rw-manifeste` (un crate absent est ignoré avec un avertissement) et **ne déploie rien**.

- [ ] **[TOI]** Pousser un tag : `git tag v0.1.0 && git push origin v0.1.0` (ou lancer à la main : `gh workflow run release.yml --repo RuneRide/runeride-weather`). Les zips et leurs `.sha256` sont en pièces jointes de la release (ou artefacts du workflow).
- [ ] **[TOI]** Télécharger : `gh release download v0.1.0 --repo RuneRide/runeride-weather --dir dist && (cd dist && sha256sum -c *.sha256)`.
- [ ] **[TOI]** Renseigner `chemin_zip_worker` (et, plus tard, ceux du poller et du manifeste) dans `terraform.tfvars`.
- Sans passer par GitHub : `rust/tools/package-lambda.sh arm64` produit `rust/target/lambda/rw-lambda-arm64.zip` (prérequis : `rustup target add aarch64-unknown-linux-musl`).

## 6. Fond de carte (175 Mo) : envoi, une seule fois

Fichier : `out/basemap/domaine-z9.pmtiles` (extrait Protomaps z0-9, build 20261017, [poc-j1/fond-de-carte.md](poc-j1/fond-de-carte.md)). Clé S3 : `basemap/domaine-z9-20261017.pmtiles`.

- [ ] **[TOI]** Contrôler le fichier : `ls -l out/basemap/domaine-z9.pmtiles && sha256sum out/basemap/domaine-z9.pmtiles` (noter la somme).
- [ ] **[TOI]** Envoyer (multipart automatique ; ne pas compresser, ne pas rejouer sous un autre nom) :
  ```sh
  aws s3 cp out/basemap/domaine-z9.pmtiles "s3://$BUCKET/basemap/domaine-z9-20261017.pmtiles" \
    --content-type application/vnd.pmtiles \
    --cache-control "public, max-age=31536000, immutable" \
    --profile bike360 --region eu-north-1
  ```
- [ ] **[TOI]** Vérifier le Range (attendu : `HTTP/2 206`, `content-range: bytes 0-16383/…`) et le CORS :
  ```sh
  curl -s -o /dev/null -D - -H 'Range: bytes=0-16383' -H 'Origin: https://weather.runeride.fr' \
    https://weather-data.runeride.fr/basemap/domaine-z9-20261017.pmtiles | grep -iE '^(HTTP|content-range|access-control|x-cache|cache-control)'
  ```
- Renouvellement du fond (trimestriel suffit) : régénérer l'extrait ([poc-j1/fond-de-carte.md](poc-j1/fond-de-carte.md) §8), l'envoyer sous un **nouveau nom** daté, changer `FICHIER_FOND` dans `viewer/js/config.js` et dans ce runbook, redéployer le viewer.

## 7. Premier tirage et manifeste

Deux voies. La voie A suffit pour ouvrir le viewer avant que le poller existe ; la voie B est le régime normal.

**Voie A : envoi manuel d'un tirage de test (PoC `reduit2`)**

- [ ] **[TOI]** Sauvegarder le manifeste éventuellement déjà présent (le versionnage S3 est désactivé, il n'y a pas de retour arrière automatique) : `aws s3 cp "s3://$BUCKET/v1/manifest.json" manifest-avant.json --profile bike360 --region eu-north-1 || true`.
- [ ] **[TOI]** Tuiles (≈ 1,2 Go, `--dryrun` d'abord) :
  ```sh
  aws s3 sync out/tiles-reduit2/ "s3://$BUCKET/v1/" --exclude '*' --include '*.pmtiles' \
    --content-type application/vnd.pmtiles --cache-control "public, max-age=31536000, immutable" \
    --profile bike360 --region eu-north-1 --dryrun
  ```
  puis la même commande sans `--dryrun`.
- [ ] **[TOI]** Isobares (≈ 9 Mo ; `application/json` pour que CloudFront les compresse) :
  ```sh
  aws s3 sync out/isobars/arome/ "s3://$BUCKET/v1/isobares/arome/" --exclude '*' --include '*.geojson' \
    --content-type application/json --cache-control "public, max-age=31536000, immutable" \
    --profile bike360 --region eu-north-1
  ```
- [ ] **[TOI]** Manifeste **en dernier** (les tuiles d'abord, jamais l'inverse) :
  ```sh
  aws s3 cp out/manifest/manifest.json "s3://$BUCKET/v1/manifest.json" --content-type application/json \
    --cache-control "public, max-age=30" --profile bike360 --region eu-north-1
  ```
  Le manifeste du PoC date du 18/10 (tirage du 17/10) : le viewer affichera l'avis « prévisions de plus de 30 h » tant que le pipeline réel n'a pas pris le relais ; c'est le comportement voulu.

**Voie B : pipeline réel**

- [ ] **[TOI]** Activer poller et manifeste dans le module (`activer_poller`, `activer_manifeste`, voir son README), poster un message de test dans `file_jobs`, vérifier journaux et alarmes, puis laisser tourner 24 h.

## 8. Déploiement du viewer

- [ ] **[TOI]** Pousser sur `main` : le workflow `deploy.yml` lance d'abord les tests Node, puis synchronise `viewer/` par FTPS vers `~/weather` (exclut `tests/`, `tools/`, `serve.py` et les pages de démonstration `isobares.html` et `particules.html`, qui chargent encore leurs bibliothèques depuis un CDN). Ou à la main : `gh workflow run deploy.yml --repo RuneRide/runeride-weather` puis `gh run watch --repo RuneRide/runeride-weather`.
- [ ] **[TOI]** Sans les secrets (§3), le workflow ne déploie rien et affiche un avertissement : c'est normal.
- [ ] **[TOI]** Vérifier les en-têtes servis par o2switch :
  ```sh
  curl -sI https://weather.runeride.fr/ | grep -iE '^(HTTP|content-security-policy|strict-transport|x-content-type|cache-control)'
  curl -sI https://weather.runeride.fr/vendor/maplibre-gl/maplibre-gl.js | grep -iE '^(HTTP|cache-control|content-encoding)'
  curl -sI -H 'Accept-Encoding: gzip' "https://weather.runeride.fr/assets/fond/fonts/Noto%20Sans%20Regular/0-255.pbf" | grep -iE '^(HTTP|content-encoding|content-length)'
  ```
  Attendu : CSP présente, `no-cache` sur la page, `max-age=2592000` sur `vendor/` et `assets/`, compression sur les `.pbf`. Si la CSP manque, o2switch ignore `.htaccess` ou un module Apache manque (`mod_headers`).

## 9. Vérifications de bout en bout

Données (§4 à §7 faits) :

- [ ] `curl -s https://weather-data.runeride.fr/v1/manifest.json | jq '.generated, (.models | keys)'`
- [ ] `curl -sI https://weather-data.runeride.fr/v1/manifest.json | grep -iE 'cache-control|etag|content-encoding'` (TTL 30 s, ETag).
- [ ] Une tuile : `curl -s -o /dev/null -D - -H 'Range: bytes=0-16383' -H 'Origin: https://weather.runeride.fr' "https://weather-data.runeride.fr/v1/arome/<tirage>/t2m/03.pmtiles" | grep -iE '^(HTTP|content-range|access-control|x-cache|cache-control)'` : 206, `Access-Control-Allow-Origin: https://weather.runeride.fr`. Répéter : `X-Cache: Hit from cloudfront`.
- [ ] Préflight (nécessaire dès que la bibliothèque envoie `If-Match`) : `curl -s -o /dev/null -D - -X OPTIONS -H 'Origin: https://weather.runeride.fr' -H 'Access-Control-Request-Method: GET' -H 'Access-Control-Request-Headers: range,if-match' "https://weather-data.runeride.fr/v1/arome/<tirage>/t2m/03.pmtiles"` : 200 ou 204 avec `Access-Control-Allow-Headers` contenant `Range` et `If-Match`.
- [ ] Dossiers internes jamais servis : `curl -s -o /dev/null -w '%{http_code}\n' https://weather-data.runeride.fr/v1/arome/<tirage>/_domaine/03.npz` : 403 ou 404.
- [ ] Origine non autorisée : même requête avec `Origin: https://exemple.invalid` : pas d'`Access-Control-Allow-Origin`.

Viewer (navigateur, fenêtre privée, `https://weather.runeride.fr/`) :

- [ ] La carte s'affiche, couche Vent, particules, frise ; changer de couche ; zoomer jusqu'à AROME HD ; couche Pression avec valeurs d'isobares.
- [ ] Console : **aucune** erreur ni violation de CSP. Onglet Réseau : seules les origines `weather.runeride.fr` et `weather-data.runeride.fr`.
- [ ] Nombre de requêtes à l'ouverture (voir §11 : référence ≈ 40 à 60 requêtes, ≈ 1,7 Mo non compressé, dont ≈ 25 de données) ; relever aussi ce qui est redemandé au rechargement (cache HTTP sur les réponses `Range` non mesuré).
- [ ] Bas de page : attribution Météo-France (Licence Ouverte v2.0), Protomaps © OpenStreetMap contributors (ODbL), lien « À propos et sources » ouvrant la fenêtre (EUPL-1.2, licences).
- [ ] Langue : `?lang=en` (ou navigateur en anglais) passe l'interface en anglais ; la pastille de tirage et la frise restent en français (limite connue).
- [ ] Hors ligne (DevTools > Réseau > Hors connexion, après un premier chargement) : la page s'ouvre (service worker), message « Hors ligne ».
- [ ] Mobile réel (iOS Safari et Android Chrome) : fluidité, pastille, cibles tactiles, « Ajouter à l'écran d'accueil » (icône RuneRide).
- [ ] Facultatif : Lighthouse (accessibilité, PWA) sur la page publique.
- [ ] Au bout de 24 h (voie B) : le tirage suivant apparaît sans intervention, fraîcheur ≤ 15 min après publication ; alarmes SNS silencieuses ; facture AWS (Budgets) à 0 $.

## 10. Retour arrière

| Quoi | Comment |
|---|---|
| **Viewer** | Revenir à la version précédente : `git revert <commit>` puis push (le workflow redéploie), ou `gh workflow run deploy.yml --ref <tag-ou-commit-sain> --repo RuneRide/runeride-weather`. Pour couper net l'accès : renommer `~/weather/index.html` dans cPanel (Gestionnaire de fichiers) ou supprimer le sous-domaine. |
| **Service worker en cause** (page figée chez des visiteurs) | Incrémenter `VERSION` dans `viewer/sw.js` et redéployer ; en urgence, publier un `sw.js` qui appelle `self.registration.unregister()` et vide les caches. |
| **Manifeste** | Pas de versionnage S3 : remettre la copie faite au §7 (`aws s3 cp manifest-avant.json "s3://$BUCKET/v1/manifest.json" --content-type application/json --cache-control "public, max-age=30"`) ou laisser la Lambda `manifeste` le reconstruire. Cache de 30 s : l'effet est visible en moins d'une minute. |
| **Tirage défectueux** | Supprimer ses objets (`aws s3 rm "s3://$BUCKET/v1/arome/<tirage>/" --recursive`) **puis** régénérer le manifeste ; les anciens fichiers restent 1 an dans les caches CloudFront si leur contenu a été corrigé sous le même nom : invalider par chemin (`aws cloudfront create-invalidation …`, 1 000 chemins gratuits/mois) ou utiliser une révision dans le chemin (décision ouverte n° 6 de architecture-aws.md). |
| **Pipeline** | Couper l'alimentation : `mapping_actif = false` (module), ou désactiver la règle EventBridge du poller. Les données déjà publiées restent servies. |
| **Fond de carte** | Remettre `FICHIER_FOND` à l'ancien nom (les anciens fichiers ne sont jamais écrasés) et redéployer. |
| **DNS / CloudFront** | Supprimer le CNAME `weather-data` chez o2switch : le viewer affiche alors « Prévisions indisponibles » avec un bouton « Réessayer ». `terraform destroy` seulement après sauvegarde et accord explicite (le bucket contient les tirages). |
| **Hébergement** | `AWS_PROFILE=bike360 tools/ftp-setup.sh --apply --rotate` change le mot de passe FTP ; supprimer le compte `deploy-weather` dans cPanel pour couper le déploiement. |

## 11. Comportement du viewer en production

### 11.1 Environnement

`viewer/js/config.js` : sur l'hôte `weather.runeride.fr` (ou avec `?env=prod` ailleurs), le viewer lit le manifeste, les tuiles, les isobares et le fond de carte sur `https://weather-data.runeride.fr`. Sinon (127.0.0.1, `localhost`), il garde le mode développement : `serve.py` et ses montages `/data*`, `/manifest`, `/isobares`, `/basemap`. `?env=prod&donnees=http://127.0.0.1:8232` pointe vers une maquette locale (refusé sur le vrai hôte : aucun paramètre ne peut y rediriger les données).

### 11.2 Erreurs côté utilisateur (testées en local avec Chrome et une maquette)

| Situation | Comportement |
|---|---|
| Manifeste injoignable | 3 essais espacés (0,6 s, 1,2 s), puis écran « Prévisions indisponibles » avec bouton « Réessayer » ; rechargement automatique au retour du réseau |
| Hors ligne | message dédié ; bandeau « Hors ligne » pendant l'utilisation, « Connexion rétablie » ensuite |
| Manifeste sans aucun tirage, ou illisible | écrans distincts (« Aucun tirage… », « liste illisible ») |
| Tirage incomplet | repli par échéance déjà géré (D8) : avis discret sous la pastille |
| Prévisions de plus de 30 h | bandeau d'information (15 s) |
| Tuiles en échec (réseau, 5xx) | bandeau après 2 échecs ; nouvel essai de la tuile après 5 s, 10 s, 20 s… (plafond 60 s) ; délai maximal de 20 s par requête |
| Fond de carte ou isobares en échec | bandeau bref « Une partie de la carte ne s'est pas chargée » |
| WebGL indisponible | message dédié |

### 11.3 Préchargement sobre (mesuré)

Mesure faite sur le viewer actuel et sur sa version précédente (Chrome sans interface, WebGL logiciel, serveur local sans latence, profil `reduit2`, vue France, couche Vent, aucune compression : seuls les **nombres de requêtes** et les octets de données sont indépendants de la machine) :

| Étape | Avant (précharge de 3 h dès l'ouverture) | Après (1 h à l'arrêt, 3 h en lecture) |
|---|---|---|
| Requêtes de données après l'ouverture et 12 s de repos | 46 (0,72 Mo) | **24 (0,37 Mo)** |
| 6 pas de frise de 1 h (à l'arrêt) | +66 requêtes (+1,1 Mo) | +66 requêtes (+1,1 Mo) |
| Lecture ×1 pendant 8 s | +77 requêtes (+1,4 Mo) | +99 requêtes (+1,75 Mo) |
| **Total après ces trois étapes** | 189 requêtes (3,2 Mo) | 189 requêtes (3,2 Mo) |

La politique (`horizonPrecharge`, `viewer/js/integration-logique.js`, 9 cas testés) : **3 h en lecture, 1 h à l'arrêt, 0 si l'économie de données est demandée (`Save-Data`), si l'onglet est masqué ou hors ligne ; 1 h seulement en lecture sur connexion déclarée 2g/3g**. Gain : −22 requêtes et −0,35 Mo à l'ouverture, soit environ −48 % des requêtes de données d'une session **passive** (ouverture puis rien) ; pour une session qui joue l'animation, le total est identique (la politique décale le coût vers l'usage réel : une heure de frise ≈ 11 requêtes, soit 1 en-tête de 16 ko et une dizaine de tuiles). Total d'une ouverture hors cache : ≈ 40 à 60 requêtes, ≈ 1,7 Mo non compressé (dont 1,19 Mo de bibliothèques, ≈ 0,3 Mo avec gzip), très en dessous des 150 à 535 requêtes par session de architecture-aws.md §10.4 ; une lecture de 8 s ajoute ≈ 80 à 100 requêtes. Le comportement du cache HTTP du navigateur sur les réponses `Range` n'a pas été mesuré. **À remesurer en production** avec les journaux CloudFront (nombre réel de requêtes par session). Leviers restants, non faits : lecture groupée des en-têtes PMTiles (16 ko par fichier, 0,85 Mo par couche pour toute la frise), tuiles de 512 px, `preconnect` déjà ajouté.

### 11.4 Limites connues

- L'interface est traduite (boutons, couches, légende, erreurs, « À propos »), **pas** les libellés de tirage, d'échéance et de date (pastille, frise, avis de repli), qui restent en français.
- La CSP n'autorise que `weather-data.runeride.fr` ; une future API (`/v1/point`, `/v1/route`) demandera d'ajouter son origine à `connect-src` dans `viewer/.htaccess`.
- Le service worker met en cache la coquille (page, code, bibliothèques, polices et sprites) mais **pas** les prévisions : hors ligne, la carte s'ouvre sans données si le navigateur n'a plus les tuiles.
- `tools/ftp-setup.sh` et le workflow `deploy.yml` n'ont **jamais été exécutés** (pas d'accès cPanel ni GitHub depuis cet environnement) ; l'appel de création du sous-domaine est le plus incertain.
- `rust/Cargo.lock` : au moment des essais il n'était pas à jour avec `rust/Cargo.toml` (travail en cours) ; `cargo test --locked` du workflow `ci.yml` échouera tant que le verrou n'est pas régénéré et committé.
- Les objets internes (`_poller`, `_domaine`…) sont dans le préfixe `v1/` : leur non-service dépend du comportement CloudFront `*/_*` du module (à vérifier au §9).

## 12. Vérifications faites en local (assistant)

| Contrôle | Résultat |
|---|---|
| `node --test viewer/tests viewer/tools` | 72 tests, 0 échec |
| Chrome sans interface, viewer servi **avec la CSP exacte du `.htaccess`**, données sur un autre port | carte, tuiles, particules, isobares et fond OK, 0 violation |
| Manifeste injoignable / vide / tuiles en 503 / hors ligne | messages attendus (voir §11.2) |
| Interface anglaise et fenêtre « À propos » | OK (captures vues) |
| `actionlint` 1.7.12 sur les trois workflows, `yaml.safe_load` | aucune erreur |
| Python : `unittest` des dossiers `tests/{tiling,isobars,manifest,basemap,ingest}` avec `numpy`, `pillow`, `pmtiles`, `eccodes` | tous verts |
| Rust : `cargo test --release` des crates sans C (`rw-grib`, `rw-aws`, `rw-isobares`, `rw-poller`, `rw-manifeste`, `rw-lambda`), puis `rw-cli` et `rw-tiles` en `webp-pure` | tous verts (sur une copie, sans `--locked`) |
| `bash -n tools/ftp-setup.sh` | syntaxe OK (`shellcheck` absent) |
| Non vérifiable ici | `.htaccess` sur Apache o2switch, FTPS, AutoSSL, CloudFront, build arm64 en CI |
