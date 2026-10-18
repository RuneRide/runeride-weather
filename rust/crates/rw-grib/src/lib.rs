// SPDX-License-Identifier: EUPL-1.2
//! Lecteur GRIB2 minimal pour les paquets AROME / AROME HD de Météo-France.
//!
//! Périmètre volontairement étroit (tout le reste est refusé avec une erreur explicite) :
//! - section 3, gabarit 3.0 (latitude / longitude régulière) ;
//! - section 4, gabarits 4.0 (instantané) et 4.8 (intervalle statistique) ;
//! - section 5, gabarit 5.42 (CCSDS / AEC, via un décodeur de `aec`) ;
//! - section 6 : masque de bits (indicateur 0) ou masque précédent (254) ou absence (255) ;
//! - messages à plusieurs champs (sections 2 à 7 répétées).
//!
//! Les fichiers GRIB sont des données non fiables : toutes les longueurs lues sont vérifiées avant
//! d'indexer, aucune ne sert à allouer sans borne.

pub mod aec;

use rayon::prelude::*;
use thiserror::Error;

/// Valeur manquante GRIB de Météo-France (remplacée par NaN au décodage).
pub const VALEUR_MANQUANTE: f64 = 9999.0;

#[derive(Debug, Error)]
pub enum GribError {
    #[error("GRIB tronqué ou incohérent : {0}")]
    Format(String),
    #[error("non géré : {0}")]
    NonGere(String),
    #[error("décodage AEC : {0}")]
    Aec(String),
}

type Result<T> = std::result::Result<T, GribError>;

fn fmt<T>(msg: impl Into<String>) -> Result<T> {
    Err(GribError::Format(msg.into()))
}

/// Grille latitude / longitude régulière (gabarit 3.0), angles en degrés.
#[derive(Debug, Clone, PartialEq)]
pub struct Grille {
    pub ni: usize,
    pub nj: usize,
    pub la1: f64,
    pub lo1: f64,
    pub la2: f64,
    pub lo2: f64,
    pub di: f64,
    pub dj: f64,
    /// Octet de mode de balayage (bit 0x80 : i vers l'ouest, 0x40 : j vers le nord, 0x20 : j consécutif).
    pub balayage: u8,
}

/// Paramètres de représentation des données (gabarit 5.42).
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Drs {
    pub nb_valeurs: usize,
    pub reference: f32,
    pub echelle_binaire: i16,
    pub echelle_decimale: i16,
    pub bits: u8,
    pub flags_ccsds: u8,
    pub taille_bloc: u8,
    pub rsi: u16,
}

/// Un champ (une grille de valeurs) repéré dans un fichier, sans avoir été décodé.
#[derive(Debug, Clone)]
pub struct Champ<'a> {
    pub discipline: u8,
    pub categorie: u8,
    pub numero: u8,
    /// Numéro du gabarit de produit (0 ou 8).
    pub gabarit_produit: u16,
    /// Type de la première surface fixe (1 = sol, 103 = hauteur au-dessus du sol, ...).
    pub type_surface: u8,
    /// Niveau (valeur échelonnée de la première surface, en unité de la surface).
    pub niveau: f64,
    /// Processus statistique (gabarit 4.8) : 0 moyenne, 1 cumul, 2 maximum, 3 minimum.
    pub statistique: Option<u8>,
    /// Début et fin de la fenêtre, en heures depuis le tirage (comme `startStep` / `endStep` d'eccodes).
    pub debut: u32,
    pub fin: u32,
    pub grille: Grille,
    pub drs: Drs,
    /// Nombre de points de la grille (section 3).
    pub nb_points: usize,
    masque: Option<&'a [u8]>,
    donnees: &'a [u8],
}

