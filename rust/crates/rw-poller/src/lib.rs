// SPDX-License-Identifier: EUPL-1.2
//! Fonction Lambda « poller » : liste le S3 public de Météo-France (OVH), compare à l'état déjà produit dans le
//! bucket de données et met en file SQS les messages `{modele, tirage, echeance}` manquants.
//!
//! Déclenchée par EventBridge Scheduler (toutes les 5 minutes). Voir `docs/poc-rust/poller-manifeste.md`.
//! Le binaire `rw-poller` (renommé `bootstrap` dans le zip Lambda `provided.al2023`) est dans `src/bin`.

pub mod config;
pub mod noms;
pub mod plan;
pub mod poller;
