import json
import re
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation

from flask import Blueprint, render_template, request

from .auth import admin_required, current_user, login_required
from .constants import STATUSES
from .db import connection
from .deadlines import ACTIVE_DUE_STATUSES, local_today, quote_timing

bp = Blueprint("dashboard", __name__)


def _positive_decimal(value):
    try:
        amount = Decimal(str(value or "0"))
    except InvalidOperation:
        return None
    return amount if amount.is_finite() and amount > 0 else None


def _display_amount(quote):
    commercial_total = _positive_decimal(quote.get("total"))
    if commercial_total is not None and quote.get("currency"):
        return quote["currency"], commercial_total
    for currency, key in (("CLP", "clp"), ("USD", "usd"), ("EUR", "eur")):
        historical_total = _positive_decimal(quote["d"].get(key))
        if historical_total is not None:
            return currency, historical_total
    return "", None


def _quote_year(quote):
    for value in (
        quote["d"].get("fecha"),
        quote["d"].get("solicitud"),
        quote.get("created_at"),
    ):
        match = re.search(r"(?:^|\D)((?:19|20)\d{2})(?:\D|$)", str(value or ""))
        if match:
            return int(match.group(1))
    return None


@bp.route("/approvals")
@admin_required
def approvals():
    with connection() as database:
        rows = database.execute(
            """SELECT quotes.*,quote_commercial.currency,quote_commercial.total,
            COALESCE(NULLIF(trim(owner.first_name || ' ' || owner.last_name), ''),
                owner.username) AS seller_name,
            COALESCE(NULLIF(trim(estimator.first_name || ' ' ||
                estimator.last_name), ''),
                estimator.username, 'Sin asignar') AS estimator_name,
            (SELECT max(at) FROM audit
             WHERE audit.quote_id=quotes.id AND audit.action='flujo'
             AND audit.after LIKE '%en_revision%') AS requested_at
            FROM quotes
            JOIN users AS owner ON owner.id=quotes.owner_id
            LEFT JOIN users AS estimator ON estimator.id=quotes.estimator_id
            LEFT JOIN quote_commercial ON quote_commercial.quote_id=quotes.id
            WHERE quotes.workflow_status='en_revision'
            ORDER BY requested_at,quotes.id"""
        ).fetchall()
    pending = [dict(row) | {"d": json.loads(row["data"])} for row in rows]
    return render_template("approvals.html", pending=pending)


