"""Dépôt PostgreSQL + PostGIS (§ 7.1), avec SQLAlchemy 2 en mode asynchrone (asyncpg).

Les points d'enlèvement et de livraison sont stockés deux fois : en latitude/longitude (lecture
simple) et en colonne géographique PostGIS (requêtes spatiales : commandes proches d'un point,
zones de service...). Les tables sont créées au démarrage si elles n'existent pas ; Alembic
prendra le relais pour les migrations quand le schéma se stabilisera.
"""
from __future__ import annotations

from datetime import datetime

from geoalchemy2 import Geography
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from livraison.donnees.depot import Depot
from livraison.modele.cout import Taille, Vehicule
from livraison.modele.entites import Commande, Livreur
from livraison.modele.etats import Etat
from livraison.modele.geo import Point


class Base(DeclarativeBase):
    pass


def _wkt(p: Point) -> str:
    return f"SRID=4326;POINT({p.lon} {p.lat})"


class UtilisateurT(Base):
    __tablename__ = "utilisateurs"
    id: Mapped[int] = mapped_column(primary_key=True)
    telephone: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    nom: Mapped[str] = mapped_column(String(120), default="")
    role: Mapped[str] = mapped_column(String(20))  # client | livreur | repartiteur | admin
    cree_a: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LivreurT(Base):
    __tablename__ = "livreurs"
    id: Mapped[int] = mapped_column(primary_key=True)
    nom: Mapped[str] = mapped_column(String(120))
    telephone: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    vehicule: Mapped[str] = mapped_column(String(20), default="moto")
    capacite: Mapped[int] = mapped_column(Integer, default=2)
    valide: Mapped[bool] = mapped_column(Boolean, default=False)
    suspendu: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[float] = mapped_column(Float, default=4.5)
    especes_detenues: Mapped[int] = mapped_column(Integer, default=0)
    nb_evaluations: Mapped[int] = mapped_column(Integer, default=0)


class CommandeT(Base):
    __tablename__ = "commandes"
    id: Mapped[int] = mapped_column(primary_key=True)
    client: Mapped[str] = mapped_column(String(20), index=True)
    enl_lat: Mapped[float] = mapped_column(Float)
    enl_lon: Mapped[float] = mapped_column(Float)
    liv_lat: Mapped[float] = mapped_column(Float)
    liv_lon: Mapped[float] = mapped_column(Float)
    geo_enlevement = mapped_column(Geography("POINT", srid=4326), nullable=True)
    geo_livraison = mapped_column(Geography("POINT", srid=4326), nullable=True)
    adresse_enlevement: Mapped[str] = mapped_column(Text, default="")
    adresse_livraison: Mapped[str] = mapped_column(Text, default="")
    destinataire: Mapped[str] = mapped_column(String(120), default="")
    telephone_destinataire: Mapped[str] = mapped_column(String(20), default="")
    taille: Mapped[str] = mapped_column(String(10), default="petit")
    urgent: Mapped[bool] = mapped_column(Boolean, default=False)
    consignes: Mapped[str] = mapped_column(Text, default="")
    mode_paiement: Mapped[str] = mapped_column(String(20), default="especes")
    prix: Mapped[int] = mapped_column(Integer, default=0)
    distance_km: Mapped[float] = mapped_column(Float, default=0)
    etat: Mapped[str] = mapped_column(String(20), index=True)
    livreur_id: Mapped[int | None] = mapped_column(ForeignKey("livreurs.id"), nullable=True, index=True)
    code: Mapped[str] = mapped_column(String(8))
    tentatives: Mapped[int] = mapped_column(Integer, default=0)
    motif: Mapped[str] = mapped_column(Text, default="")
    cree_a: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attribuee_a: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    enlevee_a: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    livree_a: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    programmee_pour: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note_client: Mapped[int | None] = mapped_column(Integer, nullable=True)
    commentaire_client: Mapped[str] = mapped_column(Text, default="")


