"""Application du livreur (§ 5, F-20 à F-26), en Flet. Conçue pour le téléphone : grands
boutons, utilisable d'une main, compte à rebours visible.

Lancement (le serveur API doit tourner) :
    python -m livraison.interfaces.livreur --web    # http://127.0.0.1:8552 (ouvrir sur le téléphone)
    python -m livraison.interfaces.livreur          # fenêtre de bureau

Position GPS : en attendant le module de géolocalisation du lot 3 (flet-geolocator sur Android),
la position est choisie sur la carte (appui) et le trajet peut être simulé.
"""
from __future__ import annotations

import asyncio
import time

import flet as ft

from livraison.interfaces.commun import (
    VERT,
    ClientApi,
    carte,
    ecran_connexion,
    lancer,
    marqueur,
    trait,
)
from livraison.modele.geo import Point, commune_la_plus_proche, distance_km, interpoler

MOTIFS_INCIDENT = {"panne": "Panne du véhicule", "accident": "Accident",
                   "destinataire-absent": "Destinataire absent",
                   "adresse-introuvable": "Adresse introuvable"}


async def main(page: ft.Page):
    page.title = "Livraison SMA · Livreur"
    page.padding = 0
    page.theme_mode = ft.ThemeMode.LIGHT
    page.theme = ft.Theme(color_scheme_seed=VERT)
    api = ClientApi()
    e = {"position": Point(5.3236, -4.0197), "en_ligne": False, "proposition": None, "vues": set(),
         "course": None, "commande": None, "simulation": False}

    # ------------------------------------------------------------------ éléments
    titre = ft.Text("", size=18, weight=ft.FontWeight.BOLD)
    note = ft.Text("", size=13, color=ft.Colors.AMBER_800)
    interrupteur = ft.Switch(label="Hors ligne", value=False)
    info = ft.Text("", size=13, color=ft.Colors.GREY_700)
    m, marqueurs, traces = carte(zoom=13, sur_appui=None)
    corps = ft.Column([], spacing=10, scroll=ft.ScrollMode.AUTO, expand=True)
    gains = ft.Column([], spacing=8)

    def toast(texte: str, couleur=ft.Colors.BLUE_GREY_800):
        page.show_dialog(ft.SnackBar(ft.Text(texte), bgcolor=couleur))

    def dessiner_carte():
        p = e["position"]
        pts = [marqueur(p.lat, p.lon, ft.Icons.TWO_WHEELER, VERT, 34, "Vous")]
        c = e["commande"]
        if c:
            enl, liv = c["enlevement"], c["livraison"]
            pts.append(marqueur(enl["lat"], enl["lon"], ft.Icons.STOREFRONT, ft.Colors.ORANGE_800, 32,
                                "Enlèvement"))
            pts.append(marqueur(liv["lat"], liv["lon"], ft.Icons.HOME, ft.Colors.PURPLE_700, 32,
                                "Livraison"))
            etapes = [(p.lat, p.lon)] + ([(enl["lat"], enl["lon"])] if c["etat"] == "attribuee" else []) \
                + [(liv["lat"], liv["lon"])]
            traces.polylines = [trait(etapes)]
        else:
            traces.polylines = []
        marqueurs.markers = pts

    # --------------------------------------------------------------- GPS (F-20)
    async def envoyer_position():
        if e["en_ligne"]:
            await api.post("/livreur/position", e["position"].vers_dict())

    async def sur_appui_carte(ev):
        if e["simulation"]:
            return
        e["position"] = Point(ev.coordinates.latitude, ev.coordinates.longitude)
        info.value = f"Position : {commune_la_plus_proche(e['position'])} (choisie sur la carte)"
        dessiner_carte()
        page.update()
        await envoyer_position()

    m.on_tap = sur_appui_carte

    async def basculer(ev):
        if interrupteur.value:
            r = await api.post("/livreur/en-ligne", e["position"].vers_dict())
            if r.status_code != 200:
                interrupteur.value = False
                toast(r.json().get("detail", "connexion refusée"), ft.Colors.RED_700)
            else:
                e["en_ligne"] = True
                toast("Vous êtes en ligne : votre agent répond aux appels d'offres.", ft.Colors.GREEN_800)
        else:
            await api.post("/livreur/hors-ligne")
            e["en_ligne"] = False
        interrupteur.label = "En ligne" if e["en_ligne"] else "Hors ligne"
        afficher()

    interrupteur.on_change = basculer

    # ------------------------------------------------------------ vues du corps
    def bloc(titre_bloc: str, *controles, couleur=None) -> ft.Container:
        return ft.Container(ft.Column([ft.Text(titre_bloc, weight=ft.FontWeight.BOLD, size=15),
                                       *controles], spacing=8),
                            padding=14, border_radius=12,
                            bgcolor=couleur or ft.Colors.with_opacity(0.05, ft.Colors.BLUE_GREY_900))

    barre = ft.ProgressBar(value=1, color=ft.Colors.ORANGE_700, bar_height=10, border_radius=5)
    reste = ft.Text("", size=28, weight=ft.FontWeight.BOLD, color=ft.Colors.ORANGE_800)
    code = ft.TextField(label="Code du destinataire", width=200, keyboard_type=ft.KeyboardType.NUMBER,
                        text_size=20)
    motif = ft.Dropdown(label="Incident", width=230, value="panne",
                        options=[ft.dropdown.Option(k, v) for k, v in MOTIFS_INCIDENT.items()])

    def afficher():
        corps.controls.clear()
        prop, c = e["proposition"], e["commande"]
        if not e["en_ligne"]:
            corps.controls.append(bloc("Vous êtes hors ligne",
                                       ft.Text("Touchez la carte pour indiquer votre position, puis "
                                               "passez en ligne pour recevoir des courses.")))
        elif prop:
            cmd = prop["commande"]
            enl, liv = Point.depuis_dict(cmd["enlevement"]), Point.depuis_dict(cmd["livraison"])
            gain = round(cmd.get("prix", 0) * 0.8)
            corps.controls.append(bloc(
                "Nouvelle course proposée",
                ft.Row([reste, ft.Text("secondes pour répondre", size=13)]), barre,
                ft.Text(f"Enlèvement : {cmd.get('adresse_enlevement') or commune_la_plus_proche(enl)}",
                        size=15),
                ft.Text(f"Livraison : {cmd.get('adresse_livraison') or commune_la_plus_proche(liv)}",
                        size=15),
                ft.Text(f"{cmd.get('distance_km', round(distance_km(enl, liv), 1))} km · colis "
                        f"{cmd.get('taille')}{' · URGENT' if cmd.get('urgent') else ''} · arrivée à "
                        f"l'enlèvement dans ~{round((prop.get('eta_s') or 0) / 60)} min", size=13),
                ft.Text(f"Gain estimé : {gain} FCFA", size=20, weight=ft.FontWeight.BOLD, color=VERT),
                ft.Row([ft.FilledButton("ACCEPTER", icon=ft.Icons.CHECK, height=56, expand=True,
                                        style=ft.ButtonStyle(bgcolor=ft.Colors.GREEN_700),
                                        on_click=lambda _: page.run_task(repondre, True)),
                        ft.OutlinedButton("Décliner", height=56,
                                          on_click=lambda _: page.run_task(repondre, False))]),
                couleur=ft.Colors.ORANGE_50))
        elif c:
            enlevee = c["etat"] == "enlevee"
            but = c["livraison"] if enlevee else c["enlevement"]
            d = distance_km(e["position"], Point.depuis_dict(but))
            actions = [ft.FilledButton("Simuler le trajet" if not e["simulation"] else "Trajet en cours...",
                                       icon=ft.Icons.NAVIGATION, disabled=e["simulation"],
                                       on_click=lambda _: page.run_task(simuler_trajet))]
            if not enlevee:
                actions.append(ft.FilledButton("Colis enlevé", icon=ft.Icons.INVENTORY_2, height=52,
                                               on_click=lambda _: page.run_task(etape, "enlevement")))
            corps.controls.append(bloc(
                f"Course n° {c['id']} · {'livraison' if enlevee else 'enlèvement'} à {d:.1f} km",
                ft.Text(("Livrez à " + (c.get("destinataire") or "destinataire")
                         + f" ({c.get('telephone_destinataire') or 'tél. masqué'})") if enlevee else
                        "Rendez-vous au point d'enlèvement (icône orange).", size=14),
                ft.Text(c.get("consignes") or "", italic=True, size=13),
                ft.Row(actions, wrap=True)))
            if enlevee:
                corps.controls.append(bloc("Remise du colis (F-23)", ft.Row([
                    code, ft.FilledButton("Valider", icon=ft.Icons.VERIFIED, height=52,
                                          on_click=lambda _: page.run_task(etape, "remise"))], wrap=True)))
            corps.controls.append(bloc("Un problème ? (F-24)", ft.Row([
                motif, ft.OutlinedButton("Signaler", icon=ft.Icons.REPORT, on_click=lambda _:
                                         page.run_task(etape, "incident"))], wrap=True)))
        else:
            corps.controls.append(bloc("En attente d'une course",
                                       ft.Row([ft.ProgressRing(width=18, height=18),
                                               ft.Text("Votre agent négocie pour vous...")]),
                                       ft.Text("Les propositions apparaîtront ici avec un compte à "
                                               "rebours de 30 secondes.", size=12)))
        corps.controls.append(gains)
        dessiner_carte()
        page.update()

    # ----------------------------------------------------------------- actions
    async def repondre(accepte: bool):
        prop = e["proposition"]
        if not prop:
            return
        await api.post("/livreur/reponse", {"conversation": prop["conversation"], "accepte": accepte})
        e["proposition"] = None
        toast("Course acceptée : direction l'enlèvement." if accepte else "Course déclinée.",
              ft.Colors.GREEN_800 if accepte else ft.Colors.BLUE_GREY_700)
        afficher()

    async def etape(action: str):
        c = e["commande"]
        corps_req = {"commande_id": c["id"]}
        if action == "remise":
            corps_req["code"] = code.value or ""
        if action == "incident":
            corps_req["motif"] = motif.value
        r = await api.post(f"/livreur/{action}", corps_req)
        if r.status_code != 200:
            toast(r.json().get("detail", "action refusée"), ft.Colors.RED_700)
        elif action == "enlevement":
            toast("Enlèvement enregistré.")

    async def simuler_trajet():
        c = e["commande"]
        but = Point.depuis_dict(c["livraison"] if c["etat"] == "enlevee" else c["enlevement"])
        e["simulation"] = True
        afficher()
        depart = e["position"]
        for k in range(1, 11):
            await asyncio.sleep(0.8)
            e["position"] = interpoler(depart, but, k / 10)
            await envoyer_position()
            dessiner_carte()
            page.update()
        e["simulation"] = False
        afficher()

    # ------------------------------------------------------------ boucle (F-21)
    async def boucle():
        derniere_pos = 0.0
        while True:
            try:
                if e["en_ligne"]:
                    if time.monotonic() - derniere_pos > 8:  # le GPS reste « frais »
                        await envoyer_position()
                        derniere_pos = time.monotonic()
                    for msg in (await api.get("/livreur/boite")).json():
                        t = msg["type"]
                        if t == "proposition" and msg["conversation"] not in e["vues"]:
                            e["vues"].add(msg["conversation"])
                            e["proposition"] = msg
                        elif t == "remise-resultat" and msg.get("recu_a") not in e["vues"]:
                            e["vues"].add(msg.get("recu_a"))
                            if msg.get("ok"):
                                toast("Livraison validée. Bravo !", ft.Colors.GREEN_800)
                            else:
                                toast(f"Code refusé : {msg.get('motif')} "
                                      f"({msg.get('essais_restants', '?')} essai(s) restant(s))",
                                      ft.Colors.RED_700)
                        elif t == "annulation" and msg.get("recu_a") not in e["vues"]:
                            e["vues"].add(msg.get("recu_a"))
                            toast(f"Course {msg.get('commande_id')} retirée : "
                                  f"{msg.get('motif', 'annulée')}", ft.Colors.ORANGE_800)
                        elif t == "proposition-expiree" and e["proposition"] and \
                                e["proposition"]["conversation"] == msg.get("conversation"):
                            e["proposition"] = None
                            toast("Proposition expirée : elle passe au livreur suivant.")
                    courses = (await api.get("/livreur/courses")).json()
                    ids = (courses.get("agent") or {}).get("courses", [])
                    e["commande"] = (await api.get(f"/commandes/{ids[0]}")).json()["commande"] if ids else None
                    rel = (await api.get("/livreur/releve")).json()
                    gains.controls = [bloc(
                        "Mes gains (F-25)",
                        ft.Text(f"{rel['courses_facturees']} course(s) · gains {rel['gains']} FCFA · "
                                f"note {rel['note']:.2f} / 5", size=14),
                        ft.Text(f"Espèces détenues : {rel['especes_detenues']} FCFA · solde : "
                                f"{rel['solde']} FCFA ({rel['sens']})", size=13))]
                    if e["proposition"]:
                        ecoule = time.time() - e["proposition"]["recu_a"]
                        total = float(e["proposition"].get("delai_s", 30))
                        if ecoule > total:
                            e["proposition"] = None
                        else:
                            reste.value = f"{max(0, total - ecoule):.0f}"
                            barre.value = max(0.0, 1 - ecoule / total)
                    c = e["commande"]
                    signature = (e["en_ligne"], (e["proposition"] or {}).get("conversation"),
                                 c and (c["id"], c["etat"]), e["simulation"])
                    if signature != e.get("signature"):  # on ne reconstruit que si l'état change
                        e["signature"] = signature
                        afficher()
                    else:
                        page.update()
            except Exception as ex:  # noqa: BLE001
                print("livreur : rafraîchissement impossible :", ex)
            await asyncio.sleep(1.0)

    # ---------------------------------------------------------- démarrage
    def ecran_principal():
        lv = api.profil.get("livreur") or {}
        titre.value = lv.get("nom", "Livreur")
        note.value = f"Note {lv.get('note', 4.5):.2f}/5 · {lv.get('vehicule', 'moto')} · capacité {lv.get('capacite')}"
        info.value = "Touchez la carte pour indiquer votre position."
        e["en_ligne"] = bool(api.profil.get("en_ligne"))
        interrupteur.value = e["en_ligne"]
        interrupteur.label = "En ligne" if e["en_ligne"] else "Hors ligne"
        page.controls.clear()
        page.add(ft.SafeArea(ft.Column([
            ft.Container(ft.Row([ft.Column([titre, note], spacing=0, expand=True), interrupteur]),
                         padding=ft.Padding.symmetric(horizontal=14, vertical=8)),
            ft.Container(m, height=300),
            ft.Container(info, padding=ft.Padding.symmetric(horizontal=14)),
            ft.Container(corps, padding=12, expand=True)], spacing=4, expand=True), expand=True))
        afficher()
        page.run_task(boucle)

    def ecran_inscription():
        nom = ft.TextField(label="Nom complet", value=api.profil.get("nom", ""), width=320)
        vehicule = ft.Dropdown(label="Véhicule", value="moto", width=320, options=[
            ft.dropdown.Option(v, v) for v in ("moto", "tricycle", "voiture", "velo")])
        capacite = ft.Dropdown(label="Capacité (colis en même temps)", value="2", width=320,
                               options=[ft.dropdown.Option(str(i)) for i in range(1, 5)])
        msg = ft.Text("")

        async def envoyer(ev):
            r = await api.post("/livreurs/inscription", {"nom": nom.value, "vehicule": vehicule.value,
                                                         "capacite": int(capacite.value)})
            msg.value = ("Dossier envoyé. Il doit être validé par l'administrateur (F-03) ; "
                         "reconnectez-vous ensuite." if r.status_code == 200 else r.json().get("detail"))
            page.update()

        page.controls.clear()
        page.add(ft.Container(ft.Column([
            ft.Text("Devenir livreur", size=24, weight=ft.FontWeight.BOLD),
            ft.Text("Votre numéro n'a pas encore de dossier livreur."), nom, vehicule, capacite,
            ft.FilledButton("Envoyer mon dossier", on_click=envoyer, width=320, height=48), msg],
            spacing=12, horizontal_alignment=ft.CrossAxisAlignment.CENTER), padding=24,
            alignment=ft.Alignment.CENTER))
        page.update()

    async def apres_connexion(profil: dict):
        if profil.get("role") == "livreur":
            ecran_principal()
        else:
            ecran_inscription()

    page.add(ecran_connexion(page, api, "Livraison SMA", "Application du livreur", apres_connexion))


if __name__ == "__main__":
    lancer(main, 8552)
