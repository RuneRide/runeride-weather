// SPDX-License-Identifier: EUPL-1.2
//! Gestionnaire d'un lot SQS, indépendant du runtime Lambda (donc testable en local).

use crate::erreur::Erreur;
use crate::message::Travail;
use crate::s3::{ConfigS3, Identifiants, StockageS3};
use crate::source::{SourceGrib, SourceHttp, SourceLocale, BASE_OVH};
use crate::stockage::{Stockage, StockageLocal};
use crate::traitement::{self, Config};
use aws_lambda_events::event::sqs::{BatchItemFailure, SqsBatchResponse, SqsEvent};
use rw_tiles::WebpBackend;
use serde_json::{json, Value};
use std::time::Duration;

pub struct Contexte {
    pub source: Box<dyn SourceGrib>,
    pub sortie: Box<dyn Stockage>,
    pub cfg: Config,
}

/// Une ligne de journal JSON (CloudWatch Logs : un événement par ligne).
pub fn journal(niveau: &str, mut champs: Value) {
    champs["niveau"] = json!(niveau);
    eprintln!("{champs}");
}

/// Traite un corps de message. `Ok` = acquitté (traité ou déjà fait) ; `Err` = classe de l'échec.
pub fn traiter_corps(ctx: &Contexte, corps: &str) -> Result<traitement::Rapport, Erreur> {
    let travail: Travail = serde_json::from_str(corps).map_err(|e| Erreur::definitive(anyhow::anyhow!("message illisible : {e}")))?;
    let w = travail.valider().map_err(|e| Erreur::definitive(anyhow::anyhow!(e)))?;
    let r = traitement::traiter(&w, ctx.source.as_ref(), ctx.sortie.as_ref(), &ctx.cfg);
    if let Err(Erreur::Definitive(e)) = &r {
        traitement::consigner_echec(&w, ctx.sortie.as_ref(), &ctx.cfg, &format!("{e:#}"));
    }
    r
}

/// Traite le lot (idéalement de taille 1) et renvoie les identifiants des messages à rejouer.
pub fn traiter_lot(ctx: &Contexte, evenement: SqsEvent) -> SqsBatchResponse {
    let mut echecs = Vec::new();
    for m in evenement.records {
        let id = m.message_id.clone().unwrap_or_default();
        let corps = m.body.clone().unwrap_or_default();
        let t = std::time::Instant::now();
        match traiter_corps(ctx, &corps) {
            Ok(r) => journal(
                "INFO",
                json!({"evenement": "echeance", "message": id, "statut": r.statut, "modele": r.etat["modele"], "tirage": r.etat["tirage"],
                       "echeance": r.etat["echeance"], "duree_s": t.elapsed().as_secs_f64(),
                       // les mesures d'un état déjà publié sont celles du premier traitement : on ne les répète pas
                       "mesures": if r.statut == "traite" { r.etat["mesures"].clone() } else { Value::Null }}),
            ),
            Err(e) => {
                let rejoue = e.est_transitoire() || ctx.cfg.definitif_rejoue;
                journal(
                    if e.est_transitoire() { "WARN" } else { "ERROR" },
                    json!({"evenement": "echec", "message": id, "classe": e.classe(), "rejoue": rejoue, "corps": corps, "erreur": format!("{:#}", e.interne()),
                           "duree_s": t.elapsed().as_secs_f64()}),
                );
                if rejoue {
                    let mut f = BatchItemFailure::default();
                    f.item_identifier = id;
                    echecs.push(f);
                }
            }
        }
    }
    let mut r = SqsBatchResponse::default();
    r.batch_item_failures = echecs;
    r
}

fn entier(nom: &str) -> Result<Option<usize>, String> {
    match std::env::var(nom) {
        Ok(v) if !v.is_empty() => v.parse().map(Some).map_err(|_| format!("{nom} : entier attendu, reçu {v:?}")),
        _ => Ok(None),
    }
}

impl Contexte {
    /// Configuration par variables d'environnement (voir `docs/poc-rust/lambda.md` §4).
    pub fn depuis_env() -> Result<Self, String> {
        let var = |n: &str| std::env::var(n).ok().filter(|v| !v.is_empty());
        let mut cfg = Config::default();
        if let Some(p) = var("RW_PREFIXE") {
            cfg.prefixe = p.trim_matches('/').to_string();
        }
        if let Some(p) = var("RW_PROFIL") {
            cfg.profil = p;
        }
        if let Some(w) = var("RW_WEBP") {
            cfg.webp = w.parse::<WebpBackend>()?;
        }
        cfg.threads = entier("RW_THREADS")?;
        if let Some(n) = entier("RW_CONNEXIONS")? {
            cfg.connexions = n;
        }
        cfg.ecrire_canon = var("RW_ECRIRE_CANON").as_deref() == Some("1");
        cfg.forcer = var("RW_FORCER").as_deref() == Some("1");
        cfg.definitif_rejoue = var("RW_DEFINITIF").as_deref() == Some("rejouer");
        if let Some(e) = var("RW_ECHEANCE_DOMAINE") {
            cfg.echeance_domaine = if e == "none" { None } else { Some(e.parse().map_err(|_| "RW_ECHEANCE_DOMAINE : entier ou none".to_string())?) };
        }
        let essais = entier("RW_ESSAIS_SOURCE")?.unwrap_or(4) as u32;
        let pause = Duration::from_millis(entier("RW_PAUSE_MS")?.unwrap_or(1000) as u64);

        let source: Box<dyn SourceGrib> = match var("RW_SOURCE") {
            Some(d) if d.starts_with("dossier:") => Box::new(SourceLocale { dossiers: vec![d["dossier:".len()..].into()] }),
            Some(u) => Box::new(SourceHttp::new(u).avec_essais(essais, pause)),
            None => Box::new(SourceHttp::new(BASE_OVH).avec_essais(essais, pause)),
        };
        let sortie: Box<dyn Stockage> = match var("RW_SORTIE") {
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
        Ok(Self { source, sortie, cfg })
    }
}
