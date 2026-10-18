// SPDX-License-Identifier: EUPL-1.2
//! Construction des PMTiles d'une échéance (port de `build.py`) ; parallélisme par tuile avec rayon.

use crate::champs::Canon;
use crate::encode::{self, Echelle, Image, WebpBackend, SCALAIRES, VENT};
use crate::mercator::{lonlat_pixels, tuiles_couvrantes, Grille, BORDURE, STOCKEE, TUILE};
use crate::pmtiles::{self, EnTete};
use crate::profil::Profil;
use rayon::prelude::*;
use serde_json::{json, Map, Value};
use std::time::Instant;

#[derive(Debug, thiserror::Error)]
pub enum TuilageError {
    #[error("modèle inconnu : {0}")]
    Modele(String),
    #[error(transparent)]
    Grille(#[from] crate::mercator::GrilleIrreguliere),
    #[error("grille et variable incohérentes : {0}")]
    Taille(String),
    #[error("encodage WebP : {0}")]
    Webp(String),
}

#[derive(Debug, Clone)]
pub struct Options {
    pub profil: Profil,
    pub backend: WebpBackend,
    /// Restreint les variables (ex. `["t2m", "wind10"]`).
    pub variables: Option<Vec<String>>,
    /// Surcharge des zooms (min, max).
    pub zooms: Option<(u32, u32)>,
    /// Effort libwebp pour les scalaires (défaut 4, comme le Python) ; le vent garde 3.
    pub methode_scalaire: Option<i32>,
}

impl Options {
    pub fn nouveau(profil: Profil) -> Self {
        Self { profil, backend: WebpBackend::default(), variables: None, zooms: None, methode_scalaire: None }
    }
}

/// Fichier PMTiles produit pour une variable.
#[derive(Debug)]
pub struct Fichier {
    pub variable: String,
    /// `<modèle>/<tirage>/<variable>/<échéance>.pmtiles`
    pub chemin: String,
    pub octets: Vec<u8>,
    pub tuiles: usize,
    pub octets_tuiles: usize,
    /// Pixels hors de la plage resserrée du profil (écrêtés), 0 si pas de plage.
    pub pixels_ecretes: u64,
}

#[derive(Debug, Default, Clone, Copy)]
pub struct Mesures {
    pub preparation_s: f64,
    pub rechantillonnage_encodage_s: f64,
    pub ecriture_pmtiles_s: f64,
}

pub fn zooms_modele(modele: &str) -> Option<(u32, u32)> {
    match modele {
        "arome-hd" => Some((5, 8)),
        "arome" => Some((3, 7)),
        _ => None,
    }
}

struct Sortie {
    nom: &'static str,
    wind: bool,
    min: f64,
    max: f64,
    echelle: Echelle,
    unite: &'static str,
    niveaux: u32,
    zmax: u32,
    plage: Option<(f64, f64)>,
}

type ResTuile = ((u32, u32, u32), Vec<Option<Vec<u8>>>, Vec<u64>);

pub fn construire(canon: &Canon, opts: &Options) -> Result<(Vec<Fichier>, Mesures), TuilageError> {
    let t0 = Instant::now();
    let (zmin, zmax) = match opts.zooms {
        Some(z) => z,
        None => zooms_modele(&canon.modele).ok_or_else(|| TuilageError::Modele(canon.modele.clone()))?,
    };
    let grille = Grille::new(&canon.lat, &canon.lon)?;
    let n = grille.nlat() * grille.nlon();
    for (nom, v) in &canon.variables {
        if v.len() != n {
            return Err(TuilageError::Taille(format!("{nom} : {} valeurs pour {} points", v.len(), n)));
        }
    }
    let veut = |nom: &str| opts.variables.as_ref().is_none_or(|l| l.iter().any(|x| x == nom));
    let p = &opts.profil;

    let mut sorties: Vec<Sortie> = Vec::new();
    for &(nom, min, max, echelle, unite) in SCALAIRES.iter() {
        if canon.variable(nom).is_some() && veut(nom) {
            let plage = p.plage(nom);
            let (min, max) = plage.unwrap_or((min, max));
            sorties.push(Sortie { nom, wind: false, min, max, echelle, unite, niveaux: p.niveaux(nom), zmax: p.zoom_max(nom, zmax), plage });
        }
    }
    if canon.variable("wind10_u").is_some() && canon.variable("wind10_v").is_some() && veut(VENT) {
        sorties.push(Sortie {
            nom: VENT,
            wind: true,
            min: -p.vent_vmax,
            max: p.vent_vmax,
            echelle: Echelle::Lineaire,
            unite: "m/s",
            niveaux: 254,
            zmax: p.zoom_max(VENT, zmax),
            plage: None,
        });
    }

    let mut travaux: Vec<(u32, u32, u32)> = Vec::new();
    let (lo0, la0, lo1, la1) = grille.emprise;
    for z in zmin..=zmax {
        for (x, y) in tuiles_couvrantes(lo0, la0, lo1, la1, z) {
            travaux.push((z, x, y));
        }
    }
    let preparation_s = t0.elapsed().as_secs_f64();

    let t1 = Instant::now();
    let resultats: Vec<ResTuile> = travaux
        .par_iter()
        .map(|&(z, x, y)| -> Result<ResTuile, TuilageError> {
            let (lon, lat) = lonlat_pixels(z, x, y, BORDURE);
            let (lignes, colonnes) = grille.poids(&lon, &lat);
            let mut ech = vec![0f32; STOCKEE * STOCKEE];
            let mut ech2 = Vec::new();
            let mut gris = vec![0u8; STOCKEE * STOCKEE];
            let mut rvb = vec![0u8; STOCKEE * STOCKEE * 3];
            let mut sortie = Vec::with_capacity(sorties.len());
            let mut ecretes = Vec::with_capacity(sorties.len());
            for s in &sorties {
                if z > s.zmax {
                    sortie.push(None);
                    ecretes.push(0);
                    continue;
                }
                let mut ecr = 0u64;
                let octets = if s.wind {
                    ech2.resize(STOCKEE * STOCKEE, 0f32);
                    grille.echantillonner(canon.variable("wind10_u").expect("u"), &lignes, &colonnes, &mut ech);
                    grille.echantillonner(canon.variable("wind10_v").expect("v"), &lignes, &colonnes, &mut ech2);
                    encode::quantifier_vent8(&ech, &ech2, p.vent_vmax, &mut rvb);
                    if rvb.iter().all(|&b| b == 0) {
                        None
                    } else {
                        Some(encode::vers_webp(&Image::Rvb(&rvb), opts.backend, 3).map_err(TuilageError::Webp)?)
                    }
                } else {
                    grille.echantillonner(canon.variable(s.nom).expect("variable"), &lignes, &colonnes, &mut ech);
                    if let Some((a, b)) = s.plage {
                        ecr = ech.iter().filter(|&&v| v < a as f32 || v > b as f32).count() as u64;
                    }
                    encode::quantifier_scalaire(&ech, s.min, s.max, s.echelle, s.niveaux, &mut gris);
                    if gris.iter().all(|&b| b == 0) {
                        None
                    } else {
                        Some(encode::vers_webp(&Image::Gris(&gris), opts.backend, opts.methode_scalaire.unwrap_or(4)).map_err(TuilageError::Webp)?)
                    }
                };
                sortie.push(octets);
                ecretes.push(ecr);
            }
            Ok(((z, x, y), sortie, ecretes))
        })
        .collect::<Result<_, _>>()?;
    let rechantillonnage_encodage_s = t1.elapsed().as_secs_f64();

    let t2 = Instant::now();
    let run = canon.tirage_court();
    let mut fichiers = Vec::new();
    for (k, s) in sorties.iter().enumerate() {
        let mut tuiles = Vec::new();
        let mut octets_tuiles = 0usize;
        let mut ecretes = 0u64;
        for (zxy, octets, ecr) in &resultats {
            ecretes += ecr[k];
            if let Some(o) = &octets[k] {
                octets_tuiles += o.len();
                tuiles.push((*zxy, o.clone()));
            }
        }
        if tuiles.is_empty() {
            continue;
        }
        let nb = tuiles.len();
        let pas = if s.wind { encode::pas_vent8(p.vent_vmax) } else { encode::pas_scalaire(s.min, s.max, s.echelle, s.niveaux) };
        let md = metadonnees(s, p, &canon.modele, &run, canon.echeance, zmin, s.zmax, pas);
        let octets = pmtiles::ecrire(tuiles, &md, EnTete { emprise: grille.emprise, zoom_centre: zmin as u8 });
        fichiers.push(Fichier {
            variable: s.nom.to_string(),
            chemin: format!("{}/{}/{}/{:02}.pmtiles", canon.modele, run, s.nom, canon.echeance),
            octets,
            tuiles: nb,
            octets_tuiles,
            pixels_ecretes: ecretes,
        });
    }
    let ecriture_pmtiles_s = t2.elapsed().as_secs_f64();
    Ok((fichiers, Mesures { preparation_s, rechantillonnage_encodage_s, ecriture_pmtiles_s }))
}

#[allow(clippy::too_many_arguments)]
fn metadonnees(s: &Sortie, p: &Profil, modele: &str, run: &str, echeance: u32, zmin: u32, zmax: u32, pas: f64) -> Value {
    let mut m = Map::new();
    if s.wind {
        m.insert("encoding".into(), json!("wind8"));
        m.insert("min".into(), json!(s.min));
        m.insert("max".into(), json!(s.max));
        m.insert("scale".into(), json!("linear"));
        m.insert("unit".into(), json!("m/s"));
        m.insert("components".into(), json!(["wind10_u", "wind10_v"]));
        m.insert("quant_step".into(), json!(pas));
    } else {
        m.insert("encoding".into(), json!("scalar8"));
        m.insert("min".into(), json!(s.min));
        m.insert("max".into(), json!(s.max));
        m.insert("scale".into(), json!(s.echelle.nom()));
        m.insert("unit".into(), json!(s.unite));
        m.insert("quant_step".into(), json!(pas));
        m.insert("levels".into(), json!(s.niveaux));
    }
    m.insert("profile".into(), json!(p.nom));
    m.insert("border_dropped_from_zoom".into(), Value::Null);
    m.insert("variable".into(), json!(s.nom));
    m.insert("model".into(), json!(modele));
    m.insert("run".into(), json!(run));
    m.insert("step".into(), json!(format!("{echeance:02}")));
    m.insert("border".into(), json!(BORDURE));
    m.insert("tile_size".into(), json!(TUILE));
    m.insert("stored_size".into(), json!(STOCKEE));
    m.insert("format".into(), json!("webp-lossless"));
    m.insert("nodata_code".into(), json!(0));
    m.insert("minzoom".into(), json!(zmin));
    m.insert("maxzoom".into(), json!(zmax));
    m.insert("attribution".into(), json!("Source : Météo-France, Licence Ouverte v2.0"));
    Value::Object(m)
}
