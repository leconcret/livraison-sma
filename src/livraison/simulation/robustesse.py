"""Démonstration de robustesse : le Dispatcher face aux aléas, en direct.

Cinq scénarios joués par l'API, avec de vrais agents, à suivre dans la console du répartiteur
(onglet « Sniffer ») :
  1. un livreur décline           -> le Dispatcher retient l'offre suivante ;
  2. un livreur ne répond pas      -> délai dépassé, offre suivante ;
  3. panne pendant la course       -> FAILURE, nouvel appel d'offres sans ce livreur ;
  4. aucun livreur disponible      -> file d'attente, puis attribution dès qu'un livreur arrive ;
  5. téléphone éteint en course    -> alerte GPS, puis réattribution.

Préparation (deux terminaux) :
    python -m livraison.lancer --memoire --rapide       # délais courts : 15 s, 8 s, 20 s
    python -m livraison.interfaces.console --web         # suivre le Sniffer
Puis : python -m livraison.simulation.robustesse         # ou --scenario 3 pour un seul
"""
from __future__ import annotations

import argparse
import asyncio
import time

import httpx

from livraison.config import parametres
from livraison.modele.geo import Point

# Les scénarios se jouent vers Port-Bouët, loin des livreurs de la démonstration générale.
CENTRE = Point(5.2560, -3.9330)
GRAS, VERT_T, ROUGE, JAUNE, BLEU, FIN = "\033[1m", "\033[32m", "\033[31m", "\033[33m", "\033[36m", "\033[0m"


def decaler(p: Point, dlat: float, dlon: float = 0.0) -> Point:
    return Point(p.lat + dlat, p.lon + dlon)


class Acteur:
    def __init__(self, nom: str, tel: str):
        self.nom, self.tel = nom, tel
        self.h: dict = {}
        self.id: int | None = None
        self.pos: Point | None = None
        self.gps: asyncio.Task | None = None


