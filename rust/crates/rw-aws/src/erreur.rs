// SPDX-License-Identifier: EUPL-1.2
//! Erreurs des clients AWS minimaux.

/// Échec d'accès au stockage d'objets (S3 ou disque).
#[derive(Debug, thiserror::Error)]
pub enum ErreurStockage {
    #[error("stockage : {0}")]
    Io(String),
    #[error("S3 HTTP {statut} sur {cle} : {corps}")]
    Http { statut: u16, cle: String, corps: String },
    #[error("configuration S3 : {0}")]
    Configuration(String),
}

/// Échec d'un appel SQS.
#[derive(Debug, thiserror::Error)]
pub enum ErreurSqs {
    #[error("SQS : {0}")]
    Io(String),
    #[error("SQS HTTP {statut} : {corps}")]
    Http { statut: u16, corps: String },
    #[error("configuration SQS : {0}")]
    Configuration(String),
}
