"""Cycle de vie d'une commande (cahier des charges § 3.4).

Créée -> Validée (payée, ou paiement à la livraison) -> En recherche de livreur -> Attribuée
-> Enlevée -> Livrée -> Clôturée ; états d'exception : Annulée, Échec de livraison, Litige.

Le passage « Enlevée -> En cours de livraison » du cahier des charges est fusionné : un colis
enlevé est en cours de livraison. Toute transition non prévue lève TransitionInvalide, ce qui
garantit qu'une commande ne peut pas « sauter » d'étape (exigence NF-04).
"""
from __future__ import annotations

from enum import StrEnum


class Etat(StrEnum):
    CREEE = "creee"
    VALIDEE = "validee"
    RECHERCHE = "recherche"
    ATTRIBUEE = "attribuee"
    ENLEVEE = "enlevee"
    LIVREE = "livree"
    CLOTUREE = "cloturee"
    ANNULEE = "annulee"
    ECHEC = "echec"
    LITIGE = "litige"


LIBELLES = {
    Etat.CREEE: "Créée",
    Etat.VALIDEE: "Validée",
    Etat.RECHERCHE: "En recherche de livreur",
    Etat.ATTRIBUEE: "Attribuée",
    Etat.ENLEVEE: "Enlevée (en cours de livraison)",
    Etat.LIVREE: "Livrée",
    Etat.CLOTUREE: "Clôturée",
    Etat.ANNULEE: "Annulée",
    Etat.ECHEC: "Échec de livraison",
    Etat.LITIGE: "Litige",
}

TRANSITIONS: dict[Etat, set[Etat]] = {
    Etat.CREEE: {Etat.VALIDEE, Etat.ANNULEE},
    Etat.VALIDEE: {Etat.RECHERCHE, Etat.ANNULEE},
    # Une commande en recherche peut être attribuée, annulée, ou relancée (reste en recherche).
    Etat.RECHERCHE: {Etat.ATTRIBUEE, Etat.ANNULEE, Etat.RECHERCHE},
    # Réattribution (F-34) : refus, panne, perte du GPS avant l'enlèvement.
    Etat.ATTRIBUEE: {Etat.ENLEVEE, Etat.RECHERCHE, Etat.ANNULEE},
    # Après l'enlèvement : livraison, échec (destinataire absent...), ou réattribution (panne).
    Etat.ENLEVEE: {Etat.LIVREE, Etat.ECHEC, Etat.RECHERCHE},
    Etat.LIVREE: {Etat.CLOTUREE, Etat.LITIGE},
    Etat.CLOTUREE: {Etat.LITIGE},
    Etat.ECHEC: {Etat.LITIGE, Etat.CLOTUREE},
    Etat.ANNULEE: set(),
    Etat.LITIGE: {Etat.CLOTUREE},
}

ETATS_FINAUX = {Etat.CLOTUREE, Etat.ANNULEE, Etat.ECHEC}
ETATS_ACTIFS = {Etat.CREEE, Etat.VALIDEE, Etat.RECHERCHE, Etat.ATTRIBUEE, Etat.ENLEVEE}


class TransitionInvalide(ValueError):
    pass


def verifier_transition(actuel: Etat, suivant: Etat) -> Etat:
    if suivant not in TRANSITIONS[actuel]:
        raise TransitionInvalide(f"transition interdite : {actuel} -> {suivant}")
    return suivant
