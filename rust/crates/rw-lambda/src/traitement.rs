// SPDX-License-Identifier: EUPL-1.2
//! Traitement d'une échéance de bout en bout : téléchargement des GRIB → champs canoniques → PMTiles →
//! écriture, puis état d'échéance (marqueur final).
//!
//! Ordre d'écriture : fichiers PMTiles, fichiers dérivés (source des isobares, masque du domaine), **état en
//! dernier**. L'état `complete` est le marqueur : tant qu'il n'existe pas, l'échéance n'est pas publiée.

use crate::erreur::{Erreur, ErreurSource, ErreurStockage};
use crate::message::Valide;
use crate::source::SourceGrib;
use crate::stockage::{Meta, Stockage, CACHE_IMMUABLE, ETAT, NPZ, PMTILES};
use crate::temps;
use rayon::prelude::*;
use rw_cli::{canon, npz};
use rw_tiles::{Canon, Options, Profil, WebpBackend};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::Mutex;
use std::time::Instant;

pub const SCHEMA_ETAT: u32 = 1;

/// GeoJSON des isobares lu par `viewer/js/isobares.js`. Type `application/json` : compressible à la volée par le CDN
/// (voir `docs/poc-rust/isobares.md` §5 pour le stockage pré-compressé, qui demande `Content-Encoding` dans `rw-aws`).
pub const GEOJSON: Meta = Meta { type_contenu: "application/json", cache: CACHE_IMMUABLE };

#[derive(Debug, Clone)]
pub struct Config {
    /// Préfixe de clé (version du format publié), sans `/` final : `v1`.
    pub prefixe: String,
    pub profil: String,
    pub webp: WebpBackend,
    /// Fils de calcul (rayon) ; `None` = tous les processeurs disponibles.
    pub threads: Option<usize>,
    /// Téléchargements simultanés (courtoisie envers l'hébergeur : 3 comme le Python).
    pub connexions: usize,
    /// Écrit aussi le champ canonique complet (`_canon/NN.npz`) : comparaisons et débogage seulement.
    pub ecrire_canon: bool,
    /// Échéance dont on écrit le masque `t2m` (`_domaine/NN.npz`), qui sert à calculer le polygone du domaine.
    pub echeance_domaine: Option<u32>,
    /// Ignore l'état existant et retraite.
    pub forcer: bool,
    /// Les erreurs définitives sont rendues à SQS (rejouées) au lieu d'être acquittées.
    pub definitif_rejoue: bool,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            prefixe: "v1".into(),
            profil: "reduit2".into(),
            webp: WebpBackend::ImageWebp,
            threads: None,
            connexions: 3,
            ecrire_canon: false,
            echeance_domaine: Some(3),
            forcer: false,
            definitif_rejoue: false,
        }
    }
}

/// Clés de sortie d'une échéance.
pub struct Cles<'a> {
    pub prefixe: &'a str,
    pub w: &'a Valide,
}

impl Cles<'_> {
    pub fn dossier(&self) -> String {
        format!("{}/{}/{}", self.prefixe, self.w.modele, self.w.tirage_court)
    }
    /// Chemin relatif de `file_template` du manifeste : `<modèle>/<tirage>/<variable>/<NN>.pmtiles`.
    pub fn chemin_relatif(&self, variable: &str) -> String {
        format!("{}/{}/{variable}/{:02}.pmtiles", self.w.modele, self.w.tirage_court, self.w.echeance)
    }
    pub fn pmtiles(&self, variable: &str) -> String {
        format!("{}/{variable}/{:02}.pmtiles", self.dossier(), self.w.echeance)
    }
    pub fn etat(&self) -> String {
        format!("{}/_etat/{:02}.json", self.dossier(), self.w.echeance)
    }
    pub fn isobares(&self) -> String {
        format!("{}/_isobares/{:02}.npz", self.dossier(), self.w.echeance)
    }
    /// GeoJSON des isobares : `<préfixe>/isobares/<modèle>/<tirage>/NN.geojson`, de sorte que l'URL du viewer
    /// (`/isobares/<modèle>/<tirage>/NN.geojson`) correspond à la clé par simple origine `/<préfixe>`.
    pub fn isobares_geojson(&self) -> String {
        format!("{}/isobares/{}/{}/{:02}.geojson", self.prefixe, self.w.modele, self.w.tirage_court, self.w.echeance)
    }
    pub fn domaine(&self) -> String {
        format!("{}/_domaine/{:02}.npz", self.dossier(), self.w.echeance)
    }
    pub fn canon(&self) -> String {
        format!("{}/_canon/{:02}.npz", self.dossier(), self.w.echeance)
    }
}

#[derive(Debug)]
pub struct Rapport {
    /// `traite` ou `deja_fait`.
    pub statut: &'static str,
    pub etat: Value,
}

