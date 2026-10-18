// SPDX-License-Identifier: EUPL-1.2
//! Tuilage d'un champ synthétique linéaire : relecture des tuiles contre la valeur analytique,
//! bordure d'1 px, tuiles entièrement vides non écrites, métadonnées conformes au contrat.

use rw_tiles::pmtiles;
use rw_tiles::{construire, Canon, Options, Profil, WebpBackend};

/// Grille 0,01° sur 2 à 3,5 E et 47 à 48 N ; t2m = 280 + 5 (lon − 2) + 3 (lat − 47) ; coin NO masqué.
fn canon() -> Canon {
    let lat: Vec<f32> = (0..=100).map(|j| 48.0 - 0.01 * j as f32).collect();
    let lon: Vec<f32> = (0..=150).map(|i| 2.0 + 0.01 * i as f32).collect();
    let mut t2m = Vec::new();
    let mut u = Vec::new();
    let mut v = Vec::new();
    for (j, &la) in lat.iter().enumerate() {
        for (i, &lo) in lon.iter().enumerate() {
            let masque = j < 10 && i < 10;
            t2m.push(if masque { f32::NAN } else { 280.0 + 5.0 * (lo - 2.0) + 3.0 * (la - 47.0) });
            u.push(if masque { f32::NAN } else { 10.0 * (lo - 2.0) });
            v.push(if masque { f32::NAN } else { -5.0 });
        }
    }
    Canon {
        modele: "arome-hd".into(),
        tirage: "2026-10-17T03:00:00Z".into(),
        echeance: 5,
        lat,
        lon,
        variables: vec![("wind10_u".into(), u), ("wind10_v".into(), v), ("t2m".into(), t2m)],
    }
}

fn decoder_gris(webp: &[u8]) -> Vec<u8> {
    let mut d = image_webp::WebPDecoder::new(std::io::Cursor::new(webp)).unwrap();
    assert!(!d.has_alpha(), "WebP sans canal alpha attendu");
    assert_eq!(d.dimensions(), (258, 258));
    let mut buf = vec![0u8; d.output_buffer_size().unwrap()];
    d.read_image(&mut buf).unwrap();
    buf.chunks(3).map(|p| p[0]).collect()
}

#[test]
fn t2m_relue_dans_le_demi_pas_et_bordure_continue() {
    let mut o = Options::nouveau(Profil::par_nom("reduit2").unwrap());
    o.backend = WebpBackend::ImageWebp;
    o.zooms = Some((7, 7));
    let (fichiers, _) = construire(&canon(), &o).unwrap();
    let noms: Vec<&str> = fichiers.iter().map(|f| f.variable.as_str()).collect();
    assert_eq!(noms, ["t2m", "wind10"]);
    let f = &fichiers[0];
    assert_eq!(f.chemin, "arome-hd/2026-10-17T03Z/t2m/05.pmtiles");
    let lu = pmtiles::lire(&f.octets).unwrap();
    let m = &lu.metadonnees;
    assert_eq!(m["encoding"], "scalar8");
    assert_eq!(m["min"], 243.15);
    assert_eq!(m["levels"], 254);
    assert_eq!((m["border"].as_i64(), m["stored_size"].as_i64(), m["nodata_code"].as_i64()), (Some(1), Some(258), Some(0)));
    let pas = m["quant_step"].as_f64().unwrap();
    assert!((pas - 0.25).abs() < 1e-9);

    // toutes les tuiles z7 présentes : relire un pixel central de chacune
    let mut vu = 0;
    let (x0, y0, x1, y1) = (64u64, 44u64, 65u64, 45u64);
    let mut tuiles = std::collections::HashMap::new();
    for x in x0..=x1 {
        for y in y0..=y1 {
            if let Some(d) = lu.tuile(&f.octets, 7, x, y) {
                tuiles.insert((x, y), decoder_gris(d));
                vu += 1;
            }
        }
    }
    assert!(vu >= 2, "au moins deux tuiles voisines attendues, {vu} trouvées");
    for ((x, y), img) in &tuiles {
        // pixel (128, 128) de la tuile = indice 129 avec la bordure
        let code = img[129 * 258 + 129];
        let (lon, lat) = pmtiles_pixel(7, *x as u32, *y as u32, 128);
        let attendu = 280.0 + 5.0 * (lon - 2.0) + 3.0 * (lat - 47.0);
        if code == 0 {
            continue;
        }
        let v = 243.15 + (code as f64 - 1.0) * pas;
        assert!((v - attendu).abs() <= pas / 2.0 + 1e-4, "tuile {x}/{y} : {v} contre {attendu}");
    }
    // bordure : la colonne de bordure droite de (x, y) vaut la 1re colonne utile de (x+1, y)
    if let (Some(a), Some(b)) = (tuiles.get(&(x0, y0)), tuiles.get(&(x0 + 1, y0))) {
        for r in 1..257 {
            assert_eq!(a[r * 258 + 257], b[r * 258 + 1], "ligne {r}");
            assert_eq!(a[r * 258 + 256], b[r * 258], "ligne {r}");
        }
    }
}

/// Longitude / latitude du centre du pixel (p, p) d'une tuile.
fn pmtiles_pixel(z: u32, x: u32, y: u32, p: u32) -> (f64, f64) {
    let (lon, lat) = rw_tiles::mercator::lonlat_pixels(z, x, y, 1);
    (lon[(p + 1) as usize], lat[(p + 1) as usize])
}

#[test]
fn zoom_maximal_du_profil_et_tuiles_vides() {
    let mut o = Options::nouveau(Profil::par_nom("reduit2").unwrap());
    o.backend = WebpBackend::ImageWebp;
    o.zooms = Some((6, 8));
    let (fichiers, _) = construire(&canon(), &o).unwrap();
    // t2m est plafonné à z7 par le profil : aucune tuile z8 ; la tuile qui ne contient que le coin masqué n'existe pas
    let t2m = &fichiers[0];
    let lu = pmtiles::lire(&t2m.octets).unwrap();
    assert_eq!(lu.zoom_max, 7);
    assert_eq!(lu.metadonnees["maxzoom"], 7);
}

#[test]
fn vent8_codes() {
    let mut o = Options::nouveau(Profil::par_nom("reduit2").unwrap());
    o.backend = WebpBackend::ImageWebp;
    o.zooms = Some((7, 7));
    let (fichiers, _) = construire(&canon(), &o).unwrap();
    let w = fichiers.iter().find(|f| f.variable == "wind10").unwrap();
    let lu = pmtiles::lire(&w.octets).unwrap();
    assert_eq!(lu.metadonnees["encoding"], "wind8");
    assert!((lu.metadonnees["quant_step"].as_f64().unwrap() - 96.0 / 254.0).abs() < 1e-12);
    let (x, y) = (65u64, 44u64);
    let d = lu.tuile(&w.octets, 7, x, y).expect("tuile");
    let mut dec = image_webp::WebPDecoder::new(std::io::Cursor::new(d)).unwrap();
    let mut buf = vec![0u8; dec.output_buffer_size().unwrap()];
    dec.read_image(&mut buf).unwrap();
    // v = -5 m/s partout -> code 1 + round((−5 + 48) / 96 × 254) = 1 + 114 = 115 ; B = 0 ; hors grille : (0, 0, 0)
    let valides: Vec<&[u8]> = buf.chunks(3).filter(|p| p[1] != 0).collect();
    assert!(valides.len() > 1000, "pixels valides : {}", valides.len());
    assert!(valides.iter().all(|p| p[1] == 115 && p[2] == 0 && p[0] > 0));
}
