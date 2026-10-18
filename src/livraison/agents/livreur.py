"""Agent Livreur : le « jumeau numérique » d'un livreur réel (§ 3.1).

Il connaît la position GPS transmise par le téléphone, le véhicule, la capacité et les courses
en cours. Il répond aux appels d'offres à la place du livreur ; le livreur humain n'intervient
que pour confirmer la course qui lui est attribuée (§ 3.3, étape 4).

Comportements (filtres disjoints, car SPADE remet un message à chaque comportement compatible) :
- AppelsOffres   : cfp                       -> propose (coût, ETA) ou refuse motivé
- Contrats       : accept / reject-proposal  -> proposition au téléphone, ou libération
- Telephone      : messages de la passerelle (position, réponse, enlèvement, remise, incident)
- Retours        : comptes rendus des agents Commande (résultat de la remise...)
- Surveillance   : délais de réponse humaine, réservations expirées, perte du GPS (F-34)
"""
from __future__ import annotations

import time

from spade.behaviour import CyclicBehaviour, PeriodicBehaviour

from livraison.agents import protocole as P
from livraison.agents.base import AgentLivraison, Contexte
from livraison.modele.cout import Taille, cout_itineraire, taille_acceptee
from livraison.modele.entites import Livreur
from livraison.modele.geo import Point


