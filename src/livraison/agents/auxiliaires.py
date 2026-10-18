"""Agents Paiement, Notification et Superviseur (§ 3.1).

À ce stade du projet, le prestataire de paiement (mobile money, carte) et l'envoi de SMS ou de
notifications push sont simulés : le dialogue entre agents est en place, il suffira de
remplacer la simulation par l'appel au prestataire retenu (lot 5).
"""
from __future__ import annotations

import asyncio
import secrets

from spade.behaviour import CyclicBehaviour, OneShotBehaviour

from livraison.agents import protocole as P
from livraison.agents.base import AgentLivraison, Contexte
from livraison.services.facturation import calculer_facture


class AgentPaiement(AgentLivraison):
    """Dialogue avec le prestataire ; confirme l'encaissement ; suit les espèces (F-51, F-52)."""

    def __init__(self, ctx: Contexte):
        super().__init__(P.PAIEMENT, ctx)

    class Transaction(OneShotBehaviour):
        def __init__(self, demande):
            super().__init__()
            self.demande = demande

        async def run(self):
            a: AgentPaiement = self.agent
            d = P.lire(self.demande)
            await asyncio.sleep(a.ctx.simulation_paiement_s)  # attente de la réponse du prestataire
            ref = f"SIM-{secrets.token_hex(4).upper()}"
            ok = d.get("mode") in ("mobile_money", "carte") and int(d.get("montant", 0)) > 0
            await a.ctx.depot.noter_paiement(int(d["commande_id"]), d.get("mode", "?"),
                                             int(d.get("montant", 0)), ref,
                                             "confirme" if ok else "refuse")
            await a.envoyer(self, P.repondre(self.demande, P.Perf.INFORM,
                                             "paiement-confirme" if ok else "paiement-echoue",
                                             {"commande_id": d["commande_id"], "reference": ref}))

    class Guichet(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=5)
            if not msg:
                return
            a: AgentPaiement = self.agent
            t, d = P.type_de(msg), P.lire(msg)
            if t == "paiement":
                a.add_behaviour(AgentPaiement.Transaction(msg), P.AUCUN)
            elif t == "encaissement":
                montant, lid = int(d.get("montant", 0)), d.get("livreur_id")
                await a.ctx.depot.noter_paiement(int(d["commande_id"]), "especes", montant,
                                                 f"ESP-{d['commande_id']}", "encaisse-par-livreur")
                if lid:
                    lv = await a.ctx.depot.livreur(int(lid))
                    if lv:
                        await a.ctx.depot.maj_livreur(lv.id, especes_detenues=lv.especes_detenues + montant)
                a.ctx.bus.publier("paiement", commande_id=d.get("commande_id"), mode="especes",
                                  montant=montant, livreur_id=lid)
            elif t == "facturer":
                await a.facturer(int(d["commande_id"]))

    async def facturer(self, commande_id: int) -> dict | None:
        """Facture numérotée sans trou : le Guichet traite les demandes une par une."""
        depot = self.ctx.depot
        if await depot.facture(commande_id):
            return None  # déjà facturée (message rejoué après une reprise)
        c = await depot.commande(commande_id)
        f = calculer_facture(c, await depot.nombre_factures() + 1).vers_dict()
        await depot.noter_facture(f)
        self.ctx.bus.publier("facture", commande_id=commande_id, numero=f["numero"],
                             montant=f["montant_ttc"], livreur_id=f["livreur_id"])
        return f

    async def setup(self):
        self.add_behaviour(self.Guichet(), P.ModeleFonction(lambda m: P.perf(m) == P.Perf.REQUEST))


class AgentNotification(AgentLivraison):
    """Envoie les notifications push et SMS (simulés : publiés sur le bus et journalisés)."""

    def __init__(self, ctx: Contexte):
        super().__init__(P.NOTIFICATION, ctx)

    class Envoi(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=5)
            if msg:
                d = P.lire(msg)
                self.agent.ctx.bus.publier("notification", commande_id=d.get("commande_id"),
                                           client=d.get("client"), canal=d.get("canal", "push"),
                                           texte=d.get("texte", ""),
                                           destinataire=d.get("destinataire"))

    async def setup(self):
        self.add_behaviour(self.Envoi(), P.ModeleFonction(lambda m: True))


class AgentSuperviseur(AgentLivraison):
    """Reçoit les signalements (commandes sans livreur...) et les remonte à la console."""

    def __init__(self, ctx: Contexte):
        super().__init__(P.SUPERVISEUR, ctx)
        self.signalements: list[dict] = []

    class Veille(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=5)
            if msg:
                d = P.lire(msg)
                self.agent.signalements.append(d)
                self.agent.ctx.bus.publier("signalement", **d)

    async def setup(self):
        self.add_behaviour(self.Veille(), P.ModeleFonction(lambda m: True))
