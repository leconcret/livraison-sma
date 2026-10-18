"""Scénarios de la recette (§ 9) et exigences F-30 à F-36, joués avec les vrais agents SPADE
sur des positions réelles d'Abidjan (moteur d'itinéraires par estimation)."""
import asyncio

import pytest

from conftest import attendre
from livraison.agents import protocole as P
from livraison.agents.systeme import ErreurSysteme
from livraison.modele.entites import Commande, Livreur
from livraison.modele.etats import Etat
from livraison.modele.geo import COMMUNES_ABIDJAN as C
from livraison.modele.geo import Point
from livraison.simulation.virtuel import Comportement, TelephonesVirtuels

PLATEAU, COCODY, MARCORY, YOPOUGON = C["Plateau"], C["Cocody"], C["Marcory"], C["Yopougon"]


def pres(p: Point, dlat=0.0, dlon=0.0) -> Point:
    return Point(p.lat + dlat, p.lon + dlon)


async def livreur(sma, lid, position, capacite=2, valide=True):
    lv = await sma.ctx.depot.creer_livreur(Livreur(lid, f"Livreur {lid}", f"07{lid:08d}",
                                                   capacite=capacite, valide=valide))
    await sma.connecter_livreur(lv, position)
    return lv


async def commander(sma, enl, liv, **kw):
    cmd = await sma.ctx.depot.creer_commande(Commande(0, kw.pop("client", "client-1"), enl, liv, **kw))
    await sma.nouvelle_commande(cmd)
    return cmd


def etat_vaut(sma, cid, *etats):
    async def f():
        c = await sma.ctx.depot.commande(cid)
        return c if c.etat in etats else None
    return f


async def inscrits(sma, n):
    await attendre(lambda: len(sma.annuaire.entrees) >= n)


# --------------------------------------------------------------------------- recette § 9
async def test_le_plus_proche_compte_tenu_de_la_charge(systeme_factory):
    """La commande va au livreur dont le temps de trajet réel est le plus court."""
    sma = await systeme_factory()
    await livreur(sma, 1, pres(COCODY))  # loin du Plateau
    await livreur(sma, 2, pres(PLATEAU, 0.004))  # tout près du Plateau
    await inscrits(sma, 2)
    tel = TelephonesVirtuels(sma, defaut=Comportement(duree_trajet_s=0.3))
    tel.demarrer()
    cmd = await commander(sma, PLATEAU, MARCORY)
    fin = await attendre(etat_vaut(sma, cmd.id, Etat.CLOTUREE))
    assert fin.livreur_id == 2
    await tel.arreter()


async def test_refus_puis_offre_suivante(systeme_factory):
    """Un livreur qui décline : la course est proposée au livreur suivant."""
    sma = await systeme_factory()
    await livreur(sma, 1, pres(PLATEAU, 0.002))  # le meilleur, mais il décline
    await livreur(sma, 2, pres(PLATEAU, 0.01))
    await inscrits(sma, 2)
    tel = TelephonesVirtuels(sma, comportements={1: Comportement(accepte=False)})
    tel.demarrer()
    cmd = await commander(sma, PLATEAU, COCODY)
    fin = await attendre(etat_vaut(sma, cmd.id, Etat.CLOTUREE))
    assert fin.livreur_id == 2
    assert ("decline", 1, cmd.id) in tel.journal
    await tel.arreter()


async def test_silence_du_livreur_puis_offre_suivante(systeme_factory):
    """Un livreur qui ne répond pas dans le délai (3 s en test) : livreur suivant."""
    sma = await systeme_factory()
    await livreur(sma, 1, pres(PLATEAU, 0.002))
    await livreur(sma, 2, pres(PLATEAU, 0.01))
    await inscrits(sma, 2)
    tel = TelephonesVirtuels(sma, comportements={1: Comportement(repond=False)})
    tel.demarrer()
    cmd = await commander(sma, PLATEAU, COCODY)
    fin = await attendre(etat_vaut(sma, cmd.id, Etat.CLOTUREE), timeout=20)
    assert fin.livreur_id == 2
    await tel.arreter()


