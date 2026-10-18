"""Dépôt PostgreSQL + PostGIS. Ignoré si la base n'est pas joignable (docker compose up -d)."""
import os

import pytest

from livraison.config import parametres
from livraison.modele.entites import Commande, Livreur
from livraison.modele.etats import Etat
from livraison.modele.geo import COMMUNES_ABIDJAN as C

# Base de test séparée (livraison_test), avec l'utilisateur, le mot de passe et le port du .env
DSN = os.environ.get(
    "LIVRAISON_TEST_DSN",
    f"postgresql://{parametres.postgres_user}:{parametres.postgres_password}"
    f"@{parametres.postgres_host}:{parametres.postgres_port}/livraison_test")


@pytest.fixture
async def depot():
    from livraison.donnees.sql import Base, DepotSQL
    d = DepotSQL(DSN)
    try:
        async with d.moteur.begin() as cx:
            await cx.run_sync(Base.metadata.drop_all)
        await d.initialiser()
    except Exception as e:  # noqa: BLE001
        await d.fermer()
        pytest.skip(f"PostgreSQL indisponible : {e}")
    yield d
    await d.fermer()


async def test_commande_aller_retour_et_requete_spatiale(depot):
    lv = await depot.creer_livreur(Livreur(0, "Awa", "0701020304", valide=True))
    c = await depot.creer_commande(Commande(0, "0505050505", C["Plateau"], C["Cocody"], prix=1500))
    c.etat, c.livreur_id = Etat.VALIDEE, lv.id
    await depot.maj_commande(c)
    relue = await depot.commande(c.id)
    assert relue.etat == Etat.VALIDEE and relue.code == c.code and relue.livreur_id == lv.id
    proches = await depot.commandes_proches(C["Plateau"], 500)
    loin = await depot.commandes_proches(C["Yopougon"], 500)
    assert [x.id for x in proches] == [c.id] and loin == []


async def test_journal_et_statistiques(depot):
    await depot.noter_evenement({"ts": "2026-10-17T10:00:00", "categorie": "message",
                                 "commande_id": 3, "performative": "cfp"})
    assert (await depot.evenements(3))[0]["performative"] == "cfp"
    stats = await depot.statistiques()
    assert "par_etat" in stats
