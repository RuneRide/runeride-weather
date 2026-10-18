<!-- SPDX-License-Identifier: EUPL-1.2 -->
# Test jetable de `rw-lambda` sur un compte AWS réel (18 octobre 2026)

Script : [`tools/test-lambda-jetable.sh`](../tools/test-lambda-jetable.sh). Il lève les points « tester sur un compte réel » de [poc-rust/lambda.md](poc-rust/lambda.md) §13 : pile TLS en Rust pur face à S3, durée de téléchargement depuis Lambda, mémoire réelle, démarrage à froid, `ListBucket` / 404. Rien n'est créé sans `--apply`, et tout est supprimé à la fin.

## Lancement

```sh
rust/tools/package-lambda.sh                                  # si le zip n'existe pas encore (arm64)
AWS_PROFILE=bike360 tools/test-lambda-jetable.sh              # simulation : plan, politique IAM, tirage choisi, rien n'est créé
AWS_PROFILE=bike360 tools/test-lambda-jetable.sh --apply      # le vrai test (compter 5 à 10 minutes)
```

Autres options : `--zip <fichier>` (défaut `rust/target/lambda/rw-lambda-arm64.zip`), `--tirage 2026-10-18T09:00:00Z` et `--echeance N` (défaut : tirage récent complet trouvé par requêtes HTTP publiques sur le S3 OVH, échéance 12), `--garder` (pas de nettoyage), `--nettoyer rw-test-AAAAMMJJ-HHMMSS [--apply]` (suppression a posteriori ; simulation sans `--apply`), `--hors-ligne` (simulation sans aucun appel AWS).

## Ce qui est créé (région eu-north-1, préfixe `rw-test-<horodatage>`, tags `projet=runeride-weather,temporaire=oui`)

| Ressource | Réglage |
|---|---|
| Bucket S3 | privé, accès public bloqué, SSE-S3, expiration des objets à 1 jour |
| Groupe de journaux | `/aws/lambda/<préfixe>`, rétention 1 jour |
| Politique et rôle IAM | politique de lambda.md §10.1 **sans SQS**, restreinte au bucket de test (`PutObject`/`GetObject` sur `v1/arome/*`, `v1/arome-hd/*` et `v1/isobares/*`, `ListBucket`, journaux) |
| Fonction Lambda | arm64, `provided.al2023`, handler `bootstrap`, 1 769 Mo, 120 s, `/tmp` 2 048 Mo, hors VPC |

Variables d'environnement : `RW_BUCKET` (bucket de test), `RW_PREFIXE=v1`, `RW_SOURCE` (S3 OVH réel), `RW_COLD` (marqueur modifié pour forcer un démarrage à froid) et `RW_FORCER=1` pendant les mesures (sinon l'idempotence ferait renvoyer `deja_fait` dès la deuxième invocation). Pas de file SQS : l'événement SQS est simulé et passé à `aws lambda invoke`.

Écart volontaire avec lambda.md §10.1 : le GeoJSON des isobares est écrit sous `v1/isobares/<modèle>/…` (hors de `v1/arome/*`) ; sans le droit correspondant, l'AROME échouerait en 403 (classé transitoire). À reporter dans la politique de production.

## Déroulement avec `--apply`

1. **Contrôles** : profil, compte (identifiant seul affiché), région `eu-north-1` imposée, refus si une ressource porte déjà le préfixe, zip valide (`bootstrap` exécutable à la racine, ELF arm64).
2. **Mesures** : HD échéance 12 : 3 invocations à froid (changement de `RW_COLD` entre chaque), puis 3 à chaud ; AROME échéance 12 : 1 à froid, 2 à chaud. Relevé de la ligne `REPORT` (durée, durée facturée, mémoire maximale, `Init Duration`) via `--log-type Tail`, et des mesures applicatives du journal (téléchargement, décodage, tuilage, écriture, débit en Mo/s).
3. **Vérifications** : objets produits ; état d'échéance (schéma, statut, fichiers cités présents avec la taille inscrite, 8 variables) ; un PMTiles téléchargé (taille, en-tête `PMTiles` version 3, ouverture avec la bibliothèque `pmtiles` si disponible, sinon la roue `pmtiles-*.whl` du dépôt, zooms comparés à l'état ; `Content-Type` et `Cache-Control` des objets) ; **idempotence** (sans `RW_FORCER` : `deja_fait`, retour en moins de 2 s à chaud, listes d'objets identiques avant/après, donc aucun nouvel écrit) ; **source absente** (tirage dans environ 36 h : `batchItemFailures` contient le message, classe transitoire, aucun état écrit).
4. **Résultats** : tableau à l'écran et `out/test-lambda/<préfixe>.json` (invocations, vérifications, zip, configuration, état du nettoyage).
5. **Nettoyage**, toujours (sortie normale, erreur, Ctrl-C), puis contrôle qu'aucune ressource ne porte le préfixe. En cas de reste, le script affiche la commande `--nettoyer` à relancer. Un `--nettoyer` n'accepte que le format `rw-test-AAAAMMJJ-HHMMSS`.

Code de sortie non nul si une vérification échoue ou si le nettoyage laisse des restes.

## Coût

Quelques centimes au plus. Seul ce qui est sûr est chiffré : la Lambda arm64 coûte 0,0000133334 $ par Go-seconde ([architecture-aws.md](architecture-aws.md) §4.1) ; avec environ 15 invocations et 1,769 Go, même au délai maximal de 120 s chacune la borne est ≈ 3 200 Go-s ≈ 0,04 $ (en pratique quelques dizaines de secondes en tout). S3, CloudWatch Logs et IAM : quelques requêtes et quelques Ko. Les téléchargements depuis OVH sont de l'entrée de données depuis Internet. Ni NAT, ni SQS, ni VPC.

## Limites

- Le script a été testé en simulation et contre un **faux `aws`** local (flux complet, nettoyage, interruption par signal) ; il n'a **jamais été exécuté contre AWS**. Les points d'API réels (propagation IAM, format exact de la ligne `REPORT`, taille de la fin de journal limitée à 4 Ko par `--log-type Tail`) restent à confirmer au premier essai.
- `Init Duration` est absent des invocations à chaud (normal). Un démarrage à froid « observé » est celui où ce champ est présent.
- Les durées mesurées dépendent de la liaison Lambda vers OVH à l'instant du test : à répéter à d'autres heures avant de fixer `MaximumConcurrency` et le délai.