fn u16_at(b: &[u8], o: usize) -> u16 {
    u16::from_be_bytes([b[o], b[o + 1]])
}
fn u32_at(b: &[u8], o: usize) -> u32 {
    u32::from_be_bytes([b[o], b[o + 1], b[o + 2], b[o + 3]])
}
/// Entier signé GRIB (signe-amplitude) sur 2 octets.
fn s16_at(b: &[u8], o: usize) -> i16 {
    let v = u16_at(b, o);
    let m = (v & 0x7fff) as i16;
    if v & 0x8000 != 0 {
        -m
    } else {
        m
    }
}
/// Entier signé GRIB (signe-amplitude) sur 4 octets.
fn s32_at(b: &[u8], o: usize) -> i64 {
    let v = u32_at(b, o);
    let m = (v & 0x7fff_ffff) as i64;
    if v & 0x8000_0000 != 0 {
        -m
    } else {
        m
    }
}

fn lire_grille(s: &[u8]) -> Result<(Grille, usize)> {
    // octets (base 1) : 7-10 nb de points, 13-14 gabarit
    if s.len() < 72 {
        return fmt("section 3 trop courte");
    }
    let nb_points = u32_at(s, 6) as usize;
    let gabarit = u16_at(s, 12);
    if gabarit != 0 {
        return Err(GribError::NonGere(format!("gabarit de grille 3.{gabarit}")));
    }
    let ni = u32_at(s, 30) as usize;
    let nj = u32_at(s, 34) as usize;
    if ni.checked_mul(nj) != Some(nb_points) {
        return fmt(format!("Ni*Nj = {}*{} différent de {} points", ni, nj, nb_points));
    }
    let angle = |o: usize| s32_at(s, o) as f64 * 1e-6; // sous-division de base = 10^6
    let g = Grille {
        ni,
        nj,
        la1: angle(46),
        lo1: angle(50),
        la2: angle(55),
        lo2: angle(59),
        di: u32_at(s, 63) as f64 * 1e-6,
        dj: u32_at(s, 67) as f64 * 1e-6,
        balayage: s[71],
    };
    if u32_at(s, 38) != 0 {
        // angle de base nul : unité du micro-degré (seul cas géré)
        return Err(GribError::NonGere("angle de base non nul".into()));
    }
    Ok((g, nb_points))
}

struct Produit {
    discipline: u8,
    categorie: u8,
    numero: u8,
    gabarit: u16,
    type_surface: u8,
    niveau: f64,
    statistique: Option<u8>,
    debut: u32,
    fin: u32,
}

fn heures(unite: u8, v: u32) -> Result<u32> {
    match unite {
        0 if v % 60 == 0 => Ok(v / 60),
        1 => Ok(v),
        2 => Ok(v * 24),
        _ => Err(GribError::NonGere(format!("unité de temps {unite} (valeur {v})"))),
    }
}

fn lire_produit(s: &[u8], discipline: u8) -> Result<Produit> {
    if s.len() < 34 {
        return fmt("section 4 trop courte");
    }
    let gabarit = u16_at(s, 7);
    if gabarit != 0 && gabarit != 8 {
        return Err(GribError::NonGere(format!("gabarit de produit 4.{gabarit}")));
    }
    let unite = s[17];
    let prevision = heures(unite, u32_at(s, 18))?;
    let echelle = if s[23] & 0x80 != 0 { -((s[23] & 0x7f) as i32) } else { s[23] as i32 };
    let valeur = u32_at(s, 24) as f64;
    let niveau = valeur * 10f64.powi(-echelle);
    let (statistique, debut, fin) = if gabarit == 8 {
        if s.len() < 58 {
            return fmt("section 4.8 trop courte");
        }
        if s[41] != 1 {
            return Err(GribError::NonGere(format!("{} intervalles de temps", s[41])));
        }
        let longueur = heures(s[48], u32_at(s, 49))?;
        (Some(s[46]), prevision, prevision + longueur)
    } else {
        (None, prevision, prevision)
    };
    Ok(Produit {
        discipline,
        categorie: s[9],
        numero: s[10],
        gabarit,
        type_surface: s[22],
        niveau,
        statistique,
        debut,
        fin,
    })
}

fn lire_drs(s: &[u8]) -> Result<Drs> {
    if s.len() < 25 {
        return fmt("section 5 trop courte");
    }
    let gabarit = u16_at(s, 9);
    if gabarit != 42 {
        return Err(GribError::NonGere(format!("gabarit de représentation 5.{gabarit} (seul 5.42 CCSDS est géré)")));
    }
    Ok(Drs {
        nb_valeurs: u32_at(s, 5) as usize,
        reference: f32::from_be_bytes([s[11], s[12], s[13], s[14]]),
        echelle_binaire: s16_at(s, 15),
        echelle_decimale: s16_at(s, 17),
        bits: s[19],
        flags_ccsds: s[21],
        taille_bloc: s[22],
        rsi: u16_at(s, 23),
    })
}

