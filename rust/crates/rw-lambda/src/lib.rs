// SPDX-License-Identifier: EUPL-1.2
//! Fonction Lambda « worker » : un message SQS = une échéance (modèle, tirage) → PMTiles dans S3.
//!
//! Voir `docs/poc-rust/lambda.md`. Le binaire `bootstrap` (runtime Lambda `provided.al2023`) est dans `src/bin`.

pub mod erreur;
pub mod handler;
pub mod message;
pub mod source;
pub mod traitement;

// Modules déplacés dans `rw-aws` : mêmes chemins qu'avant (`rw_lambda::s3`, `::stockage`, …).
pub use rw_aws::{http, s3, sigv4, stockage, temps};
