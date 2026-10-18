// SPDX-License-Identifier: EUPL-1.2
//! Tests d'intégration locaux : source GRIB et S3 simulés par `rust/tools/serveur_local.py` (aucun accès réseau
//! externe, aucun compte AWS). Ignorés (message affiché) si `python3` ou les GRIB de `out/rust/src` manquent.

use aws_lambda_events::event::sqs::SqsEvent;
use rw_lambda::erreur::Erreur;
use rw_lambda::handler::{traiter_corps, traiter_lot, Contexte};
use rw_lambda::s3::{ConfigS3, Identifiants, StockageS3};
use rw_lambda::source::SourceHttp;
use rw_lambda::stockage::{Stockage, StockageLocal};
use rw_lambda::traitement::Config;
use serde_json::{json, Value};
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::Duration;

const SECRET: &str = "secret-factice-pour-les-tests";
const CLE: &str = "AKIDEXEMPLEFACTICE";

fn racine() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..").canonicalize().unwrap()
}

fn grib_disponibles() -> bool {
    racine().join("out/rust/src/arome__001__SP1__12H__2026-10-17T03:00:00Z.grib2").exists()
        && Command::new("python3").arg("--version").output().is_ok()
}

macro_rules! prerequis {
    () => {
        if !grib_disponibles() {
            eprintln!("ignoré : GRIB de out/rust/src ou python3 absents");
            return;
        }
    };
}

/// Serveur Python local ; arrêté par son identifiant de processus à la destruction (jamais par nom).
struct Serveur {
    enfant: Child,
    port: u16,
    dossier: PathBuf,
}