/// Repère tous les champs d'un tampon GRIB2 (concaténation de messages), sans décoder les valeurs.
pub fn scanner(buf: &[u8]) -> Result<Vec<Champ<'_>>> {
    let mut champs = Vec::new();
    let mut pos = 0usize;
    while pos < buf.len() {
        // tolérance : octets de bourrage entre messages
        if buf.len() - pos < 16 {
            break;
        }
        if &buf[pos..pos + 4] != b"GRIB" {
            pos += 1;
            continue;
        }
        if buf[pos + 7] != 2 {
            return Err(GribError::NonGere(format!("édition GRIB {}", buf[pos + 7])));
        }
        let discipline = buf[pos + 6];
        let total = u64::from_be_bytes(buf[pos + 8..pos + 16].try_into().unwrap()) as usize;
        let fin_msg = pos.checked_add(total).filter(|&f| f <= buf.len() && total >= 20);
        let Some(fin_msg) = fin_msg else { return fmt("longueur de message hors fichier") };
        if &buf[fin_msg - 4..fin_msg] != b"7777" {
            return fmt("marqueur de fin 7777 absent");
        }
        let mut p = pos + 16;
        let mut grille: Option<(Grille, usize)> = None;
        let mut produit: Option<Produit> = None;
        let mut drs: Option<Drs> = None;
        let mut masque: Option<Option<&[u8]>> = None;
        while p + 5 <= fin_msg - 4 {
            let len = u32_at(buf, p) as usize;
            let num = buf[p + 4];
            if len < 5 || p + len > fin_msg - 4 {
                return fmt(format!("section {num} : longueur {len} invalide"));
            }
            let s = &buf[p..p + len];
            match num {
                1 | 2 => {}
                3 => grille = Some(lire_grille(s)?),
                4 => produit = Some(lire_produit(s, discipline)?),
                5 => drs = Some(lire_drs(s)?),
                6 => {
                    masque = Some(match s.get(5) {
                        Some(0) => Some(&s[6..]),
                        Some(254) => masque.flatten(),
                        Some(255) => None,
                        autre => return Err(GribError::NonGere(format!("indicateur de masque {autre:?}"))),
                    });
                }
                7 => {
                    let (Some((g, n)), Some(pr), Some(d), Some(m)) = (&grille, &produit, &drs, &masque) else {
                        return fmt("section 7 avant les sections 3 à 6");
                    };
                    if let Some(m) = m {
                        if m.len() * 8 < *n {
                            return fmt("masque plus court que la grille");
                        }
                    } else if d.nb_valeurs != *n {
                        return fmt("sans masque, nb de valeurs différent du nb de points");
                    }
                    if d.nb_valeurs > *n {
                        return fmt("plus de valeurs que de points");
                    }
                    champs.push(Champ {
                        discipline: pr.discipline,
                        categorie: pr.categorie,
                        numero: pr.numero,
                        gabarit_produit: pr.gabarit,
                        type_surface: pr.type_surface,
                        niveau: pr.niveau,
                        statistique: pr.statistique,
                        debut: pr.debut,
                        fin: pr.fin,
                        grille: g.clone(),
                        drs: *d,
                        nb_points: *n,
                        masque: *m,
                        donnees: &s[5..],
                    });
                }
                _ => return fmt(format!("section {num} inattendue")),
            }
            p += len;
        }
        pos = fin_msg;
    }
    Ok(champs)
}

