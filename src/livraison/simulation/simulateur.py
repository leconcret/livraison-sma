"""Simulateur de livreurs et de clients à Abidjan, qui passe par l'API comme les vraies
applications. Sert à la démonstration, à la mise au point et au test de charge de la recette.

Exemples :
    python -m livraison.simulation.simulateur --livreurs 15 --commandes 40 --cadence 20
    python -m livraison.simulation.simulateur --livreurs 100 --commandes 200 --rafale   # charge

Chaque livreur simulé : se connecte, passe en ligne à un point d'Abidjan, envoie sa position,
consulte sa boîte, accepte (ou décline, ou ignore) les propositions, roule jusqu'à
l'enlèvement, enlève, roule jusqu'à la livraison et remet le colis avec le code du client.
"""
from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import time

import httpx

from livraison.config import parametres
from livraison.modele.geo import Point, distance_km, interpoler
from livraison.simulation.virtuel import point_aleatoire_abidjan

VITESSE_KMH = 24.0


class Simulation:
    def __init__(self, a):
        self.a = a
        self.rng = random.Random(a.graine)
        self.http = httpx.AsyncClient(base_url=a.api, timeout=30)
        self.codes: dict[int, str] = {}  # commande -> code (le client le transmet au destinataire)
        self.creees: dict[int, float] = {}
        self.attributions: list[float] = []
        self.livrees = 0
        self.stop = False

    async def jeton(self, tel: str, nom: str = "") -> dict:
        code = (await self.http.post("/auth/code", json={"telephone": tel})).json().get("code_dev")
        if not code:
            raise SystemExit("Le serveur n'est pas en mode développement (MODE_DEV=false) : "
                             "le simulateur ne peut pas obtenir les codes de connexion.")
        r = (await self.http.post("/auth/jeton", json={"telephone": tel, "code": code, "nom": nom})).json()
        return {"Authorization": f"Bearer {r['jeton']}"}

    # ------------------------------------------------------------------ livreurs
    async def preparer_livreur(self, i: int, h_admin: dict) -> tuple[dict, Point]:
        tel = f"07{10_000_000 + i:08d}"
        h = await self.jeton(tel, f"Livreur {i}")
        r = await self.http.post("/livreurs/inscription", headers=h, json={
            "nom": f"Livreur {i}", "vehicule": self.rng.choice(["moto"] * 4 + ["tricycle", "velo"]),
            "capacite": self.rng.choice([1, 2, 2, 3])})
        if r.status_code == 201 or r.status_code == 200:
            lid = r.json()["livreur"]["id"]
            await self.http.post(f"/admin/livreurs/{lid}/valider", headers=h_admin)
        h = await self.jeton(tel)
        pos = point_aleatoire_abidjan(self.rng)
        r = await self.http.post("/livreur/en-ligne", headers=h, json=pos.vers_dict())
        r.raise_for_status()
        return h, pos

    async def vie_livreur(self, h: dict, pos: Point) -> None:
        etat = {"pos": pos}
        taches: set = set()
        vus: set = set()
        dernier_gps = 0.0
        while not self.stop:
            if time.monotonic() - dernier_gps > 8:
                # Petit déplacement au repos : le GPS reste « frais ».
                p = etat["pos"]
                etat["pos"] = Point(p.lat + self.rng.uniform(-3e-4, 3e-4), p.lon + self.rng.uniform(-3e-4, 3e-4))
                await self.http.post("/livreur/position", headers=h, json=etat["pos"].vers_dict())
                dernier_gps = time.monotonic()
            for m in (await self.http.get("/livreur/boite", headers=h)).json():
                if m["type"] == "proposition" and m["conversation"] not in vus:
                    vus.add(m["conversation"])
                    t = asyncio.create_task(self.course(h, m, etat))
                    taches.add(t)
                    t.add_done_callback(taches.discard)
            await asyncio.sleep(1.0)

    async def course(self, h: dict, prop: dict, etat: dict) -> None:
        tirage = self.rng.random()
        if tirage < self.a.taux_silence:
            return  # le livreur ne répond pas : le délai expire, offre suivante
        await asyncio.sleep(self.rng.uniform(0.5, 2.5) / self.a.acceleration ** 0.5)
        accepte = tirage >= self.a.taux_silence + self.a.taux_refus
        await self.http.post("/livreur/reponse", headers=h, json={
            "conversation": prop["conversation"], "accepte": accepte})
        if not accepte:
            return
        cmd = prop["commande"]
        cid = cmd["id"]
        if cid in self.creees:
            self.attributions.append(time.monotonic() - self.creees[cid])
        enl, liv = Point.depuis_dict(cmd["enlevement"]), Point.depuis_dict(cmd["livraison"])
        await self.rouler(h, etat, enl)
        await self.http.post("/livreur/enlevement", headers=h, json={"commande_id": cid})
        await self.rouler(h, etat, liv)
        r = await self.http.post("/livreur/remise", headers=h, json={
            "commande_id": cid, "code": self.codes.get(cid, "")})
        if r.status_code == 200:
            self.livrees += 1

    async def rouler(self, h: dict, etat: dict, but: Point) -> None:
        depart = etat["pos"]
        duree = distance_km(depart, but) * 1.35 / VITESSE_KMH * 3600 / self.a.acceleration
        pas = max(1, int(duree / 2))
        for k in range(1, pas + 1):
            await asyncio.sleep(duree / pas)
            etat["pos"] = interpoler(depart, but, k / pas)
            await self.http.post("/livreur/position", headers=h, json=etat["pos"].vers_dict())

    # ------------------------------------------------------------------- clients
    async def clients(self) -> None:
        entetes = [await self.jeton(f"05{20_000_000 + i:08d}", f"Client {i}") for i in range(10)]
        for n in range(self.a.commandes):
            enl = point_aleatoire_abidjan(self.rng)
            liv = point_aleatoire_abidjan(self.rng)
            while distance_km(enl, liv) < 1.0:
                liv = point_aleatoire_abidjan(self.rng)
            corps = {"enlevement": enl.vers_dict(), "livraison": liv.vers_dict(),
                     "taille": self.rng.choice(["petit"] * 3 + ["moyen", "grand"]),
                     "urgent": self.rng.random() < 0.1, "destinataire": f"Destinataire {n}",
                     "mode_paiement": self.rng.choice(["especes", "especes", "mobile_money"])}
            r = await self.http.post("/commandes", headers=self.rng.choice(entetes), json=corps)
            if r.status_code == 201:
                c = r.json()["commande"]
                self.codes[c["id"]] = c["code"]
                self.creees[c["id"]] = time.monotonic()
            if not self.a.rafale:
                await asyncio.sleep(60 / self.a.cadence)

    # ----------------------------------------------------------------- scénario
    async def lancer(self) -> None:
        h_admin = await self.jeton(parametres.admin_telephone)
        print(f"Préparation de {self.a.livreurs} livreurs...")
        livreurs = await asyncio.gather(*[self.preparer_livreur(i, h_admin)
                                          for i in range(self.a.livreurs)])
        vies = [asyncio.create_task(self.vie_livreur(h, p)) for h, p in livreurs]
        print(f"{len(livreurs)} livreurs en ligne. Envoi de {self.a.commandes} commandes...")
        debut = time.monotonic()
        await self.clients()
        while self.livrees < len(self.codes) and time.monotonic() - debut < self.a.duree_max:
            await asyncio.sleep(2)
            print(f"  {time.monotonic() - debut:5.0f} s : {self.livrees}/{len(self.codes)} livrées, "
                  f"{len(self.attributions)} attribuées")
        self.stop = True
        await asyncio.gather(*vies, return_exceptions=True)
        stats = (await self.http.get("/supervision/statistiques", headers=h_admin)).json()
        disp = stats["dispatcher"]
        print("\n===== Bilan =====")
        print(f"Commandes créées              : {len(self.codes)}")
        print(f"Commandes livrées             : {self.livrees}")
        print(f"Par état                      : {stats['par_etat']}")
        dec, tot = disp.get("decision_s") or {}, disp.get("attribution_complete_s") or {}
        print(f"Décision du SMA (recherche + appel d'offres, hors confirmation humaine) : "
              f"moyenne {dec.get('moyenne')} s, 95e centile {dec.get('p95')} s, max {dec.get('max')} s "
              f"(exigence NF-01 : < 5 s)")
        print(f"Attribution complète (confirmation humaine comprise) : moyenne {tot.get('moyenne')} s,"
              f" max {tot.get('max')} s")
        if self.attributions:
            print(f"Création -> acceptation par le livreur : médiane "
                  f"{statistics.median(self.attributions):.1f} s")
        print(f"Messages échangés entre agents : {stats['messages_echanges']}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--api", default=f"http://{parametres.api_host}:{parametres.api_port}")
    p.add_argument("--livreurs", type=int, default=10)
    p.add_argument("--commandes", type=int, default=20)
    p.add_argument("--cadence", type=float, default=30, help="commandes par minute")
    p.add_argument("--rafale", action="store_true", help="toutes les commandes d'un coup (charge)")
    p.add_argument("--acceleration", type=float, default=20, help="facteur d'accélération du temps")
    p.add_argument("--taux-refus", type=float, default=0.1)
    p.add_argument("--taux-silence", type=float, default=0.05)
    p.add_argument("--duree-max", type=float, default=600)
    p.add_argument("--graine", type=int, default=42)
    asyncio.run(Simulation(p.parse_args()).lancer())


if __name__ == "__main__":
    main()
