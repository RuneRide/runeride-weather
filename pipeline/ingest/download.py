# SPDX-License-Identifier: EUPL-1.2
"""Téléchargement courtois des fichiers GRIB2 AROME / AROME HD (S3 public de Météo-France).

Règles : 3 connexions simultanées au plus, reprise sur fichier partiel (`.part`,
requête `Range`), vérification de la taille annoncée (`Content-Length`), jamais de
retéléchargement d'un fichier déjà présent et complet, nouvelle tentative espacée
(pas de boucle serrée). Les fichiers téléchargés sont des données non fiables :
ils ne sont jamais exécutés, seulement décodés par eccodes.
"""
import os
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = "https://meteofrance-pnt.s3.rbx.io.cloud.ovh.net/pnt"
MAX_CONNEXIONS = 3
AGENT = "runeride-weather-poc/0.1 (ingestion; contact: philippegeek@gmail.com)"
TAILLE_BLOC = 1 << 20

# modèle -> (dossier de résolution sur le S3, code de résolution dans le nom)
RESOLUTION = {"arome-hd": "001", "arome": "0025"}


def groupe_025(step):
    """Étiquette du groupe d'échéances 0,025° contenant `step` (00H06H, 07H12H, ..., 49H51H)."""
    if step <= 6:
        return "00H06H"
    debut = 7 + 6 * ((step - 7) // 6)
    fin = min(debut + 5, 51)
    return f"{debut:02d}H{fin:02d}H"


def etiquette(model, step):
    """Étiquette d'échéance dans le nom de fichier : `03H` (HD) ou `00H06H` (0,025°)."""
    return f"{step:02d}H" if model == "arome-hd" else groupe_025(step)


def bornes_groupe(label):
    """`07H12H` -> (7, 12) ; `03H` -> (3, 3)."""
    parts = label.split("H")
    nums = [int(p) for p in parts if p]
    return nums[0], nums[-1]


def nom_fichier(model, packet, label, run):
    res = RESOLUTION[model]
    return f"arome__{res}__{packet}__{label}__{run}.grib2"


def url_fichier(model, packet, label, run):
    res = RESOLUTION[model]
    return f"{BASE}/{run}/arome/{res}/{packet}/{nom_fichier(model, packet, label, run)}"


def fichiers_requis(model, steps, run, paquets=("SP1", "SP2")):
    """Liste (paquet, étiquette) nécessaire pour calculer `steps`, avec l'échéance n-1 pour les débits.

    En 0,025°, l'échéance 7 a besoin de l'échéance 6, qui est dans le groupe précédent.
    """
    besoin = set(steps)
    besoin |= {s - 1 for s in steps if s >= 1}
    labels = sorted({etiquette(model, s) for s in besoin})
    return [(p, lab) for lab in labels for p in paquets]


def localiser(model, packet, label, run, dossiers):
    """Cherche un fichier déjà présent (nom complet ou nom sans tirage), récursivement."""
    res = RESOLUTION[model]
    cibles = {nom_fichier(model, packet, label, run), f"arome__{res}__{packet}__{label}.grib2"}
    for d in dossiers:
        if not os.path.isdir(d):
            continue
        for racine, _dirs, fichiers in os.walk(d):
            for f in fichiers:
                if f in cibles:
                    return os.path.join(racine, f)
    return None


def _taille_distante(url):
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        return int(r.headers["Content-Length"])


def _telecharger_un(url, dest, essais=4):
    """Télécharge `url` vers `dest` avec reprise. Retourne un dict de mesures."""
    t0 = time.time()
    attendu = _taille_distante(url)
    if os.path.exists(dest) and os.path.getsize(dest) == attendu:
        return {"fichier": dest, "octets": attendu, "secondes": 0.0, "deja_present": True}
    part = dest + ".part"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    derniere = None
    for essai in range(essais):
        deja = os.path.getsize(part) if os.path.exists(part) else 0
        if deja > attendu:
            os.remove(part)
            deja = 0
        if deja < attendu:
            hdr = {"User-Agent": AGENT}
            if deja:
                hdr["Range"] = f"bytes={deja}-"
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=60) as r:
                    if deja and r.status != 206:
                        deja = 0  # le serveur renvoie tout : on repart de zéro
                    with open(part, "ab" if deja else "wb") as out:
                        while True:
                            bloc = r.read(TAILLE_BLOC)
                            if not bloc:
                                break
                            out.write(bloc)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                derniere = exc
        if os.path.exists(part) and os.path.getsize(part) == attendu:
            os.replace(part, dest)
            dt = time.time() - t0
            return {"fichier": dest, "octets": attendu, "secondes": dt, "deja_present": False}
        time.sleep(2 * (essai + 1))
    raise IOError(f"téléchargement incomplet de {url} ({derniere})")


def telecharger(model, besoins, run, dossiers_src, dossier_dl):
    """Assure la présence de chaque fichier de `besoins`. Retourne {(paquet, label): (chemin, mesure)}."""
    resultat, a_faire = {}, []
    for packet, label in besoins:
        existant = localiser(model, packet, label, run, [dossier_dl] + list(dossiers_src))
        if existant:
            taille = os.path.getsize(existant)
            attendu = _taille_distante(url_fichier(model, packet, label, run))
            if taille != attendu:
                raise IOError(f"{existant} : {taille} octets, {attendu} attendus (fichier tronqué ?)")
            resultat[(packet, label)] = (existant, {"octets": taille, "secondes": 0.0, "deja_present": True})
        else:
            a_faire.append((packet, label))

    def tache(pl):
        packet, label = pl
        dest = os.path.join(dossier_dl, nom_fichier(model, packet, label, run))
        return pl, _telecharger_un(url_fichier(model, packet, label, run), dest)

    with ThreadPoolExecutor(max_workers=MAX_CONNEXIONS) as ex:
        for pl, mesure in ex.map(tache, a_faire):
            resultat[pl] = (mesure["fichier"], mesure)
    return resultat