async def test_telephone_eteint_pendant_la_course(systeme_factory):
    """Extinction du téléphone pendant une course : alerte, puis réattribution (F-34)."""
    sma = await systeme_factory(gps_alerte_s=1.0, gps_reattribution_s=2.5)
    await livreur(sma, 1, pres(PLATEAU, 0.002))
    await livreur(sma, 2, pres(PLATEAU, 0.01))
    await inscrits(sma, 2)
    tel = TelephonesVirtuels(sma, comportements={1: Comportement(gps_coupe_apres_acceptation=True)})
    tel.demarrer()
    # Le livreur 2 garde son GPS vivant ; le livreur 1 se tait après avoir accepté.
    async def gps_l2():
        while True:
            sma.passerelle.transmettre(P.nom_livreur(2), P.Perf.INFORM, "position",
                                       pres(PLATEAU, 0.01).vers_dict())
            await asyncio.sleep(0.5)
    battement = asyncio.create_task(gps_l2())
    cmd = await commander(sma, PLATEAU, COCODY)
    fin = await attendre(etat_vaut(sma, cmd.id, Etat.CLOTUREE), timeout=25)
    battement.cancel()
    assert fin.livreur_id == 2
    alertes = [e["texte"] for e in sma.ctx.bus.historique if e["categorie"] == "alerte"]
    assert any("GPS" in a for a in alertes)
    await tel.arreter()


async def test_commande_sans_livreur_attribuee_des_qu_un_livreur_arrive(systeme_factory):
    sma = await systeme_factory(attente_relance_s=60)  # pas de relance périodique pendant le test
    tel = TelephonesVirtuels(sma)
    tel.demarrer()
    cmd = await commander(sma, PLATEAU, COCODY)
    await attendre(lambda: cmd.id in sma.dispatcher.attente, timeout=10)
    await livreur(sma, 7, pres(PLATEAU, 0.003))
    fin = await attendre(etat_vaut(sma, cmd.id, Etat.CLOTUREE), timeout=15)
    assert fin.livreur_id == 7
    await tel.arreter()


async def test_livreur_non_valide_ne_recoit_aucune_course(systeme_factory):
    sma = await systeme_factory()
    with pytest.raises(ErreurSysteme):
        await livreur(sma, 9, pres(PLATEAU), valide=False)
    assert 9 not in sma.livreurs


# ------------------------------------------------------------------ exigences F-3x
async def test_pas_de_double_attribution_au_dela_de_la_capacite(systeme_factory):
    """F-32 : cinq commandes simultanées, deux livreurs de capacité 1."""
    sma = await systeme_factory()
    await livreur(sma, 1, pres(PLATEAU, 0.002), capacite=1)
    await livreur(sma, 2, pres(PLATEAU, -0.002), capacite=1)
    await inscrits(sma, 2)
    tel = TelephonesVirtuels(sma, defaut=Comportement(duree_trajet_s=0.6))
    tel.demarrer()
    depassement = []

    async def controle():
        while True:
            for a in sma.livreurs.values():
                if a.charge > a.livreur.capacite:
                    depassement.append((a.livreur.id, a.charge))
            await asyncio.sleep(0.02)
    ctl = asyncio.create_task(controle())
    cmds = await asyncio.gather(*[commander(sma, pres(PLATEAU, 0.001 * i), COCODY) for i in range(5)])
    for c in cmds:
        await attendre(etat_vaut(sma, c.id, Etat.CLOTUREE), timeout=40)
    ctl.cancel()
    assert depassement == []


async def test_priorite_aux_urgentes(systeme_factory):
    """F-35 : à la relance, la commande urgente passe avant les commandes plus anciennes."""
    sma = await systeme_factory(attente_relance_s=60)
    tel = TelephonesVirtuels(sma, defaut=Comportement(duree_trajet_s=1.0))
    tel.demarrer()
    normales = [await commander(sma, PLATEAU, COCODY) for _ in range(2)]
    urgente = await commander(sma, PLATEAU, COCODY, urgent=True)
    await attendre(lambda: len(sma.dispatcher.attente) == 3, timeout=10)
    await livreur(sma, 1, pres(PLATEAU, 0.002), capacite=1)
    premiere = await attendre(etat_vaut(sma, urgente.id, Etat.ATTRIBUEE, Etat.ENLEVEE, Etat.LIVREE,
                                        Etat.CLOTUREE), timeout=10)
    assert premiere.livreur_id == 1
    for n in normales:
        assert (await sma.ctx.depot.commande(n.id)).etat == Etat.RECHERCHE
    await tel.arreter()


