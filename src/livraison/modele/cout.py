"""Calcul du coût d'une offre et choix de la meilleure offre (cahier des charges § 3.2).

Deux formules coexistent :
- la version « grille » reprend la formule du TP JADE (distance de Manhattan + 3 x charge)
  et sert de test de non-régression ;
- la version « itinéraire réel » : coût = durée(position -> enlèvement) + durée du détour
  + pénalité x charge, en secondes.

Le détour est le temps ajouté parce que le livreur doit d'abord terminer ses courses déjà
acceptées : détour = ETA au point d'enlèvement (en passant par ses arrêts restants)
- durée du trajet direct. Le coût vaut donc « ETA à l'enlèvement + pénalité x charge ».
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

PENALITE_CHARGE = 3


class Vehicule(StrEnum):
    MOTO = "moto"
    VOITURE = "voiture"
    TRICYCLE = "tricycle"
    VELO = "velo"


class Taille(StrEnum):
    PETIT = "petit"
    MOYEN = "moyen"
    GRAND = "grand"


# Taille maximale de colis acceptée par type de véhicule (refus motivé sinon).
TAILLE_MAX = {
    Vehicule.VELO: Taille.MOYEN,
    Vehicule.MOTO: Taille.MOYEN,
    Vehicule.TRICYCLE: Taille.GRAND,
    Vehicule.VOITURE: Taille.GRAND,
}
_ORDRE_TAILLE = [Taille.PETIT, Taille.MOYEN, Taille.GRAND]


def taille_acceptee(vehicule: Vehicule | str, taille: Taille | str) -> bool:
    maxi = TAILLE_MAX[Vehicule(vehicule)]
    return _ORDRE_TAILLE.index(Taille(taille)) <= _ORDRE_TAILLE.index(maxi)


@dataclass(frozen=True)
class Offre:
    livreur: str
    cout: float
    eta_s: float | None = None  # heure d'arrivée estimée à l'enlèvement, en secondes
    note: float = 0.0  # note du livreur : départage deux offres égales (§ 3.2)
    ordre: int = field(default=0, compare=False)  # rang d'arrivée de l'offre


@dataclass(frozen=True)
class Coefficients:
    """Coefficients réglables par l'administrateur (§ 3.2)."""

    penalite_charge_s: float = 300.0  # 5 minutes de pénalité par course en cours
    poids_detour: float = 1.0


def cout_grille(position: tuple[int, int], destination: tuple[int, int], charge: int) -> int:
    """Formule du TP : distance de Manhattan + 3 x charge."""
    (x, y), (xd, yd) = position, destination
    return abs(xd - x) + abs(yd - y) + PENALITE_CHARGE * charge


COEFFICIENTS_DEFAUT = Coefficients()


def cout_itineraire(duree_directe_s: float, detour_s: float, charge: int,
                    coef: Coefficients = COEFFICIENTS_DEFAUT) -> float:
    """Formule du projet : durée vers l'enlèvement + détour + pénalité x charge."""
    return duree_directe_s + coef.poids_detour * max(0.0, detour_s) + coef.penalite_charge_s * charge


def classer_offres(offres: list[Offre]) -> list[Offre]:
    """Offres de la meilleure à la moins bonne : coût croissant, puis meilleure note,
    puis ordre d'arrivée (la première reçue l'emporte en cas d'égalité parfaite)."""
    return sorted(offres, key=lambda o: (o.cout, -o.note, o.ordre))


def meilleure_offre(offres: list[Offre]) -> Offre | None:
    """Offre de coût minimal ; en cas d'égalité, la première reçue l'emporte."""
    return min(offres, key=lambda o: o.cout, default=None)
