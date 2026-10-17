# SPDX-License-Identifier: EUPL-1.2
"""Équivalent Python de `pmtiles extract` (go-pmtiles) : lit un PMTiles v3 distant par
requêtes Range, ne garde que les tuiles recoupant une boîte jusqu'à un zoom maximal, et
écrit un PMTiles v3 qui conserve type de tuile, compression et métadonnées de la source.

Reprise : les blocs déjà reçus sont consignés dans un dossier `<sortie>.reprise/` ; une
relance avec les mêmes paramètres (même build, même boîte, même zoom) ne les redemande pas.
Le fichier final est écrit sous `<sortie>.part` puis renommé : jamais de fichier partiel
sous le nom définitif.
"""
import concurrent.futures as cf
import datetime
import gzip
import hashlib
import json
import os
import shutil
import time

from pmtiles.tile import (Compression, TileType, deserialize_directory,
                          deserialize_header)
from pmtiles.writer import Writer

from . import tuiles as T
from .reseau import Client, Stats

CONCURRENCE_MAX = 6  # courtoisie : jamais plus de 6 requêtes simultanées


def lire_entete_et_racine(client, url):
    """Premiers 16 Kio : en-tête (127 octets) + répertoire racine."""
    b = client.plage(url, 0, 16384)
    h = deserialize_header(b[:127])
    racine = b[h["root_offset"]:h["root_offset"] + h["root_length"]]
    return h, racine


def lire_metadonnees(client, url, h):
    brut = client.plage(url, h["metadata_offset"], h["metadata_length"])
    if h["internal_compression"] == Compression.GZIP:
        brut = gzip.decompress(brut)
    elif h["internal_compression"] != Compression.NONE:
        raise ValueError("compression interne non gérée")
    return json.loads(brut)


def collecter_entrees(client, url, h, racine_octets, ids, pool):
    """Parcourt l'arbre de répertoires en ne descendant que dans les feuilles utiles.

    Retourne [(tile_id, offset, longueur)] pour chaque tuile voulue présente dans la source."""
    trouvees = []
    # file de répertoires à traiter : (octets_gz, borne_haute_ids)
    niveau = [(racine_octets, None)]
    for profondeur in range(4):
        feuilles = []
        for octets, borne in niveau:
            entrees = deserialize_directory(octets)
            for i, e in enumerate(entrees):
                fin = entrees[i + 1].tile_id if i + 1 < len(entrees) else borne
                if e.run_length == 0:
                    if T.intersecte(ids, e.tile_id, fin):
                        feuilles.append((h["leaf_directory_offset"] + e.offset, e.length, fin))
                else:
                    for tid in T.ids_dans(ids, e.tile_id, e.tile_id + e.run_length):
                        trouvees.append((tid, e.offset, e.length))
        if not feuilles:
            return trouvees
        futs = [(pool.submit(client.plage, url, o, n), fin) for o, n, fin in feuilles]
        niveau = [(f.result(), fin) for f, fin in futs]
    raise ValueError("arbre de répertoires trop profond")


class EcrivainPMTiles(Writer):
    """Writer de la bibliothèque, avec dédoublonnage par empreinte BLAKE2 (et non hash()
    Python, salé et sur 64 bits) et fichier temporaire de tuiles placé à côté de la sortie."""

    def __init__(self, f, dossier_tmp):
        super().__init__(f)
        self.tile_f.close()
        self.tile_f = open(os.path.join(dossier_tmp, "tuiles.tmp"), "w+b")
        self._chemin_tmp = self.tile_f.name

    def write_tile(self, tileid, data):
        from pmtiles.tile import Entry
        if self.tile_entries and tileid < self.tile_entries[-1].tile_id:
            self.clustered = False
        cle = hashlib.blake2b(data, digest_size=16).digest()
        trouve = self.hash_to_offset.get(cle)
        if trouve is not None:
            dernier = self.tile_entries[-1]
            if tileid == dernier.tile_id + dernier.run_length and dernier.offset == trouve:
                dernier.run_length += 1
            else:
                self.tile_entries.append(Entry(tileid, trouve, len(data), 1))
        else:
            self.tile_f.write(data)
            self.tile_entries.append(Entry(tileid, self.offset, len(data), 1))
            self.hash_to_offset[cle] = self.offset
            self.offset += len(data)
        self.addressed_tiles += 1

    def nettoyer(self):
        try:
            os.unlink(self._chemin_tmp)
        except OSError:
            pass


