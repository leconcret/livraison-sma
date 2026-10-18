"""Espace client (§ 5, F-10 à F-16), en Flet : web adapté au téléphone.

Lancement (le serveur API doit tourner) :
    python -m livraison.interfaces.client --web     # http://127.0.0.1:8551

Commande en touchant la carte (la commune est reconnue automatiquement, pas de saisie de
coordonnées), prix garanti avant validation, suivi du livreur en direct, code à transmettre au
destinataire, annulation, facture PDF et évaluation du livreur.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

import flet as ft

from livraison.interfaces.commun import (
    API,
    COULEUR_ETAT,
    LIBELLE_ETAT,
    VERT,
    ClientApi,
    carte,
    ecran_connexion,
    lancer,
    marqueur,
    pastille,
    trait,
)
from livraison.modele.geo import Point, commune_la_plus_proche

ETAPES = ["recherche", "attribuee", "enlevee", "cloturee"]


async def main(page: ft.Page):
    page.title = "Livraison SMA · Client"
    page.padding = 0
    page.theme_mode = ft.ThemeMode.LIGHT
    page.theme = ft.Theme(color_scheme_seed=VERT)
    api = ClientApi()
    e = {"enl": None, "liv": None, "choix": "enl", "devis": None, "suivi": None, "vue": "commander"}

    def toast(texte, couleur=ft.Colors.BLUE_GREY_800):
        page.show_dialog(ft.SnackBar(ft.Text(texte), bgcolor=couleur))

    # ================================================================ COMMANDER
    choix = ft.SegmentedButton(selected=["enl"], segments=[
        ft.Segment(value="enl", label=ft.Text("1. Enlèvement"), icon=ft.Icon(ft.Icons.STOREFRONT)),
        ft.Segment(value="liv", label=ft.Text("2. Livraison"), icon=ft.Icon(ft.Icons.HOME))])
    adr_enl = ft.TextField(label="Adresse d'enlèvement (repère)", dense=True)
    adr_liv = ft.TextField(label="Adresse de livraison (repère)", dense=True)
    dest = ft.TextField(label="Nom du destinataire", dense=True, expand=True)
    tel_dest = ft.TextField(label="Téléphone", dense=True, expand=True, keyboard_type=ft.KeyboardType.PHONE)
    taille = ft.Dropdown(label="Taille", value="petit", dense=True, expand=True, options=[
        ft.dropdown.Option("petit", "Petit"), ft.dropdown.Option("moyen", "Moyen"),
        ft.dropdown.Option("grand", "Grand")])
    paiement = ft.Dropdown(label="Paiement", value="especes", dense=True, expand=True, options=[
        ft.dropdown.Option("especes", "Espèces à la livraison"),
        ft.dropdown.Option("mobile_money", "Mobile money"), ft.dropdown.Option("carte", "Carte")])
    urgent = ft.Switch(label="Urgent (+50 %)", value=False)
    programme = ft.TextField(label="Programmer (AAAA-MM-JJ HH:MM, facultatif)", dense=True)
    consignes = ft.TextField(label="Consignes pour le livreur", dense=True)
    devis_txt = ft.Column([], spacing=4)
    bouton_commander = ft.FilledButton("Commander", icon=ft.Icons.SEND, height=50, disabled=True)

    m_cmd, mk_cmd, tr_cmd = carte(zoom=12)

    def dessiner_commande():
        pts = []
        if e["enl"]:
            pts.append(marqueur(e["enl"].lat, e["enl"].lon, ft.Icons.STOREFRONT, ft.Colors.ORANGE_800, 34))
        if e["liv"]:
            pts.append(marqueur(e["liv"].lat, e["liv"].lon, ft.Icons.HOME, ft.Colors.PURPLE_700, 34))
        mk_cmd.markers = pts
        tr_cmd.polylines = [trait([(e["enl"].lat, e["enl"].lon), (e["liv"].lat, e["liv"].lon)],
                                  ft.Colors.BLUE_GREY_400)] if e["enl"] and e["liv"] else []

    async def appui_carte(ev):
        p = Point(ev.coordinates.latitude, ev.coordinates.longitude)
        commune = commune_la_plus_proche(p)
        if choix.selected[0] == "enl":
            e["enl"] = p
            adr_enl.value = adr_enl.value or f"{commune}, "
            choix.selected = ["liv"]
        else:
            e["liv"] = p
            adr_liv.value = adr_liv.value or f"{commune}, "
        e["devis"] = None
        bouton_commander.disabled = True
        devis_txt.controls = [ft.Text(f"Point placé à {commune}.", size=13, color=ft.Colors.GREY_700)]
        dessiner_commande()
        page.update()
        if e["enl"] and e["liv"]:
            await calculer_devis()

    m_cmd.on_tap = appui_carte

    async def calculer_devis(_=None):
        if not (e["enl"] and e["liv"]):
            toast("Touchez la carte pour placer l'enlèvement puis la livraison.")
            return
        r = await api.post("/devis", {"enlevement": e["enl"].vers_dict(), "livraison": e["liv"].vers_dict(),
                                      "taille": taille.value, "urgent": urgent.value})
        if r.status_code != 200:
            devis_txt.controls = [ft.Text(r.json().get("detail", "devis impossible"), color=ft.Colors.RED_700)]
            page.update()
            return
        d = e["devis"] = r.json()
        devis_txt.controls = [
            ft.Text(f"{d['prix']} FCFA", size=26, weight=ft.FontWeight.BOLD, color=VERT),
            ft.Text(f"{d['distance_km']} km · environ {d['delai_estime_min']} min jusqu'à la livraison"
                    f"{' · tarif de nuit' if d['detail']['nuit'] else ''}", size=13)]
        bouton_commander.disabled = False
        bouton_commander.content = f"Commander à {d['prix']} FCFA"
        page.update()

    taille.on_select = calculer_devis
    urgent.on_change = calculer_devis

    async def commander(_):
        corps = {"enlevement": e["enl"].vers_dict(), "livraison": e["liv"].vers_dict(),
                 "adresse_enlevement": adr_enl.value, "adresse_livraison": adr_liv.value,
                 "destinataire": dest.value, "telephone_destinataire": tel_dest.value,
                 "taille": taille.value, "urgent": urgent.value, "mode_paiement": paiement.value,
                 "consignes": consignes.value, "prix_attendu": e["devis"]["prix"]}
        if programme.value.strip():
            try:
                corps["programmee_pour"] = datetime.strptime(programme.value.strip(), "%Y-%m-%d %H:%M") \
                    .astimezone().isoformat()
            except ValueError:
                toast("Date programmée invalide (format AAAA-MM-JJ HH:MM).", ft.Colors.RED_700)
                return
        r = await api.post("/commandes", corps)
        if r.status_code == 409:
            await calculer_devis()
            toast("Le tarif a changé : vérifiez le nouveau prix puis validez à nouveau.", ft.Colors.ORANGE_800)
            return
        if r.status_code != 201:
            toast(str(r.json().get("detail", "commande refusée")), ft.Colors.RED_700)
            return
        c = r.json()["commande"]
        e.update(enl=None, liv=None, devis=None)
        bouton_commander.disabled = True
        devis_txt.controls = []
        dessiner_commande()
        toast(f"Commande n° {c['id']} envoyée. Code pour le destinataire : {c['code']}", ft.Colors.GREEN_800)
        await ouvrir_suivi(c["id"])

    bouton_commander.on_click = commander

    vue_commander = ft.Column([
        ft.Container(choix, padding=ft.Padding.only(left=10, right=10, top=6)),
        ft.Text("   Touchez la carte pour placer chaque point.", size=12, color=ft.Colors.GREY_700),
        ft.Container(m_cmd, height=280),
        ft.Container(ft.Column([
            ft.Row([devis_txt, bouton_commander], alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER, wrap=True),
            adr_enl, adr_liv, ft.Row([dest, tel_dest]), ft.Row([taille, paiement]), urgent, programme,
            consignes,
            ft.OutlinedButton("Recalculer le prix", icon=ft.Icons.CALCULATE, on_click=calculer_devis)],
            spacing=8), padding=12)],
        scroll=ft.ScrollMode.AUTO, expand=True, spacing=4)

    # ============================================================ MES COMMANDES
    liste = ft.Column([], spacing=6, scroll=ft.ScrollMode.AUTO, expand=True)

    async def charger_liste():
        cs = (await api.get("/commandes", params={"limite": 30})).json()
        liste.controls = [ft.ListTile(
            title=ft.Text(f"N° {c['id']} · {c['prix']} FCFA · {c['distance_km']} km"),
            subtitle=ft.Text(f"{c['adresse_enlevement'] or 'enlèvement'} -> {c['adresse_livraison'] or 'livraison'}",
                             size=12),
            trailing=pastille(LIBELLE_ETAT.get(c["etat"], c["etat"]), COULEUR_ETAT.get(c["etat"])),
            on_click=lambda _, cid=c["id"]: page.run_task(ouvrir_suivi, cid)) for c in cs] or \
            [ft.Text("Aucune commande pour l'instant.", italic=True)]

    vue_liste = ft.Container(liste, padding=10, expand=True)

    # ==================================================================== SUIVI
    m_suivi, mk_suivi, tr_suivi = carte(zoom=13)
    suivi_entete = ft.Column([], spacing=6)
    suivi_actions = ft.Column([], spacing=8)
    vue_suivi = ft.Column([suivi_entete, ft.Container(m_suivi, height=300),
                           ft.Container(suivi_actions, padding=12)],
                          scroll=ft.ScrollMode.AUTO, expand=True)

    def frise(etat_c: str) -> ft.Row:
        rang = ETAPES.index(etat_c) if etat_c in ETAPES else (3 if etat_c == "livree" else -1)
        noms = ["Recherche", "Livreur en route", "En livraison", "Livrée"]
        return ft.Row([ft.Column([ft.Icon(ft.Icons.CHECK_CIRCLE if i <= rang else ft.Icons.RADIO_BUTTON_UNCHECKED,
                                          color=VERT if i <= rang else ft.Colors.GREY_400),
                                  ft.Text(n, size=10)], horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                 expand=True) for i, n in enumerate(noms)])

    async def ouvrir_suivi(cid: int):
        e["suivi"] = cid
        montrer("suivi")
        await rafraichir_suivi()

    async def rafraichir_suivi():
        cid = e["suivi"]
        r = await api.get(f"/commandes/{cid}")
        if r.status_code != 200:
            return
        d = r.json()
        c, lv = d["commande"], d.get("livreur")
        pts = [marqueur(c["enlevement"]["lat"], c["enlevement"]["lon"], ft.Icons.STOREFRONT, ft.Colors.ORANGE_800),
               marqueur(c["livraison"]["lat"], c["livraison"]["lon"], ft.Icons.HOME, ft.Colors.PURPLE_700)]
        texte_livreur = "Recherche du meilleur livreur par appel d'offres entre agents..."
        if c["etat"] in ("cloturee", "livree"):
            texte_livreur = f"Livrée par le livreur n° {c['livreur_id']}. Merci de votre confiance."
        if lv and lv.get("position") and c["etat"] in ("attribuee", "enlevee"):
            p = lv["position"]
            pts.append(marqueur(p["lat"], p["lon"], ft.Icons.TWO_WHEELER, VERT, 36, "Votre livreur"))
            but = c["livraison"] if c["etat"] == "enlevee" else c["enlevement"]
            tr_suivi.polylines = [trait([(p["lat"], p["lon"]), (but["lat"], but["lon"])])]
            texte_livreur = f"Livreur n° {c['livreur_id']} · position mise à jour il y a {lv['age_gps_s']} s"
        else:
            tr_suivi.polylines = []
        mk_suivi.markers = pts
        suivi_entete.controls = [ft.Container(ft.Column([
            ft.Row([ft.Text(f"Commande n° {cid}", size=18, weight=ft.FontWeight.BOLD),
                    pastille(LIBELLE_ETAT.get(c["etat"], c["etat"]), COULEUR_ETAT.get(c["etat"]))]),
            frise(c["etat"]), ft.Text(texte_livreur, size=12, color=ft.Colors.GREY_700),
            ft.Text(f"Code à donner au destinataire : {c.get('code', '—')}", size=15, weight=ft.FontWeight.BOLD)
            if c["etat"] not in ("cloturee", "annulee", "echec") else ft.Container(),
            ft.Text(f"Programmée pour {c['programmee_pour'][:16].replace('T', ' ')}", size=12)
            if c.get("programmee_pour") and c["etat"] == "validee" else ft.Container()],
            spacing=6), padding=12)]
        actions = []
        if c["etat"] in ("creee", "validee", "recherche", "attribuee"):
            actions.append(ft.OutlinedButton("Annuler la commande", icon=ft.Icons.CANCEL,
                                              on_click=lambda _: page.run_task(annuler, cid)))
        if c["etat"] == "cloturee":
            actions.append(ft.FilledButton("Télécharger la facture", icon=ft.Icons.PICTURE_AS_PDF,
                                           on_click=lambda _: page.run_task(facture, cid)))
        if c["etat"] in ("livree", "cloturee") and c.get("note_client") is None:
            actions.append(ft.Text("Notez votre livreur :", weight=ft.FontWeight.BOLD))
            actions.append(ft.Row([ft.IconButton(ft.Icons.STAR, icon_color=ft.Colors.AMBER_700, icon_size=34,
                                                 tooltip=f"{n} / 5",
                                                 on_click=lambda _, n=n: page.run_task(evaluer, cid, n))
                                   for n in range(1, 6)]))
        elif c.get("note_client"):
            actions.append(ft.Text(f"Votre note : {c['note_client']} / 5. Merci !"))
        suivi_actions.controls = actions
        page.update()

    async def annuler(cid):
        r = await api.post(f"/commandes/{cid}/annuler")
        toast("Commande annulée" + (" (avec frais)" if r.status_code == 200 and r.json().get("frais") else "")
              if r.status_code == 200 else str(r.json().get("detail")),
              ft.Colors.BLUE_GREY_800 if r.status_code == 200 else ft.Colors.RED_700)
        await rafraichir_suivi()

    async def facture(cid):
        await ft.UrlLauncher().launch_url(f"{API}/commandes/{cid}/facture?jeton={api.jeton}")

    async def evaluer(cid, note):
        r = await api.post(f"/commandes/{cid}/evaluation", {"note": note})
        toast("Merci pour votre évaluation !" if r.status_code == 200 else str(r.json().get("detail")))
        await rafraichir_suivi()

    # ============================================================== navigation
    zone = ft.Container(vue_commander, expand=True)
    vues = {"commander": vue_commander, "liste": vue_liste, "suivi": vue_suivi}

    def montrer(v: str):
        e["vue"] = v
        zone.content = vues[v]
        nav.selected_index = {"commander": 0, "liste": 1, "suivi": 1}[v]
        page.update()

    async def changer(ev):
        if nav.selected_index == 0:
            montrer("commander")
        else:
            await charger_liste()
            montrer("liste")

    nav = ft.NavigationBar(on_change=changer, destinations=[
        ft.NavigationBarDestination(icon=ft.Icons.ADD_LOCATION_ALT, label="Commander"),
        ft.NavigationBarDestination(icon=ft.Icons.LIST_ALT, label="Mes commandes")])

    async def boucle():
        while True:
            try:
                if e["vue"] == "suivi" and e["suivi"]:
                    await rafraichir_suivi()
            except Exception as ex:  # noqa: BLE001
                print("client : rafraîchissement impossible :", ex)
            await asyncio.sleep(2)

    async def apres_connexion(profil: dict):
        page.controls.clear()
        page.navigation_bar = nav
        page.add(ft.SafeArea(ft.Column([
            ft.Container(ft.Row([ft.Icon(ft.Icons.TWO_WHEELER, color=VERT),
                                 ft.Text("Livraison SMA", size=18, weight=ft.FontWeight.BOLD, expand=True),
                                 ft.Text(profil.get("nom") or profil.get("telephone", ""), size=12)]),
                         padding=ft.Padding.symmetric(horizontal=12, vertical=6)),
            zone], expand=True, spacing=0), expand=True))
        page.update()
        page.run_task(boucle)

    page.add(ecran_connexion(page, api, "Livraison SMA", "Faites livrer vos colis à Abidjan",
                             apres_connexion))


if __name__ == "__main__":
    lancer(main, 8551)
