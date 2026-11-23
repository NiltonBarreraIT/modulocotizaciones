from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree

from .commercial_service import format_amount
from .deadlines import local_today

WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
XML_NAMESPACE = "http://www.w3.org/XML/1998/namespace"
NAMESPACES = {"w": WORD_NAMESPACE}


def _qn(name):
    prefix, local_name = name.split(":", 1)
    namespace = WORD_NAMESPACE if prefix == "w" else XML_NAMESPACE
    return f"{{{namespace}}}{local_name}"


MONTHS = (
    "Enero",
    "Febrero",
    "Marzo",
    "Abril",
    "Mayo",
    "Junio",
    "Julio",
    "Agosto",
    "Septiembre",
    "Octubre",
    "Noviembre",
    "Diciembre",
)
CURRENCY_NAMES = {
    "CLP": "Peso Chileno",
    "USD": "Dólar Americano",
    "EUR": "Euro",
}


def _spanish_date(value):
    if isinstance(value, str):
        value = date.fromisoformat(value)
    return f"{value.day} de {MONTHS[value.month - 1]} {value.year}"


def _plain_number(value):
    number = Decimal(str(value))
    if number == number.to_integral():
        return str(int(number))
    return format(number.normalize(), "f").replace(".", ",")


def _money(value, currency):
    return f"{currency} ${format_amount(value, currency)}"


def _table_money(value, currency):
    return f"${format_amount(value, currency)}"


def _paragraph_text(element):
    return "".join(node.text or "" for node in element.iter(_qn("w:t")))


def _set_xml_paragraph(element, value):
    text_nodes = list(element.iter(_qn("w:t")))
    if not text_nodes:
        run = etree.SubElement(element, _qn("w:r"))
        text_nodes = [etree.SubElement(run, _qn("w:t"))]
    text_nodes[0].text = value
    if value.startswith(" ") or value.endswith(" "):
        text_nodes[0].set(_qn("xml:space"), "preserve")
    for node in text_nodes[1:]:
        node.text = ""


def _replace_paragraph(root, predicate, value):
    for paragraph in root.iter(_qn("w:p")):
        if predicate(_paragraph_text(paragraph)):
            _set_xml_paragraph(paragraph, value)
            return True
    return False


def _update_conditions_paragraph(root, currency, validity_days):
    for paragraph in root.iter(_qn("w:p")):
        full_text = " ".join(_paragraph_text(paragraph).split())
        if not (
            full_text.startswith("Los valores indicados")
            and "validez de la oferta" in full_text
        ):
            continue
        for node in paragraph.iter(_qn("w:t")):
            if node.text == "USD":
                node.text = currency
            elif node.text == "Dólar Americano":
                node.text = CURRENCY_NAMES[currency]
            elif node.text and "validez de la oferta es de" in node.text:
                node.text = f"validez de la oferta es de {validity_days} días"
        return


def _properties(element, tag):
    properties = element.find(_qn(tag))
    if properties is None:
        properties = etree.Element(_qn(tag))
        element.insert(0, properties)
    return properties


def _new_run(text, *, bold=False, size=8):
    run = etree.Element(_qn("w:r"))
    properties = etree.SubElement(run, _qn("w:rPr"))
    fonts = etree.SubElement(properties, _qn("w:rFonts"))
    fonts.set(_qn("w:ascii"), "Montserrat")
    fonts.set(_qn("w:hAnsi"), "Montserrat")
    if bold:
        etree.SubElement(properties, _qn("w:b"))
    size_element = etree.SubElement(properties, _qn("w:sz"))
    size_element.set(_qn("w:val"), str(size * 2))
    text_element = etree.SubElement(run, _qn("w:t"))
    text_element.text = text
    if text.startswith(" ") or text.endswith(" "):
        text_element.set(_qn("xml:space"), "preserve")
    return run


def _clear_paragraph(paragraph, alignment=None):
    for child in list(paragraph):
        if child.tag != _qn("w:pPr"):
            paragraph.remove(child)
    if alignment:
        properties = _properties(paragraph, "w:pPr")
        justify = properties.find(_qn("w:jc"))
        if justify is None:
            justify = etree.SubElement(properties, _qn("w:jc"))
        justify.set(_qn("w:val"), alignment)