fn stock(e: ErreurStockage) -> Erreur {
    Erreur::transitoire(e)
}

/// L'état existant est-il un `complete` valide dont tous les fichiers sont présents avec la bonne taille ?
fn deja_complet(w: &Valide, cles: &Cles<'_>, sortie: &dyn Stockage) -> Result<bool, Erreur> {
    let Some(octets) = sortie.lire(&cles.etat()).map_err(stock)? else { return Ok(false) };
    let Ok(etat) = serde_json::from_slice::<Value>(&octets) else { return Ok(false) };
    if etat["schema"] != SCHEMA_ETAT
        || etat["statut"] != "complete"
        || etat["modele"] != w.modele.as_str()
        || etat["tirage"] != w.tirage_court.as_str()
        || etat["echeance"] != w.echeance
        || etat["pipeline"]["version"] != env!("CARGO_PKG_VERSION")
    {
        return Ok(false);
    }
    let mut attendus: Vec<(String, u64)> = Vec::new();
    for (_, v) in etat["variables"].as_object().into_iter().flatten() {
        let (Some(cle), Some(o)) = (v["cle"].as_str(), v["octets"].as_u64()) else { return Ok(false) };
        attendus.push((cle.to_string(), o));
    }
    for k in ["isobares", "isobares_geojson", "domaine"] {
        if !etat[k].is_null() {
            let (Some(cle), Some(o)) = (etat[k]["cle"].as_str(), etat[k]["octets"].as_u64()) else { return Ok(false) };
            attendus.push((cle.to_string(), o));
        }
    }
    if attendus.is_empty() {
        return Ok(false);
    }
    for (cle, o) in attendus {
        if sortie.taille(&cle).map_err(stock)? != Some(o) {
            return Ok(false);
        }
    }
    Ok(true)
}

/// Télécharge les fichiers en parallèle (au plus `connexions` à la fois).
fn telecharger(w: &Valide, noms: &[String], source: &dyn SourceGrib, connexions: usize) -> Result<HashMap<String, Vec<u8>>, ErreurSource> {
    let file: Mutex<Vec<&String>> = Mutex::new(noms.iter().rev().collect());
    let resultats: Mutex<Vec<(String, Result<Vec<u8>, ErreurSource>)>> = Mutex::new(Vec::new());
    std::thread::scope(|s| {
        for _ in 0..connexions.clamp(1, noms.len().max(1)) {
            s.spawn(|| loop {
                let Some(nom) = file.lock().unwrap().pop() else { break };
                let paquet = nom.split("__").nth(2).unwrap_or("");
                let r = source.obtenir(&w.modele, &w.tirage_long, paquet, nom);
                let echec = r.is_err();
                resultats.lock().unwrap().push((nom.clone(), r));
                if echec {
                    file.lock().unwrap().clear(); // inutile de continuer
                    break;
                }
            });
        }
    });
    let mut map = HashMap::new();
    let mut erreur: Option<ErreurSource> = None;
    for (nom, r) in resultats.into_inner().unwrap() {
        match r {
            Ok(o) => {
                map.insert(nom, o);
            }
            // « non publié » prime sur les autres erreurs
            Err(e @ ErreurSource::Absente(_)) => erreur = Some(e),
            Err(e) => {
                erreur.get_or_insert(e);
            }
        }
    }
    match erreur {
        Some(e) => Err(e),
        None => Ok(map),
    }
}

fn npz_octets(c: &Canon, sources: &[String], note: &str) -> Result<Vec<u8>, Erreur> {
    let meta = npz::meta_json(c, sources, note);
    let mut curseur = std::io::Cursor::new(Vec::new());
    npz::ecrire(&mut curseur, c, &meta, Some(6)).map_err(Erreur::definitive)?;
    Ok(curseur.into_inner())
}

fn sous_canon(c: &Canon, variable: &str) -> Option<Canon> {
    let v = c.variable(variable)?;
    Some(Canon { modele: c.modele.clone(), tirage: c.tirage.clone(), echeance: c.echeance, lat: c.lat.clone(), lon: c.lon.clone(), variables: vec![(variable.to_string(), v.to_vec())] })
}

/// Extrait de la métadonnée PMTiles les clés utiles au manifeste.
fn descripteur(chemin_rel: &str, cle: &str, f: &rw_tiles::Fichier) -> Value {
    let md = rw_tiles::pmtiles::lire(&f.octets).map(|l| l.metadonnees).unwrap_or(Value::Null);
    let mut o = serde_json::Map::new();
    o.insert("chemin".into(), json!(chemin_rel));
    o.insert("cle".into(), json!(cle));
    o.insert("octets".into(), json!(f.octets.len()));
    o.insert("tuiles".into(), json!(f.tuiles));
    for k in ["minzoom", "maxzoom", "encoding", "scale", "unit", "min", "max", "quant_step", "components"] {
        if !md[k].is_null() {
            o.insert(k.into(), md[k].clone());
        }
    }
    Value::Object(o)
}

