// SPDX-License-Identifier: EUPL-1.2
//! Reconstruction du manifeste : listage des états d'échéance dans le bucket de données, agrégation, écriture en dernier,
//! purge facultative des tirages expirés.

use crate::construire::{self, Domaine, Entrees, MODELES};
use crate::domaine;
use crate::etat::{self, Analyse, Etat};
use rw_aws::erreur::ErreurStockage;
use rw_aws::s3::{ConfigS3, Identifiants, StockageS3};
use rw_aws::stockage::{Meta, Stockage, StockageLocal};
use rw_aws::temps;
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap};
use std::sync::Mutex;
use std::time::Duration;

#[derive(Debug, Clone)]
pub struct Config {
    /// Préfixe des clés du bucket de données (`v1`).
    pub prefixe: String,
    /// Âge maximal (heures après l'heure nominale du tirage) d'un tirage cité par le manifeste. Doit rester inférieur à la
    /// durée de vie des objets (règle de cycle de vie : 2 jours = 48 h, objets écrits de +2 à +6 h après l'heure
    /// nominale) pour que le manifeste ne cite jamais un fichier expiré. Défaut : 42 h.
    pub age_ref_h: i64,
    /// Nombre maximal de tirages cités par modèle (les plus récents). Défaut : 6.
    pub tirages_max: usize,
    /// Purge physique : âge (heures) au-delà duquel les objets d'un tirage sont supprimés. Absent (défaut) : pas de
    /// suppression, la règle de cycle de vie du bucket s'en charge. Doit dépasser `age_ref_h`.
    pub purge_age_h: Option<i64>,
    /// Nombre maximal d'objets supprimés par invocation.
    pub purge_max: usize,
    /// `Cache-Control: public, max-age=<n>` du manifeste (défaut 30 s).
    pub cache_s: u32,
    pub tolerance_deg: f64,
}

impl Default for Config {
    fn default() -> Self {
        Self { prefixe: "v1".into(), age_ref_h: 42, tirages_max: 6, purge_age_h: None, purge_max: 2000, cache_s: 30, tolerance_deg: 0.04 }
    }
}

impl Config {
    pub fn depuis(lire: impl Fn(&str) -> Option<String>) -> Result<Self, String> {
        let var = |n: &str| lire(n).filter(|v| !v.trim().is_empty());
        let nombre = |n: &str, v: &str| v.trim().parse::<i64>().map_err(|_| format!("{n} : nombre entier attendu, reçu {v:?}"));
        let mut c = Self::default();
        if let Some(v) = var("RW_PREFIXE") {
            c.prefixe = v.trim_matches('/').to_string();
        }
        if let Some(v) = var("RW_AGE_REF_H") {
            c.age_ref_h = nombre("RW_AGE_REF_H", &v)?;
        }
        if let Some(v) = var("RW_TIRAGES_MANIFESTE") {
            c.tirages_max = nombre("RW_TIRAGES_MANIFESTE", &v)? as usize;
        }
        if let Some(v) = var("RW_PURGE_AGE_H") {
            c.purge_age_h = Some(nombre("RW_PURGE_AGE_H", &v)?);
        }
        if let Some(v) = var("RW_PURGE_MAX") {
            c.purge_max = nombre("RW_PURGE_MAX", &v)? as usize;
        }
        if let Some(v) = var("RW_MANIFESTE_CACHE_S") {
            c.cache_s = nombre("RW_MANIFESTE_CACHE_S", &v)? as u32;
        }
        if c.age_ref_h <= 0 || c.tirages_max == 0 {
            return Err("RW_AGE_REF_H et RW_TIRAGES_MANIFESTE doivent être > 0".into());
        }
        if let Some(p) = c.purge_age_h {
            if p <= c.age_ref_h {
                return Err(format!("RW_PURGE_AGE_H ({p}) doit dépasser RW_AGE_REF_H ({}) : on ne supprime jamais ce que le manifeste cite", c.age_ref_h));
            }
        }
        Ok(c)
    }

    pub fn cle_manifeste(&self) -> String {
        format!("{}/manifest.json", self.prefixe)
    }
}

