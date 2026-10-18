// SPDX-License-Identifier: EUPL-1.2
//! Isobares et extrema A / D d'une échéance : port Rust de `pipeline/isobars` (voir `docs/poc-rust/isobares.md`).
//!
//! Entrée : `mslp` en pascals sur la grille canonique (latitudes décroissantes), sortie : le GeoJSON que lit
//! `viewer/js/isobares.js` (identique à `calcul.vers_geojson` + `json.dump(separators=(",", ":"))`), brut ou gzip.

pub mod contours;
pub mod extrema;
pub mod geojson;
pub mod lissage;

use rayon::prelude::*;
use std::time::Instant;

/// Paramètres : valeurs par défaut de `pipeline/isobars/calcul.py`.
#[derive(Debug, Clone, PartialEq)]
pub struct Params {
    /// Lissage gaussien des isobares (mailles).
    pub sigma: f64,
    pub pas_hpa: f64,
    /// Tolérance de Douglas-Peucker (km).
    pub tol_km: f64,
    /// Érosion du masque pour les cellules de contour (mailles).
    pub marge: usize,
    /// Proéminence minimale des extrema (hPa).
    pub proeminence: f64,
    /// Lissage de détection des extrema (mailles).
    pub sigma_extrema: f64,
    /// Anneaux fermés plus courts : supprimés (km).
    pub anneau_min_km: f64,
    /// Décimales des coordonnées (degrés).
    pub decimales: i32,
}

impl Default for Params {
    fn default() -> Self {
        Self { sigma: 4.0, pas_hpa: 2.0, tol_km: 0.1, marge: 1, proeminence: 1.0, sigma_extrema: 8.0, anneau_min_km: 12.0, decimales: 4 }
    }
}

#[derive(Debug, thiserror::Error)]
pub enum Erreur {
    #[error("grille incohérente : lat {ny}, lon {nx}, champ {n}")]
    Grille { ny: usize, nx: usize, n: usize },
    #[error("grille trop petite ({ny}x{nx})")]
    Petite { ny: usize, nx: usize },
    #[error("contours : {0}")]
    Contours(String),
}

/// Une isobare.
#[derive(Debug, Clone)]
pub struct Ligne {
    pub niveau: f64,
    pub majeur: bool,
    pub ferme: bool,
    /// (lon, lat) en degrés, après simplification.
    pub coords: Vec<[f64; 2]>,
    /// Longueur avant simplification (km).
    pub longueur_km: f64,
}

#[derive(Debug, Clone)]
pub struct Extremum {
    /// `'A'` ou `'D'`.
    pub genre: char,
    pub lon: f64,
    pub lat: f64,
    pub pression: f64,
    pub proeminence: f64,
}

#[derive(Debug, Clone, Default)]
pub struct Stats {
    pub lignes_brutes: usize,
    pub anneaux_rejetes: usize,
    pub lignes: usize,
    pub sommets: usize,
    pub extrema: usize,
    pub t_lissage_s: f64,
    pub t_contours_s: f64,
    pub t_extrema_s: f64,
}

#[derive(Debug, Clone)]
pub struct Resultat {
    pub lignes: Vec<Ligne>,
    pub extrema: Vec<Extremum>,
    pub stats: Stats,
}

/// Métadonnées de l'en-tête GeoJSON (`proprietes`).
#[derive(Debug, Clone)]
pub struct Entete {
    pub modele: String,
    /// Tirage au format `2026-10-17T06:00:00Z`.
    pub tirage: String,
    pub echeance: u32,
}

/// Arrondi à `d` décimales comme `round(x, d)` de Python (arrondi correct de la valeur binaire exacte).
pub fn arrondi(x: f64, d: i32) -> f64 {
    if !x.is_finite() {
        return x;
    }
    format!("{:.*}", d as usize, x).parse().unwrap_or(x)
}

