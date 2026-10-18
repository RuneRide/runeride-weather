# SPDX-License-Identifier: EUPL-1.2
"""Polygone du domaine réel d'un modèle, déduit du masque de valeurs valides.

Le domaine d'AROME n'est pas le rectangle de la grille : c'est un trapèze à bords
légèrement courbes (projection du modèle), plus étroit au sud (donnees-arome.md §4).
On le reconstruit à partir d'un champ canonique : pour chaque ligne de la grille,
première et dernière colonne valides (étendue extérieure), puis on simplifie chaque
bord par Douglas-Peucker. Ce choix (étendue par ligne plutôt qu'un contour de
masque complet) suffit car le domaine est convexe en pratique ; les rares lignes
non contiguës (quelques points isolés au bord) sont absorbées par l'étendue.
"""
import numpy as np


def _douglas_peucker(pts, tol):
    """Simplifie une polyligne ouverte (liste de (x, y)) ; conserve les extrémités."""
    pts = np.asarray(pts, dtype=float)
    n = len(pts)
    if n <= 2:
        return pts
    garde = np.zeros(n, dtype=bool)
    garde[0] = garde[-1] = True
    pile = [(0, n - 1)]
    while pile:
        a, b = pile.pop()
        if b <= a + 1:
            continue
        p, q = pts[a], pts[b]
        d = q - p
        norme = np.hypot(*d)
        seg = pts[a + 1:b]
        if norme == 0:
            dist = np.hypot(*(seg - p).T)
        else:
            dist = np.abs(d[0] * (seg[:, 1] - p[1]) - d[1] * (seg[:, 0] - p[0])) / norme
        k = int(np.argmax(dist))
        if dist[k] > tol:
            m = a + 1 + k
            garde[m] = True
            pile.append((a, m))
            pile.append((m, b))
    return pts[garde]


def polygone_depuis_masque(lat, lon, valide, tol=0.04, rayons=2880):
    """Anneau fermé [[lon, lat], ...] du domaine valide.

    lat : latitudes (une par ligne) ; lon : longitudes (une par colonne) ; valide : tableau
    booléen [len(lat), len(lon)] ; tol : écart maximal toléré entre le bord réel et le
    polygone simplifié, en degrés (0,04° ~ 1,6 maille AROME) ; rayons : nombre de directions.

    Méthode : le domaine est étoilé par rapport à son barycentre (trapèze à bords courbes,
    bord sud légèrement concave). On lance `rayons` demi-droites depuis le barycentre, on
    garde sur chacune le point valide le plus éloigné, puis on simplifie l'anneau par
    Douglas-Peucker. Une étendue par ligne ne conviendrait pas : le bord sud est concave
    (le centre du bord sud est plus haut que ses coins), un polygone « par ligne » le
    déborderait de ~1,5 % de sa surface.
    """
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    valide = np.asarray(valide, dtype=bool)
    ii, jj = np.nonzero(valide)
    if ii.size == 0:
        raise ValueError("masque vide : aucun point valide")
    cy, cx = lat[ii].mean(), lon[jj].mean()
    dlat = abs(lat[1] - lat[0]) if len(lat) > 1 else 1.0
    dlon = abs(lon[1] - lon[0]) if len(lon) > 1 else 1.0
    lat0, lon0 = lat[0], lon[0]
    pas_lat = (lat[-1] - lat[0]) / max(1, len(lat) - 1)
    pas_lon = (lon[-1] - lon[0]) / max(1, len(lon) - 1)
    rmax = float(np.hypot(abs(lat[-1] - lat[0]), abs(lon[-1] - lon[0])))
    r = np.arange(0.0, rmax, min(dlat, dlon) * 0.5)
    anneau = []
    for th in np.linspace(0, 2 * np.pi, rayons, endpoint=False):
        y = cy + r * np.sin(th)
        x = cx + r * np.cos(th)
        a = np.rint((y - lat0) / pas_lat).astype(int)
        b = np.rint((x - lon0) / pas_lon).astype(int)
        dedans = (a >= 0) & (a < len(lat)) & (b >= 0) & (b < len(lon))
        ok = np.zeros(r.shape, dtype=bool)
        ok[dedans] = valide[a[dedans], b[dedans]]
        k = np.flatnonzero(ok)
        k = k[-1]
        anneau.append((lon[b[k]], lat[a[k]]))
    # DP sur l'anneau : deux chaînes coupées aux points d'indice 0 et rayons/2
    m = rayons // 2
    c1 = _douglas_peucker(anneau[:m + 1], tol)
    c2 = _douglas_peucker(anneau[m:] + [anneau[0]], tol)
    pts = [tuple(p) for p in c1] + [tuple(p) for p in c2[1:]]
    sortie = []
    for p in pts:
        if not sortie or p != sortie[-1]:
            sortie.append(p)
    if sortie[0] != sortie[-1]:
        sortie.append(sortie[0])
    return [[round(float(x), 4), round(float(y), 4)] for x, y in sortie]


def point_dans_polygone(x, y, anneau):
    """Test pair-impair (anneau fermé de [x, y])."""
    dedans = False
    for (x1, y1), (x2, y2) in zip(anneau[:-1], anneau[1:]):
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            dedans = not dedans
    return dedans


def couverture(anneau, lat, lon, valide):
    """Contrôle de qualité : (part des points valides dans le polygone, part du polygone valide)."""
    lat = np.asarray(lat, dtype=float)
    lon = np.asarray(lon, dtype=float)
    valide = np.asarray(valide, dtype=bool)
    # sous-échantillonnage pour rester rapide sur la grille HD
    pas_i = max(1, len(lat) // 180)
    pas_j = max(1, len(lon) // 280)
    dans = np.zeros((len(range(0, len(lat), pas_i)), len(range(0, len(lon), pas_j))), dtype=bool)
    for a, i in enumerate(range(0, len(lat), pas_i)):
        for b, j in enumerate(range(0, len(lon), pas_j)):
            dans[a, b] = point_dans_polygone(lon[j], lat[i], anneau)
    v = valide[::pas_i, ::pas_j]
    return float((v & dans).sum() / max(1, v.sum())), float((v & dans).sum() / max(1, dans.sum()))
