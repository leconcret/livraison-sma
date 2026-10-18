"""Agent Dispatcher : initiateur du Contract Net (§ 3.3, F-31 à F-35).

Un appel d'offres = un FSMBehaviour (équivalent du comportement à étapes du TP JADE) :

    RECHERCHE ──> COLLECTE ──> ATTRIBUTION ──> CONFIRMATION ──> SUCCES
        │             │             ▲                │
        └─────────────┴──> ECHEC    └── refus ou ────┘
                                        silence du livreur (offre suivante)

- RECHERCHE    : interroge l'Annuaire (livreurs en ligne dans le rayon autour de l'enlèvement) ;
- COLLECTE     : CFP à tous les candidats, collecte des PROPOSE / REFUSE avec délai ;
- ATTRIBUTION  : ACCEPT_PROPOSAL à la meilleure offre, REJECT_PROPOSAL aux autres ;
- CONFIRMATION : attente de la confirmation humaine (AGREE) ou du refus (REFUSE) ; en cas de
                 refus ou de silence, l'offre suivante est retenue ;
- ECHEC        : la commande rejoint la file d'attente ; elle sera relancée avec un rayon
                 élargi (F-33), immédiatement si un nouveau livreur passe en ligne, les
                 commandes urgentes d'abord (F-35). Au-delà de N tentatives : alerte au répartiteur.

Chaque tentative a son identifiant de conversation (cmd-12-t1, cmd-12-t2...) : plusieurs appels
d'offres tournent en parallèle sans confusion (F-32). Un message tardif ne correspond plus à
aucun comportement : SPADE l'écarte au lieu de le laisser s'accumuler (cf. Q3.5 du TP).
"""
from __future__ import annotations

import time

from spade.behaviour import CyclicBehaviour, FSMBehaviour, PeriodicBehaviour, State

from livraison.agents import protocole as P
from livraison.agents.base import AgentLivraison, Contexte, log
from livraison.modele.cout import Offre, classer_offres

RECHERCHE, COLLECTE, ATTRIBUTION, CONFIRMATION, SUCCES, ECHEC = (
    "recherche", "collecte", "attribution", "confirmation", "succes", "echec")


class AppelDOffres(FSMBehaviour):
    def __init__(self, commande: dict, tentative: int, rayon_km: float, exclure: list | None = None,
                 impose: str | None = None):
        self.commande = commande
        self.impose = impose  # JID d'un livreur imposé par le répartiteur (F-62)
        self.cid = int(commande["id"])
        self.tentative = tentative
        self.rayon_km = rayon_km
        self.exclure = exclure or []
        self.conv = f"cmd-{self.cid}-t{tentative}"
        self.candidats: list[dict] = []
        self.offres: list[Offre] = []
        self.refus: list[dict] = []
        self.classement: list[Offre] = []
        self.rang = 0
        self.retenue: Offre | None = None
        self.motif_echec = ""
        self.debut = time.monotonic()
        super().__init__()

    def setup(self):
        self.add_state(RECHERCHE, Recherche(), initial=True)
        self.add_state(COLLECTE, Collecte())
        self.add_state(ATTRIBUTION, Attribution())
        self.add_state(CONFIRMATION, Confirmation())
        self.add_state(SUCCES, Succes())
        self.add_state(ECHEC, Echec())
        for etat in self.get_states().values():
            etat.fsm = self  # chaque état accède aux données de l'appel d'offres
        for src, dst in [(RECHERCHE, COLLECTE), (RECHERCHE, ECHEC), (COLLECTE, ATTRIBUTION),
                         (COLLECTE, ECHEC), (ATTRIBUTION, CONFIRMATION), (ATTRIBUTION, ECHEC),
                         (CONFIRMATION, SUCCES), (CONFIRMATION, ATTRIBUTION), (CONFIRMATION, ECHEC)]:
            self.add_transition(src, dst)

    async def on_end(self):
        self.agent.en_cours.pop(self.cid, None)


class Etape(State):
    @property
    def ao(self) -> AppelDOffres:
        return self.fsm

    @property
    def a(self) -> AgentDispatcher:
        return self.agent

    async def envoyer(self, msg):
        await self.a.envoyer(self, msg)


