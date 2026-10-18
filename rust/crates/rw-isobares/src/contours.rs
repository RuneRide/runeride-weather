// SPDX-License-Identifier: EUPL-1.2
//! Marching squares, assemblage en lignes, géométrie, Douglas-Peucker (port de `pipeline/isobars/contours.py`).
//!
//! Convention : champ [ny, nx] en ligne majeure ; positions de sommets en coordonnées d'INDEX
//! (ligne, colonne) flottantes ; l'ordre des segments et des nœuds est celui du Python (le sens et le point
//! de départ des anneaux en dépendent).

use crate::lissage::somme_numpy;

const T: i8 = 0;
const R: i8 = 1;
const B: i8 = 2;
const L: i8 = 3;
const ABS: (i8, i8) = (-1, -1);

/// `TABLE[variante][cas]` = deux segments (paires d'arêtes). Variante 0 : centre sous le niveau ; 1 : au-dessus.
fn table() -> [[[(i8, i8); 2]; 16]; 2] {
    let mut t = [[[ABS; 2]; 16]; 2];
    let base: [(usize, (i8, i8)); 12] = [
        (1, (L, T)),
        (2, (T, R)),
        (3, (L, R)),
        (4, (R, B)),
        (6, (T, B)),
        (7, (L, B)),
        (8, (B, L)),
        (9, (T, B)),
        (11, (R, B)),
        (12, (L, R)),
        (13, (T, R)),
        (14, (L, T)),
    ];
    for v in 0..2 {
        for &(c, s) in &base {
            t[v][c][0] = s;
        }
    }
    t[1][5] = [(T, R), (B, L)];
    t[0][5] = [(L, T), (R, B)];
    t[1][10] = [(L, T), (R, B)];
    t[0][10] = [(T, R), (B, L)];
    t
}

/// Cellule [ny-1, nx-1] valide si ses quatre coins le sont ; `marge` érosions supplémentaires (le bord de la
/// grille n'érode pas).
pub fn cellules_valides(valide: &[bool], ny: usize, nx: usize, marge: usize) -> Vec<bool> {
    let (cy, cx) = (ny - 1, nx - 1);
    let mut v = vec![false; cy * cx];
    for i in 0..cy {
        for j in 0..cx {
            v[i * cx + j] = valide[i * nx + j] && valide[i * nx + j + 1] && valide[(i + 1) * nx + j + 1] && valide[(i + 1) * nx + j];
        }
    }
    for _ in 0..marge {
        let src = v.clone();
        for i in 0..cy {
            for j in 0..cx {
                let h = i == 0 || src[(i - 1) * cx + j];
                let b = i + 1 == cy || src[(i + 1) * cx + j];
                let g = j == 0 || src[i * cx + j - 1];
                let d = j + 1 == cx || src[i * cx + j + 1];
                v[i * cx + j] = src[i * cx + j] && h && b && g && d;
            }
        }
    }
    v
}

fn id_arete(lettre: i8, i: usize, j: usize, ny: usize, nx: usize) -> u64 {
    let (i, j, ny, nx) = (i as u64, j as u64, ny as u64, nx as u64);
    match lettre {
        T => i * nx + j,
        B => (i + 1) * nx + j,
        L => ny * nx + i * nx + j,
        _ => ny * nx + i * nx + j + 1,
    }
}

/// Segments d'un niveau : identifiants d'arêtes (e0, e1), dans l'ordre du Python (tous les premiers segments
/// des cellules en ordre ligne majeure, puis tous les seconds).
pub fn segments(f: &[f64], ny: usize, nx: usize, niveau: f64, cell_ok: &[bool], tab: &[[[(i8, i8); 2]; 16]; 2]) -> (Vec<u64>, Vec<u64>) {
    let cx = nx - 1;
    let mut e0 = [Vec::new(), Vec::new()];
    let mut e1 = [Vec::new(), Vec::new()];
    for i in 0..ny - 1 {
        for j in 0..cx {
            if !cell_ok[i * cx + j] {
                continue;
            }
            let (a, b, c, d) = (f[i * nx + j], f[i * nx + j + 1], f[(i + 1) * nx + j + 1], f[(i + 1) * nx + j]);
            let cas = (a >= niveau) as usize | ((b >= niveau) as usize) << 1 | ((c >= niveau) as usize) << 2 | ((d >= niveau) as usize) << 3;
            if cas == 0 || cas == 15 {
                continue;
            }
            let centre = 0.25 * (((a + b) + c) + d);
            let var = (centre >= niveau) as usize;
            for s in 0..2 {
                let (p, q) = tab[var][cas][s];
                if p >= 0 {
                    e0[s].push(id_arete(p, i, j, ny, nx));
                    e1[s].push(id_arete(q, i, j, ny, nx));
                }
            }
        }
    }
    let [a0, a1] = e0;
    let [b0, b1] = e1;
    (a0.into_iter().chain(a1).collect(), b0.into_iter().chain(b1).collect())
}

