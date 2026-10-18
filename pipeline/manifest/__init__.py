# SPDX-License-Identifier: EUPL-1.2
"""Brique « manifest » du jalon J3 : inventaire des tuiles PMTiles -> manifest.json.

    python3 -m pipeline.manifest --tiles out/tiles-reduit2 --out out/manifest/manifest.json
"""
import os
import sys

# Le paquet `pmtiles` est installé hors dépôt : PYTHONPATH le fournit, sinon on
# cherche un dossier indiqué par RUNERIDE_PYLIB (même convention que pipeline.tiling).
for _p in os.environ.get("RUNERIDE_PYLIB", "").split(os.pathsep):
    if _p and _p not in sys.path:
        sys.path.append(_p)
