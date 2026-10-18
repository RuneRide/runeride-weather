// SPDX-License-Identifier: EUPL-1.2
//! Écriture PMTiles v3 (port du `Writer` de la bibliothèque Python `pmtiles` 3.x).
//!
//! Index Hilbert, répertoires et métadonnées compressés en gzip, tuiles identiques dédupliquées,
//! répertoire racine borné à 16 383 − 127 octets (feuilles de 4096 entrées, doublées au besoin).

use flate2::{write::GzEncoder, Compression};
use std::collections::HashMap;
use std::io::Write;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Entree {
    pub id: u64,
    pub decalage: u64,
    pub longueur: u32,
    pub run: u32,
}

fn tourner(n: i64, x: &mut i64, y: &mut i64, rx: i64, ry: i64) {
    if ry == 0 {
        if rx != 0 {
            *x = n - 1 - *x;
            *y = n - 1 - *y;
        }
        std::mem::swap(x, y);
    }
}

/// Identifiant Hilbert d'une tuile (z, x, y). Arithmétique signée : comme l'implémentation de
/// référence, `x` et `y` peuvent devenir négatifs en cours de route (seuls les bits bas comptent).
pub fn tuile_vers_id(z: u32, x: u64, y: u64) -> u64 {
    assert!(z <= 31 && x < (1 << z) && y < (1 << z), "tuile hors zoom");
    let (mut x, mut y) = (x as i64, y as i64);
    let mut acc: u64 = ((1u64 << (z * 2)) - 1) / 3;
    for a in (0..z).rev() {
        let s = 1i64 << a;
        let rx = s & x;
        let ry = s & y;
        acc += (((3 * rx) ^ ry) as u64) << a;
        tourner(s, &mut x, &mut y, rx, ry);
    }
    acc
}

/// Inverse de [`tuile_vers_id`].
pub fn id_vers_tuile(id: u64) -> (u32, u64, u64) {
    let z = ((3 * id + 1).ilog2()) / 2;
    let acc = ((1u64 << (z * 2)) - 1) / 3;
    let mut pos = (id - acc) as i64;
    let (mut x, mut y) = (0i64, 0i64);
    let n = 1i64 << z;
    let mut s = 1i64;
    while s < n {
        let rx = (pos / 2) & s;
        let ry = (pos ^ rx) & s;
        tourner(s, &mut x, &mut y, rx, ry);
        x += rx;
        y += ry;
        pos >>= 1;
        s <<= 1;
    }
    (z, x as u64, y as u64)
}

fn varint(out: &mut Vec<u8>, mut v: u64) {
    loop {
        let b = (v & 0x7f) as u8;
        v >>= 7;
        if v != 0 {
            out.push(b | 0x80);
        } else {
            out.push(b);
            break;
        }
    }
}

fn gzip(data: &[u8]) -> Vec<u8> {
    let mut e = GzEncoder::new(Vec::with_capacity(data.len() / 2 + 32), Compression::best());
    e.write_all(data).expect("écriture en mémoire");
    e.finish().expect("écriture en mémoire")
}

/// Répertoire sérialisé (non compressé) puis gzip.
pub fn serialiser_repertoire(entrees: &[Entree]) -> Vec<u8> {
    let mut b = Vec::new();
    varint(&mut b, entrees.len() as u64);
    let mut dernier = 0;
    for e in entrees {
        varint(&mut b, e.id - dernier);
        dernier = e.id;
    }
    for e in entrees {
        varint(&mut b, e.run as u64);
    }
    for e in entrees {
        varint(&mut b, e.longueur as u64);
    }
    for (i, e) in entrees.iter().enumerate() {
        if i > 0 && e.decalage == entrees[i - 1].decalage + entrees[i - 1].longueur as u64 {
            varint(&mut b, 0);
        } else {
            varint(&mut b, e.decalage + 1);
        }
    }
    gzip(&b)
}

fn racine_et_feuilles(entrees: &[Entree], taille_feuille: usize) -> (Vec<u8>, Vec<u8>) {
    let mut racine = Vec::new();
    let mut feuilles = Vec::new();
    for morceau in entrees.chunks(taille_feuille) {
        let s = serialiser_repertoire(morceau);
        racine.push(Entree { id: morceau[0].id, decalage: feuilles.len() as u64, longueur: s.len() as u32, run: 0 });
        feuilles.extend_from_slice(&s);
    }
    (serialiser_repertoire(&racine), feuilles)
}

