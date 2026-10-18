// SPDX-License-Identifier: EUPL-1.2
//! Profils de tuilage (port de `profiles.py`, famille `reduit2`).

use crate::encode::NIVEAUX;

/// Paramètres d'un profil de poids. Seule la famille `reduit2` (vent `wind8`, bordure 1 px) est portée.
#[derive(Debug, Clone)]
pub struct Profil {
    pub nom: String,
    pub vent_vmax: f64,
    /// variable → zoom maximal stocké
    pub zmax: Vec<(&'static str, u32)>,
    /// variable → nombre d'intervalles (254 par défaut)
    pub niveaux: Vec<(&'static str, u32)>,
    /// variable → plage (min, max) resserrée
    pub plages: Vec<(&'static str, (f64, f64))>,
}

impl Profil {
    /// `reduit2` (t0 = 243,15 K), `reduit2-ete` (258,15), `reduit2-hiver` (233,15).
    pub fn par_nom(nom: &str) -> Option<Self> {
        let t0 = match nom {
            "reduit2" => 243.15,
            "reduit2-ete" => 258.15,
            "reduit2-hiver" => 233.15,
            _ => return None,
        };
        Some(Self {
            nom: nom.to_string(),
            vent_vmax: 48.0,
            zmax: ["t2m", "gust10", "rh2m", "cloud_total", "cape", "wind10"].iter().map(|&v| (v, 7)).collect(),
            niveaux: vec![("rh2m", 100), ("cloud_total", 100), ("gust10", 254), ("t2m", 254)],
            plages: vec![("t2m", (t0, t0 + 63.5)), ("gust10", (0.0, 63.5))],
        })
    }

    pub fn zoom_max(&self, var: &str, defaut: u32) -> u32 {
        self.zmax.iter().find(|(v, _)| *v == var).map_or(defaut, |&(_, z)| z.min(defaut))
    }

    pub fn niveaux(&self, var: &str) -> u32 {
        self.niveaux.iter().find(|(v, _)| *v == var).map_or(NIVEAUX, |&(_, n)| n)
    }

    pub fn plage(&self, var: &str) -> Option<(f64, f64)> {
        self.plages.iter().find(|(v, _)| *v == var).map(|&(_, p)| p)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reduit2() {
        let p = Profil::par_nom("reduit2").unwrap();
        assert_eq!(p.plage("t2m"), Some((243.15, 243.15 + 63.5)));
        assert_eq!(p.zoom_max("t2m", 8), 7);
        assert_eq!(p.zoom_max("precip_rate", 8), 8);
        assert_eq!(p.niveaux("rh2m"), 100);
        assert_eq!(p.niveaux("cape"), 254);
        assert!(Profil::par_nom("contrat").is_none());
    }
}