@bp.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    with connection() as database:
        if user["role"] == "admin":
            quotes = database.execute(
                """SELECT quotes.*,quote_commercial.currency,
                quote_commercial.total,
                COALESCE(NULLIF(trim(owner.first_name || ' ' ||
                    owner.last_name), ''), owner.username) AS seller_name,
                COALESCE(NULLIF(trim(estimator.first_name || ' ' ||
                    estimator.last_name), ''), estimator.username)
                    AS estimator_name
                FROM quotes JOIN users AS owner ON owner.id=quotes.owner_id
                LEFT JOIN users AS estimator ON estimator.id=quotes.estimator_id
                LEFT JOIN quote_commercial
                ON quote_commercial.quote_id=quotes.id
                ORDER BY quotes.id DESC"""
            ).fetchall()
        elif user["role"] == "cotizador":
            quotes = database.execute(
                """SELECT quotes.*,quote_commercial.currency,
                quote_commercial.total,
                COALESCE(NULLIF(trim(owner.first_name || ' ' ||
                    owner.last_name), ''), owner.username) AS seller_name,
                COALESCE(NULLIF(trim(estimator.first_name || ' ' ||
                    estimator.last_name), ''), estimator.username)
                    AS estimator_name
                FROM quotes JOIN users AS owner ON owner.id=quotes.owner_id
                LEFT JOIN users AS estimator ON estimator.id=quotes.estimator_id
                LEFT JOIN quote_commercial
                ON quote_commercial.quote_id=quotes.id
                WHERE quotes.request_status='pendiente_asignacion'
                OR quotes.estimator_id=? ORDER BY quotes.id DESC""",
                (user["id"],),
            ).fetchall()
        else:
            quotes = database.execute(
                """SELECT quotes.*,quote_commercial.currency,
                quote_commercial.total,
                COALESCE(NULLIF(trim(owner.first_name || ' ' ||
                    owner.last_name), ''), owner.username) AS seller_name,
                COALESCE(NULLIF(trim(estimator.first_name || ' ' ||
                    estimator.last_name), ''), estimator.username)
                    AS estimator_name
                FROM quotes JOIN users AS owner ON owner.id=quotes.owner_id
                LEFT JOIN users AS estimator ON estimator.id=quotes.estimator_id
                LEFT JOIN quote_commercial
                ON quote_commercial.quote_id=quotes.id
                WHERE quotes.owner_id=? ORDER BY quotes.id DESC""",
                (user["id"],),
            ).fetchall()

    today = local_today()
    enriched = []
    for row in quotes:
        quote = dict(row) | {"d": json.loads(row["data"])}
        enriched_quote = quote | quote_timing(quote)
        target_date = enriched_quote["target_date"]
        days_to_due = (target_date - today).days if target_date else None
        enriched_quote["days_to_due"] = days_to_due
        enriched_quote["is_due_soon"] = (
            not enriched_quote["is_overdue"]
            and enriched_quote["request_status"] in ACTIVE_DUE_STATUSES
            and days_to_due is not None
            and 0 <= days_to_due <= 7
        )
        display_currency, display_total = _display_amount(enriched_quote)
        enriched_quote["display_currency"] = display_currency
        enriched_quote["display_total"] = display_total
        enriched_quote["commercial_year"] = _quote_year(enriched_quote)
        enriched.append(enriched_quote)

    available_years = sorted(
        {row["commercial_year"] for row in enriched if row["commercial_year"]},
        reverse=True,
    )
    requested_year = request.args.get("year", type=int)
    selected_year = (
        requested_year
        if requested_year in available_years
        else today.year
        if today.year in available_years
        else available_years[0]
        if available_years
        else today.year
    )
    commercial_rows = [
        row for row in enriched if row["commercial_year"] == selected_year
    ]
    status_counts = Counter(
        row["d"].get("estado") or "Sin estado" for row in commercial_rows
    )
    active_totals = defaultdict(Decimal)
    active_commercial_statuses = set(STATUSES) - {"Adjudicada", "Perdida", "Anulada"}
    active_business_count = sum(
        row["d"].get("estado") in active_commercial_statuses for row in commercial_rows
    )
    for row in commercial_rows:
        if (
            row["display_currency"]
            and row["display_total"] is not None
            and row["d"].get("estado") in active_commercial_statuses
        ):
            active_totals[row["display_currency"]] += row["display_total"]
    pending_count = sum(
        row["request_status"] == "pendiente_asignacion" for row in enriched
    )
    in_progress_count = sum(
        row["request_status"]
        in ("en_cotizacion", "requiere_informacion", "en_correccion")
        for row in enriched
    )
    overdue = [row for row in enriched if row["is_overdue"]]
    priority = sorted(
        (
            row
            for row in enriched
            if row["is_overdue"]
            or row["request_status"] == "pendiente_asignacion"
            or row["is_due_soon"]
        ),
        key=lambda row: (
            0
            if row["is_overdue"]
            else 1
            if row["request_status"] == "pendiente_asignacion"
            else 2,
            row["target_date"] or today,
            row["id"],
        ),
    )
    commercial_labels = [*STATUSES, "Sin estado"]
    commercial_total_quotes = len(commercial_rows)
    commercial_breakdown = [
        {
            "label": label,
            "count": status_counts[label],
            "percent": round(status_counts[label] * 100 / commercial_total_quotes),
        }
        for label in commercial_labels
        if status_counts[label]
    ]
    won_count = status_counts["Adjudicada"]
    lost_count = status_counts["Perdida"]
    decided_count = won_count + lost_count
    win_rate = round(won_count * 100 / decided_count) if decided_count else None
    seller_wins = Counter()
    seller_labels = {}
    for row in commercial_rows:
        if row["d"].get("estado") != "Adjudicada":
            continue
        seller = " ".join(
            str(row["d"].get("vendedor") or row.get("seller_name") or "").split()
        )
        if not seller:
            continue
        seller_key = seller.casefold()
        seller_wins[seller_key] += 1
        current_label = seller_labels.get(seller_key, "")
        if sum(character.isupper() for character in seller) > sum(
            character.isupper() for character in current_label
        ):
            seller_labels[seller_key] = seller
    ranked_sellers = sorted(
        seller_wins.items(),
        key=lambda item: (-item[1], seller_labels[item[0]].casefold()),
    )[:5]
    top_seller_count = ranked_sellers[0][1] if ranked_sellers else 0
    top_sellers = [
        {
            "name": seller_labels[key],
            "count": count,
            "percent": round(count * 100 / top_seller_count),
        }
        for key, count in ranked_sellers
    ]
    return render_template(
        "dashboard.html",
        year_quote_count=commercial_total_quotes,
        commercial_breakdown=commercial_breakdown,
        active_totals={
            currency: active_totals[currency]
            for currency in ("CLP", "USD", "EUR")
            if active_totals[currency]
        },
        available_years=available_years or [selected_year],
        selected_year=selected_year,
        win_rate=win_rate,
        won_count=won_count,
        decided_count=decided_count,
        active_business_count=active_business_count,
        top_sellers=top_sellers,
        pending_count=pending_count,
        in_progress_count=in_progress_count,
        overdue_count=len(overdue),
        priority=priority[:8],
        recent=enriched[:8],
    )
