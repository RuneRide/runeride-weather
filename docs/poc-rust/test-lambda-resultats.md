# Test Lambda réel (arm64) — résultats du 18 octobre 2026

Test jetable exécuté par l'utilisateur avec `tools/test-lambda-jetable.sh --apply` (eu-north-1, profil `bike360`) : tirage `2026-10-18T15:00:00Z`, échéance 12, Lambda `provided.al2023` arm64, 1 769 Mo, `/tmp` 2 048 Mo. Source réelle : S3 public d'OVH. Toutes les ressources de test ont été supprimées (vérifié par le script). **33 vérifications sur 33 passées.**

## Mesures (Lambda réelle, mesuré)

| Cas | Durée facturée | Mémoire max | Détail |
|---|---|---|---|
| AROME HD, à chaud (3 essais) | 6,27 s (6,16–6,36) | 617 Mo | téléchargement 1,0–1,1 s, décodage 2,1 s, tuilage 2,5 s, écriture 0,33 s |
| AROME HD, à froid (3 essais) | 7,13 s (6,26–7,88) | 616 Mo | init 28–33 ms |
| AROME, à chaud (2 essais) | 3,31 s | 198 Mo | téléchargement 1,1 s, décodage 0,3 s, tuilage 1,3 s, écriture 0,55 s |
| AROME, à froid (1 essai) | 3,84 s | 196 Mo | init 22 ms |
| Idempotence (`deja_fait`) | 0,17–0,28 s | 19 Mo | aucun nouvel écrit |
| Source absente (tirage futur) | 0,16 s | 21 Mo | `batchItemFailures`, classe transitoire, aucun état écrit |

- Débit de téléchargement OVH → Lambda : 37 à 85 Mo/s.
- Le démarrage à froid est négligeable (init ≈ 30 ms) ; l'écart froid/chaud tient surtout à la dispersion des mesures.
- Sorties conformes : 8 PMTiles par modèle (en-tête v3, ouverture par la bibliothèque `pmtiles`), état écrit en dernier, en-têtes `immutable` / `no-cache`, GeoJSON des isobares (92,7 Ko non compressé) sous `v1/isobares/arome/…`.

## Ce que ça valide

- Le **TLS en Rust pur** (`rustls-rustcrypto`) fonctionne contre S3 et OVH ; le binaire arm64 musl s'exécute ; la signature SigV4 est acceptée ; la politique IAM (avec `v1/isobares/*`) suffit.
- Le cas 404 est bien classé transitoire (rejeu SQS).

## Ce que ça ne valide pas

- SQS réel (protocole JSON, `ReportBatchItemFailures`, visibilité), le poller, la Lambda manifeste, les notifications S3, CloudFront : non exercés (invocation directe de la Lambda).
- Une seule échéance (12) par modèle : les autres échéances (dont celles qui lisent n−1) peuvent différer.
- Pas de test de charge (104 invocations simultanées).

## Conséquence sur le coût (calculé)

Pour un tirage complet, en supposant toutes les échéances proches de l'échéance 12 : (6,27 + 3,31) s × 52 ≈ **498 s facturées ≈ 860 Go-s** (1,727 Go).

| Rythme | Go-s par mois | Part du palier gratuit Lambda (400 000 Go-s, non vérifié pour ce compte) |
|---|---|---|
| 2 tirages par jour | ≈ 52 000 | 13 % |
| 8 tirages par jour | ≈ 206 000 | 52 % |

À ajouter : ≈ 7 000 Go-s par mois pour le poller et le manifeste (estimation de [architecture-aws.md](../architecture-aws.md) §10). Sans palier gratuit, au prix arm64 utilisé dans ce document d'architecture (non revérifié) : ≈ 0,7 $ par mois à 2 tirages par jour, ≈ 2,8 $ à 8.

Écart avec l'estimation précédente (151 s de CPU par tirage, ≈ 260 Go-s) : la Lambda à 1 769 Mo (≈ 1 vCPU) est environ 2,5 fois plus lente que le poste de développement à 20 cœurs, et la durée facturée inclut téléchargement et écriture S3. L'estimation initiale était donc **optimiste d'un facteur ≈ 3,3**. Elle reste très inférieure à l'estimation Python (≈ 4 240 Go-s par tirage).

Résultats bruts (hors dépôt) : `out/test-lambda/rw-test-20261018-184636.json`.