/// (racine, feuilles) en respectant la borne de taille du répertoire racine.
fn optimiser(entrees: &[Entree], cible: usize) -> (Vec<u8>, Vec<u8>) {
    let test = serialiser_repertoire(entrees);
    if test.len() < cible {
        return (test, Vec::new());
    }
    let mut taille = 4096;
    loop {
        let (r, f) = racine_et_feuilles(entrees, taille);
        if r.len() < cible {
            return (r, f);
        }
        taille *= 2;
    }
}

/// Emprise et centre en 1e-7 degré.
#[derive(Debug, Clone, Copy)]
pub struct EnTete {
    pub emprise: (f64, f64, f64, f64), // lon_min, lat_min, lon_max, lat_max
    pub zoom_centre: u8,
}

fn e7(d: f64) -> i32 {
    (d * 1e7).round_ties_even() as i32
}

/// Écrit un fichier PMTiles v3 (tuiles WebP, non compressées) à partir de `tuiles` (z, x, y, octets).
/// Retourne les octets du fichier. `metadonnees` est sérialisé en JSON puis gzip.
pub fn ecrire(mut tuiles: Vec<((u32, u32, u32), Vec<u8>)>, metadonnees: &serde_json::Value, ent: EnTete) -> Vec<u8> {
    assert!(!tuiles.is_empty(), "aucune tuile");
    tuiles.sort_by_key(|((z, x, y), _)| tuile_vers_id(*z, *x as u64, *y as u64));
    let mut donnees: Vec<u8> = Vec::new();
    let mut entrees: Vec<Entree> = Vec::new();
    let mut deja: HashMap<&[u8], u64> = HashMap::new();
    let mut adressees = 0u64;
    for ((z, x, y), d) in &tuiles {
        let id = tuile_vers_id(*z, *x as u64, *y as u64);
        match deja.get(d.as_slice()) {
            Some(&decalage) => {
                let dernier = entrees.last_mut().expect("une entrée précède un doublon");
                if id == dernier.id + dernier.run as u64 && dernier.decalage == decalage {
                    dernier.run += 1;
                } else {
                    entrees.push(Entree { id, decalage, longueur: d.len() as u32, run: 1 });
                }
            }
            None => {
                let decalage = donnees.len() as u64;
                donnees.extend_from_slice(d);
                entrees.push(Entree { id, decalage, longueur: d.len() as u32, run: 1 });
                deja.insert(d.as_slice(), decalage);
            }
        }
        adressees += 1;
    }
    let contenus = deja.len() as u64;
    let zmin = id_vers_tuile(entrees[0].id).0 as u8;
    let zmax = id_vers_tuile(entrees[entrees.len() - 1].id).0 as u8;
    let (racine, feuilles) = optimiser(&entrees, 16384 - 127);
    let meta = gzip(serde_json::to_string(metadonnees).expect("JSON").as_bytes());

    let mut h: Vec<u8> = Vec::with_capacity(127);
    h.extend_from_slice(b"PMTiles");
    h.push(3);
    let racine_off = 127u64;
    let meta_off = racine_off + racine.len() as u64;
    let feuilles_off = meta_off + meta.len() as u64;
    let donnees_off = feuilles_off + feuilles.len() as u64;
    for v in [
        racine_off,
        racine.len() as u64,
        meta_off,
        meta.len() as u64,
        feuilles_off,
        feuilles.len() as u64,
        donnees_off,
        donnees.len() as u64,
        adressees,
        entrees.len() as u64,
        contenus,
    ] {
        h.extend_from_slice(&v.to_le_bytes());
    }
    h.push(1); // clustered
    h.push(2); // compression interne : gzip
    h.push(1); // compression des tuiles : aucune
    h.push(4); // type : webp
    h.push(zmin);
    h.push(zmax);
    let (lo0, la0, lo1, la1) = ent.emprise;
    for v in [e7(lo0), e7(la0), e7(lo1), e7(la1)] {
        h.extend_from_slice(&v.to_le_bytes());
    }
    h.push(ent.zoom_centre);
    h.extend_from_slice(&e7((lo0 + lo1) / 2.0).to_le_bytes());
    h.extend_from_slice(&e7((la0 + la1) / 2.0).to_le_bytes());
    debug_assert_eq!(h.len(), 127);

    let mut out = Vec::with_capacity(127 + racine.len() + meta.len() + feuilles.len() + donnees.len());
    out.extend_from_slice(&h);
    out.extend_from_slice(&racine);
    out.extend_from_slice(&meta);
    out.extend_from_slice(&feuilles);
    out.extend_from_slice(&donnees);
    out
}