async def test_panne_apres_enlevement_reattribution_a_un_autre(systeme_factory):
    sma = await systeme_factory()
    await livreur(sma, 1, pres(PLATEAU, 0.002))
    await livreur(sma, 2, pres(PLATEAU, 0.01))
    await inscrits(sma, 2)
    tel = TelephonesVirtuels(sma, comportements={1: Comportement(incident="panne")})
    tel.demarrer()
    cmd = await commander(sma, PLATEAU, COCODY)
    fin = await attendre(etat_vaut(sma, cmd.id, Etat.CLOTUREE), timeout=20)
    assert fin.livreur_id == 2  # le livreur en panne est exclu de la nouvelle recherche
    await tel.arreter()


async def test_destinataire_absent_echec_de_livraison(systeme_factory):
    sma = await systeme_factory()
    await livreur(sma, 1, pres(PLATEAU, 0.002))
    await inscrits(sma, 1)
    tel = TelephonesVirtuels(sma, defaut=Comportement(incident="destinataire-absent"))
    tel.demarrer()
    cmd = await commander(sma, PLATEAU, COCODY)
    fin = await attendre(etat_vaut(sma, cmd.id, Etat.ECHEC), timeout=15)
    assert fin.motif == "destinataire-absent"
    await tel.arreter()


async def test_code_de_confirmation_incorrect_refuse(systeme_factory):
    sma = await systeme_factory()
    await livreur(sma, 1, pres(PLATEAU, 0.002))
    await inscrits(sma, 1)
    cmd = await commander(sma, PLATEAU, COCODY)
    # Réponse manuelle du livreur (sans téléphone virtuel).
    prop = await attendre(lambda: sma.passerelle.boite(1), timeout=10)
    sma.passerelle.transmettre(P.nom_livreur(1), P.Perf.REQUEST, "reponse",
                               {"conversation": prop[0]["conversation"], "accepte": True})
    await attendre(etat_vaut(sma, cmd.id, Etat.ATTRIBUEE))
    sma.passerelle.transmettre(P.nom_livreur(1), P.Perf.REQUEST, "enlevement", {"commande_id": cmd.id})
    await attendre(etat_vaut(sma, cmd.id, Etat.ENLEVEE))
    sma.passerelle.transmettre(P.nom_livreur(1), P.Perf.REQUEST, "remise",
                               {"commande_id": cmd.id, "code": "mauvais"})
    await attendre(lambda: any(m["type"] == "remise-resultat" for m in sma.passerelle.boite(1)))
    assert (await sma.ctx.depot.commande(cmd.id)).etat == Etat.ENLEVEE
    bon = (await sma.ctx.depot.commande(cmd.id)).code
    sma.passerelle.transmettre(P.nom_livreur(1), P.Perf.REQUEST, "remise",
                               {"commande_id": cmd.id, "code": bon})
    await attendre(etat_vaut(sma, cmd.id, Etat.CLOTUREE))


async def test_annulation_avant_attribution_sans_frais(systeme_factory):
    sma = await systeme_factory(attente_relance_s=60)
    cmd = await commander(sma, PLATEAU, COCODY)
    await attendre(lambda: cmd.id in sma.dispatcher.attente, timeout=10)
    fut = sma.passerelle.transmettre(P.nom_commande(cmd.id), P.Perf.REQUEST, "annuler",
                                     {"motif": "annulée par le client"}, attendre=True)
    rep = await asyncio.wait_for(fut, 5)
    assert rep["performative"] == "agree" and rep["frais"] is False
    await attendre(etat_vaut(sma, cmd.id, Etat.ANNULEE))
    assert cmd.id not in sma.dispatcher.attente


