"""Accès aux données : interface commune et implémentation en mémoire.

Les agents et l'API ne manipulent que cette interface. Deux implémentations :
- DepotMemoire : pour les tests et une démonstration sans base de données ;
- DepotSQL (donnees/sql.py) : PostgreSQL + PostGIS, utilisé en fonctionnement normal.
"""
from __future__ import annotations

import itertools
from abc import ABC, abstractmethod
from datetime import datetime

from livraison.modele.entites import Commande, Livreur, maintenant
from livraison.modele.etats import Etat


class Depot(ABC):
    # --- utilisateurs (F-01, F-02) ---
    @abstractmethod
    async def utilisateur_par_telephone(self, telephone: str) -> dict | None: ...

    @abstractmethod
    async def creer_utilisateur(self, telephone: str, nom: str, role: str) -> dict: ...

    # --- livreurs ---
    @abstractmethod
    async def creer_livreur(self, livreur: Livreur) -> Livreur: ...

    @abstractmethod
    async def livreur(self, livreur_id: int) -> Livreur | None: ...

    @abstractmethod
    async def livreurs(self) -> list[Livreur]: ...

    @abstractmethod
    async def maj_livreur(self, livreur_id: int, **champs) -> None: ...

    # --- commandes ---
    @abstractmethod
    async def creer_commande(self, commande: Commande) -> Commande: ...

    @abstractmethod
    async def commande(self, commande_id: int) -> Commande | None: ...

    @abstractmethod
    async def commandes(self, etats: set[Etat] | None = None, client: str | None = None,
                        livreur_id: int | None = None, limite: int = 500,
                        decalage: int = 0) -> list[Commande]: ...

    # --- factures (F-53) ---
    @abstractmethod
    async def noter_facture(self, facture: dict) -> dict: ...

    @abstractmethod
    async def facture(self, commande_id: int) -> dict | None: ...

    @abstractmethod
    async def factures(self, livreur_id: int | None = None) -> list[dict]: ...

    @abstractmethod
    async def nombre_factures(self) -> int: ...

    @abstractmethod
    async def maj_commande(self, commande: Commande) -> None: ...

    # --- offres, paiements, journal ---
    @abstractmethod
    async def noter_offre(self, commande_id: int, livreur_id: int, cout: float,
                          eta_s: float | None, resultat: str, tentative: int) -> None: ...

    @abstractmethod
    async def noter_paiement(self, commande_id: int, mode: str, montant: int,
                             reference: str, etat: str) -> None: ...

    @abstractmethod
    async def paiements(self, commande_id: int | None = None) -> list[dict]: ...

    @abstractmethod
    async def noter_evenement(self, evt: dict) -> None: ...

    @abstractmethod
    async def evenements(self, commande_id: int | None = None, limite: int = 500) -> list[dict]: ...

    async def fermer(self) -> None:
        return None


class DepotMemoire(Depot):
    def __init__(self):
        self._utilisateurs: dict[str, dict] = {}
        self._livreurs: dict[int, Livreur] = {}
        self._commandes: dict[int, Commande] = {}
        self._offres: list[dict] = []
        self._paiements: list[dict] = []
        self._evenements: list[dict] = []
        self._factures: list[dict] = []
        self._id_u = itertools.count(1)
        self._id_l = itertools.count(1)
        self._id_c = itertools.count(1)

    async def utilisateur_par_telephone(self, telephone):
        return self._utilisateurs.get(telephone)

    async def creer_utilisateur(self, telephone, nom, role):
        u = {"id": next(self._id_u), "telephone": telephone, "nom": nom, "role": role}
        self._utilisateurs[telephone] = u
        return u

    async def creer_livreur(self, livreur):
        if not livreur.id:
            livreur.id = next(self._id_l)
        self._livreurs[livreur.id] = livreur
        return livreur

    async def livreur(self, livreur_id):
        return self._livreurs.get(livreur_id)

    async def livreurs(self):
        return list(self._livreurs.values())

    async def maj_livreur(self, livreur_id, **champs):
        lv = self._livreurs[livreur_id]
        for k, v in champs.items():
            setattr(lv, k, v)

    async def creer_commande(self, commande):
        if not commande.id:
            commande.id = next(self._id_c)
        self._commandes[commande.id] = commande
        return commande

    async def commande(self, commande_id):
        c = self._commandes.get(commande_id)
        return Commande.depuis_dict(c.vers_dict(avec_code=True)) if c else None

    async def commandes(self, etats=None, client=None, livreur_id=None, limite=500, decalage=0):
        res = [c for c in self._commandes.values()
               if (etats is None or c.etat in etats)
               and (client is None or c.client == client)
               and (livreur_id is None or c.livreur_id == livreur_id)]
        return sorted(res, key=lambda c: c.id, reverse=True)[decalage:decalage + limite]

    async def noter_facture(self, facture):
        self._factures.append(facture)
        return facture

    async def facture(self, commande_id):
        return next((f for f in self._factures if f["commande_id"] == commande_id), None)

    async def factures(self, livreur_id=None):
        return [f for f in self._factures if livreur_id is None or f["livreur_id"] == livreur_id]

    async def nombre_factures(self):
        return len(self._factures)

    async def maj_commande(self, commande):
        self._commandes[commande.id] = Commande.depuis_dict(commande.vers_dict(avec_code=True))

    async def noter_offre(self, commande_id, livreur_id, cout, eta_s, resultat, tentative):
        self._offres.append({"commande_id": commande_id, "livreur_id": livreur_id, "cout": cout,
                                 "eta_s": eta_s, "resultat": resultat, "tentative": tentative,
                                 "ts": maintenant().isoformat()})

    async def noter_paiement(self, commande_id, mode, montant, reference, etat):
        self._paiements.append({"commande_id": commande_id, "mode": mode, "montant": montant,
                                    "reference": reference, "etat": etat, "ts": maintenant().isoformat()})

    async def paiements(self, commande_id=None):
        return [p for p in self._paiements if commande_id is None or p["commande_id"] == commande_id]

    async def noter_evenement(self, evt):
        self._evenements.append(evt)
        if len(self._evenements) > 50_000:
            del self._evenements[:10_000]

    async def evenements(self, commande_id=None, limite=500):
        res = [e for e in self._evenements if commande_id is None or e.get("commande_id") == commande_id]
        return res[-limite:]


    async def offres(self, commande_id):
        return [o for o in self._offres if o["commande_id"] == commande_id]

    async def commandes_proches(self, p, rayon_m, etats=None):
        from livraison.modele.geo import distance_km
        return [c for c in await self.commandes(etats) if distance_km(p, c.enlevement) * 1000 <= rayon_m]

    async def statistiques(self):
        par_etat: dict[str, int] = {}
        att, liv, ca = [], [], 0
        for c in self._commandes.values():
            par_etat[str(c.etat)] = par_etat.get(str(c.etat), 0) + 1
            if c.attribuee_a:
                att.append((c.attribuee_a - c.cree_a).total_seconds())
            if c.livree_a:
                liv.append((c.livree_a - c.cree_a).total_seconds())
            if c.etat == Etat.CLOTUREE:
                ca += c.prix
        moy = lambda v: round(sum(v) / len(v), 1) if v else None
        return {"par_etat": par_etat, "delai_moyen_attribution_s": moy(att),
                "delai_moyen_livraison_s": moy(liv), "chiffre_affaires": ca}


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None
