// SPDX-License-Identifier: EUPL-1.2
//! Le polygone du domaine calculé en Rust égale celui du Python (`out/manifest/manifest.json`, produit par
//! `pipeline.manifest` à partir de `out/canon/<modèle>/<tirage>/03.npz`). Ignoré si ces fichiers sont absents.

use rw_manifeste::domaine::{lire_masque, polygone_depuis_masque, RAYONS};
use serde_json::Value;
use std::path::{Path, PathBuf};

fn racine() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..").canonicalize().unwrap()
}

fn compare(modele: &str, tirage: &str) {
    let canon = racine().join(format!("out/canon/{modele}/{tirage}/03.npz"));
    let manifeste = racine().join("out/manifest/manifest.json");
    if !canon.exists() || !manifeste.exists() {
        eprintln!("ignoré : {} ou {} absent", canon.display(), manifeste.display());
        return;
    }
    let m: Value = serde_json::from_slice(&std::fs::read(manifeste).unwrap()).unwrap();
    let attendu = &m["models"][modele]["domain"];
    if !attendu["source"].as_str().is_some_and(|s| s.ends_with(&format!("{tirage}/03"))) {
        eprintln!("ignoré : le manifeste de référence vient d'un autre tirage ({})", attendu["source"]);
        return;
    }
    let masque = lire_masque(&std::fs::read(&canon).unwrap()).unwrap();
    let t = std::time::Instant::now();
    let p = polygone_depuis_masque(&masque, 0.04, RAYONS).unwrap();
    eprintln!("{modele} : {} sommets en {:.2} s ({} × {})", p.len() - 1, t.elapsed().as_secs_f64(), masque.lat.len(), masque.lon.len());
    let attendu_p: Vec<[f64; 2]> = serde_json::from_value(attendu["polygon"].clone()).unwrap();
    assert_eq!(p, attendu_p, "polygone {modele} différent du Python");
    assert_eq!(attendu["vertices"].as_u64().unwrap() as usize, p.len() - 1);
}

#[test]
fn polygone_arome_identique_au_python() {
    compare("arome", "2026-10-17T06Z");
}

#[test]
fn polygone_arome_hd_identique_au_python() {
    compare("arome-hd", "2026-10-17T06Z");
}
