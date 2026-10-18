// SPDX-License-Identifier: EUPL-1.2
//! Quantification (règle du contrat) et encodage WebP sans perte, sans alpha (port de `encode.py`).

use crate::mercator::STOCKEE;

/// Échelle d'une variable scalaire.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Echelle {
    Lineaire,
    Racine,
}

impl Echelle {
    pub fn nom(self) -> &'static str {
        match self {
            Echelle::Lineaire => "linear",
            Echelle::Racine => "sqrt",
        }
    }
}

/// Variable scalaire 8 bits : (nom, min, max, échelle, unité) de la table du contrat.
pub const SCALAIRES: [(&str, f64, f64, Echelle, &str); 7] = [
    ("t2m", 233.15, 333.15, Echelle::Lineaire, "K"),
    ("gust10", 0.0, 64.0, Echelle::Lineaire, "m/s"),
    ("rh2m", 0.0, 100.0, Echelle::Lineaire, "%"),
    ("cloud_total", 0.0, 100.0, Echelle::Lineaire, "%"),
    ("snow_fraction", 0.0, 1.0, Echelle::Lineaire, "1"),
    ("precip_rate", 0.0, 100.0, Echelle::Racine, "mm/h"),
    ("cape", 0.0, 5000.0, Echelle::Racine, "J/kg"),
];

pub const VENT: &str = "wind10";
/// Niveaux par défaut du contrat (codes 1..255).
pub const NIVEAUX: u32 = 254;

/// Pas de quantification (unité physique) ; pour la racine : pas maximal, en haut de plage.
pub fn pas_scalaire(min: f64, max: f64, echelle: Echelle, niveaux: u32) -> f64 {
    let l = niveaux as f64;
    match echelle {
        Echelle::Lineaire => (max - min) / l,
        Echelle::Racine => max * (1.0 - ((l - 1.0) / l).powi(2)),
    }
}

pub fn pas_vent8(vmax: f64) -> f64 {
    2.0 * vmax / 254.0
}

/// f32 → u8 ; NaN ou infini → 0 ; 1..=niveaux+1 sinon.
pub fn quantifier_scalaire(valeurs: &[f32], min: f64, max: f64, echelle: Echelle, niveaux: u32, sortie: &mut [u8]) {
    let l = niveaux as f64;
    for (o, &v) in sortie.iter_mut().zip(valeurs) {
        let v = v as f64;
        if !v.is_finite() {
            *o = 0;
            continue;
        }
        let mut f = ((v - min) / (max - min)).clamp(0.0, 1.0);
        if echelle == Echelle::Racine {
            f = f.sqrt();
        }
        *o = (1.0 + (f * l).round_ties_even()).clamp(1.0, l + 1.0) as u8;
    }
}

/// u8 → f32 (décodeur de référence) ; 0 → NaN.
pub fn dequantifier_scalaire(code: u8, min: f64, max: f64, echelle: Echelle, niveaux: u32) -> f32 {
    if code == 0 {
        return f32::NAN;
    }
    let mut f = (code as f64 - 1.0) / niveaux as f64;
    if echelle == Echelle::Racine {
        f *= f;
    }
    (min + f * (max - min)) as f32
}

/// Vent 8 bits : u dans R, v dans G, B = 0 ; NaN dans u ou v → (0, 0, 0). Sortie RVB entrelacée.
pub fn quantifier_vent8(u: &[f32], v: &[f32], vmax: f64, sortie: &mut [u8]) {
    for (k, (&uu, &vv)) in u.iter().zip(v).enumerate() {
        let (uu, vv) = (uu as f64, vv as f64);
        let px = &mut sortie[3 * k..3 * k + 3];
        if !(uu.is_finite() && vv.is_finite()) {
            px.fill(0);
            continue;
        }
        let code = |x: f64| (1.0 + ((x + vmax) / (2.0 * vmax) * 254.0).round_ties_even()).clamp(1.0, 255.0) as u8;
        px[0] = code(uu);
        px[1] = code(vv);
        px[2] = 0;
    }
}

/// Implémentation WebP.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WebpBackend {
    /// libwebp (C), mêmes algorithmes que Pillow : poids identiques au Python.
    #[cfg(feature = "webp-c")]
    LibWebp,
    /// `image-webp` (Rust pur) : beaucoup plus rapide, fichiers plus gros.
    #[cfg(feature = "webp-pure")]
    ImageWebp,
}

impl Default for WebpBackend {
    #[allow(unreachable_code)]
    fn default() -> Self {
        #[cfg(feature = "webp-c")]
        return WebpBackend::LibWebp;
        #[cfg(feature = "webp-pure")]
        return WebpBackend::ImageWebp;
    }
}

