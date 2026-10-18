// SPDX-License-Identifier: EUPL-1.2
//! GRIB2 → champs canoniques (port de `pipeline/ingest/{grib,canon,cli}.py`, contrat §2).
//!
//! Le calcul est fait en `f32` / `f64` aux mêmes endroits que le NumPy de référence, pour que les
//! sorties soient identiques bit à bit quand c'est possible.

use anyhow::{anyhow, bail, Context, Result};
use rw_grib::aec::Backend;
use rw_grib::{Champ, Grille};
use rw_tiles::Canon;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::time::Instant;

pub const PAQUETS: [&str; 2] = ["SP1", "SP2"];

/// Résolution dans le nom de fichier.
pub fn resolution(modele: &str) -> Result<&'static str> {
    match modele {
        "arome-hd" => Ok("001"),
        "arome" => Ok("0025"),
        _ => bail!("modèle inconnu : {modele}"),
    }
}

/// Étiquette de groupe d'échéances 0,025° (00H06H, 07H12H, ..., 49H51H).
pub fn groupe_025(step: u32) -> String {
    if step <= 6 {
        return "00H06H".into();
    }
    let debut = 7 + 6 * ((step - 7) / 6);
    let fin = (debut + 5).min(51);
    format!("{debut:02}H{fin:02}H")
}

pub fn etiquette(modele: &str, step: u32) -> String {
    if modele == "arome-hd" {
        format!("{step:02}H")
    } else {
        groupe_025(step)
    }
}

pub fn nom_fichier(modele: &str, paquet: &str, label: &str, run: &str) -> Result<String> {
    Ok(format!("arome__{}__{paquet}__{label}__{run}.grib2", resolution(modele)?))
}

/// Cherche récursivement un fichier de ce nom dans les dossiers.
pub fn localiser(nom: &str, dossiers: &[PathBuf]) -> Option<PathBuf> {
    fn rec(d: &Path, nom: &str) -> Option<PathBuf> {
        for e in std::fs::read_dir(d).ok()?.flatten() {
            let p = e.path();
            if p.is_dir() {
                if let Some(r) = rec(&p, nom) {
                    return Some(r);
                }
            } else if e.file_name() == nom {
                return Some(p);
            }
        }
        None
    }
    dossiers.iter().find_map(|d| rec(d, nom))
}

