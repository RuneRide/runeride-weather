// SPDX-License-Identifier: EUPL-1.2
//! Décisions du poller, **sans entrées-sorties** (donc testables à l'unité) : quels tirages examiner, et, pour un
//! tirage, quelles échéances mettre en file.
//!
//! Le poller est « à déclenchement par niveau » : à chaque passage il compare ce qui est publié chez Météo-France, ce
//! qui est déjà produit (états d'échéance du bucket de données) et ce qui a déjà été envoyé récemment (marqueur). Un
//! message perdu, une file purgée ou une panne ne perdent donc rien : le passage suivant renvoie ce qui manque.

use crate::config::Config;
use crate::noms::{self, ECHEANCE_MAX};
use rw_aws::liste::Objet;
use rw_aws::temps;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet, HashMap};

/// Tirages candidats (secondes Unix), du plus récent au plus ancien : heures autorisées, ni trop récents (la source
/// ne publie rien avant ≈ 1,8 h), ni trop vieux (cycle de vie de nos données, rétention de la source), au plus
/// `tirages_max`.
pub fn tirages_candidats(maintenant: i64, cfg: &Config) -> Vec<i64> {
    let plus_recent = (maintenant - cfg.delai_min_s).div_euclid(3600) * 3600;
    let plus_ancien = maintenant - cfg.age_max_s();
    let mut v = Vec::new();
    let mut t = plus_recent;
    while t >= plus_ancien && v.len() < cfg.tirages_max {
        let (.., h, _, _) = temps::decomposer(t);
        if cfg.heures.contains(&(h as u32)) {
            v.push(t);
        }
        t -= 3600;
    }
    v
}

/// Marqueur d'envoi d'un (modèle, tirage), conservé dans le bucket de données (`<préfixe>/_poller/<modèle>/<tirage>.json`).
/// Seul le poller l'écrit (concurrence réservée 1).
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Marqueur {
    #[serde(default)]
    pub schema: u32,
    /// Échéance → instant (s Unix) du dernier envoi : déduplication par fenêtre.
    #[serde(default)]
    pub envoyes: BTreeMap<u32, i64>,
    /// Nom de fichier GRIB → taille observée au passage précédent (fichiers encore « jeunes » seulement) : un
    /// fichier n'est jugé complet que si sa taille n'a pas changé entre deux passages.
    #[serde(default)]
    pub tailles: BTreeMap<String, u64>,
    /// Les 52 échéances ont un état complet : le tirage n'est plus examiné (ni listage de la source, ni listage des
    /// états : une seule lecture de ce marqueur par passage).
    #[serde(default, skip_serializing_if = "est_faux")]
    pub termine: bool,
}

fn est_faux(b: &bool) -> bool {
    !*b
}

pub const SCHEMA_MARQUEUR: u32 = 1;

/// Ce que l'on sait d'un état d'échéance déjà présent dans le bucket de données.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EtatConnu {
    /// `statut = complete` (ou état volumineux, donc complet).
    Complet,
    /// `statut = echec_definitif`, écrit à cet instant (s Unix).
    Echec { ecrit: i64 },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EtatFichier {
    Absent,
    /// Présent mais trop récent, ou de taille différente de l'observation précédente.
    Instable,
    Pret,
}

pub fn etat_fichier(o: Option<&Objet>, marqueur: &Marqueur, maintenant: i64, cfg: &Config) -> EtatFichier {
    let Some(o) = o else { return EtatFichier::Absent };
    let age = maintenant - o.modifie;
    if o.taille == 0 || age < cfg.age_fichier_min_s {
        return EtatFichier::Instable;
    }
    if age >= cfg.age_fichier_sur_s || marqueur.tailles.get(&o.cle_nom()) == Some(&o.taille) {
        EtatFichier::Pret
    } else {
        EtatFichier::Instable
    }
}

trait NomFichier {
    fn cle_nom(&self) -> String;
}
impl NomFichier for Objet {
    fn cle_nom(&self) -> String {
        self.cle.rsplit('/').next().unwrap_or(&self.cle).to_string()
    }
}