async def test_paiement_mobile_money_puis_attribution(systeme_factory):
    sma = await systeme_factory()
    await livreur(sma, 1, pres(PLATEAU, 0.002))
    await inscrits(sma, 1)
    tel = TelephonesVirtuels(sma)
    tel.demarrer()
    cmd = await commander(sma, PLATEAU, COCODY, mode_paiement="mobile_money", prix=1500)
    await attendre(etat_vaut(sma, cmd.id, Etat.CLOTUREE), timeout=15)
    paiements = await sma.ctx.depot.paiements(cmd.id)
    assert paiements[0]["etat"] == "confirme" and paiements[0]["montant"] == 1500
    await tel.arreter()


async def test_especes_suivies_par_livreur(systeme_factory):
    """F-52 : les espèces encaissées à la livraison sont comptées dans le solde du livreur."""
    sma = await systeme_factory()
    await livreur(sma, 1, pres(PLATEAU, 0.002))
    await inscrits(sma, 1)
    tel = TelephonesVirtuels(sma)
    tel.demarrer()
    cmd = await commander(sma, PLATEAU, COCODY, prix=2000)
    await attendre(etat_vaut(sma, cmd.id, Etat.CLOTUREE))
    await attendre(lambda: sma.ctx.depot._livreurs[1].especes_detenues == 2000)
    await tel.arreter()


async def test_reattribution_manuelle_a_un_livreur_impose(systeme_factory):
    """F-62 : le répartiteur retire la course au livreur 1 et l'impose au livreur 2."""
    sma = await systeme_factory()
    await livreur(sma, 1, pres(PLATEAU, 0.002))
    await livreur(sma, 2, pres(COCODY))
    await inscrits(sma, 2)
    cmd = await commander(sma, PLATEAU, COCODY)
    prop = await attendre(lambda: sma.passerelle.boite(1), timeout=10)
    sma.passerelle.transmettre(P.nom_livreur(1), P.Perf.REQUEST, "reponse",
                               {"conversation": prop[0]["conversation"], "accepte": True})
    await attendre(etat_vaut(sma, cmd.id, Etat.ATTRIBUEE))
    tel = TelephonesVirtuels(sma)
    tel.demarrer()
    fut = sma.passerelle.transmettre(P.nom_commande(cmd.id), P.Perf.REQUEST, "reattribuer",
                                     {"livreur_id": 2}, attendre=True)
    assert (await asyncio.wait_for(fut, 5))["performative"] == "agree"
    fin = await attendre(etat_vaut(sma, cmd.id, Etat.CLOTUREE), timeout=15)
    assert fin.livreur_id == 2
    assert cmd.id not in sma.livreurs[1].courses
    await tel.arreter()


async def test_reprise_apres_redemarrage(systeme_factory):
    """F-36 et recette : après redémarrage du serveur, les commandes en cours reprennent."""
    sma = await systeme_factory(attente_relance_s=60)
    cmd = await commander(sma, PLATEAU, COCODY)
    await attendre(lambda: cmd.id in sma.dispatcher.attente, timeout=10)
    depot = sma.ctx.depot
    await sma.arreter()  # « panne » du serveur

    sma2 = await systeme_factory()
    sma2.ctx.depot = depot  # même base de données
    assert await sma2.reprendre() == 1
    tel = TelephonesVirtuels(sma2)
    tel.demarrer()
    await livreur(sma2, 3, pres(PLATEAU, 0.002))
    fin = await attendre(etat_vaut(sma2, cmd.id, Etat.CLOTUREE), timeout=20)
    assert fin.livreur_id == 3
    await tel.arreter()


async def test_livreur_juste_connecte_sans_position_reste_en_ligne(systeme_factory):
    """Non-régression (constaté sous Windows) : entre la connexion et l'arrivée de la première
    position, le ménage périodique (toutes les 2 s) ne doit pas mettre le livreur hors ligne."""
    sma = await systeme_factory()
    lv = await sma.ctx.depot.creer_livreur(Livreur(90, "Livreur 90", "0700000090", valide=True))
    agent = await sma.connecter_livreur(lv)  # position pas encore reçue
    assert agent.age_gps() < 5
    await asyncio.sleep(3)  # au moins un passage du ménage
    assert lv.id in sma.livreurs
    sma.passerelle.transmettre(agent.nom, P.Perf.INFORM, "position", pres(PLATEAU, 0.002).vers_dict())
    await inscrits(sma, 1)