/// Position (ligne, colonne) du croisement de `niveau` sur l'arête `id` (interpolation linéaire).
fn position_noeud(f: &[f64], ny: usize, nx: usize, niveau: f64, id: u64) -> (f64, f64) {
    let base = (ny * nx) as u64;
    let horiz = id < base;
    let k = if horiz { id } else { id - base } as usize;
    let (i, j) = (k / nx, k % nx);
    let (i2, j2) = if horiz { (i, j + 1) } else { (i + 1, j) };
    let (a, b) = (f[i * nx + j], f[i2 * nx + j2]);
    let t = (niveau - a) / (b - a);
    if horiz {
        (i as f64, j as f64 + t)
    } else {
        (i as f64 + t, j as f64)
    }
}

/// Assemble des segments en lignes continues : suites d'identifiants de nœuds et indicateur « fermée »
/// (anneau : premier = dernier). Mêmes règles d'ordre que `contours.assembler`.
pub fn assembler(e0: &[u64], e1: &[u64]) -> Result<Vec<(Vec<u64>, bool)>, String> {
    let n_seg = e0.len();
    if n_seg == 0 {
        return Ok(Vec::new());
    }
    let mut noeuds: Vec<u64> = e0.iter().chain(e1).copied().collect();
    noeuds.sort_unstable();
    noeuds.dedup();
    let n = noeuds.len();
    let idx = |x: u64| noeuds.binary_search(&x).unwrap();
    // entrées dans l'ordre de concaténation [a, b] (src) puis [b, a] (dst) ; tri stable par src
    let mut deg = vec![0usize; n];
    let a: Vec<usize> = e0.iter().map(|&x| idx(x)).collect();
    let b: Vec<usize> = e1.iter().map(|&x| idx(x)).collect();
    for s in 0..n_seg {
        deg[a[s]] += 1;
        deg[b[s]] += 1;
    }
    if deg.iter().any(|&d| d > 2) {
        return Err("nœud de degré > 2 : segments incohérents".into());
    }
    let mut v0 = vec![-1i64; n];
    let mut v1 = vec![-1i64; n];
    let mut rempli = vec![0u8; n];
    let mut poser = |s: usize, d: usize| {
        if rempli[s] == 0 {
            v0[s] = d as i64;
        } else {
            v1[s] = d as i64;
        }
        rempli[s] += 1;
    };
    // src = [a..., b...] : d'abord les segments où le nœud est en tête, puis ceux où il est en queue
    for s in 0..n_seg {
        poser(a[s], b[s]);
    }
    for s in 0..n_seg {
        poser(b[s], a[s]);
    }
    let mut vu = vec![false; n];
    let mut lignes = Vec::new();
    let marcher = |depart: usize, vu: &mut Vec<bool>| -> (Vec<usize>, bool) {
        let mut chaine = vec![depart];
        vu[depart] = true;
        let (mut prec, mut cur) = (-1i64, depart);
        loop {
            let nxt = if v0[cur] != prec { v0[cur] } else { v1[cur] };
            if nxt < 0 || nxt as usize == depart {
                return (chaine, nxt >= 0);
            }
            let nu = nxt as usize;
            if vu[nu] {
                return (chaine, false);
            }
            vu[nu] = true;
            chaine.push(nu);
            prec = cur as i64;
            cur = nu;
        }
    };
    for d in 0..n {
        if deg[d] == 1 && !vu[d] {
            let (ch, _) = marcher(d, &mut vu);
            lignes.push((ch.iter().map(|&k| noeuds[k]).collect(), false));
        }
    }
    for d in 0..n {
        if !vu[d] {
            let (mut ch, ferme) = marcher(d, &mut vu);
            if ferme {
                ch.push(d);
            }
            lignes.push((ch.iter().map(|&k| noeuds[k]).collect(), ferme));
        }
    }
    Ok(lignes)
}