impl<'a> Champ<'a> {
    /// Décode les valeurs dans l'ordre du fichier (`ni * nj` f32), `NaN` aux points masqués.
    ///
    /// Formule GRIB2 : `Y = (R + X * 2^E) / 10^D`, calculée en f64 comme eccodes puis arrondie en f32.
    pub fn decoder(&self, backend: aec::Backend) -> Result<Vec<f32>> {
        let d = &self.drs;
        let entiers = aec::decoder_entiers(self.donnees, d, backend)?;
        let r = d.reference as f64;
        let b = (2f64).powi(d.echelle_binaire as i32);
        let dec = (10f64).powi(-(d.echelle_decimale as i32));
        let conv = |x: u32| ((x as f64 * b + r) * dec) as f32;
        let mut sortie = vec![f32::NAN; self.nb_points];
        match self.masque {
            None => {
                for (o, &x) in sortie.iter_mut().zip(entiers.iter()) {
                    *o = conv(x);
                }
            }
            Some(m) => {
                let mut k = 0usize;
                for (i, o) in sortie.iter_mut().enumerate() {
                    if m[i >> 3] & (0x80 >> (i & 7)) != 0 {
                        let x = *entiers.get(k).ok_or_else(|| GribError::Format("masque : plus de points que de valeurs".into()))?;
                        *o = conv(x);
                        k += 1;
                    }
                }
                if k != entiers.len() {
                    return fmt(format!("masque : {} points présents pour {} valeurs", k, entiers.len()));
                }
            }
        }
        // Convention du pipeline : la valeur manquante 9999 vaut NaN
        for v in sortie.iter_mut() {
            if *v as f64 == VALEUR_MANQUANTE {
                *v = f32::NAN;
            }
        }
        Ok(sortie)
    }
}

