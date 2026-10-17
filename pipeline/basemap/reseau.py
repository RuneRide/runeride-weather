# SPDX-License-Identifier: EUPL-1.2
"""Client HTTP poli : requêtes Range, nouvelles tentatives à attente croissante, compteurs.

Courtoisie : concurrence plafonnée (voir extract), attente exponentielle avec gigue,
respect de `Retry-After`, User-Agent identifiable. Aucune donnée reçue n'est exécutée.
"""
import random
import threading
import time
import urllib.error
import urllib.request

USER_AGENT = "runeride-weather-basemap/0.1 (extraction PMTiles ponctuelle; philippegeek@gmail.com)"
CODES_REESSAYABLES = {408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 523, 524}


def delai_attente(tentative, base=1.0, plafond=60.0, gigue=0.25, alea=random.random):
    """Attente avant la tentative n+1 (tentative >= 0) : base * 2**n, plafonnée, avec gigue."""
    d = min(plafond, base * (2 ** tentative))
    return d * (1.0 + gigue * alea())


class Stats:
    """Compteurs partagés entre threads."""

    def __init__(self):
        self._v = threading.Lock()
        self.requetes = 0          # tentatives HTTP émises (reprises comprises)
        self.reussies = 0
        self.reprises = 0          # tentatives supplémentaires après échec
        self.octets = 0            # octets de corps de réponse reçus (hors en-têtes HTTP)
        self.requetes_dossier = 0  # sous-ensemble : en-tête, répertoires

    def ajouter(self, requetes=0, reussies=0, reprises=0, octets=0):
        with self._v:
            self.requetes += requetes
            self.reussies += reussies
            self.reprises += reprises
            self.octets += octets

    def dict(self):
        with self._v:
            return {"requetes": self.requetes, "reussies": self.reussies,
                    "reprises": self.reprises, "octets": self.octets}


class Client:
    def __init__(self, stats=None, essais=6, timeout=60, pause=0.0, dormir=time.sleep):
        self.stats = stats or Stats()
        self.essais = essais
        self.timeout = timeout
        self.pause = pause
        self._dormir = dormir

    def _requete(self, url, entetes, methode="GET"):
        h = {"User-Agent": USER_AGENT}
        h.update(entetes)
        return urllib.request.Request(url, headers=h, method=methode)

    def sonde(self, url):
        """Code HTTP d'une requête HEAD (200 attendu). Réessaie sur erreur serveur/réseau."""
        for t in range(self.essais):
            try:
                self.stats.ajouter(requetes=1)
                with urllib.request.urlopen(self._requete(url, {}, "HEAD"), timeout=self.timeout) as r:
                    self.stats.ajouter(reussies=1)
                    return r.status
            except urllib.error.HTTPError as e:
                if e.code not in CODES_REESSAYABLES:
                    return e.code
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                pass
            if t + 1 < self.essais:
                self.stats.ajouter(reprises=1)
                self._dormir(delai_attente(t))
        raise ConnectionError(f"HEAD {url} : échecs répétés")

    def plage(self, url, offset, longueur):
        """Octets [offset, offset+longueur[ via Range ; exige un 206 de la bonne taille."""
        derniere = None
        for t in range(self.essais):
            attente = None
            try:
                self.stats.ajouter(requetes=1)
                req = self._requete(url, {"Range": f"bytes={offset}-{offset + longueur - 1}"})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    if r.status != 206:
                        raise ValueError(f"réponse {r.status} au lieu de 206 (Range ignoré ?)")
                    corps = r.read()
                if len(corps) != longueur:
                    raise ValueError(f"{len(corps)} octets reçus au lieu de {longueur}")
                self.stats.ajouter(reussies=1, octets=len(corps))
                if self.pause:
                    self._dormir(self.pause)
                return corps
            except urllib.error.HTTPError as e:
                derniere = e
                if e.code not in CODES_REESSAYABLES:
                    raise
                ra = e.headers.get("Retry-After") if e.headers else None
                if ra and ra.isdigit():
                    attente = min(120.0, float(ra))
            except (urllib.error.URLError, TimeoutError, ConnectionError, ValueError, OSError) as e:
                derniere = e
            if t + 1 < self.essais:
                self.stats.ajouter(reprises=1)
                self._dormir(attente if attente is not None else delai_attente(t))
        raise ConnectionError(f"Range {offset}+{longueur} : {self.essais} échecs ({derniere})")
