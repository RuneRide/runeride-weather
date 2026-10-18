// SPDX-License-Identifier: EUPL-1.2
//! Lissage gaussien normalisé par le masque (port de `contours.lisser`).
//!
//! L'ordre des opérations flottantes est celui du Python (somme des taps dans l'ordre croissant, noyau
//! normalisé par la somme « pairwise » de numpy) : le résultat est identique bit à bit sur la même libm.

use rayon::prelude::*;

/// Somme de numpy (`ndarray.sum` sur un vecteur contigu) : sommation par blocs (pairwise).
pub fn somme_numpy(a: &[f64]) -> f64 {
    let n = a.len();
    if n < 8 {
        let mut r = 0.0;
        for &x in a {
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
        somme_numpy(&a[..n2]) + somme_numpy(&a[n2..])
    }
}

/// Noyau gaussien 1D normalisé, rayon 3 sigma.
pub fn noyau_gaussien(sigma: f64) -> Vec<f64> {
    let r = ((3.0 * sigma).ceil() as i64).max(1);
    let k: Vec<f64> = (-r..=r)
        .map(|x| {
            let t = x as f64 / sigma;
            (-0.5 * (t * t)).exp()
        })
        .collect();
    let s = somme_numpy(&k);
    k.iter().map(|v| v / s).collect()
}

/// Convolution verticale (zéros hors grille) d'un champ [ny, nx].
fn conv_vertical(src: &[f64], ny: usize, nx: usize, k: &[f64]) -> Vec<f64> {
    let r = k.len() / 2;
    let mut sortie = vec![0.0f64; ny * nx];
    sortie.par_chunks_mut(nx).enumerate().for_each(|(i, ligne)| {
        for (m, &c) in k.iter().enumerate() {
            // ligne source i + m - r ; hors grille : terme nul (0 + x = x : omission sans effet)
            let Some(s) = (i + m).checked_sub(r) else { continue };
            if s >= ny {
                continue;
            }
            let ls = &src[s * nx..(s + 1) * nx];
            for (o, &v) in ligne.iter_mut().zip(ls) {
                *o += c * v;
            }
        }
    });
    sortie
}

/// Convolution horizontale (zéros hors grille).
fn conv_horizontal(src: &[f64], ny: usize, nx: usize, k: &[f64]) -> Vec<f64> {
    let r = k.len() / 2;
    let mut sortie = vec![0.0f64; ny * nx];
    sortie.par_chunks_mut(nx).zip(src.par_chunks(nx)).for_each(|(ligne, ls)| {
        let mut pad = vec![0.0f64; nx + 2 * r];
        pad[r..r + nx].copy_from_slice(ls);
        for (m, &c) in k.iter().enumerate() {
            for (o, &v) in ligne.iter_mut().zip(&pad[m..m + nx]) {
                *o += c * v;
            }
        }
    });
    sortie
}

/// Lissage gaussien d'écart-type `sigma` (mailles) normalisé par le masque : `conv(F·m) / conv(m)`.
/// Les valeurs non finies restent NaN en sortie. `sigma <= 0` : copie.
pub fn lisser(champ: &[f64], ny: usize, nx: usize, sigma: f64) -> Vec<f64> {
    if sigma <= 0.0 {
        return champ.to_vec();
    }
    let m: Vec<bool> = champ.iter().map(|v| v.is_finite()).collect();
    let k = noyau_gaussien(sigma);
    let num0: Vec<f64> = champ.iter().zip(&m).map(|(&v, &ok)| if ok { v } else { 0.0 }).collect();
    let den0: Vec<f64> = m.iter().map(|&ok| if ok { 1.0 } else { 0.0 }).collect();
    let (num, den) = rayon::join(
        || conv_horizontal(&conv_vertical(&num0, ny, nx, &k), ny, nx, &k),
        || conv_horizontal(&conv_vertical(&den0, ny, nx, &k), ny, nx, &k),
    );
    num.par_iter()
        .zip(den.par_iter())
        .zip(m.par_iter())
        .map(|((&a, &b), &ok)| if ok { a / b } else { f64::NAN })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn noyau_somme_un() {
        for s in [1.0, 4.0, 8.0] {
            let k = noyau_gaussien(s);
            assert_eq!(k.len() % 2, 1);
            assert!((k.iter().sum::<f64>() - 1.0).abs() < 1e-14);
            assert_eq!(k[0], k[k.len() - 1]);
        }
    }

    #[test]
    fn champ_constant_reste_constant_avec_masque() {
        let (ny, nx) = (30, 40);
        let mut f = vec![1000.0; ny * nx];
        for j in 0..nx {
            f[j] = f64::NAN; // première ligne masquée
        }
        let l = lisser(&f, ny, nx, 3.0);
        assert!(l[0].is_nan());
        for &v in &l[nx..] {
            assert!((v - 1000.0).abs() < 1e-9, "{v}");
        }
    }

    #[test]
    fn sigma_nul_copie() {
        let f = vec![1.0, f64::NAN, 3.0, 4.0];
        let l = lisser(&f, 2, 2, 0.0);
        assert_eq!(l[0], 1.0);
        assert!(l[1].is_nan());
    }

    #[test]
    fn impulsion_se_diffuse_symetriquement() {
        let (ny, nx) = (21, 21);
        let mut f = vec![0.0; ny * nx];
        f[10 * nx + 10] = 1.0;
        let l = lisser(&f, ny, nx, 2.0);
        assert!((l[10 * nx + 9] - l[10 * nx + 11]).abs() < 1e-15);
        assert!((l[9 * nx + 10] - l[11 * nx + 10]).abs() < 1e-15);
        assert!(l[10 * nx + 10] > l[10 * nx + 9]);
    }
}
