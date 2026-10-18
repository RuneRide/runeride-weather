// SPDX-License-Identifier: EUPL-1.2
//! Tuilage des champs canoniques en PMTiles v3 (WebP sans perte, bordure d'1 px, Web Mercator).
//!
//! Reproduit `pipeline/tiling` (Python) pour le profil `reduit2` ; voir `docs/poc-rust/synthese.md`.

pub mod build;
pub mod champs;
pub mod encode;
pub mod mercator;
pub mod pmtiles;
pub mod profil;

pub use build::{construire, Fichier, Options};
pub use champs::Canon;
pub use encode::WebpBackend;
pub use profil::Profil;