/// Traite une échéance. Idempotent : si l'état `complete` existe et que ses fichiers sont là, ne fait rien.
pub fn traiter(w: &Valide, source: &dyn SourceGrib, sortie: &dyn Stockage, cfg: &Config) -> Result<Rapport, Erreur> {
    let debut = Instant::now();
    let cles = Cles { prefixe: &cfg.prefixe, w };
    if !cfg.forcer && deja_complet(w, &cles, sortie)? {
        let etat = sortie.lire(&cles.etat()).map_err(stock)?.and_then(|o| serde_json::from_slice(&o).ok()).unwrap_or(Value::Null);
        return Ok(Rapport { statut: "deja_fait", etat });
    }
    let profil = Profil::par_nom(&cfg.profil).ok_or_else(|| Erreur::definitive(anyhow::anyhow!("profil inconnu : {}", cfg.profil)))?;
    let pool = {
        let mut b = rayon::ThreadPoolBuilder::new();
        if let Some(n) = cfg.threads {
            b = b.num_threads(n);
        }
        b.build().map_err(|e| Erreur::transitoire(anyhow::anyhow!("pool de fils : {e}")))?
    };

    // 1. téléchargement (n et, si nécessaire, n−1)
    let t = Instant::now();
    let noms = canon::noms_requis(&w.modele, &w.tirage_long, w.echeance).map_err(Erreur::definitive)?;
    let mut fichiers_grib = telecharger(w, &noms, source, cfg.connexions).map_err(Erreur::transitoire)?;
    let octets_telecharges: usize = fichiers_grib.values().map(Vec::len).sum();
    let telechargement_s = t.elapsed().as_secs_f64();

    // 2. GRIB → champs canoniques (une erreur ici : fichier illisible ou incohérent, définitive)
    let opts_ing = canon::Options { tout_decoder: false, backend: Default::default() };
    let (c, sources, et) = pool
        .install(|| {
            canon::ingerer_octets(
                &w.modele,
                &w.tirage_long,
                w.echeance,
                &mut |nom| fichiers_grib.remove(nom).ok_or_else(|| anyhow::anyhow!("fichier non téléchargé : {nom}")),
                &opts_ing,
            )
        })
        .map_err(Erreur::definitive)?;
    drop(fichiers_grib);

    // 3. tuilage
    let t = Instant::now();
    let mut opts = Options::nouveau(profil);
    opts.backend = cfg.webp;
    let (fichiers, mesures_tuiles) = pool.install(|| rw_tiles::construire(&c, &opts)).map_err(Erreur::definitive)?;
    let tuilage_s = t.elapsed().as_secs_f64();
    if fichiers.is_empty() {
        return Err(Erreur::definitive(anyhow::anyhow!("aucune tuile produite")));
    }

    // 4. écriture : PMTiles (en parallèle), dérivés, puis l'état
    let t = Instant::now();
    pool.install(|| fichiers.par_iter().try_for_each(|f| sortie.ecrire(&cles.pmtiles(&f.variable), &f.octets, &PMTILES))).map_err(stock)?;
    let mut variables = serde_json::Map::new();
    for f in &fichiers {
        variables.insert(f.variable.clone(), descripteur(&cles.chemin_relatif(&f.variable), &cles.pmtiles(&f.variable), f));
    }
    let note = "rw-lambda : source de données dérivée du champ canonique";
    // source des isobares : pression réduite au niveau de la mer (Pa), AROME 0,025° seulement ; compatible `pipeline.isobars.calcul.charger`
    let mut isobares = Value::Null;
    if let Some(sc) = sous_canon(&c, "mslp") {
        let o = npz_octets(&sc, &sources, note)?;
        sortie.ecrire(&cles.isobares(), &o, &NPZ).map_err(stock)?;
        isobares = json!({"cle": cles.isobares(), "octets": o.len(), "variable": "mslp", "unite": "Pa"});
    }
    // isobares finales (GeoJSON du viewer) : calcul Rust de `pipeline/isobars`, octet pour octet identique au Python
    let t_iso = Instant::now();
    let mut isobares_geojson = Value::Null;
    if let Some(mslp) = c.variable("mslp") {
        let entete = rw_isobares::Entete { modele: w.modele.clone(), tirage: w.tirage_long.clone(), echeance: w.echeance };
        let params = rw_isobares::Params::default();
        let r = pool.install(|| rw_isobares::calculer(&c.lat, &c.lon, mslp, &params)).map_err(Erreur::definitive)?;
        let geojson = rw_isobares::geojson::vers_geojson(&r, &entete, &params).into_bytes();
        sortie.ecrire(&cles.isobares_geojson(), &geojson, &GEOJSON).map_err(stock)?;
        isobares_geojson = json!({
            "cle": cles.isobares_geojson(), "octets": geojson.len(),
            "lignes": r.stats.lignes, "sommets": r.stats.sommets, "extrema": r.stats.extrema,
        });
    }
    let isobares_s = t_iso.elapsed().as_secs_f64();
    // masque du domaine : t2m à l'échéance de référence ; compatible `pipeline.manifest.build.polygone_modele`
    let mut domaine = Value::Null;
    if cfg.echeance_domaine == Some(w.echeance) {
        if let Some(sc) = sous_canon(&c, "t2m") {
            let o = npz_octets(&sc, &sources, note)?;
            sortie.ecrire(&cles.domaine(), &o, &NPZ).map_err(stock)?;
            domaine = json!({"cle": cles.domaine(), "octets": o.len(), "variable": "t2m"});
        }
    }
    if cfg.ecrire_canon {
        let o = npz_octets(&c, &sources, note)?;
        sortie.ecrire(&cles.canon(), &o, &NPZ).map_err(stock)?;
    }
    let ecriture_s = t.elapsed().as_secs_f64();

    let valides = |nom: &str| c.variable(nom).map(|v| v.iter().filter(|x| !x.is_nan()).count());
    let etat = json!({
        "schema": SCHEMA_ETAT,
        "statut": "complete",
        "modele": w.modele,
        "tirage": w.tirage_court,
        "run": w.tirage_long,
        "echeance": w.echeance,
        "valide": w.valide_iso(),
        "traite": temps::iso(temps::maintenant()),
        "pipeline": {"version": env!("CARGO_PKG_VERSION"), "profil": cfg.profil, "webp": "image-webp"},
        "grille": {
            "nlat": c.lat.len(), "nlon": c.lon.len(),
            "lat": [c.lat.first(), c.lat.last()], "lon": [c.lon.first(), c.lon.last()],
            "points_valides_t2m": valides("t2m"),
        },
        "variables": Value::Object(variables),
        "isobares": isobares,
        "isobares_geojson": isobares_geojson,
        "domaine": domaine,
        "sources": sources,
        "mesures": {
            "telechargement_s": telechargement_s, "octets_telecharges": octets_telecharges,
            "decodage_grib_s": et.decodage_grib_s, "calcul_canonique_s": et.calcul_canonique_s, "champs_decodes": et.champs_decodes,
            "tuilage_s": tuilage_s, "rechantillonnage_encodage_s": mesures_tuiles.rechantillonnage_encodage_s,
            "ecriture_s": ecriture_s, "isobares_s": isobares_s, "fils": pool.current_num_threads(),
            "octets_pmtiles": fichiers.iter().map(|f| f.octets.len()).sum::<usize>(),
            "total_s": debut.elapsed().as_secs_f64(),
        },
    });
    // marqueur final
    sortie.ecrire(&cles.etat(), serde_json::to_string(&etat).unwrap().as_bytes(), &ETAT).map_err(stock)?;
    Ok(Rapport { statut: "traite", etat })
}

