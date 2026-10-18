// SPDX-License-Identifier: EUPL-1.2
//! Abstraction du stockage de sortie : système de fichiers (tests, développement) ou S3.

use crate::erreur::ErreurStockage;
use std::path::{Path, PathBuf};

/// Fichiers de tirage : jamais modifiés une fois publiés (clé déterministe).
pub const CACHE_IMMUABLE: &str = "public, max-age=31536000, immutable";
/// Fichiers d'état : relus par les fonctions d'agrégation, jamais mis en cache.
pub const CACHE_AUCUN: &str = "no-cache";

/// En-têtes HTTP posés à l'écriture (S3 les conserve ; le stockage local les ignore).
#[derive(Debug, Clone, Copy)]
pub struct Meta {
    pub type_contenu: &'static str,
    pub cache: &'static str,
}

pub const PMTILES: Meta = Meta { type_contenu: "application/vnd.pmtiles", cache: CACHE_IMMUABLE };
pub const NPZ: Meta = Meta { type_contenu: "application/octet-stream", cache: CACHE_IMMUABLE };
pub const ETAT: Meta = Meta { type_contenu: "application/json", cache: CACHE_AUCUN };

pub trait Stockage: Send + Sync {
    /// Écrit (ou remplace) l'objet de façon atomique : un lecteur voit l'ancien ou le nouveau contenu, jamais un mélange.
    fn ecrire(&self, cle: &str, octets: &[u8], meta: &Meta) -> Result<(), ErreurStockage>;
    /// `None` si l'objet n'existe pas.
    fn lire(&self, cle: &str) -> Result<Option<Vec<u8>>, ErreurStockage>;
    /// Taille en octets, `None` si l'objet n'existe pas.
    fn taille(&self, cle: &str) -> Result<Option<u64>, ErreurStockage>;
}

/// Refuse les clés qui sortiraient de la racine.
pub(crate) fn cle_sure(cle: &str) -> Result<(), ErreurStockage> {
    let mauvais = cle.is_empty()
        || cle.starts_with('/')
        || cle.contains('\\')
        || cle.contains('\0')
        || cle.split('/').any(|s| s.is_empty() || s == "." || s == "..");
    if mauvais {
        Err(ErreurStockage::Io(format!("clé refusée : {cle:?}")))
    } else {
        Ok(())
    }
}

/// Stockage sur disque : écriture dans un fichier temporaire du même dossier puis `rename` (atomique).
pub struct StockageLocal {
    racine: PathBuf,
}

impl StockageLocal {
    pub fn new(racine: impl Into<PathBuf>) -> Self {
        Self { racine: racine.into() }
    }
    pub fn racine(&self) -> &Path {
        &self.racine
    }
    fn chemin(&self, cle: &str) -> Result<PathBuf, ErreurStockage> {
        cle_sure(cle)?;
        Ok(self.racine.join(cle))
    }
}

fn io(e: std::io::Error, quoi: &Path) -> ErreurStockage {
    ErreurStockage::Io(format!("{} : {e}", quoi.display()))
}

impl Stockage for StockageLocal {
    fn ecrire(&self, cle: &str, octets: &[u8], _meta: &Meta) -> Result<(), ErreurStockage> {
        let p = self.chemin(cle)?;
        std::fs::create_dir_all(p.parent().unwrap()).map_err(|e| io(e, &p))?;
        let mut tmp = p.clone().into_os_string();
        tmp.push(format!(".{}.tmp", std::process::id()));
        let tmp = PathBuf::from(tmp);
        std::fs::write(&tmp, octets).map_err(|e| io(e, &tmp))?;
        std::fs::rename(&tmp, &p).map_err(|e| io(e, &p))
    }

    fn lire(&self, cle: &str) -> Result<Option<Vec<u8>>, ErreurStockage> {
        let p = self.chemin(cle)?;
        match std::fs::read(&p) {
            Ok(v) => Ok(Some(v)),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(e) => Err(io(e, &p)),
        }
    }

    fn taille(&self, cle: &str) -> Result<Option<u64>, ErreurStockage> {
        let p = self.chemin(cle)?;
        match std::fs::metadata(&p) {
            Ok(m) => Ok(Some(m.len())),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
            Err(e) => Err(io(e, &p)),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmp(nom: &str) -> PathBuf {
        let d = std::env::temp_dir().join(format!("rw-lambda-test-{nom}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        d
    }

    #[test]
    fn local_aller_retour_et_atomicite() {
        let d = tmp("local");
        let s = StockageLocal::new(&d);
        assert_eq!(s.lire("v1/a/b.json").unwrap(), None);
        assert_eq!(s.taille("v1/a/b.json").unwrap(), None);
        s.ecrire("v1/a/b.json", b"{}", &ETAT).unwrap();
        s.ecrire("v1/a/b.json", b"{\"x\":1}", &ETAT).unwrap();
        assert_eq!(s.lire("v1/a/b.json").unwrap().unwrap(), b"{\"x\":1}");
        assert_eq!(s.taille("v1/a/b.json").unwrap(), Some(7));
        // aucun fichier temporaire résiduel
        let n = std::fs::read_dir(d.join("v1/a")).unwrap().count();
        assert_eq!(n, 1);
        std::fs::remove_dir_all(&d).unwrap();
    }

    #[test]
    fn cles_dangereuses() {
        let s = StockageLocal::new(tmp("cles"));
        for c in ["../x", "/etc/passwd", "a/../b", "a//b", "", "a\\b", "./a"] {
            assert!(s.ecrire(c, b"x", &ETAT).is_err(), "{c:?}");
            assert!(s.lire(c).is_err());
        }
    }
}
