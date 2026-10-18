// SPDX-License-Identifier: EUPL-1.2
//! Listage de buckets S3 : structures communes, lecture du XML `ListObjectsV2` et client de listage **public non
//! signé** (le S3 ouvert de Météo-France chez OVH ne demande ni clé ni compte).

use crate::erreur::ErreurStockage;
use crate::http::{self, Delais};
use crate::sigv4::encoder_segment;
use crate::temps;
use std::io::Read;
use std::time::Duration;

/// Un objet d'un listage.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Objet {
    pub cle: String,
    pub taille: u64,
    /// Date de dernière modification (secondes Unix).
    pub modifie: i64,
    /// `ETag` sans guillemets (vide si absent).
    pub etag: String,
}

/// Résultat d'un listage complet (toutes les pages).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Listage {
    pub objets: Vec<Objet>,
    /// `CommonPrefixes` (avec le délimiteur final), seulement si un délimiteur a été demandé.
    pub prefixes: Vec<String>,
}

/// Une page de réponse `ListObjectsV2`.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Page {
    pub objets: Vec<Objet>,
    pub prefixes: Vec<String>,
    pub tronquee: bool,
    pub suite: Option<String>,
}

fn decoder_entites(s: &str) -> String {
    if !s.contains('&') {
        return s.to_string();
    }
    let mut o = String::with_capacity(s.len());
    let mut reste = s;
    while let Some(i) = reste.find('&') {
        o.push_str(&reste[..i]);
        let suite = &reste[i..];
        let (rempl, long) = if suite.starts_with("&amp;") {
            ("&", 5)
        } else if suite.starts_with("&lt;") {
            ("<", 4)
        } else if suite.starts_with("&gt;") {
            (">", 4)
        } else if suite.starts_with("&quot;") {
            ("\"", 6)
        } else if suite.starts_with("&apos;") {
            ("'", 6)
        } else {
            ("&", 1)
        };
        o.push_str(rempl);
        reste = &suite[long..];
    }
    o.push_str(reste);
    o
}

/// Texte de la première balise `<nom>…</nom>` de `xml`.
fn balise<'a>(xml: &'a str, nom: &str) -> Option<&'a str> {
    let ouv = format!("<{nom}>");
    let a = xml.find(&ouv)? + ouv.len();
    let z = xml[a..].find(&format!("</{nom}>"))? + a;
    Some(&xml[a..z])
}

/// Blocs `<nom>…</nom>` successifs.
fn blocs<'a>(xml: &'a str, nom: &str) -> Vec<&'a str> {
    let (ouv, fer) = (format!("<{nom}>"), format!("</{nom}>"));
    let mut v = Vec::new();
    let mut reste = xml;
    while let Some(a) = reste.find(&ouv) {
        let debut = a + ouv.len();
        let Some(z) = reste[debut..].find(&fer) else { break };
        v.push(&reste[debut..debut + z]);
        reste = &reste[debut + z + fer.len()..];
    }
    v
}

/// Analyse une page `ListObjectsV2` (XML de S3 ou d'OVH).
pub fn analyser_page(xml: &str) -> Result<Page, String> {
    if !xml.contains("<ListBucketResult") {
        return Err(format!("réponse de listage inattendue : {}", xml.chars().take(200).collect::<String>()));
    }
    let mut page = Page::default();
    for c in blocs(xml, "Contents") {
        let cle = decoder_entites(balise(c, "Key").ok_or("Contents sans Key")?);
        let taille = balise(c, "Size").and_then(|t| t.trim().parse().ok()).ok_or_else(|| format!("Size illisible pour {cle}"))?;
        let modifie = balise(c, "LastModified").and_then(|d| temps::depuis_iso(d.trim())).ok_or_else(|| format!("LastModified illisible pour {cle}"))?;
        let etag = balise(c, "ETag").map(|e| decoder_entites(e).trim_matches('"').to_string()).unwrap_or_default();
        page.objets.push(Objet { cle, taille, modifie, etag });
    }
    // les préfixes communs : hors des blocs <Contents>
    for p in blocs(xml, "CommonPrefixes") {
        if let Some(v) = balise(p, "Prefix") {
            page.prefixes.push(decoder_entites(v));
        }
    }
    page.tronquee = balise(xml, "IsTruncated").is_some_and(|t| t.trim() == "true");
    page.suite = balise(xml, "NextContinuationToken").map(|t| decoder_entites(t));
    if page.tronquee && page.suite.is_none() {
        return Err("listage tronqué sans NextContinuationToken".into());
    }
    Ok(page)
}

/// Requête canonique d'un `ListObjectsV2` : paramètres triés par nom, valeurs encodées (RFC 3986).
pub fn requete_liste(prefixe: &str, delimiteur: Option<char>, suite: Option<&str>, max: u32) -> String {
    let mut p: Vec<(&str, String)> = vec![("list-type", "2".into()), ("max-keys", max.to_string()), ("prefix", prefixe.to_string())];
    if let Some(d) = delimiteur {
        p.push(("delimiter", d.to_string()));
    }
    if let Some(s) = suite {
        p.push(("continuation-token", s.to_string()));
    }
    p.sort_by(|a, b| a.0.cmp(b.0));
    p.iter().map(|(k, v)| format!("{k}={}", encoder_segment(v))).collect::<Vec<_>>().join("&")
}

/// Garde-fou : nombre maximal de pages suivies (1 000 objets par page).
pub const PAGES_MAX: usize = 200;

