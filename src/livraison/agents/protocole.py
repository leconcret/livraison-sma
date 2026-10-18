"""Vocabulaire commun des agents : performatives FIPA-ACL, ontologie et format des messages.

Chaque message SPADE porte dans ses métadonnées :
- performative : l'intention (cfp, propose, refuse, accept-proposal...) ;
- ontology     : « livraison » (vocabulaire partagé par tous les agents) ;
- language     : « json » (le corps est un objet JSON) ;
- type         : l'action précise (inscription, recherche, proposition, etape...).
Le fil (thread) XMPP joue le rôle de l'identifiant de conversation JADE.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from spade.message import Message
from spade.template import BaseTemplate, Template

ONTOLOGIE = "livraison"
LANGAGE = "json"


class Perf:
    CFP = "cfp"
    PROPOSE = "propose"
    REFUSE = "refuse"
    ACCEPT = "accept-proposal"
    REJECT = "reject-proposal"
    INFORM = "inform"
    FAILURE = "failure"
    REQUEST = "request"
    AGREE = "agree"
    CANCEL = "cancel"
    SUBSCRIBE = "subscribe"
    QUERY = "query-ref"


# Noms (partie locale du JID) des agents uniques du système.
ANNUAIRE = "annuaire"
DISPATCHER = "dispatcher"
SUPERVISEUR = "superviseur"
NOTIFICATION = "notification"
PAIEMENT = "paiement"
PASSERELLE = "passerelle"


def nom_livreur(livreur_id: int) -> str:
    return f"livreur-{livreur_id}"


def nom_commande(commande_id: int) -> str:
    return f"commande-{commande_id}"


def creer(to: str, performative: str, type_: str, contenu: dict | None = None,
          conversation: str | None = None) -> Message:
    msg = Message(to=to)
    msg.set_metadata("performative", performative)
    msg.set_metadata("ontology", ONTOLOGIE)
    msg.set_metadata("language", LANGAGE)
    msg.set_metadata("type", type_)
    if conversation:
        msg.thread = conversation
    msg.body = json.dumps(contenu or {}, ensure_ascii=False, default=str)
    return msg


def repondre(msg: Message, performative: str, type_: str | None = None,
             contenu: dict | None = None) -> Message:
    rep = creer(str(msg.sender.bare), performative, type_ or msg.get_metadata("type"),
                contenu, msg.thread)
    return rep


def lire(msg: Message) -> dict:
    """Contenu JSON du message ; {} si le contenu est mal formé (ne jamais faire confiance)."""
    try:
        d = json.loads(msg.body or "{}")
        return d if isinstance(d, dict) else {}
    except (TypeError, ValueError):
        return {}


def perf(msg: Message) -> str:
    return msg.get_metadata("performative") or ""


def type_de(msg: Message) -> str:
    return msg.get_metadata("type") or ""


def emetteur(msg: Message) -> str:
    return str(msg.sender.bare) if msg.sender else ""


def modele(performative: str | None = None, type_: str | None = None,
           conversation: str | None = None) -> Template:
    """Équivalent de MessageTemplate : filtre sur performative, type et conversation."""
    t = Template()
    if performative:
        t.set_metadata("performative", performative)
    if type_:
        t.set_metadata("type", type_)
    if conversation:
        t.thread = conversation
    return t


class ModeleFonction(BaseTemplate):
    """Filtre défini par une fonction (équivalent de MessageTemplate.MatchExpression)."""

    def __init__(self, fonction: Callable[[Message], bool]):
        self.fonction = fonction

    def match(self, message: Message) -> bool:
        try:
            return bool(self.fonction(message))
        except Exception:  # noqa: BLE001
            return False


def resume(msg: Message) -> dict[str, Any]:
    """Représentation d'un message pour le journal (équivalent du Sniffer, F-63)."""
    return {
        "performative": perf(msg),
        "type": type_de(msg),
        "emetteur": str(msg.sender.bare).split("@")[0] if msg.sender else "",
        "destinataire": str(msg.to.bare).split("@")[0] if msg.to else "",
        "conversation": msg.thread or "",
        "contenu": lire(msg),
    }


# Filtre qui ne correspond à aucun message. SPADE remet une copie de chaque message à tout
# comportement sans filtre : un comportement qui ne lit jamais sa file (périodique, émetteur)
# la verrait grossir indéfiniment. On lui attribue donc ce filtre.
AUCUN = ModeleFonction(lambda m: False)
