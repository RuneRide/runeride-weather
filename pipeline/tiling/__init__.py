# SPDX-License-Identifier: EUPL-1.2
"""Brique « tuilage » du PoC J1 : champs canoniques .npz -> tuiles PMTiles v3."""
import os
import sys

# Le paquet `pmtiles` est installé hors dépôt : PYTHONPATH le fournit, sinon on
# cherche un dossier indiqué par RUNERIDE_PYLIB.
for _p in os.environ.get("RUNERIDE_PYLIB", "").split(os.pathsep):
    if _p and _p not in sys.path:
        sys.path.append(_p)
