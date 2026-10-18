// SPDX-License-Identifier: EUPL-1.2
//! Arborescence et nommage du S3 public de Météo-France chez OVH (miroir de `pipeline/ingest/download.py` et de
//! `rw_cli::canon`, dont un test de ce crate vérifie l'accord).
//!
//! `pnt/<tirage>/arome/<0025|001>/<paquet>/arome__<res>__<paquet>__<étiquette>__<tirage>.grib2`, avec
//! `<tirage>` = `2026-10-17T03:00:00Z`. Étiquette : `NNH` en 0,01° (un fichier par heure) ; en 0,025° un groupe de six
//! échéances (`00H06H`, `07H12H`, …, `49H51H`).

use std::collections::BTreeSet;

/// Paquets nécessaires aux huit variables (SP1 et SP2, comme le worker).
pub const PAQUETS: [&str; 2] = ["SP1", "SP2"];
pub const ECHEANCE_MAX: u32 = 51;

pub fn resolution(modele: &str) -> Option<&'static str> {
    match modele {
        "arome-hd" => Some("001"),
        "arome" => Some("0025"),
        _ => None,
    }
}

/// Étiquette du groupe 0,025° contenant l'échéance (`00H06H`, `07H12H`, …, `49H51H`).
pub fn groupe_025(step: u32) -> String {
    if step <= 6 {
        return "00H06H".into();
    }
    let debut = 7 + 6 * ((step - 7) / 6);
    let fin = (debut + 5).min(ECHEANCE_MAX);
    format!("{debut:02}H{fin:02}H")
}

pub fn etiquette(modele: &str, step: u32) -> String {
    if modele == "arome-hd" { format!("{step:02}H") } else { groupe_025(step) }
}

/// Étiquettes à lire pour l'échéance `step` : la sienne et, dès l'échéance 2, celle de l'échéance précédente
/// (dé-cumul des précipitations ; à l'échéance 1 le cumul précédent est nul par construction).
pub fn etiquettes_requises(modele: &str, step: u32) -> Vec<String> {
    let n = etiquette(modele, step);
    let mut v = vec![n.clone()];
    if step >= 2 {
        let p = etiquette(modele, step - 1);
        if p != n {
            v.push(p);
        }
    }
    v
}

/// Étiquettes distinctes d'un tirage complet (52 en HD, 9 en 0,025°).
pub fn etiquettes_tirage(modele: &str) -> BTreeSet<String> {
    (0..=ECHEANCE_MAX).map(|s| etiquette(modele, s)).collect()
}

pub fn nom_fichier(modele: &str, paquet: &str, etiquette: &str, tirage_long: &str) -> String {
    format!("arome__{}__{paquet}__{etiquette}__{tirage_long}.grib2", resolution(modele).unwrap_or("?"))
}

/// Préfixe de listage d'un paquet : `pnt/2026-10-17T03:00:00Z/arome/001/SP1/`.
pub fn prefixe_liste(racine: &str, modele: &str, tirage_long: &str, paquet: &str) -> String {
    format!("{}/{tirage_long}/arome/{}/{paquet}/", racine.trim_matches('/'), resolution(modele).unwrap_or("?"))
}

/// Fichiers GRIB (nom, paquet, étiquette) requis par l'échéance.
pub fn fichiers_requis(modele: &str, tirage_long: &str, step: u32) -> Vec<(String, &'static str, String)> {
    let mut v = Vec::new();
    for l in etiquettes_requises(modele, step) {
        for p in PAQUETS {
            v.push((nom_fichier(modele, p, &l, tirage_long), p, l.clone()));
        }
    }
    v
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn groupes() {
        let g: Vec<String> = [0, 6, 7, 12, 13, 48, 49, 51].iter().map(|&s| groupe_025(s)).collect();
        assert_eq!(g, ["00H06H", "00H06H", "07H12H", "07H12H", "13H18H", "43H48H", "49H51H", "49H51H"]);
        assert_eq!(etiquettes_tirage("arome").len(), 9);
        assert_eq!(etiquettes_tirage("arome-hd").len(), 52);
    }

    #[test]
    fn requis() {
        assert_eq!(etiquettes_requises("arome-hd", 0), ["00H"]);
        assert_eq!(etiquettes_requises("arome-hd", 1), ["01H"]);
        assert_eq!(etiquettes_requises("arome-hd", 12), ["12H", "11H"]);
        assert_eq!(etiquettes_requises("arome", 12), ["07H12H"]);
        assert_eq!(etiquettes_requises("arome", 7), ["07H12H", "00H06H"]);
        assert_eq!(etiquettes_requises("arome", 1), ["00H06H"]);
    }

    #[test]
    fn prefixe_et_nom() {
        assert_eq!(prefixe_liste("pnt", "arome-hd", "2026-10-17T06:00:00Z", "SP1"), "pnt/2026-10-17T06:00:00Z/arome/001/SP1/");
        assert_eq!(nom_fichier("arome", "SP2", "07H12H", "2026-10-17T06:00:00Z"), "arome__0025__SP2__07H12H__2026-10-17T06:00:00Z.grib2");
    }

    /// Les noms attendus par le worker (`rw_cli::canon::noms_requis`) sont ceux que le poller attend, pour tous les cas.
    #[test]
    fn accord_avec_le_worker() {
        let tirage = "2026-10-17T06:00:00Z";
        for modele in ["arome", "arome-hd"] {
            for step in 0..=ECHEANCE_MAX {
                let mut worker = rw_cli::canon::noms_requis(modele, tirage, step).unwrap();
                let mut poller: Vec<String> = fichiers_requis(modele, tirage, step).into_iter().map(|(n, ..)| n).collect();
                worker.sort();
                poller.sort();
                assert_eq!(worker, poller, "{modele} échéance {step}");
            }
        }
    }
}
