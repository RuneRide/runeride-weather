// SPDX-License-Identifier: EUPL-1.2
//! Un passage du poller : listage de la source, comparaison à l'état produit, envoi SQS, marqueurs.

use crate::config::Config;
use crate::noms;
use crate::plan::{self, Decision, Entrees, EtatConnu, Marqueur, SCHEMA_MARQUEUR};
use rw_aws::erreur::{ErreurSqs, ErreurStockage};
use rw_aws::liste::{ListeurPublic, Objet};
use rw_aws::s3::{ConfigS3, Identifiants, StockageS3};
use rw_aws::sqs::{ClientSqs, ConfigSqs, Message, ResultatLot, LOT_MAX};
use rw_aws::stockage::{Meta, Stockage, StockageLocal};
use rw_aws::temps;
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap};
use std::time::Duration;

/// Listage de la source (S3 public Météo-France ou faux serveur de test).
pub trait Listeur: Send + Sync {
    fn lister(&self, prefixe: &str) -> Result<Vec<Objet>, ErreurStockage>;
}

impl Listeur for ListeurPublic {
    fn lister(&self, prefixe: &str) -> Result<Vec<Objet>, ErreurStockage> {
        ListeurPublic::lister(self, prefixe)
    }
}

/// Envoi dans la file de travaux.
pub trait Envoi: Send + Sync {
    fn est_fifo(&self) -> bool;
    fn envoyer(&self, lot: &[Message]) -> Result<ResultatLot, ErreurSqs>;
}

impl Envoi for ClientSqs {
    fn est_fifo(&self) -> bool {
        self.config().est_fifo()
    }
    fn envoyer(&self, lot: &[Message]) -> Result<ResultatLot, ErreurSqs> {
        self.envoyer_lot(lot)
    }
}

pub struct Contexte {
    pub cfg: Config,
    pub source: Box<dyn Listeur>,
    /// Bucket de données (états d'échéance en lecture, marqueurs en lecture et écriture).
    pub donnees: Box<dyn Stockage>,
    /// `None` : pas de file configurée (autorisé seulement en simulation).
    pub envoi: Option<Box<dyn Envoi>>,
}

/// Une ligne de journal JSON (CloudWatch Logs : un événement par ligne).
pub fn journal(niveau: &str, mut champs: Value) {
    champs["niveau"] = json!(niveau);
    eprintln!("{champs}");
}

const MARQUEUR: Meta = Meta { type_contenu: "application/json", cache: "no-cache" };

fn tirage_court(epoch: i64) -> String {
    let (a, m, j, h, ..) = temps::decomposer(epoch);
    format!("{a:04}-{m:02}-{j:02}T{h:02}Z")
}

fn echeance_de_cle(cle: &str) -> Option<u32> {
    let nom = cle.rsplit('/').next()?.strip_suffix(".json")?;
    (nom.len() == 2 && nom.bytes().all(|b| b.is_ascii_digit())).then(|| nom.parse().ok()).flatten().filter(|&n| n <= noms::ECHEANCE_MAX)
}

fn var(n: &str) -> Option<String> {
    std::env::var(n).ok().filter(|v| !v.trim().is_empty())
}

