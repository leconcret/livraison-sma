"""Parcours complet par l'API (recette § 9, premier point) et contrôle des droits.

Un client commande et paie en espèces ; un livreur validé passe en ligne, reçoit la course,
l'accepte, enlève le colis et le livre avec le code ; la commande est clôturée.
"""
import asyncio

from conftest import attendre_etat, connexion
from livraison.config import parametres
from livraison.modele.geo import COMMUNES_ABIDJAN as C


async def test_parcours_complet(api):
    plateau, cocody = C["Plateau"], C["Cocody"]
    # Livreur : inscription, validation par l'administrateur, connexion
    h_l, _ = await connexion(api, "0707070707", "Kouassi")
    r = await api.post("/livreurs/inscription", headers=h_l, json={"nom": "Kouassi", "capacite": 2})
    lid = r.json()["livreur"]["id"]
    h_admin, _ = await connexion(api, parametres.admin_telephone)
    assert (await api.post(f"/admin/livreurs/{lid}/valider", headers=h_admin)).status_code == 200
    h_l, info = await connexion(api, "0707070707")
    assert info["role"] == "livreur" and info["livreur_id"] == lid
    r = await api.post("/livreur/en-ligne", headers=h_l,
                       json={"lat": plateau.lat + 0.003, "lon": plateau.lon})
    assert r.status_code == 200

    # Client : devis puis commande
    h_c, _ = await connexion(api, "0505050505", "Aminata")
    trajet = {"enlevement": plateau.vers_dict(), "livraison": cocody.vers_dict()}
    dv = (await api.post("/devis", json=trajet)).json()
    assert dv["prix"] >= 700 and dv["devise"] == "FCFA"
    r = await api.post("/commandes", headers=h_c, json={**trajet, "destinataire": "Moussa"})
    assert r.status_code == 201
    cmd = r.json()["commande"]
    code = cmd["code"]

    # Le téléphone du livreur reçoit la proposition et l'accepte
    for _ in range(100):
        boite = (await api.get("/livreur/boite", headers=h_l)).json()
        if boite:
            break
        await asyncio.sleep(0.1)
    prop = boite[0]
    assert prop["type"] == "proposition" and prop["commande"]["id"] == cmd["id"]
    await api.post("/livreur/reponse", headers=h_l, json={"conversation": prop["conversation"],
                                                          "accepte": True})
    await attendre_etat(api, h_c, cmd["id"], "attribuee")
    await api.post("/livreur/enlevement", headers=h_l, json={"commande_id": cmd["id"]})
    await attendre_etat(api, h_c, cmd["id"], "enlevee")
    await api.post("/livreur/position", headers=h_l, json=cocody.vers_dict())
    await api.post("/livreur/remise", headers=h_l, json={"commande_id": cmd["id"], "code": code})
    fin = await attendre_etat(api, h_c, cmd["id"], "cloturee")
    assert fin["livreur_id"] == lid

    # Supervision : journal des messages et statistiques
    h_r, _ = await connexion(api, parametres.repartiteur_telephone)
    det = (await api.get(f"/commandes/{cmd['id']}", headers=h_r)).json()
    perfs = {e.get("performative") for e in det["journal"] if e["categorie"] == "message"}
    assert {"cfp", "propose", "accept-proposal", "agree"} <= perfs
    stats = (await api.get("/supervision/statistiques", headers=h_r)).json()
    assert stats["par_etat"].get("cloturee") == 1
    gains = (await api.get("/livreur/courses", headers=h_l)).json()
    assert gains["especes_a_reverser"] == fin["prix"]


async def test_droits(api):
    plateau, cocody = C["Plateau"], C["Cocody"]
    h1, _ = await connexion(api, "0501010101")
    h2, _ = await connexion(api, "0502020202")
    cmd = (await api.post("/commandes", headers=h1, json={
        "enlevement": plateau.vers_dict(), "livraison": cocody.vers_dict()})).json()["commande"]
    # Un client ne peut pas consulter la commande d'un autre (recette § 9)
    assert (await api.get(f"/commandes/{cmd['id']}", headers=h2)).status_code == 403
    assert all(c["id"] != cmd["id"] for c in (await api.get("/commandes", headers=h2)).json())
    # Un client n'accède pas à la supervision ; sans jeton, rien
    assert (await api.get("/supervision/etat", headers=h1)).status_code == 403
    assert (await api.get("/commandes")).status_code == 401
    # Un livreur non validé ne peut pas passer en ligne, donc ne reçoit aucune course
    hl, _ = await connexion(api, "0709090909")
    await api.post("/livreurs/inscription", headers=hl, json={"nom": "Non validé"})
    hl, _ = await connexion(api, "0709090909")
    r = await api.post("/livreur/en-ligne", headers=hl, json=plateau.vers_dict())
    assert r.status_code == 403
    # Hors zone de service
    r = await api.post("/devis", json={"enlevement": {"lat": 6.82, "lon": -5.28},
                                       "livraison": cocody.vers_dict()})
    assert r.status_code == 422


async def test_code_de_connexion_limite_en_essais(api):
    await api.post("/auth/code", json={"telephone": "0500000099"})
    statuts = [(await api.post("/auth/jeton", json={"telephone": "0500000099", "code": "000000"}))
               .status_code for _ in range(6)]
    assert statuts[:5] == [401] * 5 and statuts[5] == 429
