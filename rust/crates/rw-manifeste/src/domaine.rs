// SPDX-License-Identifier: EUPL-1.2
//! Polygone du domaine d'un modèle, déduit du masque `t2m` d'un fichier `_domaine/NN.npz`.
//!
//! Port fidèle de `pipeline/manifest/domaine.py` (`polygone_depuis_masque`) : 2880 rayons depuis le barycentre des
//! points valides, point valide le plus éloigné sur chacun (domaine étoilé), simplification de Douglas-Peucker. Le
//! barycentre utilise la sommation par paires de NumPy pour reproduire ses arrondis ; les sinus et cosinus viennent de
//! la bibliothèque mathématique de Rust (écarts possibles de 1 ulp avec NumPy, sans effet observé : voir le test de
//! comparaison avec le manifeste Python).

use anyhow::{anyhow, bail, Context, Result};
use std::io::{Cursor, Read};

pub const RAYONS: usize = 2880;

/// Tableau `.npy` lu en flottants 64 bits (formes `<f4` et `<f8`).
fn lire_npy(mut r: impl Read) -> Result<(Vec<f64>, Vec<usize>)> {
    let mut m = [0u8; 8];
    r.read_exact(&mut m)?;
    if &m[..6] != b"\x93NUMPY" {
        bail!("pas un fichier npy");
    }
    let lh = if m[6] == 1 {
        let mut b = [0u8; 2];
        r.read_exact(&mut b)?;
        u16::from_le_bytes(b) as usize
    } else {
        let mut b = [0u8; 4];
        r.read_exact(&mut b)?;
        u32::from_le_bytes(b) as usize
    };
    if lh > 4096 {
        bail!("en-tête npy démesuré");
    }
    let mut h = vec![0u8; lh];
    r.read_exact(&mut h)?;
    let h = String::from_utf8(h)?;
    let descr = h.split("'descr':").nth(1).and_then(|s| s.split('\'').nth(1)).context("descr absent")?.to_string();
    if h.contains("'fortran_order': True") {
        bail!("ordre Fortran non géré");
    }
    let shape = h.split("'shape':").nth(1).and_then(|s| s.split('(').nth(1)).and_then(|s| s.split(')').next()).context("shape absent")?;
    let forme: Vec<usize> = shape.split(',').map(str::trim).filter(|s| !s.is_empty()).map(str::parse).collect::<Result<_, _>>()?;
    let n: usize = forme.iter().product();
    let mut brut = Vec::new();
    r.read_to_end(&mut brut)?;
    let v: Vec<f64> = match descr.as_str() {
        "<f4" if brut.len() == 4 * n => brut.chunks_exact(4).map(|c| f64::from(f32::from_le_bytes([c[0], c[1], c[2], c[3]]))).collect(),
        "<f8" if brut.len() == 8 * n => brut.chunks_exact(8).map(|c| f64::from_le_bytes(c.try_into().unwrap())).collect(),
        _ => bail!("dtype {descr} ou taille {} (pour {n} valeurs) non gérés", brut.len()),
    };
    Ok((v, forme))
}

/// Masque de validité d'un domaine : `lat` (une valeur par ligne), `lon` (par colonne), `valide[i * nlon + j]`.
#[derive(Debug, Clone, PartialEq)]
pub struct Masque {
    pub lat: Vec<f64>,
    pub lon: Vec<f64>,
    pub valide: Vec<bool>,
}

/// Lit `lat`, `lon` et `t2m` d'un `.npz` ; valide = non NaN (comme `~np.isnan` du Python).
pub fn lire_masque(octets: &[u8]) -> Result<Masque> {
    let mut z = zip::ZipArchive::new(Cursor::new(octets)).context("npz illisible")?;
    let mut tableau = |nom: &str| -> Result<(Vec<f64>, Vec<usize>)> {
        lire_npy(z.by_name(&format!("{nom}.npy")).with_context(|| format!("{nom}.npy absent"))?).with_context(|| nom.to_string())
    };
    let (lat, _) = tableau("lat")?;
    let (lon, _) = tableau("lon")?;
    let (t2m, forme) = tableau("t2m")?;
    if forme != [lat.len(), lon.len()] {
        bail!("forme de t2m {forme:?} différente de ({}, {})", lat.len(), lon.len());
    }
    Ok(Masque { lat, lon, valide: t2m.iter().map(|x| !x.is_nan()).collect() })
}

