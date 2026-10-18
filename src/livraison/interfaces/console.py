"""Console du répartiteur (§ 5, F-60 à F-64), en Flet.

Lancement (le serveur API doit tourner) :
    python -m livraison.interfaces.console --web     # dans le navigateur : http://127.0.0.1:8550
    python -m livraison.interfaces.console           # fenêtre de bureau
Le mode web sert le moteur graphique depuis le poste (no_cdn) : il fonctionne sans Internet,
seul le fond de carte OpenStreetMap a besoin du réseau.

Écran : carte en direct d'Abidjan (livreurs et commandes actives), indicateurs, liste des
commandes, fiche d'une commande avec ses offres et le journal des messages entre agents
(équivalent du Sniffer), alertes, réattribution et annulation manuelles.
"""
from __future__ import annotations

import asyncio
import os

import flet as ft
import flet_map as fm
import httpx

API = os.environ.get("LIVRAISON_API", "http://127.0.0.1:8000")
TEL_DEFAUT = os.environ.get("REPARTITEUR_TELEPHONE", "0700000001")

COULEUR_ETAT = {
    "creee": ft.Colors.GREY_500, "validee": ft.Colors.GREY_600, "recherche": ft.Colors.ORANGE_700,
    "attribuee": ft.Colors.BLUE_700, "enlevee": ft.Colors.PURPLE_600, "livree": ft.Colors.GREEN_600,
    "cloturee": ft.Colors.GREEN_800, "annulee": ft.Colors.GREY_700, "echec": ft.Colors.RED_700,
}
LIBELLE_ETAT = {"creee": "Créée", "validee": "Validée", "recherche": "Recherche",
                "attribuee": "Attribuée", "enlevee": "Enlevée", "livree": "Livrée",
                "cloturee": "Clôturée", "annulee": "Annulée", "echec": "Échec"}
COULEUR_PERF = {"cfp": ft.Colors.INDIGO_700, "propose": ft.Colors.TEAL_700,
                "refuse": ft.Colors.RED_700, "accept-proposal": ft.Colors.GREEN_800,
                "reject-proposal": ft.Colors.DEEP_ORANGE_700, "inform": ft.Colors.BLUE_GREY_700,
                "failure": ft.Colors.RED_900, "agree": ft.Colors.GREEN_700,
                "request": ft.Colors.BROWN_600, "query-ref": ft.Colors.CYAN_800}
ABIDJAN = fm.MapLatitudeLongitude(5.335, -4.005)


def pastille(texte: str, couleur) -> ft.Container:
    return ft.Container(ft.Text(texte, size=11, color=ft.Colors.WHITE, weight=ft.FontWeight.W_600),
                        bgcolor=couleur, padding=ft.Padding.symmetric(horizontal=8, vertical=2),
                        border_radius=10)


TYPES_MASQUES = {"notification", "position"}  # bruit inutile dans un diagramme de séquence


def ordre_participant(nom: str) -> tuple:
    rang = {"dispatcher": 1, "annuaire": 2, "passerelle": 4, "paiement": 5, "superviseur": 6,
            "notification": 7}
    if nom.startswith("commande-"):
        return (0, nom)
    if nom.startswith("livreur-"):
        return (3, int(nom.split("-")[1]) if nom.split("-")[1].isdigit() else 0)
    return (rang.get(nom, 8), nom)


