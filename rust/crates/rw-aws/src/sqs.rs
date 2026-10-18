// SPDX-License-Identifier: EUPL-1.2
//! Client SQS minimal (`SendMessageBatch`) signé SigV4, protocole JSON (`X-Amz-Target: AmazonSQS.SendMessageBatch`),
//! sans le SDK AWS. Les identifiants viennent de l'environnement de la fonction (voir [`crate::s3::Identifiants`]).
//!
//! Limites de l'API respectées ici : 10 entrées par lot, 262 144 octets au plus par lot (corps des messages), identifiant
//! d'entrée de 80 caractères au plus (alphanumériques, `-`, `_`).

use crate::erreur::ErreurSqs;
use crate::http::{self, Delais};
use crate::s3::Identifiants;
use crate::sigv4::{self, Demande};
use crate::temps;
use serde_json::{json, Value};
use std::io::Read;
use std::time::Duration;

pub const LOT_MAX: usize = 10;
pub const OCTETS_LOT_MAX: usize = 262_144;

/// Un message à envoyer.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Message {
    /// Identifiant d'entrée, unique dans le lot (retrouvé dans la réponse).
    pub id: String,
    pub corps: String,
    /// Files FIFO seulement : groupe de messages.
    pub groupe: Option<String>,
    /// Files FIFO seulement : identifiant de déduplication (fenêtre de 5 minutes côté AWS).
    pub deduplication: Option<String>,
}

/// Résultat d'un lot : identifiants acceptés et refusés (`(id, code, message)`).
#[derive(Debug, Default, PartialEq, Eq)]
pub struct ResultatLot {
    pub acceptes: Vec<String>,
    pub refuses: Vec<(String, String, String)>,
}

#[derive(Debug, Clone)]
pub struct ConfigSqs {
    pub region: String,
    /// URL de la file (`https://sqs.eu-north-1.amazonaws.com/123456789012/rw-jobs`).
    pub file: String,
    /// Point de terminaison explicite (tests : `http://127.0.0.1:8099`). Absent : `https://sqs.<région>.amazonaws.com`.
    pub endpoint: Option<String>,
    pub essais: u32,
    pub pause_base: Duration,
}

impl ConfigSqs {
    pub fn aws(region: impl Into<String>, file: impl Into<String>) -> Self {
        Self { region: region.into(), file: file.into(), endpoint: None, essais: 4, pause_base: Duration::from_millis(500) }
    }
    /// Une file FIFO se reconnaît à son nom (`….fifo`).
    pub fn est_fifo(&self) -> bool {
        self.file.ends_with(".fifo")
    }
}

pub struct ClientSqs {
    cfg: ConfigSqs,
    ident: Identifiants,
    agent: ureq::Agent,
}

/// Corps JSON d'un `SendMessageBatch`.
pub fn corps_lot(file: &str, messages: &[Message]) -> Value {
    let entrees: Vec<Value> = messages
        .iter()
        .map(|m| {
            let mut e = json!({"Id": m.id, "MessageBody": m.corps});
            if let Some(g) = &m.groupe {
                e["MessageGroupId"] = json!(g);
            }
            if let Some(d) = &m.deduplication {
                e["MessageDeduplicationId"] = json!(d);
            }
            e
        })
        .collect();
    json!({"QueueUrl": file, "Entries": entrees})
}

/// Analyse la réponse JSON d'un `SendMessageBatch`.
pub fn analyser_reponse(v: &Value) -> ResultatLot {
    let mut r = ResultatLot::default();
    for e in v["Successful"].as_array().into_iter().flatten() {
        if let Some(id) = e["Id"].as_str() {
            r.acceptes.push(id.to_string());
        }
    }
    for e in v["Failed"].as_array().into_iter().flatten() {
        let t = |k: &str| e[k].as_str().unwrap_or("").to_string();
        r.refuses.push((t("Id"), t("Code"), t("Message")));
    }
    r
}

impl ClientSqs {
    pub fn new(cfg: ConfigSqs, ident: Identifiants) -> Result<Self, ErreurSqs> {
        if cfg.region.is_empty() || cfg.file.is_empty() {
            return Err(ErreurSqs::Configuration("région ou URL de file vide".into()));
        }
        Ok(Self { cfg, ident, agent: http::agent(Delais::default(), http::AGENT_UTILISATEUR) })
    }

    pub fn config(&self) -> &ConfigSqs {
        &self.cfg
    }