def _set_cell_text(cell, text, *, bold=False, alignment=None, size=8, no_wrap=False):
    paragraphs = cell.findall(_qn("w:p"))
    paragraph = paragraphs[0] if paragraphs else etree.SubElement(cell, _qn("w:p"))
    for extra in paragraphs[1:]:
        cell.remove(extra)
    _clear_paragraph(paragraph, alignment)
    paragraph.append(_new_run(text, bold=bold, size=size))
    properties = _properties(cell, "w:tcPr")
    vertical = properties.find(_qn("w:vAlign"))
    if vertical is None:
        vertical = etree.SubElement(properties, _qn("w:vAlign"))
    vertical.set(_qn("w:val"), "center")
    no_wrap_element = properties.find(_qn("w:noWrap"))
    if no_wrap and no_wrap_element is None:
        etree.SubElement(properties, _qn("w:noWrap"))


def _set_description_cell(cell, item):
    _set_cell_text(
        cell,
        f"{item['kind'].title()} - {item['description']}",
        bold=True,
    )
    for raw_line in item.get("details", "").splitlines():
        detail = raw_line.strip().lstrip("•- ").strip()
        if not detail:
            continue
        paragraph = etree.SubElement(cell, _qn("w:p"))
        properties = etree.SubElement(paragraph, _qn("w:pPr"))
        style = etree.SubElement(properties, _qn("w:pStyle"))
        style.set(_qn("w:val"), "TableParagraph")
        indent = etree.SubElement(properties, _qn("w:ind"))
        indent.set(_qn("w:left"), "160")
        indent.set(_qn("w:hanging"), "120")
        paragraph.append(_new_run(f"• {detail}", size=7))
    if Decimal(item["discount_percent"]):
        paragraph = etree.SubElement(cell, _qn("w:p"))
        properties = etree.SubElement(paragraph, _qn("w:pPr"))
        style = etree.SubElement(properties, _qn("w:pStyle"))
        style.set(_qn("w:val"), "TableParagraph")
        spacing = etree.SubElement(properties, _qn("w:spacing"))
        spacing.set(_qn("w:before"), "120")
        paragraph.append(
            _new_run(f"Descuento de línea: {_plain_number(item['discount_percent'])}%")
        )


def _insert_notes_after_table(table, notes):
    if not notes:
        return
    paragraph = etree.Element(_qn("w:p"))
    properties = etree.SubElement(paragraph, _qn("w:pPr"))
    spacing = etree.SubElement(properties, _qn("w:spacing"))
    spacing.set(_qn("w:before"), "160")
    spacing.set(_qn("w:after"), "120")
    paragraph.append(_new_run("Notas para la cotización", bold=True, size=9))
    line_break = etree.SubElement(paragraph, _qn("w:r"))
    etree.SubElement(line_break, _qn("w:br"))
    for index, line in enumerate(notes.splitlines() or [notes]):
        if index:
            extra_break = etree.SubElement(paragraph, _qn("w:r"))
            etree.SubElement(extra_break, _qn("w:br"))
        paragraph.append(_new_run(line, size=9))
    table.addnext(paragraph)


def _remove_fixed_height(row):
    properties = row.find(_qn("w:trPr"))
    if properties is None:
        return
    for height in properties.findall(_qn("w:trHeight")):
        properties.remove(height)


def _repeat_table_header(row):
    properties = _properties(row, "w:trPr")
    if properties.find(_qn("w:tblHeader")) is None:
        etree.SubElement(properties, _qn("w:tblHeader"))


def _row_cells(row):
    return row.findall(_qn("w:tc"))


