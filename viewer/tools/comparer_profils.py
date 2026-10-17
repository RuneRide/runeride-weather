# SPDX-License-Identifier: EUPL-1.2
"""Comparaison visuelle des profils de tuiles `contrat` et `reduit` (décision D13).

  python3 viewer/tools/comparer_profils.py scenario DOSSIER   # écrit un scénario par profil pour pilote_chrome.mjs
  python3 viewer/tools/comparer_profils.py analyse DOSSIER    # mesure les différences d'images (JSON + tableau)

Mesures : écart absolu moyen par canal (0..255) et pourcentage de pixels dont la différence de couleur
dépasse un seuil visible, en ΔE CIE76 (sRGB -> Lab, D65) : 2,3 = différence juste perceptible (JND) pour deux
aplats voisins ; 5 = différence nette à l'oeil. Dénominateur : pixels « de données » (là où la couche change
le rendu du fond, mesuré avec une capture d'opacité 0), pour ne pas diluer l'écart dans le fond de carte.
"""
import json
import os
import sys

import numpy as np
from PIL import Image

URL = "http://127.0.0.1:8187/"
VUES = {  # nom -> (centre lat, lng, zoom carte)
    "fr": (46.6, 2.5, 5.0), "alpes": (45.9, 7.0, 8.2), "alpesN": (45.4, 6.5, 8.5),
    "provence": (43.6, 5.4, 8.2), "nuages": (45.5, -2.3, 8.2),
}
CAPTURES = (
    [("fr", c, t) for t in (6, 24, 48) for c in ("t2m", "vent", "rafales", "humidite", "nuages", "pluie", "cape", "neige")]
    + [("alpes", c, 24) for c in ("t2m", "vent", "rafales", "humidite")]
    + [("alpesN", c, 48) for c in ("neige", "pluie", "t2m")]
    + [("provence", c, 24) for c in ("vent", "t2m", "cape")]
    + [("nuages", c, 48) for c in ("nuages", "pluie", "humidite")]
    + [("fr", "ventc", t) for t in (6, 24, 48)] + [("alpes", "ventc", 24), ("provence", "ventc", 24)]  # vent sans flèches
)


def nom(vue, couche, t):
    return "%s_%s_%02d" % (vue, couche, t)


def scenario(dossier):
    for profil, qs in (("contrat", "profil=contrat"), ("reduit", "profil=reduit"),
                       ("ancien-contrat", "profil=contrat&decodage=ancien"), ("ancien-reduit", "profil=reduit&decodage=ancien")):
        os.makedirs(os.path.join(dossier, profil), exist_ok=True)
        et = [{"pause": 3000}]
        vus = set()
        liste = CAPTURES if not profil.startswith("ancien") else [c for c in CAPTURES if c[0] == "fr" and c[2] == 24]
        for vue, couche, t in liste:
            la, ln, z = VUES[vue]
            if profil == "contrat" and vue not in vus:  # capture de référence du fond (opacité 0)
                vus.add(vue)
                et.append({"eval": "__app.aller({couche:'t2m',t:24,centre:[%s,%s],zoom:%s}).then(()=>__app.opacite(0))" % (la, ln, z)})
                et += [{"pause": 600}, {"capture": "%s/fond_%s.png" % (dossier, vue)}, {"eval": "__app.opacite(0.86)"}]
            sf = couche == "ventc"  # « ventc » = couleur du vent seule, flèches masquées
            et.append({"eval": "__app.fleches(%s)" % ("false" if sf else "true")})
            et.append({"eval": "__app.aller({couche:'%s',t:%s,centre:[%s,%s],zoom:%s})" % ("vent" if sf else couche, t, la, ln, z)})
            et += [{"pause": 400}, {"capture": "%s/%s/%s.png" % (dossier, profil, nom(vue, couche, t))}]
        json.dump({"url": URL + "?ui=0&" + qs, "largeur": 1280, "hauteur": 800, "etapes": et},
                  open(os.path.join(dossier, "scenario-%s.json" % profil), "w"))


def lab(rgb):
    c = rgb.astype(np.float64) / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    M = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = c @ M.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 216 / 24389, np.cbrt(xyz), (24389 / 27 * xyz + 16) / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def charger(p):
    return np.array(Image.open(p).convert("RGB"))