    /// Envoie au plus [`LOT_MAX`] messages. Les erreurs réseau, 5xx et 429 sont réessayées ; un refus d'entrée
    /// (`Failed`) n'est pas une erreur : il est rendu dans [`ResultatLot::refuses`].
    pub fn envoyer_lot(&self, messages: &[Message]) -> Result<ResultatLot, ErreurSqs> {
        if messages.is_empty() {
            return Ok(ResultatLot::default());
        }
        if messages.len() > LOT_MAX {
            return Err(ErreurSqs::Configuration(format!("{} messages dans un lot (maximum {LOT_MAX})", messages.len())));
        }
        let corps = corps_lot(&self.cfg.file, messages).to_string();
        if corps.len() > OCTETS_LOT_MAX {
            return Err(ErreurSqs::Configuration(format!("lot de {} octets (maximum {OCTETS_LOT_MAX})", corps.len())));
        }
        let (url, hote) = match &self.cfg.endpoint {
            Some(e) => {
                let base = e.trim_end_matches('/');
                (format!("{base}/"), base.split("://").nth(1).unwrap_or(base).to_string())
            }
            None => {
                let h = format!("sqs.{}.amazonaws.com", self.cfg.region);
                (format!("https://{h}/"), h)
            }
        };
        let hash = sigv4::sha256_hex(corps.as_bytes());
        let mut derniere = ErreurSqs::Io("aucun essai".into());
        for essai in 0..self.cfg.essais.max(1) {
            if essai > 0 {
                std::thread::sleep(http::pause(self.cfg.pause_base, essai - 1));
            }
            let date = temps::amz(temps::maintenant());
            let mut entetes: Vec<(String, String)> = vec![
                ("content-type".into(), "application/x-amz-json-1.0".into()),
                ("host".into(), hote.clone()),
                ("x-amz-date".into(), date.clone()),
                ("x-amz-target".into(), "AmazonSQS.SendMessageBatch".into()),
            ];
            if let Some(j) = &self.ident.jeton {
                entetes.push(("x-amz-security-token".into(), j.clone()));
            }
            entetes.sort();
            let auth = sigv4::autorisation(
                &Demande { methode: "POST", chemin: "/", requete: "", entetes: &entetes, hash_charge: &hash, region: &self.cfg.region, service: "sqs", date_amz: &date },
                &self.ident.cle_acces,
                &self.ident.secret,
            );
            let mut b = self.agent.post(&url);
            for (k, v) in &entetes {
                if k != "host" {
                    b = b.header(k.as_str(), v.as_str());
                }
            }
            match b.header("authorization", auth.as_str()).send(corps.as_bytes()) {
                Ok(mut rep) => {
                    let statut = rep.status().as_u16();
                    let mut reponse = Vec::new();
                    if let Err(e) = rep.body_mut().as_reader().take(1 << 20).read_to_end(&mut reponse) {
                        derniere = ErreurSqs::Io(format!("lecture de la réponse : {e}"));
                        continue;
                    }
                    let texte = String::from_utf8_lossy(&reponse).into_owned();
                    if statut == 408 || statut == 429 || statut >= 500 {
                        derniere = ErreurSqs::Http { statut, corps: texte.chars().take(300).collect() };
                        continue;
                    }
                    if !(200..300).contains(&statut) {
                        return Err(ErreurSqs::Http { statut, corps: texte.chars().take(300).collect() });
                    }
                    let v: Value = serde_json::from_str(&texte).map_err(|e| ErreurSqs::Io(format!("réponse illisible : {e}")))?;
                    return Ok(analyser_reponse(&v));
                }
                Err(e) => derniere = ErreurSqs::Io(format!("SendMessageBatch : {e}")),
            }
        }
        Err(derniere)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn corps_standard_et_fifo() {
        let m = vec![
            Message { id: "m0".into(), corps: "{\"a\":1}".into(), groupe: None, deduplication: None },
            Message { id: "m1".into(), corps: "x".into(), groupe: Some("g".into()), deduplication: Some("d".into()) },
        ];
        let v = corps_lot("https://sqs.eu-north-1.amazonaws.com/1/f.fifo", &m);
        assert_eq!(v["QueueUrl"], "https://sqs.eu-north-1.amazonaws.com/1/f.fifo");
        assert_eq!(v["Entries"][0], json!({"Id": "m0", "MessageBody": "{\"a\":1}"}));
        assert_eq!(v["Entries"][1]["MessageGroupId"], "g");
        assert_eq!(v["Entries"][1]["MessageDeduplicationId"], "d");
        assert!(ConfigSqs::aws("r", "u/f.fifo").est_fifo() && !ConfigSqs::aws("r", "u/f").est_fifo());
    }

    #[test]
    fn reponse() {
        let v = json!({"Successful": [{"Id": "m0", "MessageId": "x", "MD5OfMessageBody": "y"}],
                       "Failed": [{"Id": "m1", "SenderFault": false, "Code": "InternalError", "Message": "oups"}]});
        let r = analyser_reponse(&v);
        assert_eq!(r.acceptes, vec!["m0"]);
        assert_eq!(r.refuses, vec![("m1".into(), "InternalError".into(), "oups".into())]);
        assert_eq!(analyser_reponse(&json!({})), ResultatLot::default());
    }

    #[test]
    fn limites() {
        let c = ClientSqs::new(ConfigSqs::aws("eu-north-1", "https://sqs.eu-north-1.amazonaws.com/1/f"), Identifiants { cle_acces: "a".into(), secret: "b".into(), jeton: None }).unwrap();
        let onze: Vec<Message> = (0..11).map(|i| Message { id: format!("m{i}"), corps: "x".into(), groupe: None, deduplication: None }).collect();
        assert!(c.envoyer_lot(&onze).is_err());
        assert_eq!(c.envoyer_lot(&[]).unwrap(), ResultatLot::default());
    }
}
