// SPDX-License-Identifier: EUPL-1.2
//! Source des GRIB : le S3 public de Météo-France hébergé chez OVH (HTTP(S), sans clé), ou un dossier local.
//!
//! Les fichiers sont lus **en mémoire** (le plus gros cas : AROME 0,025°, deux groupes de 6 échéances
//! ≈ 2 × (55 + 43) Mo). Aucune écriture dans `/tmp`. Contrôles : taille annoncée (`Content-Length`) égale à
//! la taille reçue, reprise par `Range` après coupure, 404 = fichier non publié (erreur « absente »).

use crate::erreur::ErreurSource;
use crate::http::{self, Delais};
use rw_cli::canon::resolution;
use std::io::Read;
use std::path::PathBuf;
use std::time::Duration;

pub const BASE_OVH: &str = "https://meteofrance-pnt.s3.rbx.io.cloud.ovh.net/pnt";

pub trait SourceGrib: Send + Sync {
    /// Octets du fichier GRIB `nom` (`arome__001__SP1__12H__2026-10-17T03:00:00Z.grib2`).
    fn obtenir(&self, modele: &str, tirage_long: &str, paquet: &str, nom: &str) -> Result<Vec<u8>, ErreurSource>;
}

/// `<base>/<tirage>/arome/<résolution>/<paquet>/<nom>` (même schéma que `pipeline/ingest/download.py`).
pub fn url_fichier(base: &str, modele: &str, tirage_long: &str, paquet: &str, nom: &str) -> Result<String, ErreurSource> {
    let res = resolution(modele).map_err(|e| ErreurSource::Reseau(e.to_string()))?;
    Ok(format!("{}/{tirage_long}/arome/{res}/{paquet}/{nom}", base.trim_end_matches('/')))
}

pub struct SourceHttp {
    base: String,
    agent: ureq::Agent,
    essais: u32,
    pause_base: Duration,
}

impl SourceHttp {
    pub fn new(base: impl Into<String>) -> Self {
        let delais = Delais { global: Duration::from_secs(180), ..Delais::default() };
        Self { base: base.into(), agent: http::agent(delais, http::AGENT_UTILISATEUR), essais: 4, pause_base: Duration::from_secs(1) }
    }

    pub fn avec_essais(mut self, essais: u32, pause_base: Duration) -> Self {
        self.essais = essais.max(1);
        self.pause_base = pause_base;
        self
    }

    /// Télécharge avec reprise : après une coupure ou une troncature, on redemande `Range: bytes=<reçus>-`.
    pub fn telecharger(&self, url: &str) -> Result<Vec<u8>, ErreurSource> {
        let mut tampon: Vec<u8> = Vec::new();
        let mut attendu: Option<u64> = None;
        let mut derniere = ErreurSource::Reseau("aucun essai".into());
        for essai in 0..self.essais {
            if essai > 0 {
                std::thread::sleep(http::pause(self.pause_base, essai - 1));
            }
            let mut req = self.agent.get(url);
            if !tampon.is_empty() {
                req = req.header("Range", format!("bytes={}-", tampon.len()));
            }
            let mut rep = match req.call() {
                Ok(r) => r,
                Err(e) => {
                    derniere = ErreurSource::Reseau(format!("{url} : {e}"));
                    continue;
                }
            };
            let statut = rep.status().as_u16();
            match statut {
                404 => return Err(ErreurSource::Absente(url.to_string())),
                200 => {
                    // le serveur renvoie tout (pas de reprise possible) : on repart de zéro
                    tampon.clear();
                    attendu = rep.headers().get("content-length").and_then(|v| v.to_str().ok()).and_then(|v| v.parse().ok());
                    if let Some(n) = attendu {
                        tampon.reserve(n as usize);
                    }
                }
                206 => {
                    let debut = rep
                        .headers()
                        .get("content-range")
                        .and_then(|v| v.to_str().ok())
                        .and_then(|v| v.strip_prefix("bytes "))
                        .and_then(|v| v.split('-').next())
                        .and_then(|v| v.parse::<usize>().ok());
                    if debut != Some(tampon.len()) {
                        tampon.clear();
                        derniere = ErreurSource::Reseau(format!("{url} : Content-Range inattendu"));
                        continue;
                    }
                }
                416 => {
                    tampon.clear();
                    derniere = ErreurSource::Http { statut, url: url.to_string() };
                    continue;
                }
                s => {
                    derniere = ErreurSource::Http { statut: s, url: url.to_string() };
                    continue;
                }
            }
            if let Err(e) = rep.body_mut().as_reader().read_to_end(&mut tampon) {
                derniere = ErreurSource::Reseau(format!("{url} : lecture interrompue après {} octets ({e})", tampon.len()));
                continue;
            }
            match attendu {
                Some(n) if tampon.len() as u64 == n => return Ok(tampon),
                Some(n) if tampon.len() as u64 > n => {
                    derniere = ErreurSource::Reseau(format!("{url} : {} octets reçus pour {n} annoncés", tampon.len()));
                    tampon.clear();
                }
                Some(n) => derniere = ErreurSource::Reseau(format!("{url} : fichier tronqué ({} octets sur {n})", tampon.len())),
                None => return Ok(tampon),
            }
        }
        Err(derniere)
    }
}

impl SourceGrib for SourceHttp {
    fn obtenir(&self, modele: &str, tirage_long: &str, paquet: &str, nom: &str) -> Result<Vec<u8>, ErreurSource> {
        self.telecharger(&url_fichier(&self.base, modele, tirage_long, paquet, nom)?)
    }
}

/// Dossier local contenant les fichiers GRIB (recherche récursive du nom) : développement et tests.
pub struct SourceLocale {
    pub dossiers: Vec<PathBuf>,
}

impl SourceGrib for SourceLocale {
    fn obtenir(&self, _modele: &str, _tirage_long: &str, _paquet: &str, nom: &str) -> Result<Vec<u8>, ErreurSource> {
        let chemin = rw_cli::canon::localiser(nom, &self.dossiers).ok_or_else(|| ErreurSource::Absente(nom.to_string()))?;
        std::fs::read(&chemin).map_err(|e| ErreurSource::Reseau(format!("{} : {e}", chemin.display())))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn url() {
        let u = url_fichier(BASE_OVH, "arome-hd", "2026-10-17T03:00:00Z", "SP1", "arome__001__SP1__12H__2026-10-17T03:00:00Z.grib2").unwrap();
        assert_eq!(
            u,
            "https://meteofrance-pnt.s3.rbx.io.cloud.ovh.net/pnt/2026-10-17T03:00:00Z/arome/001/SP1/arome__001__SP1__12H__2026-10-17T03:00:00Z.grib2"
        );
        let u = url_fichier("http://h:1/pnt/", "arome", "2026-10-17T06:00:00Z", "SP2", "x.grib2").unwrap();
        assert_eq!(u, "http://h:1/pnt/2026-10-17T06:00:00Z/arome/0025/SP2/x.grib2");
    }
}
