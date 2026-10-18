// SPDX-License-Identifier: EUPL-1.2
//! Client HTTP(S) commun à la source GRIB et à S3 : `ureq` + rustls avec cryptographie en Rust pur.

use std::sync::Arc;
use std::time::Duration;

pub const AGENT_UTILISATEUR: &str = concat!("runeride-weather-rust/", env!("CARGO_PKG_VERSION"), " (ingestion; contact: philippegeek@gmail.com)");

#[derive(Debug, Clone, Copy)]
pub struct Delais {
    pub connexion: Duration,
    pub reponse: Duration,
    /// Durée maximale d'une requête entière (corps compris).
    pub global: Duration,
}

impl Default for Delais {
    fn default() -> Self {
        Self { connexion: Duration::from_secs(10), reponse: Duration::from_secs(30), global: Duration::from_secs(240) }
    }
}

pub fn agent(delais: Delais, agent_utilisateur: &str) -> ureq::Agent {
    let tls = ureq::tls::TlsConfig::builder()
        .provider(ureq::tls::TlsProvider::Rustls)
        .unversioned_rustls_crypto_provider(Arc::new(rustls_rustcrypto::provider()))
        .build();
    ureq::Agent::config_builder()
        .tls_config(tls)
        .user_agent(agent_utilisateur)
        .http_status_as_error(false)
        .timeout_connect(Some(delais.connexion))
        .timeout_recv_response(Some(delais.reponse))
        .timeout_global(Some(delais.global))
        .max_idle_connections_per_host(4)
        .build()
        .into()
}

/// Attente avant le nouvel essai `essai` (0, 1, 2…) : `base`, 2×`base`, 4×`base`…, plafonnée à 30 s.
pub fn pause(base: Duration, essai: u32) -> Duration {
    (base * 2u32.saturating_pow(essai)).min(Duration::from_secs(30))
}