#[derive(Default)]
struct Cache {
    /// (clé, ETag) → état analysé (`None` = échec définitif).
    etats: HashMap<(String, String), Option<Etat>>,
    /// (clé, octets) → domaine calculé.
    domaines: HashMap<(String, u64), Domaine>,
}

pub struct Contexte {
    pub cfg: Config,
    pub donnees: Box<dyn Stockage>,
    /// `Meta` du manifeste (le `Cache-Control` vient de la configuration ; la chaîne est allouée une fois pour le processus).
    meta: Meta,
    cache: Mutex<Cache>,
}

/// Une ligne de journal JSON (CloudWatch Logs : un événement par ligne).
pub fn journal(niveau: &str, mut champs: Value) {
    champs["niveau"] = json!(niveau);
    eprintln!("{champs}");
}

fn var(n: &str) -> Option<String> {
    std::env::var(n).ok().filter(|v| !v.trim().is_empty())
}

impl Contexte {
    pub fn nouveau(cfg: Config, donnees: Box<dyn Stockage>) -> Self {
        // allocation unique par processus (la configuration ne change pas pendant la vie de la fonction)
        let cache: &'static str = Box::leak(format!("public, max-age={}", cfg.cache_s).into_boxed_str());
        Self { cfg, donnees, meta: Meta { type_contenu: "application/json", cache }, cache: Mutex::new(Cache::default()) }
    }

    pub fn depuis_env() -> Result<Self, String> {
        let cfg = Config::depuis(|n| std::env::var(n).ok())?;
        let pause = Duration::from_millis(var("RW_PAUSE_MS").and_then(|v| v.parse().ok()).unwrap_or(1000));
        let donnees: Box<dyn Stockage> = match var("RW_SORTIE") {
            Some(d) if d.starts_with("local:") => Box::new(StockageLocal::new(&d["local:".len()..])),
            _ => {
                let bucket = var("RW_BUCKET").ok_or("RW_BUCKET absente")?;
                let region = var("AWS_REGION").or_else(|| var("AWS_DEFAULT_REGION")).ok_or("AWS_REGION absente")?;
                let mut c = ConfigS3::aws(bucket, region);
                c.endpoint = var("RW_S3_ENDPOINT");
                c.pause_base = pause / 2;
                Box::new(StockageS3::new(c, Identifiants::depuis_env().map_err(|e| e.to_string())?).map_err(|e| e.to_string())?)
            }
        };
        Ok(Self::nouveau(cfg, donnees))
    }
}

/// Un tirage trouvé dans le bucket.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TirageListe {
    pub id: String,
    pub epoch: i64,
}

/// `2026-10-17T06Z` → secondes Unix, `None` si la forme est invalide.
pub fn epoch_tirage(id: &str) -> Option<i64> {
    (id.len() == 14 && id.ends_with('Z')).then(|| temps::depuis_iso(id)).flatten()
}

/// Un événement S3 (corps de message SQS) concerne-t-il un état d'échéance `<préfixe>/<modèle>/<tirage>/_etat/NN.json` ?
pub fn cle_pertinente(cle: &str, prefixe: &str) -> bool {
    let Some(reste) = cle.strip_prefix(prefixe).and_then(|r| r.strip_prefix('/')) else { return false };
    let seg: Vec<&str> = reste.split('/').collect();
    seg.len() == 4
        && MODELES.iter().any(|m| m.cle == seg[0])
        && epoch_tirage(seg[1]).is_some()
        && seg[2] == "_etat"
        && seg[3].strip_suffix(".json").is_some_and(|n| n.len() == 2 && n.bytes().all(|b| b.is_ascii_digit()))
}

/// Décode les `%XX` et `+` d'une clé d'objet des notifications S3.
pub fn decoder_cle(s: &str) -> String {
    let b = s.as_bytes();
    let mut o = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        match b[i] {
            b'+' => o.push(b' '),
            b'%' if i + 2 < b.len() => {
                match (char::from(b[i + 1]).to_digit(16), char::from(b[i + 2]).to_digit(16)) {
                    (Some(h), Some(l)) => {
                        o.push((h * 16 + l) as u8);
                        i += 2;
                    }
                    _ => o.push(b'%'),
                }
            }
            c => o.push(c),
        }
        i += 1;
    }
    String::from_utf8_lossy(&o).into_owned()
}

