// SPDX-License-Identifier: EUPL-1.2
//! Lecture / écriture de fichiers `.npz` NumPy (tableaux `<f4` et chaîne unicode 0-d `meta`).

use anyhow::{bail, Context, Result};
use rw_tiles::Canon;
use std::io::{Read, Seek, Write};
use zip::write::SimpleFileOptions;
use zip::CompressionMethod;

/// Niveau de compression de l'archive : `None` = entrées stockées.
pub type Niveau = Option<i64>;

fn entete_npy(descr: &str, forme: &[usize]) -> Vec<u8> {
    let shape = match forme.len() {
        0 => "()".to_string(),
        1 => format!("({},)", forme[0]),
        _ => format!("({})", forme.iter().map(|d| d.to_string()).collect::<Vec<_>>().join(", ")),
    };
    let mut d = format!("{{'descr': '{descr}', 'fortran_order': False, 'shape': {shape}, }}");
    // en-tête total multiple de 64 octets, terminé par \n
    let total = 10 + d.len() + 1;
    let pad = (64 - total % 64) % 64;
    d.push_str(&" ".repeat(pad));
    d.push('\n');
    let mut out = b"\x93NUMPY\x01\x00".to_vec();
    out.extend_from_slice(&(d.len() as u16).to_le_bytes());
    out.extend_from_slice(d.as_bytes());
    out
}

fn npy_f32(v: &[f32], forme: &[usize]) -> Vec<u8> {
    let mut o = entete_npy("<f4", forme);
    o.reserve(v.len() * 4);
    for x in v {
        o.extend_from_slice(&x.to_le_bytes());
    }
    o
}

fn npy_chaine(s: &str) -> Vec<u8> {
    let n = s.chars().count();
    let mut o = entete_npy(&format!("<U{n}"), &[]);
    for c in s.chars() {
        o.extend_from_slice(&(c as u32).to_le_bytes());
    }
    o
}

/// Métadonnées JSON écrites avec le canonique (mêmes clés que `canon.ecrire`).
pub fn meta_json(c: &Canon, sources: &[String], note: &str) -> String {
    let unites: serde_json::Map<String, serde_json::Value> = c
        .variables
        .iter()
        .map(|(k, _)| (k.clone(), serde_json::Value::String(unite(k).to_string())))
        .collect();
    serde_json::json!({
        "model": c.modele, "run": c.tirage, "step": c.echeance,
        "units": unites, "source": sources, "note": note,
    })
    .to_string()
}

pub fn unite(variable: &str) -> &'static str {
    match variable {
        "wind10_u" | "wind10_v" | "gust10" => "m/s",
        "t2m" => "K",
        "rh2m" | "cloud_total" => "%",
        "precip_rate" => "mm/h",
        "snow_fraction" => "1",
        "cape" => "J/kg",
        "mslp" => "Pa",
        _ => "",
    }
}

/// Écrit le canonique en `.npz` (niveau de zlib `Some(6)` = comme `numpy.savez_compressed`).
pub fn ecrire<W: Write + Seek>(w: W, c: &Canon, meta: &str, niveau: Niveau) -> Result<()> {
    let mut z = zip::ZipWriter::new(w);
    let opts = match niveau {
        None => SimpleFileOptions::default().compression_method(CompressionMethod::Stored),
        Some(n) => SimpleFileOptions::default().compression_method(CompressionMethod::Deflated).compression_level(Some(n)),
    }
    .large_file(true);
    let mut ajoute = |nom: &str, octets: Vec<u8>| -> Result<()> {
        z.start_file(format!("{nom}.npy"), opts)?;
        z.write_all(&octets)?;
        Ok(())
    };
    ajoute("lat", npy_f32(&c.lat, &[c.lat.len()]))?;
    ajoute("lon", npy_f32(&c.lon, &[c.lon.len()]))?;
    ajoute("meta", npy_chaine(meta))?;
    for (k, v) in &c.variables {
        ajoute(k, npy_f32(v, &[c.lat.len(), c.lon.len()]))?;
    }
    z.finish()?;
    Ok(())
}

struct Npy {
    descr: String,
    forme: Vec<usize>,
    donnees: Vec<u8>,
}

