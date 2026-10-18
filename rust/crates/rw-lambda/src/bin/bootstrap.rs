// SPDX-License-Identifier: EUPL-1.2
//! Point d'entrée de la fonction Lambda (runtime personnalisé `provided.al2023`, exécutable nommé `bootstrap`).
//!
//! Mode de test sans runtime Lambda : `bootstrap --evenement evenement-sqs.json` lit un événement SQS en JSON,
//! l'exécute avec la configuration des variables d'environnement et écrit la réponse
//! (`batchItemFailures`) sur la sortie standard. Code de sortie 0 si tous les messages sont acquittés, 3 sinon.

use aws_lambda_events::event::sqs::{SqsBatchResponse, SqsEvent};
use lambda_runtime::{run, service_fn, Error, LambdaEvent};
use rw_lambda::handler::{journal, traiter_lot, Contexte};
use serde_json::json;
use std::sync::Arc;

async fn gestionnaire(ctx: Arc<Contexte>, evt: LambdaEvent<SqsEvent>) -> Result<SqsBatchResponse, Error> {
    // le travail est long et bloquant (CPU, E/S synchrones) : hors des fils du runtime asynchrone
    let reponse = tokio::task::spawn_blocking(move || traiter_lot(&ctx, evt.payload)).await?;
    Ok(reponse)
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
        let chemin = args.get(2).ok_or("--evenement attend un fichier")?;
        let evt: SqsEvent = serde_json::from_slice(&std::fs::read(chemin)?)?;
        let rep = traiter_lot(&ctx, evt);
        println!("{}", serde_json::to_string(&rep)?);
        std::process::exit(if rep.batch_item_failures.is_empty() { 0 } else { 3 });
    }
    run(service_fn(move |evt| gestionnaire(ctx.clone(), evt))).await
}
