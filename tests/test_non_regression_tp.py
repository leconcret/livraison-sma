"""Recette, dernier point (§ 9) : « avec le moteur d'itinéraires remplacé par la distance sur
grille, le système reproduit les six attributions du scénario de base du TP ».

Ici, ce sont les vrais agents SPADE (Annuaire, Dispatcher, Livreur, Commande...) qui négocient
par Contract Net, avec confirmation humaine simulée par les téléphones virtuels.
"""
from conftest import attendre
from livraison.modele.entites import Commande, Livreur
from livraison.modele.etats import Etat
from livraison.modele.geo import Point
from livraison.simulation.virtuel import TelephonesVirtuels

# Scénario du TP : positions de départ et destinations des six colis (graine 42).
DEPARTS = {1: (1, 1), 2: (8, 2), 3: (5, 7)}
COLIS = [(0, 3), (8, 4), (0, 5), (5, 8), (9, 3), (2, 2)]
GAGNANTS_ATTENDUS = [1, 2, 1, 3, 2, 1]
COUTS_ATTENDUS = [3, 2, 2, 1, 2, 5]


async def test_six_attributions_du_tp(systeme_factory):
    sma = await systeme_factory(grille=True)
    depot = sma.ctx.depot
    for lid, (x, y) in DEPARTS.items():
        lv = await depot.creer_livreur(Livreur(lid, f"L{lid}", f"0700000{lid}", capacite=3,
                                               valide=True))
        await sma.connecter_livreur(lv, Point(x, y))
    await attendre(lambda: len(sma.annuaire.entrees) == 3)

    tel = TelephonesVirtuels(sma)
    tel.demarrer()
    gagnants, couts = [], []
    for i, (x, y) in enumerate(COLIS, start=1):
        # Dans le TP, le colis est livré à sa destination : enlèvement = livraison = destination.
        cmd = await depot.creer_commande(Commande(0, "client-tp", Point(x, y), Point(x, y)))
        await sma.nouvelle_commande(cmd)

        async def cloturee(cid=cmd.id):
            c = await depot.commande(cid)
            return c if c.etat == Etat.CLOTUREE else None

        fin = await attendre(cloturee, timeout=20)
        gagnants.append(fin.livreur_id)
        offre = [o for o in depot._offres if o["commande_id"] == cmd.id and o["resultat"] == "acceptee"]
        couts.append(round(offre[0]["cout"]))
    await tel.arreter()

    assert gagnants == GAGNANTS_ATTENDUS
    assert couts == COUTS_ATTENDUS