/// Client de listage public (sans signature) : `GET <base>/?list-type=2&prefix=…`.
pub struct ListeurPublic {
    base: String,
    agent: ureq::Agent,
    essais: u32,
    pause_base: Duration,
}

impl ListeurPublic {
    /// `base` : racine du bucket, par exemple `https://meteofrance-pnt.s3.rbx.io.cloud.ovh.net`.
    pub fn new(base: impl Into<String>) -> Self {
        let delais = Delais { global: Duration::from_secs(60), ..Delais::default() };
        Self { base: base.into(), agent: http::agent(delais, http::AGENT_UTILISATEUR), essais: 3, pause_base: Duration::from_secs(1) }
    }

    pub fn avec_essais(mut self, essais: u32, pause_base: Duration) -> Self {
        self.essais = essais.max(1);
        self.pause_base = pause_base;
        self
    }

    fn page(&self, prefixe: &str, suite: Option<&str>) -> Result<Page, ErreurStockage> {
        let url = format!("{}/?{}", self.base.trim_end_matches('/'), requete_liste(prefixe, None, suite, 1000));
        let mut derniere = ErreurStockage::Io("aucun essai".into());
        for essai in 0..self.essais {
            if essai > 0 {
                std::thread::sleep(http::pause(self.pause_base, essai - 1));
            }
            match self.agent.get(&url).call() {
                Ok(mut rep) => {
                    let statut = rep.status().as_u16();
                    let mut corps = Vec::new();
                    if let Err(e) = rep.body_mut().as_reader().take(32 << 20).read_to_end(&mut corps) {
                        derniere = ErreurStockage::Io(format!("lecture du listage {prefixe} : {e}"));
                        continue;
                    }
                    let texte = String::from_utf8_lossy(&corps).into_owned();
                    if statut == 408 || statut == 429 || statut >= 500 {
                        derniere = ErreurStockage::Http { statut, cle: prefixe.into(), corps: texte.chars().take(300).collect() };
                        continue;
                    }
                    if !(200..300).contains(&statut) {
                        return Err(ErreurStockage::Http { statut, cle: prefixe.into(), corps: texte.chars().take(300).collect() });
                    }
                    return analyser_page(&texte).map_err(ErreurStockage::Io);
                }
                Err(e) => derniere = ErreurStockage::Io(format!("listage {prefixe} : {e}")),
            }
        }
        Err(derniere)
    }

    /// Tous les objets sous `prefixe` (toutes les pages), triés par clé.
    pub fn lister(&self, prefixe: &str) -> Result<Vec<Objet>, ErreurStockage> {
        let mut tous = Vec::new();
        let mut suite: Option<String> = None;
        for _ in 0..PAGES_MAX {
            let p = self.page(prefixe, suite.as_deref())?;
            tous.extend(p.objets);
            if !p.tronquee {
                tous.sort_by(|a, b| a.cle.cmp(&b.cle));
                return Ok(tous);
            }
            suite = p.suite;
        }
        Err(ErreurStockage::Io(format!("listage {prefixe} : plus de {PAGES_MAX} pages")))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const XML: &str = r#"<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/"><Name>b</Name><Prefix>pnt/</Prefix><KeyCount>3</KeyCount><MaxKeys>1000</MaxKeys><IsTruncated>true</IsTruncated><NextContinuationToken>tok/+=</NextContinuationToken>
<Contents><Key>pnt/a&amp;b.grib2</Key><LastModified>2026-10-17T08:42:19.000Z</LastModified><ETag>&quot;abc&quot;</ETag><Size>1234</Size><StorageClass>STANDARD</StorageClass></Contents>
<Contents><Key>pnt/c.grib2</Key><LastModified>2026-10-17T08:43:00Z</LastModified><Size>7</Size></Contents>
<CommonPrefixes><Prefix>pnt/2026-10-17T03:00:00Z/</Prefix></CommonPrefixes></ListBucketResult>"#;

    #[test]
    fn page_complete() {
        let p = analyser_page(XML).unwrap();
        assert_eq!(p.objets.len(), 2);
        assert_eq!(p.objets[0], Objet { cle: "pnt/a&b.grib2".into(), taille: 1234, modifie: temps::epoch(2026, 10, 17, 8, 42, 19), etag: "abc".into() });
        assert_eq!(p.objets[1].etag, "");
        assert_eq!(p.prefixes, vec!["pnt/2026-10-17T03:00:00Z/"]);
        assert!(p.tronquee);
        assert_eq!(p.suite.as_deref(), Some("tok/+="));
    }

    #[test]
    fn page_invalide() {
        assert!(analyser_page("<Error><Code>AccessDenied</Code></Error>").is_err());
        assert!(analyser_page("<ListBucketResult><IsTruncated>true</IsTruncated></ListBucketResult>").is_err());
        assert!(analyser_page("<ListBucketResult><Contents><Key>a</Key><Size>x</Size><LastModified>2026-10-17T08:42:19Z</LastModified></Contents></ListBucketResult>").is_err());
        let vide = analyser_page("<ListBucketResult><IsTruncated>false</IsTruncated></ListBucketResult>").unwrap();
        assert!(vide.objets.is_empty() && !vide.tronquee);
    }

    #[test]
    fn requete_canonique_triee() {
        assert_eq!(requete_liste("v1/a b/", Some('/'), None, 1000), "delimiter=%2F&list-type=2&max-keys=1000&prefix=v1%2Fa%20b%2F");
        assert_eq!(
            requete_liste("p", None, Some("t/+="), 5),
            "continuation-token=t%2F%2B%3D&list-type=2&max-keys=5&prefix=p"
        );
    }
}
