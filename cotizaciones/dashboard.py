import json
from collections import Counter, defaultdict
from decimal import Decimal

from flask import Blueprint, render_template

from .auth import admin_required, current_user, login_required
from .constants import REQUEST_STATUS_LABELS, STATUSES
from .db import connection
from .deadlines import ACTIVE_DUE_STATUSES, local_today, quote_timing

bp = Blueprint("dashboard", __name__)


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
        enriched.append(enriched_quote)

    request_counts = Counter(
        REQUEST_STATUS_LABELS.get(row["request_status"], row["request_status"])
        for row in quotes
    )
    status_counts = Counter(
        json.loads(row["data"]).get("estado") or "Sin estado" for row in quotes
    )
    active_totals = defaultdict(Decimal)
    active_commercial_statuses = set(STATUSES) - {"Adjudicada", "Perdida", "Anulada"}
    for row in enriched:
        if (
            row["currency"]
            and row["total"]
            and row["d"].get("estado") in active_commercial_statuses
        ):
            active_totals[row["currency"]] += Decimal(row["total"])
    pending_count = sum(
        row["request_status"] == "pendiente_asignacion" for row in enriched
    )
    in_progress_count = sum(
        row["request_status"]
        in ("en_cotizacion", "requiere_informacion", "en_correccion")
        for row in enriched
    )
    overdue = [row for row in enriched if row["is_overdue"]]
    delivered_count = sum(row["request_status"] == "entregada" for row in enriched)
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
    total_quotes = len(enriched)
    request_breakdown = [
        {
            "label": label,
            "count": request_counts[label],
            "percent": round(request_counts[label] * 100 / total_quotes),
        }
        for label in REQUEST_STATUS_LABELS.values()
        if request_counts[label]
    ]
    commercial_labels = [*STATUSES, "Sin estado"]
    commercial_breakdown = [
        {
            "label": label,
            "count": status_counts[label],
            "percent": round(status_counts[label] * 100 / total_quotes),
        }
        for label in commercial_labels
        if status_counts[label]
    ]
    won_count = status_counts["Adjudicada"]
    lost_count = status_counts["Perdida"]
    decided_count = won_count + lost_count
    win_rate = round(won_count * 100 / decided_count) if decided_count else None
    return render_template(
        "dashboard.html",
        total_quotes=total_quotes,
        request_breakdown=request_breakdown,
        commercial_breakdown=commercial_breakdown,
        active_totals=dict(active_totals),
        win_rate=win_rate,
        decided_count=decided_count,
        pending_count=pending_count,
        in_progress_count=in_progress_count,
        overdue_count=len(overdue),
        delivered_count=delivered_count,
        priority=priority[:8],
        recent=enriched[:8],
    )