impl Serveur {
    fn lancer(nom: &str, source: &Path, extra: &[&str]) -> Self {
        let dossier = std::env::temp_dir().join(format!("rw-lambda-it-{nom}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dossier);
        std::fs::create_dir_all(&dossier).unwrap();
        let mut enfant = Command::new("python3")
            .arg("-I")
            .arg(racine().join("rust/tools/serveur_local.py"))
            .args(["--source", source.to_str().unwrap(), "--s3", dossier.join("s3").to_str().unwrap()])
            .args(["--journal", dossier.join("journal.jsonl").to_str().unwrap(), "--secret", SECRET, "--cle-acces", CLE])
            .args(extra)
            .stdout(Stdio::piped())
            .spawn()
            .expect("python3");
        let mut ligne = String::new();
        BufReader::new(enfant.stdout.take().unwrap()).read_line(&mut ligne).unwrap();
        let port = ligne.trim().strip_prefix("PORT=").and_then(|s| s.split(' ').next()).and_then(|s| s.parse().ok()).expect("port du serveur");
        Self { enfant, port, dossier }
    }

    fn source_http(&self) -> SourceHttp {
        SourceHttp::new(format!("http://127.0.0.1:{}/pnt", self.port)).avec_essais(4, Duration::from_millis(20))
    }

    fn s3(&self, secret: &str) -> StockageS3 {
        let mut c = ConfigS3::aws("bucket-test", "eu-north-1");
        c.endpoint = Some(format!("http://127.0.0.1:{}", self.port));
        c.pause_base = Duration::from_millis(10);
        StockageS3::new(c, Identifiants { cle_acces: CLE.into(), secret: secret.into(), jeton: Some("jeton-factice".into()) }).unwrap()
    }

    fn ctx_s3(&self) -> Contexte {
        Contexte { source: Box::new(self.source_http()), sortie: Box::new(self.s3(SECRET)), cfg: Config::default() }
    }

    fn objet(&self, cle: &str) -> PathBuf {
        self.dossier.join("s3/bucket-test").join(cle)
    }

    fn meta(&self, cle: &str) -> Value {
        serde_json::from_slice(&std::fs::read(self.dossier.join("s3/.meta/bucket-test").join(format!("{cle}.json"))).unwrap()).unwrap()
    }

    fn journal(&self) -> Vec<Value> {
        std::fs::read_to_string(self.dossier.join("journal.jsonl"))
            .unwrap_or_default()
            .lines()
            .map(|l| serde_json::from_str(l).unwrap())
            .collect()
    }

    fn requetes_grib(&self) -> Vec<Value> {
        self.journal().into_iter().filter(|e| e["chemin"].as_str().is_some_and(|c| c.starts_with("/pnt/")) && e["methode"] == "GET").collect()
    }
}

impl Drop for Serveur {
    fn drop(&mut self) {
        let _ = self.enfant.kill();
        let _ = self.enfant.wait();
        let _ = std::fs::remove_dir_all(&self.dossier);
    }
}

fn corps(modele: &str, tirage: &str, echeance: u32) -> String {
    json!({"modele": modele, "tirage": tirage, "echeance": echeance}).to_string()
}

fn evenement(corps: &[(&str, String)]) -> SqsEvent {
    let r: Vec<Value> = corps
        .iter()
        .map(|(id, b)| {
            json!({"messageId": id, "receiptHandle": "r", "body": b, "attributes": {}, "messageAttributes": {}, "md5OfBody": "x",
                   "eventSource": "aws:sqs", "eventSourceARN": "arn:aws:sqs:eu-north-1:000000000000:fictive", "awsRegion": "eu-north-1"})
        })
        .collect();
    serde_json::from_value(json!({"Records": r})).unwrap()
}

const VARIABLES_HD: [&str; 8] = ["cape", "cloud_total", "gust10", "precip_rate", "rh2m", "snow_fraction", "t2m", "wind10"];

#[test]
fn hd_s3_identique_au_poc_et_idempotent() {
    prerequis!();
    let srv = Serveur::lancer("hd", &racine().join("out/rust/src"), &[]);
    let ctx = srv.ctx_s3();
    let c = corps("arome-hd", "2026-10-17T03:00:00Z", 12);

    let r = traiter_corps(&ctx, &c).unwrap();
    assert_eq!(r.statut, "traite");
    let pref = "v1/arome-hd/2026-10-17T03Z";
    for v in VARIABLES_HD {
        let sortie = std::fs::read(srv.objet(&format!("{pref}/{v}/12.pmtiles"))).unwrap_or_else(|_| panic!("{v} absent"));
        // octet pour octet identique à la sortie `rw run --webp image-webp` du PoC (si présente)
        let ref_ = racine().join(format!("out/rust/rs/tiles-pur/arome-hd/2026-10-17T03Z/{v}/12.pmtiles"));
        if let Ok(attendu) = std::fs::read(&ref_) {
            assert!(sortie == attendu, "{v} diffère de {}", ref_.display());
        }
        let m = srv.meta(&format!("{pref}/{v}/12.pmtiles"));
        assert_eq!(m["cache-control"], "public, max-age=31536000, immutable");
        assert_eq!(m["content-type"], "application/vnd.pmtiles");
    }
    let m = srv.meta(&format!("{pref}/_etat/12.json"));
    assert_eq!(m["cache-control"], "no-cache");
    let etat: Value = serde_json::from_slice(&std::fs::read(srv.objet(&format!("{pref}/_etat/12.json"))).unwrap()).unwrap();
    assert_eq!(etat["statut"], "complete");
    assert_eq!(etat["valide"], "2026-10-17T15:00:00Z");
    assert_eq!(etat["variables"].as_object().unwrap().len(), 8);
    assert_eq!(etat["variables"]["t2m"]["chemin"], "arome-hd/2026-10-17T03Z/t2m/12.pmtiles");
    assert!(etat["isobares"].is_null(), "pas de pression en 0,01°");
    // échéance 12 ≠ 3 : pas de masque de domaine
    assert!(etat["domaine"].is_null());
    // 2 fichiers (SP1, SP2) de l'échéance 12 + 2 de l'échéance 11 (HD : un fichier par heure)
    assert_eq!(srv.requetes_grib().len(), 4);

    // le marqueur d'état est écrit en dernier
    let ordre: Vec<String> = srv.journal().iter().filter(|e| e["methode"] == "PUT").map(|e| e["cle"].as_str().unwrap().to_string()).collect();
    assert_eq!(ordre.last().unwrap(), &format!("{pref}/_etat/12.json"));
    assert_eq!(ordre.len(), 9);

    // rejeu : rien n'est retéléchargé ni réécrit
    let avant = srv.journal().len();
    let r = traiter_corps(&ctx, &c).unwrap();
    assert_eq!(r.statut, "deja_fait");
    let nouveau = &srv.journal()[avant..];
    assert!(nouveau.iter().all(|e| e["methode"] != "PUT" && !e["chemin"].as_str().unwrap().starts_with("/pnt/")), "{nouveau:?}");

    // un fichier manquant (ou de mauvaise taille) invalide l'état : l'échéance est refaite
    std::fs::remove_file(srv.objet(&format!("{pref}/wind10/12.pmtiles"))).unwrap();
    let r = traiter_corps(&ctx, &c).unwrap();
    assert_eq!(r.statut, "traite");
    assert!(srv.objet(&format!("{pref}/wind10/12.pmtiles")).exists());
    // `forcer` refait même si tout est présent
    let mut ctx2 = srv.ctx_s3();
    ctx2.cfg.forcer = true;
    assert_eq!(traiter_corps(&ctx2, &c).unwrap().statut, "traite");
}

#[test]
fn arome_025_groupes_et_source_des_isobares() {
    prerequis!();
    let srv = Serveur::lancer("arome", &racine().join("out/rust/src"), &[]);
    let ctx = srv.ctx_s3();
    // échéance 12 : n et n−1 (11) dans le même groupe 07H12H : 2 fichiers seulement
    let r = traiter_corps(&ctx, &corps("arome", "2026-10-17T06Z", 12)).unwrap();
    assert_eq!(r.statut, "traite");
    let noms: Vec<String> = srv.requetes_grib().iter().map(|e| e["chemin"].as_str().unwrap().to_string()).collect();
    assert_eq!(noms.len(), 2, "{noms:?}");
    assert!(noms.iter().all(|n| n.contains("07H12H")));
    let pref = "v1/arome/2026-10-17T06Z";
    for v in VARIABLES_HD {
        let ref_ = racine().join(format!("out/rust/rs/tiles-pur/arome/2026-10-17T06Z/{v}/12.pmtiles"));
        if let Ok(attendu) = std::fs::read(&ref_) {
            assert!(std::fs::read(srv.objet(&format!("{pref}/{v}/12.pmtiles"))).unwrap() == attendu, "{v}");
        }
    }
    // source des isobares : npz (lat, lon, mslp en Pa, meta), seulement en 0,025°
    let iso = srv.objet(&format!("{pref}/_isobares/12.npz"));
    assert!(iso.exists());
    assert!(srv.objet(&format!("{pref}/mslp")).metadata().is_err(), "mslp n'est pas tuilée en raster");
    assert_eq!(srv.meta(&format!("{pref}/_isobares/12.npz"))["cache-control"], "public, max-age=31536000, immutable");
    let z = std::fs::read(&iso).unwrap();
    for entree in ["lat.npy", "lon.npy", "mslp.npy", "meta.npy"] {
        let nom = entree.as_bytes();
        assert!(z.windows(nom.len()).any(|w| w == nom), "{entree} absent du npz");
    }
    // isobares finales : GeoJSON du viewer, <préfixe>/isobares/<modèle>/<tirage>/NN.geojson
    let gj = srv.objet("v1/isobares/arome/2026-10-17T06Z/12.geojson");
    let octets = std::fs::read(&gj).expect("GeoJSON des isobares absent");
    let v: serde_json::Value = serde_json::from_slice(&octets).unwrap();
    assert_eq!(v["type"], "FeatureCollection");
    assert_eq!(v["proprietes"]["echeance"], 12);
    assert_eq!(v["proprietes"]["tirage"], "2026-10-17T06:00:00Z");
    assert!(v["features"].as_array().unwrap().iter().any(|f| f["properties"]["type"] == "isobare"));
    assert_eq!(srv.meta("v1/isobares/arome/2026-10-17T06Z/12.geojson")["cache-control"], "public, max-age=31536000, immutable");
    assert_eq!(r.etat["isobares_geojson"]["cle"], "v1/isobares/arome/2026-10-17T06Z/12.geojson");
    assert_eq!(r.etat["isobares_geojson"]["octets"], octets.len());
    // identique à la sortie du Python de référence quand elle est disponible (out/isobars, 52 GeoJSON)
    if let Ok(py) = std::fs::read(racine().join("out/isobars/arome/2026-10-17T06Z/12.geojson")) {
        assert!(py == octets, "GeoJSON différent du Python de référence");
    }
}

#[test]
fn arome_025_echeance_en_debut_de_groupe_demande_le_groupe_precedent() {
    prerequis!();
    let srv = Serveur::lancer("arome7", &racine().join("out/rust/src"), &[]);
    // échéance 7 = premier instant du groupe 07H12H ; n−1 = 6 est dans 00H06H
    let r = traiter_corps(&srv.ctx_s3(), &corps("arome", "2026-10-17T06:00:00Z", 7)).unwrap();
    assert_eq!(r.statut, "traite");
    let mut noms: Vec<String> = srv.requetes_grib().iter().map(|e| e["chemin"].as_str().unwrap().rsplit('/').next().unwrap().to_string()).collect();
    noms.sort();
    assert_eq!(noms.len(), 4);
    assert_eq!(noms.iter().filter(|n| n.contains("00H06H")).count(), 2, "{noms:?}");
    assert_eq!(noms.iter().filter(|n| n.contains("07H12H")).count(), 2, "{noms:?}");
    // débit de précipitation présent grâce au cumul de l'échéance 6
    assert!(r.etat["variables"]["precip_rate"].is_object());
}

#[test]
fn echeance_1_et_0_ne_demandent_pas_n_moins_1() {
    prerequis!();
    // GRIB disponibles seulement pour 0, 11, 12, … : l'échéance 0 HD n'a besoin que de 00H
    let srv = Serveur::lancer("hd0", &racine().join("out/rust/src"), &[]);
    let r = traiter_corps(&srv.ctx_s3(), &corps("arome-hd", "2026-10-17T03Z", 0)).unwrap();
    assert_eq!(r.statut, "traite");
    assert_eq!(srv.requetes_grib().len(), 2);
    assert!(r.etat["variables"]["gust10"].is_null(), "pas de rafale à l'échéance 0");
    assert_eq!(r.etat["variables"].as_object().unwrap().len(), 4); // t2m, wind10, rh2m, cape
}

#[test]
fn source_absente_404_est_transitoire_et_rejouee() {
    prerequis!();
    let srv = Serveur::lancer("404", &racine().join("out/rust/src"), &[]);
    let ctx = srv.ctx_s3();
    // l'échéance 13 (groupe 13H18H) n'existe pas dans le dossier de sources
    let c = corps("arome", "2026-10-17T06:00:00Z", 13);
    match traiter_corps(&ctx, &c) {
        Err(Erreur::Transitoire(e)) => assert!(format!("{e:#}").contains("404"), "{e:#}"),
        autre => panic!("attendu transitoire, obtenu {autre:?}"),
    }
    // l'événement SQS complet : le message en échec est rendu, l'autre est acquitté
    let rep = traiter_lot(&ctx, evenement(&[("a", c.clone()), ("b", corps("arome-hd", "2026-10-17T03:00:00Z", 0))]));
    let ids: Vec<&str> = rep.batch_item_failures.iter().map(|f| f.item_identifier.as_str()).collect();
    assert_eq!(ids, ["a"]);
    assert_eq!(serde_json::to_value(&rep).unwrap(), json!({"batchItemFailures": [{"itemIdentifier": "a"}]}));
    // rien n'a été publié pour l'échéance manquante : pas d'état, donc pas de « complete »
    assert!(!srv.objet("v1/arome/2026-10-17T06Z/_etat/13.json").exists());
    assert!(srv.objet("v1/arome-hd/2026-10-17T03Z/_etat/00.json").exists());
}

#[test]
fn reprise_apres_troncature_par_range() {
    prerequis!();
    let srv = Serveur::lancer("reprise", &racine().join("out/rust/src"), &["--tronquer-premiere-requete"]);
    let tmp = std::env::temp_dir().join(format!("rw-lambda-it-reprise-sortie-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&tmp);
    let ctx = Contexte { source: Box::new(srv.source_http()), sortie: Box::new(StockageLocal::new(&tmp)), cfg: Config::default() };
    let r = traiter_corps(&ctx, &corps("arome-hd", "2026-10-17T03:00:00Z", 12)).unwrap();
    assert_eq!(r.statut, "traite");
    let g = srv.requetes_grib();
    assert!(g.iter().any(|e| e["coupe"] == true), "aucune coupure simulée");
    assert!(g.iter().filter(|e| e["statut"] == 206 && e["range"].as_str().is_some_and(|r| r.starts_with("bytes="))).count() >= 4, "{g:?}");
    // résultat identique octet pour octet malgré les coupures
    let ref_ = racine().join("out/rust/rs/tiles-pur/arome-hd/2026-10-17T03Z/t2m/12.pmtiles");
    if let Ok(attendu) = std::fs::read(ref_) {
        assert!(std::fs::read(tmp.join("v1/arome-hd/2026-10-17T03Z/t2m/12.pmtiles")).unwrap() == attendu);
    }
    let _ = std::fs::remove_dir_all(&tmp);
}

#[test]
fn erreurs_s3_retentees_puis_signature_fausse() {
    prerequis!();
    let srv = Serveur::lancer("s3err", &racine().join("out/rust/src"), &["--s3-echecs", "3"]);
    // 3 réponses 503 (SlowDown) sont absorbées par les nouvelles tentatives
    let s = srv.s3(SECRET);
    s.ecrire("v1/test/a.json", b"{}", &rw_lambda::stockage::ETAT).unwrap();
    assert_eq!(s.taille("v1/test/a.json").unwrap(), Some(2));
    assert_eq!(s.lire("v1/test/a.json").unwrap().unwrap(), b"{}");
    assert_eq!(s.lire("v1/test/absent.json").unwrap(), None);
    assert_eq!(s.taille("v1/test/absent.json").unwrap(), None);
    assert_eq!(srv.journal().iter().filter(|e| e["statut"] == 503).count(), 3);
    // mauvais secret → 403, jamais « réussi » ; côté traitement c'est transitoire (configuration à corriger, message conservé)
    let mauvais = srv.s3("autre-secret");
    assert!(mauvais.ecrire("v1/test/b.json", b"{}", &rw_lambda::stockage::ETAT).is_err());
    assert!(!srv.objet("v1/test/b.json").exists());
    let ctx = Contexte { source: Box::new(srv.source_http()), sortie: Box::new(mauvais), cfg: Config::default() };
    assert!(matches!(traiter_corps(&ctx, &corps("arome-hd", "2026-10-17T03Z", 0)), Err(Erreur::Transitoire(_))));
}

#[test]
fn erreurs_definitives() {
    prerequis!();
    let srv = Serveur::lancer("def", &racine().join("out/rust/src"), &[]);
    let ctx = srv.ctx_s3();
    for mauvais in ["pas du json", r#"{"modele":"gfs","tirage":"2026-10-17T03Z","echeance":1}"#, r#"{"modele":"arome","tirage":"2026-10-17T03Z","echeance":99}"#] {
        assert!(matches!(traiter_corps(&ctx, mauvais), Err(Erreur::Definitive(_))), "{mauvais}");
    }
    // acquitté par défaut (pas rejoué), rejoué si RW_DEFINITIF=rejouer
    assert!(traiter_lot(&ctx, evenement(&[("x", "pas du json".into())])).batch_item_failures.is_empty());
    let mut ctx_r = srv.ctx_s3();
    ctx_r.cfg.definitif_rejoue = true;
    assert_eq!(traiter_lot(&ctx_r, evenement(&[("x", "pas du json".into())])).batch_item_failures.len(), 1);

    // GRIB corrompu (taille cohérente avec l'annonce) → définitif, état « echec_definitif » consigné, pas de « complete »
    let faux = std::env::temp_dir().join(format!("rw-lambda-it-faux-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&faux);
    std::fs::create_dir_all(&faux).unwrap();
    for l in ["12H", "11H"] {
        for p in ["SP1", "SP2"] {
            std::fs::write(faux.join(format!("arome__001__{p}__{l}__2026-10-17T03:00:00Z.grib2")), vec![0x42u8; 5000]).unwrap();
        }
    }
    let srv2 = Serveur::lancer("def2", &faux, &[]);
    let ctx2 = srv2.ctx_s3();
    let c = corps("arome-hd", "2026-10-17T03Z", 12);
    let rep = traiter_lot(&ctx2, evenement(&[("g", c.clone())]));
    assert!(rep.batch_item_failures.is_empty());
    let etat: Value = serde_json::from_slice(&std::fs::read(srv2.objet("v1/arome-hd/2026-10-17T03Z/_etat/12.json")).unwrap()).unwrap();
    assert_eq!(etat["statut"], "echec_definitif");
    assert!(etat["erreur"].as_str().unwrap().len() > 5);
    assert!(matches!(traiter_corps(&ctx2, &c), Err(Erreur::Definitive(_))), "un état d'échec n'est pas un « complete »");
    let _ = std::fs::remove_dir_all(&faux);
}

#[test]
fn masque_du_domaine_a_l_echeance_3() {
    prerequis!();
    let srv = Serveur::lancer("dom", &racine().join("out/rust/src"), &[]);
    // l'échéance 3 n'est pas dans out/rust/src (0, 11, 12, …) : on force la référence du domaine sur l'échéance 12
    let mut ctx = srv.ctx_s3();
    ctx.cfg.echeance_domaine = Some(12);
    let r = traiter_corps(&ctx, &corps("arome-hd", "2026-10-17T03Z", 12)).unwrap();
    assert!(r.etat["domaine"]["cle"].is_string());
    assert!(srv.objet("v1/arome-hd/2026-10-17T03Z/_domaine/12.npz").exists());
}