class FactureT(Base):
    """Factures (F-53) : numérotation continue, jamais modifiées (NF-06)."""

    __tablename__ = "factures"
    id: Mapped[int] = mapped_column(primary_key=True)
    numero: Mapped[str] = mapped_column(String(30), unique=True)
    commande_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    livreur_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    donnees = mapped_column(JSON().with_variant(JSONB(), "postgresql"))


class OffreT(Base):
    __tablename__ = "offres"
    id: Mapped[int] = mapped_column(primary_key=True)
    commande_id: Mapped[int] = mapped_column(Integer, index=True)
    livreur_id: Mapped[int] = mapped_column(Integer)
    cout: Mapped[float] = mapped_column(Float)
    eta_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    resultat: Mapped[str] = mapped_column(String(40))
    tentative: Mapped[int] = mapped_column(Integer)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PaiementT(Base):
    __tablename__ = "paiements"
    id: Mapped[int] = mapped_column(primary_key=True)
    commande_id: Mapped[int] = mapped_column(Integer, index=True)
    mode: Mapped[str] = mapped_column(String(20))
    montant: Mapped[int] = mapped_column(Integer)
    reference: Mapped[str] = mapped_column(String(60))
    etat: Mapped[str] = mapped_column(String(30))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EvenementT(Base):
    """Journal (NF-09) : messages entre agents, changements d'état, alertes. Jamais modifié."""

    __tablename__ = "evenements"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[str] = mapped_column(String(40))
    categorie: Mapped[str] = mapped_column(String(20), index=True)
    commande_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    donnees = mapped_column(JSON().with_variant(JSONB(), "postgresql"))


def _vers_commande(r: CommandeT) -> Commande:
    c = Commande(
        id=r.id, client=r.client, enlevement=Point(r.enl_lat, r.enl_lon),
        livraison=Point(r.liv_lat, r.liv_lon), adresse_enlevement=r.adresse_enlevement,
        adresse_livraison=r.adresse_livraison, destinataire=r.destinataire,
        telephone_destinataire=r.telephone_destinataire, taille=Taille(r.taille), urgent=r.urgent,
        consignes=r.consignes, mode_paiement=r.mode_paiement, prix=r.prix,
        distance_km=r.distance_km, etat=Etat(r.etat), livreur_id=r.livreur_id,
        tentatives=r.tentatives, motif=r.motif, cree_a=r.cree_a, attribuee_a=r.attribuee_a,
        enlevee_a=r.enlevee_a, livree_a=r.livree_a, programmee_pour=r.programmee_pour,
        note_client=r.note_client, commentaire_client=r.commentaire_client or "")
    c.code = r.code
    return c


def _vers_livreur(r: LivreurT) -> Livreur:
    return Livreur(r.id, r.nom, r.telephone, Vehicule(r.vehicule), r.capacite, r.valide, r.note,
                   r.suspendu, r.especes_detenues, r.nb_evaluations or 0)


