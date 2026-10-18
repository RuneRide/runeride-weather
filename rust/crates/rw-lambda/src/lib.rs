// SPDX-License-Identifier: EUPL-1.2
//! Fonction Lambda « worker » : un message SQS = une échéance (modèle, tirage) → PMTiles dans S3.
//!
//! Voir `docs/poc-rust/lambda.md`. Le binaire `bootstrap` (runtime Lambda `provided.al2023`) est dans `src/bin`.

pub mod erreur;
pub mod handler;
pub mod http;
pub mod message;
pub mod s3;
pub mod sigv4;
pub mod source;
pub mod stockage;
pub mod temps;
pub mod traitement;
