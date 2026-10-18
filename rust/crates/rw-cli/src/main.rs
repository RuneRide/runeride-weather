// SPDX-License-Identifier: EUPL-1.2
//! Binaire `rw` : une invocation = une échéance d'un modèle (unité de travail d'une fonction Lambda).
//!
//! ```text
//! rw ingest --model arome-hd --run 2026-10-17T03:00:00Z --step 12 --src out/grib --out out/rust/canon
//! rw tile   --canon out/rust/canon/arome-hd/2026-10-17T03Z/12.npz --out out/rust/tiles
//! rw run    --model arome --run 2026-10-17T06:00:00Z --step 24 --src out/grib --out out/rust/tiles
//! ```
//! Les mesures sont écrites en JSON sur la sortie standard.

use anyhow::{bail, Context, Result};
use rw_cli::{canon, npz};
use rw_tiles::{Canon, Options, Profil, WebpBackend};
use serde_json::json;
use std::path::PathBuf;
use std::time::Instant;

#[derive(Default)]
struct Args {
    model: String,
    run: String,
    step: Option<u32>,
    src: Vec<PathBuf>,
    out: Option<PathBuf>,
    canon: Vec<PathBuf>,
    canon_out: Option<PathBuf>,
    threads: Option<usize>,
    decode_all: bool,
    npz_level: Option<Option<i64>>, // None = défaut (6) ; Some(None) = stocké
    profile: String,
    webp: Option<String>,
    webp_method: Option<i32>,
    aec: Option<String>,
}

fn parse(mut it: impl Iterator<Item = String>) -> Result<Args> {
    let mut a = Args { profile: "reduit2".into(), ..Default::default() };
    while let Some(k) = it.next() {
        let mut val = |nom: &str| it.next().with_context(|| format!("{nom} attend une valeur"));
        match k.as_str() {
            "--model" => a.model = val("--model")?,
            "--run" => a.run = val("--run")?,
            "--step" => a.step = Some(val("--step")?.parse()?),
            "--src" => a.src.push(val("--src")?.into()),
            "--out" => a.out = Some(val("--out")?.into()),
            "--canon" => a.canon.push(val("--canon")?.into()),
            "--canon-out" => a.canon_out = Some(val("--canon-out")?.into()),
            "--threads" => a.threads = Some(val("--threads")?.parse()?),
            "--decode-all" => a.decode_all = true,
            "--npz-level" => {
                let v = val("--npz-level")?;
                a.npz_level = Some(if v == "none" { None } else { Some(v.parse()?) });
            }
            "--profile" => a.profile = val("--profile")?,
            "--webp" => a.webp = Some(val("--webp")?),
            "--webp-method" => a.webp_method = Some(val("--webp-method")?.parse()?),
            "--aec" => a.aec = Some(val("--aec")?),
            autre => bail!("option inconnue : {autre}"),
        }
    }
    Ok(a)
}

fn tirage_court(run: &str) -> String {
    if run.len() > 13 { format!("{}Z", &run[..13]) } else { run.to_string() }
}

fn options_tuilage(a: &Args) -> Result<Options> {
    let profil = Profil::par_nom(&a.profile).with_context(|| format!("profil inconnu : {}", a.profile))?;
    let mut o = Options::nouveau(profil);
    if let Some(w) = &a.webp {
        o.backend = w.parse::<WebpBackend>().map_err(anyhow::Error::msg)?;
    }
    o.methode_scalaire = a.webp_method;
    Ok(o)
}

fn options_ingestion(a: &Args) -> Result<canon::Options> {
    let backend = match &a.aec {
        Some(s) => s.parse().map_err(anyhow::Error::msg)?,
        None => Default::default(),
    };
    Ok(canon::Options { tout_decoder: a.decode_all, backend })
}

fn ecrire_canon(c: &Canon, sources: &[String], dossier: &std::path::Path, niveau: Option<Option<i64>>) -> Result<(PathBuf, f64, u64)> {
    let t = Instant::now();
    let chemin = dossier.join(&c.modele).join(c.tirage_court()).join(format!("{:02}.npz", c.echeance));
    std::fs::create_dir_all(chemin.parent().unwrap())?;
    let tmp = chemin.with_extension("tmp.npz");
    let meta = npz::meta_json(c, sources, "ingestion rw (Rust) ; gust10 HD = hypot(max_10efg, max_10nfg) ; cloud_total = recouvrement aléatoire ; 9999 -> NaN");
    npz::ecrire(std::io::BufWriter::new(std::fs::File::create(&tmp)?), c, &meta, niveau.unwrap_or(Some(6)))?;
    std::fs::rename(&tmp, &chemin)?;
    let taille = std::fs::metadata(&chemin)?.len();
    Ok((chemin, t.elapsed().as_secs_f64(), taille))
}

