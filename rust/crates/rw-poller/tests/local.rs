// SPDX-License-Identifier: EUPL-1.2
//! Tests d'intégration locaux : source Météo-France, S3 et SQS simulés par `rust/tools/serveur_aws_local.py` (aucun accès
//! réseau externe, aucun compte AWS). Ignorés (message affiché) si `python3` est absent.

use rw_aws::liste::ListeurPublic;
use rw_aws::s3::{ConfigS3, Identifiants, StockageS3};
use rw_aws::sqs::{ClientSqs, ConfigSqs};
use rw_aws::stockage::{Meta, Stockage};
use rw_aws::temps;
use rw_poller::config::Config;
use rw_poller::noms;
use rw_poller::poller::{passer, Contexte};
use serde_json::{json, Value};
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, SystemTime};

const SECRET: &str = "secret-factice-pour-les-tests";
const CLE: &str = "AKIDEXEMPLEFACTICE";
const NOW: &str = "2026-10-17T15:10:00Z"; // horloge fixée des scénarios : le tirage 12Z a 3,2 h
const T12: &str = "2026-10-17T12:00:00Z";
const T00: &str = "2026-10-17T00:00:00Z";

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

/// Serveur Python local ; arrêté par son identifiant de processus à la destruction (jamais par nom).
struct Serveur {
    enfant: Child,
    port: u16,
    dossier: PathBuf,
}