class Reprise:
    """Journal des blocs de tuiles déjà reçus (données + index JSON lignes)."""

    def __init__(self, dossier, cle):
        self.dossier = dossier
        os.makedirs(dossier, exist_ok=True)
        fcle = os.path.join(dossier, "cle.json")
        self.donnees = os.path.join(dossier, "blocs.bin")
        self.index = os.path.join(dossier, "index.jsonl")
        ok = False
        if os.path.exists(fcle):
            try:
                with open(fcle) as fc:
                    ok = json.load(fc) == cle
            except ValueError:
                ok = False
        if not ok:
            shutil.rmtree(dossier, ignore_errors=True)
            os.makedirs(dossier)
            with open(fcle, "w") as fc:
                json.dump(cle, fc)
        self.pos = {}  # (offset, longueur) -> position dans blocs.bin
        taille = os.path.getsize(self.donnees) if os.path.exists(self.donnees) else 0
        fin_valide = 0
        if os.path.exists(self.index):
            with open(self.index) as f:
                for ligne in f:
                    try:
                        o, n, p = json.loads(ligne)
                    except ValueError:
                        break  # dernière ligne tronquée
                    if p + n > taille:
                        break
                    self.pos[(o, n)] = p
                    fin_valide = max(fin_valide, p + n)
        self.fd = open(self.donnees, "ab")
        self.fd.truncate(fin_valide)
        self.fd.seek(fin_valide)
        self.fi = open(self.index, "a")
        self.fin = fin_valide
        self.deja = len(self.pos)

    def ajouter(self, off, lon, data):
        self.fd.write(data)
        self.fd.flush()
        self.pos[(off, lon)] = self.fin
        self.fi.write(json.dumps([off, lon, self.fin]) + "\n")
        self.fi.flush()
        self.fin += lon

    def lire(self, off, lon):
        with open(self.donnees, "rb") as f:
            f.seek(self.pos[(off, lon)])
            return f.read(lon)

    def fermer(self):
        self.fd.close()
        self.fi.close()


