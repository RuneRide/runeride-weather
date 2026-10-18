// SPDX-License-Identifier: EUPL-1.2
//! Tests d'intégration locaux : faux S3 de `rust/tools/serveur_aws_local.py` (signature SigV4 vérifiée, listage, suppression),
//! aucun accès réseau externe, aucun compte AWS. Ignorés (message affiché) si `python3` est absent.

use rw_aws::s3::{ConfigS3, Identifiants, StockageS3};
use rw_aws::stockage::{Meta, Stockage};
use rw_aws::temps;
use rw_manifeste::manifeste::{Config, Contexte};
use serde_json::{json, Map, Value};
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::Duration;

const SECRET: &str = "secret-factice-pour-les-tests";
const CLE: &str = "AKIDEXEMPLEFACTICE";
const NOW: &str = "2026-10-17T15:10:00Z";
const VARIABLES: [&str; 8] = ["cape", "cloud_total", "gust10", "precip_rate", "rh2m", "snow_fraction", "t2m", "wind10"];
const JSON: Meta = Meta { type_contenu: "application/json", cache: "no-cache" };

fn racine() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..").canonicalize().unwrap()
}

macro_rules! prerequis {
    () => {
        if Command::new("python3").arg("--version").output().is_err() {
            eprintln!("ignoré : python3 absent");
            return;
        }
    };
}

struct Serveur {
    enfant: Child,
    port: u16,
    dossier: PathBuf,
}