/// Entrées d'une évaluation (un modèle, un tirage).
pub struct Entrees<'a> {
    pub modele: &'a str,
    pub tirage_long: &'a str,
    /// Fichiers GRIB listés chez Météo-France, par nom de fichier.
    pub source: &'a HashMap<String, Objet>,
    /// États d'échéance déjà présents dans le bucket de données.
    pub etats: &'a BTreeMap<u32, EtatConnu>,
    pub marqueur: &'a Marqueur,
    pub maintenant: i64,
}

#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize)]
pub struct Decision {
    /// À envoyer maintenant (échéances prêtes, sans état, hors fenêtre de déduplication).
    pub a_envoyer: Vec<u32>,
    /// Déjà produites (état complet).
    pub faites: Vec<u32>,
    /// Prêtes mais envoyées depuis moins d'une fenêtre.
    pub en_file: Vec<u32>,
    /// État `echec_definitif` récent : non renvoyées (retentées après `retry_echec_s`).
    pub en_echec: Vec<u32>,
    /// Tous les fichiers sont présents mais pas encore jugés complets (trop récents ou taille changeante).
    pub instables: Vec<u32>,
    /// Au moins un fichier requis n'est pas (encore) publié.
    pub incompletes: Vec<u32>,
    /// Paquet → échéances dont le fichier de ce paquet est absent. Un paquet absent seul (SP1 HD du 06Z/09Z du
    /// 17/10/2026) apparaît ici alors que l'autre paquet est présent.
    pub paquet_absent: BTreeMap<String, Vec<u32>>,
    /// Fichiers publiés par paquet sur le nombre attendu (`SP1` → (présents, attendus)).
    pub fichiers: BTreeMap<String, (usize, usize)>,
    /// Marqueur à conserver (sans les envois de ce passage, ajoutés par l'appelant après confirmation de SQS).
    pub marqueur: Marqueur,
}

pub fn evaluer(e: &Entrees<'_>, cfg: &Config) -> Decision {
    let mut d = Decision::default();
    let attendues = noms::etiquettes_tirage(e.modele);
    for p in noms::PAQUETS {
        let presents = attendues.iter().filter(|l| e.source.contains_key(&noms::nom_fichier(e.modele, p, l, e.tirage_long))).count();
        d.fichiers.insert(p.to_string(), (presents, attendues.len()));
    }
    for n in 0..=ECHEANCE_MAX {
        match e.etats.get(&n) {
            Some(EtatConnu::Complet) => {
                d.faites.push(n);
                continue;
            }
            Some(EtatConnu::Echec { ecrit }) if e.maintenant - ecrit < cfg.retry_echec_s => {
                d.en_echec.push(n);
                continue;
            }
            _ => {}
        }
        let mut pire = EtatFichier::Pret;
        let mut absent_paquets: BTreeSet<&str> = BTreeSet::new();
        for (nom, paquet, _) in noms::fichiers_requis(e.modele, e.tirage_long, n) {
            match etat_fichier(e.source.get(&nom), e.marqueur, e.maintenant, cfg) {
                EtatFichier::Absent => {
                    pire = EtatFichier::Absent;
                    absent_paquets.insert(paquet);
                }
                EtatFichier::Instable if pire == EtatFichier::Pret => pire = EtatFichier::Instable,
                _ => {}
            }
        }
        for p in absent_paquets {
            d.paquet_absent.entry(p.to_string()).or_default().push(n);
        }
        match pire {
            EtatFichier::Absent => d.incompletes.push(n),
            EtatFichier::Instable => d.instables.push(n),
            EtatFichier::Pret => match e.marqueur.envoyes.get(&n) {
                Some(&t) if e.maintenant - t < cfg.fenetre_s => d.en_file.push(n),
                _ => d.a_envoyer.push(n),
            },
        }
    }
    // marqueur suivant : envois encore dans la fenêtre pour des échéances sans état ; tailles des fichiers jeunes
    d.marqueur.schema = SCHEMA_MARQUEUR;
    d.marqueur.termine = e.marqueur.termine || d.faites.len() == (ECHEANCE_MAX + 1) as usize;
    d.marqueur.envoyes = e
        .marqueur
        .envoyes
        .iter()
        .filter(|(n, t)| e.maintenant - **t < cfg.fenetre_s && !d.faites.contains(n))
        .map(|(n, t)| (*n, *t))
        .collect();
    d.marqueur.tailles = e
        .source
        .iter()
        .filter(|(_, o)| e.maintenant - o.modifie < cfg.age_fichier_sur_s)
        .map(|(nom, o)| (nom.clone(), o.taille))
        .collect();
    d
}

