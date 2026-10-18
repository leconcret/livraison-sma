"""Agent Commande : un agent par commande en cours (§ 3.1).

Il porte l'état de la commande (cycle de vie § 3.4), le fait évoluer au fil des messages, le
persiste, prévient le client, surveille les délais et déclenche les relances. Il s'arrête quand
la commande atteint un état final (clôturée, annulée, échec) : aucune commande ne reste en
suspens (NF-04).

Messages reçus (fil « cmd-<id> ») :
  inform  / paiement-confirme | paiement-echoue   (Paiement)
  inform  / attribution | recherche-infructueuse  (Dispatcher)
  inform  / etape {etat: enlevee}                 (Livreur)
  request / remise {code}                          (Livreur)  -> inform remise-resultat
  failure / incident {motif}                       (Livreur)  -> réattribution ou échec
  request / annuler | reattribuer                  (Passerelle : client, répartiteur)
"""
from __future__ import annotations

import time
from datetime import timedelta

from spade.behaviour import CyclicBehaviour, PeriodicBehaviour, TimeoutBehaviour

from livraison.agents import protocole as P
from livraison.agents.base import AgentLivraison, Contexte
from livraison.modele.entites import Commande, maintenant
from livraison.modele.etats import (
    ETATS_FINAUX,
    LIBELLES,
    Etat,
    TransitionInvalide,
    verifier_transition,
)

# Incidents qui mettent fin à la livraison (le colis revient à l'expéditeur) ; les autres
# (panne, accident, perte du GPS...) entraînent une réattribution (F-34).
INCIDENTS_DEFINITIFS = {"destinataire-absent", "adresse-introuvable", "colis-refuse"}
MAX_ESSAIS_CODE = 3


