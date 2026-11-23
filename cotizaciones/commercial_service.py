import json
import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from itertools import zip_longest

from .db import iso

CENT = Decimal("0.01")
CURRENCIES = ("CLP", "USD", "EUR")


def _normalized_decimal(value, *, grouped=False):
    text = str(value or "0").strip().replace("\u00a0", "").replace(" ", "")
    if not text:
        return "0"
    if "," in text and "." in text:
        decimal_separator = "," if text.rfind(",") > text.rfind(".") else "."
        grouping_separator = "." if decimal_separator == "," else ","
        return text.replace(grouping_separator, "").replace(decimal_separator, ".")
    if grouped and re.fullmatch(r"[+-]?\d{1,3}([.,]\d{3})+", text):
        return text.replace(".", "").replace(",", "")
    if text.count(",") == 1:
        return text.replace(",", ".")
    if text.count(",") > 1:
        return text.replace(",", "")
    return text


def _decimal(
    value,
    label,
    minimum=Decimal("0"),
    maximum=None,
    positive=False,
    grouped=False,
):
    try:
        number = Decimal(_normalized_decimal(value, grouped=grouped))
    except InvalidOperation as error:
        raise ValueError(f"Valor inválido: {label}") from error
    if not number.is_finite() or number < minimum:
        raise ValueError(f"Valor inválido: {label}")
    if positive and number <= 0:
        raise ValueError(f"{label} debe ser mayor que cero")
    if maximum is not None and number > maximum:
        raise ValueError(f"{label} excede el máximo permitido")
    return number


