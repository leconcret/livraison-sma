"""Éléments partagés par les applications Flet : client HTTP de l'API, écran de connexion,
carte d'Abidjan, couleurs et petits composants."""
from __future__ import annotations

import os
from collections.abc import Awaitable, Callable

import flet as ft
import flet_map as fm
import httpx

API = os.environ.get("LIVRAISON_API", "http://127.0.0.1:8000")
ABIDJAN = fm.MapLatitudeLongitude(5.335, -4.005)

VERT = "#0D7A74"
COULEUR_ETAT = {
    "creee": ft.Colors.GREY_500, "validee": ft.Colors.BLUE_GREY_500, "recherche": ft.Colors.ORANGE_700,
    "attribuee": ft.Colors.BLUE_700, "enlevee": ft.Colors.PURPLE_600, "livree": ft.Colors.GREEN_600,
    "cloturee": ft.Colors.GREEN_800, "annulee": ft.Colors.GREY_700, "echec": ft.Colors.RED_700,
}
LIBELLE_ETAT = {"creee": "Créée", "validee": "Validée", "recherche": "Recherche d'un livreur",
                "attribuee": "Livreur en route", "enlevee": "Colis en livraison", "livree": "Livrée",
                "cloturee": "Livrée et clôturée", "annulee": "Annulée", "echec": "Échec de livraison"}


class ClientApi:
    """Accès à l'API avec le jeton de l'utilisateur connecté."""

    def __init__(self):
        self.http = httpx.AsyncClient(base_url=API, timeout=15)
        self.jeton: str | None = None
        self.profil: dict = {}

    @property
    def entetes(self) -> dict:
        return {"Authorization": f"Bearer {self.jeton}"} if self.jeton else {}

    async def get(self, chemin: str, **kw) -> httpx.Response:
        return await self.http.get(chemin, headers=self.entetes, **kw)

    async def post(self, chemin: str, json: dict | None = None, **kw) -> httpx.Response:
        return await self.http.post(chemin, headers=self.entetes, json=json, **kw)


def pastille(texte: str, couleur, taille: int = 11) -> ft.Container:
    return ft.Container(ft.Text(texte, size=taille, color=ft.Colors.WHITE, weight=ft.FontWeight.W_600),
                        bgcolor=couleur, padding=ft.Padding.symmetric(horizontal=8, vertical=3),
                        border_radius=10)


def carte(centre=ABIDJAN, zoom: float = 12.5, sur_appui=None) -> tuple[fm.Map, fm.MarkerLayer,
                                                                        fm.PolylineLayer]:
    marqueurs = fm.MarkerLayer(markers=[])
    traces = fm.PolylineLayer(polylines=[])
    m = fm.Map(expand=True, initial_center=centre, initial_zoom=zoom, on_tap=sur_appui, layers=[
        fm.TileLayer(url_template="https://tile.openstreetmap.org/{z}/{x}/{y}.png",
                     user_agent_package_name="ci.master2.livraison_sma"),
        traces, marqueurs, fm.SimpleAttribution(text="© contributeurs OpenStreetMap")])
    return m, marqueurs, traces


def marqueur(lat: float, lon: float, icone, couleur, taille: int = 30, info: str = "") -> fm.Marker:
    return fm.Marker(coordinates=fm.MapLatitudeLongitude(lat, lon), width=taille, height=taille,
                     content=ft.Icon(icone, color=couleur, size=taille, tooltip=info or None))


def trait(points: list[tuple[float, float]], couleur=VERT) -> fm.PolylineMarker:
    return fm.PolylineMarker(coordinates=[fm.MapLatitudeLongitude(a, b) for a, b in points],
                             color=couleur, stroke_width=4)


def ecran_connexion(page: ft.Page, api: ClientApi, titre: str, sous_titre: str,
                    apres: Callable[[dict], Awaitable[None]], tel_defaut: str = "") -> ft.Control:
    """Connexion par téléphone et code à usage unique (F-01). En mode développement, le code
    « SMS » est prérempli."""
    tel = ft.TextField(label="Numéro de téléphone", value=tel_defaut, width=300,
                       keyboard_type=ft.KeyboardType.PHONE, prefix_icon=ft.Icons.PHONE)
    nom = ft.TextField(label="Votre nom (première connexion)", width=300)
    code = ft.TextField(label="Code reçu par SMS", width=300, visible=False,
                        prefix_icon=ft.Icons.SMS)
    msg = ft.Text("", color=ft.Colors.RED_700)

    async def envoyer_code(e):
        try:
            r = (await api.http.post("/auth/code", json={"telephone": tel.value})).json()
        except httpx.HTTPError:
            msg.value = f"Serveur injoignable ({API})."
            page.update()
            return
        code.visible = True
        code.value = r.get("code_dev", "")
        bouton.content = "Se connecter"
        bouton.on_click = connecter
        msg.value = "Code envoyé." + (" (mode développement : prérempli)" if r.get("code_dev") else "")
        msg.color = ft.Colors.GREEN_800
        page.update()

    async def connecter(e):
        r = await api.http.post("/auth/jeton", json={"telephone": tel.value, "code": code.value,
                                                     "nom": nom.value})
        if r.status_code != 200:
            msg.value, msg.color = "Code incorrect ou expiré.", ft.Colors.RED_700
            page.update()
            return
        api.jeton = r.json()["jeton"]
        api.profil = (await api.get("/moi")).json()
        await apres(api.profil)

    bouton = ft.FilledButton("Recevoir le code", on_click=envoyer_code, width=300, height=48)
    return ft.Container(ft.Column([
        ft.Icon(ft.Icons.TWO_WHEELER, size=64, color=VERT),
        ft.Text(titre, size=28, weight=ft.FontWeight.BOLD),
        ft.Text(sous_titre, size=15, color=ft.Colors.GREY_700),
        ft.Container(height=10), tel, nom, code, bouton, msg],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=12),
        alignment=ft.Alignment.CENTER, expand=True, padding=24)


def lancer(main, port: int) -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--web", action="store_true", help="ouvrir dans le navigateur")
    ap.add_argument("--port", type=int, default=port)
    args = ap.parse_args()
    if args.web:
        ft.run(main, view=ft.AppView.WEB_BROWSER, port=args.port, no_cdn=True)
    else:
        ft.run(main)