class AgentCommande(AgentLivraison):
    def __init__(self, ctx: Contexte, commande: Commande, reprise: bool = False):
        super().__init__(P.nom_commande(commande.id), ctx)
        self.c = commande
        self.reprise = reprise
        self.conv = f"cmd-{commande.id}"
        self.essais_code = 0
        self.alerte_enlevement = False
        self.livreurs_exclus: list[str] = []
        self.termine = False

    # ----------------------------------------------------------------- outils
    async def changer(self, comp, etat: Etat, motif: str = "", **champs) -> bool:
        try:
            verifier_transition(self.c.etat, etat)
        except TransitionInvalide as e:
            self.alerte(f"commande {self.c.id} : {e}", commande_id=self.c.id)
            return False
        ancien = self.c.etat
        self.c.etat = etat
        if motif:
            self.c.motif = motif
        for k, v in champs.items():
            setattr(self.c, k, v)
        await self.ctx.depot.maj_commande(self.c)
        self.ctx.bus.publier("etat", commande_id=self.c.id, ancien=str(ancien), etat=str(etat),
                             libelle=LIBELLES[etat], livreur_id=self.c.livreur_id, motif=motif)
        await self.notifier(comp, f"Commande {self.c.id} : {LIBELLES[etat].lower()}"
                                  + (f" ({motif})" if motif else ""))
        return True

    async def notifier(self, comp, texte: str, canal: str = "push", **extra) -> None:
        await self.envoyer(comp, P.creer(self.jid_de(P.NOTIFICATION), P.Perf.INFORM, "notification", {
            "commande_id": self.c.id, "client": self.c.client, "texte": texte, "canal": canal,
            **extra}, self.conv))

    async def demander_attribution(self, comp, imposer: int | None = None) -> None:
        await self.envoyer(comp, P.creer(self.jid_de(P.DISPATCHER), P.Perf.REQUEST, "attribuer", {
            "commande": self.c.vers_dict(), "tentative": self.c.tentatives + 1,
            "exclure": self.livreurs_exclus, "imposer": imposer}, self.conv))

    async def informer_livreur(self, comp, type_: str, **contenu) -> None:
        if self.c.livreur_id:
            await self.envoyer(comp, P.creer(self.jid_de(P.nom_livreur(self.c.livreur_id)),
                                             P.Perf.INFORM, type_,
                                             {"commande_id": self.c.id, **contenu}, self.conv))

    async def lancer_recherche(self, comp) -> None:
        """Passe en recherche de livreur, tout de suite ou à l'heure programmée (F-16)."""
        c = self.c
        if c.programmee_pour:
            debut = c.programmee_pour - timedelta(seconds=self.ctx.delais.avance_programmation_s)
            if debut > maintenant():
                self.ctx.bus.publier("protocole", commande_id=c.id, etape="programmation",
                                     texte=f"commande {c.id} programmée : recherche du livreur à "
                                           f"{debut.astimezone().strftime('%d/%m %H:%M')}")
                self.add_behaviour(AgentCommande.Programmation(debut), P.AUCUN)
                return
        await self.changer(comp, Etat.RECHERCHE)
        await self.demander_attribution(comp)

    class Programmation(TimeoutBehaviour):
        async def run(self):
            a: AgentCommande = self.agent
            if a.c.etat == Etat.VALIDEE and not a.termine:
                await a.changer(self, Etat.RECHERCHE)
                await a.demander_attribution(self)

    def finir(self) -> None:
        self.termine = True

    # ----------------------------------------------------------- comportements
    class Cycle(CyclicBehaviour):
        async def on_start(self):
            a: AgentCommande = self.agent
            c = a.c
            if a.reprise:
                # Reprise après redémarrage (F-36) : on reprend là où la commande s'était arrêtée.
                a.ctx.bus.publier("etat", commande_id=c.id, etat=str(c.etat), libelle=LIBELLES[c.etat],
                                  motif="reprise après redémarrage", livreur_id=c.livreur_id)
                if c.etat == Etat.VALIDEE:
                    await a.lancer_recherche(self)
                elif c.etat == Etat.RECHERCHE:
                    await a.demander_attribution(self)
                elif c.etat == Etat.CREEE:
                    a.reprise = False
                    await self.on_start()
                return
            await a.notifier(self, f"Code de confirmation de la commande {c.id} : {c.code}",
                             canal="sms", destinataire=c.telephone_destinataire)
            if c.mode_paiement == "especes":
                await a.changer(self, Etat.VALIDEE, "paiement à la livraison")
                await a.lancer_recherche(self)
            else:
                await a.envoyer(self, P.creer(a.jid_de(P.PAIEMENT), P.Perf.REQUEST, "paiement", {
                    "commande_id": c.id, "mode": c.mode_paiement, "montant": c.prix}, a.conv))

        async def run(self):
            msg = await self.receive(timeout=5)
            if not msg:
                return
            a: AgentCommande = self.agent
            c = a.c
            t, contenu, p = P.type_de(msg), P.lire(msg), P.perf(msg)
            if t == "paiement-confirme" and c.etat == Etat.CREEE:
                await a.changer(self, Etat.VALIDEE, f"payée ({contenu.get('reference', '')})")
                await a.lancer_recherche(self)
            elif t == "paiement-echoue" and c.etat == Etat.CREEE:
                await a.changer(self, Etat.ANNULEE, "paiement refusé")
                a.finir()
            elif t == "attribution" and c.etat == Etat.RECHERCHE:
                c.tentatives = int(contenu.get("tentative", c.tentatives + 1))
                await a.changer(self, Etat.ATTRIBUEE, livreur_id=int(contenu["livreur_id"]),
                                attribuee_a=maintenant())
                eta_min = round((contenu.get("eta_s") or 0) / 60)
                await a.notifier(self, f"Votre livreur arrive dans environ {eta_min} min")
            elif t == "recherche-infructueuse" and c.etat == Etat.RECHERCHE:
                c.tentatives = int(contenu.get("tentative", c.tentatives))
                c.motif = contenu.get("motif", "")
                await a.ctx.depot.maj_commande(c)
                if c.tentatives == 1:
                    await a.notifier(self, "Tous nos livreurs sont occupés : nous cherchons encore.")
            elif t == "etape" and contenu.get("etat") == "enlevee" and c.etat == Etat.ATTRIBUEE:
                await a.changer(self, Etat.ENLEVEE, enlevee_a=maintenant())
            elif t == "remise" and p == P.Perf.REQUEST:
                await self.verifier_remise(msg, contenu)
            elif t == "incident" and p == P.Perf.FAILURE:
                await self.traiter_incident(contenu)
            elif t == "annuler":
                await self.annuler(msg, contenu)
            elif t == "reattribuer":
                await self.reattribuer(msg, contenu)
            if a.termine:
                self.kill()

        async def verifier_remise(self, msg, contenu):
            a: AgentCommande = self.agent
            c = a.c
            if c.etat != Etat.ENLEVEE:
                await a.envoyer(self, P.repondre(msg, P.Perf.INFORM, "remise-resultat", {
                    "commande_id": c.id, "ok": False, "motif": f"commande {c.etat}"}))
                return
            a.essais_code += 1
            if str(contenu.get("code", "")).strip() == c.code:
                await a.envoyer(self, P.repondre(msg, P.Perf.INFORM, "remise-resultat",
                                                 {"commande_id": c.id, "ok": True}))
                await a.changer(self, Etat.LIVREE, livree_a=maintenant())
                if c.mode_paiement == "especes":
                    await a.envoyer(self, P.creer(a.jid_de(P.PAIEMENT), P.Perf.REQUEST, "encaissement", {
                        "commande_id": c.id, "livreur_id": c.livreur_id, "montant": c.prix}, a.conv))
                await a.changer(self, Etat.CLOTUREE)
                # L'agent Paiement émet la facture et calcule la rémunération (F-53, F-54).
                await a.envoyer(self, P.creer(a.jid_de(P.PAIEMENT), P.Perf.REQUEST, "facturer",
                                              {"commande_id": c.id}, a.conv))
                a.finir()
            else:
                reste = MAX_ESSAIS_CODE - a.essais_code
                await a.envoyer(self, P.repondre(msg, P.Perf.INFORM, "remise-resultat", {
                    "commande_id": c.id, "ok": False, "motif": "code incorrect", "essais_restants": reste}))
                if reste <= 0:
                    a.alerte(f"commande {c.id} : {MAX_ESSAIS_CODE} codes incorrects", commande_id=c.id)

        async def traiter_incident(self, contenu):
            a: AgentCommande = self.agent
            c = a.c
            motif = contenu.get("motif", "incident")
            if c.etat not in (Etat.ATTRIBUEE, Etat.ENLEVEE):
                return
            if motif in INCIDENTS_DEFINITIFS and c.etat == Etat.ENLEVEE:
                await a.changer(self, Etat.ECHEC, motif)
                a.alerte(f"commande {c.id} : échec de livraison ({motif})", commande_id=c.id)
                a.finir()
                return
            # Réattribution (F-34) : le livreur défaillant est exclu de la nouvelle recherche.
            if c.livreur_id:
                a.livreurs_exclus.append(a.jid_de(P.nom_livreur(c.livreur_id)))
            a.alerte(f"commande {c.id} : {motif} signalé par livreur-{c.livreur_id}, réattribution",
                     commande_id=c.id)
            await a.changer(self, Etat.RECHERCHE, motif, livreur_id=None)
            await a.demander_attribution(self)

        async def annuler(self, msg, contenu):
            a: AgentCommande = self.agent
            c = a.c
            if c.etat in (Etat.ENLEVEE,) or c.etat in ETATS_FINAUX or c.etat == Etat.LIVREE:
                await a.envoyer(self, P.repondre(msg, P.Perf.REFUSE, "annuler", {
                    "commande_id": c.id, "motif": f"impossible à l'état {c.etat}"}))
                return
            frais = c.etat == Etat.ATTRIBUEE  # F-13 : sans frais avant attribution, frais après
            await a.envoyer(self, P.creer(a.jid_de(P.DISPATCHER), P.Perf.REQUEST, "annuler",
                                          {"commande_id": c.id}, a.conv))
            await a.informer_livreur(self, "annulation")
            await a.changer(self, Etat.ANNULEE, contenu.get("motif", "annulée")
                            + (" (avec frais)" if frais else ""))
            await a.envoyer(self, P.repondre(msg, P.Perf.AGREE, "annuler",
                                             {"commande_id": c.id, "frais": frais}))
            a.finir()

        async def reattribuer(self, msg, contenu):
            """Réattribution manuelle par le répartiteur (F-62), éventuellement à un livreur imposé."""
            a: AgentCommande = self.agent
            c = a.c
            if c.etat not in (Etat.RECHERCHE, Etat.ATTRIBUEE):
                await a.envoyer(self, P.repondre(msg, P.Perf.REFUSE, "reattribuer", {
                    "commande_id": c.id, "motif": f"impossible à l'état {c.etat}"}))
                return
            if c.etat == Etat.ATTRIBUEE:
                await a.informer_livreur(self, "annulation", motif="réattribuée par le répartiteur")
                if c.livreur_id and not contenu.get("livreur_id"):
                    a.livreurs_exclus.append(a.jid_de(P.nom_livreur(c.livreur_id)))
                await a.changer(self, Etat.RECHERCHE, "réattribution manuelle", livreur_id=None)
            else:
                await a.envoyer(self, P.creer(a.jid_de(P.DISPATCHER), P.Perf.REQUEST, "annuler",
                                              {"commande_id": c.id}, a.conv))
            await a.demander_attribution(self, imposer=contenu.get("livreur_id"))
            await a.envoyer(self, P.repondre(msg, P.Perf.AGREE, "reattribuer", {"commande_id": c.id}))

    class Surveillance(PeriodicBehaviour):
        async def run(self):
            a: AgentCommande = self.agent
            c = a.c
            if c.etat == Etat.ATTRIBUEE and c.attribuee_a and not a.alerte_enlevement:
                attente = (maintenant() - c.attribuee_a).total_seconds()
                if attente > a.ctx.delais.attente_enlevement_max_s:
                    a.alerte_enlevement = True
                    a.alerte(f"commande {c.id} attribuée depuis {attente / 60:.0f} min et pas encore "
                             "enlevée", commande_id=c.id)

    async def setup(self):
        self.add_behaviour(self.Cycle(), P.ModeleFonction(lambda m: True))
        self.add_behaviour(self.Surveillance(period=5.0), P.AUCUN)

    def actif(self) -> bool:
        return not self.termine and self.c.etat not in ETATS_FINAUX


def age(t0: float) -> float:
    return time.monotonic() - t0
