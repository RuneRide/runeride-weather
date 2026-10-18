# SPDX-License-Identifier: EUPL-1.2
"""Contours d'un champ 2D : lissage, marching squares, assemblage en lignes, simplification.

Convention : le champ `F` est un tableau [ny, nx] indexé (ligne i, colonne j) ; les positions
des sommets sont en coordonnées d'INDEX (ligne, colonne) flottantes. La conversion en
longitude / latitude se fait à part (`vers_lonlat`), car la latitude des champs canoniques décroît.
"""
import numpy as np

# ----------------------------------------------------------------------------- unités


def pa_vers_hpa(p):
    """Pascal -> hectopascal (le champ canonique `mslp` est en Pa)."""
    return np.asarray(p, dtype=np.float64) / 100.0


# ----------------------------------------------------------------------------- lissage


def noyau_gaussien(sigma):
    """Noyau gaussien 1D normalisé, rayon 3 sigma (99,7 % de la masse)."""
    r = max(1, int(np.ceil(3.0 * sigma)))
    x = np.arange(-r, r + 1, dtype=np.float64)
    k = np.exp(-0.5 * (x / sigma) ** 2)
    return k / k.sum()


def _convoluer_axe(a, k, axe):
    """Convolution 1D le long d'un axe, bords remplis de zéros (la normalisation est faite par l'appelant)."""
    r = len(k) // 2
    pad = [(0, 0), (0, 0)]
    pad[axe] = (r, r)
    p = np.pad(a, pad)
    n = a.shape[axe]
    sortie = np.zeros_like(a)
    for m, c in enumerate(k):
        sortie += c * (p[m:m + n, :] if axe == 0 else p[:, m:m + n])
    return sortie


def lisser(champ, sigma):
    """Lissage gaussien (écart-type `sigma` en mailles) avec convolution NORMALISÉE par le masque.

    Les NaN sont exclus du calcul : lissé = conv(F·m) / conv(m), avec m = 1 où F est défini.
    Aucune valeur factice n'est injectée en bordure du trapèze, donc pas de pente artificielle
    (contrairement à un remplissage par 0 ou par la moyenne). Les NaN restent NaN en sortie.
    `sigma` <= 0 : renvoie une copie.
    """
    F = np.asarray(champ, dtype=np.float64)
    if sigma <= 0:
        return F.copy()
    m = np.isfinite(F)
    k = noyau_gaussien(sigma)
    num = np.where(m, F, 0.0)
    den = m.astype(np.float64)
    for axe in (0, 1):
        num = _convoluer_axe(num, k, axe)
        den = _convoluer_axe(den, k, axe)
    with np.errstate(invalid="ignore", divide="ignore"):
        sortie = num / den
    sortie[~m] = np.nan
    return sortie


# ----------------------------------------------------------------------------- marching squares

# Arêtes d'une cellule : 0 = haut (T), 1 = droite (R), 2 = bas (B), 3 = gauche (L).
# Indice de cas : coin haut-gauche a = 1, haut-droit b = 2, bas-droit c = 4, bas-gauche d = 8 (bit à 1 si F >= niveau).
_T, _R, _B, _L = 0, 1, 2, 3
# _TABLE[variante][cas] = deux segments (paire d'arêtes), (-1, -1) = absent.
# Variante 0 = centre de la cellule sous le niveau ; variante 1 = au-dessus (ne diffère que pour les selles 5 et 10).
_NUL = ((-1, -1), (-1, -1))
_BASE = {
    0: _NUL, 15: _NUL,
    1: (((_L, _T), (-1, -1))), 2: (((_T, _R), (-1, -1))), 3: (((_L, _R), (-1, -1))),
    4: (((_R, _B), (-1, -1))), 6: (((_T, _B), (-1, -1))), 7: (((_L, _B), (-1, -1))),
    8: (((_B, _L), (-1, -1))), 9: (((_T, _B), (-1, -1))), 11: (((_R, _B), (-1, -1))),
    12: (((_L, _R), (-1, -1))), 13: (((_T, _R), (-1, -1))), 14: (((_L, _T), (-1, -1))),
}
_TABLE = np.full((2, 16, 2, 2), -1, dtype=np.int64)
for _v in (0, 1):
    for _c, _s in _BASE.items():
        _TABLE[_v, _c] = _s
# Selle 5 (a et c hauts) : centre haut -> les coins bas b et d sont isolés ; centre bas -> a et c sont isolés.
_TABLE[1, 5] = ((_T, _R), (_B, _L))
_TABLE[0, 5] = ((_L, _T), (_R, _B))
# Selle 10 (b et d hauts) : centre haut -> a et c isolés ; centre bas -> b et d isolés.
_TABLE[1, 10] = ((_L, _T), (_R, _B))
_TABLE[0, 10] = ((_T, _R), (_B, _L))


