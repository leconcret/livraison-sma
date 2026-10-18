"""Moteur d'itinéraires (F-42).

Trois implémentations interchangeables, qui exposent la même méthode `trajet()` :

- MoteurOSRM : interroge le serveur OSRM auto-hébergé (docker compose --profile itineraires) ;
- MoteurEstime : estimation sans serveur, à partir de la distance à vol d'oiseau, d'un
  coefficient de sinuosité et de la vitesse moyenne du véhicule en ville ; c'est aussi la
  solution de repli quand OSRM ne répond pas ;
- MoteurGrille : distance de Manhattan sur la grille du TP (test de non-régression de la recette).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

import httpx

from livraison.modele.cout import Vehicule
from livraison.modele.geo import Point, distance_km

log = logging.getLogger(__name__)

# Vitesses moyennes en circulation urbaine à Abidjan (hypothèses à calibrer sur le terrain).
VITESSE_KMH = {
    Vehicule.MOTO: 24.0,
    Vehicule.VOITURE: 18.0,
    Vehicule.TRICYCLE: 16.0,
    Vehicule.VELO: 12.0,
}
SINUOSITE = 1.35  # longueur de route / distance à vol d'oiseau (valeur usuelle en ville)

# Profils OSRM préparés : voiture au lot 1 ; moto et vélo au lot 2 (cf. README).
PROFIL_OSRM = {Vehicule.MOTO: "driving", Vehicule.VOITURE: "driving",
               Vehicule.TRICYCLE: "driving", Vehicule.VELO: "driving"}
# Correction de durée par rapport au profil voiture (une moto se faufile, un vélo est lent).
CORRECTION_OSRM = {Vehicule.MOTO: 0.8, Vehicule.VOITURE: 1.0,
                   Vehicule.TRICYCLE: 1.1, Vehicule.VELO: 1.5}


@dataclass(frozen=True)
class Trajet:
    distance_m: float
    duree_s: float
    source: str = "estimation"
    geometrie: tuple[tuple[float, float], ...] | None = None  # liste de (lat, lon)


class MoteurEstime:
    nom = "estimation"

    async def trajet(self, depart, arrivee, vehicule: Vehicule | str = Vehicule.MOTO) -> Trajet:
        return _estimer(depart, arrivee, Vehicule(vehicule))


@lru_cache(maxsize=50_000)
def _estimer(depart: Point, arrivee: Point, vehicule: Vehicule) -> Trajet:
    d_km = distance_km(depart, arrivee) * SINUOSITE
    duree = d_km / VITESSE_KMH[vehicule] * 3600
    return Trajet(round(d_km * 1000), round(duree, 1), "estimation")


class MoteurGrille:
    """Grille du TP : une case = une unité de temps (permet de rejouer le scénario de base)."""

    nom = "grille"

    async def trajet(self, depart, arrivee, vehicule: Vehicule | str = Vehicule.MOTO) -> Trajet:
        d = abs(arrivee.lat - depart.lat) + abs(arrivee.lon - depart.lon)
        return Trajet(d, d, "grille")


class MoteurOSRM:
    """Client OSRM avec cache et repli automatique sur l'estimation."""

    nom = "osrm"

    def __init__(self, url: str, timeout: float = 3.0):
        self.url = url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=timeout)
        self.repli = MoteurEstime()
        self._cache: dict[tuple, Trajet] = {}
        self.disponible = True

    async def trajet(self, depart, arrivee, vehicule: Vehicule | str = Vehicule.MOTO,
                     geometrie: bool = False) -> Trajet:
        v = Vehicule(vehicule)
        cle = (round(depart.lat, 4), round(depart.lon, 4), round(arrivee.lat, 4),
               round(arrivee.lon, 4), v, geometrie)
        if cle in self._cache:
            return self._cache[cle]
        if not self.disponible:
            return await self.repli.trajet(depart, arrivee, v)
        url = (f"{self.url}/route/v1/{PROFIL_OSRM[v]}/{depart.osrm()};{arrivee.osrm()}"
               f"?overview={'full' if geometrie else 'false'}&geometries=geojson")
        try:
            rep = await self.client.get(url)
            route = rep.json()["routes"][0]
            geo = None
            if geometrie:
                geo = tuple((lat, lon) for lon, lat in route["geometry"]["coordinates"])
            t = Trajet(route["distance"], route["duration"] * CORRECTION_OSRM[v], "osrm", geo)
        except Exception as e:  # noqa: BLE001 - tout échec bascule sur l'estimation
            log.warning("OSRM indisponible (%s) : bascule sur l'estimation", type(e).__name__)
            self.disponible = False
            return await self.repli.trajet(depart, arrivee, v)
        if len(self._cache) > 50_000:
            self._cache.clear()
        self._cache[cle] = t
        return t

    async def verifier(self) -> bool:
        """Teste le serveur (Plateau -> Cocody) ; met à jour `disponible`."""
        self.disponible = True
        t = await self.trajet(Point(5.3236, -4.0197), Point(5.3480, -3.9870))
        return t.source == "osrm"


async def creer_moteur(url_osrm: str | None):
    """OSRM s'il répond, sinon l'estimation."""
    if url_osrm:
        m = MoteurOSRM(url_osrm)
        if await m.verifier():
            log.info("Moteur d'itinéraires : OSRM (%s)", url_osrm)
            return m
    log.info("Moteur d'itinéraires : estimation (OSRM non disponible)")
    return MoteurEstime()