class Recherche(Etape):
    async def run(self):
        ao, a = self.ao, self.a
        cmd = ao.commande
        critere = {"centre": cmd["enlevement"], "rayon_km": ao.rayon_km, "taille": cmd.get("taille"),
                   "exclure": ao.exclure, "geo": not a.ctx_grille}
        if ao.impose:
            ao.candidats = [{"jid": ao.impose, "livreur_id": None, "distance_km": None}]
        else:
            await self.envoyer(P.creer(a.jid_de(P.ANNUAIRE), P.Perf.QUERY, "recherche", critere,
                                       ao.conv))
            rep = await self.receive(timeout=3)
            ao.candidats = P.lire(rep).get("livreurs", []) if rep else []
        a.ctx.bus.publier("protocole", conversation=ao.conv, commande_id=ao.cid, etape="recherche",
                          texte=f"{len(ao.candidats)} livreur(s) dans un rayon de {ao.rayon_km:g} km")
        if ao.candidats:
            self.set_next_state(COLLECTE)
        else:
            ao.motif_echec = "aucun livreur en ligne dans le rayon"
            self.set_next_state(ECHEC)


class Collecte(Etape):
    async def run(self):
        ao, a = self.ao, self.a
        cmd = ao.commande
        contenu = {"commande_id": ao.cid, "enlevement": cmd["enlevement"],
                   "livraison": cmd["livraison"], "taille": cmd.get("taille", "petit"),
                   "urgent": cmd.get("urgent", False)}
        # Un CFP par destinataire : XMPP n'a pas de message à destinataires multiples.
        for cand in ao.candidats:
            await self.envoyer(P.creer(cand["jid"], P.Perf.CFP, "appel-offres", contenu, ao.conv))
        echeance = time.monotonic() + a.ctx.delais.collecte_offres_s
        attendues = {c["jid"] for c in ao.candidats}
        ordre = 0
        while attendues and (reste := echeance - time.monotonic()) > 0:
            msg = await self.receive(timeout=reste)
            if msg is None:
                break
            qui = P.emetteur(msg)
            if qui not in attendues:
                continue
            attendues.discard(qui)
            c = P.lire(msg)
            if P.perf(msg) == P.Perf.PROPOSE:
                try:
                    o = Offre(qui, float(c["cout"]), c.get("eta_s"), float(c.get("note", 0)), ordre)
                except (KeyError, TypeError, ValueError):
                    continue  # offre mal formée : ignorée
                ordre += 1
                ao.offres.append(o)
            else:
                ao.refus.append({"jid": qui, "motif": c.get("motif", "")})
        ao.classement = classer_offres(ao.offres)
        resume = ", ".join(f"{o.livreur.split('@')[0]}={o.cout:.0f}" for o in ao.classement) or "aucune"
        a.ctx.bus.publier("protocole", conversation=ao.conv, commande_id=ao.cid, etape="collecte",
                          texte=f"offres : {resume} ; refus : {len(ao.refus)} ; sans réponse : "
                                f"{len(attendues)}")
        for o in ao.classement:
            await a.noter_offre(ao, o, "proposee")
        if ao.classement:
            self.set_next_state(ATTRIBUTION)
        else:
            ao.motif_echec = "aucune offre (tous les livreurs ont refusé ou se sont tus)"
            self.set_next_state(ECHEC)


class Attribution(Etape):
    async def run(self):
        ao, a = self.ao, self.a
        if ao.rang >= len(ao.classement):
            ao.motif_echec = "toutes les offres ont été déclinées"
            self.set_next_state(ECHEC)
            return
        o = ao.classement[ao.rang]
        ao.retenue = o
        if ao.rang == 0:
            # NF-01 : temps de décision du SMA, de la recherche à l'ACCEPT (hors confirmation humaine)
            a.stats["decisions"].append(time.monotonic() - ao.debut)
        await self.envoyer(P.creer(o.livreur, P.Perf.ACCEPT, "attribution", {
            "commande": ao.commande, "eta_s": o.eta_s, "cout": o.cout}, ao.conv))
        if ao.rang == 0:
            for perdant in ao.classement[1:]:
                await self.envoyer(P.creer(perdant.livreur, P.Perf.REJECT, "attribution",
                                           {"commande_id": ao.cid}, ao.conv))
        a.ctx.bus.publier("protocole", conversation=ao.conv, commande_id=ao.cid, etape="attribution",
                          texte=f"offre retenue : {o.livreur.split('@')[0]} (coût {o.cout:.0f}, "
                                f"rang {ao.rang + 1}) ; attente de la confirmation du livreur")
        self.set_next_state(CONFIRMATION)


