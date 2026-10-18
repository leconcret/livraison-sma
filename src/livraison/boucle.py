"""Boucle d'événements asyncio commune au serveur XMPP, aux agents et à l'API.

SPADE choisit winloop sous Windows quand il est installé. Avec winloop, la négociation TLS entre
un agent et le serveur XMPP reste bloquée puis la connexion est coupée (DisconnectedException,
constaté sous Windows 10 avec spade 4.1.4 et winloop 0.8.0). Sous Windows, on impose donc la
boucle asyncio standard « selector », que SPADE et slixmpp supportent. Sous Linux et macOS, on
garde le choix de SPADE (uvloop si disponible).
"""
from __future__ import annotations

import asyncio
import sys

WINDOWS = sys.platform == "win32"


def creer_boucle() -> asyncio.AbstractEventLoop:
    """Boucle en cours si elle existe, sinon une nouvelle boucle adaptée au système."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        pass
    if WINDOWS:
        boucle = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(boucle)
        return boucle
    from spade.container import get_or_create_eventloop

    return get_or_create_eventloop()


def appliquer_a_spade() -> None:
    """Sous Windows, SPADE crée ses boucles avec creer_boucle() au lieu de winloop."""
    if not WINDOWS:
        return
    import spade.container

    spade.container.get_or_create_eventloop = creer_boucle
