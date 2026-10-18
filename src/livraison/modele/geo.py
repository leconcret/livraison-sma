"""Géographie : points, distances et zones de service (exigences F-41 à F-43).

Les coordonnées sont en degrés décimaux (latitude, longitude), système WGS 84,
comme le GPS des téléphones et OpenStreetMap.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

RAYON_TERRE_KM = 6371.0


@dataclass(frozen=True)
class Point:
    lat: float
    lon: float

    def vers_dict(self) -> dict:
        return {"lat": self.lat, "lon": self.lon}

    @classmethod
    def depuis_dict(cls, d: dict) -> Point:
        return cls(float(d["lat"]), float(d["lon"]))

    def osrm(self) -> str:
        """Format attendu par OSRM : longitude,latitude (attention à l'ordre)."""
        return f"{self.lon:.6f},{self.lat:.6f}"


def distance_km(a: Point, b: Point) -> float:
    """Distance à vol d'oiseau (formule de haversine)."""
    p1, p2 = math.radians(a.lat), math.radians(b.lat)
    dp = p2 - p1
    dl = math.radians(b.lon - a.lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * RAYON_TERRE_KM * math.asin(math.sqrt(h))


def interpoler(a: Point, b: Point, t: float) -> Point:
    """Point situé à la fraction t (0..1) du segment [a, b] ; utilisé par le simulateur."""
    t = max(0.0, min(1.0, t))
    return Point(a.lat + (b.lat - a.lat) * t, a.lon + (b.lon - a.lon) * t)


@dataclass(frozen=True)
class Zone:
    """Zone de service circulaire (F-43). Une version polygonale pourra s'appuyer sur PostGIS."""

    nom: str
    centre: Point
    rayon_km: float

    def contient(self, p: Point) -> bool:
        return distance_km(self.centre, p) <= self.rayon_km


# Repères approximatifs des communes d'Abidjan (centres indicatifs, à affiner sur la carte).
COMMUNES_ABIDJAN: dict[str, Point] = {
    "Plateau": Point(5.3236, -4.0197),
    "Cocody": Point(5.3480, -3.9870),
    "Riviera": Point(5.3650, -3.9600),
    "Adjamé": Point(5.3550, -4.0250),
    "Attécoubé": Point(5.3350, -4.0450),
    "Yopougon": Point(5.3400, -4.0800),
    "Abobo": Point(5.4250, -4.0200),
    "Treichville": Point(5.2950, -4.0080),
    "Marcory": Point(5.3000, -3.9850),
    "Koumassi": Point(5.2950, -3.9550),
    "Port-Bouët": Point(5.2550, -3.9300),
    "Bingerville": Point(5.3550, -3.8900),
}

# Zone de service par défaut de la ville pilote : un disque couvrant le district d'Abidjan.
ZONE_ABIDJAN = Zone("Abidjan", Point(5.3400, -4.0000), 18.0)


def commune_la_plus_proche(p: Point) -> str:
    return min(COMMUNES_ABIDJAN, key=lambda n: distance_km(COMMUNES_ABIDJAN[n], p))
