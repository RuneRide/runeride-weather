// SPDX-License-Identifier: EUPL-1.2
//! Briques AWS minimales, sans le SDK AWS, partagées par `rw-lambda`, `rw-poller` et `rw-manifeste`.
//!
//! * [`sigv4`] : signature AWS SigV4 ;
//! * [`s3`] : client S3 signé (PutObject, GetObject, HeadObject, ListObjectsV2, DeleteObject) ;
//! * [`sqs`] : client SQS signé (SendMessageBatch, protocole JSON) ;
//! * [`liste`] : listage S3 public non signé (source Météo-France chez OVH) et lecture du XML de listage ;
//! * [`stockage`] : trait de stockage d'objets (S3 ou disque) ;
//! * [`http`], [`temps`], [`erreur`] : agent HTTP (rustls en Rust pur), dates UTC, erreurs.
//!
//! Voir `docs/poc-rust/lambda.md` (choix et mesures) et `docs/poc-rust/poller-manifeste.md`.

pub mod erreur;
pub mod http;
pub mod liste;
pub mod s3;
pub mod sigv4;
pub mod sqs;
pub mod stockage;
pub mod temps;