/// Fichier PMTiles relu (pour les tests et la vérification : même lecture que la bibliothèque Python).
#[derive(Debug)]
pub struct Lu {
    pub metadonnees: serde_json::Value,
    pub zoom_min: u8,
    pub zoom_max: u8,
    pub adressees: u64,
    pub contenus: u64,
    entrees: Vec<Entree>,
    debut_donnees: usize,
}

fn lire_varint(b: &[u8], p: &mut usize) -> Result<u64, String> {
    let mut v = 0u64;
    let mut dec = 0;
    loop {
        let o = *b.get(*p).ok_or("varint tronqué")?;
        *p += 1;
        v |= ((o & 0x7f) as u64) << dec;
        dec += 7;
        if o & 0x80 == 0 {
            return Ok(v);
        }
        if dec > 63 {
            return Err("varint trop long".into());
        }
    }
}

fn gunzip(b: &[u8]) -> Result<Vec<u8>, String> {
    use std::io::Read;
    let mut o = Vec::new();
    flate2::read::GzDecoder::new(b).read_to_end(&mut o).map_err(|e| e.to_string())?;
    Ok(o)
}

fn lire_repertoire(gz: &[u8]) -> Result<Vec<Entree>, String> {
    let b = gunzip(gz)?;
    let mut p = 0;
    let n = lire_varint(&b, &mut p)? as usize;
    if n > b.len() {
        return Err("répertoire incohérent".into());
    }
    let mut e = vec![Entree { id: 0, decalage: 0, longueur: 0, run: 0 }; n];
    let mut dernier = 0;
    for x in e.iter_mut() {
        dernier += lire_varint(&b, &mut p)?;
        x.id = dernier;
    }
    for x in e.iter_mut() {
        x.run = lire_varint(&b, &mut p)? as u32;
    }
    for x in e.iter_mut() {
        x.longueur = lire_varint(&b, &mut p)? as u32;
    }
    for i in 0..n {
        let t = lire_varint(&b, &mut p)?;
        e[i].decalage = if i > 0 && t == 0 { e[i - 1].decalage + e[i - 1].longueur as u64 } else { t - 1 };
    }
    Ok(e)
}

/// Lit l'en-tête, les métadonnées et tous les répertoires (racine et feuilles).
pub fn lire(f: &[u8]) -> Result<Lu, String> {
    if f.len() < 127 || &f[..7] != b"PMTiles" || f[7] != 3 {
        return Err("pas un PMTiles v3".into());
    }
    let u = |o: usize| u64::from_le_bytes(f[o..o + 8].try_into().unwrap()) as usize;
    let (ro, rl, mo, ml, lo, ll, dof) = (u(8), u(16), u(24), u(32), u(40), u(48), u(56));
    let tranche = |o: usize, l: usize| o.checked_add(l).and_then(|e| f.get(o..e)).ok_or_else(|| "section hors fichier".to_string());
    let metadonnees = serde_json::from_slice(&gunzip(tranche(mo, ml)?)?).map_err(|e| e.to_string())?;
    let mut entrees = Vec::new();
    for e in lire_repertoire(tranche(ro, rl)?)? {
        if e.run == 0 {
            entrees.extend(lire_repertoire(tranche(lo + e.decalage as usize, e.longueur as usize)?)?);
        } else {
            entrees.push(e);
        }
    }
    let _ = ll;
    Ok(Lu { metadonnees, zoom_min: f[100], zoom_max: f[101], adressees: u(72) as u64, contenus: u(88) as u64, entrees, debut_donnees: dof })
}

