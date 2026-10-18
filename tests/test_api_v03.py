"""Version 0.3 : sessions (F-01), prix garanti (F-11), livraison programmée (F-16), pagination,
facture PDF (F-53), évaluation du livreur (F-15), relevé et reversement (F-54)."""
import asyncio
from datetime import UTC, datetime, timedelta

from conftest import attendre_etat, connexion
from livraison.config import parametres
from livraison.modele.geo import COMMUNES_ABIDJAN as C

TRAJET = {"enlevement": C["Plateau"].vers_dict(), "livraison": C["Cocody"].vers_dict()}


async def livreur_en_ligne(api, tel="0706060606"):
    h, _ = await connexion(api, tel, "Konan")
    lid = (await api.post("/livreurs/inscription", headers=h, json={"nom": "Konan"})).json()["livreur"]["id"]
    h_admin, _ = await connexion(api, parametres.admin_telephone)
    await api.post(f"/admin/livreurs/{lid}/valider", headers=h_admin)
    h, _ = await connexion(api, tel)
    p = C["Plateau"]
    await api.post("/livreur/en-ligne", headers=h, json={"lat": p.lat + 0.002, "lon": p.lon})
    return h, lid, h_admin


async def livrer(api, h_l, cmd):
    for _ in range(100):
        boite = [m for m in (await api.get("/livreur/boite", headers=h_l)).json() if m["type"] == "proposition"]
        if boite:
            break
        await asyncio.sleep(0.1)
    await api.post("/livreur/reponse", headers=h_l, json={"conversation": boite[0]["conversation"],
                                                          "accepte": True})
    await asyncio.sleep(0.3)
    await api.post("/livreur/enlevement", headers=h_l, json={"commande_id": cmd["id"]})
    await asyncio.sleep(0.3)
    await api.post("/livreur/remise", headers=h_l, json={"commande_id": cmd["id"], "code": cmd["code"]})


async def test_session_rafraichir_deconnexion_moi(api):
    h, _ = await connexion(api, "0511111111", "Adjoua")
    moi = (await api.get("/moi", headers=h)).json()
    assert moi["role"] == "client" and moi["nom"] == "Adjoua"
    nouveau = (await api.post("/auth/rafraichir", headers=h)).json()["jeton"]
    assert (await api.get("/moi", headers=h)).status_code == 401  # l'ancien jeton est révoqué
    h2 = {"Authorization": f"Bearer {nouveau}"}
    assert (await api.get("/moi", headers=h2)).status_code == 200
    await api.post("/auth/deconnexion", headers=h2)
    assert (await api.get("/moi", headers=h2)).status_code == 401


async def test_prix_garanti_et_pagination(api):
    h, _ = await connexion(api, "0512121212")
    prix = (await api.post("/devis", json=TRAJET)).json()["prix"]
    r = await api.post("/commandes", headers=h, json={**TRAJET, "prix_attendu": prix + 100})
    assert r.status_code == 409 and r.json()["detail"]["nouveau_prix"] == prix
    for _ in range(3):
        assert (await api.post("/commandes", headers=h, json={**TRAJET, "prix_attendu": prix})).status_code == 201
    page1 = (await api.get("/commandes?limite=2", headers=h)).json()
    page2 = (await api.get("/commandes?limite=2&decalage=2", headers=h)).json()
    assert len(page1) == 2 and len(page2) == 1 and page1[0]["id"] > page2[0]["id"]


async def test_livraison_programmee_attend_son_heure(api):
    h, _ = await connexion(api, "0513131313")
    demain = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    cmd = (await api.post("/commandes", headers=h, json={**TRAJET, "programmee_pour": demain})).json()["commande"]
    await asyncio.sleep(1.0)
    c = (await api.get(f"/commandes/{cmd['id']}", headers=h)).json()["commande"]
    assert c["etat"] == "validee" and c["programmee_pour"].startswith(demain[:16])
    trop_tard = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    assert (await api.post("/commandes", headers=h, json={**TRAJET, "programmee_pour": trop_tard})).status_code == 422


async def test_facture_evaluation_releve_reversement(api):
    h_l, lid, h_admin = await livreur_en_ligne(api)
    h_c, _ = await connexion(api, "0514141414", "Fanta")
    cmd = (await api.post("/commandes", headers=h_c, json=TRAJET)).json()["commande"]
    await livrer(api, h_l, cmd)
    await attendre_etat(api, h_c, cmd["id"], "cloturee")
    for _ in range(50):
        r = await api.get(f"/commandes/{cmd['id']}/facture?format=json", headers=h_c)
        if r.status_code == 200:
            break
        await asyncio.sleep(0.1)
    f = r.json()
    assert f["numero"].startswith("FAC-") and f["montant_ttc"] == cmd["prix"]
    assert f["gain_livreur"] + f["commission"] == f["montant_ttc"]
    pdf = await api.get(f"/commandes/{cmd['id']}/facture", headers=h_c)
    assert pdf.headers["content-type"] == "application/pdf" and pdf.content[:4] == b"%PDF"
    # Un autre client ne peut pas lire la facture
    h_autre, _ = await connexion(api, "0515151515")
    assert (await api.get(f"/commandes/{cmd['id']}/facture", headers=h_autre)).status_code == 403
    # Évaluation : une seule fois, la note du livreur évolue
    ev = (await api.post(f"/commandes/{cmd['id']}/evaluation", headers=h_c, json={"note": 2})).json()
    assert ev["note_livreur"] < 4.5
    assert (await api.post(f"/commandes/{cmd['id']}/evaluation", headers=h_c,
                           json={"note": 5})).status_code == 409
    # Relevé : le livreur détient les espèces, doit reverser la commission
    rel = (await api.get("/livreur/releve", headers=h_l)).json()
    assert rel["especes_detenues"] == cmd["prix"] and rel["solde"] == -f["commission"]
    rel = (await api.post(f"/admin/livreurs/{lid}/reversement", headers=h_admin,
                          json={"montant": cmd["prix"]})).json()
    assert rel["especes_detenues"] == 0 and rel["solde"] == f["gain_livreur"]