def _fill_commercial_table(root, commercial, items):
    table = next(
        candidate
        for candidate in root.findall(".//w:tbl", NAMESPACES)
        if "Qty" in _paragraph_text(candidate)
        and "Valor unitario" in _paragraph_text(candidate)
    )
    rows = table.findall(_qn("w:tr"))
    header_row, sample_row, summary_row = rows[:3]
    _repeat_table_header(header_row)
    sample_xml = deepcopy(sample_row)
    summary_xml = deepcopy(summary_row)
    table.remove(sample_row)

    for position, item in enumerate(items, start=1):
        row = deepcopy(sample_xml)
        summary_row.addprevious(row)
        _remove_fixed_height(row)
        cells = _row_cells(row)
        _set_cell_text(cells[0], f"{position:02d}", bold=True, alignment="center")
        _set_cell_text(
            cells[1],
            _plain_number(item["quantity"]),
            bold=True,
            alignment="center",
        )
        _set_description_cell(cells[2], item)
        _set_cell_text(
            cells[3],
            _table_money(item["unit_price"], commercial["currency"]),
            alignment="right",
            size=8,
            no_wrap=True,
        )
        _set_cell_text(
            cells[4],
            _table_money(item["line_total"], commercial["currency"]),
            alignment="right",
            size=8,
            no_wrap=True,
        )

    net_total = Decimal(commercial["subtotal"]) - Decimal(commercial["discount_total"])
    summaries = [("Total Neto", net_total)]
    summary_rows = [summary_row]
    for _ in summaries[1:]:
        row = deepcopy(summary_xml)
        summary_rows[-1].addnext(row)
        summary_rows.append(row)
    for row, (label, value) in zip(summary_rows, summaries, strict=True):
        _remove_fixed_height(row)
        cells = _row_cells(row)
        for cell in cells[:3]:
            _set_cell_text(cell, "")
        _set_cell_text(cells[3], label, bold=True, size=8)
        _set_cell_text(
            cells[4],
            _table_money(value, commercial["currency"]),
            bold=True,
            alignment="right",
            size=8,
            no_wrap=True,
        )

    _insert_notes_after_table(table, commercial.get("notes", ""))

    return net_total


def _document_xml(quote, commercial, items, source_xml):
    root = etree.fromstring(source_xml)
    data = quote["d"]
    currency = commercial["currency"]
    issued_on = local_today()
    valid_until = issued_on + timedelta(days=commercial["validity_days"])
    net_total = _fill_commercial_table(root, commercial, items)
    seller = quote.get("owner_name") or data.get("vendedor", "") or "Por definir"
    seller_email = quote.get("owner_email") or (
        data.get("vendedor", "") if "@" in data.get("vendedor", "") else ""
    )
    seller_phone = quote.get("owner_phone", "")
    estimator = quote.get("estimator_name") or "Por definir"
    description = data.get("descripcion", "") or "Por definir"
    supply_scope = commercial.get("supply_scope", "") or description
    supply_scope = " ".join(supply_scope.splitlines())
    payment = commercial.get("payment_terms", "") or "Por definir"
    delivery_time = commercial.get("delivery_time", "") or "Por confirmar"

    replacements = [
        (
            lambda text: "REFERENCIA DE COTIZACIÓN NO." in text,
            f"REFERENCIA DE COTIZACIÓN NO. {quote['number']}",
        ),
        (lambda text: text.startswith("SQM S.A."), data.get("cliente", "")),
        (
            lambda text: text.startswith("Atención a:"),
            f"Atención a: {data.get('cliente', '')}",
        ),
        (lambda text: text == "26 de Octubre 2026", _spanish_date(issued_on)),
        (lambda text: text == "26 de Noviembre 2026", _spanish_date(valid_until)),
        (
            lambda text: text == "Instrumentación",
            data.get("area", "") or "Por definir",
        ),
        (lambda text: text == "Rev.0", f"Rev.{max(quote['version'] - 1, 0)}"),
        (lambda text: text == "Emilio Castillo", estimator),
        (lambda text: text == "Cesar Iglesias", seller),
        (
            lambda text: text == "cesar.iglesias@i-tsolutions.cl",
            seller_email,
        ),
        (lambda text: text == "+56 9 5741 5260", seller_phone),
        (lambda text: text == "FLUJÓMETRO ELECTROMAGNÉTICO DN25", supply_scope),
        (lambda text: text.strip() == "4 Semanas", delivery_time),
        (lambda text: text == "USD $18.420", _money(net_total, currency)),
        (lambda text: text == "Pago a 30 días contra entrega.", payment),
        (lambda text: text == "En bodega consolidación", "Por definir"),
    ]
    for predicate, value in replacements:
        _replace_paragraph(root, predicate, value)
    _update_conditions_paragraph(root, currency, commercial["validity_days"])
    return etree.tostring(
        root,
        xml_declaration=True,
        encoding="UTF-8",
        standalone=True,
    )


def build_offer_docx(quote, commercial, items, template_path):
    output = BytesIO()
    with ZipFile(template_path, "r") as source:
        document_xml = _document_xml(
            quote, commercial, items, source.read("word/document.xml")
        )
        with ZipFile(output, "w", compression=ZIP_DEFLATED) as destination:
            for entry in source.infolist():
                content = (
                    document_xml
                    if entry.filename == "word/document.xml"
                    else source.read(entry.filename)
                )
                destination.writestr(entry, content)
    output.seek(0)
    return output