/// Clés d'objets d'un corps de message SQS contenant une notification S3 (vide pour `s3:TestEvent`).
pub fn cles_de_notification(corps: &str) -> Vec<String> {
    let Ok(v) = serde_json::from_str::<Value>(corps) else { return Vec::new() };
    v["Records"].as_array().into_iter().flatten().filter_map(|r| r["s3"]["object"]["key"].as_str().map(decoder_cle)).collect()
}

fn erreur_s(e: ErreurStockage) -> String {
    e.to_string()
}

impl Contexte {
    fn tirages(&self, modele: &str) -> Result<Vec<TirageListe>, String> {
        let pref = format!("{}/{modele}/", self.cfg.prefixe);
        let mut v: Vec<TirageListe> = self
            .donnees
            .lister(&pref, true)
            .map_err(erreur_s)?
            .prefixes
            .iter()
            .filter_map(|p| {
                let id = p.strip_prefix(&pref)?.trim_end_matches('/');
                Some(TirageListe { id: id.to_string(), epoch: epoch_tirage(id)? })
            })
            .collect();
        v.sort_by_key(|t| t.epoch);
        Ok(v)
    }

    fn lire_etats(&self, modele: &str, tirage: &TirageListe, vus: &mut Vec<(String, String)>, echecs: &mut usize, ignores: &mut usize) -> Result<BTreeMap<u32, Etat>, String> {
        let pref = format!("{}/{modele}/{}/_etat/", self.cfg.prefixe, tirage.id);
        let mut res = BTreeMap::new();
        for o in self.donnees.lister(&pref, true).map_err(erreur_s)?.objets {
            if !cle_pertinente(&o.cle, &self.cfg.prefixe) {
                continue;
            }
            let cle_cache = (o.cle.clone(), o.etag.clone());
            vus.push(cle_cache.clone());
            let connu = self.cache.lock().unwrap().etats.get(&cle_cache).cloned();
            let analyse = match connu {
                Some(a) => a,
                None => {
                    let Some(octets) = self.donnees.lire(&o.cle).map_err(erreur_s)? else { continue }; // supprimé entre-temps
                    let a = match etat::analyser(&octets) {
                        Ok(Analyse::Complet(e)) => Some(*e),
                        Ok(Analyse::Echec) => None,
                        Err(e) => {
                            journal("WARN", json!({"evenement": "etat_ignore", "cle": o.cle, "raison": e}));
                            *ignores += 1;
                            continue;
                        }
                    };
                    self.cache.lock().unwrap().etats.insert(cle_cache, a.clone());
                    a
                }
            };
            match analyse {
                Some(e) if e.modele == modele && e.tirage == tirage.id => {
                    res.insert(e.echeance, e);
                }
                Some(e) => {
                    journal("WARN", json!({"evenement": "etat_incoherent", "cle": o.cle, "modele": e.modele, "tirage": e.tirage}));
                    *ignores += 1;
                }
                None => *echecs += 1,
            }
        }
        Ok(res)
    }

    /// Domaine du modèle : masque du tirage retenu le plus récent qui en possède un (calculé une fois par fichier).
    fn domaine(&self, modele: &str, tirages: &construire::Tirages) -> Result<Option<Domaine>, String> {
        let Some((cle, octets, id)) = tirages.iter().rev().find_map(|(id, t)| t.values().find_map(|e| e.domaine.clone().map(|(c, o)| (c, o, id.clone())))) else { return Ok(None) };
        if let Some(d) = self.cache.lock().unwrap().domaines.get(&(cle.clone(), octets)) {
            return Ok(Some(d.clone()));
        }
        let Some(brut) = self.donnees.lire(&cle).map_err(erreur_s)? else { return Ok(None) };
        let masque = domaine::lire_masque(&brut).map_err(|e| format!("{cle} : {e:#}"))?;
        let polygone = domaine::polygone_depuis_masque(&masque, self.cfg.tolerance_deg, domaine::RAYONS).map_err(|e| format!("{cle} : {e:#}"))?;
        let ech = cle.rsplit('/').next().and_then(|n| n.strip_suffix(".npz")).unwrap_or("03");
        let d = Domaine { polygone, source: format!("{modele}/{id}/_domaine/{ech}") };
        self.cache.lock().unwrap().domaines.insert((cle, octets), d.clone());
        Ok(Some(d))
    }