impl std::str::FromStr for WebpBackend {
    type Err = String;
    fn from_str(s: &str) -> Result<Self, String> {
        match s {
            #[cfg(feature = "webp-c")]
            "libwebp" => Ok(WebpBackend::LibWebp),
            #[cfg(feature = "webp-pure")]
            "image-webp" => Ok(WebpBackend::ImageWebp),
            _ => Err(format!("encodeur WebP inconnu ou non compilé : {s}")),
        }
    }
}

/// Image de tuile prête à encoder : niveaux de gris (1 canal) ou RVB (3 canaux), 258 × 258.
pub enum Image<'a> {
    Gris(&'a [u8]),
    Rvb(&'a [u8]),
}

/// WebP sans perte et sans alpha. Les niveaux de gris sont stockés en RVB (R = V = B), comme Pillow.
/// `methode` : effort libwebp (4 pour les scalaires, 3 pour le vent, comme le Python).
#[allow(unused_variables)]
pub fn vers_webp(img: &Image<'_>, backend: WebpBackend, methode: i32) -> Result<Vec<u8>, String> {
    let rvb_stocke: Vec<u8>;
    let rvb: &[u8] = match img {
        Image::Rvb(d) => d,
        Image::Gris(g) => {
            rvb_stocke = g.iter().flat_map(|&p| [p, p, p]).collect();
            &rvb_stocke
        }
    };
    debug_assert_eq!(rvb.len(), STOCKEE * STOCKEE * 3);
    match backend {
        #[cfg(feature = "webp-c")]
        WebpBackend::LibWebp => {
            let mut cfg = webp::WebPConfig::new().map_err(|_| "configuration libwebp".to_string())?;
            cfg.lossless = 1;
            cfg.quality = 100.0;
            cfg.method = methode;
            cfg.exact = 1;
            let m = webp::Encoder::from_rgb(rvb, STOCKEE as u32, STOCKEE as u32)
                .encode_advanced(&cfg)
                .map_err(|e| format!("libwebp : {e:?}"))?;
            Ok(m.to_vec())
        }
        #[cfg(feature = "webp-pure")]
        WebpBackend::ImageWebp => {
            let mut out = Vec::with_capacity(32 * 1024);
            image_webp::WebPEncoder::new(&mut out)
                .encode(rvb, STOCKEE as u32, STOCKEE as u32, image_webp::ColorType::Rgb8)
                .map_err(|e| format!("image-webp : {e}"))?;
            Ok(out)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn aller_retour_t2m() {
        let (min, max) = (243.15, 306.65);
        let vals = [243.15f32, 250.0, 280.123, 306.65, 400.0, -5.0];
        let mut c = [0u8; 6];
        quantifier_scalaire(&vals, min, max, Echelle::Lineaire, 254, &mut c);
        assert_eq!(c[0], 1);
        assert_eq!(c[3], 255);
        assert_eq!((c[4], c[5]), (255, 1)); // écrêtage, jamais 0
        let pas = pas_scalaire(min, max, Echelle::Lineaire, 254);
        let r = dequantifier_scalaire(c[2], min, max, Echelle::Lineaire, 254) as f64;
        assert!((r - 280.123).abs() <= pas / 2.0 + 1e-6);
    }

    #[test]
    fn nan_donne_zero_et_racine() {
        let mut c = [9u8; 3];
        quantifier_scalaire(&[f32::NAN, f32::INFINITY, 25.0], 0.0, 100.0, Echelle::Racine, 254, &mut c);
        assert_eq!(c[0], 0);
        assert_eq!(c[1], 0);
        assert_eq!(c[2], 1 + 127); // sqrt(0,25) = 0,5 -> 1 + 127
    }

    #[test]
    fn vent8() {
        let mut o = [7u8; 6];
        quantifier_vent8(&[0.0, f32::NAN], &[48.0, 1.0], 48.0, &mut o);
        assert_eq!(o, [128, 255, 0, 0, 0, 0]);
    }

    #[test]
    fn webp_aller_retour_purs() {
        #[cfg(feature = "webp-pure")]
        {
            let g: Vec<u8> = (0..STOCKEE * STOCKEE).map(|i| ((i * 7) % 255 + 1) as u8).collect();
            let b = vers_webp(&Image::Gris(&g), WebpBackend::ImageWebp, 4).unwrap();
            let mut d = image_webp::WebPDecoder::new(std::io::Cursor::new(&b)).unwrap();
            assert!(!d.has_alpha());
            let mut buf = vec![0u8; d.output_buffer_size().unwrap()];
            d.read_image(&mut buf).unwrap();
            assert!(buf.chunks(3).zip(&g).all(|(p, &v)| p == [v, v, v]));
        }
    }
}
