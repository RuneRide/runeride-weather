// SPDX-License-Identifier: EUPL-1.2
//! Client S3 minimal (PutObject, GetObject, HeadObject, ListObjectsV2, DeleteObject) signé SigV4, sans le SDK AWS.
//!
//! Choix et mesures : voir `docs/poc-rust/lambda.md`. Les identifiants viennent de l'environnement de la
//! fonction (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`, fournis par le runtime Lambda).

use crate::erreur::ErreurStockage;
use crate::http::{self, Delais};
use crate::sigv4::{self, Demande};
use crate::liste::{self, Listage};
use crate::stockage::{cle_sure, prefixe_sur, Meta, Stockage};
use crate::temps;
use std::io::Read;
use std::time::Duration;

#[derive(Clone)]
pub struct Identifiants {
    pub cle_acces: String,
    pub secret: String,
    pub jeton: Option<String>,
}

impl std::fmt::Debug for Identifiants {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str("Identifiants(***)")
    }
}

impl Identifiants {
    /// Lit les variables d'environnement standard de Lambda.
    pub fn depuis_env() -> Result<Self, ErreurStockage> {
        let var = |n: &str| std::env::var(n).map_err(|_| ErreurStockage::Configuration(format!("variable {n} absente")));
        Ok(Self { cle_acces: var("AWS_ACCESS_KEY_ID")?, secret: var("AWS_SECRET_ACCESS_KEY")?, jeton: std::env::var("AWS_SESSION_TOKEN").ok() })
    }
}

#[derive(Debug, Clone)]
pub struct ConfigS3 {
    pub bucket: String,
    pub region: String,
    /// Point de terminaison explicite (tests : `http://127.0.0.1:8099`, accès « path-style »). Absent : AWS.
    pub endpoint: Option<String>,
    pub essais: u32,
    pub pause_base: Duration,
}

impl ConfigS3 {
    pub fn aws(bucket: impl Into<String>, region: impl Into<String>) -> Self {
        Self { bucket: bucket.into(), region: region.into(), endpoint: None, essais: 4, pause_base: Duration::from_millis(500) }
    }
}

pub struct StockageS3 {
    cfg: ConfigS3,
    ident: Identifiants,
    agent: ureq::Agent,
}

impl StockageS3 {
    pub fn new(cfg: ConfigS3, ident: Identifiants) -> Result<Self, ErreurStockage> {
        if cfg.bucket.is_empty() || cfg.region.is_empty() {
            return Err(ErreurStockage::Configuration("bucket ou région vide".into()));
        }
        Ok(Self { cfg, ident, agent: http::agent(Delais::default(), http::AGENT_UTILISATEUR) })
    }

    /// (URL, hôte signé, chemin canonique) d'une clé ; `cle` vide = le bucket lui-même (listage). `requete` : chaîne de
    /// requête canonique (déjà triée et encodée), éventuellement vide.
    fn adresse(&self, cle: &str, requete: &str) -> (String, String, String) {
        let ck = sigv4::encoder_chemin(cle);
        let (base, hote, chemin) = match &self.cfg.endpoint {
            Some(e) => {
                let base = e.trim_end_matches('/').to_string();
                let hote = base.split("://").nth(1).unwrap_or(&base).to_string();
                let chemin = if cle.is_empty() { format!("/{}", sigv4::encoder_segment(&self.cfg.bucket)) } else { format!("/{}/{}", sigv4::encoder_segment(&self.cfg.bucket), ck) };
                (base, hote, chemin)
            }
            None if self.cfg.bucket.contains('.') => {
                // un bucket avec des points ne passe pas le certificat générique : accès « path-style »
                let hote = format!("s3.{}.amazonaws.com", self.cfg.region);
                let chemin = if cle.is_empty() { format!("/{}", self.cfg.bucket) } else { format!("/{}/{}", self.cfg.bucket, ck) };
                (format!("https://{hote}"), hote, chemin)
            }
            None => {
                let hote = format!("{}.s3.{}.amazonaws.com", self.cfg.bucket, self.cfg.region);
                let chemin = format!("/{ck}");
                (format!("https://{hote}"), hote, chemin)
            }
        };
        let url = if requete.is_empty() { format!("{base}{chemin}") } else { format!("{base}{chemin}?{requete}") };
        (url, hote, chemin)
    }