impl Contexte {
    pub fn depuis_env() -> Result<Self, String> {
        let cfg = Config::depuis_env()?;
        let pause = Duration::from_millis(var("RW_PAUSE_MS").and_then(|v| v.parse().ok()).unwrap_or(1000));
        let region = var("AWS_REGION").or_else(|| var("AWS_DEFAULT_REGION"));
        let donnees: Box<dyn Stockage> = match var("RW_SORTIE") {
            Some(d) if d.starts_with("local:") => Box::new(StockageLocal::new(&d["local:".len()..])),
            _ => {
                let bucket = var("RW_BUCKET").ok_or("RW_BUCKET absente")?;
                let mut c = ConfigS3::aws(bucket, region.clone().ok_or("AWS_REGION absente")?);
                c.endpoint = var("RW_S3_ENDPOINT");
                c.pause_base = pause / 2;
                Box::new(StockageS3::new(c, Identifiants::depuis_env().map_err(|e| e.to_string())?).map_err(|e| e.to_string())?)
            }
        };
        let envoi: Option<Box<dyn Envoi>> = match var("RW_SQS_URL") {
            Some(url) => {
                let mut c = ConfigSqs::aws(region.ok_or("AWS_REGION absente")?, url);
                c.endpoint = var("RW_SQS_ENDPOINT");
                c.pause_base = pause / 2;
                Some(Box::new(ClientSqs::new(c, Identifiants::depuis_env().map_err(|e| e.to_string())?).map_err(|e| e.to_string())?))
            }
            None => None,
        };
        let source = ListeurPublic::new(cfg.source_liste.clone()).avec_essais(3, pause);
        Ok(Self { cfg, source: Box::new(source), donnees, envoi })
    }
}

/// États d'échéance présents pour (modèle, tirage). Les petits fichiers sont relus pour distinguer un échec définitif.
fn lire_etats(ctx: &Contexte, modele: &str, court: &str) -> Result<BTreeMap<u32, EtatConnu>, ErreurStockage> {
    let prefixe = format!("{}/{modele}/{court}/_etat/", ctx.cfg.prefixe);
    let mut etats = BTreeMap::new();
    for o in ctx.donnees.lister(&prefixe, true)?.objets {
        let Some(n) = echeance_de_cle(&o.cle) else { continue };
        if o.taille >= ctx.cfg.etat_petit_octets {
            etats.insert(n, EtatConnu::Complet);
            continue;
        }
        let statut = ctx
            .donnees
            .lire(&o.cle)?
            .and_then(|b| serde_json::from_slice::<Value>(&b).ok())
            .and_then(|v| v["statut"].as_str().map(str::to_string));
        etats.insert(
            n,
            match statut.as_deref() {
                Some("complete") => EtatConnu::Complet,
                Some("echec_definitif") => EtatConnu::Echec { ecrit: o.modifie },
                // contenu illisible ou inconnu : on le considère produit, le worker saura rejouer au besoin
                _ => EtatConnu::Complet,
            },
        );
    }
    Ok(etats)
}

fn lire_marqueur(ctx: &Contexte, cle: &str) -> Result<Marqueur, ErreurStockage> {
    Ok(ctx.donnees.lire(cle)?.and_then(|b| serde_json::from_slice(&b).ok()).unwrap_or_default())
}

fn plages(v: &[u32]) -> String {
    // `0-18,25` : lisible dans les journaux
    let mut s: Vec<String> = Vec::new();
    let mut i = 0;
    while i < v.len() {
        let mut j = i;
        while j + 1 < v.len() && v[j + 1] == v[j] + 1 {
            j += 1;
        }
        s.push(if i == j { v[i].to_string() } else { format!("{}-{}", v[i], v[j]) });
        i = j + 1;
    }
    s.join(",")
}

/// Un passage complet. `simulation` : liste et décide, mais n'envoie rien et n'écrit rien.
/// Retourne le bilan JSON ; `Err` si une opération a échoué (le reste du passage est tout de même exécuté).
pub fn passer(ctx: &Contexte, maintenant: i64, simulation: bool) -> Result<Value, String> {
    if !simulation && ctx.envoi.is_none() {
        return Err("RW_SQS_URL absente (autorisé seulement en simulation)".into());
    }
    let cfg = &ctx.cfg;
    let mut budget = cfg.envoi_max;
    let mut erreurs: Vec<String> = Vec::new();
    let mut bilans = Vec::new();
    let mut total_envoyes = 0usize;
    for modele in &cfg.modeles {
        for epoch in plan::tirages_candidats(maintenant, cfg) {
            let long = temps::iso(epoch);
            let court = tirage_court(epoch);
            match traiter_tirage(ctx, modele, epoch, &long, &court, maintenant, simulation, &mut budget) {
                Ok((bilan, envoyes)) => {
                    total_envoyes += envoyes;
                    bilans.push(bilan);
                }
                Err(e) => {
                    journal("ERROR", json!({"evenement": "tirage_en_erreur", "modele": modele, "tirage": court, "erreur": e}));
                    erreurs.push(format!("{modele} {court} : {e}"));
                }
            }
        }
    }
    let bilan = json!({"maintenant": temps::iso(maintenant), "simulation": simulation, "envoyes": total_envoyes, "tirages": bilans, "erreurs": erreurs});
    journal("INFO", json!({"evenement": "passage", "envoyes": total_envoyes, "tirages": bilans.len(), "erreurs": erreurs.len(), "simulation": simulation}));
    if erreurs.is_empty() { Ok(bilan) } else { Err(format!("{} : {}", erreurs.len(), erreurs.join(" ; "))) }
}

