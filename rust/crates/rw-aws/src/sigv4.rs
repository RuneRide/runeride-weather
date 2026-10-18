// SPDX-License-Identifier: EUPL-1.2
//! Signature AWS Signature Version 4 (en-tête `Authorization`), juste ce qu'il faut pour S3.

use hmac::{Hmac, Mac};
use sha2::{Digest, Sha256};

type HmacSha256 = Hmac<Sha256>;

pub fn hex(b: &[u8]) -> String {
    const H: &[u8; 16] = b"0123456789abcdef";
    let mut s = String::with_capacity(b.len() * 2);
    for &x in b {
        s.push(H[(x >> 4) as usize] as char);
        s.push(H[(x & 15) as usize] as char);
    }
    s
}

pub fn sha256_hex(b: &[u8]) -> String {
    hex(&Sha256::digest(b))
}

fn hmac(cle: &[u8], donnees: &[u8]) -> Vec<u8> {
    let mut m = HmacSha256::new_from_slice(cle).expect("HMAC accepte toute longueur de clé");
    m.update(donnees);
    m.finalize().into_bytes().to_vec()
}

/// Encodage d'un segment de chemin (RFC 3986, comme S3 : tout sauf `A-Za-z0-9-_.~`).
pub fn encoder_segment(s: &str) -> String {
    let mut o = String::with_capacity(s.len());
    for &b in s.as_bytes() {
        if b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.' | b'~') {
            o.push(b as char);
        } else {
            o.push_str(&format!("%{b:02X}"));
        }
    }
    o
}

/// Chemin canonique : segments encodés, `/` conservés.
pub fn encoder_chemin(chemin: &str) -> String {
    chemin.split('/').map(encoder_segment).collect::<Vec<_>>().join("/")
}

pub struct Demande<'a> {
    pub methode: &'a str,
    /// Chemin déjà encodé (commence par `/`).
    pub chemin: &'a str,
    /// Requête canonique (vide pour nos usages).
    pub requete: &'a str,
    /// En-têtes à signer : noms en minuscules, triés par nom ; valeurs sans espaces superflus.
    pub entetes: &'a [(String, String)],
    pub hash_charge: &'a str,
    pub region: &'a str,
    pub service: &'a str,
    /// `20130524T000000Z`
    pub date_amz: &'a str,
}

/// Valeur de l'en-tête `Authorization` (et liste des en-têtes signés incluse).
pub fn autorisation(d: &Demande<'_>, cle_acces: &str, secret: &str) -> String {
    let canon_entetes: String = d.entetes.iter().map(|(k, v)| format!("{k}:{v}\n")).collect();
    let signes = d.entetes.iter().map(|(k, _)| k.as_str()).collect::<Vec<_>>().join(";");
    let requete_canonique = format!("{}\n{}\n{}\n{}\n{}\n{}", d.methode, d.chemin, d.requete, canon_entetes, signes, d.hash_charge);
    let jour = &d.date_amz[..8];
    let portee = format!("{jour}/{}/{}/aws4_request", d.region, d.service);
    let a_signer = format!("AWS4-HMAC-SHA256\n{}\n{portee}\n{}", d.date_amz, sha256_hex(requete_canonique.as_bytes()));
    let k = hmac(format!("AWS4{secret}").as_bytes(), jour.as_bytes());
    let k = hmac(&k, d.region.as_bytes());
    let k = hmac(&k, d.service.as_bytes());
    let k = hmac(&k, b"aws4_request");
    let signature = hex(&hmac(&k, a_signer.as_bytes()));
    format!("AWS4-HMAC-SHA256 Credential={cle_acces}/{portee}, SignedHeaders={signes}, Signature={signature}")
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Exemple « GET Object avec en-tête Range » de la documentation SigV4 de S3 (clés publiques d'exemple).
    #[test]
    fn exemple_documentation_get_range() {
        let vide = sha256_hex(b"");
        assert_eq!(vide, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
        let entetes = vec![
            ("host".to_string(), "examplebucket.s3.amazonaws.com".to_string()),
            ("range".to_string(), "bytes=0-9".to_string()),
            ("x-amz-content-sha256".to_string(), vide.clone()),
            ("x-amz-date".to_string(), "20130524T000000Z".to_string()),
        ];
        let a = autorisation(
            &Demande { methode: "GET", chemin: "/test.txt", requete: "", entetes: &entetes, hash_charge: &vide, region: "us-east-1", service: "s3", date_amz: "20130524T000000Z" },
            "AKIAIOSFODNN7EXAMPLE",
            "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        );
        assert!(a.ends_with("Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"), "{a}");
        assert!(a.contains("SignedHeaders=host;range;x-amz-content-sha256;x-amz-date"));
    }

    #[test]
    fn encodage() {
        assert_eq!(encoder_chemin("/v1/arome-hd/2026-10-17T03Z/t2m/12.pmtiles"), "/v1/arome-hd/2026-10-17T03Z/t2m/12.pmtiles");
        assert_eq!(encoder_chemin("/a b/é$"), "/a%20b/%C3%A9%24");
    }
}
