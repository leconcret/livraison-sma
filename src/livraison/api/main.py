"""Serveur applicatif (§ 7). Lancement : uvicorn livraison.api.main:app --reload

Le serveur démarre le système multi-agents dans sa propre boucle asyncio (lifespan) et traduit
les actions des utilisateurs en messages pour les agents, via l'agent Passerelle. Les
applications ne parlent jamais directement aux agents.

Documentation interactive : http://127.0.0.1:8000/docs
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from fastapi import Depends, FastAPI, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from livraison.agents import protocole as P
from livraison.agents.base import Contexte, Delais
from livraison.agents.systeme import ErreurSysteme, SystemeMultiAgents
from livraison.api.securite import (
    creer_jeton,
    emettre_code,
    exiger,
    lire_jeton,
    revoquer,
    utilisateur_courant,
    utilisateur_entete_ou_lien,
    verifier_code,
)
from livraison.config import parametres
from livraison.donnees.depot import DepotMemoire
from livraison.modele.cout import Taille, Vehicule
from livraison.modele.entites import Commande, Livreur
from livraison.modele.etats import ETATS_ACTIFS, Etat
from livraison.modele.geo import ZONE_ABIDJAN, Point
from livraison.services.evenements import Bus
from livraison.services.itineraires import creer_moteur
from livraison.services.tarification import calculer_devis

log = logging.getLogger("livraison.api")


# ------------------------------------------------------------------ schémas
class PointE(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)

    def p(self) -> Point:
        return Point(self.lat, self.lon)


class DemandeCode(BaseModel):
    telephone: str = Field(min_length=8, max_length=20)


class DemandeJeton(BaseModel):
    telephone: str
    code: str
    nom: str = ""


class DemandeDevis(BaseModel):
    enlevement: PointE
    livraison: PointE
    taille: Taille = Taille.PETIT
    urgent: bool = False


class NouvelleCommande(DemandeDevis):
    adresse_enlevement: str = ""
    adresse_livraison: str = ""
    destinataire: str = ""
    telephone_destinataire: str = ""
    consignes: str = ""
    mode_paiement: str = Field("especes", pattern="^(especes|mobile_money|carte)$")
    # F-11 : prix vu par le client sur le devis ; la commande est refusée si le tarif a changé.
    prix_attendu: int | None = None
    # F-16 : livraison programmée (date et heure, avec fuseau ; au plus 7 jours à l'avance).
    programmee_pour: datetime | None = None


class Evaluation(BaseModel):
    note: int = Field(ge=1, le=5)
    commentaire: str = Field("", max_length=500)


class Reversement(BaseModel):
    montant: int = Field(gt=0)


class InscriptionLivreur(BaseModel):
    nom: str
    vehicule: Vehicule = Vehicule.MOTO
    capacite: int = Field(2, ge=1, le=6)


class Reponse(BaseModel):
    conversation: str
    accepte: bool


class Etape(BaseModel):
    commande_id: int
    code: str = ""
    motif: str = ""


class Reattribution(BaseModel):
    livreur_id: int | None = None


# ------------------------------------------------------------- application
class Etat_:
    sma: SystemeMultiAgents
    depot: object
    bus: Bus
    moteur: object


etat = Etat_()


async def construire_depot():
    if parametres.depot == "memoire":
        log.warning("Dépôt en mémoire : les données seront perdues à l'arrêt du serveur")
        return DepotMemoire()
    from livraison.donnees.sql import DepotSQL

    d = DepotSQL(parametres.postgres_dsn)
    try:
        await d.initialiser()
    except Exception as e:
        raise RuntimeError(
            f"PostgreSQL injoignable ({parametres.postgres_host}:{parametres.postgres_port}) : "
            f"lancez « docker compose up -d », ou mettez DEPOT=memoire dans .env. Détail : {e}") from e
    return d


async def comptes_initiaux(depot) -> None:
    for tel, role, nom in ((parametres.admin_telephone, "admin", "Administrateur"),
                           (parametres.repartiteur_telephone, "repartiteur", "Répartiteur")):
        if not await depot.utilisateur_par_telephone(tel):
            await depot.creer_utilisateur(tel, nom, role)


@asynccontextmanager
async def cycle_de_vie(app: FastAPI):
    logging.basicConfig(level=logging.INFO)
    etat.depot = await construire_depot()
    etat.bus = Bus(etat.depot)
    etat.moteur = await creer_moteur(parametres.osrm_url)
    ctx = Contexte(depot=etat.depot, bus=etat.bus, moteur=etat.moteur,
                   domaine=parametres.xmpp_domaine, mot_de_passe=parametres.xmpp_mot_de_passe_agents,
                   port=parametres.xmpp_port,
                   delais=Delais(confirmation_humaine_s=parametres.delai_confirmation_s,
                                 gps_alerte_s=parametres.gps_alerte_s,
                                 gps_reattribution_s=parametres.gps_reattribution_s))
    etat.sma = SystemeMultiAgents(ctx)
    await etat.sma.demarrer()
    await comptes_initiaux(etat.depot)
    await etat.sma.reprendre()
    yield
    await etat.sma.arreter()
    await etat.depot.fermer()


app = FastAPI(title="Livraison SMA", version="0.2.0", lifespan=cycle_de_vie,
              description="Plateforme de livraison urbaine à allocation par Contract Net (SPADE).")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


CLIENT = exiger("client", "repartiteur", "admin")
LIVREUR = exiger("livreur")
ADMIN = exiger("admin")
SUPERVISION = exiger("repartiteur", "admin")
EVAL_CLIENT = exiger("client")


def _passerelle():
    return etat.sma.passerelle


async def _commande_autorisee(cid: int, u: dict) -> Commande:
    c = await etat.depot.commande(cid)
    if c is None:
        raise HTTPException(404, "commande inconnue")
    if u["role"] == "client" and c.client != u["sub"]:
        raise HTTPException(403, "cette commande ne vous appartient pas")  # recette § 9
    if u["role"] == "livreur" and c.livreur_id != u.get("livreur_id"):
        raise HTTPException(403, "cette commande ne vous est pas attribuée")
    return c


# ------------------------------------------------------------------ général
@app.get("/sante", tags=["général"])
async def sante() -> dict:
    """Point de contrôle utilisé pour vérifier que le serveur répond."""
    return {"etat": "ok", "agents": etat.sma.etat()["agents"] if getattr(etat, "sma", None) else 0}


# --------------------------------------------------------------- comptes (F-01, F-02)
@app.post("/auth/code", tags=["comptes"])
async def demander_code(d: DemandeCode) -> dict:
    code = emettre_code(d.telephone)
    etat.bus.publier("notification", canal="sms", destinataire=d.telephone,
                     texte="Code de connexion envoyé (SMS simulé)")
    return {"envoye": True, **({"code_dev": code} if parametres.mode_dev else {})}


@app.post("/auth/jeton", tags=["comptes"])
async def obtenir_jeton(d: DemandeJeton) -> dict:
    if not verifier_code(d.telephone, d.code):
        raise HTTPException(401, "code incorrect ou expiré")
    u = await etat.depot.utilisateur_par_telephone(d.telephone)
    if u is None:
        u = await etat.depot.creer_utilisateur(d.telephone, d.nom, "client")
    livreur_id = None
    if u["role"] == "livreur":
        livreur_id = next((lv.id for lv in await etat.depot.livreurs()
                           if lv.telephone == d.telephone), None)
    return {"jeton": creer_jeton(d.telephone, u["role"], livreur_id), "role": u["role"],
            "livreur_id": livreur_id}


@app.post("/auth/rafraichir", tags=["comptes"])
async def rafraichir_jeton(u: dict = Depends(utilisateur_courant)) -> dict:
    """Nouveau jeton pour une session encore valide ; l'ancien est révoqué."""
    revoquer(u)
    return {"jeton": creer_jeton(u["sub"], u["role"], u.get("livreur_id")), "role": u["role"]}


