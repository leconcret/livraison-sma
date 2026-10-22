"""Lancement en une commande : serveur XMPP intégré + API + système multi-agents (+ simulateur).

    python -m livraison.lancer                 # XMPP + API (PostgreSQL requis : docker compose up -d)
    python -m livraison.lancer --memoire       # sans base de données (démonstration rapide)
    python -m livraison.lancer --demo 15       # + 15 livreurs et des clients simulés
    python -m livraison.lancer --sans-xmpp     # si le serveur XMPP tourne déjà dans un autre terminal
    python -m livraison.lancer --memoire --rapide   # pour la démonstration de robustesse
    python -m livraison.lancer --trace         # + chaque message ACL entre agents dans le terminal

Puis, dans un autre terminal : python -m livraison.interfaces.console --web
"""
from __future__ import annotations

import argparse
import asyncio
import socket
from types import SimpleNamespace

import uvicorn

from livraison import serveur_xmpp
from livraison.boucle import creer_boucle
from livraison.config import parametres


def port_libre(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


async def principal(args) -> None:
    taches = []
    if not args.sans_xmpp:
        if not port_libre(parametres.xmpp_port):
            raise SystemExit(f"Le port {parametres.xmpp_port} est occupé : un serveur XMPP tourne "
                             "déjà ? Relancez avec --sans-xmpp.")
        # Journal du serveur XMPP réduit aux avertissements : sans cela, chaque connexion d'agent
        # s'affiche (« Connection from ... ») et noie les messages utiles du terminal.
        import sys

        from loguru import logger
        logger.remove()
        logger.add(sys.stderr, level="WARNING")
        taches.append(asyncio.create_task(serveur_xmpp.demarrer(parametres.xmpp_port)))
        while port_libre(parametres.xmpp_port):
            await asyncio.sleep(0.2)
        print(f"Serveur XMPP intégré prêt (port {parametres.xmpp_port}).")
    if args.memoire:
        parametres.depot = "memoire"
    if args.trace:
        parametres.trace_messages = True
    if args.rapide:
        # Délais raccourcis pour une démonstration en direct (valeurs réelles : 30 s, 60 s, 180 s).
        parametres.delai_confirmation_s = 15
        parametres.gps_alerte_s = 8
        parametres.gps_reattribution_s = 20
        print("Délais de démonstration : confirmation 15 s, alerte GPS 8 s, réattribution 20 s.")
    config = uvicorn.Config("livraison.api.main:app", host=parametres.api_host,
                            port=parametres.api_port, log_level="warning")
    api = uvicorn.Server(config)
    taches.append(asyncio.create_task(api.serve()))
    while not api.started:
        await asyncio.sleep(0.2)
    print(f"API prête : http://{parametres.api_host}:{parametres.api_port}/docs")
    print("Console du répartiteur : python -m livraison.interfaces.console --web")
    if args.demo:
        from livraison.simulation.simulateur import Simulation

        sim = Simulation(SimpleNamespace(
            api=f"http://{parametres.api_host}:{parametres.api_port}", livreurs=args.demo,
            commandes=args.commandes, cadence=args.cadence, rafale=False, acceleration=15,
            taux_refus=0.1, taux_silence=0.05, duree_max=3600, graine=42))
        taches.append(asyncio.create_task(sim.lancer()))
    await asyncio.gather(*taches)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--memoire", action="store_true", help="sans PostgreSQL (données perdues à l'arrêt)")
    p.add_argument("--sans-xmpp", action="store_true", help="utiliser un serveur XMPP déjà lancé (python -m livraison.serveur_xmpp)")
    p.add_argument("--trace", action="store_true",
                   help="afficher chaque message ACL échangé entre agents (ENVOI / RECU)")
    p.add_argument("--rapide", action="store_true",
                   help="délais raccourcis pour la démonstration de robustesse")
    p.add_argument("--demo", type=int, default=0, metavar="N", help="simuler N livreurs et des clients")
    p.add_argument("--commandes", type=int, default=60, help="nombre de commandes simulées")
    p.add_argument("--cadence", type=float, default=12, help="commandes simulées par minute")
    # Linux/macOS : boucle de SPADE (uvloop) ; avec la boucle standard, le serveur XMPP intégré peut
    # tenter de lier deux fois son port. Windows : boucle « selector » (winloop bloque les agents).
    boucle = creer_boucle()
    try:
        boucle.run_until_complete(principal(p.parse_args()))
    except KeyboardInterrupt:
        print("Arrêt.")


if __name__ == "__main__":
    main()
