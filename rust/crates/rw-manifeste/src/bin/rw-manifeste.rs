// SPDX-License-Identifier: EUPL-1.2
//! Point d'entrée de la fonction Lambda « manifeste » (runtime personnalisé `provided.al2023` ; le zip de déploiement le range sous le nom `bootstrap`, voir `rust/tools/package-lambda.sh`).
//!
//! Événements acceptés :
//! * événement SQS (source : notifications S3 sur `<préfixe>/<modèle>/<tirage>/_etat/NN.json`, fenêtre de lot 20 s) :
//!   une seule reconstruction par invocation, quel que soit le nombre de messages ; les messages sans état d'échéance
//!   (notification de test, autre clé) sont acquittés sans rien faire. Réponse `{"batchItemFailures": [...]}` : en cas
//!   d'échec tous les messages du lot sont rendus à SQS (`ReportBatchItemFailures` à activer sur le mapping) ;
//! * tout autre JSON (invocation directe, Scheduler) : reconstruction forcée, bilan en réponse.
//!   Champ facultatif `maintenant` (ISO UTC) pour fixer l'horloge (essais).
//!
//! Mode de test sans runtime Lambda : `rw-manifeste --evenement evenement.json` (ou sans fichier : événement vide).
//! Code de sortie 0 si tout va bien, 4 en cas d'erreur.

use lambda_runtime::{run, service_fn, Error, LambdaEvent};
use rw_aws::temps;
use rw_manifeste::manifeste::{cle_pertinente, cles_de_notification, journal, Contexte};
use serde_json::{json, Value};
use std::sync::Arc;

fn executer(ctx: &Contexte, evt: &Value) -> Result<Value, String> {
    let maintenant = match evt["maintenant"].as_str() {
        Some(s) => temps::depuis_iso(s).ok_or_else(|| format!("maintenant : date ISO UTC attendue, reçu {s:?}"))?,
        None => temps::maintenant(),
    };
    let Some(records) = evt["Records"].as_array().filter(|r| r.iter().any(|x| x["eventSource"] == "aws:sqs")) else {
        return ctx.reconstruire(maintenant); // invocation directe
    };
    let pertinent = records.iter().any(|r| cles_de_notification(r["body"].as_str().unwrap_or("")).iter().any(|k| cle_pertinente(k, &ctx.cfg.prefixe)));
    if !pertinent {
        journal("INFO", json!({"evenement": "ignore", "messages": records.len()}));
        return Ok(json!({"batchItemFailures": []}));
    }
    match ctx.reconstruire(maintenant) {
        Ok(_) => Ok(json!({"batchItemFailures": []})),
        Err(e) => {
            journal("ERROR", json!({"evenement": "echec", "erreur": e, "messages": records.len()}));
            let ids: Vec<Value> = records.iter().filter_map(|r| r["messageId"].as_str()).map(|id| json!({"itemIdentifier": id})).collect();
            Ok(json!({"batchItemFailures": ids, "erreur": e}))
        }
    }
}

async fn gestionnaire(ctx: Arc<Contexte>, evt: LambdaEvent<Value>) -> Result<Value, Error> {
    let r = tokio::task::spawn_blocking(move || executer(&ctx, &evt.payload)).await?;
    r.map_err(Error::from)
}

#[tokio::main(flavor = "current_thread")]
async fn main() -> Result<(), Error> {
    let ctx = match Contexte::depuis_env() {
        Ok(c) => Arc::new(c),
        Err(e) => {
            journal("ERROR", json!({"evenement": "configuration", "erreur": e}));
            return Err(e.into());
        }
    };
    let args: Vec<String> = std::env::args().collect();
    if args.get(1).map(String::as_str) == Some("--evenement") {
        let evt: Value = match args.get(2) {
            Some(chemin) => serde_json::from_slice(&std::fs::read(chemin)?)?,
            None => json!({}),
        };
        match executer(&ctx, &evt) {
            Ok(b) => {
                println!("{}", serde_json::to_string(&b)?);
                let echec = b["batchItemFailures"].as_array().is_some_and(|a| !a.is_empty());
                std::process::exit(if echec { 4 } else { 0 });
            }
            Err(e) => {
                eprintln!("{e}");
                std::process::exit(4);
            }
        }
    }
    run(service_fn(move |evt| gestionnaire(ctx.clone(), evt))).await
}
