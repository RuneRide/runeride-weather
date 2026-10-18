// SPDX-License-Identifier: EUPL-1.2
//! Classement des erreurs d'un traitement d'échéance.
//!
//! * **Transitoire** : un nouvel essai plus tard peut réussir (source non encore publiée, réseau, 5xx,
//!   limitation S3, droits ou configuration à corriger). Le message est rendu à SQS (`batchItemFailures`).
//! * **Définitive** : le message ne peut pas aboutir par simple répétition (message mal formé, modèle ou
//!   échéance hors contrat, GRIB illisible ou incohérent). Par défaut le message est acquitté et l'échec est
//!   journalisé et consigné dans l'état d'échéance (voir `Config::definitif_rejoue`).

use std::fmt;

#[derive(Debug)]
pub enum Erreur {
    Transitoire(anyhow::Error),
    Definitive(anyhow::Error),
}

impl Erreur {
    pub fn transitoire(e: impl Into<anyhow::Error>) -> Self {
        Erreur::Transitoire(e.into())
    }
    pub fn definitive(e: impl Into<anyhow::Error>) -> Self {
        Erreur::Definitive(e.into())
    }
    pub fn est_transitoire(&self) -> bool {
        matches!(self, Erreur::Transitoire(_))
    }
    pub fn classe(&self) -> &'static str {
        if self.est_transitoire() { "transitoire" } else { "definitive" }
    }
    pub fn interne(&self) -> &anyhow::Error {
        match self {
            Erreur::Transitoire(e) | Erreur::Definitive(e) => e,
        }
    }
}

impl fmt::Display for Erreur {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{} : {:#}", self.classe(), self.interne())
    }
}

/// Échec d'accès à la source GRIB.
#[derive(Debug, thiserror::Error)]
pub enum ErreurSource {
    /// 404 : le fichier n'est pas (encore) publié chez Météo-France.
    #[error("fichier non publié (HTTP 404) : {0}")]
    Absente(String),
    #[error("HTTP {statut} pour {url}")]
    Http { statut: u16, url: String },
    #[error("réseau : {0}")]
    Reseau(String),
}

/// Échec d'accès au stockage de sortie.
#[derive(Debug, thiserror::Error)]
pub enum ErreurStockage {
    #[error("stockage : {0}")]
    Io(String),
    #[error("S3 HTTP {statut} sur {cle} : {corps}")]
    Http { statut: u16, cle: String, corps: String },
    #[error("configuration S3 : {0}")]
    Configuration(String),
}
