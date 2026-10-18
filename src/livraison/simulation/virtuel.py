"""Téléphones virtuels : simulent les livreurs humains en restant dans le même processus.

Utilisés par les tests et la démonstration rapide. Ils écoutent le bus d'événements (ce que la
passerelle destine au téléphone) et répondent par la passerelle, exactement comme le ferait
l'application du livreur via l'API :
- proposition reçue -> acceptée (ou déclinée / ignorée selon le comportement choisi) ;
- course acceptée   -> trajet jusqu'à l'enlèvement, enlèvement, trajet, remise avec le code.
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field

from livraison.agents import protocole as P
from livraison.modele.geo import Point, interpoler


@dataclass
class Comportement:
    accepte: bool = True  # répond « oui » aux propositions
    repond: bool = True  # répond tout court (sinon : silence, délai dépassé)
    delai_reponse_s: float = 0.05
    duree_trajet_s: float = 0.1  # durée simulée de chaque trajet (accélérée)
    pas_gps: int = 2  # nombre de positions envoyées par trajet
    incident: str | None = None  # motif d'incident à signaler après l'enlèvement
    gps_coupe_apres_acceptation: bool = False  # téléphone éteint pendant la course


@dataclass
class TelephonesVirtuels:
    systeme: object
    comportements: dict[int, Comportement] = field(default_factory=dict)
    defaut: Comportement = field(default_factory=Comportement)
    taches: set = field(default_factory=set)
    journal: list = field(default_factory=list)

    def demarrer(self) -> None:
        self._file = self.systeme.ctx.bus.abonner()
        self._tache = asyncio.create_task(self._ecouter())

    async def arreter(self) -> None:
        self._tache.cancel()
        for t in list(self.taches):
            t.cancel()
        self.systeme.ctx.bus.desabonner(self._file)

    def _envoyer(self, livreur_id: int, type_: str, contenu: dict) -> None:
        self.systeme.passerelle.transmettre(P.nom_livreur(livreur_id), P.Perf.INFORM
                                            if type_ == "position" else P.Perf.REQUEST, type_, contenu)

    async def _ecouter(self) -> None:
        while True:
            evt = await self._file.get()
            if evt.get("categorie") != "telephone" or evt.get("type") != "proposition":
                continue
            lid = int(evt["livreur_id"])
            t = asyncio.create_task(self._traiter(lid, evt["contenu"]))
            self.taches.add(t)
            t.add_done_callback(self.taches.discard)

    async def _traiter(self, lid: int, prop: dict) -> None:
        comp = self.comportements.get(lid, self.defaut)
        conv, cmd = prop["conversation"], prop["commande"]
        if not comp.repond:
            self.journal.append(("silence", lid, cmd["id"]))
            return
        await asyncio.sleep(comp.delai_reponse_s)
        self._envoyer(lid, "reponse", {"conversation": conv, "accepte": comp.accepte})
        self.journal.append(("accepte" if comp.accepte else "decline", lid, cmd["id"]))
        if not comp.accepte or comp.gps_coupe_apres_acceptation:
            return
        agent = self.systeme.livreurs.get(lid)
        depart = agent.position if agent and agent.position else Point.depuis_dict(cmd["enlevement"])
        enl, liv = Point.depuis_dict(cmd["enlevement"]), Point.depuis_dict(cmd["livraison"])
        await self._rouler(lid, depart, enl, comp)
        self._envoyer(lid, "enlevement", {"commande_id": cmd["id"]})
        await asyncio.sleep(0.02)
        if comp.incident:
            self._envoyer(lid, "incident", {"commande_id": cmd["id"], "motif": comp.incident})
            self.journal.append(("incident", lid, cmd["id"]))
            return
        await self._rouler(lid, enl, liv, comp)
        commande = await self.systeme.ctx.depot.commande(int(cmd["id"]))
        self._envoyer(lid, "remise", {"commande_id": cmd["id"], "code": commande.code})
        self.journal.append(("remise", lid, cmd["id"]))

    async def _rouler(self, lid: int, a: Point, b: Point, comp: Comportement) -> None:
        for i in range(1, comp.pas_gps + 1):
            await asyncio.sleep(comp.duree_trajet_s / comp.pas_gps)
            p = b if i == comp.pas_gps else interpoler(a, b, i / comp.pas_gps)
            self._envoyer(lid, "position", p.vers_dict())


def point_aleatoire_abidjan(rng: random.Random) -> Point:
    """Point plausible dans une commune d'Abidjan (autour de son centre)."""
    from livraison.modele.geo import COMMUNES_ABIDJAN

    centre = rng.choice(list(COMMUNES_ABIDJAN.values()))
    return Point(centre.lat + rng.uniform(-0.012, 0.012), centre.lon + rng.uniform(-0.012, 0.012))