@app.post("/auth/deconnexion", tags=["comptes"])
async def deconnexion(u: dict = Depends(utilisateur_courant)) -> dict:
    revoquer(u)
    return {"deconnecte": True}


@app.get("/moi", tags=["comptes"])
async def moi(u: dict = Depends(utilisateur_courant)) -> dict:
    profil = await etat.depot.utilisateur_par_telephone(u["sub"]) or {}
    res = {**profil, "role": u["role"], "expire_a": datetime.fromtimestamp(u["exp"], UTC).isoformat()}
    if u.get("livreur_id") is not None:
        lv = await etat.depot.livreur(int(u["livreur_id"]))
        res["livreur"] = lv.vers_dict() if lv else None
        res["en_ligne"] = int(u["livreur_id"]) in etat.sma.livreurs
    return res


@app.post("/livreurs/inscription", tags=["comptes"])
async def inscrire_livreur(d: InscriptionLivreur, u: dict = Depends(utilisateur_courant)) -> dict:
    """Dossier livreur (F-03) : le compte doit ensuite être validé par l'administrateur."""
    if any(lv.telephone == u["sub"] for lv in await etat.depot.livreurs()):
        raise HTTPException(409, "un dossier livreur existe déjà pour ce numéro")
    lv = await etat.depot.creer_livreur(Livreur(0, d.nom, u["sub"], d.vehicule, d.capacite))
    if hasattr(etat.depot, "session"):
        from sqlalchemy import update

        from livraison.donnees.sql import UtilisateurT
        async with etat.depot.session.begin() as s:
            await s.execute(update(UtilisateurT).where(UtilisateurT.telephone == u["sub"])
                            .values(role="livreur", nom=d.nom))
    else:
        etat.depot._utilisateurs[u["sub"]]["role"] = "livreur"
    return {"livreur": lv.vers_dict(), "message": "dossier créé, en attente de validation ; "
            "reconnectez-vous pour obtenir un jeton livreur"}