/// Décode plusieurs champs en parallèle (rayon, pool courant).
pub fn decoder_tous(champs: &[&Champ<'_>], backend: aec::Backend) -> Result<Vec<Vec<f32>>> {
    champs.par_iter().map(|c| c.decoder(backend)).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use oxiarc_szip::{encode, SzipParams};

    fn sec(num: u8, corps: &[u8]) -> Vec<u8> {
        let mut v = ((corps.len() + 5) as u32).to_be_bytes().to_vec();
        v.push(num);
        v.extend_from_slice(corps);
        v
    }

    /// Message GRIB2 synthétique : grille ni*nj, valeurs entières AEC (12 bits, sans compression),
    /// R = `reference`, E = -2, D = 0 ; masque optionnel ; `stat` : gabarit 4.8 (processus, début, durée).
    fn message(ni: u32, nj: u32, entiers: &[u32], masque: Option<&[bool]>, stat: Option<(u8, u32, u32)>, reference: f32) -> Vec<u8> {
        let s1 = sec(1, &[0; 16]);
        let mut c3 = vec![0u8]; // octet 6 : source de la grille
        c3.extend_from_slice(&(ni * nj).to_be_bytes()); // 7-10
        c3.extend_from_slice(&[0, 0]); // 11-12
        c3.extend_from_slice(&[0, 0]); // 13-14 : gabarit 3.0
        c3.push(6); // 15 : forme de la Terre
        c3.extend_from_slice(&[0; 15]); // 16-30
        c3.extend_from_slice(&ni.to_be_bytes()); // 31-34
        c3.extend_from_slice(&nj.to_be_bytes()); // 35-38
        c3.extend_from_slice(&[0; 4]); // 39-42 : angle de base
        c3.extend_from_slice(&[0xff; 4]); // 43-46 : sous-division
        c3.extend_from_slice(&55_400_000u32.to_be_bytes()); // La1
        c3.extend_from_slice(&348_000_000u32.to_be_bytes()); // Lo1
        c3.push(48); // 55
        c3.extend_from_slice(&(55_400_000u32 - 10_000 * (nj - 1)).to_be_bytes()); // La2
        c3.extend_from_slice(&(348_000_000u32 + 10_000 * (ni - 1)).to_be_bytes()); // Lo2
        c3.extend_from_slice(&10_000u32.to_be_bytes());
        c3.extend_from_slice(&10_000u32.to_be_bytes());
        c3.push(0); // 72 : balayage
        let s3 = sec(3, &c3);
        let mut c4 = vec![0, 0, 0, if stat.is_some() { 8 } else { 0 }]; // 6-7 coordonnées, 8-9 gabarit
        c4.extend_from_slice(&[0; 8]); // 10-17 : catégorie, numéro, processus, fond, analyse, coupure (3)
        c4.push(1); // 18 : unité de temps = heure
        c4.extend_from_slice(&stat.map_or(0, |(_, d, _)| d).to_be_bytes()); // 19-22
        c4.extend_from_slice(&[103, 0]);
        c4.extend_from_slice(&2u32.to_be_bytes()); // niveau 2
        c4.extend_from_slice(&[255, 0, 0, 0, 0, 0]);
        if let Some((proc_, _, duree)) = stat {
            c4.extend_from_slice(&[0x07, 0xea, 10, 18, 0, 0, 0]); // 35-41 : fin de période
            c4.push(1); // 42 : une plage de temps
            c4.extend_from_slice(&0u32.to_be_bytes()); // 43-46
            c4.push(proc_); // 47
            c4.push(1); // 48
            c4.push(1); // 49 : unité = heure
            c4.extend_from_slice(&duree.to_be_bytes()); // 50-53
            c4.push(255);
            c4.extend_from_slice(&0u32.to_be_bytes());
        }
        let s4 = sec(4, &c4);
        let mut c5 = (entiers.len() as u32).to_be_bytes().to_vec();
        c5.extend_from_slice(&[0, 42]);
        c5.extend_from_slice(&reference.to_be_bytes());
        c5.extend_from_slice(&[0x80, 2]); // E = -2 (signe-amplitude)
        c5.extend_from_slice(&[0, 0]);
        c5.extend_from_slice(&[12, 0, 6, 32, 0, 128]); // bits, type, flags (3 octets + MSB), bloc, RSI
        let s5 = sec(5, &c5);
        let s6 = match masque {
            Some(m) => {
                let mut b = vec![0u8; m.len().div_ceil(8) + 1];
                for (i, &p) in m.iter().enumerate() {
                    if p {
                        b[1 + i / 8] |= 0x80 >> (i % 8);
                    }
                }
                sec(6, &b)
            }
            None => sec(6, &[255]),
        };
        let p = SzipParams {
            bits_per_pixel: 12,
            pixels_per_block: 32,
            samples: entiers.len(),
            reference_sample_interval: 128 * 32,
            msb: true,
            nn_preprocess: false,
            ..Default::default()
        };
        let donnees = encode(&entiers.iter().map(|&x| x as u64).collect::<Vec<_>>(), &p).unwrap();
        let s7 = sec(7, &donnees);
        let mut corps = Vec::new();
        for s in [&s1, &s3, &s4, &s5, &s6, &s7] {
            corps.extend_from_slice(s);
        }
        let total = 16 + corps.len() + 4;
        let mut m = b"GRIB\0\0\0\x02".to_vec();
        m.extend_from_slice(&(total as u64).to_be_bytes());
        m.extend_from_slice(&corps);
        m.extend_from_slice(b"7777");
        m
    }

    #[test]
    fn signe_amplitude() {
        assert_eq!(s16_at(&[0x80, 0x0a], 0), -10);
        assert_eq!(s16_at(&[0x00, 0x0a], 0), 10);
        assert_eq!(s32_at(&[0x80, 0, 0, 5], 0), -5);
    }

    #[test]
    fn tampon_vide_ou_invalide() {
        assert!(scanner(&[]).unwrap().is_empty());
        assert!(scanner(b"GRIB\0\0\0\x02\0\0\0\0\0\0\0\x10").is_err()); // longueur < 20
        assert!(scanner(b"GRIB\0\0\0\x01\0\0\0\0\0\0\0\x20xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx").is_err()); // édition 1
    }

    #[test]
    fn valeurs_sans_masque() {
        let x: Vec<u32> = (0..60).map(|i| i * 3 + 1).collect();
        let m = message(10, 6, &x, None, None, 100.0);
        let c = scanner(&m).unwrap();
        assert_eq!(c.len(), 1);
        assert_eq!((c[0].grille.ni, c[0].grille.nj), (10, 6));
        assert_eq!((c[0].niveau, c[0].debut, c[0].fin), (2.0, 0, 0));
        assert!((c[0].grille.lo1 - 348.0).abs() < 1e-9 && (c[0].grille.la1 - 55.4).abs() < 1e-9);
        let v = c[0].decoder(aec::Backend::default()).unwrap();
        for (i, &e) in x.iter().enumerate() {
            assert_eq!(v[i], (100.0 + e as f64 * 0.25) as f32);
        }
    }

    #[test]
    fn masque_et_valeur_9999() {
        // 12 points dont 4 manquants ; une valeur codée vaut exactement 9999 -> NaN
        let masque = [true, true, false, true, false, true, true, true, false, true, true, false];
        let nb = masque.iter().filter(|&&b| b).count();
        let mut x: Vec<u32> = (0..nb as u32).map(|i| i * 10).collect();
        x[2] = ((9999.0 - 9000.0) / 0.25) as u32;
        let m = message(4, 3, &x, Some(&masque), None, 9000.0);
        let c = scanner(&m).unwrap();
        let v = c[0].decoder(aec::Backend::default()).unwrap();
        assert_eq!(v.len(), 12);
        for (i, &p) in masque.iter().enumerate() {
            if !p {
                assert!(v[i].is_nan(), "point {i}");
            }
        }
        assert_eq!(v[0], 9000.0);
        assert_eq!(v[1], 9002.5);
        assert!(v[3].is_nan()); // la 3e valeur présente (point 3) vaut 9999 : devient NaN
        assert_eq!(v[5], 9000.0 + 30.0 * 0.25); // 4e valeur présente
    }

    #[test]
    fn fenetre_statistique() {
        let x: Vec<u32> = vec![1; 6];
        let m = message(3, 2, &x, None, Some((2, 11, 1)), 0.0);
        let c = scanner(&m).unwrap();
        assert_eq!((c[0].statistique, c[0].debut, c[0].fin), (Some(2), 11, 12));
        let m = message(3, 2, &x, None, Some((1, 0, 12)), 0.0);
        let c = scanner(&m).unwrap();
        assert_eq!((c[0].statistique, c[0].debut, c[0].fin), (Some(1), 0, 12));
    }

    #[test]
    fn deux_champs_dans_un_message_et_troncature() {
        let m = message(3, 2, &[1; 6], None, None, 0.0);
        assert!(scanner(&m[..m.len() - 10]).is_err());
        let mut m2 = m.clone();
        m2.extend_from_slice(&m); // deux messages concaténés
        assert_eq!(scanner(&m2).unwrap().len(), 2);
        // longueur de section mensongère : erreur, pas de panique
        let mut m3 = m.clone();
        m3[16 + 21..16 + 25].copy_from_slice(&0x7fff_ffffu32.to_be_bytes());
        assert!(scanner(&m3).is_err());
    }

    /// Valeurs de référence calculées avec eccodes (Python) sur un vrai fichier AROME HD ;
    /// ignoré si le fichier n'est pas présent dans `out/grib`.
    #[test]
    fn fichier_reel_contre_eccodes() {
        let chemin = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../../out/grib/arome-hd/2026-10-17T03:00:00Z/arome__001__SP1__12H__2026-10-17T03:00:00Z.grib2");
        let Ok(octets) = std::fs::read(&chemin) else {
            eprintln!("GRIB réel absent, test ignoré");
            return;
        };
        let champs = scanner(&octets).unwrap();
        assert_eq!(champs.len(), 6);
        let t2m = &champs[0];
        assert_eq!((t2m.categorie, t2m.numero, t2m.niveau), (0, 0, 2.0));
        assert_eq!((t2m.grille.ni, t2m.grille.nj), (2801, 1791));
        let v = t2m.decoder(aec::Backend::default()).unwrap();
        let valides: Vec<f32> = v.iter().copied().filter(|x| !x.is_nan()).collect();
        assert_eq!(valides.len(), 4_160_519);
        let somme: f64 = valides.iter().map(|&x| x as f64).sum();
        assert!((somme - 1_201_356_905.630_798_3).abs() < 1e-3, "somme {somme}");
        assert_eq!(valides.iter().cloned().fold(f32::MAX, f32::min), 261.936_096_191_406_25);
        assert_eq!(valides.iter().cloned().fold(f32::MIN, f32::max), 304.060_119_628_906_25);
    }
}