def _money(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def decimal_text(value):
    return f"{_money(Decimal(value)):.2f}"


def format_amount(value, currency):
    number = Decimal(str(value or "0"))
    decimals = 0 if currency == "CLP" else 2
    rendered = f"{number:,.{decimals}f}"
    return rendered.replace(",", "_").replace(".", ",").replace("_", ".")


def default_commercial():
    return {
        "currency": "CLP",
        "global_discount": "0.00",
        "tax_percent": "0.00",
        "validity_days": 15,
        "payment_terms": "",
        "notes": "",
        "delivery_time": "",
        "supply_scope": "",
        "profit_percent": "",
        "profit_usd": "",
        "shared_user_id": None,
        "shared_percent": "",
        "technical_support_user_id": None,
        "shared_user_name": "",
        "technical_support_user_name": "",
        "subtotal": "0.00",
        "discount_total": "0.00",
        "tax_total": "0.00",
        "total": "0.00",
    }


def get_commercial(database, quote_id):
    row = database.execute(
        """SELECT quote_commercial.*,
        COALESCE(NULLIF(trim(shared_user.first_name || ' ' ||
            shared_user.last_name), ''),
            shared_user.username, '') AS shared_user_name,
        COALESCE(NULLIF(trim(technical_user.first_name || ' ' ||
            technical_user.last_name), ''),
            technical_user.username, '') AS technical_support_user_name
        FROM quote_commercial
        LEFT JOIN users AS shared_user
        ON shared_user.id=quote_commercial.shared_user_id
        LEFT JOIN users AS technical_user
        ON technical_user.id=quote_commercial.technical_support_user_id
        WHERE quote_commercial.quote_id=?""",
        (quote_id,),
    ).fetchone()
    return dict(row) if row else default_commercial()


def get_items(database, quote_id):
    return [
        dict(row)
        for row in database.execute(
            "SELECT * FROM quote_items WHERE quote_id=? ORDER BY position,id",
            (quote_id,),
        ).fetchall()
    ]


def calculate_totals(items, global_discount):
    subtotal = sum((Decimal(item["line_total"]) for item in items), Decimal("0"))
    discount_total = _money(subtotal * global_discount / Decimal("100"))
    total = subtotal - discount_total
    return {
        "subtotal": decimal_text(subtotal),
        "discount_total": decimal_text(discount_total),
        "tax_total": "0.00",
        "total": decimal_text(total),
    }


def parse_commercial_form(form):
    currency = form.get("currency", "CLP")
    if currency not in CURRENCIES:
        raise ValueError("Moneda inválida")
    global_discount = _decimal(
        form.get("global_discount"), "descuento global", maximum=Decimal("100")
    )
    try:
        validity_days = int(form.get("validity_days", "15"))
    except ValueError as error:
        raise ValueError("La vigencia debe ser un número entero") from error
    if not 1 <= validity_days <= 3650:
        raise ValueError("La vigencia debe estar entre 1 y 3650 días")
    profit_percent_value = form.get("profit_percent", "").strip()
    profit_usd_value = form.get("profit_usd", "").strip()
    profit_percent = (
        decimal_text(
            _decimal(
                profit_percent_value,
                "% utilidad",
                maximum=Decimal("100"),
            )
        )
        if profit_percent_value
        else ""
    )
    profit_usd = (
        decimal_text(_decimal(profit_usd_value, "utilidad US$", grouped=True))
        if profit_usd_value
        else ""
    )
    shared_user_value = form.get("shared_user_id", "").strip()
    technical_support_value = form.get("technical_support_user_id", "").strip()
    try:
        shared_user_id = int(shared_user_value) if shared_user_value else None
        technical_support_user_id = (
            int(technical_support_value) if technical_support_value else None
        )
    except ValueError as error:
        raise ValueError("Usuario de colaboración inválido") from error
    shared_percent_value = form.get("shared_percent", "").strip()
    if bool(shared_user_id) != bool(shared_percent_value):
        raise ValueError(
            "Negocio compartido y porcentaje compartido deben informarse juntos"
        )
    shared_percent = (
        decimal_text(
            _decimal(
                shared_percent_value,
                "porcentaje compartido",
                maximum=Decimal("100"),
                positive=True,
            )
        )
        if shared_percent_value
        else ""
    )

    lists = [
        form.getlist("item_kind"),
        form.getlist("item_description"),
        form.getlist("item_details"),
        form.getlist("item_quantity"),
        form.getlist("item_unit_price"),
        form.getlist("item_discount"),
    ]
    items = []
    for position, values in enumerate(zip_longest(*lists, fillvalue=""), start=1):
        (
            kind,
            description,
            details,
            quantity_value,
            price_value,
            discount_value,
        ) = values
        description = description.strip()
        if not description and not any(
            value.strip() for value in (quantity_value, price_value, discount_value)
        ):
            continue
        if not description:
            raise ValueError(f"La línea {position} requiere descripción")
        if kind not in ("producto", "servicio"):
            raise ValueError(f"Tipo inválido en la línea {position}")
        quantity = _decimal(
            quantity_value or "1", f"cantidad línea {position}", positive=True
        )
        unit_price = _decimal(
            price_value,
            f"precio línea {position}",
            grouped=True,
        )
        discount = _decimal(
            discount_value,
            f"descuento línea {position}",
            maximum=Decimal("100"),
        )
        gross = quantity * unit_price
        line_total = _money(gross * (Decimal("1") - discount / Decimal("100")))
        items.append(
            {
                "position": len(items) + 1,
                "kind": kind,
                "description": description[:500],
                "details": details.strip()[:4000],
                "quantity": decimal_text(quantity),
                "unit_price": decimal_text(unit_price),
                "discount_percent": decimal_text(discount),
                "line_total": decimal_text(line_total),
            }
        )
        if len(items) > 100:
            raise ValueError("La cotización no puede superar 100 líneas")

    totals = calculate_totals(items, global_discount)
    commercial = {
        "currency": currency,
        "global_discount": decimal_text(global_discount),
        "tax_percent": "0.00",
        "validity_days": validity_days,
        "payment_terms": form.get("payment_terms", "").strip()[:1000],
        "notes": form.get("notes", "").strip()[:4000],
        "delivery_time": form.get("delivery_time", "").strip()[:1000],
        "supply_scope": form.get("supply_scope", "").strip()[:4000],
        "profit_percent": profit_percent,
        "profit_usd": profit_usd,
        "shared_user_id": shared_user_id,
        "shared_percent": shared_percent,
        "technical_support_user_id": technical_support_user_id,
        **totals,
    }
    return commercial, items


def save_commercial(database, quote_id, commercial, items):
    database.execute(
        """INSERT INTO quote_commercial(
        quote_id,currency,global_discount,tax_percent,validity_days,
        payment_terms,notes,delivery_time,supply_scope,profit_percent,profit_usd,
        shared_user_id,shared_percent,technical_support_user_id,
        subtotal,discount_total,tax_total,total,updated_at
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(quote_id) DO UPDATE SET
        currency=excluded.currency,
        global_discount=excluded.global_discount,
        tax_percent=excluded.tax_percent,
        validity_days=excluded.validity_days,
        payment_terms=excluded.payment_terms,
        notes=excluded.notes,
        delivery_time=excluded.delivery_time,
        supply_scope=excluded.supply_scope,
        profit_percent=excluded.profit_percent,
        profit_usd=excluded.profit_usd,
        shared_user_id=excluded.shared_user_id,
        shared_percent=excluded.shared_percent,
        technical_support_user_id=excluded.technical_support_user_id,
        subtotal=excluded.subtotal,
        discount_total=excluded.discount_total,
        tax_total=excluded.tax_total,
        total=excluded.total,
        updated_at=excluded.updated_at""",
        (
            quote_id,
            commercial["currency"],
            commercial["global_discount"],
            commercial["tax_percent"],
            commercial["validity_days"],
            commercial["payment_terms"],
            commercial["notes"],
            commercial["delivery_time"],
            commercial["supply_scope"],
            commercial["profit_percent"],
            commercial["profit_usd"],
            commercial["shared_user_id"],
            commercial["shared_percent"],
            commercial["technical_support_user_id"],
            commercial["subtotal"],
            commercial["discount_total"],
            commercial["tax_total"],
            commercial["total"],
            iso(),
        ),
    )
    database.execute("DELETE FROM quote_items WHERE quote_id=?", (quote_id,))
    database.executemany(
        """INSERT INTO quote_items(
        quote_id,position,kind,description,details,quantity,unit_price,
        discount_percent,line_total
        ) VALUES(?,?,?,?,?,?,?,?,?)""",
        [
            (
                quote_id,
                item["position"],
                item["kind"],
                item["description"],
                item["details"],
                item["quantity"],
                item["unit_price"],
                item["discount_percent"],
                item["line_total"],
            )
            for item in items
        ],
    )


def snapshot_quote(database, quote_id, user_id):
    quote = database.execute("SELECT * FROM quotes WHERE id=?", (quote_id,)).fetchone()
    commercial = get_commercial(database, quote_id)
    items = get_items(database, quote_id)
    database.execute(
        """INSERT INTO quote_versions(
        quote_id,version,created_at,user_id,data,commercial_data,items_data
        ) VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(quote_id,version) DO UPDATE SET
        created_at=excluded.created_at,
        user_id=excluded.user_id,
        data=excluded.data,
        commercial_data=excluded.commercial_data,
        items_data=excluded.items_data""",
        (
            quote_id,
            quote["version"],
            iso(),
            user_id,
            quote["data"],
            json.dumps(commercial, ensure_ascii=False),
            json.dumps(items, ensure_ascii=False),
        ),
    )
