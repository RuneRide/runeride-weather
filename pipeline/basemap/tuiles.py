# SPDX-License-Identifier: EUPL-1.2
"""Logique pure de l'extraction : tuiles recoupant une boîte, filtrage par zoom,
fusion des plages d'octets, choix du build quotidien. Aucune entrée/sortie, aucun réseau.

Les identifiants de tuile sont ceux de PMTiles v3 (courbe de Hilbert, zooms concaténés) ;
on réutilise le calcul de la bibliothèque `pmtiles`.
"""
import bisect
import datetime
import math

from pmtiles.tile import zxy_to_tileid

LAT_MAX = 85.0511287798066  # limite de la projection Web Mercator


def valider_bbox(bbox):
    """Contrôle (ouest, sud, est, nord) en degrés ; l'antiméridien n'est pas géré."""
    ouest, sud, est, nord = bbox
    if not (-180 <= ouest < est <= 180):
        raise ValueError("bbox : il faut -180 <= ouest < est <= 180 (antiméridien non géré)")
    if not (-90 <= sud < nord <= 90):
        raise ValueError("bbox : il faut -90 <= sud < nord <= 90")
    return (float(ouest), float(sud), float(est), float(nord))


def lonlat_vers_tuile(lon, lat, z):
    """Tuile (x, y) du niveau z contenant le point ; la latitude est bornée au Web Mercator."""
    n = 1 << z
    lat = max(-LAT_MAX, min(LAT_MAX, lat))
    x = (lon + 180.0) / 360.0 * n
    r = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(r)) / math.pi) / 2.0 * n
    return (min(n - 1, max(0, int(math.floor(x)))), min(n - 1, max(0, int(math.floor(y)))))


def etendue_zoom(bbox, z):
    """(x_min, x_max, y_min, y_max) inclusifs des tuiles du zoom z recoupant la boîte.

    Une tuile qui touche la boîte seulement par son bord est incluse (choix prudent)."""
    ouest, sud, est, nord = valider_bbox(bbox)
    x0, y1 = lonlat_vers_tuile(ouest, sud, z)
    x1, y0 = lonlat_vers_tuile(est, nord, z)
    return (x0, x1, y0, y1)


def compter_tuiles(bbox, zoom_min, zoom_max):
    """Nombre de tuiles adressées (hors dédoublonnage du contenu) entre deux zooms inclus."""
    total = 0
    for z in range(zoom_min, zoom_max + 1):
        x0, x1, y0, y1 = etendue_zoom(bbox, z)
        total += (x1 - x0 + 1) * (y1 - y0 + 1)
    return total


def ids_voulus(bbox, zoom_min, zoom_max):
    """Liste triée des identifiants PMTiles des tuiles voulues (zoom_min..zoom_max inclus)."""
    if zoom_min > zoom_max:
        raise ValueError("zoom_min > zoom_max")
    ids = []
    for z in range(zoom_min, zoom_max + 1):
        x0, x1, y0, y1 = etendue_zoom(bbox, z)
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                ids.append(zxy_to_tileid(z, x, y))
    ids.sort()
    return ids


def zoom_de_id(tile_id):
    """Zoom d'un identifiant de tuile PMTiles."""
    return ((3 * tile_id + 1).bit_length() - 1) // 2


def plage_ids_du_zoom(z):
    """[premier, dernier] identifiants du niveau z."""
    debut = ((1 << (2 * z)) - 1) // 3
    return (debut, debut + (1 << (2 * z)) - 1)


def intersecte(ids_tries, bas, haut):
    """Vrai si un identifiant voulu est dans [bas, haut[ (haut exclu ; None = sans borne)."""
    i = bisect.bisect_left(ids_tries, bas)
    if i >= len(ids_tries):
        return False
    return haut is None or ids_tries[i] < haut


def ids_dans(ids_tries, bas, haut):
    """Identifiants voulus dans [bas, haut[."""
    i = bisect.bisect_left(ids_tries, bas)
    j = len(ids_tries) if haut is None else bisect.bisect_left(ids_tries, haut)
    return ids_tries[i:j]


def fusionner_plages(blocs, ecart_max, taille_max):
    """Regroupe des blocs (offset, longueur) triés en plages d'octets à demander d'un coup.

    Deux blocs sont fusionnés si l'écart qui les sépare est <= ecart_max octets et si la
    plage ne dépasse pas taille_max (un bloc seul plus gros que taille_max reste seul).
    Retourne une liste de (offset, longueur, [blocs]). Les blocs doivent être uniques."""
    plages = []
    courant = None
    for off, lon in sorted(blocs):
        if courant is not None:
            fin = courant[0] + courant[1]
            if off < fin:
                raise ValueError("blocs chevauchants")
            if off - fin <= ecart_max and (off + lon - courant[0]) <= taille_max:
                courant[1] = off + lon - courant[0]
                courant[2].append((off, lon))
                continue
        courant = [off, lon, [(off, lon)]]
        plages.append(courant)
    return [tuple(p) for p in plages]


def url_build(date, base="https://build.protomaps.com"):
    """URL du build quotidien d'une date (datetime.date)."""
    return f"{base}/{date:%Y%m%d}.pmtiles"


def choisir_build(aujourdhui, sonde, jours_max=14, base="https://build.protomaps.com"):
    """Build quotidien le plus récent : essaie `aujourdhui`, puis les jours précédents.

    `sonde(url)` renvoie le code HTTP ; le premier 200 gagne. Retourne (date, url).
    Lève LookupError si rien n'est trouvé sur `jours_max` jours."""
    for k in range(jours_max):
        d = aujourdhui - datetime.timedelta(days=k)
        u = url_build(d, base)
        if sonde(u) == 200:
            return d, u
    raise LookupError(f"aucun build trouvé sur les {jours_max} derniers jours")


def bbox_vers_e7(bbox):
    """(min_lon, min_lat, max_lon, max_lat) en 1e-7 degré, entiers pour l'en-tête PMTiles."""
    return tuple(int(round(v * 1e7)) for v in bbox)


def zoom_centre(bbox, zoom_max):
    """Zoom d'ouverture conseillé : le plus grand zoom où la boîte tient en ~1024 px de large."""
    largeur = max(bbox[2] - bbox[0], 1e-6)
    z = int(math.floor(math.log2(360.0 * 1024 / 256 / largeur)))
    return max(0, min(zoom_max, z))
