# SPDX-License-Identifier: EUPL-1.2
"""Construction du manifest à partir de l'arborescence de tuiles.

Arborescence lue : <tuiles>/<modèle>/<tirage AAAA-MM-JJTHHZ>/<variable>/<échéance NN>.pmtiles.
Aucune tuile n'est décodée : on liste les fichiers (échéances disponibles) et on lit
l'en-tête + les métadonnées PMTiles d'un seul fichier par (modèle, tirage, variable).
"""
import datetime as dt
import json
import os
import re

import numpy as np

from . import domaine

VERSION = 1
ATTRIBUTION = "Source : Météo-France, Licence Ouverte v2.0"
MODELE_FICHIER = "{model}/{run_id}/{variable}/{step}.pmtiles"
RE_TIRAGE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2})Z$")
RE_ECHEANCE = re.compile(r"^(\d{2,3})\.pmtiles$")

# Description statique des modèles (specifications.md §3.1) ; l'emprise de la grille est
# vérifiée contre les données quand les champs canoniques sont disponibles.
MODELES = {
    "arome": {"label": "AROME", "resolution_deg": 0.025, "grid": [-12.0, 37.5, 16.0, 55.4], "horizon_h": 51},
    "arome-hd": {"label": "AROME HD", "resolution_deg": 0.01, "grid": [-12.0, 37.5, 16.0, 55.4], "horizon_h": 51},
}

# Première échéance publiée par variable : les grandeurs dérivées d'un cumul ou d'un
# maximum sur l'heure précédente n'existent pas à l'échéance 0 (poc-j1-contrat.md §2).
PREMIERE_ECHEANCE = {"gust10": 1, "precip_rate": 1, "snow_fraction": 1, "cloud_total": 1}

# Couches de l'interface -> variable tuilée (mêmes clés que viewer/js/config.js).
# La pression (mslp) n'est pas tuilée en raster : ses isobares seront des tuiles vectorielles.
COUCHES = {
    "vent": ("Vent", "wind10"),
    "rafales": ("Rafales", "gust10"),
    "t2m": ("Température", "t2m"),
    "pluie": ("Pluie", "precip_rate"),
    "nuages": ("Nuages", "cloud_total"),
    "humidite": ("Humidité", "rh2m"),
    "neige": ("Neige (part)", "snow_fraction"),
    "cape": ("Orages (CAPE)", "cape"),
}
HORS_RASTER = {
    "pression": {"variable": "mslp", "models": ["arome"], "rendu": "isobares vectorielles",
                 "note": "pas de pression réduite au niveau de la mer en 0,01° ; non tuilée en raster"},
}


def _iso(run_id):
    m = RE_TIRAGE.match(run_id)
    return "%s-%s-%sT%s:00:00Z" % m.groups()


def _ms(iso):
    return dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def _iso_plus(iso, heures):
    return (_ms(iso) + dt.timedelta(hours=heures)).strftime("%Y-%m-%dT%H:%M:%SZ")


def lire_metadonnees(chemin):
    """Une seule lecture d'en-tête PMTiles : (en-tête, métadonnées). Les tuiles ne sont pas lues."""
    from pmtiles.reader import MmapSource, Reader
    with open(chemin, "rb") as f:
        r = Reader(MmapSource(f))
        return r.header(), r.metadata()


def lister_echeances(dossier_variable):
    """Échéances disponibles (entiers triés) et chemin du fichier de plus petite échéance."""
    pas = {}
    for nom in os.listdir(dossier_variable):
        m = RE_ECHEANCE.match(nom)
        if m:
            pas[int(m.group(1))] = nom
    return sorted(pas), pas


def _variable_complete(variable, steps, horizon):
    return steps == list(range(PREMIERE_ECHEANCE.get(variable, 0), horizon + 1))


def inventaire(dossier_tuiles):
    """{modèle: {tirage: {variable: (échéances, en-tête, métadonnées, mtime)}}} ; dossiers vides ignorés.
    mtime = date de modification (s, epoch) du fichier PMTiles le plus récent de la variable."""
    res = {}
    for modele in sorted(os.listdir(dossier_tuiles)):
        dm = os.path.join(dossier_tuiles, modele)
        if modele not in MODELES or not os.path.isdir(dm):
            continue
        for tirage in sorted(os.listdir(dm)):
            if not RE_TIRAGE.match(tirage):
                continue
            for variable in sorted(os.listdir(os.path.join(dm, tirage))):
                dv = os.path.join(dm, tirage, variable)
                if not os.path.isdir(dv):
                    continue
                steps, fichiers = lister_echeances(dv)
                if not steps:
                    continue
                entete, meta = lire_metadonnees(os.path.join(dv, fichiers[steps[0]]))
                mtime = max(os.path.getmtime(os.path.join(dv, f)) for f in fichiers.values())   # dernier fichier écrit
                res.setdefault(modele, {}).setdefault(tirage, {})[variable] = (steps, entete, meta, mtime)
    return res


