"""Entités métier (cahier des charges § 7.1), indépendantes de la base de données."""
from __future__ import annotations

import secrets
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from livraison.modele.cout import Taille, Vehicule
from livraison.modele.etats import Etat
from livraison.modele.geo import Point


def maintenant() -> datetime:
    return datetime.now(UTC)


def code_confirmation() -> str:
    """Code à usage unique communiqué au destinataire (preuve de livraison, F-23)."""
    return f"{secrets.randbelow(10_000):04d}"


@dataclass
class Livreur:
    id: int
    nom: str
    telephone: str
    vehicule: Vehicule = Vehicule.MOTO
    capacite: int = 2
    valide: bool = False  # validé par l'administrateur (F-03) avant toute course
    note: float = 4.5
    suspendu: bool = False
    especes_detenues: int = 0  # FCFA encaissés à reverser (F-52)
    nb_evaluations: int = 0

    def vers_dict(self) -> dict:
        d = asdict(self)
        d["vehicule"] = str(self.vehicule)
        return d


@dataclass
class Commande:
    id: int
    client: str
    enlevement: Point
    livraison: Point
    adresse_enlevement: str = ""
    adresse_livraison: str = ""
    destinataire: str = ""
    telephone_destinataire: str = ""
    taille: Taille = Taille.PETIT
    urgent: bool = False
    consignes: str = ""
    mode_paiement: str = "especes"  # especes | mobile_money | carte
    prix: int = 0
    distance_km: float = 0.0
    etat: Etat = Etat.CREEE
    livreur_id: int | None = None
    code: str = field(default_factory=code_confirmation)
    tentatives: int = 0
    cree_a: datetime = field(default_factory=maintenant)
    attribuee_a: datetime | None = None
    enlevee_a: datetime | None = None
    livree_a: datetime | None = None
    motif: str = ""
    programmee_pour: datetime | None = None  # F-16 : livraison programmée (UTC)
    note_client: int | None = None  # F-15 : évaluation de 1 à 5
    commentaire_client: str = ""

    def vers_dict(self, avec_code: bool = False) -> dict:
        d = {
            "id": self.id, "client": self.client,
            "enlevement": self.enlevement.vers_dict(), "livraison": self.livraison.vers_dict(),
            "adresse_enlevement": self.adresse_enlevement,
            "adresse_livraison": self.adresse_livraison,
            "destinataire": self.destinataire, "telephone_destinataire": self.telephone_destinataire,
            "taille": str(self.taille), "urgent": self.urgent, "consignes": self.consignes,
            "mode_paiement": self.mode_paiement, "prix": self.prix,
            "distance_km": self.distance_km, "etat": str(self.etat),
            "livreur_id": self.livreur_id, "tentatives": self.tentatives, "motif": self.motif,
            "note_client": self.note_client, "commentaire_client": self.commentaire_client,
        }
        for k in ("cree_a", "attribuee_a", "enlevee_a", "livree_a", "programmee_pour"):
            v = getattr(self, k)
            d[k] = v.isoformat() if v else None
        if avec_code:
            d["code"] = self.code
        return d

    @classmethod
    def depuis_dict(cls, d: dict) -> Commande:
        c = cls(
            id=d["id"], client=d.get("client", ""),
            enlevement=Point.depuis_dict(d["enlevement"]), livraison=Point.depuis_dict(d["livraison"]),
            adresse_enlevement=d.get("adresse_enlevement", ""),
            adresse_livraison=d.get("adresse_livraison", ""),
            destinataire=d.get("destinataire", ""),
            telephone_destinataire=d.get("telephone_destinataire", ""),
            taille=Taille(d.get("taille", "petit")), urgent=bool(d.get("urgent", False)),
            consignes=d.get("consignes", ""), mode_paiement=d.get("mode_paiement", "especes"),
            prix=int(d.get("prix", 0)), distance_km=float(d.get("distance_km", 0)),
            etat=Etat(d.get("etat", "creee")), livreur_id=d.get("livreur_id"),
            tentatives=int(d.get("tentatives", 0)), motif=d.get("motif", ""),
            note_client=d.get("note_client"), commentaire_client=d.get("commentaire_client", ""),
        )
        if d.get("code"):
            c.code = d["code"]
        for k in ("cree_a", "attribuee_a", "enlevee_a", "livree_a", "programmee_pour"):
            if d.get(k):
                setattr(c, k, datetime.fromisoformat(d[k]))
        return c
