// SPDX-License-Identifier: EUPL-1.2
//! Extrema A / D par proéminence (inondation décroissante + union-find), port de `pipeline/isobars/extrema.py`.
//! L'ordre d'insertion des sommets est conservé (le tri final par proéminence est stable, comme en Python).

const VOISINS8: [(i64, i64); 8] = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)];

/// Un maximum : (ligne, colonne, valeur, proéminence), en coordonnées du champ complet.
pub type Maximum = (usize, usize, f64, f64);

fn racine(parent: &mut [usize], mut x: usize) -> usize {
    while parent[x] != x {
        parent[x] = parent[parent[x]];
        x = parent[x];
    }
    x
}

/// Proéminence de tous les maxima locaux de `f` (NaN = hors domaine), sur la grille sous-échantillonnée d'un facteur `pas`.
pub fn proeminences_maxima(f: &[f64], ny0: usize, nx0: usize, pas: usize) -> Vec<Maximum> {
    let ny = (ny0 + pas - 1) / pas;
    let nx = (nx0 + pas - 1) / pas;
    let mut g = vec![0.0f64; ny * nx];
    for i in 0..ny {
        for j in 0..nx {
            g[i * nx + j] = f[(i * pas) * nx0 + j * pas];
        }
    }
    let ok: Vec<bool> = g.iter().map(|v| v.is_finite()).collect();
    let est_ok = |i: i64, j: i64| i >= 0 && j >= 0 && (i as usize) < ny && (j as usize) < nx && ok[i as usize * nx + j as usize];
    let mut bord = vec![false; ny * nx];
    for i in 0..ny {
        for j in 0..nx {
            if ok[i * nx + j] {
                let tous = VOISINS8.iter().all(|&(di, dj)| est_ok(i as i64 + di, j as i64 + dj));
                bord[i * nx + j] = !tous;
            }
        }
    }
    let val: Vec<f64> = g.iter().zip(&ok).map(|(&v, &o)| if o { v } else { f64::NEG_INFINITY }).collect();
    let n_ok = ok.iter().filter(|&&o| o).count();
    let mut ordre: Vec<usize> = (0..ny * nx).collect();
    // tri stable par valeur décroissante (équivalent de argsort(-flat, stable))
    ordre.sort_by(|&a, &b| (-val[a]).partial_cmp(&(-val[b])).unwrap());
    ordre.truncate(n_ok);

    let mut parent: Vec<usize> = (0..ny * nx).collect();
    let sommet: Vec<usize> = (0..ny * nx).collect();
    let mut actif = vec![false; ny * nx];
    let mut infini = vec![false; ny * nx];
    let mut prom: Vec<Option<f64>> = vec![None; ny * nx];
    let mut ordre_ins: Vec<usize> = Vec::new();
    let mut fixer = |prom: &mut Vec<Option<f64>>, s: usize, v: f64, force: bool| {
        if prom[s].is_none() {
            ordre_ins.push(s);
            prom[s] = Some(v);
        } else if force {
            prom[s] = Some(v);
        }
    };

    for &c in &ordre {
        actif[c] = true;
        let v = val[c];
        let (i, j) = ((c / nx) as i64, (c % nx) as i64);
        if bord[c] {
            infini[c] = true;
            fixer(&mut prom, c, 0.0, true);
        }
        for (di, dj) in VOISINS8 {
            let (a, b) = (i + di, j + dj);
            if a < 0 || b < 0 || a as usize >= ny || b as usize >= nx {
                continue;
            }
            let q = a as usize * nx + b as usize;
            if !actif[q] {
                continue;
            }
            let r_q = racine(&mut parent, q);
            let r_c = racine(&mut parent, c);
            if r_q == r_c {
                continue;
            }
            let (inf_q, inf_c) = (infini[r_q], infini[r_c]);
            let (gagnant, perdant);
            if inf_q && inf_c {
                gagnant = r_q;
                perdant = r_c;
            } else if inf_q {
                gagnant = r_q;
                perdant = r_c;
                let s = sommet[r_c];
                fixer(&mut prom, s, val[s] - v, false);
            } else if inf_c {
                gagnant = r_c;
                perdant = r_q;
                let s = sommet[r_q];
                fixer(&mut prom, s, val[s] - v, false);
            } else if val[sommet[r_q]] >= val[sommet[r_c]] {
                gagnant = r_q;
                perdant = r_c;
                let s = sommet[r_c];
                fixer(&mut prom, s, val[s] - v, false);
            } else {
                gagnant = r_c;
                perdant = r_q;
                let s = sommet[r_q];
                fixer(&mut prom, s, val[s] - v, false);
            }
            parent[perdant] = gagnant;
            if inf_q || inf_c {
                infini[gagnant] = true;
            }
        }
    }
    let mn = g.iter().filter(|v| v.is_finite()).fold(f64::INFINITY, |a, &b| a.min(b));
    for &c in &ordre {
        if parent[c] == c && prom[sommet[c]].is_none() && !infini[c] {
            let s = sommet[c];
            fixer(&mut prom, s, val[s] - mn, false);
        }
    }
    ordre_ins
        .into_iter()
        .map(|s| ((s / nx) * pas, (s % nx) * pas, val[s], prom[s].unwrap()))
        .collect()
}

