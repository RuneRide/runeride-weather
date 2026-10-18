// SPDX-License-Identifier: EUPL-1.2
//! Contrôle sur données réelles : champs canoniques Rust contre les sorties Python existantes
//! (`out/canon`). Ignoré si les GRIB ou les références ne sont pas présents.

use rw_cli::{canon, npz};
use std::path::{Path, PathBuf};

fn racine() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..")
}

fn comparer(modele: &str, tirage: &str, dossier_tirage: &str, step: u32, dossier_grib: &str) {
    let r = racine();
    let reference = r.join(format!("out/canon/{modele}/{dossier_tirage}/{step:02}.npz"));
    let grib = r.join(dossier_grib);
    if !reference.exists() || !grib.exists() {
        eprintln!("références absentes, test ignoré ({modele} +{step})");
        return;
    }
    let (c, _, _) = canon::ingerer(modele, tirage, step, &[grib], &canon::Options { tout_decoder: false, backend: Default::default() }).unwrap();
    let (ref_c, _) = npz::lire(std::io::BufReader::new(std::fs::File::open(reference).unwrap())).unwrap();
    assert_eq!(c.lat, ref_c.lat);
    assert_eq!(c.lon, ref_c.lon);
    let mut noms: Vec<_> = c.variables.iter().map(|(k, _)| k.clone()).collect();
    let mut noms_ref: Vec<_> = ref_c.variables.iter().map(|(k, _)| k.clone()).collect();
    noms.sort();
    noms_ref.sort();
    assert_eq!(noms, noms_ref);
    for (nom, v) in &c.variables {
        let w = ref_c.variable(nom).unwrap();
        assert_eq!(v.len(), w.len());
        for (i, (&a, &b)) in v.iter().zip(w).enumerate() {
            if a.is_nan() || b.is_nan() {
                assert!(a.is_nan() && b.is_nan(), "{nom}[{i}] : NaN différents ({a} contre {b})");
            } else {
                let rel = ((a - b).abs() / b.abs().max(1e-3)) as f64;
                assert!(rel <= 1e-4, "{nom}[{i}] : {a} contre {b}");
            }
        }
    }
}

#[test]
fn hd_echeance_12_contre_python() {
    comparer("arome-hd", "2026-10-17T03:00:00Z", "2026-10-17T03Z", 12, "out/grib/arome-hd");
}

#[test]
fn arome_echeance_24_contre_python() {
    comparer("arome", "2026-10-17T06:00:00Z", "2026-10-17T06Z", 24, "out/grib/arome");
}
