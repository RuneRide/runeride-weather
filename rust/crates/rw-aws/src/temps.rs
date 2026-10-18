// SPDX-License-Identifier: EUPL-1.2
//! Dates UTC sans dépendance (calendrier grégorien proleptique, algorithmes de H. Hinnant).

use std::time::{SystemTime, UNIX_EPOCH};

fn jours_depuis_epoch(a: i64, m: i64, j: i64) -> i64 {
    let a = if m <= 2 { a - 1 } else { a };
    let ere = a.div_euclid(400);
    let aa = a - ere * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + j - 1;
    let doe = aa * 365 + aa / 4 - aa / 100 + doy;
    ere * 146097 + doe - 719468
}

fn civil_depuis_jours(z: i64) -> (i64, i64, i64) {
    let z = z + 719468;
    let ere = z.div_euclid(146097);
    let doe = z - ere * 146097;
    let aa = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let doy = doe - (365 * aa + aa / 4 - aa / 100);
    let mp = (5 * doy + 2) / 153;
    let j = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (aa + ere * 400 + i64::from(m <= 2), m, j)
}

/// Secondes depuis l'époque Unix pour une date UTC.
pub fn epoch(a: i64, m: i64, j: i64, h: i64, mi: i64, s: i64) -> i64 {
    jours_depuis_epoch(a, m, j) * 86400 + h * 3600 + mi * 60 + s
}

/// `(année, mois, jour, heure, minute, seconde)` UTC.
pub fn decomposer(t: i64) -> (i64, i64, i64, i64, i64, i64) {
    let (j, r) = (t.div_euclid(86400), t.rem_euclid(86400));
    let (a, m, d) = civil_depuis_jours(j);
    (a, m, d, r / 3600, r % 3600 / 60, r % 60)
}

/// `2026-10-17T03:00:00Z`.
pub fn iso(t: i64) -> String {
    let (a, m, d, h, mi, s) = decomposer(t);
    format!("{a:04}-{m:02}-{d:02}T{h:02}:{mi:02}:{s:02}Z")
}

/// `20261017T030000Z` (format `x-amz-date` de SigV4).
pub fn amz(t: i64) -> String {
    let (a, m, d, h, mi, s) = decomposer(t);
    format!("{a:04}{m:02}{d:02}T{h:02}{mi:02}{s:02}Z")
}

/// Analyse `2026-10-17T03:00:00Z`, `2026-10-17T03:00:00.000Z` (listage S3) ou `2026-10-17T03Z` (forme courte
/// des tirages) et renvoie les secondes Unix. `None` si la forme ou la date est invalide.
pub fn depuis_iso(s: &str) -> Option<i64> {
    let b = s.as_bytes();
    if !s.is_ascii() || b.len() < 14 || b[4] != b'-' || b[7] != b'-' || b[10] != b'T' || *b.last()? != b'Z' {
        return None;
    }
    let n = |a: usize, z: usize| -> Option<i64> {
        let t = s.get(a..z)?;
        (t.bytes().all(|c| c.is_ascii_digit()) && !t.is_empty()).then(|| t.parse().ok()).flatten()
    };
    let (an, mo, jo, he) = (n(0, 4)?, n(5, 7)?, n(8, 10)?, n(11, 13)?);
    let (mi, se) = if b.len() == 14 {
        (0, 0)
    } else {
        if b[13] != b':' || b.len() < 20 || b[16] != b':' {
            return None;
        }
        let fin = if b.len() == 20 {
            true
        } else {
            // fraction de seconde facultative : `.123Z`
            b[19] == b'.' && s[20..b.len() - 1].bytes().all(|c| c.is_ascii_digit()) && b.len() > 21
        };
        if !fin {
            return None;
        }
        (n(14, 16)?, n(17, 19)?)
    };
    if !(1..=12).contains(&mo) || !(1..=31).contains(&jo) || he > 23 || mi > 59 || se > 60 {
        return None;
    }
    let t = epoch(an, mo, jo, he, mi, se);
    let (a2, m2, j2, ..) = decomposer(t);
    ((a2, m2, j2) == (an, mo, jo)).then_some(t)
}

pub fn maintenant() -> i64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn aller_retour() {
        let t = epoch(2026, 10, 17, 3, 0, 0);
        assert_eq!(iso(t), "2026-10-17T03:00:00Z");
        assert_eq!(iso(t + 51 * 3600), "2026-10-19T06:00:00Z");
        assert_eq!(amz(t), "20261017T030000Z");
        assert_eq!(epoch(1970, 1, 1, 0, 0, 0), 0);
        assert_eq!(iso(epoch(2024, 2, 29, 23, 59, 59) + 1), "2024-03-01T00:00:00Z");
        assert_eq!(epoch(2013, 5, 24, 0, 0, 0), 1369353600); // jeu d'essai de la documentation SigV4 S3
    }

    #[test]
    fn analyse_iso() {
        let t = epoch(2026, 10, 17, 3, 0, 0);
        assert_eq!(depuis_iso("2026-10-17T03:00:00Z"), Some(t));
        assert_eq!(depuis_iso("2026-10-17T03Z"), Some(t));
        assert_eq!(depuis_iso("2026-10-17T03:00:01.250Z"), Some(t + 1));
        for mauvais in ["", "2026-10-17", "2026-10-17T03:00:00", "2026-02-30T03Z", "2026-10-17T25Z", "2026-10-17T03:00:00.Z", "x026-10-17T03Z"] {
            assert_eq!(depuis_iso(mauvais), None, "{mauvais:?}");
        }
    }
}
