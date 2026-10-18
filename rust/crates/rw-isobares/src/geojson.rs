// SPDX-License-Identifier: EUPL-1.2
//! Sérialisation GeoJSON identique à `json.dump(gj, ensure_ascii=False, separators=(",", ":"))` de Python.

use crate::{arrondi, Entete, Params, Resultat};
use std::fmt::Write as _;
use std::io::Write as _;

/// Représentation d'un flottant comme `repr(float)` de Python dans la plage utile (pas d'exposant) :
/// plus court aller-retour, toujours avec un point décimal.
pub fn flottant(x: f64) -> String {
    let mut s = format!("{x}");
    if !s.contains('.') && !s.contains('e') && !s.contains("inf") && !s.contains("NaN") {
        s.push_str(".0");
    }
    s
}

fn chaine(s: &str) -> String {
    serde_json::to_string(s).unwrap()
}

pub fn vers_geojson(r: &Resultat, e: &Entete, p: &Params) -> String {
    let d = p.decimales;
    let mut o = String::with_capacity(128 * 1024);
    let _ = write!(
        o,
        "{{\"type\":\"FeatureCollection\",\"proprietes\":{{\"modele\":{},\"tirage\":{},\"echeance\":{},\"unite\":\"hPa\",\"pas_hpa\":{},\"lissage_sigma_mailles\":{},\"tolerance_km\":{},\"proeminence_hpa\":{},\"lissage_extrema_sigma_mailles\":{},\"source\":\"mslp (Pa) / 100\"}},\"features\":[",
        chaine(&e.modele),
        chaine(&e.tirage),
        e.echeance,
        flottant(p.pas_hpa),
        flottant(p.sigma),
        flottant(p.tol_km),
        flottant(p.proeminence),
        flottant(p.sigma_extrema)
    );
    // tri stable par (niveau, -longueur)
    let mut ordre: Vec<usize> = (0..r.lignes.len()).collect();
    ordre.sort_by(|&a, &b| {
        let (la, lb) = (&r.lignes[a], &r.lignes[b]);
        la.niveau.partial_cmp(&lb.niveau).unwrap().then((-la.longueur_km).partial_cmp(&(-lb.longueur_km)).unwrap())
    });
    let mut premier = true;
    for k in ordre {
        let l = &r.lignes[k];
        if !premier {
            o.push(',');
        }
        premier = false;
        let _ = write!(
            o,
            "{{\"type\":\"Feature\",\"properties\":{{\"type\":\"isobare\",\"niveau\":{},\"majeur\":{},\"ferme\":{}}},\"geometry\":{{\"type\":\"LineString\",\"coordinates\":[",
            l.niveau.round() as i64,
            l.majeur,
            l.ferme
        );
        for (i, c) in l.coords.iter().enumerate() {
            if i > 0 {
                o.push(',');
            }
            let _ = write!(o, "[{},{}]", flottant(arrondi(c[0], d)), flottant(arrondi(c[1], d)));
        }
        o.push_str("]}}");
    }
    for x in &r.extrema {
        if !premier {
            o.push(',');
        }
        premier = false;
        let lib = if x.genre == 'A' { "Anticyclone" } else { "Dépression" };
        let _ = write!(
            o,
            "{{\"type\":\"Feature\",\"properties\":{{\"type\":\"extremum\",\"genre\":\"{}\",\"pression\":{},\"libelle\":\"{}\",\"proeminence\":{}}},\"geometry\":{{\"type\":\"Point\",\"coordinates\":[{},{}]}}}}",
            x.genre,
            flottant(x.pression),
            lib,
            flottant(x.proeminence),
            flottant(arrondi(x.lon, d)),
            flottant(arrondi(x.lat, d))
        );
    }
    o.push_str("]}");
    o
}

/// gzip déterministe (en-tête sans date).
pub fn gzip(octets: &[u8], niveau: u32) -> Vec<u8> {
    let mut g = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::new(niveau));
    g.write_all(octets).unwrap();
    g.finish().unwrap()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn flottants_comme_python() {
        assert_eq!(flottant(2.0), "2.0");
        assert_eq!(flottant(0.1), "0.1");
        assert_eq!(flottant(-0.0), "-0.0");
        assert_eq!(flottant(1013.2), "1013.2");
        assert_eq!(flottant(5.23), "5.23");
        assert_eq!(flottant(-12.0), "-12.0");
    }
}
