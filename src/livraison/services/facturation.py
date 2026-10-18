"""Facturation (F-53) et rémunération des livreurs (F-54).

À la clôture d'une commande, l'agent Paiement émet une facture numérotée sans trou
(FAC-2026-000001, FAC-2026-000002...) et calcule la part du livreur et la commission de la
plateforme. Le PDF est produit à la demande à partir des données enregistrées, qui ne sont
jamais modifiées (NF-06 : opérations financières tracées).

Hypothèses à faire valider (cahier des charges § 10.2, modèle de rémunération) :
prix affichés TTC, TVA de 18 % ; commission de la plateforme de 20 % du prix.
"""
from __future__ import annotations

import io
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

TAUX_TVA = 0.18
TAUX_COMMISSION = 0.20


@dataclass
class Facture:
    numero: str
    commande_id: int
    emise_a: str
    client: str
    livreur_id: int | None
    montant_ttc: int
    montant_ht: int
    tva: int
    commission: int
    gain_livreur: int
    mode_paiement: str

    def vers_dict(self) -> dict:
        return asdict(self)


def numero_facture(rang: int, annee: int | None = None) -> str:
    return f"FAC-{annee or datetime.now(UTC).year}-{rang:06d}"


def calculer_facture(commande, rang: int) -> Facture:
    ttc = int(commande.prix)
    ht = round(ttc / (1 + TAUX_TVA))
    commission = round(ttc * TAUX_COMMISSION)
    return Facture(
        numero=numero_facture(rang), commande_id=commande.id,
        emise_a=datetime.now(UTC).isoformat(), client=commande.client,
        livreur_id=commande.livreur_id, montant_ttc=ttc, montant_ht=ht, tva=ttc - ht,
        commission=commission, gain_livreur=ttc - commission, mode_paiement=commande.mode_paiement)


def fcfa(v: int) -> str:
    return f"{v:,}".replace(",", " ") + " FCFA"


def facture_pdf(f: dict, commande) -> bytes:
    """Facture au format PDF (A4), générée avec reportlab."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    tampon = io.BytesIO()
    doc = SimpleDocTemplate(tampon, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm,
                            topMargin=2 * cm, title=f"Facture {f['numero']}")
    st = getSampleStyleSheet()
    vert = colors.HexColor("#0D7A74")
    emise = datetime.fromisoformat(f["emise_a"]).strftime("%d/%m/%Y %H:%M")
    livree = commande.livree_a.strftime("%d/%m/%Y %H:%M") if commande.livree_a else "—"
    elements = [
        Paragraph("<b>Livraison SMA</b> — Abidjan", st["Title"]),
        Paragraph(f"<b>Facture n° {f['numero']}</b><br/>Émise le {emise}", st["Normal"]),
        Spacer(1, 0.6 * cm),
        Table([["Client", f["client"]], ["Commande", f"n° {commande.id}"],
               ["Enlèvement", commande.adresse_enlevement or
                f"{commande.enlevement.lat:.5f}, {commande.enlevement.lon:.5f}"],
               ["Livraison", commande.adresse_livraison or
                f"{commande.livraison.lat:.5f}, {commande.livraison.lon:.5f}"],
               ["Destinataire", commande.destinataire or "—"],
               ["Livrée le", livree], ["Livreur", f"n° {f['livreur_id']}"],
               ["Paiement", {"especes": "espèces à la livraison", "mobile_money": "mobile money",
                             "carte": "carte bancaire"}.get(f["mode_paiement"], f["mode_paiement"])]],
              colWidths=[4 * cm, 12 * cm],
              style=TableStyle([("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 4)])),
        Spacer(1, 0.6 * cm),
        Table([["Désignation", "Montant"],
               [f"Livraison urbaine, {commande.distance_km} km, colis {commande.taille}"
                + (", urgent" if commande.urgent else ""), fcfa(f["montant_ht"])],
               ["TVA 18 %", fcfa(f["tva"])],
               ["Total TTC", fcfa(f["montant_ttc"])]],
              colWidths=[12 * cm, 4 * cm],
              style=TableStyle([("BACKGROUND", (0, 0), (-1, 0), vert),
                                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                                ("LINEABOVE", (0, -1), (-1, -1), 1, vert),
                                ("GRID", (0, 0), (-1, -2), 0.25, colors.lightgrey)])),
        Spacer(1, 1 * cm),
        Paragraph("Document généré automatiquement par la plateforme Livraison SMA "
                  "(projet de Master 2 Systèmes multi-agents).", st["Italic"]),
    ]
    doc.build(elements)
    return tampon.getvalue()
