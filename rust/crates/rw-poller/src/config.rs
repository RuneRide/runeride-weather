// SPDX-License-Identifier: EUPL-1.2
//! Configuration du poller par variables d'environnement (voir `docs/poc-rust/poller-manifeste.md` §3).

use crate::noms;

#[derive(Debug, Clone)]
pub struct Config {
    /// Préfixe des clés du bucket de données (`v1`, ou `v1r2` si la révision du pipeline change).
    pub prefixe: String,
    /// Modèles à suivre, dans l'ordre de traitement.
    pub modeles: Vec<String>,
    /// Heures de tirage (UTC) à traiter. Lancement : `00` et `12` (2 tirages par jour).
    pub heures: Vec<u32>,
    /// Plafond du nombre de tirages examinés par modèle à chaque passage (les plus récents).
    pub tirages_max: usize,
    /// Âge maximal (heures après l'heure nominale) d'un tirage traité. À garder **inférieur** à la durée de vie des
    /// objets dans le bucket de données (règle de cycle de vie), sinon un tirage expiré serait retraité en boucle.
    pub age_max_h: i64,
    /// Rétention du S3 de Météo-France (environ 15 jours constatés) : borne dure de l'âge.
    pub retention_ovh_h: i64,
    /// Délai après l'heure nominale avant de commencer à interroger la source (le premier fichier apparaît à +1,8 h).
    pub delai_min_s: i64,
    /// Fenêtre de déduplication : une échéance déjà envoyée depuis moins de ce délai n'est pas renvoyée.
    pub fenetre_s: i64,
    /// Âge minimal (date de modification chez OVH) d'un fichier GRIB pour être jugé complet.
    pub age_fichier_min_s: i64,
    /// Au-delà de cet âge, un fichier est jugé stable sans comparer sa taille à l'observation précédente.
    pub age_fichier_sur_s: i64,
    /// Plafond de messages envoyés par passage.
    pub envoi_max: usize,
    /// Un état `echec_definitif` n'est retenté qu'après ce délai (s) depuis son écriture.
    pub retry_echec_s: i64,
    /// Taille (octets) en dessous de laquelle un état est relu pour distinguer `complete` de `echec_definitif`.
    pub etat_petit_octets: u64,
    /// Âge (heures) à partir duquel un tirage encore incomplet chez Météo-France est signalé en WARN.
    pub alerte_incomplet_h: i64,
    /// Liste (listage public) : racine du bucket Météo-France et préfixe des données.
    pub source_liste: String,
    pub source_prefixe: String,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            prefixe: "v1".into(),
            modeles: vec!["arome".into(), "arome-hd".into()],
            heures: vec![0, 12],
            tirages_max: 2,
            age_max_h: 36,
            retention_ovh_h: 336,
            delai_min_s: 5400,
            fenetre_s: 900,
            age_fichier_min_s: 120,
            age_fichier_sur_s: 600,
            envoi_max: 300,
            retry_echec_s: 6 * 3600,
            etat_petit_octets: 1024,
            alerte_incomplet_h: 7,
            source_liste: "https://meteofrance-pnt.s3.rbx.io.cloud.ovh.net".into(),
            source_prefixe: "pnt".into(),
        }
    }
}

fn entier<T: std::str::FromStr>(nom: &str, v: &str) -> Result<T, String> {
    v.trim().parse().map_err(|_| format!("{nom} : nombre entier attendu, reçu {v:?}"))
}

/// Liste d'heures `00,12` (0 à 23, sans doublon, triées).
pub fn analyser_heures(nom: &str, v: &str) -> Result<Vec<u32>, String> {
    let mut h = Vec::new();
    for p in v.split(',').map(str::trim).filter(|p| !p.is_empty()) {
        let n: u32 = entier(nom, p)?;
        if n > 23 {
            return Err(format!("{nom} : heure hors 0 à 23 : {n}"));
        }
        if !h.contains(&n) {
            h.push(n);
        }
    }
    if h.is_empty() {
        return Err(format!("{nom} : au moins une heure est requise"));
    }
    h.sort_unstable();
    Ok(h)
}