/// Paramètres GRIB utiles (noms eccodes) d'après (discipline, catégorie, numéro, niveau, statistique).
fn nom_parametre(c: &Champ<'_>) -> Option<&'static str> {
    let niv = c.niveau;
    Some(match (c.discipline, c.categorie, c.numero, c.statistique) {
        (0, 0, 0, None) if niv == 2.0 => "2t",
        (0, 1, 1, None) if niv == 2.0 => "2r",
        (0, 2, 2, None) if niv == 10.0 => "10u",
        (0, 2, 3, None) if niv == 10.0 => "10v",
        (0, 2, 23, Some(2)) if niv == 10.0 => "max_10efg",
        (0, 2, 24, Some(2)) if niv == 10.0 => "max_10nfg",
        (0, 2, 22, Some(2)) if niv == 10.0 => "max_i10fg",
        (0, 3, 1, None) => "prmsl",
        (0, 1, 65, Some(1)) => "tirf",
        (0, 1, 66, Some(1)) => "tsnowp",
        (0, 1, 75, Some(1)) => "tgrp",
        (0, 1, 52, Some(1)) => "tp",
        (0, 6, 3, None) => "lcc",
        (0, 6, 4, None) => "mcc",
        (0, 6, 5, None) => "hcc",
        (0, 7, 6, None) => "CAPE_INS",
        _ => return None,
    })
}

const CUMULS: [&str; 4] = ["tirf", "tsnowp", "tgrp", "tp"];
const MAXIMA: [&str; 3] = ["max_10efg", "max_10nfg", "max_i10fg"];

/// Échéance d'un champ (comme `step` d'eccodes) avec contrôle de la fenêtre temporelle du contrat :
/// cumul 0 → n, maximum n−1 → n.
fn echeance_controlee(nom: &str, c: &Champ<'_>) -> Result<u32> {
    let n = c.fin;
    if CUMULS.contains(&nom) && !(c.statistique == Some(1) && c.debut == 0) {
        bail!("{nom} : fenêtre inattendue {}-{} (échéance {n})", c.debut, c.fin);
    }
    if MAXIMA.contains(&nom) && !(c.statistique == Some(2) && c.debut + 1 == n) {
        bail!("{nom} : fenêtre inattendue {}-{} (échéance {n})", c.debut, c.fin);
    }
    Ok(n)
}

/// Champs d'un fichier : octets lus et grille.
struct Fichier {
    nom: String,
    octets: Vec<u8>,
}

/// Temps passés par étape (secondes).
#[derive(Debug, Default, Clone, Copy)]
pub struct Etapes {
    pub lecture_fichiers_s: f64,
    pub scan_s: f64,
    pub decodage_grib_s: f64,
    pub calcul_canonique_s: f64,
    pub champs_decodes: usize,
}

pub struct Options {
    /// Décode tous les champs utiles des fichiers (comportement du Python), au lieu de la seule échéance demandée.
    pub tout_decoder: bool,
    pub backend: Backend,
}

type Champs = HashMap<&'static str, Vec<f32>>;

/// Variables de sortie, dans l'ordre du Python.
fn construire(modele: &str, step: u32, champs: &Champs, prev: Option<&Champs>) -> Vec<(String, Vec<f32>)> {
    let mut out: Vec<(String, Vec<f32>)> = Vec::new();
    let clip = |v: &Vec<f32>, a: f32, b: f32| -> Vec<f32> { v.iter().map(|&x| if x.is_nan() { x } else { x.max(a).min(b) }).collect() };
    if let (Some(u), Some(v)) = (champs.get("10u"), champs.get("10v")) {
        out.push(("wind10_u".into(), u.clone()));
        out.push(("wind10_v".into(), v.clone()));
    }
    if let Some(t) = champs.get("2t") {
        out.push(("t2m".into(), t.clone()));
    }
    if let Some(r) = champs.get("2r") {
        out.push(("rh2m".into(), clip(r, 0.0, 100.0)));
    }
    if let Some(c) = champs.get("CAPE_INS") {
        out.push(("cape".into(), c.clone()));
    }
    if modele == "arome" {
        if let Some(p) = champs.get("prmsl") {
            out.push(("mslp".into(), p.clone()));
        }
    }
    if step >= 1 {
        if modele == "arome-hd" {
            if let (Some(e), Some(n)) = (champs.get("max_10efg"), champs.get("max_10nfg")) {
                out.push(("gust10".into(), e.iter().zip(n).map(|(&a, &b)| (a as f64).hypot(b as f64) as f32).collect()));
            }
        } else if let Some(g) = champs.get("max_i10fg") {
            out.push(("gust10".into(), g.clone()));
        }
        // cumul total tirf + tsnowp + tgrp en f32, dans cet ordre
        let total = |c: &Champs| -> Option<Vec<f32>> {
            let (a, b, d) = (c.get("tirf")?, c.get("tsnowp")?, c.get("tgrp")?);
            Some(a.iter().zip(b).zip(d).map(|((&x, &y), &z)| x + y + z).collect())
        };
        if let Some(tot_n) = total(champs) {
            let (tot_p, sn_p): (Option<Vec<f32>>, Option<Vec<f32>>) = if step == 1 {
                (Some(vec![0.0; tot_n.len()]), Some(vec![0.0; tot_n.len()]))
            } else {
                match prev {
                    Some(p) => (total(p), p.get("tsnowp").cloned()),
                    None => (None, None),
                }
            };
            if let (Some(tot_p), Some(sn_p)) = (tot_p, sn_p) {
                // débit = max(Δcumul, 0), NaN conservé
                let d_tot: Vec<f32> = tot_n.iter().zip(&tot_p).map(|(&a, &b)| {
                    let d = a - b;
                    if d.is_nan() { d } else if d < 0.0 { 0.0 } else { d }
                }).collect();
                let sn_n = &champs["tsnowp"];
                let frac: Vec<f32> = d_tot.iter().zip(sn_n.iter().zip(&sn_p)).map(|(&dt, (&sn, &sp))| {
                    let dn = sn - sp;
                    if dt.is_nan() || dn.is_nan() {
                        return f32::NAN;
                    }
                    let f = if dt > 0.05 { dn / if dt > 0.0 { dt } else { 1.0 } } else { 0.0 };
                    f.max(0.0).min(1.0)
                }).collect();
                out.push(("precip_rate".into(), d_tot));
                out.push(("snow_fraction".into(), frac));
            }
        }
        if let (Some(b), Some(m), Some(h)) = (champs.get("lcc"), champs.get("mcc"), champs.get("hcc")) {
            let c = 100.0f32;
            let v: Vec<f32> = b.iter().zip(m).zip(h).map(|((&b, &m), &h)| {
                let x = c * (1.0 - (1.0 - b / c) * (1.0 - m / c) * (1.0 - h / c));
                if x.is_nan() { x } else { x.max(0.0).min(100.0) }
            }).collect();
            out.push(("cloud_total".into(), v));
        }
    }
    out
}

/// Axes (lat décroissante, lon croissante −180..180), arrondis à 1e-6 puis f32 ; `retourner` si les
/// lignes du fichier vont du sud au nord.
fn axes(g: &Grille) -> Result<(Vec<f32>, Vec<f32>, bool)> {
    if g.balayage & 0x20 != 0 {
        bail!("balayage j consécutif non géré");
    }
    if g.balayage & 0x80 != 0 {
        bail!("balayage i négatif non géré");
    }
    let arr = |x: f64| ((x * 1e6).round_ties_even() / 1e6) as f32;
    let sens_j = if g.balayage & 0x40 != 0 { 1.0 } else { -1.0 };
    let mut lat: Vec<f64> = (0..g.nj).map(|j| g.la1 + sens_j * g.dj * j as f64).collect();
    if (lat[g.nj - 1] - g.la2).abs() > 1e-6 {
        bail!("latitude finale incohérente : {} contre {}", lat[g.nj - 1], g.la2);
    }
    let retourner = lat[0] < lat[g.nj - 1];
    if retourner {
        lat.reverse();
    }
    let lon: Vec<f64> = (0..g.ni).map(|i| (((g.lo1 + g.di * i as f64) + 180.0).rem_euclid(360.0)) - 180.0).collect();
    if lon.windows(2).any(|w| w[1] <= w[0]) {
        bail!("longitudes non croissantes après conversion (grille traversant le méridien 180, non gérée)");
    }
    Ok((lat.into_iter().map(arr).collect(), lon.into_iter().map(arr).collect(), retourner))
}

/// Groupes d'étiquettes dont les fichiers sont nécessaires à l'échéance `step` : l'échéance n, et
/// l'échéance n−1 si elle est dans un autre fichier et si `step ≥ 2` (à l'échéance 1 le cumul précédent
/// est nul par construction, comme dans `canon.construire` du Python : le fichier n'est pas lu).
pub fn etiquettes_requises(modele: &str, step: u32) -> Vec<String> {
    let label_n = etiquette(modele, step);
    let mut labels = vec![label_n.clone()];
    if step >= 2 {
        let lp = etiquette(modele, step - 1);
        if lp != label_n {
            labels.push(lp);
        }
    }
    labels
}

/// Noms des fichiers GRIB nécessaires à l'échéance (SP1 et SP2 de chaque étiquette), dans l'ordre de lecture.
pub fn noms_requis(modele: &str, tirage: &str, step: u32) -> Result<Vec<String>> {
    let mut v = Vec::new();
    for l in etiquettes_requises(modele, step) {
        for p in PAQUETS {
            v.push(nom_fichier(modele, p, &l, tirage)?);
        }
    }
    Ok(v)
}

/// Produit le canonique d'une échéance. `dossiers` : où chercher les GRIB.
pub fn ingerer(modele: &str, tirage: &str, step: u32, dossiers: &[PathBuf], opts: &Options) -> Result<(Canon, Vec<String>, Etapes)> {
    ingerer_octets(
        modele,
        tirage,
        step,
        &mut |nom| {
            let chemin = localiser(nom, dossiers).ok_or_else(|| anyhow!("fichier absent : {nom}"))?;
            std::fs::read(&chemin).with_context(|| chemin.display().to_string())
        },
        opts,
    )
}

/// Comme [`ingerer`], mais les octets de chaque fichier GRIB (par nom, voir [`noms_requis`]) viennent de `fournir`
/// (téléchargement, mémoire…).
pub fn ingerer_octets(
    modele: &str,
    tirage: &str,
    step: u32,
    fournir: &mut dyn FnMut(&str) -> Result<Vec<u8>>,
    opts: &Options,
) -> Result<(Canon, Vec<String>, Etapes)> {
    let mut et = Etapes::default();
    let t = Instant::now();
    // fichiers SP1 / SP2 des étiquettes utiles ; le groupe précédent n'est lu que si l'échéance ≥ 2
    let mut fichiers: Vec<(String, Vec<Fichier>)> = Vec::new();
    for l in etiquettes_requises(modele, step) {
        let mut v = Vec::new();
        for p in PAQUETS {
            let nom = nom_fichier(modele, p, &l, tirage)?;
            let octets = fournir(&nom)?;
            v.push(Fichier { nom, octets });
        }
        fichiers.push((l, v));
    }
    et.lecture_fichiers_s = t.elapsed().as_secs_f64();

    // repérage des champs
    let t = Instant::now();
    struct Cible<'a> {
        nom: &'static str,
        echeance: u32,
        champ: Champ<'a>,
    }
    let mut cibles: Vec<Cible> = Vec::new();
    let mut grille: Option<Grille> = None;
    let mut vus = std::collections::HashSet::new();
    for (_, fs) in &fichiers {
        for f in fs {
            let champs = rw_grib::scanner(&f.octets).with_context(|| f.nom.clone())?;
            for c in champs {
                match &grille {
                    None => grille = Some(c.grille.clone()),
                    Some(g) if *g != c.grille => bail!("grille différente entre fichiers ({})", f.nom),
                    _ => {}
                }
                if let Some(nom) = nom_parametre(&c) {
                    let e = echeance_controlee(nom, &c)?;
                    if !vus.insert((f.nom.as_str(), nom, e)) {
                        bail!("{nom} présent deux fois à l'échéance {e} ({})", f.nom);
                    }
                    cibles.push(Cible { nom, echeance: e, champ: c });
                }
            }
        }
    }
    et.scan_s = t.elapsed().as_secs_f64();
    let grille = grille.ok_or_else(|| anyhow!("aucun champ"))?;

    // sélection à décoder
    let besoin_courant = |nom: &str| -> bool {
        match nom {
            "2t" | "2r" | "10u" | "10v" | "CAPE_INS" => true,
            "prmsl" => modele == "arome",
            "max_10efg" | "max_10nfg" => step >= 1 && modele == "arome-hd",
            "max_i10fg" => step >= 1 && modele == "arome",
            "tirf" | "tsnowp" | "tgrp" | "lcc" | "mcc" | "hcc" => step >= 1,
            _ => false,
        }
    };
    let besoin_prev = |nom: &str| step >= 2 && matches!(nom, "tirf" | "tsnowp" | "tgrp");
    let choisis: Vec<&Cible> = cibles
        .iter()
        .filter(|c| {
            if opts.tout_decoder {
                true
            } else if c.echeance == step {
                besoin_courant(c.nom)
            } else {
                c.echeance + 1 == step && besoin_prev(c.nom)
            }
        })
        .collect();
    let t = Instant::now();
    let refs: Vec<&Champ> = choisis.iter().map(|c| &c.champ).collect();
    let valeurs = rw_grib::decoder_tous(&refs, opts.backend)?;
    et.decodage_grib_s = t.elapsed().as_secs_f64();
    et.champs_decodes = valeurs.len();

    let t = Instant::now();
    let (lat, lon, retourner) = axes(&grille)?;
    let (ni, nj) = (grille.ni, grille.nj);
    let mut courant: Champs = HashMap::new();
    let mut prev: Champs = HashMap::new();
    for (c, mut v) in choisis.iter().zip(valeurs) {
        if retourner {
            let mut r = Vec::with_capacity(v.len());
            for j in (0..nj).rev() {
                r.extend_from_slice(&v[j * ni..(j + 1) * ni]);
            }
            v = r;
        }
        if c.echeance == step {
            // même paramètre dans SP1 et SP2 : le dernier gagne (comme `update` du Python)
            courant.insert(c.nom, v);
        } else if c.echeance + 1 == step {
            prev.insert(c.nom, v);
        }
    }
    let variables = construire(modele, step, &courant, if step >= 2 { Some(&prev) } else { None });
    if step >= 2 && prev.is_empty() && courant.contains_key("tirf") {
        bail!("échéance {} absente (nécessaire au débit de précipitation)", step - 1);
    }
    et.calcul_canonique_s = t.elapsed().as_secs_f64();

    let mut sources: Vec<String> = Vec::new();
    for (_, fs) in &fichiers {
        sources.extend(fs.iter().map(|f| f.nom.clone()));
    }
    Ok((Canon { modele: modele.into(), tirage: tirage.into(), echeance: step, lat, lon, variables }, sources, et))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn champs(pairs: &[(&'static str, Vec<f32>)]) -> Champs {
        pairs.iter().cloned().collect()
    }

    #[test]
    fn groupes() {
        assert_eq!(groupe_025(0), "00H06H");
        assert_eq!(groupe_025(6), "00H06H");
        assert_eq!(groupe_025(7), "07H12H");
        assert_eq!(groupe_025(48), "43H48H");
        assert_eq!(groupe_025(51), "49H51H");
        assert_eq!(etiquette("arome-hd", 3), "03H");
    }

    #[test]
    fn precip_et_neige() {
        let n = champs(&[
            ("tirf", vec![1.0, 0.0, f32::NAN, 0.2]),
            ("tsnowp", vec![0.5, 0.0, 0.0, 0.0]),
            ("tgrp", vec![0.0; 4]),
            ("2t", vec![280.0; 4]),
        ]);
        let p = champs(&[("tirf", vec![0.0, 0.5, 0.0, 0.2]), ("tsnowp", vec![0.0; 4]), ("tgrp", vec![0.0; 4])]);
        let v = construire("arome", 2, &n, Some(&p));
        let get = |k: &str| v.iter().find(|(n, _)| n == k).map(|(_, v)| v.clone()).unwrap();
        let pr = get("precip_rate");
        assert_eq!(pr[0], 1.5);
        assert_eq!(pr[1], 0.0); // décrément négatif → 0
        assert!(pr[2].is_nan());
        let sn = get("snow_fraction");
        assert!((sn[0] - 0.5 / 1.5).abs() < 1e-6);
        assert_eq!(sn[3], 0.0); // Δtotal = 0 < seuil
    }

    #[test]
    fn rafale_hd_et_nuages() {
        let n = champs(&[
            ("max_10efg", vec![3.0, -3.0]),
            ("max_10nfg", vec![4.0, 4.0]),
            ("lcc", vec![100.0, 0.0]),
            ("mcc", vec![0.0, 50.0]),
            ("hcc", vec![0.0, 50.0]),
            ("2r", vec![100.02, -1.0]),
        ]);
        let v = construire("arome-hd", 1, &n, None);
        let get = |k: &str| v.iter().find(|(n, _)| n == k).map(|(_, v)| v.clone()).unwrap();
        assert_eq!(get("gust10"), vec![5.0, 5.0]);
        assert_eq!(get("cloud_total"), vec![100.0, 75.0]);
        assert_eq!(get("rh2m"), vec![100.0, 0.0]);
        assert!(v.iter().all(|(k, _)| k != "mslp"));
    }

    #[test]
    fn axes_arome() {
        let g = Grille { ni: 3, nj: 3, la1: 55.4, lo1: 348.0, la2: 55.35, lo2: 348.05, di: 0.025, dj: 0.025, balayage: 0 };
        let (lat, lon, retourner) = axes(&g).unwrap();
        assert!(!retourner);
        assert_eq!(lon[0], -12.0);
        assert!(lat[0] > lat[2]);
    }
}