def diagramme_sequence(evts: list[dict], largeur: int = 500, max_messages: int = 60) -> ft.Control:
    """Diagramme de séquence UML dessiné à partir des vrais messages ACL (équivalent du Sniffer
    de JADE) : une ligne de vie par agent, une flèche par message, colorée selon la performative."""
    msgs = [e for e in evts if e.get("categorie") == "message"
            and e.get("type") not in TYPES_MASQUES and e.get("emetteur") and e.get("destinataire")]
    msgs = msgs[-max_messages:]
    if not msgs:
        return ft.Text("Aucun message entre agents pour l'instant.", italic=True, size=12)
    agents = sorted({m["emetteur"] for m in msgs} | {m["destinataire"] for m in msgs},
                    key=ordre_participant)
    col = max(92, largeur // len(agents))
    x = {a: col * i + col // 2 for i, a in enumerate(agents)}
    entete, pas = 34, 30
    hauteur = entete + pas * len(msgs) + 16
    largeur_totale = col * len(agents)
    elems: list[ft.Control] = []
    for a in agents:  # lignes de vie et en-têtes
        elems.append(ft.Container(left=x[a], top=entete, width=1, height=hauteur - entete,
                                  bgcolor=ft.Colors.BLUE_GREY_200))
        couleur = (ft.Colors.AMBER_700 if a == "dispatcher" else ft.Colors.TEAL_700
                   if a.startswith("livreur") else ft.Colors.INDIGO_400 if a.startswith("commande")
                   else ft.Colors.BLUE_GREY_500)
        elems.append(ft.Container(
            ft.Text(a, size=10, color=ft.Colors.WHITE, weight=ft.FontWeight.W_600, no_wrap=True),
            left=x[a] - col // 2 + 4, top=2, width=col - 8, height=26, bgcolor=couleur,
            border_radius=6, alignment=ft.Alignment.CENTER))
    for i, m in enumerate(msgs):
        y = entete + pas * i + 20
        x1, x2 = x[m["emetteur"]], x[m["destinataire"]]
        couleur = COULEUR_PERF.get(m.get("performative"), ft.Colors.BLACK)
        gauche, droite = min(x1, x2), max(x1, x2)
        elems.append(ft.Container(left=gauche, top=y, width=max(droite - gauche, 2), height=2,
                                  bgcolor=couleur))
        vers_droite = x2 >= x1
        elems.append(ft.Icon(ft.Icons.ARROW_RIGHT if vers_droite else ft.Icons.ARROW_LEFT,
                             color=couleur, size=18, left=(x2 - 12) if vers_droite else (x2 - 6),
                             top=y - 8))
        elems.append(ft.Text(f"{i + 1}. {m.get('performative')} · {m.get('type')}", size=10,
                             color=couleur, weight=ft.FontWeight.W_600, no_wrap=True,
                             left=gauche + 6, top=y - 15))
    return ft.Row([ft.Stack(elems, width=largeur_totale, height=hauteur)],
                  scroll=ft.ScrollMode.AUTO)


def indicateur(titre: str) -> tuple[ft.Container, ft.Text]:
    valeur = ft.Text("–", size=22, weight=ft.FontWeight.BOLD)
    return ft.Container(ft.Column([ft.Text(titre, size=11, color=ft.Colors.GREY_700), valeur],
                                  spacing=2), padding=10, border_radius=8, expand=True,
                        bgcolor=ft.Colors.with_opacity(0.06, ft.Colors.BLUE_GREY_900)), valeur


async def main(page: ft.Page):
    page.title = "Livraison SMA · Console du répartiteur"
    page.padding = 0
    page.theme_mode = ft.ThemeMode.LIGHT
    http = httpx.AsyncClient(base_url=API, timeout=10)
    etat = {"entetes": None, "selection": None, "vue": "commandes", "livreurs": []}

    # ------------------------------------------------------------ connexion
    tel = ft.TextField(label="Téléphone du répartiteur", value=TEL_DEFAUT, width=280)
    code = ft.TextField(label="Code reçu par SMS", width=280, visible=False)
    message = ft.Text("", color=ft.Colors.RED_700)

    async def demander_code(e):
        try:
            r = (await http.post("/auth/code", json={"telephone": tel.value})).json()
        except httpx.HTTPError:
            message.value = f"Serveur injoignable ({API}). Lancez : uvicorn livraison.api.main:app"
            page.update()
            return
        code.visible = True
        if r.get("code_dev"):
            code.value = r["code_dev"]  # mode développement : le « SMS » est affiché
        bouton.content = "Se connecter"
        bouton.on_click = se_connecter
        page.update()

    async def se_connecter(e):
        r = await http.post("/auth/jeton", json={"telephone": tel.value, "code": code.value})
        if r.status_code != 200 or r.json()["role"] not in ("repartiteur", "admin"):
            message.value = "Connexion refusée (code incorrect ou compte sans droit de supervision)."
            page.update()
            return
        etat["entetes"] = {"Authorization": f"Bearer {r.json()['jeton']}"}
        page.controls.clear()
        page.add(ecran)
        page.update()
        page.run_task(boucle)

    bouton = ft.Button("Recevoir un code", on_click=demander_code)
    connexion = ft.Container(ft.Column([
        ft.Text("Livraison SMA", size=28, weight=ft.FontWeight.BOLD),
        ft.Text("Console du répartiteur", size=16, color=ft.Colors.GREY_700),
        tel, code, bouton, message], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=14),
        alignment=ft.Alignment.CENTER, expand=True)

    # ------------------------------------------------------------- carte (F-60)
    couche_cmd = fm.CircleLayer(circles=[])
    couche_liv = fm.MarkerLayer(markers=[])
    carte = fm.Map(expand=True, initial_center=ABIDJAN, initial_zoom=12, layers=[
        fm.TileLayer(url_template="https://tile.openstreetmap.org/{z}/{x}/{y}.png",
                     user_agent_package_name="ci.master2.livraison_sma"),
        couche_cmd, couche_liv,
        fm.SimpleAttribution(text="© contributeurs OpenStreetMap"),
    ])
    legende = ft.Row([pastille("Livreur libre", ft.Colors.GREEN_700),
                      pastille("Livreur occupé", ft.Colors.AMBER_800)]
                     + [pastille(LIBELLE_ETAT[e], COULEUR_ETAT[e])
                        for e in ("recherche", "attribuee", "enlevee")], wrap=True, spacing=6)

    # --------------------------------------------------------- indicateurs (F-64)
    k_liv, v_liv = indicateur("Livreurs en ligne")
    k_act, v_act = indicateur("Commandes actives")
    k_att, v_att = indicateur("En attente de livreur")
    k_dec, v_dec = indicateur("Décision SMA (moy.)")
    k_clo, v_clo = indicateur("Clôturées")

    # ---------------------------------------------------------- panneaux droits
    liste_cmd = ft.ListView(expand=True, spacing=4)
    journal = ft.ListView(expand=True, spacing=2, auto_scroll=True)
    alertes = ft.ListView(expand=True, spacing=4)
    fiche = ft.Column(expand=True, scroll=ft.ScrollMode.AUTO, spacing=6)
    titre_sniffer = ft.Text("", size=13, weight=ft.FontWeight.BOLD)
    zone_sniffer = ft.Column([], scroll=ft.ScrollMode.AUTO, expand=True)
    sniffer = ft.Column([ft.Text("Diagramme de séquence en direct (vrais messages ACL entre agents)",
                                 size=12, color=ft.Colors.GREY_700), titre_sniffer, zone_sniffer],
                        expand=True, spacing=6)
    panneaux = {"commandes": liste_cmd, "journal": journal, "alertes": alertes, "fiche": fiche,
                "sniffer": sniffer}
    zone = ft.Container(liste_cmd, expand=True)

    def montrer(vue: str):
        etat["vue"] = vue
        zone.content = panneaux[vue]
        droite.width = 960 if vue == "sniffer" else 520  # le Sniffer prend la place de la carte
        for b in onglets.controls:
            b.style = ft.ButtonStyle(bgcolor=ft.Colors.BLUE_GREY_100 if b.data == vue else None)
        page.update()

    onglets = ft.Row([ft.TextButton(t, data=v, on_click=lambda e: montrer(e.control.data))
                      for t, v in (("Commandes", "commandes"), ("Sniffer", "sniffer"),
                                   ("Journal des agents", "journal"), ("Alertes", "alertes"))],
                     spacing=2)

    def ligne_message(e: dict) -> ft.Control:
        if e["categorie"] == "message":
            return ft.Row([
                ft.Text(e["ts"][11:19], size=11, color=ft.Colors.GREY_600, width=58),
                ft.Text(e.get("emetteur", ""), size=11, width=92, no_wrap=True),
                ft.Text("->", size=11, color=ft.Colors.GREY_600),
                ft.Text(e.get("destinataire", ""), size=11, width=92, no_wrap=True),
                ft.Text(e.get("performative", ""), size=11, width=108, weight=ft.FontWeight.W_600,
                        color=COULEUR_PERF.get(e.get("performative"), ft.Colors.BLACK)),
                ft.Text(e.get("type", ""), size=11, expand=True, no_wrap=True),
            ], spacing=4)
        couleur = ft.Colors.RED_700 if e["categorie"] == "alerte" else ft.Colors.BLUE_GREY_800
        texte = e.get("texte") or e.get("libelle") or ""
        return ft.Text(f"{e['ts'][11:19]}  [{e['categorie']}] {texte}", size=11, color=couleur,
                       italic=e["categorie"] == "protocole")

    async def ouvrir(cid: int):
        etat["selection"] = cid
        r = await http.get(f"/commandes/{cid}", headers=etat["entetes"])
        if r.status_code != 200:
            return
        d = r.json()
        c, lv = d["commande"], d.get("livreur")
        fiche.controls = [
            ft.Row([ft.Text(f"Commande {cid}", size=18, weight=ft.FontWeight.BOLD),
                    pastille(LIBELLE_ETAT.get(c["etat"], c["etat"]), COULEUR_ETAT.get(c["etat"]))]),
            ft.Text(f"Client {c['client']} · {c['prix']} FCFA · {c['distance_km']} km · "
                    f"{c['taille']}{' · URGENT' if c['urgent'] else ''} · paiement {c['mode_paiement']}",
                    size=12),
            ft.Text(f"Livreur : {c['livreur_id'] or '—'}   Tentatives : {c['tentatives']}   "
                    f"{c['motif']}", size=12),
            ft.Row([
                ft.Button("Réattribuer", icon=ft.Icons.SWAP_HORIZ,
                          on_click=lambda e: page.run_task(reattribuer, cid, None)),
                ft.Dropdown(label="Imposer un livreur", width=200, dense=True,
                            options=[ft.dropdown.Option(str(l["livreur_id"]), f"Livreur {l['livreur_id']} "
                                                        f"({l['charge']}/{l['capacite']})")
                                     for l in etat["livreurs"]],
                            on_select=lambda e: page.run_task(reattribuer, cid, int(e.control.value))),
                ft.OutlinedButton("Annuler", icon=ft.Icons.CANCEL,
                                  on_click=lambda e: page.run_task(annuler, cid)),
            ], wrap=True),
            ft.Text("Offres reçues", weight=ft.FontWeight.BOLD),
            *[ft.Text(f"tentative {o['tentative']} · livreur {o['livreur_id']} · coût {o['cout']:.0f}"
                      f" · {o['resultat']}", size=12) for o in d.get("offres", [])],
            ft.Row([ft.Text("Contract Net de cette commande", weight=ft.FontWeight.BOLD),
                    ft.TextButton("Suivre en direct dans le Sniffer",
                                  on_click=lambda e: montrer("sniffer"))]),
            diagramme_sequence(d.get("journal", []), 490),
            ft.Text("Journal détaillé", weight=ft.FontWeight.BOLD),
            *[ligne_message(e) for e in d.get("journal", [])
              if e["categorie"] in ("message", "etat", "protocole", "alerte")],
        ]
        if lv and lv.get("position"):
            fiche.controls.insert(3, ft.Text(f"Position du livreur : {lv['position']['lat']:.5f}, "
                                             f"{lv['position']['lon']:.5f} (GPS il y a "
                                             f"{lv['age_gps_s']} s)", size=12))
        montrer("fiche")

    async def reattribuer(cid, livreur_id):
        r = await http.post(f"/commandes/{cid}/reattribuer", headers=etat["entetes"],
                            json={"livreur_id": livreur_id})
        page.show_dialog(ft.SnackBar(ft.Text("Réattribution lancée" if r.status_code == 200
                                             else r.json().get("detail", "refusée"))))
        await ouvrir(cid)

    async def annuler(cid):
        r = await http.post(f"/commandes/{cid}/annuler", headers=etat["entetes"])
        page.show_dialog(ft.SnackBar(ft.Text("Commande annulée" if r.status_code == 200
                                             else r.json().get("detail", "refusée"))))
        await ouvrir(cid)

    # ----------------------------------------------------------- rafraîchissement
    async def rafraichir():
        h = etat["entetes"]
        carte_d, stats, jr = await asyncio.gather(
            http.get("/supervision/carte", headers=h), http.get("/supervision/statistiques", headers=h),
            http.get("/supervision/journal?limite=120", headers=h))
        carte_d, stats, jr = carte_d.json(), stats.json(), jr.json()
        etat["livreurs"] = carte_d["livreurs"]
        couche_liv.markers = [
            fm.Marker(coordinates=fm.MapLatitudeLongitude(l["position"]["lat"], l["position"]["lon"]),
                      width=26, height=26,
                      content=ft.Icon(ft.Icons.TWO_WHEELER, size=22, tooltip=f"Livreur {l['livreur_id']} "
                                      f"· {l['charge']}/{l['capacite']} course(s)",
                                      color=ft.Colors.AMBER_800 if l["charge"] else ft.Colors.GREEN_700))
            for l in carte_d["livreurs"] if l.get("position")]
        couche_cmd.circles = [
            fm.CircleMarker(coordinates=fm.MapLatitudeLongitude(c["enlevement"]["lat"], c["enlevement"]["lon"]),
                            radius=7, color=COULEUR_ETAT.get(c["etat"], ft.Colors.GREY),
                            border_color=ft.Colors.WHITE, border_stroke_width=1.5)
            for c in carte_d["commandes"]]
        disp = stats["dispatcher"]
        v_liv.value = str(stats["livreurs_en_ligne"])
        v_act.value = str(len(carte_d["commandes"]))
        v_att.value = str(len(disp["en_attente"]))
        dec = disp.get("decision_s") or {}
        v_dec.value = f"{dec['moyenne'] * 1000:.1f} ms" if dec.get("moyenne") is not None else "–"
        v_clo.value = str(stats["par_etat"].get("cloturee", 0))
        liste_cmd.controls = [
            ft.ListTile(dense=True, on_click=lambda e, cid=c["id"]: page.run_task(ouvrir, cid),
                        title=ft.Text(f"#{c['id']}  {c['prix']} FCFA  ·  {c['distance_km']} km"
                                      + ("  ·  URGENT" if c["urgent"] else ""), size=13),
                        subtitle=ft.Text(f"livreur {c['livreur_id'] or '—'} · tentatives {c['tentatives']}"
                                         f" {('· ' + c['motif']) if c['motif'] else ''}", size=11),
                        trailing=pastille(LIBELLE_ETAT.get(c["etat"], c["etat"]),
                                          COULEUR_ETAT.get(c["etat"])))
            for c in carte_d["commandes"][:150]]
        journal.controls = [ligne_message(e) for e in jr
                            if e["categorie"] in ("message", "protocole", "alerte", "etat")][-120:]
        alertes.controls = [ft.Text(f"{e['ts'][11:19]}  {e.get('texte', '')}", size=12,
                                    color=ft.Colors.RED_800)
                            for e in jr if e["categorie"] in ("alerte", "signalement")][-50:]
        await rafraichir_sniffer(jr)
        page.update()

    async def rafraichir_sniffer(jr):
        if etat["vue"] != "sniffer":
            return
        cid = etat["selection"]
        if cid is None:  # pas de commande choisie : la plus récente qui a des messages
            ids = [e.get("commande_id") for e in jr if e["categorie"] == "message" and e.get("commande_id")]
            cid = ids[-1] if ids else None
        if cid is None:
            titre_sniffer.value = "En attente de messages..."
            return
        r = await http.get(f"/commandes/{cid}", headers=etat["entetes"])
        if r.status_code == 200:
            d = r.json()
            c = d["commande"]
            titre_sniffer.value = (f"Commande {cid} · {LIBELLE_ETAT.get(c['etat'], c['etat'])} · "
                                   f"livreur {c['livreur_id'] or '—'} · tentatives {c['tentatives']}")
            zone_sniffer.controls = [diagramme_sequence(d.get("journal", []), 930, 80)]

    async def boucle():
        while True:
            try:
                await rafraichir()
            except Exception as e:  # noqa: BLE001
                print("console : rafraîchissement impossible :", e)
            await asyncio.sleep(2)

    droite = ft.Container(ft.Column([
        ft.Row([k_liv, k_act], spacing=8), ft.Row([k_att, k_dec, k_clo], spacing=8),
        onglets, ft.Divider(height=1), zone], spacing=8, expand=True),
        width=520, padding=12)
    ecran = ft.Row([
        ft.Column([ft.Container(ft.Row([ft.Text("Livraison SMA · Abidjan", size=18,
                                                weight=ft.FontWeight.BOLD), legende],
                                       alignment=ft.MainAxisAlignment.SPACE_BETWEEN, wrap=True),
                                padding=10),
                   ft.Container(carte, expand=True)], expand=True, spacing=0),
        ft.VerticalDivider(width=1), droite], expand=True, spacing=0)

    page.add(connexion)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Console du répartiteur")
    ap.add_argument("--web", action="store_true", help="ouvrir dans le navigateur")
    ap.add_argument("--port", type=int, default=8550)
    args = ap.parse_args()
    if args.web:
        ft.run(main, view=ft.AppView.WEB_BROWSER, port=args.port, no_cdn=True)
    else:
        ft.run(main)