/// Lignes de niveau `niveau` : (points (ligne, colonne), fermée).
pub fn contour(f: &[f64], ny: usize, nx: usize, niveau: f64, cell_ok: &[bool], tab: &[[[(i8, i8); 2]; 16]; 2]) -> Result<Vec<(Vec<(f64, f64)>, bool)>, String> {
    let (e0, e1) = segments(f, ny, nx, niveau, cell_ok, tab);
    Ok(assembler(&e0, &e1)?
        .into_iter()
        .map(|(ids, ferme)| (ids.iter().map(|&id| position_noeud(f, ny, nx, niveau, id)).collect(), ferme))
        .collect())
}

pub fn table_ms() -> [[[(i8, i8); 2]; 16]; 2] {
    table()
}

/// Niveaux (multiples de `pas`) couverts par le champ (équivalent de `np.arange(ceil(lo/pas)*pas, floor(hi/pas)*pas + pas/2, pas)`).
pub fn niveaux_hpa(f: &[f64], pas: f64) -> Vec<f64> {
    let (mut lo, mut hi) = (f64::INFINITY, f64::NEG_INFINITY);
    for &v in f {
        if v.is_finite() {
            lo = lo.min(v);
            hi = hi.max(v);
        }
    }
    if !lo.is_finite() {
        return Vec::new();
    }
    let debut = (lo / pas).ceil() * pas;
    let fin = (hi / pas).floor() * pas + pas / 2.0;
    let n = ((fin - debut) / pas).ceil();
    if n <= 0.0 {
        return Vec::new();
    }
    let delta = (debut + pas) - debut;
    (0..n as usize).map(|i| if i == 0 { debut } else { debut + i as f64 * delta }).collect()
}

// ----------------------------------------------------------------------------- géométrie

/// (ligne, colonne) flottants -> (lon, lat) par interpolation linéaire des axes (comme `np.interp`).
pub fn interp_axe(x: f64, axe: &[f64]) -> f64 {
    let n = axe.len();
    if x.is_nan() {
        return x;
    }
    if x < 0.0 {
        return axe[0];
    }
    if x > (n - 1) as f64 {
        return axe[n - 1];
    }
    let j = x.floor() as usize;
    if j >= n - 1 {
        return axe[n - 1];
    }
    let pente = (axe[j + 1] - axe[j]) / 1.0;
    let r = pente * (x - j as f64) + axe[j];
    if r.is_nan() {
        let r2 = pente * (x - (j + 1) as f64) + axe[j + 1];
        if r2.is_nan() && axe[j] == axe[j + 1] {
            return axe[j];
        }
        return r2;
    }
    r
}

pub fn vers_lonlat(pts: &[(f64, f64)], lat: &[f64], lon: &[f64]) -> Vec<[f64; 2]> {
    pts.iter().map(|&(r, c)| [interp_axe(c, lon), interp_axe(r, lat)]).collect()
}

const LAT0: f64 = 46.5;

/// (lon, lat) -> plan local en km (équirectangulaire centré sur 46,5°).
pub fn vers_km(ll: &[[f64; 2]]) -> Vec<[f64; 2]> {
    let k = std::f64::consts::PI / 180.0 * 6371.0;
    let c = (LAT0 * (std::f64::consts::PI / 180.0)).cos();
    ll.iter().map(|p| [p[0] * k * c, p[1] * k]).collect()
}

pub fn longueur_km(ll: &[[f64; 2]]) -> f64 {
    if ll.len() < 2 {
        return 0.0;
    }
    let p = vers_km(ll);
    let d: Vec<f64> = p.windows(2).map(|w| (w[1][0] - w[0][0]).hypot(w[1][1] - w[0][1])).collect();
    somme_numpy(&d)
}

/// Douglas-Peucker itératif ; renvoie les indices gardés (triés).
pub fn douglas_peucker(p: &[[f64; 2]], tol: f64) -> Vec<usize> {
    let n = p.len();
    if n <= 2 || tol <= 0.0 {
        return (0..n).collect();
    }
    let mut garde = vec![false; n];
    garde[0] = true;
    garde[n - 1] = true;
    let mut pile = vec![(0usize, n - 1)];
    while let Some((a, b)) = pile.pop() {
        if b - a < 2 {
            continue;
        }
        let sx = p[b][0] - p[a][0];
        let sy = p[b][1] - p[a][1];
        let l = sx.hypot(sy);
        let (mut kmax, mut dmax) = (0usize, f64::NEG_INFINITY);
        for q in a + 1..b {
            let mx = p[q][0] - p[a][0];
            let my = p[q][1] - p[a][1];
            let d = if l == 0.0 { mx.hypot(my) } else { (sx * my - sy * mx).abs() / l };
            if d > dmax {
                dmax = d;
                kmax = q - (a + 1);
            }
        }
        if dmax > tol {
            let m = a + 1 + kmax;
            garde[m] = true;
            pile.push((a, m));
            pile.push((m, b));
        }
    }
    (0..n).filter(|&i| garde[i]).collect()
}

