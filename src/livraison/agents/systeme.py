"""Orchestrateur du système multi-agents.

Démarre les agents uniques (Annuaire, Dispatcher, Superviseur, Notification, Paiement,
Passerelle), crée et supprime dynamiquement les agents Livreur (connexion / déconnexion, F-30)
et les agents Commande (un par commande en cours), et reconstruit l'état après un redémarrage
du serveur (F-36).
"""
from __future__ import annotations

import asyncio
import logging

from spade.container import Container

from livraison.agents.annuaire import AgentAnnuaire
from livraison.agents.auxiliaires import AgentNotification, AgentPaiement, AgentSuperviseur
from livraison.agents.base import Contexte
from livraison.agents.commande import AgentCommande
from livraison.agents.dispatcher import AgentDispatcher
from livraison.agents.livreur import AgentLivreur
from livraison.agents.passerelle import AgentPasserelle
from livraison.modele.entites import Commande, Livreur
from livraison.modele.etats import Etat
from livraison.modele.geo import Point

log = logging.getLogger("livraison.systeme")


class ErreurSysteme(RuntimeError):
    pass


class SystemeMultiAgents:
    def __init__(self, ctx: Contexte, mode_grille: bool = False):
        self.ctx = ctx
        self.mode_grille = mode_grille
        self.annuaire: AgentAnnuaire | None = None
        self.dispatcher: AgentDispatcher | None = None
        self.passerelle: AgentPasserelle | None = None
        self.superviseur: AgentSuperviseur | None = None
        self.uniques: list = []
        self.livreurs: dict[int, AgentLivreur] = {}
        self.commandes: dict[int, AgentCommande] = {}
        self._menage: asyncio.Task | None = None
        self.demarre = False

    TENTATIVES_CONNEXION = 4

    async def _lancer(self, agent) -> None:
        """Connecte l'agent au serveur XMPP. Plusieurs agents qui se connectent en même temps
        (rafale de commandes) peuvent voir une connexion coupée par le serveur, surtout sous
        Windows : on réessaie alors après une courte pause croissante."""
        for essai in range(1, self.TENTATIVES_CONNEXION + 1):
            try:
                await agent.start(auto_register=True)
                return
            except Exception as e:
                if essai < self.TENTATIVES_CONNEXION:
                    log.warning("connexion de %s refusée (%s) : nouvel essai %d/%d", agent.jid, e,
                                essai + 1, self.TENTATIVES_CONNEXION)
                    await asyncio.sleep(0.5 * essai)
                    continue
                raise ErreurSysteme(
                    f"Impossible de connecter l'agent {agent.jid} au serveur XMPP "
                    f"({self.ctx.domaine}:{self.ctx.port}). Le serveur est-il lancé "
                    f"(python -m livraison.serveur_xmpp) ? Détail : {e}") from e

    async def demarrer(self) -> None:
        # SPADE garde la boucle asyncio dans un conteneur global : on lui donne la boucle courante
        # (celle d'uvicorn ou de pytest) avant de créer le moindre agent.
        Container().reset()
        Container().loop = asyncio.get_running_loop()
        self.ctx.bus.demarrer()
        self.annuaire = AgentAnnuaire(self.ctx)
        self.superviseur = AgentSuperviseur(self.ctx)
        self.passerelle = AgentPasserelle(self.ctx)
        self.uniques = [self.annuaire, self.superviseur, AgentNotification(self.ctx),
                        AgentPaiement(self.ctx), self.passerelle]
        for a in self.uniques:
            await self._lancer(a)
        # Le Dispatcher en dernier : il s'abonne à l'Annuaire au démarrage.
        self.dispatcher = AgentDispatcher(self.ctx, self.mode_grille)
        await self._lancer(self.dispatcher)
        self.uniques.append(self.dispatcher)
        self._menage = asyncio.create_task(self._faire_menage())
        self.demarre = True
        self.ctx.bus.publier("systeme", texte="système multi-agents démarré",
                             agents=[str(a.jid) for a in self.uniques])

    async def arreter(self) -> None:
        if self._menage:
            self._menage.cancel()
        for a in list(self.commandes.values()) + list(self.livreurs.values()) + self.uniques[::-1]:
            try:
                await a.stop()
            except Exception:
                log.debug("arrêt de %s impossible", a.jid, exc_info=True)
        self.commandes.clear()
        self.livreurs.clear()
        await self.ctx.bus.arreter()
        self.demarre = False

    async def _arreter_agent(self, agent) -> None:
        try:
            await agent.stop()
        finally:
            Container().unregister(str(agent.jid))

    async def _faire_menage(self) -> None:
        """Arrête les agents Commande arrivés à un état final (libère leur connexion XMPP) et
        met hors ligne les livreurs muets depuis longtemps et sans course (téléphone oublié)."""
        while True:
            await asyncio.sleep(2)
            for cid, a in list(self.commandes.items()):
                if a.termine:
                    self.commandes.pop(cid, None)
                    await self._arreter_agent(a)
            for lid, a in list(self.livreurs.items()):
                if not a.courses and a.age_gps() > self.ctx.delais.hors_ligne_auto_s:
                    await self.deconnecter_livreur(lid)
                    self.ctx.bus.publier("alerte", auteur="systeme", niveau="info", livreur_id=lid,
                                         texte=f"livreur-{lid} mis hors ligne (aucun GPS depuis "
                                               f"{a.age_gps() / 60:.0f} min)")

    # ---------------------------------------------------------------- livreurs
    async def connecter_livreur(self, livreur: Livreur, position: Point | None = None) -> AgentLivreur:
        """F-30 : le livreur passe en ligne, son agent est créé et s'inscrit à l'Annuaire."""
        if not livreur.valide:
            raise ErreurSysteme("livreur non validé par l'administrateur (F-03)")
        if livreur.suspendu:
            raise ErreurSysteme("livreur suspendu")
        if livreur.id in self.livreurs:
            return self.livreurs[livreur.id]
        # Reprise : courses déjà attribuées à ce livreur (après une déconnexion ou un redémarrage).
        courses = {}
        for c in await self.ctx.depot.commandes({Etat.ATTRIBUEE, Etat.ENLEVEE}, livreur_id=livreur.id):
            courses[c.id] = {"enlevement": c.enlevement, "livraison": c.livraison,
                             "etape": "enlevee" if c.etat == Etat.ENLEVEE else "a-enlever"}
        agent = AgentLivreur(self.ctx, livreur, None, courses)
        self.livreurs[livreur.id] = agent
        await self._lancer(agent)
        if position is not None:
            self.passerelle.transmettre(agent.nom, "inform", "position", position.vers_dict())
        self.ctx.bus.publier("livreur", livreur_id=livreur.id, action="en-ligne", nom=livreur.nom)
        return agent

    async def deconnecter_livreur(self, livreur_id: int) -> None:
        agent = self.livreurs.pop(livreur_id, None)
        if agent:
            await agent.arreter()
            Container().unregister(str(agent.jid))
            self.ctx.bus.publier("livreur", livreur_id=livreur_id, action="hors-ligne")

    # --------------------------------------------------------------- commandes
    async def nouvelle_commande(self, commande: Commande, reprise: bool = False) -> AgentCommande:
        agent = AgentCommande(self.ctx, commande, reprise=reprise)
        self.commandes[commande.id] = agent
        await self._lancer(agent)
        return agent

    async def reprendre(self) -> int:
        """F-36 : après un redémarrage, recrée un agent pour chaque commande non terminée."""
        n = 0
        for c in await self.ctx.depot.commandes({Etat.CREEE, Etat.VALIDEE, Etat.RECHERCHE,
                                                 Etat.ATTRIBUEE, Etat.ENLEVEE}):
            if c.id not in self.commandes:
                await self.nouvelle_commande(c, reprise=True)
                n += 1
        if n:
            log.info("reprise : %d commande(s) en cours reconstruite(s)", n)
        return n

    def etat(self) -> dict:
        return {
            "demarre": self.demarre,
            "agents": len(self.uniques) + len(self.livreurs) + len(self.commandes),
            "livreurs_en_ligne": len(self.livreurs),
            "livreurs_inscrits_annuaire": len(self.annuaire.entrees) if self.annuaire else 0,
            "commandes_actives": len(self.commandes),
            "dispatcher": self.dispatcher.etat_public() if self.dispatcher else {},
            "signalements": self.superviseur.signalements[-20:] if self.superviseur else [],
        }
