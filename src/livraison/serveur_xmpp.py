"""Serveur XMPP du projet (pyjabber, celui de SPADE), réglé pour fonctionner aussi sous Windows.

    python -m livraison.serveur_xmpp                  # port du .env (5222), base temporaire neuve
    python -m livraison.serveur_xmpp --port 5242      # autre port
    python -m livraison.serveur_xmpp --db serveur.db  # base conservée d'un lancement à l'autre

À utiliser à la place de « spade run » : sous Windows, « spade run » choisit winloop, avec
lequel les agents n'arrivent pas à se connecter (voir livraison/boucle.py).
"""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from livraison.boucle import appliquer_a_spade, creer_boucle
from livraison.config import parametres

BASE_TEMPORAIRE = Path(tempfile.gettempdir()) / "livraison-sma-xmpp.db"


def effacer_base(base: Path) -> None:
    """Supprime la base SQLite du serveur XMPP (comptes des agents, recréés au démarrage)."""
    for f in (base, base.with_name(base.name + "-wal"), base.with_name(base.name + "-shm")):
        f.unlink(missing_ok=True)


async def demarrer(port: int, base: Path | None = None, purger: bool = True) -> None:
    """Démarre le serveur et tourne jusqu'à l'arrêt de la tâche.

    Par défaut, base temporaire vidée à chaque démarrage : la base « en mémoire » de SQLite
    n'est pas partagée entre connexions et perd les comptes des agents.
    """
    appliquer_a_spade()
    from pyjabber.server import Server
    from pyjabber.server_parameters import Parameters

    base = base or BASE_TEMPORAIRE
    if purger:
        effacer_base(base)
    await Server(Parameters(host="0.0.0.0", client_port=port, server_port=port + 47,
                            database_path=str(base), database_purge=purger)).start()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=parametres.xmpp_port, help="port des clients XMPP")
    p.add_argument("--db", type=Path, default=None,
                   help="fichier de base à conserver (par défaut : base temporaire neuve)")
    p.add_argument("--purge", action="store_true", help="vider la base indiquée par --db")
    a = p.parse_args()
    purger = a.db is None or a.purge
    import sys

    from loguru import logger  # journal du serveur pyjabber, comme « spade run »

    logger.remove()
    logger.add(sys.stderr, level="INFO",
               format="<green>{time:HH:mm:ss}</green> - <level>{level}: {message}</level>")
    print(f"Serveur XMPP du projet sur le port {a.port} (Ctrl+C pour arrêter)...")
    boucle = creer_boucle()
    try:
        boucle.run_until_complete(demarrer(a.port, a.db, purger))
    except KeyboardInterrupt:
        print("Arrêt du serveur XMPP.")


if __name__ == "__main__":
    main()
