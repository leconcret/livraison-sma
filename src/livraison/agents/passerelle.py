"""Agent Passerelle : interface entre le serveur applicatif et le système multi-agents (§ 7).

« Les applications ne dialoguent jamais directement avec les agents » : l'API dépose ses
demandes ici (`transmettre`), la passerelle les envoie aux agents sous forme de messages ACL.
En retour, ce que les agents destinent aux humains (proposition de course pour le téléphone
d'un livreur, résultat d'une remise...) est rangé dans la boîte du livreur et diffusé en direct.
"""
from __future__ import annotations

import asyncio
import time

from spade.behaviour import CyclicBehaviour

from livraison.agents import protocole as P
from livraison.agents.base import AgentLivraison, Contexte


class AgentPasserelle(AgentLivraison):
    def __init__(self, ctx: Contexte):
        super().__init__(P.PASSERELLE, ctx)
        self.sortie: asyncio.Queue = asyncio.Queue()
        self.boites: dict[int, list[dict]] = {}  # livreur_id -> messages pour son téléphone
        self.reponses: dict[str, asyncio.Future] = {}

    def transmettre(self, nom_agent: str, performative: str, type_: str, contenu: dict,
                    conversation: str | None = None, attendre: bool = False) -> asyncio.Future | None:
        """Appelé par l'API. Si `attendre`, renvoie un futur résolu par la réponse de l'agent."""
        fut = None
        if attendre:
            conversation = conversation or f"api-{time.monotonic_ns()}"
            fut = asyncio.get_running_loop().create_future()
            self.reponses[conversation] = fut
        self.sortie.put_nowait(P.creer(self.jid_de(nom_agent), performative, type_, contenu,
                                       conversation))
        return fut

    def boite(self, livreur_id: int, vider: bool = False) -> list[dict]:
        b = self.boites.get(livreur_id, [])
        # On retire les propositions expirées.
        b[:] = [m for m in b if m.get("expire_a", 1e18) > time.time()]
        if vider:
            self.boites[livreur_id] = []
        return list(b)

    class Emettre(CyclicBehaviour):
        async def run(self):
            msg = await self.agent.sortie.get()
            await self.agent.envoyer(self, msg)

    class Recevoir(CyclicBehaviour):
        async def run(self):
            msg = await self.receive(timeout=5)
            if not msg:
                return
            a: AgentPasserelle = self.agent
            t, c = P.type_de(msg), P.lire(msg)
            fut = a.reponses.pop(msg.thread or "", None)
            if fut and not fut.done():
                fut.set_result({"performative": P.perf(msg), "type": t, **c})
            lid = c.get("livreur_id")
            if t == "proposition" and lid is not None:
                item = {"type": t, **c, "recu_a": time.time(),
                        "expire_a": time.time() + float(c.get("delai_s", 30))}
                a.boites.setdefault(int(lid), []).append(item)
            elif t in ("proposition-expiree", "remise-resultat", "annulation") and lid is not None:
                boite = a.boites.setdefault(int(lid), [])
                if t == "proposition-expiree":
                    boite[:] = [m for m in boite if m.get("conversation") != c.get("conversation")]
                boite.append({"type": t, **c, "recu_a": time.time()})
            if lid is not None:
                a.ctx.bus.publier("telephone", livreur_id=lid, type=t, contenu=c)

    async def setup(self):
        self.add_behaviour(self.Emettre(), P.AUCUN)
        self.add_behaviour(self.Recevoir(), P.ModeleFonction(lambda m: True))
