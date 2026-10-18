// SPDX-License-Identifier: EUPL-1.2
//! Message SQS : `{"modele": "arome-hd", "tirage": "2026-10-17T03:00:00Z", "echeance": 12}`.
//!
//! Le tirage accepte aussi la forme courte `2026-10-17T03Z`. Tout est validé strictement (ces valeurs
//! entrent dans des URL et des clés S3) : pas de `/`, pas de `..`, pas d'espace.

use crate::temps;
use serde::{Deserialize, Serialize};

pub const MODELES: [&str; 2] = ["arome", "arome-hd"];
pub const ECHEANCE_MAX: u32 = 51;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Travail {
    pub modele: String,
    pub tirage: String,
    pub echeance: u32,
}

/// Travail validé et normalisé.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Valide {
    pub modele: String,
    /// `2026-10-17T03:00:00Z` (noms de fichiers GRIB, URL de la source).
    pub tirage_long: String,
    /// `2026-10-17T03Z` (clés de sortie, comme `pipeline/manifest`).
    pub tirage_court: String,
    pub echeance: u32,
    /// Secondes Unix du tirage.
    pub epoch_tirage: i64,
}

impl Valide {
    /// Instant valide (tirage + échéance), ISO UTC.
    pub fn valide_iso(&self) -> String {
        temps::iso(self.epoch_tirage + 3600 * i64::from(self.echeance))
    }
}

fn chiffres(s: &str, n: usize) -> Option<i64> {
    (s.len() == n && s.bytes().all(|b| b.is_ascii_digit())).then(|| s.parse().ok()).flatten()
}

impl Travail {
    pub fn valider(&self) -> Result<Valide, String> {
        if !MODELES.contains(&self.modele.as_str()) {
            return Err(format!("modèle inconnu : {:?} (attendu : arome ou arome-hd)", self.modele));
        }
        if self.echeance > ECHEANCE_MAX {
            return Err(format!("échéance hors contrat : {} (0 à {ECHEANCE_MAX})", self.echeance));
        }
        let t = self.tirage.as_str();
        let b = t.as_bytes();
        let ok_forme = t.is_ascii()
            && ((t.len() == 20 && b[4] == b'-' && b[7] == b'-' && b[10] == b'T' && b[13] == b':' && b[16] == b':' && &t[17..] == "00Z")
            && &t[14..16] == "00"
                || (t.len() == 14 && b[4] == b'-' && b[7] == b'-' && b[10] == b'T' && &t[13..] == "Z"));
        if !ok_forme {
            return Err(format!("tirage mal formé : {t:?} (2026-10-17T03:00:00Z ou 2026-10-17T03Z)"));
        }
        let (a, m, j, h) = (chiffres(&t[0..4], 4), chiffres(&t[5..7], 2), chiffres(&t[8..10], 2), chiffres(&t[11..13], 2));
        let (Some(a), Some(m), Some(j), Some(h)) = (a, m, j, h) else {
            return Err(format!("tirage mal formé : {t:?}"));
        };
        if !(2000..=2100).contains(&a) || !(1..=12).contains(&m) || !(1..=31).contains(&j) || h > 23 {
            return Err(format!("tirage hors limites : {t:?}"));
        }
        let e = temps::epoch(a, m, j, h, 0, 0);
        // refuse le 31 avril, le 30 février, etc.
        let (a2, m2, j2, ..) = temps::decomposer(e);
        if (a2, m2, j2) != (a, m, j) {
            return Err(format!("date inexistante : {t:?}"));
        }
        Ok(Valide {
            modele: self.modele.clone(),
            tirage_long: format!("{a:04}-{m:02}-{j:02}T{h:02}:00:00Z"),
            tirage_court: format!("{a:04}-{m:02}-{j:02}T{h:02}Z"),
            echeance: self.echeance,
            epoch_tirage: e,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn t(m: &str, r: &str, e: u32) -> Travail {
        Travail { modele: m.into(), tirage: r.into(), echeance: e }
    }

    #[test]
    fn formes_acceptees() {
        let v = t("arome-hd", "2026-10-17T03:00:00Z", 12).valider().unwrap();
        assert_eq!((v.tirage_long.as_str(), v.tirage_court.as_str()), ("2026-10-17T03:00:00Z", "2026-10-17T03Z"));
        assert_eq!(v.valide_iso(), "2026-10-17T15:00:00Z");
        assert_eq!(t("arome", "2026-10-17T06Z", 0).valider().unwrap().tirage_long, "2026-10-17T06:00:00Z");
    }

    #[test]
    fn refus() {
        for (m, r, e) in [
            ("gfs", "2026-10-17T03Z", 1),
            ("arome", "2026-10-17T03Z", 52),
            ("arome", "../../etc/passwd", 1),
            ("arome", "2026-10-17T24Z", 1),
            ("arome", "2026-10-17T03:30:00Z", 1),
            ("arome", "2026-02-30T03Z", 1),
            ("arome", "2026-10-17T03:00:00Z/x", 1),
            ("arome", "２０２６-10-17T03Z", 1),
        ] {
            assert!(t(m, r, e).valider().is_err(), "{m} {r} {e}");
        }
    }

    #[test]
    fn json() {
        let w: Travail = serde_json::from_str(r#"{"modele":"arome","tirage":"2026-10-17T06:00:00Z","echeance":7}"#).unwrap();
        assert_eq!(w.echeance, 7);
        assert!(serde_json::from_str::<Travail>(r#"{"modele":"arome","tirage":"x","echeance":7,"autre":1}"#).is_err());
        assert!(serde_json::from_str::<Travail>(r#"{"modele":"arome","tirage":"x","echeance":-1}"#).is_err());
    }
}
