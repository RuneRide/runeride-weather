# SPDX-License-Identifier: EUPL-1.2
"""Extrema de pression : anticyclones (« A ») et dépressions (« D »), sélectionnés par PROÉMINENCE.

Proéminence d'un maximum = hauteur dont il faut descendre avant de pouvoir rejoindre un maximum plus
élevé (ou le bord du domaine valide). Un extremum de proéminence >= un pas d'isobare est entouré d'au
moins une isobare fermée : c'est le critère retenu pour qu'un « A » ou un « D » corresponde à quelque
chose que l'on voit sur la carte. Calcul exact par inondation décroissante et union-find sur une grille
sous-échantillonnée (le champ est déjà lissé : le sous-échantillonnage ne perd rien d'utile).
"""
import numpy as np

VOISINS8 = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


def proeminences_maxima(F, pas=1):
    """Proéminence de tous les maxima locaux de `F` (NaN = hors domaine).

    Renvoie une liste de (ligne, colonne, valeur, proéminence) en coordonnées de F.
    Un maximum qui touche le bord du domaine valide (cellule voisine de NaN ou du bord de la grille)
    est « tué » à ce niveau : il pourrait n'être que le flanc d'un maximum situé hors du domaine.
    Le maximum global, jamais tué, reçoit pour proéminence (sa valeur - le minimum du champ).
    """
    G = F[::pas, ::pas]
    ny, nx = G.shape
    ok = np.isfinite(G)
    # cellule « de bord » : hors grille voisine, ou voisine d'un NaN
    p = np.pad(ok, 1, constant_values=False)
    bord = ok & ~(p[:-2, 1:-1] & p[2:, 1:-1] & p[1:-1, :-2] & p[1:-1, 2:] &
                  p[:-2, :-2] & p[:-2, 2:] & p[2:, :-2] & p[2:, 2:])
    flat = np.where(ok, G, -np.inf).ravel()
    ordre = np.argsort(-flat, kind="stable")
    n_ok = int(ok.sum())
    ordre = ordre[:n_ok].tolist()
    val = flat.tolist()
    bord_l = bord.ravel().tolist()
    parent = list(range(ny * nx))
    sommet = list(range(ny * nx))          # sommet (indice plat) de chaque composante, indexé par racine
    actif = bytearray(ny * nx)
    infini = bytearray(ny * nx)            # composante rattachée au bord : sommet considéré infini
    prom = {}

    def racine(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def tuer(r, niveau):
        """Fixe la proéminence du sommet de la composante r (si pas déjà infinie)."""
        s = sommet[r]
        if s not in prom:
            prom[s] = val[s] - niveau

    for c in ordre:
        actif[c] = 1
        v = val[c]
        r_c = c
        i, j = divmod(c, nx)
        if bord_l[c]:
            infini[c] = 1
            prom[c] = 0.0                                   # un sommet de bord n'est pas un extremum
        for di, dj in VOISINS8:
            a, b = i + di, j + dj
            if 0 <= a < ny and 0 <= b < nx:
                q = a * nx + b
                if not actif[q]:
                    continue
                r_q = racine(q)
                r_c = racine(c)
                if r_q == r_c:
                    continue
                # fusion : la composante au sommet le plus bas (ou non infinie) meurt
                inf_q, inf_c = infini[r_q], infini[r_c]
                if inf_q and inf_c:
                    gagnant, perdant = r_q, r_c
                elif inf_q:
                    gagnant, perdant = r_q, r_c
                    tuer(r_c, v)
                elif inf_c:
                    gagnant, perdant = r_c, r_q
                    tuer(r_q, v)
                elif val[sommet[r_q]] >= val[sommet[r_c]]:
                    gagnant, perdant = r_q, r_c
                    tuer(r_c, v)
                else:
                    gagnant, perdant = r_c, r_q
                    tuer(r_q, v)
                parent[perdant] = gagnant
                infini[gagnant] = 1 if (inf_q or inf_c) else infini[gagnant]
    # survivants (maximum global) : proéminence vis-à-vis du minimum
    mn = float(np.nanmin(G))
    for c in ordre:
        if parent[c] == c and sommet[c] not in prom and not infini[c]:
            prom[sommet[c]] = val[sommet[c]] - mn
    sortie = []
    for s, pr in prom.items():
        i, j = divmod(s, nx)
        sortie.append((i * pas, j * pas, val[s], pr))
    return sortie


def detecter(F, seuil, pas=3, fenetre=None):
    """Extrema du champ lissé `F` (hPa) de proéminence >= `seuil`.

    Renvoie (maxima, minima), chacun une liste de dicts {ligne, colonne, valeur, proeminence},
    triés par proéminence décroissante. La position est affinée sur la grille pleine :
    maximum (ou minimum) de F dans la fenêtre de ±`pas` mailles autour du sommet sous-échantillonné.
    """
    ny, nx = F.shape
    out = []
    for signe in (1, -1):
        G = F if signe == 1 else -F
        liste = []
        for (i, j, v, pr) in proeminences_maxima(G, pas):
            if pr < seuil:
                continue
            i0, i1 = max(0, i - pas), min(ny, i + pas + 1)
            j0, j1 = max(0, j - pas), min(nx, j + pas + 1)
            blo = np.where(np.isfinite(G[i0:i1, j0:j1]), G[i0:i1, j0:j1], -np.inf)
            k = int(np.argmax(blo))
            a, b = divmod(k, j1 - j0)
            liste.append({"ligne": i0 + a, "colonne": j0 + b, "valeur": float(signe * blo[a, b]),
                          "proeminence": float(pr)})
        liste.sort(key=lambda d: -d["proeminence"])
        out.append(liste)
    return out[0], out[1]