class Confirmation(Etape):
    async def run(self):
        ao, a = self.ao, self.a
        echeance = time.monotonic() + a.ctx.delais.confirmation_humaine_s + 5
        while (reste := echeance - time.monotonic()) > 0:
            msg = await self.receive(timeout=reste)
            if msg is None:
                break
            if P.emetteur(msg) != ao.retenue.livreur or P.type_de(msg) != "confirmation":
                continue  # offre tardive d'un autre livreur : ignorée
            if P.perf(msg) == P.Perf.AGREE:
                await a.noter_offre(ao, ao.retenue, "acceptee")
                self.set_next_state(SUCCES)
                return
            motif = P.lire(msg).get("motif", "decline")
            await a.noter_offre(ao, ao.retenue, f"declinee:{motif}")
            break
        else:
            await a.noter_offre(ao, ao.retenue, "declinee:delai")
        ao.rang += 1
        a.ctx.bus.publier("protocole", conversation=ao.conv, commande_id=ao.cid, etape="confirmation",
                          texte=f"{ao.retenue.livreur.split('@')[0]} n'a pas confirmé : offre suivante")
        self.set_next_state(ATTRIBUTION)


class Succes(Etape):
    async def run(self):
        ao, a = self.ao, self.a
        o = ao.retenue
        livreur_id = int(o.livreur.split("@")[0].split("-")[1])
        duree = time.monotonic() - ao.debut
        await self.envoyer(P.creer(a.jid_de(P.nom_commande(ao.cid)), P.Perf.INFORM, "attribution", {
            "livreur_id": livreur_id, "cout": o.cout, "eta_s": o.eta_s, "tentative": ao.tentative,
            "duree_attribution_s": round(duree, 2)}, f"cmd-{ao.cid}"))
        a.attente.pop(ao.cid, None)
        a.stats["attribuees"] += 1
        a.stats["durees"].append(duree)
        a.ctx.bus.publier("protocole", conversation=ao.conv, commande_id=ao.cid, etape="succes",
                          texte=f"commande {ao.cid} attribuée à livreur-{livreur_id} en {duree:.1f} s")


class Echec(Etape):
    async def run(self):
        ao, a = self.ao, self.a
        d = a.ctx.delais
        prochain_rayon = min(ao.rayon_km * d.facteur_elargissement, d.rayon_max_km)
        a.mettre_en_attente(ao.commande, ao.tentative + 1, prochain_rayon, ao.exclure)
        a.stats["echecs"] += 1
        await self.envoyer(P.creer(a.jid_de(P.nom_commande(ao.cid)), P.Perf.INFORM,
                                   "recherche-infructueuse", {
                                       "tentative": ao.tentative, "motif": ao.motif_echec,
                                       "prochain_rayon_km": prochain_rayon}, f"cmd-{ao.cid}"))
        a.ctx.bus.publier("protocole", conversation=ao.conv, commande_id=ao.cid, etape="echec",
                          texte=f"tentative {ao.tentative} sans succès ({ao.motif_echec}) ; "
                                f"relance prévue avec un rayon de {prochain_rayon:g} km")
        if ao.tentative >= d.max_tentatives and ao.tentative % d.max_tentatives == 0:
            a.alerte(f"commande {ao.cid} : toujours sans livreur après {ao.tentative} tentatives",
                     niveau="critique", commande_id=ao.cid)
            await self.envoyer(P.creer(a.jid_de(P.SUPERVISEUR), P.Perf.INFORM, "signalement", {
                "commande_id": ao.cid, "tentatives": ao.tentative, "motif": ao.motif_echec},
                f"cmd-{ao.cid}"))