#[cfg(test)]
mod tests {
    use super::*;

    const T: &str = "2026-10-17T06:00:00Z";

    fn t0() -> i64 {
        temps::depuis_iso(T).unwrap()
    }

    fn objet(nom: &str, taille: u64, modifie: i64) -> (String, Objet) {
        (nom.to_string(), Objet { cle: format!("pnt/{T}/x/{nom}"), taille, modifie, etag: String::new() })
    }

    /// Fichiers HD SP1/SP2 pour les heures données, publiés à `modifie`.
    fn hd(heures: std::ops::RangeInclusive<u32>, paquets: &[&str], modifie: i64) -> HashMap<String, Objet> {
        let mut m = HashMap::new();
        for h in heures {
            for p in paquets {
                let nom = noms::nom_fichier("arome-hd", p, &format!("{h:02}H"), T);
                m.extend([objet(&nom, 1000 + u64::from(h), modifie)]);
            }
        }
        m
    }

    fn eval(source: &HashMap<String, Objet>, etats: &BTreeMap<u32, EtatConnu>, marq: &Marqueur, now: i64) -> Decision {
        evaluer(&Entrees { modele: "arome-hd", tirage_long: T, source, etats, marqueur: marq, maintenant: now }, &Config::default())
    }

    #[test]
    fn candidats_heures_plafond_et_age() {
        let c = Config::default(); // 00 et 12, 2 tirages max, 36 h, délai 1,5 h
        let now = temps::depuis_iso("2026-10-17T15:10:00Z").unwrap();
        let v: Vec<String> = tirages_candidats(now, &c).iter().map(|&t| temps::iso(t)).collect();
        assert_eq!(v, ["2026-10-17T12:00:00Z", "2026-10-17T00:00:00Z"]);
        // trop tôt : le 12Z n'est pas interrogé avant 13:30
        let now = temps::depuis_iso("2026-10-17T13:00:00Z").unwrap();
        let v: Vec<String> = tirages_candidats(now, &c).iter().map(|&t| temps::iso(t)).collect();
        assert_eq!(v, ["2026-10-17T00:00:00Z", "2026-10-16T12:00:00Z"]);
        // trop vieux : au-delà de 36 h le tirage n'est plus traité (cycle de vie des données à 2 jours)
        let now = temps::depuis_iso("2026-10-18T20:00:00Z").unwrap();
        let v: Vec<String> = tirages_candidats(now, &c).iter().map(|&t| temps::iso(t)).collect();
        assert_eq!(v, ["2026-10-18T12:00:00Z", "2026-10-18T00:00:00Z"]);
        let c3 = Config { tirages_max: 1, ..Config::default() };
        assert_eq!(tirages_candidats(now, &c3).len(), 1);
        // 8 tirages par jour
        let c8 = Config { heures: (0..24).step_by(3).collect(), tirages_max: 4, ..Config::default() };
        let v: Vec<String> = tirages_candidats(now, &c8).iter().map(|&t| temps::iso(t)).collect();
        assert_eq!(v, ["2026-10-18T18:00:00Z", "2026-10-18T15:00:00Z", "2026-10-18T12:00:00Z", "2026-10-18T09:00:00Z"]);
        // la rétention de la source plafonne même une consigne plus large
        let large = Config { age_max_h: 10_000, retention_ovh_h: 24, tirages_max: 10, ..Config::default() };
        let now = temps::depuis_iso("2026-10-18T20:00:00Z").unwrap();
        assert!(tirages_candidats(now, &large).iter().all(|&t| now - t <= 24 * 3600));
    }