/// Somme de NumPy pour un tableau 1-D contigu : premier élément, puis sommation par paires du reste.
fn somme_numpy(a: &[f64]) -> f64 {
    fn paires(a: &[f64]) -> f64 {
        let n = a.len();
        if n < 8 {
            let mut r = 0.0;
            for x in a {
                r += x;
            }
            r
        } else if n <= 128 {
            let mut r = [a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7]];
            let mut i = 8;
            while i < n - (n % 8) {
                for j in 0..8 {
                    r[j] += a[i + j];
                }
                i += 8;
            }
            let mut res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
            while i < n {
                res += a[i];
                i += 1;
            }
            res
        } else {
            let mut n2 = n / 2;
            n2 -= n2 % 8;
            paires(&a[..n2]) + paires(&a[n2..])
        }
    }
    match a.split_first() {
        None => 0.0,
        Some((p, reste)) => p + paires(reste),
    }
}

fn douglas_peucker(pts: &[(f64, f64)], tol: f64) -> Vec<(f64, f64)> {
    let n = pts.len();
    if n <= 2 {
        return pts.to_vec();
    }
    let mut garde = vec![false; n];
    garde[0] = true;
    garde[n - 1] = true;
    let mut pile = vec![(0usize, n - 1)];
    while let Some((a, b)) = pile.pop() {
        if b <= a + 1 {
            continue;
        }
        let (p, q) = (pts[a], pts[b]);
        let d = (q.0 - p.0, q.1 - p.1);
        let norme = d.0.hypot(d.1);
        let mut k = 0usize;
        let mut max = f64::NEG_INFINITY;
        for (i, s) in pts[a + 1..b].iter().enumerate() {
            let dist = if norme == 0.0 { (s.0 - p.0).hypot(s.1 - p.1) } else { (d.0 * (s.1 - p.1) - d.1 * (s.0 - p.0)).abs() / norme };
            if dist > max {
                max = dist;
                k = i;
            }
        }
        if max > tol {
            let m = a + 1 + k;
            garde[m] = true;
            pile.push((a, m));
            pile.push((m, b));
        }
    }
    pts.iter().zip(garde).filter(|(_, g)| *g).map(|(p, _)| *p).collect()
}

/// Arrondi décimal exact à 4 décimales (comme `round(x, 4)` de Python sur un `float`).
fn arrondi4(x: f64) -> f64 {
    let v: f64 = format!("{x:.4}").parse().unwrap_or(x);
    if v == 0.0 { 0.0 } else { v }
}