impl Serveur {
    fn lancer(nom: &str, extra: &[&str]) -> Self {
        let dossier = std::env::temp_dir().join(format!("rw-poller-it-{nom}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dossier);
        std::fs::create_dir_all(dossier.join("ovh")).unwrap();
        let mut enfant = Command::new("python3")
            .arg("-I")
            .arg(racine().join("rust/tools/serveur_aws_local.py"))
            .args(["--ovh", dossier.join("ovh").to_str().unwrap(), "--s3", dossier.join("s3").to_str().unwrap()])
            .args(["--sqs", dossier.join("sqs.jsonl").to_str().unwrap(), "--journal", dossier.join("journal.jsonl").to_str().unwrap()])
            .args(extra)
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

    fn donnees(&self, secret: &str) -> StockageS3 {
        let mut c = ConfigS3::aws("bucket-test", "eu-north-1");
        c.endpoint = Some(self.url());
        c.pause_base = Duration::from_millis(5);
        StockageS3::new(c, Identifiants { cle_acces: CLE.into(), secret: secret.into(), jeton: Some("jeton-factice".into()) }).unwrap()
    }

    fn sqs(&self, secret: &str, file: &str, essais: u32) -> ClientSqs {
        let mut c = ConfigSqs::aws("eu-north-1", file);
        c.endpoint = Some(self.url());
        c.essais = essais;
        c.pause_base = Duration::from_millis(5);
        ClientSqs::new(c, Identifiants { cle_acces: CLE.into(), secret: secret.into(), jeton: Some("jeton-factice".into()) }).unwrap()
    }

    fn contexte(&self, cfg: Config) -> Contexte {
        self.contexte_sqs(cfg, self.sqs(SECRET, "https://sqs.eu-north-1.amazonaws.com/000000000000/rw-jobs", 3))
    }

    fn contexte_sqs(&self, mut cfg: Config, sqs: ClientSqs) -> Contexte {
        cfg.source_liste = self.url();
        Contexte {
            source: Box::new(ListeurPublic::new(self.url()).avec_essais(2, Duration::from_millis(5))),
            donnees: Box::new(self.donnees(SECRET)),
            envoi: Some(Box::new(sqs)),
            cfg,
        }
    }

    /// Publie `heures` (paquets donnés) d'un tirage HD chez le faux OVH, modifiés il y a `age_s` secondes avant `maintenant`.
    fn publier_hd(&self, tirage: &str, heures: std::ops::RangeInclusive<u32>, paquets: &[&str], maintenant: i64, age_s: i64) {
        for h in heures {
            for p in paquets {
                self.fichier_ovh("arome-hd", tirage, p, &format!("{h:02}H"), 1000 + u64::from(h), maintenant - age_s);
            }
        }
    }

    fn fichier_ovh(&self, modele: &str, tirage: &str, paquet: &str, etiquette: &str, taille: u64, mtime: i64) {
        let d = self.dossier.join("ovh").join(noms::prefixe_liste("pnt", modele, tirage, paquet));
        std::fs::create_dir_all(&d).unwrap();
        let f = std::fs::File::create(d.join(noms::nom_fichier(modele, paquet, etiquette, tirage))).unwrap();
        f.set_len(taille).unwrap();
        f.set_modified(SystemTime::UNIX_EPOCH + Duration::from_secs(mtime as u64)).unwrap();
    }

    fn objet(&self, cle: &str) -> PathBuf {
        self.dossier.join("s3/bucket-test").join(cle)
    }

    fn messages(&self) -> Vec<Value> {
        std::fs::read_to_string(self.dossier.join("sqs.jsonl")).unwrap_or_default().lines().map(|l| serde_json::from_str(l).unwrap()).collect()
    }

    fn journal(&self) -> Vec<Value> {
        std::fs::read_to_string(self.dossier.join("journal.jsonl")).unwrap_or_default().lines().map(|l| serde_json::from_str(l).unwrap()).collect()
    }

    fn requetes(&self, service: &str, methode: &str) -> usize {
        self.journal().iter().filter(|e| e["service"] == service && e["methode"] == methode).count()
    }
}

impl Drop for Serveur {
    fn drop(&mut self) {
        let _ = self.enfant.kill();
        let _ = self.enfant.wait();
        let _ = std::fs::remove_dir_all(&self.dossier);
    }
}

fn now() -> i64 {
    temps::depuis_iso(NOW).unwrap()
}

fn cfg_hd() -> Config {
    Config { modeles: vec!["arome-hd".into()], tirages_max: 1, ..Config::default() }
}

fn echeances(msgs: &[Value]) -> Vec<u64> {
    let mut v: Vec<u64> = msgs.iter().map(|m| m["corps"]["echeance"].as_u64().unwrap()).collect();
    v.sort_unstable();
    v
}

fn etat(srv: &Serveur, modele: &str, tirage_court: &str, n: u32, contenu: Value, taille_mini: usize) {
    let mut s = contenu.to_string();
    if s.len() < taille_mini {
        s.truncate(s.len() - 1);
        s.push_str(&format!(",\"remplissage\":\"{}\"}}", "x".repeat(taille_mini - s.len())));
    }
    srv.donnees(SECRET).ecrire(&format!("v1/{modele}/{tirage_court}/_etat/{n:02}.json"), s.as_bytes(), &Meta { type_contenu: "application/json", cache: "no-cache" }).unwrap();
}

#[test]
fn envoi_nominal_deduplication_et_etats() {
    prerequis!();
    let srv = Serveur::lancer("nominal", &[]);
    srv.publier_hd(T12, 0..=5, &["SP1", "SP2"], now(), 3600);
    let ctx = srv.contexte(cfg_hd());

    // passage 1 : six échéances prêtes, un seul lot (≤ 10) ; message conforme au contrat du worker
    let b = passer(&ctx, now(), false).unwrap();
    assert_eq!(b["envoyes"], 6);
    let m = srv.messages();
    assert_eq!(echeances(&m), vec![0, 1, 2, 3, 4, 5]);
    assert_eq!(m[0]["corps"], json!({"modele": "arome-hd", "tirage": T12, "echeance": 0}));
    assert_eq!(m[0]["jeton"], "jeton-factice");
    assert_eq!(srv.requetes("sqs", "POST"), 1);
    let marq: Value = serde_json::from_slice(&std::fs::read(srv.objet("v1/_poller/arome-hd/2026-10-17T12Z.json")).unwrap()).unwrap();
    assert_eq!(marq["envoyes"].as_object().unwrap().len(), 6);
    // le marqueur est en `no-cache`
    assert_eq!(b["tirages"][0]["envoyees"], "0-5");
    let puts = srv.requetes("s3", "PUT");

    // passage 2, une minute plus tard : déduplication par fenêtre, aucun message, aucune écriture
    let b = passer(&ctx, now() + 60, false).unwrap();
    assert_eq!(b["envoyes"], 0);
    assert_eq!(b["tirages"][0]["en_file"], "0-5");
    assert_eq!(srv.messages().len(), 6);
    assert_eq!(srv.requetes("s3", "PUT"), puts, "marqueur inchangé : pas de PUT");
    assert_eq!(srv.requetes("sqs", "POST"), 1);

    // les états du worker arrivent pour 0 à 2 ; passage après la fenêtre (15 min) : seul le reste est renvoyé (niveau)
    for n in 0..=2 {
        etat(&srv, "arome-hd", "2026-10-17T12Z", n, json!({"schema": 1, "statut": "complete", "echeance": n}), 1500);
    }
    let b = passer(&ctx, now() + 1000, false).unwrap();
    assert_eq!(b["tirages"][0]["faites"], "0-2");
    assert_eq!(echeances(&srv.messages()[6..]), vec![3, 4, 5]);

    // tout est fait : plus rien à envoyer, le marqueur est vidé de ses envois
    for n in 3..=5 {
        etat(&srv, "arome-hd", "2026-10-17T12Z", n, json!({"schema": 1, "statut": "complete", "echeance": n}), 1500);
    }
    let b = passer(&ctx, now() + 3000, false).unwrap();
    assert_eq!(b["envoyes"], 0);
    assert_eq!(b["tirages"][0]["faites"], "0-5");
    assert_eq!(srv.messages().len(), 9);
}

#[test]
fn tirage_termine_n_est_plus_examine() {
    prerequis!();
    let srv = Serveur::lancer("termine", &[]);
    srv.publier_hd(T12, 0..=51, &["SP1", "SP2"], now(), 3600);
    let ctx = srv.contexte(cfg_hd());
    passer(&ctx, now(), false).unwrap();
    assert_eq!(srv.messages().len(), 52);
    for n in 0..=51 {
        etat(&srv, "arome-hd", "2026-10-17T12Z", n, json!({"schema": 1, "statut": "complete", "echeance": n}), 1500);
    }
    let b = passer(&ctx, now() + 2000, false).unwrap();
    assert_eq!(b["tirages"][0]["faites"], "0-51");
    let marq: Value = serde_json::from_slice(&std::fs::read(srv.objet("v1/_poller/arome-hd/2026-10-17T12Z.json")).unwrap()).unwrap();
    assert_eq!(marq["termine"], true);
    // passages suivants : une seule requête (lecture du marqueur), ni listage OVH, ni listage S3, ni message
    let avant = srv.journal().len();
    let b = passer(&ctx, now() + 2300, false).unwrap();
    assert_eq!(b["tirages"][0]["termine"], true);
    let nouvelles: Vec<Value> = srv.journal().into_iter().skip(avant).collect();
    assert_eq!(nouvelles.len(), 1, "{nouvelles:?}");
    assert_eq!((nouvelles[0]["service"].clone(), nouvelles[0]["methode"].clone()), (json!("s3"), json!("GET")));
    assert_eq!(srv.messages().len(), 52);
}

#[test]
fn publication_incomplete_sp1_hd_manquant() {
    prerequis!();
    // cas du 17/10/2026 : SP1 HD limité à 0-18 h (tirage 06 h) ; SP2 complet
    let srv = Serveur::lancer("sp1", &[]);
    srv.publier_hd(T12, 0..=18, &["SP1"], now(), 3600);
    srv.publier_hd(T12, 0..=51, &["SP2"], now(), 3600);
    let ctx = srv.contexte(cfg_hd());
    let b = passer(&ctx, now(), false).unwrap();
    let tir = &b["tirages"][0];
    assert_eq!(tir["envoyees"], "0-18");
    assert_eq!(tir["paquet_absent"]["SP1"], "19-51");
    assert_eq!(tir["fichiers"]["SP1"], json!([19, 52]));
    assert_eq!(tir["fichiers"]["SP2"], json!([52, 52]));
    assert_eq!(srv.messages().len(), 19);
    // SP1 est publié plus tard : le passage suivant (hors fenêtre) envoie la suite, sans renvoyer 0-18 (dans la fenêtre)
    srv.publier_hd(T12, 19..=51, &["SP1"], now() + 600, 300);
    let b = passer(&ctx, now() + 600, false).unwrap();
    assert_eq!(b["tirages"][0]["en_file"], "0-18");
    assert_eq!(b["tirages"][0]["instables"], "19-51", "fichiers neufs : taille à confirmer au passage suivant");
    let b = passer(&ctx, now() + 660, false).unwrap();
    assert_eq!(b["tirages"][0]["envoyees"], "19-51", "{b}");
    assert_eq!(srv.messages().len(), 52);
}

#[test]
fn fichiers_recents_attendent_une_taille_stable() {
    prerequis!();
    let srv = Serveur::lancer("stable", &[]);
    srv.publier_hd(T12, 0..=1, &["SP1", "SP2"], now(), 300); // 5 minutes : plus de 2 min, moins de 10 min
    let ctx = srv.contexte(cfg_hd());
    let b = passer(&ctx, now(), false).unwrap();
    assert_eq!((b["envoyes"].as_u64().unwrap(), b["tirages"][0]["instables"].as_str().unwrap()), (0, "0-1"));
    // taille inchangée au passage suivant : envoyé
    let b = passer(&ctx, now() + 60, false).unwrap();
    assert_eq!(b["envoyes"], 2);
    // un fichier qui grossit entre deux passages n'est pas envoyé
    srv.publier_hd(T00, 0..=0, &["SP1", "SP2"], now() + 60, 400);
    let mut cfg = cfg_hd();
    cfg.tirages_max = 2;
    cfg.heures = vec![0, 12];
    let ctx2 = srv.contexte(cfg);
    let b = passer(&ctx2, now() + 60, false).unwrap();
    assert_eq!(b["tirages"].as_array().unwrap().len(), 2);
    srv.fichier_ovh("arome-hd", T00, "SP1", "00H", 5000, now() + 60 - 400); // a changé de taille
    let b = passer(&ctx2, now() + 120, false).unwrap();
    let t00 = b["tirages"].as_array().unwrap().iter().find(|x| x["tirage"] == "2026-10-17T00Z").unwrap();
    assert_eq!(t00["instables"], "0", "{b}");
}

#[test]
fn lots_de_dix_refus_partiel_et_reprise() {
    prerequis!();
    let srv = Serveur::lancer("lots", &["--sqs-refuser", "e07"]);
    srv.publier_hd(T12, 0..=51, &["SP1", "SP2"], now(), 3600);
    let ctx = srv.contexte(cfg_hd());
    let b = passer(&ctx, now(), false).unwrap();
    assert_eq!(b["envoyes"], 51, "e07 refusé");
    assert_eq!(b["tirages"][0]["refuses"][0]["id"], "e07");
    // 52 échéances : 5 lots de 10 + 1 lot de 2
    let tailles: Vec<u64> = srv.journal().iter().filter(|e| e["service"] == "sqs").map(|e| e["entrees"].as_u64().unwrap()).collect();
    assert_eq!(tailles, vec![10, 10, 10, 10, 10, 2]);
    assert!(!echeances(&srv.messages()).contains(&7));
    // le passage suivant ne renvoie que l'échéance refusée (les autres sont dans la fenêtre) ; refusée de nouveau
    let b = passer(&ctx, now() + 30, false).unwrap();
    assert_eq!(b["tirages"][0]["a_envoyer"], "7");
    assert_eq!(b["tirages"][0]["refuses"][0]["id"], "e07");
    assert_eq!(srv.messages().len(), 51);
}

#[test]
fn plafond_de_tirages_et_heures_autorisees() {
    prerequis!();
    let srv = Serveur::lancer("plafond", &[]);
    // 15 h, 12 h, 9 h et 00 h : seuls 00 et 12 sont autorisés ; plafond d'un tirage à la fois
    for tirage in ["2026-10-17T00:00:00Z", T12, "2026-10-17T09:00:00Z", "2026-10-16T12:00:00Z"] {
        srv.publier_hd(tirage, 0..=0, &["SP1", "SP2"], now(), 7200);
    }
    let mut cfg = cfg_hd();
    cfg.tirages_max = 1;
    let b = passer(&srv.contexte(cfg), now(), false).unwrap();
    assert_eq!(b["tirages"].as_array().unwrap().len(), 1);
    assert_eq!(b["tirages"][0]["tirage"], "2026-10-17T12Z");
    // deux tirages : 12Z et 00Z, pas 09Z ; le 16/10 12Z a 27 h (< 36 h) mais dépasse le plafond
    let mut cfg = cfg_hd();
    cfg.tirages_max = 2;
    let b = passer(&srv.contexte(cfg), now(), false).unwrap();
    let ids: Vec<&str> = b["tirages"].as_array().unwrap().iter().map(|x| x["tirage"].as_str().unwrap()).collect();
    assert_eq!(ids, vec!["2026-10-17T12Z", "2026-10-17T00Z"]);
    // rétention : au-delà de l'âge maximal, plus rien (même s'il reste des fichiers chez le faux OVH)
    let b = passer(&srv.contexte(cfg_hd()), now() + 100 * 3600, false).unwrap();
    assert_eq!(b["envoyes"], 0);
    assert!(b["tirages"].as_array().unwrap().iter().all(|x| x["age_h"].as_f64().unwrap() <= 36.0), "{b}");
}

#[test]
fn pagination_du_listage_source() {
    prerequis!();
    let srv = Serveur::lancer("pages", &["--ovh-pages", "7"]);
    srv.publier_hd(T12, 0..=51, &["SP1", "SP2"], now(), 3600);
    let b = passer(&srv.contexte(cfg_hd()), now(), false).unwrap();
    assert_eq!(b["envoyes"], 52);
    assert_eq!(b["tirages"][0]["fichiers"]["SP1"], json!([52, 52]));
    // 52 fichiers / 7 par page = 8 pages par paquet
    let pages = srv.journal().iter().filter(|e| e["service"] == "ovh").count();
    assert_eq!(pages, 16);
}

#[test]
fn arome_025_groupes() {
    prerequis!();
    let srv = Serveur::lancer("g025", &[]);
    for l in ["00H06H", "07H12H"] {
        for p in ["SP1", "SP2"] {
            srv.fichier_ovh("arome", T12, p, l, 5000, now() - 3600);
        }
    }
    let cfg = Config { modeles: vec!["arome".into()], ..Config::default() };
    let b = passer(&srv.contexte(cfg), now(), false).unwrap();
    assert_eq!(echeances(&srv.messages()), (0..=12).collect::<Vec<u64>>());
    assert_eq!(b["tirages"][0]["incompletes"], "13-51");
}

#[test]
fn echec_d_envoi_puis_reprise() {
    prerequis!();
    // les 2 premiers SendMessageBatch reçoivent un 500 ; 2 essais par lot : le premier passage échoue, le second réussit
    let srv = Serveur::lancer("panne", &["--sqs-echecs", "2"]);
    srv.publier_hd(T12, 0..=3, &["SP1", "SP2"], now(), 3600);
    let ctx = srv.contexte_sqs(cfg_hd(), srv.sqs(SECRET, "https://sqs.eu-north-1.amazonaws.com/0/f", 2));
    let e = passer(&ctx, now(), false).unwrap_err();
    assert!(e.contains("envoi SQS") && e.contains("500"), "{e}");
    assert!(srv.messages().is_empty());
    assert_eq!(srv.requetes("s3", "PUT"), 0, "rien n'a été envoyé : pas de marqueur");
    let b = passer(&ctx, now() + 5, false).unwrap();
    assert_eq!(b["envoyes"], 4);
}

#[test]
fn signature_fausse_et_file_fifo() {
    prerequis!();
    let srv = Serveur::lancer("sig", &[]);
    srv.publier_hd(T12, 0..=1, &["SP1", "SP2"], now(), 3600);
    let ctx = srv.contexte_sqs(cfg_hd(), srv.sqs("mauvais-secret", "https://sqs.eu-north-1.amazonaws.com/0/f", 3));
    let e = passer(&ctx, now(), false).unwrap_err();
    assert!(e.contains("403"), "{e}");
    assert_eq!(srv.requetes("sqs", "POST"), 1, "403 : pas de nouvel essai");
    // file FIFO : groupe et identifiant de déduplication par fenêtre
    let ctx = srv.contexte_sqs(cfg_hd(), srv.sqs(SECRET, "https://sqs.eu-north-1.amazonaws.com/0/rw-jobs.fifo", 3));
    passer(&ctx, now(), false).unwrap();
    let m = srv.messages();
    assert_eq!(m.len(), 2);
    assert_eq!(m[0]["groupe"], "arome-hd-2026-10-17T12Z");
    assert_eq!(m[0]["deduplication"], format!("arome-hd-2026-10-17T12Z-00-{}", now() / 900));
}

#[test]
fn echec_definitif_retente_apres_delai() {
    prerequis!();
    let srv = Serveur::lancer("echec", &[]);
    srv.publier_hd(T12, 0..=1, &["SP1", "SP2"], now(), 3600);
    let ctx = srv.contexte(cfg_hd()); // retry_echec_s = 6 h
    etat(&srv, "arome-hd", "2026-10-17T12Z", 0, json!({"schema": 1, "statut": "echec_definitif", "echeance": 0}), 0);
    let date_etat = |instant: i64| {
        let f = std::fs::OpenOptions::new().write(true).open(srv.objet("v1/arome-hd/2026-10-17T12Z/_etat/00.json")).unwrap();
        f.set_modified(SystemTime::UNIX_EPOCH + Duration::from_secs(instant as u64)).unwrap();
    };
    // échec écrit il y a 10 minutes : l'échéance 0 n'est pas renvoyée, la 1 l'est
    date_etat(now() - 600);
    let b = passer(&ctx, now(), false).unwrap();
    assert_eq!(b["tirages"][0]["en_echec"], "0");
    assert_eq!(echeances(&srv.messages()), vec![1]);
    // échec écrit il y a 7 h (> 6 h) : retenté (la 1 est encore dans la fenêtre de déduplication, 15 min)
    date_etat(now() - 7 * 3600);
    let b = passer(&ctx, now() + 60, false).unwrap();
    assert_eq!(b["tirages"][0]["envoyees"], "0");
    assert_eq!(echeances(&srv.messages()), vec![0, 1]);
}

#[test]
fn simulation_n_envoie_ni_n_ecrit_rien() {
    prerequis!();
    let srv = Serveur::lancer("simulation", &[]);
    srv.publier_hd(T12, 0..=3, &["SP1", "SP2"], now(), 3600);
    let mut ctx = srv.contexte(cfg_hd());
    let b = passer(&ctx, now(), true).unwrap();
    assert_eq!((b["simulation"].clone(), b["envoyes"].clone(), b["tirages"][0]["a_envoyer"].clone()), (json!(true), json!(0), json!("0-3")));
    assert!(srv.messages().is_empty());
    assert_eq!(srv.requetes("s3", "PUT"), 0);
    assert_eq!(srv.requetes("sqs", "POST"), 0);
    // sans file configurée, seul le mode simulation est permis
    ctx.envoi = None;
    assert!(passer(&ctx, now(), true).is_ok());
    assert!(passer(&ctx, now(), false).unwrap_err().contains("RW_SQS_URL"));
}

#[test]
fn binaire_mode_evenement() {
    prerequis!();
    let srv = Serveur::lancer("bin", &[]);
    srv.publier_hd(T12, 0..=2, &["SP1", "SP2"], now(), 3600);
    let evt = srv.dossier.join("evt.json");
    std::fs::write(&evt, json!({"maintenant": NOW}).to_string()).unwrap();
    let lancer = |extra: &[(&str, &str)]| {
        let mut c = Command::new(env!("CARGO_BIN_EXE_rw-poller"));
        c.args(["--evenement", evt.to_str().unwrap()])
            .env_clear()
            .envs([
                ("RW_MODELES", "arome-hd"), ("RW_BUCKET", "bucket-test"), ("AWS_REGION", "eu-north-1"),
                ("AWS_ACCESS_KEY_ID", CLE), ("AWS_SECRET_ACCESS_KEY", SECRET), ("RW_PAUSE_MS", "10"),
                ("RW_SOURCE_LISTE", &srv.url()), ("RW_S3_ENDPOINT", &srv.url()), ("RW_SQS_ENDPOINT", &srv.url()),
                ("RW_SQS_URL", "https://sqs.eu-north-1.amazonaws.com/000000000000/rw-jobs"),
            ])
            .envs(extra.iter().copied());
        c.output().unwrap()
    };
    let s = lancer(&[]);
    assert!(s.status.success(), "{}", String::from_utf8_lossy(&s.stderr));
    let b: Value = serde_json::from_slice(&s.stdout).unwrap();
    assert_eq!(b["envoyes"], 3);
    assert_eq!(echeances(&srv.messages()), vec![0, 1, 2]);
    // configuration invalide : code de sortie non nul, message sur la sortie d'erreur, aucun envoi
    let s = lancer(&[("RW_TIRAGES_HEURES", "25")]);
    assert!(!s.status.success());
    assert!(String::from_utf8_lossy(&s.stderr).contains("RW_TIRAGES_HEURES"));
    assert_eq!(srv.messages().len(), 3);
    // secret faux : erreur S3 403 remontée, code 4
    let s = lancer(&[("AWS_SECRET_ACCESS_KEY", "faux")]);
    assert_eq!(s.status.code(), Some(4), "{}", String::from_utf8_lossy(&s.stderr));
}
