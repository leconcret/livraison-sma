"""Robustesse de la connexion des agents au serveur XMPP (constaté sous Windows : quand plusieurs
agents se connectent en même temps, le serveur peut couper l'une des connexions)."""
from spade.agent import DisconnectedException

from livraison.agents.base import AgentLivraison


async def test_connexion_coupee_puis_nouvel_essai(systeme_factory):
    sma = await systeme_factory()
    agent = AgentLivraison("essai-relance", sma.ctx)
    connecter = agent._async_connect
    appels = []

    async def connexion_instable():
        appels.append(1)
        if len(appels) == 1:
            raise DisconnectedException("coupure simulée")
        await connecter()

    agent._async_connect = connexion_instable
    await sma._lancer(agent)
    assert agent.is_alive() and len(appels) == 2
    await agent.stop()