#[allow(clippy::too_many_arguments)]
fn traiter_tirage(ctx: &Contexte, modele: &str, epoch: i64, long: &str, court: &str, now: i64, simulation: bool, budget: &mut usize) -> Result<(Value, usize), String> {
    let cfg = &ctx.cfg;
    let age_h = (now - epoch) as f64 / 3600.0;
    // 0. tirage terminé (52 états complets, constaté à un passage précédent) : une lecture du marqueur, rien d'autre
    let cle_marqueur = format!("{}/_poller/{modele}/{court}.json", cfg.prefixe);
    let ancien = lire_marqueur(ctx, &cle_marqueur).map_err(|e| format!("marqueur : {e}"))?;
    if ancien.termine {
        return Ok((json!({"modele": modele, "tirage": court, "age_h": (age_h * 10.0).round() / 10.0, "termine": true}), 0));
    }
    // 1. source : un listage par paquet (52 fichiers en 0,01°, 9 en 0,025°)
    let mut source: HashMap<String, Objet> = HashMap::new();
    for p in noms::PAQUETS {
        let pref = noms::prefixe_liste(&cfg.source_prefixe, modele, long, p);
        for o in ctx.source.lister(&pref).map_err(|e| format!("listage de la source ({pref}) : {e}"))? {
            if let Some(nom) = o.cle.rsplit('/').next().map(str::to_string) {
                source.insert(nom, o);
            }
        }
    }
    // 2. état produit
    let etats = lire_etats(ctx, modele, court).map_err(|e| format!("états : {e}"))?;
    // 3. décision
    let d: Decision = plan::evaluer(&Entrees { modele, tirage_long: long, source: &source, etats: &etats, marqueur: &ancien, maintenant: now }, cfg);
    // publication incomplète chez Météo-France (cas connu : SP1 HD absent des tirages 06Z et 09Z du 17/10/2026)
    let incomplet = d.fichiers.values().any(|(p, a)| p < a);
    if incomplet && age_h >= cfg.alerte_incomplet_h as f64 {
        journal(
            "WARN",
            json!({"evenement": "publication_incomplete", "modele": modele, "tirage": court, "age_h": (age_h * 10.0).round() / 10.0,
                   "fichiers": d.fichiers, "paquet_absent": d.paquet_absent.iter().map(|(p, v)| (p.clone(), plages(v))).collect::<BTreeMap<_, _>>()}),
        );
    }
    // 4. envoi (par lots de 10), puis marqueur
    let mut marqueur = d.marqueur.clone();
    let mut envoyes: Vec<u32> = Vec::new();
    let mut refuses: Vec<Value> = Vec::new();
    let mut echec_envoi: Option<String> = None;
    let a_envoyer: Vec<u32> = d.a_envoyer.iter().copied().take(*budget).collect();
    if !simulation && !a_envoyer.is_empty() {
        let envoi = ctx.envoi.as_ref().ok_or("pas de file")?;
        let fenetre_idx = if cfg.fenetre_s > 0 { now / cfg.fenetre_s } else { now };
        for lot in a_envoyer.chunks(LOT_MAX) {
            let messages: Vec<Message> = lot
                .iter()
                .map(|&n| Message {
                    id: format!("e{n:02}"),
                    corps: json!({"modele": modele, "tirage": long, "echeance": n}).to_string(),
                    groupe: envoi.est_fifo().then(|| format!("{modele}-{court}")),
                    deduplication: envoi.est_fifo().then(|| format!("{modele}-{court}-{n:02}-{fenetre_idx}")),
                })
                .collect();
            match envoi.envoyer(&messages) {
                Ok(r) => {
                    for id in &r.acceptes {
                        if let Some(n) = id.strip_prefix('e').and_then(|s| s.parse::<u32>().ok()) {
                            marqueur.envoyes.insert(n, now);
                            envoyes.push(n);
                        }
                    }
                    for (id, code, msg) in r.refuses {
                        journal("ERROR", json!({"evenement": "message_refuse", "modele": modele, "tirage": court, "id": id, "code": code, "message": msg}));
                        refuses.push(json!({"id": id, "code": code}));
                    }
                }
                Err(e) => {
                    echec_envoi = Some(e.to_string());
                    break;
                }
            }
        }
        envoyes.sort_unstable();
        *budget = budget.saturating_sub(envoyes.len());
    }
    // 5. marqueur : seulement s'il change (pas d'écriture inutile : 5 $ le million de PUT)
    marqueur.schema = SCHEMA_MARQUEUR;
    let mut marqueur_ecrit = false;
    let vide = marqueur.envoyes.is_empty() && marqueur.tailles.is_empty();
    if !simulation && marqueur != ancien && !(vide && ancien == Marqueur::default()) {
        let octets = serde_json::to_vec(&marqueur).map_err(|e| e.to_string())?;
        ctx.donnees.ecrire(&cle_marqueur, &octets, &MARQUEUR).map_err(|e| format!("écriture du marqueur : {e}"))?;
        marqueur_ecrit = true;
    }
    if !envoyes.is_empty() {
        journal("INFO", json!({"evenement": "envoi", "modele": modele, "tirage": court, "echeances": plages(&envoyes), "nombre": envoyes.len()}));
    }
    let bilan = json!({
        "modele": modele, "tirage": court, "age_h": (age_h * 10.0).round() / 10.0,
        "fichiers": d.fichiers,
        "faites": plages(&d.faites), "envoyees": plages(&envoyes), "a_envoyer": plages(&d.a_envoyer), "en_file": plages(&d.en_file),
        "instables": plages(&d.instables), "incompletes": plages(&d.incompletes), "en_echec": plages(&d.en_echec),
        "paquet_absent": d.paquet_absent.iter().map(|(p, v)| (p.clone(), plages(v))).collect::<BTreeMap<_, _>>(),
        "refuses": refuses, "marqueur_ecrit": marqueur_ecrit,
    });
    match echec_envoi {
        Some(e) => Err(format!("envoi SQS : {e} ({} échéances envoyées avant l'erreur)", envoyes.len())),
        None => Ok((bilan, envoyes.len())),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn plages_lisibles() {
        assert_eq!(plages(&[]), "");
        assert_eq!(plages(&[0, 1, 2, 3, 7, 9, 10]), "0-3,7,9-10");
        assert_eq!(plages(&(0..=51).collect::<Vec<_>>()), "0-51");
    }

    #[test]
    fn cles_d_etat() {
        assert_eq!(echeance_de_cle("v1/arome/2026-10-17T06Z/_etat/07.json"), Some(7));
        for mauvais in ["v1/x/_etat/7.json", "v1/x/_etat/52.json", "v1/x/_etat/ab.json", "v1/x/_etat/07.json.tmp", "v1/x/_etat/"] {
            assert_eq!(echeance_de_cle(mauvais), None, "{mauvais}");
        }
        assert_eq!(tirage_court(temps::depuis_iso("2026-10-17T06:00:00Z").unwrap()), "2026-10-17T06Z");
    }
}