# ------------------------------------------------------------- client (F-10 à F-15)
@app.post("/devis", tags=["client"])
async def devis(d: DemandeDevis) -> dict:
    """Prix et délai estimés avant validation (F-11) ; refus hors zone de service (F-43)."""
    for p in (d.enlevement.p(), d.livraison.p()):
        if not ZONE_ABIDJAN.contient(p):
            raise HTTPException(422, f"adresse hors de la zone de service ({ZONE_ABIDJAN.nom})")
    t = await etat.moteur.trajet(d.enlevement.p(), d.livraison.p(), Vehicule.MOTO)
    dv = calculer_devis(t.distance_m, t.duree_s, d.taille, d.urgent)
    return {"prix": dv.prix, "devise": "FCFA", "distance_km": dv.distance_km,
            "duree_trajet_min": round(dv.duree_trajet_s / 60), "delai_estime_min":
            round(dv.delai_estime_s / 60), "detail": dv.detail, "itineraire": t.source}


@app.post("/commandes", tags=["client"], status_code=201)
async def commander(d: NouvelleCommande, u: dict = Depends(CLIENT)):
    dv = await devis(d)
    if d.prix_attendu is not None and d.prix_attendu != dv["prix"]:
        raise HTTPException(409, {"message": "le tarif a changé depuis le devis",
                                  "prix_attendu": d.prix_attendu, "nouveau_prix": dv["prix"]})
    programmee = None
    if d.programmee_pour is not None:
        programmee = d.programmee_pour if d.programmee_pour.tzinfo else d.programmee_pour.replace(tzinfo=UTC)
        if not (datetime.now(UTC) < programmee < datetime.now(UTC) + timedelta(days=7)):
            raise HTTPException(422, "livraison programmée : date dans les 7 prochains jours")
    c = Commande(0, u["sub"], d.enlevement.p(), d.livraison.p(), d.adresse_enlevement,
                 d.adresse_livraison, d.destinataire, d.telephone_destinataire, d.taille, d.urgent,
                 d.consignes, d.mode_paiement, dv["prix"], dv["distance_km"])
    c.programmee_pour = programmee
    c = await etat.depot.creer_commande(c)
    await etat.sma.nouvelle_commande(c)
    return {"commande": c.vers_dict(avec_code=True), "devis": dv,
            "information": "Communiquez le code au destinataire : il le donnera au livreur."}


@app.get("/commandes", tags=["client", "supervision"])
async def lister_commandes(etat_: str | None = Query(None, alias="etat"),
                           actives: bool = False, limite: int = Query(50, ge=1, le=500),
                           decalage: int = Query(0, ge=0),
                           u: dict = Depends(utilisateur_courant)) -> list[dict]:
    etats = {Etat(e) for e in etat_.split(",")} if etat_ else (ETATS_ACTIFS if actives else None)
    page = {"limite": limite, "decalage": decalage}
    if u["role"] == "client":
        cs = await etat.depot.commandes(etats, client=u["sub"], **page)
    elif u["role"] == "livreur":
        cs = await etat.depot.commandes(etats, livreur_id=u.get("livreur_id"), **page)
    else:
        cs = await etat.depot.commandes(etats, **page)
    return [c.vers_dict(avec_code=u["role"] == "client") for c in cs]