/// Calcule isobares et extrema. `lat` (décroissante), `lon` : axes de la grille ; `mslp_pa` : [lat.len() × lon.len()].
/// Utilise le pool rayon courant (appeler dans `pool.install` pour fixer le nombre de fils).
pub fn calculer(lat: &[f32], lon: &[f32], mslp_pa: &[f32], p: &Params) -> Result<Resultat, Erreur> {
    let (ny, nx) = (lat.len(), lon.len());
    if mslp_pa.len() != ny * nx {
        return Err(Erreur::Grille { ny, nx, n: mslp_pa.len() });
    }
    if ny < 3 || nx < 3 {
        return Err(Erreur::Petite { ny, nx });
    }
    let lat64: Vec<f64> = lat.iter().map(|&v| v as f64).collect();
    let lon64: Vec<f64> = lon.iter().map(|&v| v as f64).collect();
    let brut: Vec<f64> = mslp_pa.iter().map(|&v| v as f64 / 100.0).collect();

    // 1. lissages (champ des isobares et champ des extrema, indépendants)
    let t0 = Instant::now();
    let (f, f_ext) = if p.sigma_extrema != p.sigma {
        let (a, b) = rayon::join(|| lissage::lisser(&brut, ny, nx, p.sigma), || lissage::lisser(&brut, ny, nx, p.sigma_extrema));
        (a, Some(b))
    } else {
        (lissage::lisser(&brut, ny, nx, p.sigma), None)
    };
    drop(brut);
    let t_lissage_s = t0.elapsed().as_secs_f64();

    // 2. isobares (niveaux en parallèle, ordre conservé)
    let t1 = Instant::now();
    let valide: Vec<bool> = f.iter().map(|v| v.is_finite()).collect();
    let cell_ok = contours::cellules_valides(&valide, ny, nx, p.marge);
    let tab = contours::table_ms();
    let niveaux = contours::niveaux_hpa(&f, p.pas_hpa);
    let par_niveau: Vec<Result<(Vec<Ligne>, usize, usize), String>> = niveaux
        .par_iter()
        .map(|&niv| {
            let mut lignes = Vec::new();
            let (mut brutes, mut rejets) = (0usize, 0usize);
            for (pts, ferme) in contours::contour(&f, ny, nx, niv, &cell_ok, &tab)? {
                let ll = contours::vers_lonlat(&pts, &lat64, &lon64);
                brutes += 1;
                let longueur = contours::longueur_km(&ll);
                if ferme && longueur < p.anneau_min_km {
                    rejets += 1;
                    continue;
                }
                let coords = contours::simplifier(ll, ferme, p.tol_km);
                lignes.push(Ligne { niveau: niv, majeur: (niv.round() as i64) % 10 == 0, ferme, coords, longueur_km: longueur });
            }
            Ok((lignes, brutes, rejets))
        })
        .collect();
    let mut stats = Stats::default();
    let mut lignes = Vec::new();
    for r in par_niveau {
        let (l, b, rej) = r.map_err(Erreur::Contours)?;
        stats.lignes_brutes += b;
        stats.anneaux_rejetes += rej;
        lignes.extend(l);
    }
    stats.t_contours_s = t1.elapsed().as_secs_f64();

    // 3. extrema
    let t2 = Instant::now();
    let fe = f_ext.as_deref().unwrap_or(&f);
    let (mx, mn) = extrema::detecter(fe, ny, nx, p.proeminence, 3);
    let mut extr = Vec::new();
    for (genre, liste) in [('A', mx), ('D', mn)] {
        for e in liste {
            extr.push(Extremum {
                genre,
                lon: lon[e.colonne] as f64,
                lat: lat[e.ligne] as f64,
                pression: arrondi(f[e.ligne * nx + e.colonne], 1),
                proeminence: arrondi(e.proeminence, 1),
            });
        }
    }
    stats.t_extrema_s = t2.elapsed().as_secs_f64();
    stats.t_lissage_s = t_lissage_s;
    stats.lignes = lignes.len();
    stats.sommets = lignes.iter().map(|l| l.coords.len()).sum();
    stats.extrema = extr.len();
    Ok(Resultat { lignes, extrema: extr, stats })
}

/// Sortie complète d'une échéance.
pub struct Produit {
    /// GeoJSON non compressé (octet pour octet celui du Python).
    pub geojson: Vec<u8>,
    /// Même contenu, gzip (niveau 6, date nulle : déterministe).
    pub gzip: Vec<u8>,
    pub stats: Stats,
}

