"""Socle commun à tous les agents : contexte partagé, envoi journalisé des messages."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from spade.agent import Agent
from spade.behaviour import CyclicBehaviour
from spade.message import Message

from livraison.agents import protocole as P
from livraison.modele.cout import Coefficients

log = logging.getLogger("livraison.agents")
# Trace de chaque message envoyé et reçu (niveau INFO, invisible par défaut).
# Pour la voir : pytest --log-cli-level=INFO, ou logging.basicConfig(level=logging.INFO).
trace = logging.getLogger("livraison.messages")


@dataclass
class Delais:
    """Délais et seuils du protocole (réglables ; valeurs par défaut du cahier des charges)."""

    collecte_offres_s: float = 2.0  # durée maximale de collecte des offres (§ 3.3, étape 2)
    confirmation_humaine_s: float = 30.0  # délai de réponse du livreur (§ 3.3, étape 4)
    reservation_s: float = 8.0  # durée de réservation de capacité pendant une offre (F-32)
    rayon_initial_km: float = 3.0  # présélection géographique autour de l'enlèvement
    facteur_elargissement: float = 2.0  # F-33 : rayon x2 à chaque nouvelle tentative
    rayon_max_km: float = 25.0
    attente_relance_s: float = 10.0  # délai avant de relancer une commande sans offre
    max_tentatives: int = 6  # au-delà : signalement au répartiteur
    gps_ancien_s: float = 120.0  # position trop ancienne : le livreur refuse (§ 3.2)
    gps_alerte_s: float = 60.0  # F-34 : alerte si le GPS se tait pendant une course
    gps_reattribution_s: float = 180.0  # F-34 : réattribution si le silence se prolonge
    periode_position_annuaire_s: float = 10.0
    attente_enlevement_max_s: float = 45 * 60  # alerte si une commande attribuée n'est pas enlevée
    hors_ligne_auto_s: float = 15 * 60  # livreur sans GPS ni course : mis hors ligne
    avance_programmation_s: float = 30 * 60  # F-16 : recherche lancée 30 min avant l'heure prévue


@dataclass
class Contexte:
    """Ce que tous les agents partagent : données, journal, itinéraires, paramètres."""

    depot: object
    bus: object
    moteur: object
    domaine: str = "localhost"
    mot_de_passe: str = "changez-moi"
    port: int = 5222
    delais: Delais = field(default_factory=Delais)
    coefficients: Coefficients = field(default_factory=Coefficients)
    simulation_paiement_s: float = 1.5

    def jid(self, nom: str) -> str:
        return f"{nom}@{self.domaine}"


class AgentLivraison(Agent):
    """Agent du projet. Tout envoi passe par `envoyer`, qui l'inscrit au journal (F-63)."""

    def __init__(self, nom: str, ctx: Contexte):
        super().__init__(ctx.jid(nom), ctx.mot_de_passe, port=ctx.port, verify_security=False)
        self.ctx = ctx
        self.nom = nom

    def jid_de(self, nom: str) -> str:
        return self.ctx.jid(nom)

    async def envoyer(self, comportement: CyclicBehaviour, msg: Message) -> None:
        if not str(msg.sender.bare):  # JID vide (objet toujours « vrai ») : on renseigne l'émetteur
            msg.sender = str(self.jid)
        self.ctx.bus.publier("message", **P.resume(msg))
        await comportement.send(msg)
        if trace.isEnabledFor(logging.INFO):
            r = P.resume(msg)
            trace.info("ENVOI %s -> %s | %s %s | %s", self.nom, r["destinataire"], r["performative"],
                       r["type"], r["conversation"])

    def dispatch(self, msg: Message):
        taches = super().dispatch(msg)
        if trace.isEnabledFor(logging.INFO):
            r = P.resume(msg)
            trace.info("RECU  %s <- %s | %s %s | %s | %d comportement(s)", self.nom, r["emetteur"],
                       r["performative"], r["type"], r["conversation"], len(taches))
        return taches

    def alerte(self, texte: str, niveau: str = "attention", **donnees) -> None:
        self.ctx.bus.publier("alerte", auteur=self.nom, niveau=niveau, texte=texte, **donnees)
        log.warning("[%s] %s", self.nom, texte)