    #[test]
    fn fichiers_recents_attendent_et_taille_stable() {
        let now = t0() + 4 * 3600;
        let marq = Marqueur::default();
        // fichiers publiés il y a 30 s : instables ; il y a 5 min sans observation précédente : instables ; 15 min : prêts
        let jeunes = hd(0..=1, &["SP1", "SP2"], now - 30);
        let d = eval(&jeunes, &BTreeMap::new(), &marq, now);
        assert_eq!(d.a_envoyer, Vec::<u32>::new());
        assert_eq!(d.instables, vec![0, 1]);
        assert_eq!(d.marqueur.tailles.len(), 4, "les tailles sont mémorisées pour le passage suivant");

        let moyens = hd(0..=1, &["SP1", "SP2"], now - 300);
        assert_eq!(eval(&moyens, &BTreeMap::new(), &marq, now).instables, vec![0, 1]);
        // même taille qu'au passage précédent : prêts
        let marq2 = Marqueur { tailles: moyens.iter().map(|(n, o)| (n.clone(), o.taille)).collect(), ..Marqueur::default() };
        assert_eq!(eval(&moyens, &BTreeMap::new(), &marq2, now).a_envoyer, vec![0, 1]);
        // taille changée depuis le passage précédent : toujours instable
        let marq3 = Marqueur { tailles: moyens.iter().map(|(n, o)| (n.clone(), o.taille + 1)).collect(), ..Marqueur::default() };
        assert_eq!(eval(&moyens, &BTreeMap::new(), &marq3, now).a_envoyer, Vec::<u32>::new());

        let anciens = hd(0..=1, &["SP1", "SP2"], now - 900);
        let d = eval(&anciens, &BTreeMap::new(), &marq, now);
        assert_eq!(d.a_envoyer, vec![0, 1]);
        assert!(d.marqueur.tailles.is_empty());
    }

    #[test]
    fn echeance_n_demande_aussi_n_moins_1() {
        let now = t0() + 5 * 3600;
        let mut s = hd(0..=3, &["SP1", "SP2"], now - 3600);
        // retire l'heure 2 : les échéances 2 et 3 en dépendent (n et n-1), pas les autres
        s.retain(|nom, _| !nom.contains("__02H__"));
        let d = eval(&s, &BTreeMap::new(), &Marqueur::default(), now);
        assert_eq!(d.a_envoyer, vec![0, 1]);
        assert_eq!(d.incompletes, [2, 3].into_iter().chain(4..=51).collect::<Vec<u32>>());
    }

    #[test]
    fn sp1_hd_manquant_sp2_present_cas_connu() {
        // 17/10/2026 : tirage HD de 09 h sans aucune échéance SP1, SP2 complet ; tirage de 06 h avec SP1 pour 0 à 18 h
        let now = t0() + 7 * 3600;
        let mut s = hd(0..=18, &["SP1"], now - 3600);
        s.extend(hd(0..=51, &["SP2"], now - 3600));
        let d = eval(&s, &BTreeMap::new(), &Marqueur::default(), now);
        assert_eq!(d.a_envoyer, (0..=18).collect::<Vec<u32>>(), "seules les échéances dont SP1 et SP2 existent sont envoyées");
        assert_eq!(d.paquet_absent["SP1"], (19..=51).collect::<Vec<u32>>());
        assert!(!d.paquet_absent.contains_key("SP2"));
        assert_eq!(d.fichiers["SP1"], (19, 52));
        assert_eq!(d.fichiers["SP2"], (52, 52));
        // quand SP1 est enfin publié, le passage suivant envoie le reste (déclenchement par niveau)
        let mut s2 = s.clone();
        s2.extend(hd(19..=51, &["SP1"], now - 900));
        let mut etats = BTreeMap::new();
        for n in 0..=18 {
            etats.insert(n, EtatConnu::Complet);
        }
        let d2 = eval(&s2, &etats, &Marqueur::default(), now);
        assert_eq!(d2.a_envoyer, (19..=51).collect::<Vec<u32>>());
        assert_eq!(d2.faites.len(), 19);
        assert!(d2.paquet_absent.is_empty());
    }

