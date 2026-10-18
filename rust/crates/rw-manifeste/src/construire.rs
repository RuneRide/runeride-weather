// SPDX-License-Identifier: EUPL-1.2
//! Construction du manifeste à partir des états d'échéance (sans entrées-sorties).
//!
//! Même schéma que `pipeline/manifest/build.py` (`docs/poc-j3/manifest.md` §1) ; ajouts compatibles :
//! `models.<m>.latest_complete` (identifiant du dernier tirage complet, ou `null`).

use crate::etat::{Etat, VarEtat};
use anyhow::{bail, Result};
use rw_aws::temps;
use serde_json::{json, Map, Value};
use std::collections::{BTreeMap, BTreeSet};

pub const VERSION: u64 = 1;
pub const ATTRIBUTION: &str = "Source : Météo-France, Licence Ouverte v2.0";
pub const MODELE_FICHIER: &str = "{model}/{run_id}/{variable}/{step}.pmtiles";

pub struct Modele {
    pub cle: &'static str,
    pub label: &'static str,
    pub resolution_deg: f64,
    pub grid: [f64; 4],
    pub horizon_h: u32,
}

/// Description statique des modèles (`MODELES` du Python), triés par nom comme l'inventaire du Python.
pub const MODELES: [Modele; 2] = [
    Modele { cle: "arome", label: "AROME", resolution_deg: 0.025, grid: [-12.0, 37.5, 16.0, 55.4], horizon_h: 51 },
    Modele { cle: "arome-hd", label: "AROME HD", resolution_deg: 0.01, grid: [-12.0, 37.5, 16.0, 55.4], horizon_h: 51 },
];

/// Première échéance publiée par variable (`PREMIERE_ECHEANCE` du Python).
pub fn premiere_echeance(variable: &str) -> u32 {
    match variable {
        "gust10" | "precip_rate" | "snow_fraction" | "cloud_total" => 1,
        _ => 0,
    }
}

const COUCHES: [(&str, &str, &str); 8] = [
    ("vent", "Vent", "wind10"),
    ("rafales", "Rafales", "gust10"),
    ("t2m", "Température", "t2m"),
    ("pluie", "Pluie", "precip_rate"),
    ("nuages", "Nuages", "cloud_total"),
    ("humidite", "Humidité", "rh2m"),
    ("neige", "Neige (part)", "snow_fraction"),
    ("cape", "Orages (CAPE)", "cape"),
];

/// Domaine d'un modèle : anneau fermé `[lon, lat]` et origine.
#[derive(Debug, Clone, PartialEq)]
pub struct Domaine {
    pub polygone: Vec<[f64; 2]>,
    pub source: String,
}

pub fn domaine_rectangle(m: &Modele) -> Domaine {
    let [w, s, e, n] = m.grid;
    Domaine { polygone: vec![[w, n], [e, n], [e, s], [w, s], [w, n]], source: "grille (rectangle, champs canoniques absents)".into() }
}

/// Tirages d'un modèle : identifiant court → états (par échéance).
pub type Tirages = BTreeMap<String, BTreeMap<u32, Etat>>;

pub struct Entrees<'a> {
    pub etats: &'a BTreeMap<String, Tirages>,
    pub domaines: &'a BTreeMap<String, Domaine>,
    pub generated: &'a str,
    pub tolerance_deg: f64,
}

fn iso_plus(run: i64, heures: u32) -> String {
    temps::iso(run + 3600 * i64::from(heures))
}

fn complete(variable: &str, steps: &[u32], horizon: u32) -> bool {
    let attendu: Vec<u32> = (premiere_echeance(variable)..=horizon).collect();
    steps == attendu.as_slice()
}

fn entree_variable(v: &str, e: &VarEtat) -> Value {
    let mut o = Map::new();
    o.insert("encoding".into(), e.encoding.clone());
    o.insert("scale".into(), e.scale.clone());
    o.insert("unit".into(), e.unit.clone());
    o.insert("first_step".into(), json!(premiere_echeance(v)));
    // D14 : plage et pas propres à chaque fichier ; valeurs indicatives (échéance lue), le client relit les PMTiles
    o.insert("per_file_range".into(), json!(true));
    o.insert("min".into(), e.min.clone());
    o.insert("max".into(), e.max.clone());
    o.insert("quant_step".into(), e.quant_step.clone());
    if let Some(c) = &e.components {
        o.insert("components".into(), c.clone());
    }
    Value::Object(o)
}

