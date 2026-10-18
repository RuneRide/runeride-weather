// SPDX-License-Identifier: EUPL-1.2
//! Banc et outil : calcule les isobares d'un ou plusieurs canoniques `.npz` (écrits par le Python ou par rw-lambda).
//!
//! Usage : rw-isobares --canon <dossier ou fichier.npz>... --out <dossier> [--echeances 0,12] [--repetitions N] [--fils N]
//! Écrit `<out>/NN.geojson` (non compressé, comparable au Python) et `<out>/NN.geojson.gz`, puis un résumé JSON
//! (temps par échéance, octets) sur la sortie standard.

use anyhow::{bail, Context, Result};
use rw_isobares::{produire, Entete, Params};
use std::path::PathBuf;
use std::time::Instant;

fn main() -> Result<()> {
    let mut canon = PathBuf::new();
    let mut out = PathBuf::new();
    let mut ech: Option<Vec<u32>> = None;
    let mut reps = 1usize;
    let mut fils: Option<usize> = None;
    let mut p = Params::default();
    let mut ecrire = true;
    let mut a = std::env::args().skip(1);
    while let Some(x) = a.next() {
        let mut v = || a.next().context("valeur manquante");
        match x.as_str() {
            "--canon" => canon = v()?.into(),
            "--out" => out = v()?.into(),
            "--echeances" => ech = Some(v()?.split(',').map(|s| s.trim().parse()).collect::<Result<_, _>>()?),
            "--repetitions" => reps = v()?.parse()?,
            "--fils" => fils = Some(v()?.parse()?),
            "--sigma" => p.sigma = v()?.parse()?,
            "--tol-km" => p.tol_km = v()?.parse()?,
            "--sans-ecriture" => ecrire = false,
            _ => bail!("option inconnue : {x}"),
        }
    }
    let mut fichiers: Vec<(u32, PathBuf)> = Vec::new();
    for e in std::fs::read_dir(&canon).with_context(|| canon.display().to_string())? {
        let e = e?.path();
        if e.extension().and_then(|s| s.to_str()) == Some("npz") {
            if let Some(n) = e.file_stem().and_then(|s| s.to_str()).and_then(|s| s.parse::<u32>().ok()) {
                if ech.as_ref().map_or(true, |l| l.contains(&n)) {
                    fichiers.push((n, e));
                }
            }
        }
    }
    fichiers.sort();
    if ecrire {
        std::fs::create_dir_all(&out)?;
    }
    let pool = {
        let mut b = rayon::ThreadPoolBuilder::new();
        if let Some(n) = fils {
            b = b.num_threads(n);
        }
        b.build()?
    };
    // lecture hors mesure
    let mut charges = Vec::new();
    for (n, f) in &fichiers {
        let (c, meta) = rw_cli::npz::lire(std::io::BufReader::new(std::fs::File::open(f)?))?;
        let m = c.variable("mslp").with_context(|| format!("pas de mslp dans {}", f.display()))?.to_vec();
        charges.push((*n, c, meta, m));
    }
    let mut par_ech = Vec::new();
    let debut = Instant::now();
    for (n, c, meta, m) in &charges {
        let e = Entete { modele: c.modele.clone(), tirage: c.tirage.clone(), echeance: c.echeance, };
        let _ = meta;
        let mut meilleur = f64::INFINITY;
        let mut dernier = None;
        for _ in 0..reps {
            let t = Instant::now();
            let r = pool.install(|| produire(&c.lat, &c.lon, m, &e, &p))?;
            meilleur = meilleur.min(t.elapsed().as_secs_f64());
            dernier = Some(r);
        }
        let r = dernier.unwrap();
        if ecrire {
            std::fs::write(out.join(format!("{n:02}.geojson")), &r.geojson)?;
            std::fs::write(out.join(format!("{n:02}.geojson.gz")), &r.gzip)?;
        }
        par_ech.push(serde_json::json!({
            "echeance": n, "t_s": meilleur, "octets": r.geojson.len(), "octets_gzip": r.gzip.len(),
            "lignes": r.stats.lignes, "sommets": r.stats.sommets, "extrema": r.stats.extrema,
            "t_lissage_s": r.stats.t_lissage_s, "t_contours_s": r.stats.t_contours_s, "t_extrema_s": r.stats.t_extrema_s,
        }));
    }
    let total: f64 = par_ech.iter().map(|v| v["t_s"].as_f64().unwrap()).sum();
    println!(
        "{}",
        serde_json::json!({"echeances": par_ech.len(), "fils": pool.current_num_threads(), "repetitions": reps,
            "somme_t_s": total, "mural_s": debut.elapsed().as_secs_f64() / reps as f64, "par_echeance": par_ech})
    );
    Ok(())
}