class AgentLivreur(AgentLivraison):
    def __init__(self, ctx: Contexte, livreur: Livreur, position: Point | None = None,
                 courses: dict | None = None):
        super().__init__(P.nom_livreur(livreur.id), ctx)
        self.livreur = livreur
        self.position: Point | None = position
        self.maj_position = time.monotonic() if position else 0.0
        # Heure de connexion : sert de référence tant qu'aucune position n'est arrivée. Sans elle,
        # un livreur tout juste connecté (position encore en route) paraissait muet « depuis
        # toujours » et le ménage périodique le mettait aussitôt hors ligne (constaté sous Windows).
        self.connecte_a = time.monotonic()
        self.courses: dict[int, dict] = courses or {}  # commande_id -> {enlevement, livraison, etape}
        self.reservations: dict[str, float] = {}  # conversation -> échéance
        self.propositions: dict[str, dict] = {}  # conversation -> proposition en attente
        self.inscrit = False
        self.alerte_gps_envoyee = False
        self._derniere_pos_annuaire = 0.0

    # ------------------------------------------------------------------ état
    @property
    def charge(self) -> int:
        return len(self.courses)

    def places_libres(self) -> int:
        return self.livreur.capacite - len(self.courses) - len(self.reservations)

    def age_gps(self) -> float:
        return time.monotonic() - (self.maj_position or self.connecte_a)

    def etat_public(self) -> dict:
        return {
            "livreur_id": self.livreur.id, "nom": self.livreur.nom,
            "vehicule": str(self.livreur.vehicule), "capacite": self.livreur.capacite,
            "position": self.position.vers_dict() if self.position else None,
            "age_gps_s": None if not self.maj_position else round(self.age_gps(), 1),
            "courses": list(self.courses), "charge": self.charge, "inscrit": self.inscrit,
            "reservations": len(self.reservations),
        }

    # --------------------------------------------------------------- calcul du coût
    async def evaluer(self, enlevement: Point) -> tuple[float, float]:
        """Coût (§ 3.2) et ETA à l'enlèvement, en tenant compte des arrêts restants."""
        moteur, v = self.ctx.moteur, self.livreur.vehicule
        direct = (await moteur.trajet(self.position, enlevement, v)).duree_s
        eta, courant = 0.0, self.position
        for course in self.courses.values():
            arrets = [course["livraison"]] if course["etape"] == "enlevee" else [
                course["enlevement"], course["livraison"]]
            for arret in arrets:
                eta += (await moteur.trajet(courant, arret, v)).duree_s
                courant = arret
        eta += (await moteur.trajet(courant, enlevement, v)).duree_s
        detour = eta - direct
        return cout_itineraire(direct, detour, self.charge, self.ctx.coefficients), eta

    def motif_refus(self, contenu: dict) -> str | None:
        if self.livreur.suspendu or not self.livreur.valide:
            return "livreur-non-autorise"
        if self.position is None or self.age_gps() > self.ctx.delais.gps_ancien_s:
            return "position-trop-ancienne"
        if self.places_libres() <= 0:
            return "capacite-atteinte"
        if not taille_acceptee(self.livreur.vehicule, contenu.get("taille", Taille.PETIT)):
            return "colis-trop-grand"
        return None

    # ------------------------------------------------------------- annuaire
    async def s_inscrire(self, comp) -> None:
        if self.inscrit or self.position is None:
            return
        await self.envoyer(comp, P.creer(self.jid_de(P.ANNUAIRE), P.Perf.REQUEST, "inscription", {
            "livreur_id": self.livreur.id, "vehicule": str(self.livreur.vehicule),
            "position": self.position.vers_dict()}))
        self.inscrit = True
        self._derniere_pos_annuaire = time.monotonic()

    async def se_retirer(self, comp) -> None:
        if not self.inscrit:
            return
        await self.envoyer(comp, P.creer(self.jid_de(P.ANNUAIRE), P.Perf.REQUEST, "retrait",
                                         {"livreur_id": self.livreur.id}))
        self.inscrit = False

    # ----------------------------------------------------------- comportements
    class AppelsOffres(CyclicBehaviour):
        async def run(self):
            cfp = await self.receive(timeout=5)
            if not cfp:
                return
            a: AgentLivreur = self.agent
            c = P.lire(cfp)
            motif = a.motif_refus(c)
            if motif:
                await a.envoyer(self, P.repondre(cfp, P.Perf.REFUSE, "offre", {
                    "motif": motif, "livreur_id": a.livreur.id}))
                return
            try:
                cout, eta = await a.evaluer(Point.depuis_dict(c["enlevement"]))
            except (KeyError, TypeError, ValueError):
                await a.envoyer(self, P.repondre(cfp, P.Perf.REFUSE, "offre", {
                    "motif": "format-invalide", "livreur_id": a.livreur.id}))
                return
            # Réservation de la capacité tant que l'offre est en jeu (pas de double attribution).
            a.reservations[cfp.thread] = time.monotonic() + a.ctx.delais.reservation_s
            await a.envoyer(self, P.repondre(cfp, P.Perf.PROPOSE, "offre", {
                "livreur_id": a.livreur.id, "cout": round(cout, 2), "eta_s": round(eta),
                "note": a.livreur.note, "charge": a.charge}))

    class Contrats(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=5)
            if not msg:
                return
            a: AgentLivreur = self.agent
            conv, c = msg.thread, P.lire(msg)
            if P.perf(msg) == P.Perf.REJECT:
                a.reservations.pop(conv, None)
                return
            # ACCEPT_PROPOSAL : on revérifie (cas d'une offre suivante acceptée plus tard).
            reserve = a.reservations.pop(conv, None) is not None
            if not reserve and a.places_libres() <= 0:
                await a.envoyer(self, P.repondre(msg, P.Perf.REFUSE, "confirmation", {
                    "motif": "plus-disponible", "livreur_id": a.livreur.id}))
                return
            # On garde la place pendant le délai de confirmation humaine.
            delai = a.ctx.delais.confirmation_humaine_s
            a.reservations[conv] = time.monotonic() + delai + 5
            a.propositions[conv] = {"commande": c.get("commande", {}), "dispatcher": P.emetteur(msg),
                                    "echeance": time.monotonic() + delai, "eta_s": c.get("eta_s")}
            await a.envoyer(self, P.creer(a.jid_de(P.PASSERELLE), P.Perf.INFORM, "proposition", {
                "livreur_id": a.livreur.id, "conversation": conv, "delai_s": delai,
                "commande": c.get("commande", {}), "eta_s": c.get("eta_s")}, conv))

    class Telephone(CyclicBehaviour):
        """Actions du livreur humain, relayées par la passerelle (API)."""

        async def run(self):
            msg = await self.receive(timeout=5)
            if not msg:
                return
            a: AgentLivreur = self.agent
            t, c = P.type_de(msg), P.lire(msg)
            if t == "position":
                await a.recevoir_position(self, Point.depuis_dict(c))
            elif t == "reponse":
                await a.repondre_proposition(self, c.get("conversation", ""), bool(c.get("accepte")))
            elif t == "enlevement":
                cid = int(c.get("commande_id", 0))
                if cid in a.courses:
                    a.courses[cid]["etape"] = "enlevee"
                    await a.envoyer(self, P.creer(a.jid_de(P.nom_commande(cid)), P.Perf.INFORM,
                                                  "etape", {"etat": "enlevee", "livreur_id": a.livreur.id},
                                                  f"cmd-{cid}"))
            elif t == "remise":
                cid = int(c.get("commande_id", 0))
                if cid in a.courses:
                    await a.envoyer(self, P.creer(a.jid_de(P.nom_commande(cid)), P.Perf.REQUEST,
                                                  "remise", {"code": c.get("code", ""),
                                                             "livreur_id": a.livreur.id}, f"cmd-{cid}"))
            elif t == "incident":
                cid = int(c.get("commande_id", 0))
                if cid in a.courses:
                    a.courses.pop(cid)
                    await a.envoyer(self, P.creer(a.jid_de(P.nom_commande(cid)), P.Perf.FAILURE,
                                                  "incident", {"motif": c.get("motif", "incident"),
                                                               "livreur_id": a.livreur.id}, f"cmd-{cid}"))
            elif t == "retrait-course":
                # Le répartiteur a réattribué ou annulé la commande (F-62).
                a.courses.pop(int(c.get("commande_id", 0)), None)
            elif t == "note":
                a.livreur.note = float(c.get("note", a.livreur.note))
            elif t == "suspension":
                a.livreur.suspendu = bool(c.get("suspendu", True))
                if a.livreur.suspendu:
                    await a.se_retirer(self)
                else:
                    await a.s_inscrire(self)

    class Retours(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=5)
            if not msg:
                return
            a: AgentLivreur = self.agent
            t, c = P.type_de(msg), P.lire(msg)
            if t == "remise-resultat":
                cid = int(c.get("commande_id", 0))
                if c.get("ok"):
                    a.courses.pop(cid, None)
                await a.envoyer(self, P.creer(a.jid_de(P.PASSERELLE), P.Perf.INFORM, "remise-resultat",
                                              {**c, "livreur_id": a.livreur.id}, msg.thread))
            elif t == "annulation":
                a.courses.pop(int(c.get("commande_id", 0)), None)
                await a.envoyer(self, P.creer(a.jid_de(P.PASSERELLE), P.Perf.INFORM, "annulation",
                                              {**c, "livreur_id": a.livreur.id}, msg.thread))

    class Surveillance(PeriodicBehaviour):
        async def run(self):
            a: AgentLivreur = self.agent
            d = a.ctx.delais
            maintenant = time.monotonic()
            for conv, ech in list(a.reservations.items()):
                if ech < maintenant and conv not in a.propositions:
                    a.reservations.pop(conv, None)
            for conv, prop in list(a.propositions.items()):
                if prop["echeance"] < maintenant:
                    await a.repondre_proposition(self, conv, False, motif="sans-reponse")
            age = a.age_gps()
            if a.courses and age > d.gps_reattribution_s:
                a.alerte(f"{a.nom} : GPS muet depuis {age:.0f} s, réattribution de "
                         f"{len(a.courses)} course(s)", niveau="critique", livreur_id=a.livreur.id)
                for cid in list(a.courses):
                    a.courses.pop(cid)
                    await a.envoyer(self, P.creer(a.jid_de(P.nom_commande(cid)), P.Perf.FAILURE,
                                                  "incident", {"motif": "gps-perdu",
                                                               "livreur_id": a.livreur.id}, f"cmd-{cid}"))
                await a.se_retirer(self)
            elif a.courses and age > d.gps_alerte_s and not a.alerte_gps_envoyee:
                a.alerte_gps_envoyee = True
                a.alerte(f"{a.nom} : plus de position GPS depuis {age:.0f} s pendant une course",
                         livreur_id=a.livreur.id)
            elif not a.courses and a.inscrit and age > d.gps_ancien_s:
                await a.se_retirer(self)  # position périmée : plus consulté jusqu'au retour du GPS

    # ------------------------------------------------------------- actions
    async def recevoir_position(self, comp, p: Point) -> None:
        self.position = p
        self.maj_position = time.monotonic()
        self.alerte_gps_envoyee = False
        self.ctx.bus.publier("position", livreur_id=self.livreur.id, lat=p.lat, lon=p.lon,
                             charge=self.charge)
        if not self.inscrit and not self.livreur.suspendu:
            await self.s_inscrire(comp)
        elif time.monotonic() - self._derniere_pos_annuaire > self.ctx.delais.periode_position_annuaire_s:
            self._derniere_pos_annuaire = time.monotonic()
            await self.envoyer(comp, P.creer(self.jid_de(P.ANNUAIRE), P.Perf.INFORM, "position",
                                             {"position": p.vers_dict()}))

    async def repondre_proposition(self, comp, conv: str, accepte: bool, motif: str = "decline"):
        prop = self.propositions.pop(conv, None)
        if prop is None:
            return
        cmd = prop["commande"]
        if accepte:
            self.reservations.pop(conv, None)
            self.courses[int(cmd["id"])] = {
                "enlevement": Point.depuis_dict(cmd["enlevement"]),
                "livraison": Point.depuis_dict(cmd["livraison"]), "etape": "a-enlever"}
            await self.envoyer(comp, P.creer(prop["dispatcher"], P.Perf.AGREE, "confirmation", {
                "livreur_id": self.livreur.id, "eta_s": prop.get("eta_s")}, conv))
        else:
            self.reservations.pop(conv, None)
            await self.envoyer(comp, P.creer(prop["dispatcher"], P.Perf.REFUSE, "confirmation", {
                "livreur_id": self.livreur.id, "motif": motif}, conv))
            if motif == "sans-reponse":
                await self.envoyer(comp, P.creer(self.jid_de(P.PASSERELLE), P.Perf.INFORM,
                                                 "proposition-expiree", {
                                                     "livreur_id": self.livreur.id,
                                                     "conversation": conv}, conv))

    async def setup(self):
        passerelle = self.jid_de(P.PASSERELLE)
        self.add_behaviour(self.AppelsOffres(), P.modele(P.Perf.CFP))
        self.add_behaviour(self.Contrats(), P.ModeleFonction(
            lambda m: P.perf(m) in (P.Perf.ACCEPT, P.Perf.REJECT)))
        self.add_behaviour(self.Telephone(), P.ModeleFonction(
            lambda m: P.emetteur(m) == passerelle and P.perf(m) not in (P.Perf.ACCEPT, P.Perf.REJECT, P.Perf.CFP)))
        annuaire = self.jid_de(P.ANNUAIRE)
        self.add_behaviour(self.Retours(), P.ModeleFonction(
            lambda m: (P.emetteur(m) != passerelle and P.perf(m) == P.Perf.INFORM
                       and P.type_de(m) in ("remise-resultat", "annulation"))
            or (P.emetteur(m) == annuaire and P.perf(m) == P.Perf.AGREE)))
        self.add_behaviour(self.Surveillance(period=1.0), P.AUCUN)

    async def arreter(self) -> None:
        """Passage hors ligne : retrait de l'annuaire, puis arrêt de l'agent (F-30)."""
        if self.inscrit:
            class Retrait(CyclicBehaviour):
                async def run(inner):
                    await self.se_retirer(inner)
                    inner.kill()
            r = Retrait()
            self.add_behaviour(r, P.AUCUN)
            await r.join(timeout=3)
        await self.stop()