def extraire(url, bbox, zoom_max, sortie, zoom_min=0, concurrence=CONCURRENCE_MAX,
             ecart_max=32 * 1024, taille_max=8 << 20, pause=0.05, a_blanc=False,
             date_build=None, journal=print):
    """Extraction complète. Retourne un dict de mesures."""
    if not 1 <= concurrence <= CONCURRENCE_MAX:
        raise ValueError(f"concurrence entre 1 et {CONCURRENCE_MAX}")
    bbox = T.valider_bbox(bbox)
    t0 = time.monotonic()
    stats = Stats()
    client = Client(stats, pause=pause)
    h, racine = lire_entete_et_racine(client, url)
    if h["tile_type"] == TileType.UNKNOWN:
        raise ValueError("type de tuile inconnu dans la source")
    if h["internal_compression"] != Compression.GZIP:
        raise ValueError("seule la compression interne gzip est gérée")
    meta = lire_metadonnees(client, url, h)
    zoom_max_eff = min(zoom_max, h["max_zoom"])
    ids = T.ids_voulus(bbox, zoom_min, zoom_max_eff)
    journal(f"{len(ids)} tuiles voulues (zooms {zoom_min}-{zoom_max_eff}) ; lecture des répertoires")
    with cf.ThreadPoolExecutor(concurrence) as pool:
        entrees = collecter_entrees(client, url, h, racine, ids, pool)
    # Les offsets du répertoire sont relatifs à la section des tuiles : on les rend absolus.
    base = h["tile_data_offset"]
    entrees = sorted((tid, o + base, n) for tid, o, n in entrees)
    presentes = len(entrees)
    blocs = sorted({(o, n) for _, o, n in entrees})
    octets_utiles = sum(n for _, n in blocs)
    par_zoom = {}
    for tid, _, n in entrees:
        z = T.zoom_de_id(tid)
        c = par_zoom.setdefault(z, [0, 0])
        c[0] += 1
        c[1] += n
    mesures = {
        "source": url, "bbox": list(bbox), "zoom_min": zoom_min, "zoom_max": zoom_max_eff,
        "tuiles_voulues": len(ids), "tuiles_presentes": presentes,
        "contenus_uniques": len(blocs), "octets_tuiles_uniques": octets_utiles,
        "par_zoom": {str(z): {"tuiles": c[0], "octets_avant_dedoublonnage": c[1]}
                     for z, c in sorted(par_zoom.items())},
        "requetes_repertoires": stats.dict(),
    }
    if a_blanc:
        mesures["duree_s"] = round(time.monotonic() - t0, 2)
        return mesures
    plages = T.fusionner_plages(blocs, ecart_max, taille_max)
    mesures["plages"] = len(plages)
    journal(f"{presentes} tuiles présentes, {len(blocs)} contenus uniques "
            f"({octets_utiles / 1e6:.1f} Mo), {len(plages)} plages à demander")

    os.makedirs(os.path.dirname(os.path.abspath(sortie)), exist_ok=True)
    cle = {"url": url, "bbox": list(bbox), "zoom_min": zoom_min, "zoom_max": zoom_max_eff,
           "etag_taille": h["tile_data_length"]}
    reprise = Reprise(sortie + ".reprise", cle)
    mesures["blocs_repris_du_journal"] = reprise.deja
    restantes = [p for p in plages if any(b not in reprise.pos for b in p[2])]
    journal(f"{len(plages) - len(restantes)} plages déjà dans le journal de reprise")
    try:
        file_attente = iter(restantes)
        en_vol = {}
        fait = 0
        with cf.ThreadPoolExecutor(concurrence) as pool:
            def lancer():
                try:
                    p = next(file_attente)
                except StopIteration:
                    return False
                en_vol[pool.submit(client.plage, url, p[0], p[1])] = p
                return True
            for _ in range(concurrence):
                lancer()
            while en_vol:
                termines, _ = cf.wait(en_vol, return_when=cf.FIRST_COMPLETED)
                for f in termines:
                    off, lon, sous = en_vol.pop(f)
                    corps = f.result()  # lève si les nouvelles tentatives ont échoué
                    for bo, bn in sous:
                        reprise.ajouter(bo, bn, corps[bo - off:bo - off + bn])
                    fait += 1
                    if fait % 200 == 0:
                        journal(f"  {fait}/{len(restantes)} plages")
                    lancer()
    except BaseException:
        reprise.fermer()
        journal("Échec : le journal de reprise est conservé, relancer la même commande.")
        raise

    # Écriture du PMTiles de sortie, dans l'ordre des identifiants de tuile.
    part = sortie + ".part"
    dossier_tmp = os.path.dirname(os.path.abspath(sortie))
    with open(part, "wb") as f:
        w = EcrivainPMTiles(f, dossier_tmp)
        try:
            for tid, off, lon in entrees:
                w.write_tile(tid, reprise.lire(off, lon))
            ouest, sud, est, nord = bbox
            # Bornes rognées à celles de la source.
            sb = [h["min_lon_e7"] / 1e7, h["min_lat_e7"] / 1e7, h["max_lon_e7"] / 1e7, h["max_lat_e7"] / 1e7]
            bornes = (max(ouest, sb[0]), max(sud, sb[1]), min(est, sb[2]), min(nord, sb[3]))
            e7 = T.bbox_vers_e7(bornes)
            entete = {
                "version": 3, "tile_compression": h["tile_compression"],
                "tile_type": h["tile_type"],
                "min_lon_e7": e7[0], "min_lat_e7": e7[1], "max_lon_e7": e7[2], "max_lat_e7": e7[3],
                "center_zoom": T.zoom_centre(bornes, zoom_max_eff),
                "center_lon_e7": int(round((e7[0] + e7[2]) / 2)),
                "center_lat_e7": int(round((e7[1] + e7[3]) / 2)),
            }
            meta_sortie = dict(meta)  # conserve attribution, vector_layers, etc.
            meta_sortie["runeride:extraction"] = {
                "source": url, "bbox": list(bbox), "zoom_min": zoom_min, "zoom_max": zoom_max_eff,
                "date_extraction": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                "outil": "pipeline.basemap (reimplémentation Python de pmtiles extract)",
            }
            w.finalize(entete, meta_sortie)
        finally:
            w.nettoyer()
    reprise.fermer()
    os.replace(part, sortie)
    shutil.rmtree(sortie + ".reprise", ignore_errors=True)
    mesures.update({
        "duree_s": round(time.monotonic() - t0, 2),
        "reseau": stats.dict(),
        "poids_final_octets": os.path.getsize(sortie),
        "date_build": date_build,
    })
    return mesures