def cellules_valides(valide, marge=0):
    """Cellule [ny-1, nx-1] valide si ses quatre coins le sont ; `marge` mailles d'érosion supplémentaires.

    Une cellule contenant un coin NaN n'engendre jamais de segment : c'est ce qui évite les fausses
    lignes le long du bord du domaine masqué (un NaN n'est ni « haut » ni « bas »).
    """
    v = valide[:-1, :-1] & valide[:-1, 1:] & valide[1:, 1:] & valide[1:, :-1]
    for _ in range(int(marge)):
        p = np.pad(v, 1, constant_values=True)   # le bord de la grille n'érode pas (seuls les voisins invalides comptent)
        v = v & p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:]
    return v


def segments(F, niveau, cell_ok):
    """Segments de marching squares pour un niveau : (e0, e1) identifiants d'ARÊTES (un par extrémité).

    Identifiant d'arête : horizontale H(i,j) (entre (i,j) et (i,j+1)) = i·nx + j ;
    verticale V(i,j) (entre (i,j) et (i+1,j)) = ny·nx + i·nx + j. Deux cellules voisines
    partagent la même arête, donc le même identifiant : l'assemblage s'en sert comme nœud.
    """
    ny, nx = F.shape
    with np.errstate(invalid="ignore"):
        haut = F >= niveau                      # NaN >= x vaut False
    cas = (haut[:-1, :-1] * 1 + haut[:-1, 1:] * 2 + haut[1:, 1:] * 4 + haut[1:, :-1] * 8).astype(np.int64)
    cas = np.where(cell_ok, cas, 0)
    ii, jj = np.nonzero((cas != 0) & (cas != 15))
    if ii.size == 0:
        return np.empty(0, np.int64), np.empty(0, np.int64)
    c = cas[ii, jj]
    centre = 0.25 * (F[ii, jj] + F[ii, jj + 1] + F[ii + 1, jj + 1] + F[ii + 1, jj])
    var = (centre >= niveau).astype(np.int64)
    paires = _TABLE[var, c]                                   # [n, 2 segments, 2 extrémités]
    e0, e1 = [], []
    for s in (0, 1):
        a, b = paires[:, s, 0], paires[:, s, 1]
        ok = a >= 0
        e0.append(_id_arete(a[ok], ii[ok], jj[ok], ny, nx))
        e1.append(_id_arete(b[ok], ii[ok], jj[ok], ny, nx))
    return np.concatenate(e0), np.concatenate(e1)


def _id_arete(lettre, i, j, ny, nx):
    """Identifiant global de l'arête `lettre` (T/R/B/L) de la cellule (i, j)."""
    return np.select(
        [lettre == _T, lettre == _B, lettre == _L, lettre == _R],
        [i * nx + j, (i + 1) * nx + j, ny * nx + i * nx + j, ny * nx + i * nx + j + 1])


def positions_noeuds(F, niveau, ids):
    """Position (ligne, colonne) flottante du croisement de `niveau` sur chaque arête `ids` (interpolation linéaire)."""
    ny, nx = F.shape
    ids = np.asarray(ids)
    horiz = ids < ny * nx
    k = np.where(horiz, ids, ids - ny * nx)
    i, j = k // nx, k % nx
    i2 = np.where(horiz, i, i + 1)
    j2 = np.where(horiz, j + 1, j)
    a, b = F[i, j], F[i2, j2]
    t = (niveau - a) / (b - a)          # a et b sont de part et d'autre du niveau : b - a != 0
    return np.where(horiz, i, i + t), np.where(horiz, j + t, j)


