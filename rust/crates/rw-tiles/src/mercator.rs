// SPDX-License-Identifier: EUPL-1.2
//! Géométrie Web Mercator XYZ et rééchantillonnage bilinéaire (port de `mercator.py`).

pub const TUILE: usize = 256;
pub const BORDURE: usize = 1;
pub const STOCKEE: usize = TUILE + 2 * BORDURE;

/// Formule XYZ standard : tuile contenant le point.
pub fn lonlat_vers_tuile(lon: f64, lat: f64, z: u32) -> (u32, u32) {
    let n = (1u64 << z) as f64;
    let x = ((lon + 180.0) / 360.0 * n).floor();
    let lr = lat.to_radians();
    let y = ((1.0 - lr.tan().asinh() / std::f64::consts::PI) / 2.0 * n).floor();
    let borne = |v: f64| v.max(0.0).min(n - 1.0) as u32;
    (borne(x), borne(y))
}

/// Tuiles (x, y) du zoom `z` recoupant l'emprise (bornes incluses), ordre ligne par ligne.
pub fn tuiles_couvrantes(lon_min: f64, lat_min: f64, lon_max: f64, lat_max: f64, z: u32) -> Vec<(u32, u32)> {
    let (x0, y1) = lonlat_vers_tuile(lon_min, lat_min, z);
    let (x1, y0) = lonlat_vers_tuile(lon_max, lat_max, z);
    let mut v = Vec::new();
    for y in y0..=y1 {
        for x in x0..=x1 {
            v.push((x, y));
        }
    }
    v
}

/// Longitudes et latitudes des centres de pixels d'une tuile avec bordure (258 valeurs chacune).
pub fn lonlat_pixels(z: u32, x: u32, y: u32, bordure: usize) -> (Vec<f64>, Vec<f64>) {
    let n = ((1u64 << z) as usize * TUILE) as f64;
    let taille = TUILE + 2 * bordure;
    let mut lon = Vec::with_capacity(taille);
    let mut lat = Vec::with_capacity(taille);
    for k in 0..taille {
        let i = k as f64 - bordure as f64 + 0.5;
        let gx = ((x as usize * TUILE) as f64 + i) / n;
        let gy = ((y as usize * TUILE) as f64 + i) / n;
        lon.push(gx * 360.0 - 180.0);
        lat.push((std::f64::consts::PI * (1.0 - 2.0 * gy)).sinh().atan().to_degrees());
    }
    (lon, lat)
}

/// Grille régulière lat (décroissante) / lon (croissante).
#[derive(Debug, Clone)]
pub struct Grille {
    pub lat: Vec<f64>,
    pub lon: Vec<f64>,
    pub dlat: f64,
    pub dlon: f64,
    /// (lon_min, lat_min, lon_max, lat_max)
    pub emprise: (f64, f64, f64, f64),
}

/// Poids bilinéaires le long d'un axe : indice inférieur, fraction, validité (dans l'emprise).
#[derive(Debug, Clone, Default)]
pub struct Axe {
    pub i0: Vec<usize>,
    pub frac: Vec<f64>,
    pub valide: Vec<bool>,
}

#[derive(Debug, thiserror::Error)]
#[error("grille non régulière ou trop petite")]
pub struct GrilleIrreguliere;

impl Grille {
    pub fn new(lat: &[f32], lon: &[f32]) -> Result<Self, GrilleIrreguliere> {
        if lat.len() < 2 || lon.len() < 2 {
            return Err(GrilleIrreguliere);
        }
        let lat: Vec<f64> = lat.iter().map(|&v| v as f64).collect();
        let lon: Vec<f64> = lon.iter().map(|&v| v as f64).collect();
        let dlat = (lat[lat.len() - 1] - lat[0]) / (lat.len() - 1) as f64;
        let dlon = (lon[lon.len() - 1] - lon[0]) / (lon.len() - 1) as f64;
        let irreg = |a: &[f64], d: f64| a.windows(2).any(|w| ((w[1] - w[0]) - d).abs() > 1e-4 * d.abs() + 1e-5);
        if irreg(&lat, dlat) || irreg(&lon, dlon) {
            return Err(GrilleIrreguliere);
        }
        let emprise = (lon[0], lat[lat.len() - 1], lon[lon.len() - 1], lat[0]);
        Ok(Self { lat, lon, dlat, dlon, emprise })
    }

    pub fn nlat(&self) -> usize {
        self.lat.len()
    }
    pub fn nlon(&self) -> usize {
        self.lon.len()
    }

