# SPDX-License-Identifier: EUPL-1.2
"""Ligne de commande de l'ingestion : GRIB2 AROME -> champs canoniques .npz."""
import argparse
import json
import os
import resource
import sys
import time

from . import canon, download, grib

PAQUETS = ("SP1", "SP2")


def lire_run(texte):
    """`2026-10-17T06:00:00Z` -> (texte normalisé, dossier `2026-10-17T06Z`)."""
    from datetime import datetime
    d = datetime.strptime(texte, "%Y-%m-%dT%H:%M:%SZ")
    return d.strftime("%Y-%m-%dT%H:%M:%SZ"), d.strftime("%Y-%m-%dT%HZ")


def lire_echeances(texte):
    """`0-6` ou `0,3,5` ou `0-3,7` -> liste triée d'entiers."""
    out = set()
    for part in texte.split(","):
        if "-" in part:
            a, b = part.split("-")
            out |= set(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return sorted(out)


class Lecteur:
    """Décode les fichiers (paquet, étiquette) à la demande et garde les 2 derniers groupes en mémoire."""

    def __init__(self, model, fichiers, stats):
        self.model, self.fichiers, self.stats = model, fichiers, stats
        self.cache = {}   # étiquette -> ({échéance: champs}, lat, lon, [noms de fichiers])
        self.ordre = []

    def groupe(self, label):
        if label in self.cache:
            return self.cache[label]
        par_echeance, lat, lon, noms = {}, None, None, []
        for packet in PAQUETS:
            chemin, _ = self.fichiers[(packet, label)]
            t0 = time.time()
            msgs, la, lo = grib.lire_fichier(chemin, canon.filtre_message)
            dt = time.time() - t0
            idx = canon.indexer(msgs)
            self.stats["decodage"].append({"fichier": os.path.basename(chemin), "paquet": packet, "etiquette": label,
                                           "messages": len(msgs), "echeances": sorted(idx), "secondes": round(dt, 3),
                                           "octets": os.path.getsize(chemin)})
            if lat is None:
                lat, lon = la, lo
            elif not (np_egal(lat, la) and np_egal(lon, lo)):
                raise ValueError(f"grille différente entre paquets pour {label}")
            for n, ch in idx.items():
                par_echeance.setdefault(n, {}).update(ch)
            noms.append(os.path.basename(chemin))
        self.cache[label] = (par_echeance, lat, lon, noms)
        self.ordre.append(label)
        while len(self.ordre) > 2:
            del self.cache[self.ordre.pop(0)]
        return self.cache[label]


def np_egal(a, b):
    import numpy as np
    return a.shape == b.shape and bool(np.all(a == b))


def traiter(model, run, steps, src, telecharger, out, dossier_dl=None, jouer=print, bornes=True):
    """Produit les .npz canoniques. Retourne la liste des chemins écrits."""
    run_txt, run_dir = lire_run(run)
    stats = {"modele": model, "tirage": run_txt, "telechargement": [], "decodage": [], "echeances": []}
    besoins = download.fichiers_requis(model, steps, run_txt, PAQUETS)
    dossier_dl = dossier_dl or os.path.join("out", "grib", model, run_txt)
    t0 = time.time()
    if telecharger:
        fichiers = download.telecharger(model, besoins, run_txt, src, dossier_dl)
        stats["telechargement"] = [{"fichier": os.path.basename(c), **{k: v for k, v in m.items() if k != "fichier"}}
                                   for c, m in fichiers.values()]
    else:
        fichiers = {}
        for packet, label in besoins:
            c = download.localiser(model, packet, label, run_txt, list(src) + [dossier_dl])
            if c is None:
                raise FileNotFoundError(f"fichier absent : {packet} {label} (utiliser --download ou --src)")
            fichiers[(packet, label)] = (c, {})
    stats["temps_telechargement_s"] = round(time.time() - t0, 2)

    lecteur = Lecteur(model, fichiers, stats)
    ecrits = []
    for n in steps:
        t1 = time.time()
        par, lat, lon, noms = lecteur.groupe(download.etiquette(model, n))
        if n not in par:
            raise ValueError(f"échéance {n} absente du fichier {noms}")
        champs = par[n]
        prev = None
        if n >= 1:
            par_p, _, _, noms_p = lecteur.groupe(download.etiquette(model, n - 1))
            prev = par_p.get(n - 1)
            if n >= 2 and prev is None:
                raise ValueError(f"échéance {n - 1} absente (nécessaire au débit de précipitation)")
            noms = noms + [x for x in noms_p if x not in noms]
        variables = canon.construire(model, n, champs, prev, bornes=bornes)
        chemin = os.path.join(out, model, run_dir, f"{n:02d}.npz")
        note = ("ingestion pipeline.ingest ; gust10 HD = hypot(max_10efg, max_10nfg) (hypothèse) ; "
                "cloud_total = recouvrement aléatoire (hypothèse) ; 9999 -> NaN")
        canon.ecrire(chemin, lat, lon, variables, model, run_txt, n, noms, note)
        dt = time.time() - t1
        stats["echeances"].append({"echeance": n, "secondes": round(dt, 3), "variables": sorted(variables),
                                   "octets_npz": os.path.getsize(chemin)})
        jouer(f"{model} {run_dir} +{n:02d}h : {len(variables)} variables, {os.path.getsize(chemin) / 1e6:.1f} Mo, {dt:.2f} s")
        ecrits.append(chemin)
    stats["pic_memoire_Mo"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    stats["duree_totale_s"] = round(time.time() - t0, 2)
    with open(os.path.join(out, model, run_dir, "ingest_stats.json"), "w") as f:
        json.dump(stats, f, indent=1, ensure_ascii=False)
    return ecrits


def main(argv=None):
    p = argparse.ArgumentParser(prog="python3 -m pipeline.ingest", description=__doc__)
    p.add_argument("--model", required=True, choices=sorted(download.RESOLUTION))
    p.add_argument("--run", required=True, help="tirage, ex. 2026-10-17T06:00:00Z")
    p.add_argument("--steps", required=True, help="échéances, ex. 0-6 ou 0,3,5")
    p.add_argument("--src", nargs="+", default=[], help="dossier(s) de GRIB locaux (recherche récursive)")
    p.add_argument("--download", action="store_true", help="télécharger les fichiers absents (3 connexions max)")
    p.add_argument("--dl-dir", default=None, help="où écrire les téléchargements (défaut out/grib/<modèle>/<tirage>)")
    p.add_argument("--out", default="out/canon")
    p.add_argument("--sans-bornes", action="store_true",
                   help="ne borne pas rh2m à 0..100 (nécessaire pour reproduire exactement la fixture du contrat)")
    a = p.parse_args(argv)
    if not a.src and not a.download:
        p.error("il faut --src et/ou --download")
    traiter(a.model, a.run, lire_echeances(a.steps), a.src, a.download, a.out, a.dl_dir, bornes=not a.sans_bornes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