    /// Reconstruit et publie le manifeste (écrit en dernier). Retourne le bilan JSON.
    pub fn reconstruire(&self, maintenant: i64) -> Result<Value, String> {
        let cfg = &self.cfg;
        let mut etats: BTreeMap<String, construire::Tirages> = BTreeMap::new();
        let mut domaines: BTreeMap<String, Domaine> = BTreeMap::new();
        let mut vus = Vec::new();
        let mut bilan_modeles = Map::new();
        let mut a_purger: Vec<(String, TirageListe)> = Vec::new();
        let (mut echecs, mut ignores) = (0usize, 0usize);
        for m in MODELES.iter() {
            let tous = self.tirages(m.cle)?;
            let (mut retenus, mut expires) = (Vec::new(), 0usize);
            for t in tous.iter().rev() {
                if maintenant - t.epoch > cfg.age_ref_h * 3600 || retenus.len() >= cfg.tirages_max {
                    expires += 1;
                    if cfg.purge_age_h.is_some_and(|p| maintenant - t.epoch > p * 3600) {
                        a_purger.push((m.cle.to_string(), t.clone()));
                    }
                } else {
                    retenus.push(t.clone());
                }
            }
            let mut par_tirage = construire::Tirages::new();
            let mut n_etats = 0;
            for t in &retenus {
                let e = self.lire_etats(m.cle, t, &mut vus, &mut echecs, &mut ignores)?;
                n_etats += e.len();
                if !e.is_empty() {
                    par_tirage.insert(t.id.clone(), e);
                }
            }
            if let Some(d) = self.domaine(m.cle, &par_tirage)? {
                domaines.insert(m.cle.to_string(), d);
            }
            bilan_modeles.insert(m.cle.to_string(), json!({"tirages_cites": par_tirage.len(), "tirages_expires": expires, "etats": n_etats}));
            etats.insert(m.cle.to_string(), par_tirage);
        }
        // oublie les entrées de cache qui ne correspondent plus à aucun objet
        {
            let mut c = self.cache.lock().unwrap();
            let gardes: std::collections::HashSet<&(String, String)> = vus.iter().collect();
            c.etats.retain(|k, _| gardes.contains(k));
        }
        let generated = temps::iso(maintenant);
        let manifeste = construire::construire(&Entrees { etats: &etats, domaines: &domaines, generated: &generated, tolerance_deg: cfg.tolerance_deg }).map_err(|e| format!("{e:#}"))?;
        for m in MODELES.iter() {
            if let Some(v) = manifeste["models"][m.cle]["latest_complete"].as_str() {
                bilan_modeles.get_mut(m.cle).unwrap()["latest_complete"] = json!(v);
            }
        }
        // inchangé (hors `generated`) : pas de PUT, ni de changement d'ETag pour les clients
        let cle = cfg.cle_manifeste();
        let ancien = self.donnees.lire(&cle).map_err(erreur_s)?.and_then(|b| serde_json::from_slice::<Value>(&b).ok());
        let ecrit = !ancien.as_ref().is_some_and(|a| construire::equivalents(a, &manifeste));
        let octets = serde_json::to_vec(&manifeste).map_err(|e| e.to_string())?;
        if ecrit {
            // le manifeste est le DERNIER écrit : quand il cite une échéance, son fichier est déjà lisible
            self.donnees.ecrire(&cle, &octets, &self.meta).map_err(|e| format!("écriture du manifeste : {e}"))?;
        }
        // purge : après l'écriture, et seulement ce que le manifeste ne cite pas (RW_PURGE_AGE_H > RW_AGE_REF_H)
        let mut supprimes = 0usize;
        let mut purge_erreur: Option<String> = None;
        'purge: for (modele, t) in &a_purger {
            // tuiles et états du tirage, puis GeoJSON des isobares (`<préfixe>/isobares/<modèle>/<tirage>/`, hors du dossier du tirage)
            let mut cles: Vec<String> = Vec::new();
            for pref in [format!("{}/{modele}/{}/", cfg.prefixe, t.id), format!("{}/isobares/{modele}/{}/", cfg.prefixe, t.id)] {
                match self.donnees.lister(&pref, false) {
                    Ok(l) => cles.extend(l.objets.into_iter().map(|o| o.cle)),
                    Err(e) => {
                        purge_erreur = Some(erreur_s(e));
                        break 'purge;
                    }
                }
            }
            cles.push(format!("{}/_poller/{modele}/{}.json", cfg.prefixe, t.id));
            for c in cles {
                if supprimes >= cfg.purge_max {
                    break 'purge;
                }
                if let Err(e) = self.donnees.supprimer(&c) {
                    purge_erreur = Some(erreur_s(e));
                    break 'purge;
                }
                supprimes += 1;
            }
        }
        if let Some(e) = &purge_erreur {
            journal("WARN", json!({"evenement": "purge_en_erreur", "erreur": e}));
        }
        let bilan = json!({"ecrit": ecrit, "octets": octets.len(), "generated": generated, "modeles": Value::Object(bilan_modeles),
                           "echecs_definitifs": echecs, "etats_ignores": ignores, "purge": {"supprimes": supprimes, "tirages": a_purger.len()}});
        journal("INFO", json!({"evenement": "manifeste", "ecrit": ecrit, "octets": octets.len(), "echecs_definitifs": echecs, "etats_ignores": ignores, "supprimes": supprimes}));
        Ok(bilan)
    }
}

