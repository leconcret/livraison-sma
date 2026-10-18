"""Tarification (F-50) et estimation du délai (F-11).

prix = part fixe + part au kilomètre + suppléments (taille, nuit), puis majoration urgence ;
arrondi aux 50 FCFA supérieurs. Tous les montants sont paramétrables par l'administrateur.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from livraison.modele.cout import Taille


@dataclass(frozen=True)
class Grille:
    part_fixe: int = 500  # FCFA
    par_km: int = 150  # FCFA par kilomètre de trajet enlèvement -> livraison
    supplement_taille: dict = field(default_factory=lambda: {
        Taille.PETIT: 0, Taille.MOYEN: 300, Taille.GRAND: 800})
    majoration_urgence: float = 0.5  # +50 %
    majoration_nuit: float = 0.2  # +20 % entre 21 h et 6 h
    heure_debut_nuit: int = 21
    heure_fin_nuit: int = 6
    arrondi: int = 50
    prix_minimum: int = 700
    delai_attribution_moyen_s: int = 300  # temps moyen pour qu'un livreur arrive à l'enlèvement


@dataclass(frozen=True)
class Devis:
    prix: int
    distance_km: float
    duree_trajet_s: float
    delai_estime_s: float
    detail: dict


GRILLE_DEFAUT = Grille()


def est_nuit(moment: datetime, g: Grille) -> bool:
    return moment.hour >= g.heure_debut_nuit or moment.hour < g.heure_fin_nuit


def calculer_devis(distance_m: float, duree_s: float, taille: Taille | str = Taille.PETIT,
                   urgent: bool = False, moment: datetime | None = None,
                   grille: Grille = GRILLE_DEFAUT) -> Devis:
    moment = moment or datetime.now()
    km = distance_m / 1000
    base = grille.part_fixe + grille.par_km * km
    supp_taille = grille.supplement_taille[Taille(taille)]
    sous_total = base + supp_taille
    nuit = est_nuit(moment, grille)
    if nuit:
        sous_total *= 1 + grille.majoration_nuit
    if urgent:
        sous_total *= 1 + grille.majoration_urgence
    prix = max(grille.prix_minimum, math.ceil(sous_total / grille.arrondi) * grille.arrondi)
    return Devis(
        prix=int(prix),
        distance_km=round(km, 2),
        duree_trajet_s=round(duree_s),
        delai_estime_s=round(grille.delai_attribution_moyen_s + duree_s),
        detail={"part_fixe": grille.part_fixe, "part_km": round(grille.par_km * km),
                "supplement_taille": supp_taille, "nuit": nuit, "urgent": urgent},
    )