def diff(a, b, masque):
    d = np.abs(a.astype(int) - b.astype(int))
    de = np.linalg.norm(lab(a) - lab(b), axis=-1)
    n = max(1, int(masque.sum()))
    return {"pixels_donnees": int(masque.sum()), "ecart_moyen_rvb": round(float(d[masque].mean()), 3) if masque.any() else 0,
            "pct_de_gt_2_3": round(100 * float((de[masque] > 2.3).sum()) / n, 2),
            "pct_de_gt_5": round(100 * float((de[masque] > 5).sum()) / n, 2),
            "de_max": round(float(de[masque].max()), 1) if masque.any() else 0}


def planche(dossier, n, sortie, crop=None, zoom=1, gain=20):
    """Image à trois panneaux : contrat | reduit | |différence| x gain (niveaux de gris), éventuellement rognée et agrandie."""
    a, b = charger("%s/contrat/%s.png" % (dossier, n)), charger("%s/reduit/%s.png" % (dossier, n))
    d = np.clip(np.abs(a.astype(int) - b.astype(int)).max(-1) * gain, 0, 255).astype(np.uint8)
    d3 = np.stack([d] * 3, -1)
    if crop:
        x, y, w, h = crop
        a, b, d3 = a[y:y + h, x:x + w], b[y:y + h, x:x + w], d3[y:y + h, x:x + w]
    im = Image.fromarray(np.hstack([a, np.full((a.shape[0], 6, 3), 255, np.uint8), b, np.full((a.shape[0], 6, 3), 255, np.uint8), d3]))
    if zoom != 1:
        im = im.resize((im.width * zoom, im.height * zoom), Image.NEAREST)
    im.save(sortie)


def analyse(dossier):
    res = {}
    for vue, couche, t in CAPTURES:
        n = nom(vue, couche, t)
        try:
            a, b = charger("%s/contrat/%s.png" % (dossier, n)), charger("%s/reduit/%s.png" % (dossier, n))
            fond = charger("%s/fond_%s.png" % (dossier, vue))
        except FileNotFoundError:
            continue
        masque = (np.abs(a.astype(int) - fond).sum(-1) > 6) | (np.abs(b.astype(int) - fond).sum(-1) > 6)
        res[n] = diff(a, b, masque)
        # écart de la seule couleur de donnée, sans les flèches : le vent compte les flèches (surcouche 2D)
    for profil in ("ancien-contrat", "ancien-reduit"):
        for vue, couche, t in [c for c in CAPTURES if c[0] == "fr" and c[2] == 24]:
            n = nom(vue, couche, t)
            try:
                a = charger("%s/%s/%s.png" % (dossier, profil, n))
                ref = charger("%s/contrat/%s.png" % (dossier, n)) if profil == "ancien-contrat" else charger("%s/reduit/%s.png" % (dossier, n))
                fond = charger("%s/fond_fr.png" % dossier)
            except FileNotFoundError:
                continue
            masque = (np.abs(a.astype(int) - fond).sum(-1) > 6) | (np.abs(ref.astype(int) - fond).sum(-1) > 6)
            res["%s:%s" % (profil, n)] = diff(a, ref, masque)
    json.dump(res, open(os.path.join(dossier, "resultats.json"), "w"), indent=1)
    print("%-26s %9s %8s %8s %8s %6s" % ("image", "px donn.", "moy RVB", "%dE>2,3", "%dE>5", "dEmax"))
    for k, v in res.items():
        print("%-26s %9d %8.3f %8.2f %8.2f %6.1f" % (k, v["pixels_donnees"], v["ecart_moyen_rvb"], v["pct_de_gt_2_3"], v["pct_de_gt_5"], v["de_max"]))


if __name__ == "__main__":
    if sys.argv[1] == "planche":  # planche DOSSIER nom sortie [x,y,w,h] [zoom] [gain]
        planche(sys.argv[2], sys.argv[3], sys.argv[4], [int(v) for v in sys.argv[5].split(",")] if len(sys.argv) > 5 else None,
                int(sys.argv[6]) if len(sys.argv) > 6 else 1, int(sys.argv[7]) if len(sys.argv) > 7 else 20)
    else:
        {"scenario": scenario, "analyse": analyse}[sys.argv[1]](sys.argv[2])