    #[test]
    fn deduplication_par_fenetre_et_etats() {
        let now = t0() + 4 * 3600;
        let s = hd(0..=5, &["SP1", "SP2"], now - 3600);
        let mut marq = Marqueur::default();
        marq.envoyes.insert(0, now - 100); // envoyé il y a 100 s : dans la fenêtre de 900 s
        marq.envoyes.insert(1, now - 1000); // fenêtre écoulée : renvoyé
        marq.envoyes.insert(2, now - 50); // déjà fait entre-temps : l'état prime
        let mut etats = BTreeMap::new();
        etats.insert(2, EtatConnu::Complet);
        etats.insert(3, EtatConnu::Echec { ecrit: now - 600 }); // échec récent : pas renvoyé
        etats.insert(4, EtatConnu::Echec { ecrit: now - 7 * 3600 }); // échec ancien : retenté
        let d = eval(&s, &etats, &marq, now);
        assert_eq!(d.en_file, vec![0]);
        assert_eq!(d.a_envoyer, vec![1, 4, 5]);
        assert_eq!(d.faites, vec![2]);
        assert_eq!(d.en_echec, vec![3]);
        assert_eq!(d.marqueur.envoyes.keys().copied().collect::<Vec<_>>(), vec![0], "1 (périmé) et 2 (fait) sont oubliés");
        // idempotence : une seconde évaluation sans changement donne la même décision
        assert_eq!(eval(&s, &etats, &marq, now), d);
        assert!(!d.marqueur.termine);
        let tous: BTreeMap<u32, EtatConnu> = (0..=51).map(|n| (n, EtatConnu::Complet)).collect();
        assert!(eval(&s, &tous, &marq, now).marqueur.termine, "52 états complets : tirage terminé");
    }

    #[test]
    fn arome_025_par_groupes() {
        let now = t0() + 5 * 3600;
        let mut s = HashMap::new();
        for l in ["00H06H", "07H12H"] {
            for p in ["SP1", "SP2"] {
                s.extend([objet(&noms::nom_fichier("arome", p, l, T), 5_000_000, now - 3600)]);
            }
        }
        let d = evaluer(&Entrees { modele: "arome", tirage_long: T, source: &s, etats: &BTreeMap::new(), marqueur: &Marqueur::default(), maintenant: now }, &Config::default());
        // les échéances 0 à 12 sont prêtes (7 demande aussi le groupe précédent, présent) ; 13 demande 13H18H absent
        assert_eq!(d.a_envoyer, (0..=12).collect::<Vec<u32>>());
        assert_eq!(d.fichiers["SP1"], (2, 9));
    }

    #[test]
    fn marqueur_serialisation() {
        let mut m = Marqueur { schema: 1, ..Default::default() };
        m.envoyes.insert(3, 100);
        m.tailles.insert("a.grib2".into(), 7);
        let j = serde_json::to_string(&m).unwrap();
        assert_eq!(j, r#"{"schema":1,"envoyes":{"3":100},"tailles":{"a.grib2":7}}"#);
        assert_eq!(serde_json::from_str::<Marqueur>(&j).unwrap(), m);
        assert_eq!(serde_json::from_str::<Marqueur>("{}").unwrap(), Marqueur::default());
        m.termine = true;
        assert!(serde_json::to_string(&m).unwrap().ends_with(r#""termine":true}"#));
    }
}
