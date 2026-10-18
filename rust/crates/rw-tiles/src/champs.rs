// SPDX-License-Identifier: EUPL-1.2
//! Champs canoniques (contrat §2) : entrée du tuilage.

/// Un fichier canonique : axes et variables `f32` de forme `[lat.len(), lon.len()]`, NaN = pas de donnée.
#[derive(Debug, Clone)]
pub struct Canon {
    pub modele: String,
    /// Tirage au format `2026-10-17T03:00:00Z`.
    pub tirage: String,
    pub echeance: u32,
    /// Latitudes décroissantes (nord vers sud).
    pub lat: Vec<f32>,
    /// Longitudes croissantes, −180..180.
    pub lon: Vec<f32>,
    /// Variables dans l'ordre de calcul.
    pub variables: Vec<(String, Vec<f32>)>,
}

impl Canon {
    pub fn variable(&self, nom: &str) -> Option<&[f32]> {
        self.variables.iter().find(|(n, _)| n == nom).map(|(_, v)| v.as_slice())
    }

    /// Dossier de tirage `2026-10-17T03Z` (comme `build.py` : 13 premiers caractères + Z).
    pub fn tirage_court(&self) -> String {
        if self.tirage.len() > 13 {
            format!("{}Z", &self.tirage[..13])
        } else {
            self.tirage.clone()
        }
    }
}