impl Lu {
    /// Octets de la tuile (z, x, y), `None` si absente.
    pub fn tuile<'a>(&self, f: &'a [u8], z: u32, x: u64, y: u64) -> Option<&'a [u8]> {
        let id = tuile_vers_id(z, x, y);
        let i = self.entrees.partition_point(|e| e.id <= id).checked_sub(1)?;
        let e = &self.entrees[i];
        if id >= e.id + e.run as u64 {
            return None;
        }
        let d = self.debut_donnees + e.decalage as usize;
        f.get(d..d + e.longueur as usize)
    }

    /// Nombre d'entrées de répertoire (après déduplication par plages).
    pub fn nb_entrees(&self) -> usize {
        self.entrees.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hilbert_valeurs_connues() {
        // valeurs de la spécification PMTiles v3
        assert_eq!(tuile_vers_id(0, 0, 0), 0);
        assert_eq!(tuile_vers_id(1, 0, 0), 1);
        assert_eq!(tuile_vers_id(1, 0, 1), 2);
        assert_eq!(tuile_vers_id(1, 1, 1), 3);
        assert_eq!(tuile_vers_id(1, 1, 0), 4);
        assert_eq!(tuile_vers_id(2, 0, 0), 5);
    }

    #[test]
    fn hilbert_aller_retour() {
        for z in 0..9u32 {
            let n = 1u64 << z;
            for (x, y) in [(0, 0), (n - 1, 0), (0, n - 1), (n - 1, n - 1), (n / 2, n / 3)] {
                assert_eq!(id_vers_tuile(tuile_vers_id(z, x, y)), (z, x, y));
            }
        }
    }

    #[test]
    fn fichier_minimal() {
        let meta = serde_json::json!({"a": 1});
        let t = vec![((1, 0, 0), vec![1, 2, 3]), ((1, 1, 0), vec![1, 2, 3]), ((1, 0, 1), vec![9])];
        let f = ecrire(t, &meta, EnTete { emprise: (-1.0, 2.0, 3.0, 4.0), zoom_centre: 1 });
        assert_eq!(&f[..8], b"PMTiles\x03");
        let u64_at = |o: usize| u64::from_le_bytes(f[o..o + 8].try_into().unwrap());
        assert_eq!(u64_at(8), 127);
        assert_eq!(u64_at(72), 3); // tuiles adressées
        assert_eq!(u64_at(88), 2); // contenus distincts
        assert_eq!(f.len() as u64, u64_at(56) + u64_at(64));
        assert_eq!(f[99], 4);
    }

    #[test]
    fn relecture_et_deduplication() {
        let meta = serde_json::json!({"a": 1, "b": "é"});
        // 3 tuiles identiques consécutives sur l'index Hilbert, une différente
        let ids: Vec<(u32, u32, u32)> = (5..9).map(|i| { let (z, x, y) = id_vers_tuile(i); (z, x as u32, y as u32) }).collect();
        let t: Vec<_> = ids.iter().enumerate().map(|(i, &k)| (k, if i < 3 { vec![7u8; 10] } else { vec![8u8; 4] })).collect();
        let f = ecrire(t, &meta, EnTete { emprise: (0.0, 0.0, 1.0, 1.0), zoom_centre: 2 });
        let l = lire(&f).unwrap();
        assert_eq!(l.metadonnees, meta);
        assert_eq!((l.zoom_min, l.zoom_max, l.adressees, l.contenus), (2, 2, 4, 2));
        assert_eq!(l.nb_entrees(), 2); // plage de 3 + 1
        assert_eq!(l.tuile(&f, ids[1].0, ids[1].1 as u64, ids[1].2 as u64).unwrap(), &[7u8; 10]);
        assert_eq!(l.tuile(&f, ids[3].0, ids[3].1 as u64, ids[3].2 as u64).unwrap(), &[8u8; 4]);
        assert!(l.tuile(&f, 2, 3, 3).is_none());
    }

    #[test]
    fn feuilles_quand_la_racine_deborde() {
        // beaucoup de tuiles distinctes, positions et longueurs pseudo-aléatoires (peu compressibles) :
        // la racine seule dépasserait 16 256 octets
        let mut etat = 12345u64;
        let mut alea = move || {
            etat = etat.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
            etat >> 33
        };
        let mut vus = std::collections::HashSet::new();
        let mut t = Vec::new();
        while t.len() < 12000 {
            let (x, y) = (alea() % 8192, alea() % 8192);
            if vus.insert((x, y)) {
                let n = 1 + alea() % 200;
                let mut d = vec![0u8; n as usize];
                d[0] = (t.len() % 251) as u8;
                d.extend_from_slice(&(t.len() as u32).to_le_bytes()); // contenu unique
                t.push(((13u32, x as u32, y as u32), d));
            }
        }
        let copie = t.clone();
        let f = ecrire(t, &serde_json::json!({}), EnTete { emprise: (0.0, 0.0, 1.0, 1.0), zoom_centre: 13 });
        let l = lire(&f).unwrap();
        assert_eq!(l.adressees, 12000);
        let feuilles = u64::from_le_bytes(f[48..56].try_into().unwrap());
        let racine = u64::from_le_bytes(f[16..24].try_into().unwrap());
        assert!(feuilles > 0, "des répertoires feuilles étaient attendus");
        assert!(racine < 16384 - 127);
        for ((z, x, y), d) in copie.iter().step_by(997) {
            assert_eq!(l.tuile(&f, *z, *x as u64, *y as u64).unwrap(), d.as_slice());
        }
    }
}
