# SPDX-License-Identifier: EUPL-1.2
"""Brique « isobares » du PoC J3 : champ `mslp` (Pa) -> isobares tous les 2 hPa et extrema A / D en GeoJSON.

Tout est en numpy (pas de scipy, pas de shapely) : lissage gaussien normalisé sur le masque,
marching squares vectorisé, assemblage des segments en lignes, Douglas-Peucker.
"""