impl Config {
    /// Lit la configuration ; `lire` renvoie la valeur d'une variable (vide = absente).
    pub fn depuis(lire: impl Fn(&str) -> Option<String>) -> Result<Self, String> {
        let var = |n: &str| lire(n).filter(|v| !v.trim().is_empty());
        let mut c = Self::default();
        if let Some(v) = var("RW_PREFIXE") {
            c.prefixe = v.trim_matches('/').to_string();
        }
        if let Some(v) = var("RW_MODELES") {
            let m: Vec<String> = v.split(',').map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect();
            if let Some(x) = m.iter().find(|x| noms::resolution(x).is_none()) {
                return Err(format!("RW_MODELES : modèle inconnu {x:?} (arome, arome-hd)"));
            }
            if m.is_empty() {
                return Err("RW_MODELES : liste vide".into());
            }
            c.modeles = m;
        }
        if let Some(v) = var("RW_TIRAGES_HEURES") {
            c.heures = analyser_heures("RW_TIRAGES_HEURES", &v)?;
        }
        if let Some(v) = var("RW_TIRAGES_MAX") {
            c.tirages_max = entier("RW_TIRAGES_MAX", &v)?;
        }
        if let Some(v) = var("RW_AGE_MAX_H") {
            c.age_max_h = entier("RW_AGE_MAX_H", &v)?;
        }
        if let Some(v) = var("RW_RETENTION_OVH_H") {
            c.retention_ovh_h = entier("RW_RETENTION_OVH_H", &v)?;
        }
        if let Some(v) = var("RW_DELAI_MIN_S") {
            c.delai_min_s = entier("RW_DELAI_MIN_S", &v)?;
        }
        if let Some(v) = var("RW_FENETRE_S") {
            c.fenetre_s = entier("RW_FENETRE_S", &v)?;
        }
        if let Some(v) = var("RW_AGE_FICHIER_MIN_S") {
            c.age_fichier_min_s = entier("RW_AGE_FICHIER_MIN_S", &v)?;
        }
        if let Some(v) = var("RW_AGE_FICHIER_SUR_S") {
            c.age_fichier_sur_s = entier("RW_AGE_FICHIER_SUR_S", &v)?;
        }
        if let Some(v) = var("RW_ENVOI_MAX") {
            c.envoi_max = entier("RW_ENVOI_MAX", &v)?;
        }
        if let Some(v) = var("RW_RETRY_ECHEC_S") {
            c.retry_echec_s = entier("RW_RETRY_ECHEC_S", &v)?;
        }
        if let Some(v) = var("RW_ALERTE_INCOMPLET_H") {
            c.alerte_incomplet_h = entier("RW_ALERTE_INCOMPLET_H", &v)?;
        }
        if let Some(v) = var("RW_SOURCE_LISTE") {
            c.source_liste = v;
        }
        if let Some(v) = var("RW_SOURCE_PREFIXE") {
            c.source_prefixe = v.trim_matches('/').to_string();
        }
        if c.tirages_max == 0 || c.age_max_h <= 0 || c.fenetre_s < 0 || c.age_fichier_min_s < 0 || c.delai_min_s < 0 {
            return Err("RW_TIRAGES_MAX et RW_AGE_MAX_H doivent être > 0, les délais ≥ 0".into());
        }
        Ok(c)
    }

    pub fn depuis_env() -> Result<Self, String> {
        Self::depuis(|n| std::env::var(n).ok())
    }

    /// Âge maximal effectif d'un tirage traité (s) : le plus petit de la consigne et de la rétention de la source.
    pub fn age_max_s(&self) -> i64 {
        3600 * self.age_max_h.min(self.retention_ovh_h)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn avec(paires: &[(&str, &str)]) -> Result<Config, String> {
        let m: HashMap<String, String> = paires.iter().map(|(k, v)| (k.to_string(), v.to_string())).collect();
        Config::depuis(|n| m.get(n).cloned())
    }

    #[test]
    fn defauts_de_lancement() {
        let c = avec(&[]).unwrap();
        assert_eq!(c.heures, vec![0, 12]);
        assert_eq!(c.modeles, ["arome", "arome-hd"]);
        assert_eq!(c.age_max_s(), 36 * 3600);
    }

    #[test]
    fn surcharges_et_refus() {
        let c = avec(&[("RW_TIRAGES_HEURES", "12, 00,12"), ("RW_TIRAGES_MAX", "3"), ("RW_AGE_MAX_H", "1000"), ("RW_MODELES", "arome-hd")]).unwrap();
        assert_eq!((c.heures.clone(), c.tirages_max, c.modeles.clone()), (vec![0, 12], 3, vec!["arome-hd".to_string()]));
        assert_eq!(c.age_max_s(), 336 * 3600, "plafonné par la rétention de la source");
        for mauvais in [("RW_TIRAGES_HEURES", "24"), ("RW_TIRAGES_HEURES", "a"), ("RW_MODELES", "gfs"), ("RW_TIRAGES_MAX", "0"), ("RW_FENETRE_S", "x")] {
            assert!(avec(&[mauvais]).is_err(), "{mauvais:?}");
        }
    }
}
