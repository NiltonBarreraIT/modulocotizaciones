from decimal import Decimal
from html import escape
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .formatting import format_local_date

NAVY = colors.HexColor("#111F39")
BLUE = colors.HexColor("#1754AA")
LIGHT_BLUE = colors.HexColor("#E8EEF7")
MUTED = colors.HexColor("#60718A")


def _amount(value, currency):
    number = Decimal(str(value or "0"))
    decimals = 0 if currency == "CLP" else 2
    rendered = f"{number:,.{decimals}f}"
    rendered = rendered.replace(",", "_").replace(".", ",").replace("_", ".")
    return f"{currency} {rendered}"


def _text(value):
    return escape(str(value or "-"))


def build_quote_pdf(quote, commercial, items):
    output = BytesIO()
    document = SimpleDocTemplate(
        output,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=28 * mm,
        bottomMargin=20 * mm,
        title=f"Cotización {quote['number']}",
        author="ITS",
    )
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="QuoteTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=20,
            leading=23,
            textColor=NAVY,
            spaceAfter=5 * mm,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Section",
            parent=styles["Heading2"],
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=14,
            textColor=NAVY,
            spaceBefore=5 * mm,
            spaceAfter=2 * mm,
        )
    )
    styles.add(
        ParagraphStyle(
            name="Small",
            parent=styles["BodyText"],
            fontSize=8.5,
            leading=11,
            textColor=NAVY,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SmallRight",
            parent=styles["Small"],
            alignment=TA_RIGHT,
        )
    )

    currency = commercial["currency"]
    data = quote["d"]
    story = [
        Paragraph(f"Cotización {_text(quote['number'])}", styles["QuoteTitle"]),
        Table(
            [
                [
                    Paragraph("<b>Cliente</b>", styles["Small"]),
                    Paragraph(_text(data.get("cliente")), styles["Small"]),
                    Paragraph("<b>Versión</b>", styles["Small"]),
                    Paragraph(_text(quote["version"]), styles["Small"]),
                ],
                [
                    Paragraph("<b>Fecha</b>", styles["Small"]),
                    Paragraph(
                        _text(
                            format_local_date(
                                data.get("fecha") or data.get("solicitud")
                            )
                        ),
                        styles["Small"],
                    ),
                    Paragraph("<b>Flujo</b>", styles["Small"]),
                    Paragraph(_text(quote["workflow_status"]), styles["Small"]),
                ],
                [
                    Paragraph("<b>Vendedor</b>", styles["Small"]),
                    Paragraph(_text(data.get("vendedor")), styles["Small"]),
                    Paragraph("<b>Vigencia</b>", styles["Small"]),
                    Paragraph(f"{commercial['validity_days']} días", styles["Small"]),
                ],
                [
                    Paragraph("<b>RFQ</b>", styles["Small"]),
                    Paragraph(_text(data.get("rfq")), styles["Small"]),
                    Paragraph("<b>Moneda</b>", styles["Small"]),
                    Paragraph(currency, styles["Small"]),
                ],
            ],
            colWidths=[24 * mm, 72 * mm, 22 * mm, 46 * mm],
            style=TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                    ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#D8E0EB")),
                    ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#E7EAF0")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            ),
        ),
        Paragraph("Descripción", styles["Section"]),
        Paragraph(_text(data.get("descripcion")), styles["BodyText"]),
        Paragraph("Productos y servicios", styles["Section"]),
    ]

    item_rows = [["#", "Descripción", "Cantidad", "Precio unitario", "Desc.", "Total"]]
    for item in items:
        item_rows.append(
            [
                str(item["position"]),
                Paragraph(
                    f"<b>{_text(item['kind']).title()}</b><br/>{_text(item['description'])}",
                    styles["Small"],
                ),
                item["quantity"],
                _amount(item["unit_price"], currency),
                f"{Decimal(item['discount_percent']):.2f}%",
                _amount(item["line_total"], currency),
            ]
        )
    if len(item_rows) == 1:
        item_rows.append(["-", "Sin líneas comerciales", "-", "-", "-", "-"])
    item_table = Table(
        item_rows,
        repeatRows=1,
        colWidths=[8 * mm, 67 * mm, 18 * mm, 30 * mm, 17 * mm, 32 * mm],
        style=TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#D8E0EB")),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [colors.white, colors.HexColor("#F7F9FC")],
                ),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        ),
    )
    story.extend([item_table, Spacer(1, 5 * mm)])

    totals = [
        ["Subtotal", _amount(commercial["subtotal"], currency)],
        [
            f"Descuento global ({Decimal(commercial['global_discount']):.2f}%)",
            f"- {_amount(commercial['discount_total'], currency)}",
        ],
        ["TOTAL NETO", _amount(commercial["total"], currency)],
    ]
    totals_table = Table(
        totals,
        colWidths=[55 * mm, 42 * mm],
        hAlign="RIGHT",
        style=TableStyle(
            [
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BACKGROUND", (0, -1), (-1, -1), LIGHT_BLUE),
                ("LINEABOVE", (0, -1), (-1, -1), 1, BLUE),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        ),
    )
    story.append(totals_table)

    terms = []
    if commercial.get("payment_terms"):
        terms.extend(
            [
                Paragraph("Condiciones de pago", styles["Section"]),
                Paragraph(_text(commercial["payment_terms"]), styles["BodyText"]),
            ]
        )
    if commercial.get("notes"):
        terms.extend(
            [
                Paragraph("Notas", styles["Section"]),
                Paragraph(_text(commercial["notes"]), styles["BodyText"]),
            ]
        )
    if terms:
        story.append(KeepTogether(terms))

    workflow = quote["workflow_status"]

    def decorate_page(canvas, doc):
        canvas.saveState()
        width, height = A4
        canvas.setFillColor(NAVY)
        canvas.rect(0, height - 18 * mm, width, 18 * mm, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 15)
        canvas.drawString(18 * mm, height - 11 * mm, "ITS")
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(
            width - 18 * mm, height - 10.5 * mm, "Gestión de Cotizaciones"
        )
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 7.5)
        canvas.drawString(
            18 * mm, 10 * mm, f"{quote['number']} - versión {quote['version']}"
        )
        canvas.drawRightString(width - 18 * mm, 10 * mm, f"Página {doc.page}")
        if workflow not in ("aprobada", "enviada"):
            canvas.saveState()
            canvas.setFillColor(colors.HexColor("#D99B9B"))
            canvas.setFont("Helvetica-Bold", 18)
            canvas.translate(width - 7 * mm, height / 2)
            canvas.rotate(90)
            canvas.drawCentredString(0, 0, workflow.upper().replace("_", " "))
            canvas.restoreState()
        canvas.restoreState()

    document.build(story, onFirstPage=decorate_page, onLaterPages=decorate_page)
    output.seek(0)
    return output