class AgentDispatcher(AgentLivraison):
    def __init__(self, ctx: Contexte, mode_grille: bool = False):
        super().__init__(P.DISPATCHER, ctx)
        self.ctx_grille = mode_grille
        self.en_cours: dict[int, AppelDOffres] = {}
        self.attente: dict[int, dict] = {}  # commande_id -> {commande, tentative, rayon, relance}
        self.stats = {"attribuees": 0, "echecs": 0, "durees": [], "decisions": []}

    def lancer(self, commande: dict, tentative: int = 1, rayon_km: float | None = None,
               exclure: list | None = None, impose: str | None = None) -> bool:
        cid = int(commande["id"])
        if cid in self.en_cours:
            return False
        ao = AppelDOffres(commande, tentative, rayon_km or self.ctx.delais.rayon_initial_km,
                          exclure, impose)
        self.en_cours[cid] = ao
        self.add_behaviour(ao, P.modele(conversation=ao.conv))
        return True

    def mettre_en_attente(self, commande: dict, tentative: int, rayon_km: float,
                          exclure: list | None = None) -> None:
        d = self.ctx.delais
        # Après le maximum de tentatives, on espace les relances (le nouvel arrivant reste prioritaire).
        attente = d.attente_relance_s * (6 if tentative > d.max_tentatives else 1)
        self.attente[int(commande["id"])] = {
            "commande": commande, "tentative": tentative, "rayon": rayon_km,
            "relance": time.monotonic() + attente, "depuis": time.monotonic(),
            "exclure": exclure or []}

    def relancer(self, immediat: bool = False) -> int:
        """Relance les commandes en attente, urgentes d'abord puis par ancienneté (F-35)."""
        maintenant = time.monotonic()
        prets = [(cid, e) for cid, e in self.attente.items()
                 if (immediat or e["relance"] <= maintenant) and cid not in self.en_cours]
        prets.sort(key=lambda x: (not x[1]["commande"].get("urgent", False), x[1]["depuis"]))
        lances = 0
        for cid, e in prets:
            if self.lancer(e["commande"], e["tentative"], e["rayon"], e.get("exclure")):
                self.attente.pop(cid, None)
                lances += 1
        return lances

    def annuler(self, cid: int) -> None:
        self.attente.pop(cid, None)
        ao = self.en_cours.pop(cid, None)
        if ao:
            ao.kill()

    async def noter_offre(self, ao: AppelDOffres, o: Offre, resultat: str) -> None:
        try:
            lid = int(o.livreur.split("@")[0].split("-")[1])
            await self.ctx.depot.noter_offre(ao.cid, lid, o.cout, o.eta_s, resultat, ao.tentative)
        except Exception:  # noqa: BLE001 - le journal ne doit jamais bloquer le protocole
            log.debug("offre non enregistrée pour la commande %s", ao.cid, exc_info=True)

    class Demandes(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=5)
            if not msg:
                return
            a: AgentDispatcher = self.agent
            t, c = P.type_de(msg), P.lire(msg)
            if t == "attribuer":
                cmd = c["commande"]
                cid = int(cmd["id"])
                a.attente.pop(cid, None)
                impose = a.jid_de(P.nom_livreur(int(c["imposer"]))) if c.get("imposer") else None
                if not a.lancer(cmd, int(c.get("tentative", 1)), c.get("rayon_km"),
                                c.get("exclure"), impose):
                    a.mettre_en_attente(cmd, int(c.get("tentative", 1)),
                                        c.get("rayon_km") or a.ctx.delais.rayon_initial_km,
                                        c.get("exclure"))
            elif t == "annuler":
                a.annuler(int(c["commande_id"]))

    class Annonces(CyclicBehaviour):
        """Nouveau livreur en ligne : les commandes en attente sont relancées tout de suite."""

        async def run(self):
            msg = await self.receive(timeout=5)
            if msg and P.type_de(msg) == "nouveau-livreur" and self.agent.attente:
                n = self.agent.relancer(immediat=True)
                self.agent.ctx.bus.publier("protocole", etape="relance",
                                           texte=f"nouveau livreur en ligne : {n} commande(s) relancée(s)")

    class Relanceur(PeriodicBehaviour):
        async def run(self):
            self.agent.relancer()

    class Abonnement(CyclicBehaviour):
        async def on_start(self):
            a: AgentDispatcher = self.agent
            await a.envoyer(self, P.creer(a.jid_de(P.ANNUAIRE), P.Perf.SUBSCRIBE, "abonnement"))

        async def run(self):
            await self.receive(timeout=30)  # accusés de réception de l'annuaire

    async def setup(self):
        annuaire = self.jid_de(P.ANNUAIRE)
        self.add_behaviour(self.Demandes(), P.ModeleFonction(
            lambda m: P.perf(m) == P.Perf.REQUEST and P.type_de(m) in ("attribuer", "annuler")))
        self.add_behaviour(self.Annonces(), P.modele(P.Perf.INFORM, "nouveau-livreur"))
        self.add_behaviour(self.Abonnement(), P.ModeleFonction(
            lambda m: P.emetteur(m) == annuaire and P.perf(m) == P.Perf.AGREE and not m.thread))
        self.add_behaviour(self.Relanceur(period=1.0), P.AUCUN)

    def etat_public(self) -> dict:
        dec = sorted(self.stats["decisions"][-2000:])
        tot = sorted(self.stats["durees"][-2000:])

        def resume(v):
            if not v:
                return None
            return {"moyenne": round(sum(v) / len(v), 2), "p95": round(v[int(0.95 * (len(v) - 1))], 2),
                    "max": round(v[-1], 2), "n": len(v)}
        return {"en_cours": sorted(self.en_cours), "en_attente": sorted(self.attente),
                "attribuees": self.stats["attribuees"], "recherches_infructueuses": self.stats["echecs"],
                # NF-01 : décision du SMA (recherche + appel d'offres jusqu'à l'ACCEPT)
                "decision_s": resume(dec),
                # attribution complète, confirmation humaine comprise
                "attribution_complete_s": resume(tot)}