/// Anneau fermé `[[lon, lat], …]` du domaine valide (voir le module).
pub fn polygone_depuis_masque(m: &Masque, tol: f64, rayons: usize) -> Result<Vec<[f64; 2]>> {
    let (nlat, nlon) = (m.lat.len(), m.lon.len());
    if nlat < 2 || nlon < 2 || m.valide.len() != nlat * nlon {
        bail!("masque de forme invalide ({nlat} × {nlon}, {} valeurs)", m.valide.len());
    }
    let (mut lats, mut lons) = (Vec::new(), Vec::new());
    for i in 0..nlat {
        for j in 0..nlon {
            if m.valide[i * nlon + j] {
                lats.push(m.lat[i]);
                lons.push(m.lon[j]);
            }
        }
    }
    if lats.is_empty() {
        bail!("masque vide : aucun point valide");
    }
    let n = lats.len() as f64;
    let (cy, cx) = (somme_numpy(&lats) / n, somme_numpy(&lons) / n);
    let dlat = (m.lat[1] - m.lat[0]).abs();
    let dlon = (m.lon[1] - m.lon[0]).abs();
    let (lat0, lon0) = (m.lat[0], m.lon[0]);
    let pas_lat = (m.lat[nlat - 1] - m.lat[0]) / (nlat - 1).max(1) as f64;
    let pas_lon = (m.lon[nlon - 1] - m.lon[0]) / (nlon - 1).max(1) as f64;
    let rmax = (m.lat[nlat - 1] - m.lat[0]).abs().hypot((m.lon[nlon - 1] - m.lon[0]).abs());
    let pas_r = dlat.min(dlon) * 0.5;
    let nr = (rmax / pas_r).ceil() as usize;
    let pas_th = (2.0 * std::f64::consts::PI) / rayons as f64;
    let mut anneau: Vec<(f64, f64)> = Vec::with_capacity(rayons);
    for t in 0..rayons {
        let th = t as f64 * pas_th;
        let (s, c) = th.sin_cos();
        let mut dernier: Option<(usize, usize)> = None;
        for k in 0..nr {
            let r = k as f64 * pas_r;
            let a = ((cy + r * s - lat0) / pas_lat).round_ties_even();
            let b = ((cx + r * c - lon0) / pas_lon).round_ties_even();
            if a >= 0.0 && (a as usize) < nlat && b >= 0.0 && (b as usize) < nlon {
                let (a, b) = (a as usize, b as usize);
                if m.valide[a * nlon + b] {
                    dernier = Some((a, b));
                }
            }
        }
        let (a, b) = dernier.ok_or_else(|| anyhow!("rayon {t} sans point valide"))?;
        anneau.push((m.lon[b], m.lat[a]));
    }
    let moitie = rayons / 2;
    let c1 = douglas_peucker(&anneau[..=moitie], tol);
    let mut seconde = anneau[moitie..].to_vec();
    seconde.push(anneau[0]);
    let c2 = douglas_peucker(&seconde, tol);
    let mut sortie: Vec<(f64, f64)> = Vec::new();
    for p in c1.into_iter().chain(c2.into_iter().skip(1)) {
        if sortie.last() != Some(&p) {
            sortie.push(p);
        }
    }
    if sortie.first() != sortie.last() {
        sortie.push(sortie[0]);
    }
    Ok(sortie.into_iter().map(|(x, y)| [arrondi4(x), arrondi4(y)]).collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn somme_comme_numpy() {
        // petits tableaux : somme séquentielle ; grands : par paires (valeurs vérifiées avec numpy.sum)
        assert_eq!(somme_numpy(&[]), 0.0);
        assert_eq!(somme_numpy(&[1.0, 2.0, 3.0]), 6.0);
        let v: Vec<f64> = (0..1000).map(|i| 0.1 * f64::from(i)).collect();
        let s = somme_numpy(&v);
        assert!((s - 49950.0).abs() < 1e-9);
    }

    #[test]
    fn dp_garde_les_angles() {
        let l = [(0.0, 0.0), (1.0, 0.001), (2.0, 0.0), (2.0, 2.0)];
        assert_eq!(douglas_peucker(&l, 0.04), vec![(0.0, 0.0), (2.0, 0.0), (2.0, 2.0)]);
    }

    #[test]
    fn arrondi() {
        assert_eq!(arrondi4(14.000_015_26), 14.0);
        assert_eq!(arrondi4(-0.000_01), 0.0);
        assert_eq!(arrondi4(46.950_001), 46.95);
    }

    /// Un rectangle de 10 × 20 mailles de 0,5° : le polygone est ce rectangle (4 coins + fermeture).
    #[test]
    fn rectangle() {
        let lat: Vec<f64> = (0..11).map(|i| 50.0 - 0.5 * f64::from(i)).collect();
        let lon: Vec<f64> = (0..21).map(|j| -5.0 + 0.5 * f64::from(j)).collect();
        let m = Masque { valide: vec![true; lat.len() * lon.len()], lat, lon };
        let p = polygone_depuis_masque(&m, 0.04, RAYONS).unwrap();
        assert_eq!(p.first(), p.last());
        // le premier sommet est au milieu d'un bord (rayon d'angle 0) : au moins les 4 coins, tous sur le contour
        assert!(p.len() >= 5 && p.iter().all(|q| q[0].abs() == 5.0 || q[1] == 45.0 || q[1] == 50.0), "{p:?}");
        let xs: Vec<f64> = p.iter().map(|q| q[0]).collect();
        let ys: Vec<f64> = p.iter().map(|q| q[1]).collect();
        assert_eq!((xs.iter().cloned().fold(f64::MAX, f64::min), xs.iter().cloned().fold(f64::MIN, f64::max)), (-5.0, 5.0));
        assert_eq!((ys.iter().cloned().fold(f64::MAX, f64::min), ys.iter().cloned().fold(f64::MIN, f64::max)), (45.0, 50.0));
    }

    #[test]
    fn masque_vide_et_npy() {
        let m = Masque { lat: vec![1.0, 0.0], lon: vec![0.0, 1.0], valide: vec![false; 4] };
        assert!(polygone_depuis_masque(&m, 0.04, 8).is_err());
        assert!(lire_masque(b"pas un zip").is_err());
    }
}