/// Calcule et sérialise : GeoJSON brut et gzip.
pub fn produire(lat: &[f32], lon: &[f32], mslp_pa: &[f32], entete: &Entete, p: &Params) -> Result<Produit, Erreur> {
    let r = calculer(lat, lon, mslp_pa, p)?;
    let geojson = geojson::vers_geojson(&r, entete, p).into_bytes();
    let gzip = geojson::gzip(&geojson, 6);
    Ok(Produit { geojson, gzip, stats: r.stats })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Dépression synthétique : creux gaussien dans un fond incliné ; 1 trapèze masqué à droite.
    fn synthetique() -> (Vec<f32>, Vec<f32>, Vec<f32>) {
        let (ny, nx) = (120usize, 160usize);
        let lat: Vec<f32> = (0..ny).map(|i| 50.0 - 0.025 * i as f32).collect();
        let lon: Vec<f32> = (0..nx).map(|j| -5.0 + 0.025 * j as f32).collect();
        let mut m = vec![0f32; ny * nx];
        for i in 0..ny {
            for j in 0..nx {
                let d2 = ((i as f32 - 60.0).powi(2) + (j as f32 - 80.0).powi(2)) / (2.0 * 25.0 * 25.0);
                m[i * nx + j] = 101_300.0 - 1_800.0 * (-d2).exp() + 3.0 * j as f32;
            }
        }
        (lat, lon, m)
    }

    #[test]
    fn chaine_complete_sur_depression_synthetique() {
        let (lat, lon, m) = synthetique();
        let r = calculer(&lat, &lon, &m, &Params::default()).unwrap();
        assert!(r.lignes.iter().any(|l| l.ferme), "au moins une isobare fermée");
        assert_eq!(r.extrema.iter().filter(|e| e.genre == 'D').count(), 1);
        let d = r.extrema.iter().find(|e| e.genre == 'D').unwrap();
        assert!((d.lon - (-5.0 + 0.025 * 80.0)).abs() < 0.2 && (d.lat - (50.0 - 0.025 * 60.0)).abs() < 0.2, "{d:?}");
        assert!(d.pression < 1000.0);
        for l in &r.lignes {
            assert_eq!(l.niveau % 2.0, 0.0);
            assert_eq!(l.majeur, (l.niveau as i64) % 10 == 0);
            if l.ferme {
                assert_eq!(l.coords.first(), l.coords.last());
            }
        }
    }

    #[test]
    fn produit_est_deterministe_et_gzip_se_relit() {
        use std::io::Read;
        let (lat, lon, m) = synthetique();
        let e = Entete { modele: "arome".into(), tirage: "2026-10-17T06:00:00Z".into(), echeance: 12 };
        let a = produire(&lat, &lon, &m, &e, &Params::default()).unwrap();
        let b = produire(&lat, &lon, &m, &e, &Params::default()).unwrap();
        assert_eq!(a.geojson, b.geojson);
        assert_eq!(a.gzip, b.gzip);
        let mut s = Vec::new();
        flate2::read::GzDecoder::new(&a.gzip[..]).read_to_end(&mut s).unwrap();
        assert_eq!(s, a.geojson);
        let v: serde_json::Value = serde_json::from_slice(&a.geojson).unwrap();
        assert_eq!(v["type"], "FeatureCollection");
        assert_eq!(v["proprietes"]["echeance"], 12);
        assert!(a.gzip.len() < a.geojson.len());
    }

    #[test]
    fn grille_incoherente_refusee() {
        assert!(calculer(&[1.0, 2.0, 3.0], &[1.0, 2.0, 3.0], &[0.0; 5], &Params::default()).is_err());
    }

    #[test]
    fn arrondi_comme_python() {
        assert_eq!(arrondi(0.25, 1), 0.2);
        assert_eq!(arrondi(2.675, 2), 2.67);
        assert_eq!(arrondi(1013.2499, 1), 1013.2);
        assert_eq!(arrondi(-0.00001, 4).to_bits(), (-0.0f64).to_bits());
    }
}
