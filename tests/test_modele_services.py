"""Tests unitaires (NF-10) : coût, tarification, cycle de vie, géographie, itinéraires."""
from datetime import datetime

import pytest

from livraison.modele.cout import (
    Coefficients,
    Offre,
    Taille,
    Vehicule,
    classer_offres,
    cout_itineraire,
    taille_acceptee,
)
from livraison.modele.etats import Etat, TransitionInvalide, verifier_transition
from livraison.modele.geo import COMMUNES_ABIDJAN, ZONE_ABIDJAN, Point, distance_km
from livraison.services.itineraires import MoteurEstime, MoteurGrille, MoteurOSRM
from livraison.services.tarification import Grille, calculer_devis


# ---------------------------------------------------------------- coût (§ 3.2)
def test_cout_itineraire_formule():
    coef = Coefficients(penalite_charge_s=300)
    assert cout_itineraire(600, 0, 0, coef) == 600
    assert cout_itineraire(600, 240, 2, coef) == 600 + 240 + 600
    assert cout_itineraire(600, -10, 0, coef) == 600  # un détour n'est jamais négatif


def test_classement_cout_puis_note_puis_ordre():
    offres = [Offre("a", 10, note=4.0, ordre=0), Offre("b", 8, ordre=1),
              Offre("c", 10, note=4.8, ordre=2), Offre("d", 10, note=4.8, ordre=3)]
    assert [o.livreur for o in classer_offres(offres)] == ["b", "c", "d", "a"]


def test_taille_selon_vehicule():
    assert taille_acceptee(Vehicule.MOTO, Taille.MOYEN)
    assert not taille_acceptee(Vehicule.MOTO, Taille.GRAND)
    assert taille_acceptee(Vehicule.TRICYCLE, Taille.GRAND)


# ------------------------------------------------------------- cycle de vie (§ 3.4)
def test_cycle_de_vie_nominal():
    e = Etat.CREEE
    for suivant in (Etat.VALIDEE, Etat.RECHERCHE, Etat.ATTRIBUEE, Etat.ENLEVEE, Etat.LIVREE,
                    Etat.CLOTUREE):
        e = verifier_transition(e, suivant)
    assert e == Etat.CLOTUREE


@pytest.mark.parametrize("de,vers", [(Etat.CREEE, Etat.LIVREE), (Etat.RECHERCHE, Etat.ENLEVEE),
                                     (Etat.ANNULEE, Etat.RECHERCHE), (Etat.ENLEVEE, Etat.ANNULEE)])
def test_transitions_interdites(de, vers):
    with pytest.raises(TransitionInvalide):
        verifier_transition(de, vers)


# --------------------------------------------------------------- géographie
def test_distance_plateau_cocody():
    d = distance_km(COMMUNES_ABIDJAN["Plateau"], COMMUNES_ABIDJAN["Cocody"])
    assert 3.5 < d < 5.0


def test_zone_abidjan():
    assert ZONE_ABIDJAN.contient(COMMUNES_ABIDJAN["Yopougon"])
    assert not ZONE_ABIDJAN.contient(Point(5.85, -5.35))  # Yamoussoukro... ou presque


# --------------------------------------------------------------- tarification (F-50)
def test_devis_jour_et_majorations():
    g = Grille()
    jour = datetime(2026, 10, 17, 10)
    d = calculer_devis(5000, 900, Taille.PETIT, moment=jour, grille=g)
    assert d.prix == 1250  # 500 + 5 km x 150
    assert calculer_devis(5000, 900, Taille.GRAND, moment=jour).prix == 2050
    assert calculer_devis(5000, 900, urgent=True, moment=jour).prix == 1900  # 1250 x 1,5 arrondi
    assert calculer_devis(5000, 900, moment=datetime(2026, 10, 17, 23)).prix == 1500
    assert calculer_devis(100, 60, moment=jour).prix == g.prix_minimum


# --------------------------------------------------------------- itinéraires (F-42)
async def test_estimation_moto_plus_rapide_que_velo():
    m = MoteurEstime()
    a, b = COMMUNES_ABIDJAN["Plateau"], COMMUNES_ABIDJAN["Cocody"]
    moto, velo = await m.trajet(a, b, Vehicule.MOTO), await m.trajet(a, b, Vehicule.VELO)
    assert moto.duree_s < velo.duree_s
    assert moto.distance_m > distance_km(a, b) * 1000  # la route est plus longue que le vol d'oiseau


async def test_grille_du_tp():
    t = await MoteurGrille().trajet(Point(1, 1), Point(0, 3))
    assert t.duree_s == 3


async def test_osrm_absent_bascule_sur_estimation():
    m = MoteurOSRM("http://127.0.0.1:9", timeout=0.5)
    assert not await m.verifier()
    t = await m.trajet(COMMUNES_ABIDJAN["Plateau"], COMMUNES_ABIDJAN["Cocody"])
    assert t.source == "estimation"