impl Serveur {
    fn lancer(nom: &str) -> Self {
        let dossier = std::env::temp_dir().join(format!("rw-manifeste-it-{nom}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dossier);
        std::fs::create_dir_all(dossier.join("ovh")).unwrap();
        let mut enfant = Command::new("python3")
            .arg("-I")
            .arg(racine().join("rust/tools/serveur_aws_local.py"))
            .args(["--ovh", dossier.join("ovh").to_str().unwrap(), "--s3", dossier.join("s3").to_str().unwrap()])
            .args(["--sqs", dossier.join("sqs.jsonl").to_str().unwrap(), "--journal", dossier.join("journal.jsonl").to_str().unwrap()])
            .stdout(Stdio::piped())
            .spawn()
            .expect("python3");
        let mut ligne = String::new();
        BufReader::new(enfant.stdout.take().unwrap()).read_line(&mut ligne).unwrap();
        let port = ligne.trim().strip_prefix("PORT=").and_then(|s| s.split(' ').next()).and_then(|s| s.parse().ok()).expect("port du serveur");
        Self { enfant, port, dossier }
    }

    fn url(&self) -> String {
        format!("http://127.0.0.1:{}", self.port)
    }

    fn s3(&self, secret: &str) -> StockageS3 {
        let mut c = ConfigS3::aws("bucket-test", "eu-north-1");
        c.endpoint = Some(self.url());
        c.pause_base = Duration::from_millis(5);
        StockageS3::new(c, Identifiants { cle_acces: CLE.into(), secret: secret.into(), jeton: None }).unwrap()
    }

    fn contexte(&self, cfg: Config) -> Contexte {
        Contexte::nouveau(cfg, Box::new(self.s3(SECRET)))
    }

    fn ecrire(&self, cle: &str, octets: &[u8]) {
        self.s3(SECRET).ecrire(cle, octets, &JSON).unwrap();
    }

    fn lire_json(&self, cle: &str) -> Option<Value> {
        std::fs::read(self.dossier.join("s3/bucket-test").join(cle)).ok().map(|b| serde_json::from_slice(&b).unwrap())
    }

    fn meta(&self, cle: &str) -> Value {
        serde_json::from_slice(&std::fs::read(self.dossier.join("s3/.meta/bucket-test").join(format!("{cle}.json"))).unwrap()).unwrap()
    }

    fn existe(&self, cle: &str) -> bool {
        self.dossier.join("s3/bucket-test").join(cle).exists()
    }

    fn journal(&self) -> Vec<Value> {
        std::fs::read_to_string(self.dossier.join("journal.jsonl")).unwrap_or_default().lines().map(|l| serde_json::from_str(l).unwrap()).collect()
    }

    fn compter(&self, methode: &str, contient: &str) -> usize {
        self.journal().iter().filter(|e| e["methode"] == methode && e["chemin"].as_str().is_some_and(|c| c.contains(contient))).count()
    }

    /// Écrit les états `echeances` d'un tirage pour les `variables` (les variables à première échéance 1 sont absentes de 0).
    fn etats(&self, modele: &str, tirage: &str, echeances: impl IntoIterator<Item = u32>, variables: &[&str]) {
        for n in echeances {
            self.ecrire(&format!("v1/{modele}/{tirage}/_etat/{n:02}.json"), etat(modele, tirage, n, variables).to_string().as_bytes());
        }
    }
}

impl Drop for Serveur {
    fn drop(&mut self) {
        let _ = self.enfant.kill();
        let _ = self.enfant.wait();
        let _ = std::fs::remove_dir_all(&self.dossier);
    }
}

fn etat(modele: &str, tirage: &str, n: u32, variables: &[&str]) -> Value {
    let vars: Map<String, Value> = variables
        .iter()
        .filter(|v| n >= 1 || !matches!(**v, "gust10" | "precip_rate" | "snow_fraction" | "cloud_total"))
        .map(|v| {
            let mut d = json!({"chemin": format!("{modele}/{tirage}/{v}/{n:02}.pmtiles"), "octets": 1000 + n, "tuiles": 142, "minzoom": if modele == "arome" { 3 } else { 5 },
                "maxzoom": 7, "encoding": if *v == "wind10" { "wind8" } else { "scalar8" }, "scale": "linear", "unit": "K",
                "min": 243.15 + f64::from(n), "max": 306.65, "quant_step": 0.25});
            if *v == "wind10" {
                d["components"] = json!(["wind10_u", "wind10_v"]);
            }
            (v.to_string(), d)
        })
        .collect();
    json!({"schema": 1, "statut": "complete", "modele": modele, "tirage": tirage, "run": format!("{}:00:00Z", &tirage[..tirage.len() - 1]),
           "echeance": n, "valide": "2026-10-17T12:00:00Z", "traite": format!("2026-10-17T14:{:02}:00Z", n), "variables": vars, "isobares": null, "domaine": null})
}

fn now() -> i64 {
    temps::depuis_iso(NOW).unwrap()
}

fn cfg() -> Config {
    Config::default()
}

#[test]
fn agregation_latest_complete_et_bascule_atomique() {
    prerequis!();
    let srv = Serveur::lancer("agreg");
    srv.etats("arome", "2026-10-17T00Z", 0..=51, &VARIABLES);
    srv.etats("arome", "2026-10-17T12Z", 0..=10, &VARIABLES);
    srv.etats("arome-hd", "2026-10-17T12Z", 0..=3, &VARIABLES);
    let ctx = srv.contexte(cfg());

    let b = ctx.reconstruire(now()).unwrap();
    assert_eq!(b["ecrit"], true);
    let m = srv.lire_json("v1/manifest.json").unwrap();
    // forme du manifeste du Python
    assert_eq!(m["version"], 1);
    assert_eq!(m["file_template"], "{model}/{run_id}/{variable}/{step}.pmtiles");
    assert_eq!(m["attribution"], "Source : Météo-France, Licence Ouverte v2.0");
    assert_eq!(m["generated"], NOW);
    let cles: Vec<&str> = m.as_object().unwrap().keys().map(String::as_str).collect();
    assert_eq!(cles, ["version", "generated", "attribution", "file_template", "models", "variables", "couches", "hors_raster"]);
    let a = &m["models"]["arome"];
    assert_eq!(a["label"], "AROME");
    assert_eq!(a["horizon_h"], 51);
    assert_eq!(a["variables"], json!(VARIABLES));
    assert_eq!(a["runs"].as_array().unwrap().len(), 2);
    let (r0, r1) = (&a["runs"][0], &a["runs"][1]);
    assert_eq!((r0["id"].as_str(), r0["run"].as_str(), r0["path"].as_str(), r0["complete"].clone()), (Some("2026-10-17T00Z"), Some("2026-10-17T00:00:00Z"), Some("arome/2026-10-17T00Z"), json!(true)));
    assert_eq!(r0["variables"]["t2m"]["steps"].as_array().unwrap().len(), 52);
    assert_eq!(r0["variables"]["gust10"]["steps"][0], 1, "pas d'échéance 0 pour les rafales");
    assert_eq!(r0["variables"]["gust10"]["complete"], true);
    assert_eq!(r0["valid_debut"], "2026-10-17T00:00:00Z");
    assert_eq!(r0["valid_fin"], "2026-10-19T03:00:00Z");
    assert_eq!(r1["complete"], false);
    assert_eq!(r1["variables"]["t2m"]["steps"], json!((0..=10).collect::<Vec<u32>>()));
    assert_eq!(r1["variables"]["t2m"]["complete"], false);
    assert_eq!(r1["traite"], "2026-10-17T14:10:00Z", "dernier traitement des états");
    assert_eq!(a["zooms"]["t2m"], json!([3, 7]));
    assert_eq!(m["models"]["arome-hd"]["zooms"]["t2m"], json!([5, 7]));
    // nouveau champ : dernier tirage complet par modèle ; HD n'en a pas
    assert_eq!(a["latest_complete"], "2026-10-17T00Z");
    assert_eq!(m["models"]["arome-hd"]["latest_complete"], Value::Null);
    // variables et couches
    assert_eq!(m["variables"]["wind10"]["components"], json!(["wind10_u", "wind10_v"]));
    assert_eq!(m["variables"]["t2m"]["models"], json!(["arome", "arome-hd"]));
    assert_eq!(m["variables"]["gust10"]["first_step"], 1);
    assert_eq!(m["couches"]["vent"], json!({"nom": "Vent", "variable": "wind10", "models": ["arome", "arome-hd"]}));
    assert!(m["hors_raster"]["pression"].is_object());
    // pas de polygone calculé (aucun _domaine) : rectangle de la grille, comme le Python
    assert_eq!(a["domain"]["source"], "grille (rectangle, champs canoniques absents)");
    assert_eq!(a["domain"]["vertices"], 4);
    // en-têtes du manifeste : cache court, JSON
    let meta = srv.meta("v1/manifest.json");
    assert_eq!(meta["cache-control"], "public, max-age=30");
    assert_eq!(meta["content-type"], "application/json");
    // le manifeste est le DERNIER objet écrit
    let puts: Vec<String> = srv.journal().iter().filter(|e| e["methode"] == "PUT").map(|e| e["cle"].as_str().unwrap().to_string()).collect();
    assert_eq!(puts.last().unwrap(), "v1/manifest.json");
    assert_eq!(puts.iter().filter(|c| c.as_str() == "v1/manifest.json").count(), 1);

    // inchangé : pas de réécriture, `generated` conservé ; les états ne sont pas relus (cache par ETag)
    let gets_etat = srv.compter("GET", "_etat/");
    let b = ctx.reconstruire(now() + 300).unwrap();
    assert_eq!(b["ecrit"], false);
    assert_eq!(srv.lire_json("v1/manifest.json").unwrap()["generated"], NOW);
    assert_eq!(srv.compter("GET", "_etat/"), gets_etat + 0, "états en cache : seulement des listages");

    // le tirage 12Z se complète : la bascule atomique fait passer latest_complete au 12Z, en un seul PUT
    srv.etats("arome", "2026-10-17T12Z", 11..=51, &VARIABLES);
    let b = ctx.reconstruire(now() + 600).unwrap();
    assert_eq!(b["ecrit"], true);
    let m = srv.lire_json("v1/manifest.json").unwrap();
    assert_eq!(m["models"]["arome"]["latest_complete"], "2026-10-17T12Z");
    assert_eq!(m["models"]["arome"]["runs"][1]["complete"], true);
    assert_eq!(m["generated"], "2026-10-17T15:20:00Z");
}

#[test]
fn retention_manifeste_et_purge() {
    prerequis!();
    let srv = Serveur::lancer("retention");
    // âges au 17/10 15:10 : 12Z = 3 h, 16/10 00Z = 39 h (cité), 15/10 12Z = 51 h (hors manifeste), 14/10 00Z = 111 h
    for t in ["2026-10-17T12Z", "2026-10-16T00Z", "2026-10-15T12Z", "2026-10-14T00Z"] {
        srv.etats("arome", t, 0..=2, &["t2m"]);
        srv.ecrire(&format!("v1/arome/{t}/t2m/00.pmtiles"), b"x");
        srv.ecrire(&format!("v1/_poller/arome/{t}.json"), b"{}");
        srv.ecrire(&format!("v1/isobares/arome/{t}/00.geojson"), b"{}");
    }
    let ctx = srv.contexte(cfg());
    let b = ctx.reconstruire(now()).unwrap();
    let m = srv.lire_json("v1/manifest.json").unwrap();
    let ids: Vec<&str> = m["models"]["arome"]["runs"].as_array().unwrap().iter().map(|r| r["id"].as_str().unwrap()).collect();
    assert_eq!(ids, ["2026-10-16T00Z", "2026-10-17T12Z"]);
    assert_eq!(b["modeles"]["arome"]["tirages_expires"], 2);
    assert_eq!(b["purge"]["supprimes"], 0, "pas de purge par défaut : le cycle de vie du bucket s'en charge");
    assert!(srv.existe("v1/arome/2026-10-14T00Z/t2m/00.pmtiles"));
    // plafond de tirages cités
    let mut c = cfg();
    c.tirages_max = 1;
    let ctx1 = srv.contexte(c);
    ctx1.reconstruire(now()).unwrap();
    let ids: Vec<Value> = srv.lire_json("v1/manifest.json").unwrap()["models"]["arome"]["runs"].as_array().unwrap().iter().map(|r| r["id"].clone()).collect();
    assert_eq!(ids, [json!("2026-10-17T12Z")]);

    // purge physique au-delà de 48 h : supprime 15/10 12Z et 14/10 00Z (objets et marqueur), jamais ce qui est cité
    let mut c = cfg();
    c.purge_age_h = Some(48);
    let ctx = srv.contexte(c);
    let b = ctx.reconstruire(now()).unwrap();
    assert_eq!(b["purge"]["tirages"], 2);
    assert_eq!(b["purge"]["supprimes"], 2 * (3 + 1 + 1 + 1));
    for t in ["2026-10-15T12Z", "2026-10-14T00Z"] {
        assert!(!srv.existe(&format!("v1/arome/{t}/t2m/00.pmtiles")) && !srv.existe(&format!("v1/arome/{t}/_etat/00.json")) && !srv.existe(&format!("v1/_poller/arome/{t}.json")) && !srv.existe(&format!("v1/isobares/arome/{t}/00.geojson")));
    }
    assert!(srv.existe("v1/arome/2026-10-16T00Z/t2m/00.pmtiles") && srv.existe("v1/arome/2026-10-17T12Z/_etat/02.json") && srv.existe("v1/isobares/arome/2026-10-16T00Z/00.geojson"));
    // le manifeste est écrit (ou confirmé inchangé) AVANT toute suppression
    let j = srv.journal();
    let premiere_suppression = j.iter().position(|e| e["methode"] == "DELETE").unwrap();
    let dernier_put_manifeste = j.iter().rposition(|e| e["methode"] == "PUT" && e["cle"] == "v1/manifest.json").unwrap();
    assert!(dernier_put_manifeste < premiere_suppression);
    // plafond de suppressions par invocation
    let srv2 = Serveur::lancer("purge-max");
    for t in ["2026-10-14T00Z", "2026-10-13T00Z"] {
        srv2.etats("arome", t, 0..=3, &["t2m"]);
    }
    srv2.etats("arome", "2026-10-17T12Z", 0..=0, &["t2m"]);
    let mut c = cfg();
    c.purge_age_h = Some(48);
    c.purge_max = 5;
    let b = srv2.contexte(c).reconstruire(now()).unwrap();
    assert_eq!(b["purge"]["supprimes"], 5);
}

#[test]
fn polygone_du_domaine_depuis_le_npz() {
    prerequis!();
    let canon = racine().join("out/canon/arome/2026-10-17T06Z/03.npz");
    let ref_py = racine().join("out/manifest/manifest.json");
    if !canon.exists() || !ref_py.exists() {
        eprintln!("ignoré : canonique ou manifeste de référence absent");
        return;
    }
    let srv = Serveur::lancer("domaine");
    srv.etats("arome", "2026-10-17T12Z", 0..=2, &["t2m"]);
    srv.ecrire("v1/arome/2026-10-17T12Z/_domaine/03.npz", &std::fs::read(&canon).unwrap());
    let mut e = etat("arome", "2026-10-17T12Z", 3, &["t2m"]);
    e["domaine"] = json!({"cle": "v1/arome/2026-10-17T12Z/_domaine/03.npz", "octets": std::fs::metadata(&canon).unwrap().len(), "variable": "t2m"});
    srv.ecrire("v1/arome/2026-10-17T12Z/_etat/03.json", e.to_string().as_bytes());
    let ctx = srv.contexte(cfg());
    ctx.reconstruire(now()).unwrap();
    let m = srv.lire_json("v1/manifest.json").unwrap();
    let py: Value = serde_json::from_slice(&std::fs::read(ref_py).unwrap()).unwrap();
    let d = &m["models"]["arome"]["domain"];
    assert_eq!(d["polygon"], py["models"]["arome"]["domain"]["polygon"], "polygone identique à celui du Python");
    assert_eq!(d["vertices"], py["models"]["arome"]["domain"]["vertices"]);
    assert_eq!(d["source"], "arome/2026-10-17T12Z/_domaine/03");
    assert_eq!(d["tolerance_deg"], 0.04);
    // le polygone est mis en cache : un second passage ne relit pas le .npz
    let gets = srv.compter("GET", "_domaine");
    ctx.reconstruire(now() + 60).unwrap();
    assert_eq!(srv.compter("GET", "_domaine"), gets);
}

#[test]
fn etats_echoues_ou_illisibles_ne_sont_pas_publies() {
    prerequis!();
    let srv = Serveur::lancer("invalides");
    srv.etats("arome-hd", "2026-10-17T12Z", [0, 1, 3], &["t2m"]);
    srv.ecrire("v1/arome-hd/2026-10-17T12Z/_etat/02.json", br#"{"schema":1,"statut":"echec_definitif","modele":"arome-hd","tirage":"2026-10-17T12Z","echeance":2}"#);
    srv.ecrire("v1/arome-hd/2026-10-17T12Z/_etat/04.json", b"{pas du json");
    srv.ecrire("v1/arome-hd/2026-10-17T12Z/_etat/05.json", br#"{"schema":9,"statut":"complete"}"#);
    srv.ecrire("v1/arome-hd/2026-10-17T12Z/_etat/06.json", etat("arome", "2026-10-17T12Z", 6, &["t2m"]).to_string().as_bytes()); // mauvais modèle
    srv.ecrire("v1/arome-hd/2026-10-17T12Z/_etat/note.txt", b"x");
    let b = srv.contexte(cfg()).reconstruire(now()).unwrap();
    assert_eq!((b["echecs_definitifs"].clone(), b["etats_ignores"].clone()), (json!(1), json!(3)));
    let m = srv.lire_json("v1/manifest.json").unwrap();
    assert_eq!(m["models"]["arome-hd"]["runs"][0]["variables"]["t2m"]["steps"], json!([0, 1, 3]), "un trou reste un trou (échéance 2 en échec)");
    assert!(m["models"].get("arome").is_none(), "un modèle sans tirage est absent, comme dans le Python");
}

#[test]
fn bucket_sans_etat_donne_un_manifeste_vide_valide() {
    prerequis!();
    let srv = Serveur::lancer("vide");
    let b = srv.contexte(cfg()).reconstruire(now()).unwrap();
    assert_eq!(b["ecrit"], true);
    let m = srv.lire_json("v1/manifest.json").unwrap();
    assert_eq!(m["models"], json!({}));
    assert_eq!(m["couches"], json!({}));
}

#[test]
fn binaire_evenements_sqs() {
    prerequis!();
    let srv = Serveur::lancer("bin");
    srv.etats("arome", "2026-10-17T12Z", 0..=2, &["t2m"]);
    let notif = |cle: &str| json!({"Records": [{"eventSource": "aws:s3", "eventName": "ObjectCreated:Put", "s3": {"bucket": {"name": "bucket-test"}, "object": {"key": cle, "size": 1}}}]}).to_string();
    let enveloppe = |corps: Vec<String>| {
        let r: Vec<Value> = corps.iter().enumerate().map(|(i, b)| json!({"messageId": format!("m{i}"), "receiptHandle": "r", "body": b, "eventSource": "aws:sqs"})).collect();
        json!({"Records": r, "maintenant": NOW})
    };
    let evt = srv.dossier.join("evt.json");
    let lancer = |e: Value, secret: &str| {
        std::fs::write(&evt, e.to_string()).unwrap();
        Command::new(env!("CARGO_BIN_EXE_rw-manifeste"))
            .args(["--evenement", evt.to_str().unwrap()])
            .env_clear()
            .envs([("RW_BUCKET", "bucket-test"), ("AWS_REGION", "eu-north-1"), ("AWS_ACCESS_KEY_ID", CLE), ("AWS_SECRET_ACCESS_KEY", secret), ("RW_S3_ENDPOINT", srv.url().as_str()), ("RW_PAUSE_MS", "10")])
            .output()
            .unwrap()
    };
    // événement de test S3 et clés étrangères (manifeste lui-même, tuiles, marqueurs) : aucune reconstruction, aucune requête
    let ignores = enveloppe(vec![
        r#"{"Service":"Amazon S3","Event":"s3:TestEvent","Bucket":"bucket-test"}"#.into(),
        notif("v1/manifest.json"),
        notif("v1/arome/2026-10-17T12Z/t2m/00.pmtiles"),
        notif("v1/_poller/arome/2026-10-17T12Z.json"),
    ]);
    let avant = srv.journal().len();
    let s = lancer(ignores, SECRET);
    assert!(s.status.success(), "{}", String::from_utf8_lossy(&s.stderr));
    assert_eq!(serde_json::from_slice::<Value>(&s.stdout).unwrap(), json!({"batchItemFailures": []}));
    assert_eq!(srv.journal().len(), avant, "rien ne doit être demandé à S3");
    // notifications d'états : une seule reconstruction pour tout le lot ; clé encodée comme dans les notifications S3
    let utiles = enveloppe(vec![notif("v1/arome/2026-10-17T12Z/_etat/00.json"), notif("v1/arome/2026-10-17T12Z/_etat/01.json"), notif("v1%2Farome/2026-10-17T12Z/_etat/02.json")]);
    let s = lancer(utiles.clone(), SECRET);
    assert!(s.status.success(), "{}", String::from_utf8_lossy(&s.stderr));
    assert_eq!(serde_json::from_slice::<Value>(&s.stdout).unwrap(), json!({"batchItemFailures": []}));
    assert_eq!(srv.compter("PUT", "manifest.json"), 1);
    assert!(srv.lire_json("v1/manifest.json").is_some());
    // échec (signature refusée) : tous les messages du lot sont rendus à SQS, code de sortie 4
    let s = lancer(utiles, "mauvais-secret");
    assert_eq!(s.status.code(), Some(4), "{}", String::from_utf8_lossy(&s.stderr));
    let r: Value = serde_json::from_slice(&s.stdout).unwrap();
    assert_eq!(r["batchItemFailures"], json!([{"itemIdentifier": "m0"}, {"itemIdentifier": "m1"}, {"itemIdentifier": "m2"}]));
    // invocation directe (sans enregistrement SQS) : reconstruction forcée
    let s = lancer(json!({"maintenant": NOW}), SECRET);
    assert!(s.status.success());
    assert_eq!(serde_json::from_slice::<Value>(&s.stdout).unwrap()["ecrit"], false);
}