/// Construit le manifeste. Les modèles sans aucun tirage sont absents (comme le Python).
pub fn construire(e: &Entrees<'_>) -> Result<Value> {
    let mut modeles = Map::new();
    let mut variables: Map<String, Value> = Map::new();
    for m in MODELES.iter() {
        let Some(tirages) = e.etats.get(m.cle).filter(|t| t.values().any(|x| !x.is_empty())) else { continue };
        let tirages: BTreeMap<&String, &BTreeMap<u32, Etat>> = tirages.iter().filter(|(_, x)| !x.is_empty()).collect();
        let toutes: BTreeSet<&str> = tirages.values().flat_map(|t| t.values().flat_map(|x| x.variables.keys().map(String::as_str))).collect();
        let domaine = e.domaines.get(m.cle).cloned().unwrap_or_else(|| domaine_rectangle(m));
        let mut runs = Vec::new();
        for (id, etats) in &tirages {
            let run_iso = &etats.values().next().unwrap().run;
            let run = temps::depuis_iso(run_iso).ok_or_else(|| anyhow::anyhow!("run illisible : {run_iso}"))?;
            let mut entrees = Map::new();
            let mut tous_pas = Vec::new();
            let mut toutes_completes = true;
            for v in &toutes {
                let steps: Vec<u32> = etats.iter().filter(|(_, x)| x.variables.contains_key(*v)).map(|(n, _)| *n).collect();
                if steps.is_empty() {
                    toutes_completes = false;
                    continue;
                }
                let c = complete(v, &steps, m.horizon_h);
                toutes_completes &= c;
                tous_pas.extend(steps.iter().copied());
                entrees.insert((*v).to_string(), json!({"steps": steps, "complete": c}));
            }
            let traite = etats.values().map(|x| x.traite.as_str()).max().unwrap_or_default();
            runs.push(json!({
                "run": temps::iso(run), "id": id, "traite": traite,
                "valid_debut": iso_plus(run, *tous_pas.iter().min().unwrap_or(&0)),
                "valid_fin": iso_plus(run, *tous_pas.iter().max().unwrap_or(&0)),
                "complete": toutes_completes, "path": format!("{}/{id}", m.cle), "variables": Value::Object(entrees),
            }));
        }
        // zooms et métadonnées de variable : tirage le plus récent qui contient la variable, plus petite échéance
        let mut zooms = Map::new();
        for v in &toutes {
            let Some(ve) = tirages.values().rev().find_map(|t| t.values().find_map(|x| x.variables.get(*v))) else { continue };
            zooms.insert((*v).to_string(), json!([ve.minzoom, ve.maxzoom]));
            let entree = entree_variable(v, ve);
            match variables.get_mut(*v) {
                None => {
                    let mut o = entree;
                    o["models"] = json!([m.cle]);
                    variables.insert((*v).to_string(), o);
                }
                Some(ancien) => {
                    if ancien["encoding"] != entree["encoding"] {
                        bail!("encodage de {v} différent entre modèles : {} / {}", ancien["encoding"], entree["encoding"]);
                    }
                    ancien["models"].as_array_mut().unwrap().push(json!(m.cle));
                }
            }
        }
        let latest = runs.iter().rev().find(|r| r["complete"] == true).map(|r| r["id"].clone()).unwrap_or(Value::Null);
        modeles.insert(
            m.cle.to_string(),
            json!({
                "label": m.label, "resolution_deg": m.resolution_deg, "grid": m.grid,
                "domain": {"polygon": domaine.polygone, "vertices": domaine.polygone.len() - 1, "source": domaine.source, "tolerance_deg": e.tolerance_deg},
                "horizon_h": m.horizon_h, "zooms": Value::Object(zooms), "variables": toutes.iter().collect::<Vec<_>>(), "runs": runs,
                "latest_complete": latest,
            }),
        );
    }
    let mut couches = Map::new();
    for (cle, nom, v) in COUCHES {
        if let Some(def) = variables.get(v) {
            couches.insert(cle.to_string(), json!({"nom": nom, "variable": v, "models": def["models"]}));
        }
    }
    Ok(json!({
        "version": VERSION, "generated": e.generated, "attribution": ATTRIBUTION, "file_template": MODELE_FICHIER,
        "models": Value::Object(modeles), "variables": Value::Object(variables), "couches": Value::Object(couches),
        "hors_raster": {"pression": {"variable": "mslp", "models": ["arome"], "rendu": "isobares vectorielles",
            "note": "pas de pression réduite au niveau de la mer en 0,01° ; non tuilée en raster"}},
    }))
}

/// Deux manifestes sont-ils identiques hors `generated` ? (pour ne pas réécrire un fichier inchangé)
pub fn equivalents(a: &Value, b: &Value) -> bool {
    let sans = |v: &Value| {
        let mut c = v.clone();
        if let Some(o) = c.as_object_mut() {
            o.remove("generated");
        }
        c
    };
    sans(a) == sans(b)
}