def polygone_modele(modele, dossier_canon, tol=0.04):
    """(anneau, source) : polygone du domaine depuis un champ canonique `t2m`, sinon le rectangle."""
    dm = os.path.join(dossier_canon, modele) if dossier_canon else None
    if dm and os.path.isdir(dm):
        for tirage in sorted(os.listdir(dm), reverse=True):
            dt_ = os.path.join(dm, tirage)
            fichiers = sorted(f for f in os.listdir(dt_) if f.endswith(".npz")) if os.path.isdir(dt_) else []
            if not fichiers:
                continue
            # échéance 3 si présente (masque stable, voir poc-j1/synthese.md), sinon la première
            choix = "03.npz" if "03.npz" in fichiers else fichiers[0]
            d = np.load(os.path.join(dt_, choix))
            if "t2m" not in d.files:
                continue
            valide = ~np.isnan(d["t2m"])
            anneau = domaine.polygone_depuis_masque(d["lat"], d["lon"], valide, tol)
            return anneau, "canon/%s/%s/%s" % (modele, tirage, choix[:-4])
    w, s, e, n = MODELES[modele]["grid"]
    return [[w, n], [e, n], [e, s], [w, s], [w, n]], "grille (rectangle, champs canoniques absents)"


def construire(dossier_tuiles, dossier_canon=None, generated=None, tol=0.04):
    inv = inventaire(dossier_tuiles)
    generated = generated or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    modeles, variables = {}, {}
    for modele, tirages in inv.items():
        cfg = MODELES[modele]
        horizon = cfg["horizon_h"]
        toutes = sorted({v for t in tirages.values() for v in t})
        anneau, source = polygone_modele(modele, dossier_canon, tol)
        runs = []
        for tirage in sorted(tirages):
            vs = tirages[tirage]
            run = _iso(tirage)
            entrees = {}
            for v in toutes:
                if v not in vs:
                    continue
                steps = vs[v][0]
                entrees[v] = {"steps": steps, "complete": _variable_complete(v, steps, horizon)}
            tous = [s for e in entrees.values() for s in e["steps"]]
            # `traite` : dernier traitement CHEZ NOUS (date de modification des PMTiles), pas la publication chez Météo-France
            traite = dt.datetime.fromtimestamp(max(vs[v][3] for v in entrees), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            runs.append({
                "run": run, "id": tirage, "traite": traite,
                "valid_debut": _iso_plus(run, min(tous)), "valid_fin": _iso_plus(run, max(tous)),
                "complete": all(v in entrees and entrees[v]["complete"] for v in toutes),
                "path": "%s/%s" % (modele, tirage),
                "variables": entrees,
            })
        # zooms par variable : tirage le plus récent qui contient la variable
        zooms = {}
        for v in toutes:
            for tirage in sorted(tirages, reverse=True):
                if v in tirages[tirage]:
                    h, meta = tirages[tirage][v][1], tirages[tirage][v][2]
                    zooms[v] = [int(meta.get("minzoom", h["min_zoom"])), int(meta.get("maxzoom", h["max_zoom"]))]
                    break
        modeles[modele] = {
            "label": cfg["label"], "resolution_deg": cfg["resolution_deg"], "grid": cfg["grid"],
            "domain": {"polygon": anneau, "vertices": len(anneau) - 1, "source": source, "tolerance_deg": tol},
            "horizon_h": horizon, "zooms": zooms, "variables": toutes, "runs": runs,
        }
        # métadonnées de variable : du tirage le plus récent, tous modèles confondus
        for v in toutes:
            for tirage in sorted(tirages, reverse=True):
                if v in tirages[tirage]:
                    meta = tirages[tirage][v][2]
                    ancien = variables.get(v)
                    entree = {
                        "encoding": meta.get("encoding"), "scale": meta.get("scale"), "unit": meta.get("unit"),
                        "first_step": PREMIERE_ECHEANCE.get(v, 0),
                        # D14 : plage et pas propres à chaque fichier ; valeurs indicatives (fichier lu),
                        # le client relit min et quant_step dans chaque PMTiles.
                        "per_file_range": True, "min": meta.get("min"), "max": meta.get("max"),
                        "quant_step": meta.get("quant_step"),
                    }
                    if meta.get("components"):
                        entree["components"] = meta["components"]
                    if ancien is None:
                        entree["models"] = [modele]
                        variables[v] = entree
                    else:
                        ancien["models"].append(modele)
                        if ancien["encoding"] != entree["encoding"]:
                            raise ValueError("encodage de %s différent entre modèles : %s / %s"
                                             % (v, ancien["encoding"], entree["encoding"]))
                    break
    couches = {}
    for cle, (nom, v) in COUCHES.items():
        if v in variables:
            couches[cle] = {"nom": nom, "variable": v, "models": variables[v]["models"]}
    return {
        "version": VERSION, "generated": generated, "attribution": ATTRIBUTION,
        "file_template": MODELE_FICHIER,
        "models": modeles, "variables": variables, "couches": couches,
        "hors_raster": HORS_RASTER,
    }


def ecrire(manifest, sortie):
    os.makedirs(os.path.dirname(os.path.abspath(sortie)), exist_ok=True)
    tmp = sortie + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, sortie)