/// Simplifie une ligne (lon, lat) ; un anneau est coupé au point le plus éloigné du départ.
pub fn simplifier(ll: Vec<[f64; 2]>, ferme: bool, tol_km: f64) -> Vec<[f64; 2]> {
    if tol_km <= 0.0 || ll.len() <= 3 {
        return ll;
    }
    let p = vers_km(&ll);
    let proche = |a: [f64; 2], b: [f64; 2]| (a[0] - b[0]).abs() <= 1e-8 + 1e-5 * b[0].abs() && (a[1] - b[1]).abs() <= 1e-8 + 1e-5 * b[1].abs();
    let idx: Vec<usize> = if ferme && proche(ll[0], ll[ll.len() - 1]) {
        let (mut k, mut dmax) = (0usize, f64::NEG_INFINITY);
        for (q, pt) in p.iter().enumerate() {
            let d = (pt[0] - p[0][0]).hypot(pt[1] - p[0][1]);
            if d > dmax {
                dmax = d;
                k = q;
            }
        }
        let mut i1 = douglas_peucker(&p[..k + 1], tol_km);
        let i2 = douglas_peucker(&p[k..], tol_km);
        i1.extend(i2.into_iter().skip(1).map(|i| i + k));
        i1
    } else {
        douglas_peucker(&p, tol_km)
    };
    idx.into_iter().map(|i| ll[i]).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn champ(ny: usize, nx: usize, f: impl Fn(f64, f64) -> f64) -> Vec<f64> {
        (0..ny * nx).map(|k| f((k / nx) as f64, (k % nx) as f64)).collect()
    }

    fn contours(f: &[f64], ny: usize, nx: usize, niv: f64) -> Vec<(Vec<(f64, f64)>, bool)> {
        let valide: Vec<bool> = f.iter().map(|v| v.is_finite()).collect();
        let ok = cellules_valides(&valide, ny, nx, 0);
        contour(f, ny, nx, niv, &ok, &table()).unwrap()
    }

    #[test]
    fn plan_incline_ligne_droite_exacte() {
        // F = colonne : le niveau 10,5 est la droite colonne = 10,5, ouverte, de haut en bas
        let (ny, nx) = (8, 20);
        let f = champ(ny, nx, |_, c| c);
        let l = contours(&f, ny, nx, 10.5);
        assert_eq!(l.len(), 1);
        assert!(!l[0].1);
        assert_eq!(l[0].0.len(), ny);
        for &(_, c) in &l[0].0 {
            assert!((c - 10.5).abs() < 1e-12);
        }
    }

    #[test]
    fn cercle_anneau_ferme_de_bon_rayon() {
        let (ny, nx) = (41, 41);
        let f = champ(ny, nx, |i, j| ((i - 20.0).powi(2) + (j - 20.0).powi(2)).sqrt());
        let l = contours(&f, ny, nx, 9.3);
        assert_eq!(l.len(), 1);
        let (pts, ferme) = &l[0];
        assert!(*ferme);
        assert_eq!(pts.first(), pts.last());
        for &(i, j) in pts {
            let r = ((i - 20.0).powi(2) + (j - 20.0).powi(2)).sqrt();
            assert!((r - 9.3).abs() < 0.05, "r = {r}");
        }
    }

    #[test]
    fn nan_arrete_la_ligne_sans_fausse_ligne_de_bord() {
        let (ny, nx) = (10, 20);
        let mut f = champ(ny, nx, |_, c| c);
        for i in 0..ny {
            for j in 15..nx {
                f[i * nx + j] = f64::NAN;
            }
        }
        // niveau 16 : dans la zone masquée, aucune ligne
        assert!(contours(&f, ny, nx, 16.5).is_empty());
        // niveau 13,5 : ligne ouverte, aucune ligne le long du masque
        let l = contours(&f, ny, nx, 13.5);
        assert_eq!(l.len(), 1);
        assert!(l[0].0.iter().all(|&(_, c)| (c - 13.5).abs() < 1e-12));
    }

    #[test]
    fn selle_tranchee_par_la_moyenne_des_coins() {
        // 2x2 : coins hauts en (0,0) et (1,1) ; centre haut -> les coins bas sont isoles, deux segments
        let f = vec![10.0, 0.0, 0.0, 10.0];
        let l = contours(&f, 2, 2, 4.0);
        assert_eq!(l.len(), 2);
        // centre bas (coins hauts valent 6 > 4 mais moyenne 3 < 4) : les coins hauts sont isoles
        let f = vec![6.0, 0.0, 0.0, 6.0];
        let l2 = contours(&f, 2, 2, 4.0);
        assert_eq!(l2.len(), 2);
        let milieu = |l: &Vec<(Vec<(f64, f64)>, bool)>| l.iter().map(|(p, _)| p.iter().map(|q| q.0 + q.1).sum::<f64>() / p.len() as f64).fold(0.0, f64::max);
        assert!(milieu(&l) != milieu(&l2) || l != l2);
    }

    #[test]
    fn marge_erode_les_cellules() {
        let (ny, nx) = (6, 6);
        let mut v = vec![true; ny * nx];
        v[2 * nx + 2] = false;
        let c0 = cellules_valides(&v, ny, nx, 0);
        let c1 = cellules_valides(&v, ny, nx, 1);
        assert!(c0.iter().filter(|&&x| !x).count() == 4);
        assert!(c1.iter().filter(|&&x| !x).count() > 4);
        assert!(c1[0]); // le bord de la grille n'érode pas
    }

    #[test]
    fn niveaux_comme_arange() {
        let f = vec![1003.1, 1010.9];
        assert_eq!(niveaux_hpa(&f, 2.0), vec![1004.0, 1006.0, 1008.0, 1010.0]);
        assert_eq!(niveaux_hpa(&[1004.0, 1010.0], 2.0), vec![1004.0, 1006.0, 1008.0, 1010.0]);
        assert!(niveaux_hpa(&[f64::NAN], 2.0).is_empty());
    }

    #[test]
    fn dp_garde_extremites_et_pics() {
        let p: Vec<[f64; 2]> = vec![[0.0, 0.0], [1.0, 0.001], [2.0, 0.0], [3.0, 1.0], [4.0, 0.0]];
        assert_eq!(douglas_peucker(&p, 0.1), vec![0, 2, 3, 4]);
        assert_eq!(douglas_peucker(&p, 0.7), vec![0, 3, 4]);
        assert_eq!(douglas_peucker(&p, 0.0), vec![0, 1, 2, 3, 4]);
        assert_eq!(douglas_peucker(&p, 5.0), vec![0, 4]);
    }

    #[test]
    fn dp_droite_se_reduit_a_deux_points() {
        let p: Vec<[f64; 2]> = (0..100).map(|i| [i as f64, 2.0 * i as f64]).collect();
        assert_eq!(douglas_peucker(&p, 1e-9), vec![0, 99]);
    }

    #[test]
    fn dp_distance_au_segment_respecte_la_tolerance() {
        let p: Vec<[f64; 2]> = (0..=200).map(|i| { let t = i as f64 / 200.0 * std::f64::consts::PI; [t.cos() * 10.0, t.sin() * 10.0] }).collect();
        let tol = 0.05;
        let idx = douglas_peucker(&p, tol);
        assert!(idx.len() < p.len() / 2);
        for w in idx.windows(2) {
            let (a, b) = (p[w[0]], p[w[1]]);
            let l = (b[0] - a[0]).hypot(b[1] - a[1]);
            for q in w[0]..=w[1] {
                let d = ((b[0] - a[0]) * (p[q][1] - a[1]) - (b[1] - a[1]) * (p[q][0] - a[0])).abs() / l;
                assert!(d <= tol + 1e-12);
            }
        }
    }

    #[test]
    fn simplifier_anneau_garde_la_fermeture() {
        let n = 400;
        let ll: Vec<[f64; 2]> = (0..=n).map(|i| { let t = i as f64 / n as f64 * 2.0 * std::f64::consts::PI; [2.0 + 0.3 * t.cos(), 46.0 + 0.3 * t.sin()] }).collect();
        let s = simplifier(ll.clone(), true, 0.1);
        assert!(s.len() < 60 && s.len() > 8);
        assert_eq!(s.first(), s.last());
    }

    #[test]
    fn interp_comme_numpy() {
        let lat = [55.4, 55.375, 55.35];
        assert_eq!(interp_axe(0.0, &lat), 55.4);
        assert_eq!(interp_axe(2.0, &lat), 55.35);
        assert!((interp_axe(0.5, &lat) - 55.3875).abs() < 1e-12);
    }

    #[test]
    fn assemblage_degre_trois_refuse() {
        assert!(assembler(&[1, 1, 1], &[2, 3, 4]).is_err());
    }
}