    fn axe(&self, coords: &[f64], origine: f64, pas: f64, n: usize) -> Axe {
        let eps = 1e-9;
        let mut a = Axe::default();
        for &c in coords {
            let f = (c - origine) / pas;
            let valide = f >= -eps && f <= (n - 1) as f64 + eps;
            let f = f.max(0.0).min((n - 1) as f64);
            let i0 = (f.floor() as usize).min(n - 2);
            a.i0.push(i0);
            a.frac.push(f - i0 as f64);
            a.valide.push(valide);
        }
        a
    }

    /// Poids des lignes (latitudes) puis des colonnes (longitudes).
    pub fn poids(&self, lon: &[f64], lat: &[f64]) -> (Axe, Axe) {
        (self.axe(lat, self.lat[0], self.dlat, self.nlat()), self.axe(lon, self.lon[0], self.dlon, self.nlon()))
    }

    /// Bilinéaire de `champ` (`nlat * nlon`) sur le produit cartésien ; NaN hors emprise ou si un voisin est NaN.
    /// Même ordre d'opérations que le Python : calcul en f64 puis arrondi f32.
    pub fn echantillonner(&self, champ: &[f32], lignes: &Axe, colonnes: &Axe, sortie: &mut [f32]) {
        let nlon = self.nlon();
        let w = colonnes.i0.len();
        for (r, (&j0, &fy)) in lignes.i0.iter().zip(lignes.frac.iter()).enumerate() {
            let vy = lignes.valide[r];
            let l0 = &champ[j0 * nlon..(j0 + 1) * nlon];
            let l1 = &champ[(j0 + 1) * nlon..(j0 + 2) * nlon];
            let sortie_l = &mut sortie[r * w..(r + 1) * w];
            for c in 0..w {
                if !(vy && colonnes.valide[c]) {
                    sortie_l[c] = f32::NAN;
                    continue;
                }
                let i0 = colonnes.i0[c];
                let fx = colonnes.frac[c];
                let (a, b, cc, d) = (l0[i0], l0[i0 + 1], l1[i0], l1[i0 + 1]);
                if a.is_nan() || b.is_nan() || cc.is_nan() || d.is_nan() {
                    sortie_l[c] = f32::NAN;
                    continue;
                }
                let (a, b, cc, d) = (a as f64, b as f64, cc as f64, d as f64);
                let v = (a * (1.0 - fx) + b * fx) * (1.0 - fy) + (cc * (1.0 - fx) + d * fx) * fy;
                sortie_l[c] = v as f32;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tuile_de_paris() {
        // Paris (2,35 E, 48,85 N) au zoom 8 : tuile 129/88 (grille XYZ standard)
        assert_eq!(lonlat_vers_tuile(2.35, 48.85, 8), (129, 88));
    }

    #[test]
    fn pixels_centres() {
        let (lon, lat) = lonlat_pixels(0, 0, 0, 1);
        assert_eq!(lon.len(), 258);
        // centre du pixel 128 (indice 129 avec bordure) au zoom 0 : proche de 0 degré
        assert!((lon[128] - (-180.0 + 360.0 * 127.5 / 256.0)).abs() < 1e-9);
        assert!(lat[0] > 85.0);
    }

    #[test]
    fn bilineaire() {
        let lat = [2.0f32, 1.0, 0.0];
        let lon = [0.0f32, 1.0, 2.0];
        let g = Grille::new(&lat, &lon).unwrap();
        // champ = 10*ligne + colonne
        let ch: Vec<f32> = (0..9).map(|k| (10 * (k / 3) + k % 3) as f32).collect();
        let (l, c) = g.poids(&[0.5, 2.5, 2.0], &[1.5, 1.5, 0.0]);
        let mut s = vec![0f32; 9];
        g.echantillonner(&ch, &l, &c, &mut s);
        assert!((s[0] - 5.5).abs() < 1e-6); // lat 1,5 : ligne 0,5 ; lon 0,5 : colonne 0,5
        assert!(s[1].is_nan()); // lon 2,5 hors emprise
        assert!((s[2] - 7.0).abs() < 1e-6); // lon 2 (bord) valide
        assert!((s[8] - 22.0).abs() < 1e-6); // lat 0, lon 2
    }

    #[test]
    fn voisin_nan() {
        let g = Grille::new(&[1.0, 0.0], &[0.0, 1.0]).unwrap();
        let (l, c) = g.poids(&[0.5], &[0.5]);
        let mut s = [0f32; 1];
        g.echantillonner(&[1.0, f32::NAN, 3.0, 4.0], &l, &c, &mut s);
        assert!(s[0].is_nan());
    }
}
