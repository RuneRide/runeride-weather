# SPDX-License-Identifier: EUPL-1.2
"""Profils de tuilage : variantes de réduction du poids (étape 2 du PoC J1).

Le profil `contrat` (défaut) est celui du contrat §3. Les autres sont des OPTIONS mesurées
dans docs/poc-j1/tuilage.md ; aucune n'est appliquée au contrat sans décision.
"""
from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class Profil:
    nom: str
    description: str = ""
    wind_mode: str = "wind12"          # "wind12" (contrat) ou "wind8" (u dans R, v dans G)
    wind_drop_bits: int = 0            # wind12 grossier : arrondi à 2**k codes
    wind_vmax: float = 64.0            # demi-plage du vent (wind8)
    zmax: dict = field(default_factory=dict)         # variable -> zoom maximal stocké
    no_border_from: int = 99           # zoom à partir duquel la tuile est stockée en 256 px (0 bordure)
    levels: dict = field(default_factory=dict)       # variable -> nombre d'intervalles (254 = contrat)
    ranges: dict = field(default_factory=dict)       # variable -> (min, max) resserrée (défaut : table du contrat)
    lossy: dict = field(default_factory=dict)        # variable -> qualité WebP avec perte (tuiles sans trou seulement)


CONTRAT = Profil("contrat", "contrat §3 : wind12, z5-8 / z3-7, bordure 1 px, 8 bits, sans perte")

PROFILS = {p.nom: p for p in [
    CONTRAT,
    Profil("vent8", "vent en 8 bits par composante, plage ±64 m/s (pas 0,50 m/s)", wind_mode="wind8", wind_vmax=64.0),
    Profil("vent8-48", "vent en 8 bits par composante, plage ±48 m/s (pas 0,38 m/s)", wind_mode="wind8", wind_vmax=48.0),
    Profil("vent12-gros2", "vent wind12 arrondi à 4 codes (pas 0,125 m/s), même format", wind_drop_bits=2),
    Profil("vent12-gros3", "vent wind12 arrondi à 8 codes (pas 0,25 m/s), même format", wind_drop_bits=3),
    Profil("vent-z7", "pas de zoom 8 pour le vent (surzoom côté client)", zmax={"wind10": 7}),
    Profil("sans-bordure-z8", "tuiles 256 px sans bordure au zoom 8", no_border_from=8),
    Profil("lossy-t2m", "t2m en WebP avec perte (qualité 95) sur les tuiles sans trou", lossy={"t2m": 95}),
    Profil("bits7", "rh2m, cloud_total, gust10 sur 7 bits (126 intervalles)",
           levels={"rh2m": 126, "cloud_total": 126, "gust10": 126}),
    Profil("pas-gros", "rh2m, cloud_total pas 1 %, gust10 pas 0,5 m/s, t2m pas 0,5 K",
           levels={"rh2m": 100, "cloud_total": 100, "gust10": 128, "t2m": 200}),
    Profil("z7-tout", "aucun zoom 8 (tout plafonné à z7, surzoom côté client)",
           zmax={v: 7 for v in ("t2m", "gust10", "rh2m", "cloud_total", "snow_fraction", "precip_rate", "cape", "wind10")}),
]}


_PAS_GROS = {"rh2m": 100, "cloud_total": 100, "gust10": 128, "t2m": 200}
PROFILS["reduit-z8"] = Profil(
    "reduit-z8", "vent8 ±48 m/s + pas plus gros (t2m 0,5 K, rh2m/cloud 1 %, rafale 0,5 m/s), tous zooms conservés",
    wind_mode="wind8", wind_vmax=48.0, levels=_PAS_GROS)
PROFILS["reduit"] = Profil(
    "reduit", "RECOMMANDÉ : reduit-z8 + zoom 8 conservé seulement pour precip_rate et snow_fraction",
    wind_mode="wind8", wind_vmax=48.0, levels=_PAS_GROS,
    zmax={v: 7 for v in ("t2m", "gust10", "rh2m", "cloud_total", "cape", "wind10")})


# reduit2 : t2m et rafale à 0,25 (254 niveaux sur 63,5 K / 63,5 m/s). La plage de t2m, resserrée, est
# propre à la saison : reduit2 (automne/printemps, -30..+33,5 degC), reduit2-ete (-15..+48,5), reduit2-hiver (-40..+23,5).
_L2 = dict(_PAS_GROS, t2m=254, gust10=254)
_Z7 = {v: 7 for v in ("t2m", "gust10", "rh2m", "cloud_total", "cape", "wind10")}
for _nom, _t0 in (("reduit2", 243.15), ("reduit2-ete", 258.15), ("reduit2-hiver", 233.15)):
    PROFILS[_nom] = Profil(
        _nom, "reduit + t2m pas 0,25 K sur [%.2f, %.2f] K, gust10 pas 0,25 m/s sur [0, 63,5]" % (_t0, _t0 + 63.5),
        wind_mode="wind8", wind_vmax=48.0, levels=_L2, zmax=_Z7,
        ranges={"t2m": (_t0, _t0 + 63.5), "gust10": (0.0, 63.5)})


def get(nom):
    if nom not in PROFILS:
        raise SystemExit("profil inconnu : %s (disponibles : %s)" % (nom, ", ".join(PROFILS)))
    return PROFILS[nom]


def combine(nom, *, base=CONTRAT, **kw):
    return replace(base, nom=nom, **kw)