class Demo:
    def __init__(self, api: str):
        self.http = httpx.AsyncClient(base_url=api, timeout=30)
        self.admin: dict = {}
        self.client: dict = {}

    # ----------------------------------------------------------------- outils
    async def jeton(self, tel: str, nom: str = "") -> dict:
        code = (await self.http.post("/auth/code", json={"telephone": tel})).json().get("code_dev")
        if not code:
            raise SystemExit("Le serveur doit être en mode développement (MODE_DEV=true).")
        r = (await self.http.post("/auth/jeton", json={"telephone": tel, "code": code, "nom": nom})).json()
        return {"Authorization": f"Bearer {r['jeton']}"}

    def dire(self, texte: str, couleur: str = "") -> None:
        print(f"{couleur}{texte}{FIN}", flush=True)

    async def annoncer(self, texte: str, cid: int | None = None) -> None:
        self.dire(texte, GRAS + BLEU)
        await self.http.post("/supervision/annonce", headers=self.admin,
                             json={"texte": texte, "commande_id": cid})

    async def preparer_livreur(self, a: Acteur, pos: Point) -> None:
        a.h = await self.jeton(a.tel, a.nom)
        r = await self.http.post("/livreurs/inscription", headers=a.h, json={"nom": a.nom, "capacite": 1})
        if r.status_code == 200:
            await self.http.post(f"/admin/livreurs/{r.json()['livreur']['id']}/valider", headers=self.admin)
        a.h = await self.jeton(a.tel)
        a.id = (await self.http.get("/moi", headers=a.h)).json()["livreur"]["id"]
        await self.en_ligne(a, pos)

    async def en_ligne(self, a: Acteur, pos: Point) -> None:
        a.pos = pos
        (await self.http.post("/livreur/en-ligne", headers=a.h, json=pos.vers_dict())).raise_for_status()
        self.allumer_gps(a)

    async def hors_ligne(self, a: Acteur) -> None:
        self.couper_gps(a)
        await self.http.post("/livreur/hors-ligne", headers=a.h)

    def allumer_gps(self, a: Acteur) -> None:
        async def battre():
            while True:
                await self.http.post("/livreur/position", headers=a.h, json=a.pos.vers_dict())
                await asyncio.sleep(3)
        self.couper_gps(a)
        a.gps = asyncio.create_task(battre())

    def couper_gps(self, a: Acteur) -> None:
        if a.gps:
            a.gps.cancel()
            a.gps = None

    async def commander(self, titre: str) -> dict:
        r = await self.http.post("/commandes", headers=self.client, json={
            "enlevement": CENTRE.vers_dict(), "livraison": decaler(CENTRE, 0.008, 0.008).vers_dict(),
            "adresse_enlevement": "Port-Bouët, marché", "adresse_livraison": "Port-Bouët, Vridi",
            "destinataire": "Awa", "consignes": titre})
        c = r.json()["commande"]
        self.dire(f"  -> commande n° {c['id']} créée (code destinataire {c['code']}). "
                  f"Sniffer : commande {c['id']}", JAUNE)
        return c

    async def etat(self, cid: int) -> dict:
        return (await self.http.get(f"/commandes/{cid}", headers=self.client)).json()["commande"]

    async def attendre(self, cid: int, condition, timeout: float = 90, texte: str = "") -> dict:
        debut = time.monotonic()
        while time.monotonic() - debut < timeout:
            c = await self.etat(cid)
            if condition(c):
                return c
            await asyncio.sleep(0.5)
        raise RuntimeError(f"délai dépassé : {texte}")

    async def proposition(self, a: Acteur, cid: int, timeout: float = 40) -> dict:
        debut = time.monotonic()
        while time.monotonic() - debut < timeout:
            for m in (await self.http.get("/livreur/boite", headers=a.h)).json():
                if m["type"] == "proposition" and m["commande"]["id"] == cid:
                    return m
            await asyncio.sleep(0.3)
        raise RuntimeError(f"{a.nom} n'a reçu aucune proposition pour la commande {cid}")

    async def repondre(self, a: Acteur, prop: dict, accepte: bool) -> None:
        await self.http.post("/livreur/reponse", headers=a.h,
                             json={"conversation": prop["conversation"], "accepte": accepte})

    async def livrer(self, a: Acteur, c: dict) -> None:
        await self.attendre(c["id"], lambda x: x["etat"] == "attribuee", 20, "attribution")
        a.pos = Point.depuis_dict(c["enlevement"])
        await self.http.post("/livreur/enlevement", headers=a.h, json={"commande_id": c["id"]})
        await self.attendre(c["id"], lambda x: x["etat"] == "enlevee", 20, "enlèvement")
        a.pos = Point.depuis_dict(c["livraison"])
        await self.http.post("/livreur/position", headers=a.h, json=a.pos.vers_dict())
        await self.http.post("/livreur/remise", headers=a.h, json={"commande_id": c["id"], "code": c["code"]})
        await self.attendre(c["id"], lambda x: x["etat"] == "cloturee", 20, "clôture")
        a.pos = decaler(CENTRE, 0.002)  # retour au point de départ pour le scénario suivant
        self.dire(f"  -> commande {c['id']} livrée par {a.nom} et clôturée.", VERT_T)

    async def pause(self, s: float = 3) -> None:
        await asyncio.sleep(s)

    # -------------------------------------------------------------- scénarios
    async def s1_refus(self, A, B, C):
        await self.annoncer("SCÉNARIO 1 · Le livreur le plus proche DÉCLINE la course")
        c = await self.commander("scénario 1 : refus")
        prop = await self.proposition(A, c["id"])
        self.dire(f"  {A.nom} (le plus proche) reçoit la proposition... et la DÉCLINE.", ROUGE)
        await self.repondre(A, prop, False)
        prop = await self.proposition(B, c["id"])
        self.dire(f"  Le Dispatcher reçoit REFUSE et envoie ACCEPT à l'offre suivante : {B.nom}.", VERT_T)
        await self.repondre(B, prop, True)
        await self.livrer(B, c)

    async def s2_silence(self, A, B, C):
        await self.annoncer("SCÉNARIO 2 · Le livreur le plus proche NE RÉPOND PAS (délai dépassé)")
        c = await self.commander("scénario 2 : silence")
        await self.proposition(A, c["id"])
        self.dire(f"  {A.nom} reçoit la proposition... et ne répond pas. Délai : "
                  f"{parametres.delai_confirmation_s:.0f} s (configuré côté serveur).", ROUGE)
        debut = time.monotonic()
        prop = await self.proposition(B, c["id"], timeout=parametres.delai_confirmation_s + 60)
        self.dire(f"  Après {time.monotonic() - debut:.0f} s, l'agent de {A.nom} répond REFUSE "
                  f"(sans-reponse) ; le Dispatcher passe à {B.nom}.", VERT_T)
        await self.repondre(B, prop, True)
        await self.livrer(B, c)

    async def s3_panne(self, A, B, C):
        await self.annoncer("SCÉNARIO 3 · PANNE du livreur après l'enlèvement")
        c = await self.commander("scénario 3 : panne")
        await self.repondre(A, await self.proposition(A, c["id"]), True)
        await self.attendre(c["id"], lambda x: x["etat"] == "attribuee", 20, "attribution")
        await self.http.post("/livreur/enlevement", headers=A.h, json={"commande_id": c["id"]})
        await self.attendre(c["id"], lambda x: x["etat"] == "enlevee", 20, "enlèvement")
        await self.pause(2)
        self.dire(f"  {A.nom} a enlevé le colis... puis tombe en PANNE et le signale.", ROUGE)
        await self.http.post("/livreur/incident", headers=A.h,
                             json={"commande_id": c["id"], "motif": "panne"})
        prop = await self.proposition(B, c["id"])
        self.dire(f"  FAILURE -> la commande repart aux enchères, {A.nom} est EXCLU ; "
                  f"{B.nom} reçoit la course.", VERT_T)
        await self.repondre(B, prop, True)
        await self.livrer(B, c)

    async def s4_file_attente(self, A, B, C):
        await self.annoncer("SCÉNARIO 4 · AUCUN livreur disponible : file d'attente")
        await self.hors_ligne(A)
        await self.hors_ligne(B)
        self.dire(f"  {A.nom} et {B.nom} passent hors ligne.", ROUGE)
        c = await self.commander("scénario 4 : file d'attente")
        x = await self.attendre(c["id"], lambda x: x["tentatives"] >= 1, 30, "recherche infructueuse")
        self.dire(f"  Recherche infructueuse ({x['motif']}) : la commande attend, le rayon sera "
                  "élargi à chaque relance.", JAUNE)
        await self.pause(5)
        self.dire(f"  {C.nom} se connecte près du point d'enlèvement...", BLEU)
        await self.en_ligne(C, decaler(CENTRE, 0.003, 0.002))
        debut = time.monotonic()
        prop = await self.proposition(C, c["id"])
        self.dire(f"  L'Annuaire prévient le Dispatcher, qui relance aussitôt "
                  f"({time.monotonic() - debut:.1f} s) : {C.nom} reçoit la course.", VERT_T)
        await self.repondre(C, prop, True)
        await self.livrer(C, c)
        await self.en_ligne(A, decaler(CENTRE, 0.002))

    async def s5_gps(self, A, B, C):
        await self.annoncer("SCÉNARIO 5 · TÉLÉPHONE ÉTEINT pendant la course (perte du GPS)")
        c = await self.commander("scénario 5 : GPS coupé")
        await self.repondre(A, await self.proposition(A, c["id"]), True)
        await self.attendre(c["id"], lambda x: x["etat"] == "attribuee", 20, "attribution")
        self.couper_gps(A)
        self.dire(f"  {A.nom} a accepté... puis son téléphone s'éteint (plus aucune position). "
                  f"Alerte après {parametres.gps_alerte_s:.0f} s, réattribution après "
                  f"{parametres.gps_reattribution_s:.0f} s (valeurs du serveur).", ROUGE)
        debut = time.monotonic()
        prop = await self.proposition(C, c["id"], timeout=parametres.gps_reattribution_s + 120)
        self.dire(f"  Après {time.monotonic() - debut:.0f} s, l'agent de {A.nom} déclare la course "
                  f"perdue (FAILURE gps-perdu) ; {C.nom} la reçoit.", VERT_T)
        await self.repondre(C, prop, True)
        await self.livrer(C, c)

    async def lancer(self, scenarios: list[int]) -> None:
        self.admin = await self.jeton(parametres.admin_telephone)
        etat = (await self.http.get("/supervision/etat", headers=self.admin)).json()
        if etat["livreurs_en_ligne"]:
            self.dire(f"Attention : {etat['livreurs_en_ligne']} autre(s) livreur(s) en ligne ; pour une "
                      "démonstration nette, lancez le serveur sans --demo.", JAUNE)
        self.client = await self.jeton("0577777777", "Cliente démo")
        A, B, C = (Acteur("Livreur A (Koné)", "0781000001"), Acteur("Livreur B (Traoré)", "0781000002"),
                   Acteur("Livreur C (Yao)", "0781000003"))
        await self.preparer_livreur(A, decaler(CENTRE, 0.002))   # ~220 m : le plus proche
        await self.preparer_livreur(B, decaler(CENTRE, 0.007))   # ~780 m
        await self.preparer_livreur(C, decaler(CENTRE, 0.003))
        await self.hors_ligne(C)  # C arrivera au scénario 4
        self.dire(f"Livreurs prêts : A (le plus proche), B. C est hors ligne. Délais du serveur : "
                  f"confirmation {parametres.delai_confirmation_s:.0f} s.", GRAS)
        table = {1: self.s1_refus, 2: self.s2_silence, 3: self.s3_panne, 4: self.s4_file_attente,
                 5: self.s5_gps}
        for n in scenarios:
            debut = time.monotonic()
            try:
                await table[n](A, B, C)
                self.dire(f"Scénario {n} réussi en {time.monotonic() - debut:.0f} s.\n", GRAS + VERT_T)
            except RuntimeError as e:
                self.dire(f"Scénario {n} interrompu : {e}\n", GRAS + ROUGE)
            await self.pause(3)
        for a in (A, B, C):
            self.couper_gps(a)
        await self.annoncer("Démonstration de robustesse terminée.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--api", default=f"http://{parametres.api_host}:{parametres.api_port}")
    p.add_argument("--scenario", type=int, action="append", choices=[1, 2, 3, 4, 5],
                   help="scénario(s) à jouer (tous par défaut)")
    p.add_argument("--rapide", action="store_true",
                   help="à utiliser si le serveur a été lancé avec --rapide (délais courts)")
    a = p.parse_args()
    if a.rapide:
        parametres.delai_confirmation_s, parametres.gps_alerte_s, parametres.gps_reattribution_s = 15, 8, 20
    asyncio.run(Demo(a.api).lancer(a.scenario or [1, 2, 3, 4, 5]))


if __name__ == "__main__":
    main()