fn lire_npy(mut r: impl Read) -> Result<Npy> {
    let mut m = [0u8; 8];
    r.read_exact(&mut m)?;
    if &m[..6] != b"\x93NUMPY" {
        bail!("pas un fichier npy");
    }
    let lh = match m[6] {
        1 => {
            let mut b = [0u8; 2];
            r.read_exact(&mut b)?;
            u16::from_le_bytes(b) as usize
        }
        _ => {
            let mut b = [0u8; 4];
            r.read_exact(&mut b)?;
            u32::from_le_bytes(b) as usize
        }
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
    let forme: Vec<usize> = shape.split(',').map(str::trim).filter(|s| !s.is_empty()).map(|s| s.parse()).collect::<Result<_, _>>()?;
    let mut donnees = Vec::new();
    r.read_to_end(&mut donnees)?;
    Ok(Npy { descr, forme, donnees })
}

fn vers_f32(n: &Npy) -> Result<Vec<f32>> {
    if n.descr != "<f4" {
        bail!("dtype {} non géré", n.descr);
    }
    let attendu: usize = n.forme.iter().product::<usize>() * 4;
    if n.donnees.len() != attendu {
        bail!("taille de données {} au lieu de {}", n.donnees.len(), attendu);
    }
    Ok(n.donnees.chunks_exact(4).map(|c| f32::from_le_bytes([c[0], c[1], c[2], c[3]])).collect())
}

/// Lit un canonique `.npz` (écrit par le Python ou par ce programme).
pub fn lire<R: Read + Seek>(r: R) -> Result<(Canon, serde_json::Value)> {
    let mut z = zip::ZipArchive::new(r)?;
    let noms: Vec<String> = z.file_names().filter_map(|n| n.ok().map(|c| c.into_owned())).collect();
    let mut lat = None;
    let mut lon = None;
    let mut meta = None;
    let mut vars = Vec::new();
    for nom in noms {
        let cle = nom.trim_end_matches(".npy").to_string();
        let n = lire_npy(z.by_name(&nom)?).with_context(|| nom.clone())?;
        match cle.as_str() {
            "lat" => lat = Some(vers_f32(&n)?),
            "lon" => lon = Some(vers_f32(&n)?),
            "meta" => {
                if !n.descr.starts_with("<U") {
                    bail!("meta : dtype {}", n.descr);
                }
                let s: String = n.donnees.chunks_exact(4).filter_map(|c| char::from_u32(u32::from_le_bytes([c[0], c[1], c[2], c[3]]))).collect();
                meta = Some(serde_json::from_str::<serde_json::Value>(&s)?);
            }
            _ => vars.push((cle, vers_f32(&n)?)),
        }
    }
    let meta = meta.context("meta absent")?;
    let lat = lat.context("lat absent")?;
    let lon = lon.context("lon absent")?;
    let c = Canon {
        modele: meta["model"].as_str().context("meta.model")?.to_string(),
        tirage: meta["run"].as_str().context("meta.run")?.to_string(),
        echeance: meta["step"].as_u64().context("meta.step")? as u32,
        lat,
        lon,
        variables: vars,
    };
    Ok((c, meta))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    fn exemple() -> Canon {
        Canon {
            modele: "arome".into(),
            tirage: "2026-10-17T06:00:00Z".into(),
            echeance: 3,
            lat: vec![2.0, 1.0],
            lon: vec![0.0, 1.0, 2.0],
            variables: vec![("t2m".into(), vec![1.0, 2.0, f32::NAN, 4.0, 5.0, 6.0])],
        }
    }

    #[test]
    fn aller_retour_compresse_et_stocke() {
        for niveau in [None, Some(6)] {
            let c = exemple();
            let meta = meta_json(&c, &["a.grib2".into()], "note é");
            let mut buf = Cursor::new(Vec::new());
            ecrire(&mut buf, &c, &meta, niveau).unwrap();
            buf.set_position(0);
            let (r, m) = lire(buf).unwrap();
            assert_eq!(r.lat, c.lat);
            assert_eq!(r.lon, c.lon);
            assert_eq!(r.echeance, 3);
            assert_eq!(m["note"], "note é");
            let v = r.variable("t2m").unwrap();
            assert!(v[2].is_nan() && v[3] == 4.0);
        }
    }

    #[test]
    fn entete_aligne_64() {
        assert_eq!(npy_f32(&[1.0], &[1]).len() % 64, 4);
        assert_eq!(entete_npy("<f4", &[3, 2]).len() % 64, 0);
    }
}