#[derive(Debug, Clone, PartialEq)]
pub struct Extremum {
    pub ligne: usize,
    pub colonne: usize,
    pub valeur: f64,
    pub proeminence: f64,
}

/// Extrema du champ lissé `f` (hPa) de proéminence >= `seuil` : (maxima, minima), triés par proéminence décroissante.
/// Position affinée sur la grille pleine (±`pas` mailles autour du sommet sous-échantillonné).
pub fn detecter(f: &[f64], ny: usize, nx: usize, seuil: f64, pas: usize) -> (Vec<Extremum>, Vec<Extremum>) {
    let mut sortie = Vec::new();
    for signe in [1.0f64, -1.0] {
        let g: Vec<f64> = if signe == 1.0 { f.to_vec() } else { f.iter().map(|v| -v).collect() };
        let mut liste = Vec::new();
        for (i, j, _v, pr) in proeminences_maxima(&g, ny, nx, pas) {
            if pr < seuil {
                continue;
            }
            let (i0, i1) = (i.saturating_sub(pas), (i + pas + 1).min(ny));
            let (j0, j1) = (j.saturating_sub(pas), (j + pas + 1).min(nx));
            let (mut best, mut bi, mut bj) = (f64::NEG_INFINITY, 0usize, 0usize);
            let mut premier = true;
            for a in i0..i1 {
                for b in j0..j1 {
                    let x = g[a * nx + b];
                    let x = if x.is_finite() { x } else { f64::NEG_INFINITY };
                    if premier || x > best {
                        best = x;
                        bi = a;
                        bj = b;
                        premier = false;
                    }
                }
            }
            liste.push(Extremum { ligne: bi, colonne: bj, valeur: signe * best, proeminence: pr });
        }
        liste.sort_by(|a, b| (-a.proeminence).partial_cmp(&(-b.proeminence)).unwrap());
        sortie.push(liste);
    }
    let mn = sortie.pop().unwrap();
    let mx = sortie.pop().unwrap();
    (mx, mn)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn gauss(ny: usize, nx: usize, centres: &[(f64, f64, f64, f64)]) -> Vec<f64> {
        let mut f = vec![1000.0; ny * nx];
        for i in 0..ny {
            for j in 0..nx {
                for &(ci, cj, h, s) in centres {
                    let d2 = (i as f64 - ci).powi(2) + (j as f64 - cj).powi(2);
                    f[i * nx + j] += h * (-d2 / (2.0 * s * s)).exp();
                }
            }
        }
        f
    }

    #[test]
    fn un_anticyclone_et_une_depression() {
        let (ny, nx) = (90, 120);
        let f = gauss(ny, nx, &[(40.0, 40.0, 12.0, 8.0), (50.0, 85.0, -9.0, 8.0)]);
        let (mx, mn) = detecter(&f, ny, nx, 1.0, 3);
        assert_eq!(mx.len(), 1);
        assert_eq!(mn.len(), 1);
        assert_eq!((mx[0].ligne, mx[0].colonne), (40, 40));
        assert_eq!((mn[0].ligne, mn[0].colonne), (50, 85));
        assert!(mx[0].valeur > 1011.0 && mn[0].valeur < 991.5);
    }

    #[test]
    fn maximum_au_bord_du_domaine_ecarte() {
        let (ny, nx) = (60, 60);
        let mut f = gauss(ny, nx, &[(30.0, 30.0, 6.0, 6.0), (30.0, 59.0, 20.0, 6.0)]);
        for i in 0..ny {
            f[i * nx + 59] = f64::NAN; // masque la colonne du bord
        }
        let (mx, _) = detecter(&f, ny, nx, 1.0, 3);
        assert!(mx.iter().all(|e| e.colonne < 55), "{mx:?}");
    }

    #[test]
    fn seuil_de_proeminence() {
        let (ny, nx) = (90, 90);
        let f = gauss(ny, nx, &[(45.0, 45.0, 12.0, 8.0), (45.0, 70.0, 0.4, 3.0)]);
        let (mx, _) = detecter(&f, ny, nx, 1.0, 3);
        assert_eq!(mx.len(), 1);
    }
}