use serde_json::Map;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cles() {
        assert!(cle_pertinente("v1/arome/2026-10-17T06Z/_etat/03.json", "v1"));
        assert!(cle_pertinente("v1/arome-hd/2026-10-17T06Z/_etat/51.json", "v1"));
        for non in [
            "v1/manifest.json", "v1/_poller/arome/2026-10-17T06Z.json", "v1/arome/2026-10-17T06Z/t2m/03.pmtiles", "v1/arome/2026-10-17T06Z/_domaine/03.npz",
            "v2/arome/2026-10-17T06Z/_etat/03.json", "v1/gfs/2026-10-17T06Z/_etat/03.json", "v1/arome/2026-10-17T06:00:00Z/_etat/03.json",
            "v1/arome/2026-10-17T06Z/_etat/3.json", "v1/arome/2026-10-17T06Z/_etat/03.json.tmp", "v1/arome/2026-10-17T06Z/x/_etat/03.json",
        ] {
            assert!(!cle_pertinente(non, "v1"), "{non}");
        }
    }

    #[test]
    fn notifications() {
        let corps = r#"{"Records":[{"eventSource":"aws:s3","s3":{"object":{"key":"v1/arome/2026-10-17T06Z/_etat/03.json","size":3000}}},
                        {"s3":{"object":{"key":"v1/a+b%C3%A9/x%2By.json"}}}]}"#;
        assert_eq!(cles_de_notification(corps), vec!["v1/arome/2026-10-17T06Z/_etat/03.json", "v1/a bé/x+y.json"]);
        assert!(cles_de_notification(r#"{"Service":"Amazon S3","Event":"s3:TestEvent"}"#).is_empty());
        assert!(cles_de_notification("pas du json").is_empty());
        assert_eq!(decoder_cle("a%2"), "a%2");
        assert_eq!(decoder_cle("%zz"), "%zz");
    }

    #[test]
    fn configuration() {
        let c = |p: &[(&str, &str)]| {
            let m: HashMap<String, String> = p.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect();
            Config::depuis(|n| m.get(n).cloned())
        };
        let d = c(&[]).unwrap();
        assert_eq!((d.age_ref_h, d.cache_s, d.purge_age_h, d.cle_manifeste().as_str()), (42, 30, None, "v1/manifest.json"));
        assert_eq!(c(&[("RW_PURGE_AGE_H", "60")]).unwrap().purge_age_h, Some(60));
        assert!(c(&[("RW_PURGE_AGE_H", "42")]).is_err(), "la purge ne doit pas atteindre ce que le manifeste cite");
        assert!(c(&[("RW_AGE_REF_H", "0")]).is_err());
        assert!(c(&[("RW_MANIFESTE_CACHE_S", "x")]).is_err());
    }
}