/// Consigne un échec définitif (meilleur effort) : l'état n'est pas `complete`, donc l'échéance sera retraitée si on la rejoue.
pub fn consigner_echec(w: &Valide, sortie: &dyn Stockage, cfg: &Config, erreur: &str) {
    let cles = Cles { prefixe: &cfg.prefixe, w };
    let etat = json!({
        "schema": SCHEMA_ETAT, "statut": "echec_definitif", "modele": w.modele, "tirage": w.tirage_court, "run": w.tirage_long,
        "echeance": w.echeance, "traite": temps::iso(temps::maintenant()), "erreur": erreur,
        "pipeline": {"version": env!("CARGO_PKG_VERSION")},
    });
    let _ = sortie.ecrire(&cles.etat(), etat.to_string().as_bytes(), &ETAT);
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::message::Travail;

    #[test]
    fn cles() {
        let w = Travail { modele: "arome-hd".into(), tirage: "2026-10-17T03:00:00Z".into(), echeance: 5 }.valider().unwrap();
        let c = Cles { prefixe: "v1", w: &w };
        assert_eq!(c.pmtiles("t2m"), "v1/arome-hd/2026-10-17T03Z/t2m/05.pmtiles");
        assert_eq!(c.chemin_relatif("wind10"), "arome-hd/2026-10-17T03Z/wind10/05.pmtiles");
        assert_eq!(c.etat(), "v1/arome-hd/2026-10-17T03Z/_etat/05.json");
    }
}
