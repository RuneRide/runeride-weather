// SPDX-License-Identifier: EUPL-1.2
//! État d'échéance écrit par le worker (`<préfixe>/<modèle>/<tirage>/_etat/NN.json`, schéma 1) : voir
//! `docs/poc-rust/lambda.md` §7.

use serde_json::{Map, Value};
use std::collections::BTreeMap;

/// Version du schéma d'état comprise par ce binaire.
pub const SCHEMA_ETAT: u64 = 1;

/// Descripteur d'une variable à une échéance (extrait de l'état).
#[derive(Debug, Clone, PartialEq)]
pub struct VarEtat {
    pub minzoom: i64,
    pub maxzoom: i64,
    pub encoding: Value,
    pub scale: Value,
    pub unit: Value,
    pub min: Value,
    pub max: Value,
    pub quant_step: Value,
    pub components: Option<Value>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Etat {
    pub modele: String,
    /// `2026-10-17T06Z`
    pub tirage: String,
    /// `2026-10-17T06:00:00Z`
    pub run: String,
    pub echeance: u32,
    /// Instant de traitement (ISO UTC).
    pub traite: String,
    pub variables: BTreeMap<String, VarEtat>,
    /// Clé S3 du masque du domaine, si cette échéance l'a écrit.
    pub domaine: Option<(String, u64)>,
}

/// Résultat de l'analyse d'un état.
#[derive(Debug, PartialEq)]
pub enum Analyse {
    Complet(Box<Etat>),
    /// `echec_definitif` : l'échéance n'est pas publiée (journalisé, jamais dans le manifeste).
    Echec,
}

pub fn analyser(octets: &[u8]) -> Result<Analyse, String> {
    let v: Value = serde_json::from_slice(octets).map_err(|e| format!("JSON illisible : {e}"))?;
    let schema = v["schema"].as_u64().ok_or("schema absent")?;
    if schema != SCHEMA_ETAT {
        return Err(format!("schéma d'état {schema} non géré (attendu {SCHEMA_ETAT})"));
    }
    match v["statut"].as_str() {
        Some("complete") => {}
        Some("echec_definitif") => return Ok(Analyse::Echec),
        autre => return Err(format!("statut inconnu : {autre:?}")),
    }
    let texte = |k: &str| v[k].as_str().map(str::to_string).ok_or_else(|| format!("{k} absent"));
    let mut variables = BTreeMap::new();
    for (nom, d) in v["variables"].as_object().ok_or("variables absentes")? {
        let entier = |k: &str| d[k].as_i64().ok_or_else(|| format!("variables.{nom}.{k} absent"));
        let champ = |k: &str| d.get(k).cloned().unwrap_or(Value::Null);
        variables.insert(
            nom.clone(),
            VarEtat {
                minzoom: entier("minzoom")?,
                maxzoom: entier("maxzoom")?,
                encoding: champ("encoding"),
                scale: champ("scale"),
                unit: champ("unit"),
                min: champ("min"),
                max: champ("max"),
                quant_step: champ("quant_step"),
                components: d.get("components").filter(|c| !c.is_null()).cloned(),
            },
        );
    }
    let domaine = v["domaine"].as_object().and_then(|o: &Map<String, Value>| Some((o.get("cle")?.as_str()?.to_string(), o.get("octets")?.as_u64()?)));
    Ok(Analyse::Complet(Box::new(Etat {
        modele: texte("modele")?,
        tirage: texte("tirage")?,
        run: texte("run")?,
        echeance: v["echeance"].as_u64().ok_or("echeance absente")? as u32,
        traite: texte("traite")?,
        variables,
        domaine,
    })))
}

#[cfg(test)]
mod tests {
    use super::*;

    pub fn exemple(modele: &str, tirage: &str, echeance: u32, variables: &[&str]) -> Value {
        let vars: Map<String, Value> = variables
            .iter()
            .map(|v| {
                let mut d = serde_json::json!({"chemin": "x", "cle": "y", "octets": 1, "tuiles": 3, "minzoom": 3, "maxzoom": 7,
                    "encoding": if *v == "wind10" { "wind8" } else { "scalar8" }, "scale": "linear", "unit": "K", "min": 243.15, "max": 306.65, "quant_step": 0.25});
                if *v == "wind10" {
                    d["components"] = serde_json::json!(["wind10_u", "wind10_v"]);
                }
                (v.to_string(), d)
            })
            .collect();
        serde_json::json!({"schema": 1, "statut": "complete", "modele": modele, "tirage": tirage, "run": format!("{}:00:00Z", &tirage[..tirage.len() - 1]),
            "echeance": echeance, "valide": "x", "traite": "2026-10-17T14:00:00Z", "variables": vars, "isobares": null, "domaine": null})
    }

    #[test]
    fn analyse_complete() {
        let mut v = exemple("arome", "2026-10-17T06Z", 3, &["t2m", "wind10"]);
        v["domaine"] = serde_json::json!({"cle": "v1/arome/2026-10-17T06Z/_domaine/03.npz", "octets": 99, "variable": "t2m"});
        let Analyse::Complet(e) = analyser(v.to_string().as_bytes()).unwrap() else { panic!() };
        assert_eq!((e.modele.as_str(), e.echeance, e.variables.len()), ("arome", 3, 2));
        assert_eq!(e.variables["wind10"].components, Some(serde_json::json!(["wind10_u", "wind10_v"])));
        assert_eq!(e.domaine, Some(("v1/arome/2026-10-17T06Z/_domaine/03.npz".into(), 99)));
        assert_eq!(e.variables["t2m"].components, None);
    }

    #[test]
    fn echec_et_refus() {
        let echec = br#"{"schema":1,"statut":"echec_definitif","modele":"arome","tirage":"x","echeance":1}"#;
        assert_eq!(analyser(echec).unwrap(), Analyse::Echec);
        assert!(analyser(b"{").is_err());
        assert!(analyser(br#"{"schema":2,"statut":"complete"}"#).is_err());
        assert!(analyser(br#"{"schema":1,"statut":"autre"}"#).is_err());
        assert!(analyser(br#"{"schema":1,"statut":"complete","modele":"arome"}"#).is_err());
    }
}
