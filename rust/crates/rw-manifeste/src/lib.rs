// SPDX-License-Identifier: EUPL-1.2
//! Fonction Lambda « manifeste » : agrège les états d'échéance du bucket de données en `manifest.json`.
//!
//! Déclenchée par les notifications S3 (`_etat/*.json`) via SQS, concurrence réservée 1. Voir
//! `docs/poc-rust/poller-manifeste.md`. Le binaire `rw-manifeste` (renommé `bootstrap` dans le zip Lambda `provided.al2023`) est dans `src/bin`.

pub mod construire;
pub mod domaine;
pub mod etat;
pub mod manifeste;