class DepotSQL(Depot):
    def __init__(self, dsn: str):
        url = dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
        self.moteur = create_async_engine(url, pool_size=10, max_overflow=20)
        self.session: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.moteur, expire_on_commit=False)

    async def initialiser(self) -> None:
        async with self.moteur.begin() as cx:
            await cx.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
            await cx.run_sync(Base.metadata.create_all)
            # Migration légère : colonnes ajoutées en version 0.3 (bases créées en 0.2).
            for table, colonne, type_ in (
                    ("commandes", "programmee_pour", "TIMESTAMPTZ"),
                    ("commandes", "note_client", "INTEGER"),
                    ("commandes", "commentaire_client", "TEXT DEFAULT ''"),
                    ("livreurs", "nb_evaluations", "INTEGER DEFAULT 0")):
                await cx.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {colonne} {type_}"))

    async def fermer(self) -> None:
        await self.moteur.dispose()

    # --- utilisateurs
    async def utilisateur_par_telephone(self, telephone):
        async with self.session() as s:
            u = (await s.execute(select(UtilisateurT).where(UtilisateurT.telephone == telephone))).scalar()
            return {"id": u.id, "telephone": u.telephone, "nom": u.nom, "role": u.role} if u else None

    async def creer_utilisateur(self, telephone, nom, role):
        async with self.session.begin() as s:
            u = UtilisateurT(telephone=telephone, nom=nom, role=role)
            s.add(u)
            await s.flush()
            return {"id": u.id, "telephone": telephone, "nom": nom, "role": role}

    # --- livreurs
    async def creer_livreur(self, livreur):
        async with self.session.begin() as s:
            r = LivreurT(nom=livreur.nom, telephone=livreur.telephone, vehicule=str(livreur.vehicule),
                         capacite=livreur.capacite, valide=livreur.valide, note=livreur.note,
                         suspendu=livreur.suspendu, especes_detenues=livreur.especes_detenues)
            if livreur.id:
                r.id = livreur.id
            s.add(r)
            await s.flush()
            livreur.id = r.id
        return livreur

    async def livreur(self, livreur_id):
        async with self.session() as s:
            r = await s.get(LivreurT, livreur_id)
            return _vers_livreur(r) if r else None

    async def livreurs(self):
        async with self.session() as s:
            return [_vers_livreur(r) for r in (await s.execute(select(LivreurT).order_by(LivreurT.id))).scalars()]

    async def maj_livreur(self, livreur_id, **champs):
        async with self.session.begin() as s:
            r = await s.get(LivreurT, livreur_id)
            for k, v in champs.items():
                setattr(r, k, str(v) if k == "vehicule" else v)

    # --- commandes
    def _remplir(self, r: CommandeT, c: Commande) -> None:
        r.client, r.enl_lat, r.enl_lon = c.client, c.enlevement.lat, c.enlevement.lon
        r.liv_lat, r.liv_lon = c.livraison.lat, c.livraison.lon
        r.geo_enlevement, r.geo_livraison = _wkt(c.enlevement), _wkt(c.livraison)
        for k in ("adresse_enlevement", "adresse_livraison", "destinataire", "telephone_destinataire",
                  "urgent", "consignes", "mode_paiement", "prix", "distance_km", "livreur_id", "code",
                  "tentatives", "motif", "cree_a", "attribuee_a", "enlevee_a", "livree_a",
                  "programmee_pour", "note_client", "commentaire_client"):
            setattr(r, k, getattr(c, k))
        r.taille, r.etat = str(c.taille), str(c.etat)

    async def creer_commande(self, commande):
        async with self.session.begin() as s:
            r = CommandeT()
            self._remplir(r, commande)
            s.add(r)
            await s.flush()
            commande.id = r.id
        return commande

    async def commande(self, commande_id):
        async with self.session() as s:
            r = await s.get(CommandeT, commande_id)
            return _vers_commande(r) if r else None

    async def commandes(self, etats=None, client=None, livreur_id=None, limite=500, decalage=0):
        q = select(CommandeT).order_by(CommandeT.id.desc()).offset(decalage).limit(limite)
        if etats:
            q = q.where(CommandeT.etat.in_([str(e) for e in etats]))
        if client:
            q = q.where(CommandeT.client == client)
        if livreur_id is not None:
            q = q.where(CommandeT.livreur_id == livreur_id)
        async with self.session() as s:
            return [_vers_commande(r) for r in (await s.execute(q)).scalars()]

    async def commandes_proches(self, p: Point, rayon_m: float, etats=None) -> list[Commande]:
        """Requête spatiale PostGIS : commandes dont l'enlèvement est à moins de rayon_m mètres."""
        cible = func.ST_GeogFromText(_wkt(p))
        q = select(CommandeT).where(func.ST_DWithin(CommandeT.geo_enlevement, cible, float(rayon_m)))
        if etats:
            q = q.where(CommandeT.etat.in_([str(e) for e in etats]))
        async with self.session() as s:
            return [_vers_commande(r) for r in (await s.execute(q)).scalars()]

    async def maj_commande(self, commande):
        async with self.session.begin() as s:
            r = await s.get(CommandeT, commande.id)
            self._remplir(r, commande)

    # --- offres, paiements, journal
    async def noter_facture(self, facture):
        async with self.session.begin() as s:
            s.add(FactureT(numero=facture["numero"], commande_id=facture["commande_id"],
                           livreur_id=facture.get("livreur_id"), donnees=facture))
        return facture

    async def facture(self, commande_id):
        async with self.session() as s:
            r = (await s.execute(select(FactureT).where(FactureT.commande_id == commande_id))).scalar()
            return r.donnees if r else None

    async def factures(self, livreur_id=None):
        q = select(FactureT).order_by(FactureT.id)
        if livreur_id is not None:
            q = q.where(FactureT.livreur_id == livreur_id)
        async with self.session() as s:
            return [r.donnees for r in (await s.execute(q)).scalars()]

    async def nombre_factures(self):
        async with self.session() as s:
            return (await s.execute(select(func.count()).select_from(FactureT))).scalar_one()

    async def noter_offre(self, commande_id, livreur_id, cout, eta_s, resultat, tentative):
        async with self.session.begin() as s:
            s.add(OffreT(commande_id=commande_id, livreur_id=livreur_id, cout=cout, eta_s=eta_s,
                         resultat=resultat, tentative=tentative))

    async def offres(self, commande_id: int) -> list[dict]:
        async with self.session() as s:
            rows = (await s.execute(select(OffreT).where(OffreT.commande_id == commande_id)
                                    .order_by(OffreT.id))).scalars()
            return [{"livreur_id": o.livreur_id, "cout": o.cout, "eta_s": o.eta_s, "resultat": o.resultat,
                         "tentative": o.tentative, "ts": o.ts.isoformat()} for o in rows]

    async def noter_paiement(self, commande_id, mode, montant, reference, etat):
        async with self.session.begin() as s:
            s.add(PaiementT(commande_id=commande_id, mode=mode, montant=montant, reference=reference,
                            etat=etat))

    async def paiements(self, commande_id=None):
        q = select(PaiementT).order_by(PaiementT.id)
        if commande_id is not None:
            q = q.where(PaiementT.commande_id == commande_id)
        async with self.session() as s:
            return [{"commande_id": p.commande_id, "mode": p.mode, "montant": p.montant,
                         "reference": p.reference, "etat": p.etat, "ts": p.ts.isoformat()}
                    for p in (await s.execute(q)).scalars()]

    async def noter_evenement(self, evt):
        async with self.session.begin() as s:
            s.add(EvenementT(ts=evt["ts"], categorie=evt["categorie"],
                             commande_id=evt.get("commande_id"), donnees=evt))

    async def evenements(self, commande_id=None, limite=500):
        q = select(EvenementT).order_by(EvenementT.id.desc()).limit(limite)
        if commande_id is not None:
            q = q.where(EvenementT.commande_id == commande_id)
        async with self.session() as s:
            return [e.donnees for e in (await s.execute(q)).scalars()][::-1]

    async def statistiques(self) -> dict:
        """Indicateurs du tableau de bord (F-64)."""
        async with self.session() as s:
            par_etat = dict((await s.execute(
                select(CommandeT.etat, func.count()).group_by(CommandeT.etat))).all())
            delais = (await s.execute(select(
                func.avg(func.extract("epoch", CommandeT.attribuee_a - CommandeT.cree_a)),
                func.avg(func.extract("epoch", CommandeT.livree_a - CommandeT.cree_a)),
                func.coalesce(func.sum(CommandeT.prix).filter(CommandeT.etat == "cloturee"), 0),
            ))).one()
        return {"par_etat": par_etat,
                "delai_moyen_attribution_s": round(delais[0], 1) if delais[0] else None,
                "delai_moyen_livraison_s": round(delais[1], 1) if delais[1] else None,
                "chiffre_affaires": int(delais[2])}
