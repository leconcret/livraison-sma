"""Agent Annuaire : les pages jaunes du système (équivalent du DF de JADE, absent de SPADE).

Les agents Livreur y publient le service « livraison » quand ils passent en ligne, avec leur
type de véhicule et leur position ; ils s'en retirent hors ligne (F-30). Le Dispatcher y
recherche les livreurs situés dans un rayon donné autour du point d'enlèvement (présélection
géographique, § 3.3 étape 1) et s'y abonne pour être prévenu des nouveaux livreurs (recette :
une commande en attente est attribuée dès qu'un livreur passe en ligne).

Messages traités :
  request   / inscription  -> agree        (+ inform nouveau-livreur aux abonnés)
  request   / retrait      -> agree
  inform    / position     -> (aucune réponse)
  query-ref / recherche    -> inform resultat {livreurs: [...]}
  subscribe / abonnement   -> agree
"""
from __future__ import annotations

import time

from spade.behaviour import CyclicBehaviour

from livraison.agents import protocole as P
from livraison.agents.base import AgentLivraison, Contexte, trace
from livraison.modele.cout import taille_acceptee
from livraison.modele.geo import Point, distance_km


class AgentAnnuaire(AgentLivraison):
    def __init__(self, ctx: Contexte):
        super().__init__(P.ANNUAIRE, ctx)
        self.entrees: dict[str, dict] = {}  # jid -> description du service
        self.abonnes: set[str] = set()

    class Guichet(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=5)
            if not msg:
                return
            a: AgentAnnuaire = self.agent
            t, c, qui = P.type_de(msg), P.lire(msg), P.emetteur(msg)
            if t == "inscription":
                a.entrees[qui] = {
                    "jid": qui, "livreur_id": c.get("livreur_id"),
                    "vehicule": c.get("vehicule", "moto"), "position": c.get("position"),
                    "maj": time.monotonic(),
                }
                await a.envoyer(self, P.repondre(msg, P.Perf.AGREE))
                a.ctx.bus.publier("annuaire", action="inscription", livreur_id=c.get("livreur_id"),
                                  total=len(a.entrees))
                for abonne in list(a.abonnes):
                    await a.envoyer(self, P.creer(abonne, P.Perf.INFORM, "nouveau-livreur",
                                                  {"jid": qui, "livreur_id": c.get("livreur_id")}))
            elif t == "retrait":
                a.entrees.pop(qui, None)
                await a.envoyer(self, P.repondre(msg, P.Perf.AGREE))
                a.ctx.bus.publier("annuaire", action="retrait", livreur_id=c.get("livreur_id"),
                                  total=len(a.entrees))
            elif t == "position":
                if qui in a.entrees:
                    a.entrees[qui]["position"] = c.get("position")
                    a.entrees[qui]["maj"] = time.monotonic()
            elif t == "recherche":
                trouves = a.rechercher(c)
                trace.info("annuaire : %d livreur(s) inscrit(s), %d retenu(s) pour %s", len(a.entrees),
                           len(trouves), c)
                await a.envoyer(self, P.repondre(msg, P.Perf.INFORM, "resultat",
                                                 {"livreurs": trouves}))
            elif t == "abonnement":
                a.abonnes.add(qui)
                await a.envoyer(self, P.repondre(msg, P.Perf.AGREE))

    def rechercher(self, critere: dict) -> list[dict]:
        """Livreurs en ligne dans le rayon, compatibles avec la taille du colis, du plus proche
        au plus éloigné. Sans centre (mode grille du TP) : tous les livreurs inscrits."""
        centre = Point.depuis_dict(critere["centre"]) if critere.get("centre") else None
        rayon = float(critere.get("rayon_km") or 1e9)
        taille = critere.get("taille")
        exclus = set(critere.get("exclure") or [])
        res = []
        for e in self.entrees.values():
            if e["jid"] in exclus or e.get("livreur_id") in exclus:
                continue
            if taille and not taille_acceptee(e["vehicule"], taille):
                continue
            d = None
            if centre is not None and critere.get("geo", True):
                if not e.get("position"):
                    continue
                d = distance_km(centre, Point.depuis_dict(e["position"]))
                if d > rayon:
                    continue
            res.append({"jid": e["jid"], "livreur_id": e["livreur_id"],
                        "distance_km": None if d is None else round(d, 2)})
        res.sort(key=lambda r: (r["distance_km"] is None, r["distance_km"] or 0, str(r["jid"])))
        return res

    async def setup(self):
        self.add_behaviour(self.Guichet())