def assembler(e0, e1):
    """Assemble des segments (paires d'identifiants de nœuds) en lignes continues.

    Renvoie une liste de (suite d'identifiants de nœuds, ferme). Un nœud appartient à au plus deux
    segments : une ligne ouverte commence et finit aux nœuds de degré 1 (bord du domaine valide ou
    de la grille), une ligne fermée revient à son point de départ (premier = dernier nœud).
    """
    e0 = np.asarray(e0, dtype=np.int64)
    e1 = np.asarray(e1, dtype=np.int64)
    n_seg = len(e0)
    if n_seg == 0:
        return []
    noeuds, inv = np.unique(np.concatenate([e0, e1]), return_inverse=True)
    a, b = inv[:n_seg], inv[n_seg:]
    n = len(noeuds)
    src = np.concatenate([a, b])
    dst = np.concatenate([b, a])
    ordre = np.argsort(src, kind="stable")
    src, dst = src[ordre], dst[ordre]
    deg = np.bincount(src, minlength=n)
    if deg.max() > 2:
        raise ValueError("nœud de degré > 2 : segments incohérents")
    deb = np.searchsorted(src, np.arange(n))
    v0 = dst[deb]
    v1 = np.where(deg == 2, dst[np.minimum(deb + 1, len(dst) - 1)], -1)
    v0, v1, deg_l = v0.tolist(), v1.tolist(), deg.tolist()
    vu = bytearray(n)
    lignes = []

    def marcher(depart):
        chaine = [depart]
        vu[depart] = 1
        prec, cur = -1, depart
        while True:
            nxt = v0[cur] if v0[cur] != prec else v1[cur]
            if nxt < 0 or nxt == depart:
                return chaine, nxt == depart
            if vu[nxt]:
                return chaine, False
            vu[nxt] = 1
            chaine.append(nxt)
            prec, cur = cur, nxt

    for d in np.nonzero(deg == 1)[0].tolist():       # lignes ouvertes
        if not vu[d]:
            ch, _ = marcher(d)
            lignes.append((noeuds[ch], False))
    for d in range(n):                                 # anneaux restants
        if not vu[d]:
            ch, ferme = marcher(d)
            if ferme:
                ch.append(d)
            lignes.append((noeuds[ch], ferme))
    return lignes


def contour(F, niveau, cell_ok):
    """Lignes de niveau `niveau` : liste de (points [N,2] (ligne, colonne), ferme)."""
    e0, e1 = segments(F, niveau, cell_ok)
    sortie = []
    for ids, ferme in assembler(e0, e1):
        r, c = positions_noeuds(F, niveau, ids)
        sortie.append((np.column_stack([r, c]), ferme))
    return sortie


def niveaux_hpa(F, pas=2.0):
    """Niveaux (multiples de `pas`) strictement compris dans l'étendue du champ."""
    lo, hi = np.nanmin(F), np.nanmax(F)
    return np.arange(np.ceil(lo / pas) * pas, np.floor(hi / pas) * pas + pas / 2, pas)


# ----------------------------------------------------------------------------- géométrie


def vers_lonlat(pts, lat, lon):
    """(ligne, colonne) flottants -> (lon, lat) par interpolation linéaire des axes (lat décroissante admise)."""
    idx_l = np.arange(len(lat), dtype=np.float64)
    idx_c = np.arange(len(lon), dtype=np.float64)
    return np.column_stack([np.interp(pts[:, 1], idx_c, lon.astype(np.float64)),
                            np.interp(pts[:, 0], idx_l, lat.astype(np.float64))])


def vers_km(lonlat, lat0=46.5):
    """(lon, lat) -> plan local en km (équirectangulaire centré sur lat0) : sert aux tolérances."""
    k = np.pi / 180.0 * 6371.0
    return np.column_stack([lonlat[:, 0] * k * np.cos(np.radians(lat0)), lonlat[:, 1] * k])


def longueur_km(lonlat):
    """Longueur d'une ligne (km), plan local."""
    if len(lonlat) < 2:
        return 0.0
    p = vers_km(lonlat)
    return float(np.hypot(*np.diff(p, axis=0).T).sum())


def douglas_peucker(p, tol):
    """Douglas-Peucker itératif sur un tableau [N,2] (mêmes unités que `tol`). Renvoie les INDICES gardés (triés)."""
    n = len(p)
    if n <= 2 or tol <= 0:
        return np.arange(n)
    garde = np.zeros(n, dtype=bool)
    garde[0] = garde[-1] = True
    pile = [(0, n - 1)]
    while pile:
        a, b = pile.pop()
        if b - a < 2:
            continue
        seg = p[b] - p[a]
        L = np.hypot(*seg)
        mid = p[a + 1:b] - p[a]
        if L == 0:
            d = np.hypot(mid[:, 0], mid[:, 1])
        else:
            d = np.abs(seg[0] * mid[:, 1] - seg[1] * mid[:, 0]) / L
        k = int(np.argmax(d))
        if d[k] > tol:
            m = a + 1 + k
            garde[m] = True
            pile.append((a, m))
            pile.append((m, b))
    return np.nonzero(garde)[0]


def simplifier(lonlat, ferme, tol_km):
    """Simplifie une ligne (lon, lat) par Douglas-Peucker de tolérance `tol_km`. Un anneau est coupé au point le plus éloigné du départ."""
    if tol_km <= 0 or len(lonlat) <= 3:
        return lonlat
    p = vers_km(lonlat)
    if ferme and np.allclose(lonlat[0], lonlat[-1]):
        k = int(np.argmax(np.hypot(*(p - p[0]).T)))
        i1 = douglas_peucker(p[:k + 1], tol_km)
        i2 = douglas_peucker(p[k:], tol_km) + k
        idx = np.concatenate([i1, i2[1:]])
    else:
        idx = douglas_peucker(p, tol_km)
    return lonlat[idx]
