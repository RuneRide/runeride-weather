// SPDX-License-Identifier: EUPL-1.2
//! Décodage CCSDS / AEC (libaec) du gabarit GRIB2 5.42, derrière une interface unique.
//!
//! Plusieurs implémentations en Rust pur sont branchables par fonctionnalité Cargo ; elles ont été
//! comparées (exactitude contre eccodes, vitesse) dans `docs/poc-rust/synthese.md`.

use crate::{Drs, GribError};

/// Implémentation du décodeur AEC.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Backend {
    /// `tensogram-szip` (ECMWF), Rust pur.
    #[cfg(feature = "aec-tensogram")]
    Tensogram,
    /// `rust-aec`, Rust pur.
    #[cfg(feature = "aec-rust-aec")]
    RustAec,
    /// `oxiarc-szip`, Rust pur.
    #[cfg(feature = "aec-oxiarc")]
    Oxiarc,
}

impl Default for Backend {
    #[allow(unreachable_code)]
    fn default() -> Self {
        #[cfg(feature = "aec-tensogram")]
        return Backend::Tensogram;
        #[cfg(feature = "aec-rust-aec")]
        return Backend::RustAec;
        #[cfg(feature = "aec-oxiarc")]
        return Backend::Oxiarc;
    }
}

impl std::str::FromStr for Backend {
    type Err = String;
    fn from_str(s: &str) -> Result<Self, String> {
        match s {
            #[cfg(feature = "aec-tensogram")]
            "tensogram" => Ok(Backend::Tensogram),
            #[cfg(feature = "aec-rust-aec")]
            "rust-aec" => Ok(Backend::RustAec),
            #[cfg(feature = "aec-oxiarc")]
            "oxiarc" => Ok(Backend::Oxiarc),
            _ => Err(format!("décodeur AEC inconnu ou non compilé : {s}")),
        }
    }
}

/// Largeur en octets d'un échantillon dans le tampon de sortie de libaec.
pub fn octets_par_echantillon(bits: u8, flags: u8) -> usize {
    match bits {
        0..=8 => 1,
        9..=16 => 2,
        17..=24 if flags & 2 != 0 => 3,
        _ => 4,
    }
}

/// Décode la charge utile de la section 7 en entiers non signés (un par valeur présente).
pub fn decoder_entiers(charge: &[u8], d: &Drs, backend: Backend) -> Result<Vec<u32>, GribError> {
    if d.bits == 0 || d.bits > 32 {
        return Err(GribError::NonGere(format!("{} bits par valeur", d.bits)));
    }
    if d.flags_ccsds & 1 != 0 {
        return Err(GribError::NonGere("échantillons signés CCSDS".into()));
    }
    let w = octets_par_echantillon(d.bits, d.flags_ccsds);
    let n = d.nb_valeurs;
    let octets = decoder_octets(charge, d, backend, n * w)?;
    if octets.len() < n * w {
        return Err(GribError::Aec(format!("{} octets décodés, {} attendus", octets.len(), n * w)));
    }
    let msb = d.flags_ccsds & 4 != 0;
    let mut out = Vec::with_capacity(n);
    match (w, msb) {
        (1, _) => out.extend(octets[..n].iter().map(|&b| b as u32)),
        (2, true) => out.extend(octets[..2 * n].chunks_exact(2).map(|c| u16::from_be_bytes([c[0], c[1]]) as u32)),
        (2, false) => out.extend(octets[..2 * n].chunks_exact(2).map(|c| u16::from_le_bytes([c[0], c[1]]) as u32)),
        (3, true) => out.extend(octets[..3 * n].chunks_exact(3).map(|c| u32::from_be_bytes([0, c[0], c[1], c[2]]))),
        (3, false) => out.extend(octets[..3 * n].chunks_exact(3).map(|c| u32::from_le_bytes([c[0], c[1], c[2], 0]))),
        (_, true) => out.extend(octets[..4 * n].chunks_exact(4).map(|c| u32::from_be_bytes([c[0], c[1], c[2], c[3]]))),
        (_, false) => out.extend(octets[..4 * n].chunks_exact(4).map(|c| u32::from_le_bytes([c[0], c[1], c[2], c[3]]))),
    }
    Ok(out)
}

#[allow(unused_variables)]
fn decoder_octets(charge: &[u8], d: &Drs, backend: Backend, octets_sortie: usize) -> Result<Vec<u8>, GribError> {
    match backend {
        #[cfg(feature = "aec-tensogram")]
        Backend::Tensogram => {
            let p = tensogram_szip::AecParams {
                bits_per_sample: d.bits as u32,
                block_size: d.taille_bloc as u32,
                rsi: d.rsi as u32,
                flags: d.flags_ccsds as u32,
            };
            tensogram_szip::aec_decompress(charge, octets_sortie, &p).map_err(|e| GribError::Aec(e.to_string()))
        }
        #[cfg(feature = "aec-rust-aec")]
        Backend::RustAec => {
            let p = rust_aec::AecParams::new(d.bits, d.taille_bloc as u32, d.rsi as u32, rust_aec::flags_from_grib2_ccsds_flags(d.flags_ccsds));
            rust_aec::decode(charge, p, d.nb_valeurs).map_err(|e| GribError::Aec(e.to_string()))
        }
        #[cfg(feature = "aec-oxiarc")]
        Backend::Oxiarc => {
            let p = oxiarc_szip::SzipParams {
                bits_per_pixel: d.bits,
                pixels_per_block: d.taille_bloc as u32,
                samples: d.nb_valeurs,
                reference_sample_interval: d.rsi as u32 * d.taille_bloc as u32,
                msb: d.flags_ccsds & 4 != 0,
                nn_preprocess: d.flags_ccsds & 8 != 0,
                ..Default::default()
            };
            oxiarc_szip::decode(charge, &p).map_err(|e| GribError::Aec(e.to_string()))
        }
    }
}