fn tuiler(c: &Canon, a: &Args, dossier: &std::path::Path) -> Result<serde_json::Value> {
    let t = Instant::now();
    let (fichiers, m) = rw_tiles::construire(c, &options_tuilage(a)?)?;
    let tc = t.elapsed().as_secs_f64();
    let t = Instant::now();
    let mut liste = Vec::new();
    for f in &fichiers {
        let chemin = dossier.join(&f.chemin);
        std::fs::create_dir_all(chemin.parent().unwrap())?;
        let tmp = chemin.with_extension("pmtiles.tmp");
        std::fs::write(&tmp, &f.octets)?;
        std::fs::rename(&tmp, &chemin)?;
        liste.push(json!({"variable": f.variable, "chemin": f.chemin, "octets": f.octets.len(), "tuiles": f.tuiles, "pixels_ecretes": f.pixels_ecretes}));
    }
    Ok(json!({
        "tuilage_s": tc, "dont_preparation_s": m.preparation_s, "dont_rechantillonnage_encodage_s": m.rechantillonnage_encodage_s,
        "dont_assemblage_pmtiles_s": m.ecriture_pmtiles_s, "ecriture_fichiers_s": t.elapsed().as_secs_f64(),
        "octets_total": fichiers.iter().map(|f| f.octets.len()).sum::<usize>(), "fichiers": liste,
    }))
}

fn main() -> Result<()> {
    let mut args = std::env::args().skip(1);
    let cmd = args.next().unwrap_or_default();
    let a = parse(args)?;
    if let Some(n) = a.threads {
        rayon::ThreadPoolBuilder::new().num_threads(n).build_global()?;
    }
    let debut = Instant::now();
    let rapport = match cmd.as_str() {
        "ingest" | "run" => {
            let step = a.step.context("--step requis")?;
            let (c, sources, et) = canon::ingerer(&a.model, &a.run, step, &a.src, &options_ingestion(&a)?)?;
            let mut r = json!({
                "commande": cmd, "modele": a.model, "tirage": tirage_court(&a.run), "echeance": step,
                "threads": rayon::current_num_threads(),
                "ingestion": {
                    "lecture_fichiers_s": et.lecture_fichiers_s, "scan_s": et.scan_s, "decodage_grib_s": et.decodage_grib_s,
                    "calcul_canonique_s": et.calcul_canonique_s, "champs_decodes": et.champs_decodes,
                    "variables": c.variables.iter().map(|(k, _)| k.clone()).collect::<Vec<_>>(),
                },
            });
            if cmd == "ingest" {
                let out = a.out.clone().context("--out requis")?;
                let (chemin, dt, taille) = ecrire_canon(&c, &sources, &out, a.npz_level)?;
                r["ingestion"]["ecriture_npz_s"] = json!(dt);
                r["ingestion"]["npz"] = json!({"chemin": chemin, "octets": taille});
            } else {
                if let Some(d) = &a.canon_out {
                    let (chemin, dt, taille) = ecrire_canon(&c, &sources, d, a.npz_level)?;
                    r["ingestion"]["ecriture_npz_s"] = json!(dt);
                    r["ingestion"]["npz"] = json!({"chemin": chemin, "octets": taille});
                }
                r["tuilage"] = tuiler(&c, &a, &a.out.clone().context("--out requis")?)?;
            }
            r
        }
        "tile" => {
            let mut sorties = Vec::new();
            for f in &a.canon {
                let t = Instant::now();
                let (c, _) = npz::lire(std::io::BufReader::new(std::fs::File::open(f)?)).with_context(|| f.display().to_string())?;
                let lecture = t.elapsed().as_secs_f64();
                let mut r = tuiler(&c, &a, &a.out.clone().context("--out requis")?)?;
                r["lecture_npz_s"] = json!(lecture);
                sorties.push(r);
            }
            json!({"commande": "tile", "threads": rayon::current_num_threads(), "resultats": sorties})
        }
        "version" => json!({"version": env!("CARGO_PKG_VERSION"), "webp_c": cfg!(feature = "webp-c"), "webp_pure": cfg!(feature = "webp-pure")}),
        _ => bail!("commande : ingest | tile | run | version"),
    };
    let mut rapport = rapport;
    rapport["duree_totale_s"] = json!(debut.elapsed().as_secs_f64());
    println!("{}", serde_json::to_string(&rapport)?);
    Ok(())
}