@app.get("/commandes/{cid}", tags=["client", "supervision"])
async def detail_commande(cid: int, u: dict = Depends(utilisateur_courant)) -> dict:
    c = await _commande_autorisee(cid, u)
    suivi = None
    if c.livreur_id and c.livreur_id in etat.sma.livreurs:
        suivi = etat.sma.livreurs[c.livreur_id].etat_public()  # F-12 : position du livreur
    reponse = {"commande": c.vers_dict(avec_code=u["role"] == "client"), "livreur": suivi}
    if u["role"] in ("repartiteur", "admin"):
        reponse["journal"] = await etat.depot.evenements(cid)
        reponse["offres"] = await etat.depot.offres(cid)
        reponse["paiements"] = await etat.depot.paiements(cid)
    return reponse


@app.get("/commandes/{cid}/facture", tags=["client", "supervision"])
async def facture(cid: int, format_: str = Query("pdf", alias="format"),
                  u: dict = Depends(utilisateur_entete_ou_lien)):
    """F-53 : facture numérotée de la commande livrée, en PDF (ou en JSON avec ?format=json)."""
    c = await _commande_autorisee(cid, u)
    f = await etat.depot.facture(cid)
    if f is None:
        raise HTTPException(404, "pas encore de facture : elle est émise à la clôture de la commande")
    if format_ == "json":
        return f
    from livraison.services.facturation import facture_pdf
    return Response(facture_pdf(f, c), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{f["numero"]}.pdf"'})


@app.post("/commandes/{cid}/evaluation", tags=["client"])
async def evaluer(cid: int, e: Evaluation, u: dict = Depends(EVAL_CLIENT)) -> dict:
    """F-15 : le client note la livraison ; la note du livreur départage les offres égales."""
    c = await _commande_autorisee(cid, u)
    if c.etat not in (Etat.LIVREE, Etat.CLOTUREE):
        raise HTTPException(409, "la commande n'est pas encore livrée")
    if c.note_client is not None:
        raise HTTPException(409, "commande déjà évaluée")
    c.note_client, c.commentaire_client = e.note, e.commentaire
    await etat.depot.maj_commande(c)
    lv = await etat.depot.livreur(c.livreur_id)
    # Moyenne lissée : la note initiale (4,5) compte comme une évaluation.
    nouvelle = round((lv.note * (lv.nb_evaluations + 1) + e.note) / (lv.nb_evaluations + 2), 2)
    await etat.depot.maj_livreur(lv.id, note=nouvelle, nb_evaluations=lv.nb_evaluations + 1)
    if lv.id in etat.sma.livreurs:
        _passerelle().transmettre(P.nom_livreur(lv.id), P.Perf.INFORM, "note", {"note": nouvelle})
    etat.bus.publier("evaluation", commande_id=cid, livreur_id=lv.id, note=e.note, moyenne=nouvelle)
    return {"commande_id": cid, "note": e.note, "note_livreur": nouvelle}


@app.post("/commandes/{cid}/annuler", tags=["client", "supervision"])
async def annuler(cid: int, u: dict = Depends(utilisateur_courant)) -> dict:
    await _commande_autorisee(cid, u)
    if cid not in etat.sma.commandes:
        raise HTTPException(409, "commande déjà terminée")
    fut = _passerelle().transmettre(P.nom_commande(cid), P.Perf.REQUEST, "annuler",
                                    {"motif": f"annulée par {u['role']}"}, attendre=True)
    rep = await asyncio.wait_for(fut, 10)
    if rep["performative"] != "agree":
        raise HTTPException(409, rep.get("motif", "annulation refusée"))
    return rep


# ----------------------------------------------------------- livreur (F-20 à F-26)
def _lid(u: dict) -> int:
    if u.get("livreur_id") is None:
        raise HTTPException(403, "jeton sans dossier livreur")
    return int(u["livreur_id"])


def _agent(u: dict):
    lid = _lid(u)
    if lid not in etat.sma.livreurs:
        raise HTTPException(409, "vous êtes hors ligne")
    return lid


@app.post("/livreur/en-ligne", tags=["livreur"])
async def en_ligne(position: PointE, u: dict = Depends(LIVREUR)) -> dict:
    lv = await etat.depot.livreur(_lid(u))
    try:
        agent = await etat.sma.connecter_livreur(lv, position.p())
    except ErreurSysteme as e:
        raise HTTPException(403, str(e)) from e
    return {"en_ligne": True, "agent": str(agent.jid)}


@app.post("/livreur/hors-ligne", tags=["livreur"])
async def hors_ligne(u: dict = Depends(LIVREUR)) -> dict:
    await etat.sma.deconnecter_livreur(_lid(u))
    return {"en_ligne": False}


@app.post("/livreur/position", tags=["livreur"])
async def position(p: PointE, u: dict = Depends(LIVREUR)) -> dict:
    lid = _agent(u)
    _passerelle().transmettre(P.nom_livreur(lid), P.Perf.INFORM, "position", p.model_dump())
    return {"ok": True}


@app.get("/livreur/boite", tags=["livreur"])
async def boite(u: dict = Depends(LIVREUR)) -> list[dict]:
    """Propositions de course en attente et retours (F-21) ; à interroger régulièrement."""
    return _passerelle().boite(_lid(u), vider=False)


@app.post("/livreur/reponse", tags=["livreur"])
async def repondre(r: Reponse, u: dict = Depends(LIVREUR)) -> dict:
    lid = _agent(u)
    _passerelle().transmettre(P.nom_livreur(lid), P.Perf.REQUEST, "reponse", r.model_dump())
    b = _passerelle().boites.get(lid, [])
    b[:] = [m for m in b if m.get("conversation") != r.conversation]
    return {"ok": True}


@app.post("/livreur/{action}", tags=["livreur"])
async def etape(action: str, e: Etape, u: dict = Depends(LIVREUR)) -> dict:
    """action = enlevement | remise | incident (F-23, F-24)."""
    if action not in ("enlevement", "remise", "incident"):
        raise HTTPException(404, "action inconnue")
    lid = _agent(u)
    await _commande_autorisee(e.commande_id, u)
    _passerelle().transmettre(P.nom_livreur(lid), P.Perf.REQUEST, action, e.model_dump())
    return {"ok": True}


@app.get("/livreur/courses", tags=["livreur"])
async def mes_courses(u: dict = Depends(LIVREUR)) -> dict:
    lid = _lid(u)
    agent = etat.sma.livreurs.get(lid)
    lv = await etat.depot.livreur(lid)
    faites = await etat.depot.commandes({Etat.CLOTUREE}, livreur_id=lid)
    return {"agent": agent.etat_public() if agent else None, "livreur": lv.vers_dict(),
            "courses_terminees": len(faites),
            "gains_bruts": sum(c.prix for c in faites),  # F-25 (commission : lot 5)
            "especes_a_reverser": lv.especes_detenues}


async def releve_livreur(lid: int) -> dict:
    """F-54 : gains, commission, espèces détenues et solde entre le livreur et la plateforme."""
    lv = await etat.depot.livreur(lid)
    fs = await etat.depot.factures(lid)
    gains = sum(f["gain_livreur"] for f in fs)
    commission = sum(f["commission"] for f in fs)
    reverse = sum(p["montant"] for p in await etat.depot.paiements()
                  if p["mode"] == "reversement" and p["reference"] == f"REV-L{lid}")
    # Le livreur garde les espèces encaissées ; la plateforme lui doit ses gains.
    solde = gains - lv.especes_detenues
    return {"livreur_id": lid, "courses_facturees": len(fs), "chiffre_affaires": gains + commission,
            "gains": gains, "commission_plateforme": commission,
            "especes_detenues": lv.especes_detenues, "especes_deja_reversees": reverse,
            "solde": solde, "sens": "la plateforme doit au livreur" if solde >= 0
            else "le livreur doit reverser à la plateforme", "note": lv.note,
            "factures": [f["numero"] for f in fs][-20:]}


@app.get("/livreur/releve", tags=["livreur"])
async def mon_releve(u: dict = Depends(LIVREUR)) -> dict:
    return await releve_livreur(_lid(u))


# ------------------------------------------- répartiteur et administration (F-60 à F-66)


@app.get("/admin/livreurs", tags=["administration"])
async def livreurs(u: dict = Depends(SUPERVISION)) -> list[dict]:
    res = []
    for lv in await etat.depot.livreurs():
        a = etat.sma.livreurs.get(lv.id)
        res.append({**lv.vers_dict(), "en_ligne": a is not None, "agent": a.etat_public() if a else None})
    return res


@app.post("/admin/livreurs/{lid}/valider", tags=["administration"])
async def valider(lid: int, u: dict = Depends(ADMIN)) -> dict:
    await etat.depot.maj_livreur(lid, valide=True)
    return {"livreur_id": lid, "valide": True}


@app.get("/admin/livreurs/{lid}/releve", tags=["administration"])
async def releve_admin(lid: int, u: dict = Depends(SUPERVISION)) -> dict:
    return await releve_livreur(lid)


@app.post("/admin/livreurs/{lid}/reversement", tags=["administration"])
async def reversement(lid: int, r: Reversement, u: dict = Depends(ADMIN)) -> dict:
    """Le livreur remet des espèces à la plateforme : opération tracée (NF-06)."""
    lv = await etat.depot.livreur(lid)
    if r.montant > lv.especes_detenues:
        raise HTTPException(422, f"le livreur ne détient que {lv.especes_detenues} FCFA")
    await etat.depot.maj_livreur(lid, especes_detenues=lv.especes_detenues - r.montant)
    await etat.depot.noter_paiement(0, "reversement", r.montant, f"REV-L{lid}", "recu")
    return await releve_livreur(lid)


@app.post("/admin/livreurs/{lid}/suspendre", tags=["administration"])
async def suspendre(lid: int, suspendu: bool = True, u: dict = Depends(SUPERVISION)) -> dict:
    await etat.depot.maj_livreur(lid, suspendu=suspendu)
    if lid in etat.sma.livreurs:
        _passerelle().transmettre(P.nom_livreur(lid), P.Perf.REQUEST, "suspension", {"suspendu": suspendu})
    return {"livreur_id": lid, "suspendu": suspendu}


@app.post("/commandes/{cid}/reattribuer", tags=["supervision"])
async def reattribuer(cid: int, r: Reattribution, u: dict = Depends(SUPERVISION)) -> dict:
    if cid not in etat.sma.commandes:
        raise HTTPException(409, "commande terminée ou inconnue")
    fut = _passerelle().transmettre(P.nom_commande(cid), P.Perf.REQUEST, "reattribuer",
                                    r.model_dump(), attendre=True)
    rep = await asyncio.wait_for(fut, 10)
    if rep["performative"] != "agree":
        raise HTTPException(409, rep.get("motif", "réattribution refusée"))
    return rep


@app.get("/supervision/etat", tags=["supervision"])
async def supervision(u: dict = Depends(SUPERVISION)) -> dict:
    return etat.sma.etat()


@app.get("/supervision/carte", tags=["supervision"])
async def carte(u: dict = Depends(SUPERVISION)) -> dict:
    """F-60 : livreurs en ligne et commandes actives, avec leur état."""
    actives = await etat.depot.commandes(ETATS_ACTIFS, limite=1000)
    return {"livreurs": [a.etat_public() for a in etat.sma.livreurs.values()],
            "commandes": [c.vers_dict() for c in actives]}


@app.get("/supervision/journal", tags=["supervision"])
async def journal(commande_id: int | None = None, categorie: str | None = None, limite: int = 200,
                  u: dict = Depends(SUPERVISION)) -> list[dict]:
    """F-63 : journal des messages entre agents (équivalent du Sniffer)."""
    if commande_id is not None:
        evts = await etat.depot.evenements(commande_id, limite)
    else:
        evts = list(etat.bus.historique)[-limite * 3:]
    if categorie:
        evts = [e for e in evts if e["categorie"] == categorie]
    return evts[-limite:]


class Annonce(BaseModel):
    texte: str = Field(max_length=300)
    commande_id: int | None = None


@app.post("/supervision/annonce", tags=["supervision"])
async def annonce(a: Annonce, u: dict = Depends(SUPERVISION)) -> dict:
    """Affiche un message dans le journal de la console (titres d'une démonstration)."""
    etat.bus.publier("alerte", auteur="démonstration", niveau="info", texte=a.texte,
                     commande_id=a.commande_id)
    return {"ok": True}


@app.get("/supervision/statistiques", tags=["supervision"])
async def statistiques(u: dict = Depends(SUPERVISION)) -> dict:
    """F-64 : tableau de bord."""
    return {**await etat.depot.statistiques(), "dispatcher": etat.sma.dispatcher.etat_public(),
            "messages_echanges": etat.bus.compteurs.get("message", 0),
            "livreurs_en_ligne": len(etat.sma.livreurs)}


@app.websocket("/ws/supervision")
async def ws_supervision(ws: WebSocket, jeton: str):
    """Flux temps réel des événements (positions, états, messages, alertes)."""
    u = lire_jeton(jeton)
    if u.get("role") not in ("repartiteur", "admin"):
        await ws.close(code=4403)
        return
    await ws.accept()
    file = etat.bus.abonner()
    try:
        while True:
            await ws.send_json(await file.get())
    except WebSocketDisconnect:
        pass
    finally:
        etat.bus.desabonner(file)
