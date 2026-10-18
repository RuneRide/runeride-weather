// SPDX-License-Identifier: EUPL-1.2
//! Point d'entrée de la fonction Lambda « poller » (runtime personnalisé `provided.al2023` ; le zip de déploiement le range sous le nom `bootstrap`, voir `rust/tools/package-lambda.sh`).
//!
//! Événement d'entrée (EventBridge Scheduler, JSON statique) : `{}` ou
//! `{"simulation": true, "maintenant": "2026-10-17T15:10:00Z"}` (champs facultatifs, pour les essais : en simulation rien
//! n'est envoyé ni écrit ; `maintenant` fixe l'horloge).
//!
//! Mode de test sans runtime Lambda : `rw-poller --evenement evenement.json` (ou sans fichier : événement vide) exécute un
//! passage avec la configuration des variables d'environnement et écrit le bilan JSON sur la sortie standard. Code de
//! sortie 0 si tout va bien, 4 en cas d'erreur.

use lambda_runtime::{run, service_fn, Error, LambdaEvent};
use rw_aws::temps;
use rw_poller::poller::{journal, passer, Contexte};
use serde_json::{json, Value};
use std::sync::Arc;

fn executer(ctx: &Contexte, evt: &Value) -> Result<Value, String> {
    let simulation = evt["simulation"].as_bool().unwrap_or(false) || std::env::var("RW_SIMULATION").as_deref() == Ok("1");
    let maintenant = match evt["maintenant"].as_str() {
        Some(s) => temps::depuis_iso(s).ok_or_else(|| format!("maintenant : date ISO UTC attendue, reçu {s:?}"))?,
        None => temps::maintenant(),
    };
    passer(ctx, maintenant, simulation)
}

async fn gestionnaire(ctx: Arc<Contexte>, evt: LambdaEvent<Value>) -> Result<Value, Error> {
    // E/S synchrones : hors des fils du runtime asynchrone
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
            Ok(b) => println!("{}", serde_json::to_string(&b)?),
            Err(e) => {
                eprintln!("{e}");
                std::process::exit(4);
            }
        }
        return Ok(());
    }
    run(service_fn(move |evt| gestionnaire(ctx.clone(), evt))).await
}