    /// Une requête signée, avec nouvelles tentatives sur erreurs transitoires (réseau, 5xx, 429, 408).
    /// Retourne (statut, corps tronqué à 64 Kio en cas d'erreur ou octets complets en lecture).
    fn requete(&self, methode: &str, cle: &str, requete: &str, charge: &[u8], meta: Option<&Meta>) -> Result<(u16, Vec<u8>, Option<u64>), ErreurStockage> {
        if !cle.is_empty() {
            cle_sure(cle)?;
        }
        let (url, hote, chemin) = self.adresse(cle, requete);
        let hash = sigv4::sha256_hex(charge);
        let mut derniere = ErreurStockage::Io("aucun essai".into());
        for essai in 0..self.cfg.essais.max(1) {
            if essai > 0 {
                std::thread::sleep(http::pause(self.cfg.pause_base, essai - 1));
            }
            let date = temps::amz(temps::maintenant());
            let mut entetes: Vec<(String, String)> = vec![("host".into(), hote.clone())];
            if let Some(m) = meta {
                entetes.push(("cache-control".into(), m.cache.into()));
                entetes.push(("content-type".into(), m.type_contenu.into()));
            }
            entetes.push(("x-amz-content-sha256".into(), hash.clone()));
            entetes.push(("x-amz-date".into(), date.clone()));
            if let Some(j) = &self.ident.jeton {
                entetes.push(("x-amz-security-token".into(), j.clone()));
            }
            entetes.sort();
            let auth = sigv4::autorisation(
                &Demande { methode, chemin: &chemin, requete, entetes: &entetes, hash_charge: &hash, region: &self.cfg.region, service: "s3", date_amz: &date },
                &self.ident.cle_acces,
                &self.ident.secret,
            );
            let envoi = |mut b: ureq::RequestBuilder<ureq::typestate::WithoutBody>| {
                for (k, v) in &entetes {
                    if k != "host" {
                        b = b.header(k.as_str(), v.as_str());
                    }
                }
                b.header("authorization", auth.as_str())
            };
            let resultat = match methode {
                "PUT" => {
                    let mut b = self.agent.put(&url);
                    for (k, v) in &entetes {
                        if k != "host" {
                            b = b.header(k.as_str(), v.as_str());
                        }
                    }
                    b.header("authorization", auth.as_str()).send(charge)
                }
                "GET" => envoi(self.agent.get(&url)).call(),
                "HEAD" => envoi(self.agent.head(&url)).call(),
                "DELETE" => envoi(self.agent.delete(&url)).call(),
                _ => return Err(ErreurStockage::Io(format!("méthode {methode} non gérée"))),
            };
            match resultat {
                Ok(mut rep) => {
                    let statut = rep.status().as_u16();
                    let longueur = rep.headers().get("content-length").and_then(|v| v.to_str().ok()).and_then(|v| v.parse::<u64>().ok());
                    let mut corps = Vec::new();
                    if methode != "HEAD" {
                        let limite = if statut < 300 { u64::MAX } else { 65536 };
                        if let Err(e) = rep.body_mut().as_reader().take(limite).read_to_end(&mut corps) {
                            derniere = ErreurStockage::Io(format!("lecture de la réponse S3 : {e}"));
                            continue;
                        }
                    }
                    if statut == 408 || statut == 429 || statut >= 500 {
                        derniere = ErreurStockage::Http { statut, cle: cle.into(), corps: String::from_utf8_lossy(&corps).chars().take(300).collect() };
                        continue;
                    }
                    return Ok((statut, corps, longueur));
                }
                Err(e) => derniere = ErreurStockage::Io(format!("{methode} {cle} : {e}")),
            }
        }
        Err(derniere)
    }

    fn echec(statut: u16, cle: &str, corps: &[u8]) -> ErreurStockage {
        ErreurStockage::Http { statut, cle: cle.into(), corps: String::from_utf8_lossy(corps).chars().take(300).collect() }
    }
}

impl Stockage for StockageS3 {
    fn ecrire(&self, cle: &str, octets: &[u8], meta: &Meta) -> Result<(), ErreurStockage> {
        // PutObject est atomique côté S3 : un lecteur voit l'ancien objet ou le nouveau, jamais un fichier partiel.
        let (s, corps, _) = self.requete("PUT", cle, "", octets, Some(meta))?;
        if (200..300).contains(&s) { Ok(()) } else { Err(Self::echec(s, cle, &corps)) }
    }

    fn lire(&self, cle: &str) -> Result<Option<Vec<u8>>, ErreurStockage> {
        let (s, corps, _) = self.requete("GET", cle, "", b"", None)?;
        match s {
            200..=299 => Ok(Some(corps)),
            404 => Ok(None),
            _ => Err(Self::echec(s, cle, &corps)),
        }
    }

    fn taille(&self, cle: &str) -> Result<Option<u64>, ErreurStockage> {
        let (s, corps, l) = self.requete("HEAD", cle, "", b"", None)?;
        match s {
            200..=299 => l.map(Some).ok_or_else(|| ErreurStockage::Io(format!("HEAD {cle} sans Content-Length"))),
            404 => Ok(None),
            _ => Err(Self::echec(s, cle, &corps)),
        }
    }

    fn lister(&self, prefixe: &str, delimiteur: bool) -> Result<Listage, ErreurStockage> {
        prefixe_sur(prefixe)?;
        let mut res = Listage::default();
        let mut suite: Option<String> = None;
        for _ in 0..liste::PAGES_MAX {
            let q = liste::requete_liste(prefixe, delimiteur.then_some('/'), suite.as_deref(), 1000);
            let (s, corps, _) = self.requete("GET", "", &q, b"", None)?;
            if !(200..300).contains(&s) {
                return Err(Self::echec(s, prefixe, &corps));
            }
            let page = liste::analyser_page(&String::from_utf8_lossy(&corps)).map_err(ErreurStockage::Io)?;
            res.objets.extend(page.objets);
            res.prefixes.extend(page.prefixes);
            if !page.tronquee {
                res.objets.sort_by(|a, b| a.cle.cmp(&b.cle));
                res.prefixes.sort();
                return Ok(res);
            }
            suite = page.suite;
        }
        Err(ErreurStockage::Io(format!("listage {prefixe} : plus de {} pages", liste::PAGES_MAX)))
    }

    fn supprimer(&self, cle: &str) -> Result<(), ErreurStockage> {
        let (s, corps, _) = self.requete("DELETE", cle, "", b"", None)?;
        // S3 répond 204 même si la clé n'existe pas ; 404 (bucket sans versionnage ou serveur de test) est aussi un succès
        if (200..300).contains(&s) || s == 404 { Ok(()) } else { Err(Self::echec(s, cle, &corps)) }
    }
}
