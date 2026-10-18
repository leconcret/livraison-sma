"""Outils communs aux tests.

Les tests multi-agents ont besoin d'un serveur XMPP : un serveur SPADE en mémoire est lancé
automatiquement sur un port dédié (5242), le temps de la session de tests.
"""
import asyncio
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from livraison.agents.base import Contexte, Delais
from livraison.agents.systeme import SystemeMultiAgents
from livraison.config import parametres
from livraison.donnees.depot import DepotMemoire
from livraison.modele.cout import Coefficients
from livraison.services.evenements import Bus
from livraison.services.itineraires import MoteurEstime, MoteurGrille

PORT_XMPP_TEST = 5242


def _port_ouvert(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture(scope="session")
def serveur_xmpp():
    if _port_ouvert(PORT_XMPP_TEST):
        yield PORT_XMPP_TEST
        return
    # Serveur du projet plutôt que « spade run » : même code, mais boucle adaptée à Windows.
    base = Path(tempfile.gettempdir()) / "livraison-sma-xmpp-test.db"
    proc = subprocess.Popen([sys.executable, "-m", "livraison.serveur_xmpp",
                             "--port", str(PORT_XMPP_TEST), "--db", str(base), "--purge"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    debut = time.time()
    while not _port_ouvert(PORT_XMPP_TEST):
        if time.time() - debut > 20:
            proc.kill()
            pytest.skip("serveur XMPP de test impossible à démarrer")
        time.sleep(0.2)
    yield PORT_XMPP_TEST
    proc.terminate()
    proc.wait(timeout=10)


def contexte_test(port: int, grille: bool = False, **delais) -> Contexte:
    valeurs = {"confirmation_humaine_s": 3.0, "collecte_offres_s": 1.0, "attente_relance_s": 1.0,
                   "gps_ancien_s": 1e6}
    valeurs.update(delais)
    d = Delais(**valeurs)
    return Contexte(
        depot=DepotMemoire(), bus=Bus(), moteur=MoteurGrille() if grille else MoteurEstime(),
        port=port, mot_de_passe="test", delais=d,
        coefficients=Coefficients(penalite_charge_s=3) if grille else Coefficients(),
        simulation_paiement_s=0.2,
    )


@pytest_asyncio.fixture
async def systeme_factory(serveur_xmpp):
    """Fabrique de systèmes multi-agents ; tout est arrêté à la fin du test."""
    crees = []

    async def fabriquer(grille: bool = False, **delais):
        sma = SystemeMultiAgents(contexte_test(serveur_xmpp, grille, **delais), mode_grille=grille)
        await sma.demarrer()
        crees.append(sma)
        return sma

    yield fabriquer
    for sma in crees:
        await sma.arreter()
    await asyncio.sleep(0.2)


async def attendre(condition, timeout: float = 15.0, pas: float = 0.05):
    """Attend qu'une condition (fonction asynchrone ou non) devienne vraie."""
    debut = time.monotonic()
    while time.monotonic() - debut < timeout:
        r = condition()
        if asyncio.iscoroutine(r):
            r = await r
        if r:
            return r
        await asyncio.sleep(pas)
    raise AssertionError("délai dépassé en attendant la condition")


# ------------------------------------------------------------- API (tests HTTP)
@pytest_asyncio.fixture
async def api(serveur_xmpp):
    parametres.depot = "memoire"
    parametres.xmpp_port = serveur_xmpp
    parametres.xmpp_mot_de_passe_agents = "test"
    parametres.osrm_url = "http://127.0.0.1:9"  # pas d'OSRM : estimation
    parametres.delai_confirmation_s = 5
    from livraison.api.main import app
    async with (app.router.lifespan_context(app),
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                  base_url="http://test") as client):
        yield client


async def connexion(api, tel, nom=""):
    code = (await api.post("/auth/code", json={"telephone": tel})).json()["code_dev"]
    r = (await api.post("/auth/jeton", json={"telephone": tel, "code": code, "nom": nom})).json()
    return {"Authorization": f"Bearer {r['jeton']}"}, r


async def attendre_etat(api, h, cid, etat, timeout=15):
    for _ in range(int(timeout / 0.1)):
        c = (await api.get(f"/commandes/{cid}", headers=h)).json()["commande"]
        if c["etat"] == etat:
            return c
        await asyncio.sleep(0.1)
    raise AssertionError(f"commande {cid} : état {c['etat']} au lieu de {etat}")


