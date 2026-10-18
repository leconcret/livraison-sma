"""Bus d'événements et journal (F-60, F-63, NF-09).

Chaque message entre agents, chaque changement d'état et chaque alerte devient un événement :
- conservé dans la base (table evenements) pour le traitement des litiges ;
- diffusé en direct aux abonnés (WebSocket de la console du répartiteur, applications).
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections import deque
from datetime import UTC, datetime

log = logging.getLogger(__name__)
_RE_COMMANDE = re.compile(r"(?:commande-|colis-|cmd-)(\d+)")


class Bus:
    def __init__(self, depot=None, taille_historique: int = 3000):
        self.depot = depot
        self.historique: deque[dict] = deque(maxlen=taille_historique)
        self._abonnes: set[asyncio.Queue] = set()
        self._a_ecrire: asyncio.Queue | None = None
        self._tache: asyncio.Task | None = None
        self.compteurs: dict[str, int] = {}

    def demarrer(self) -> None:
        if self.depot and self._tache is None:
            self._a_ecrire = asyncio.Queue()
            self._tache = asyncio.create_task(self._ecrivain())

    async def arreter(self) -> None:
        if self._tache:
            await self._a_ecrire.join()
            self._tache.cancel()
            self._tache = None

    async def _ecrivain(self) -> None:
        """Écrit les événements en base sans ralentir les agents."""
        while True:
            evt = await self._a_ecrire.get()
            try:
                await self.depot.noter_evenement(evt)
            except Exception as e:  # noqa: BLE001
                log.warning("journal : écriture impossible (%s)", e)
            finally:
                self._a_ecrire.task_done()

    def abonner(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self._abonnes.add(q)
        return q

    def desabonner(self, q: asyncio.Queue) -> None:
        self._abonnes.discard(q)

    def publier(self, categorie: str, **donnees) -> dict:
        evt = {"ts": datetime.now(UTC).isoformat(), "categorie": categorie, **donnees}
        if evt.get("commande_id") is None:
            m = _RE_COMMANDE.search(str(donnees.get("conversation", "")))
            if m:
                evt["commande_id"] = int(m.group(1))
        self.compteurs[categorie] = self.compteurs.get(categorie, 0) + 1
        self.historique.append(evt)
        if self._a_ecrire is not None and categorie != "position":
            self._a_ecrire.put_nowait(evt)
        for q in list(self._abonnes):
            try:
                q.put_nowait(evt)
            except asyncio.QueueFull:
                pass  # abonné trop lent : on perd des événements plutôt que de bloquer
        return evt
