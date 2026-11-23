import json
from collections import Counter, defaultdict
from decimal import Decimal

from flask import Blueprint, render_template

from .auth import admin_required, current_user, login_required
from .constants import REQUEST_STATUS_LABELS
from .db import connection
from .deadlines import quote_timing

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
                (SELECT username FROM users WHERE id=quotes.estimator_id)
                AS estimator_name FROM quotes LEFT JOIN quote_commercial
                ON quote_commercial.quote_id=quotes.id
                ORDER BY quotes.id DESC"""
            ).fetchall()
        elif user["role"] == "cotizador":
            quotes = database.execute(
                """SELECT quotes.*,quote_commercial.currency,
                quote_commercial.total,
                (SELECT username FROM users WHERE id=quotes.estimator_id)
                AS estimator_name FROM quotes LEFT JOIN quote_commercial
                ON quote_commercial.quote_id=quotes.id
                WHERE quotes.request_status='pendiente_asignacion'
                OR quotes.estimator_id=? ORDER BY quotes.id DESC""",
                (user["id"],),
            ).fetchall()
        else:
            quotes = database.execute(
                """SELECT quotes.*,quote_commercial.currency,
                quote_commercial.total,
                (SELECT username FROM users WHERE id=quotes.estimator_id)
                AS estimator_name FROM quotes LEFT JOIN quote_commercial
                ON quote_commercial.quote_id=quotes.id
                WHERE quotes.owner_id=? ORDER BY quotes.id DESC""",
                (user["id"],),
            ).fetchall()

    enriched = []
    for row in quotes:
        quote = dict(row) | {"d": json.loads(row["data"])}
        enriched.append(quote | quote_timing(quote))

    workflow_counts = Counter(row["workflow_status"] for row in quotes)
    request_counts = Counter(
        REQUEST_STATUS_LABELS.get(row["request_status"], row["request_status"])
        for row in quotes
    )
    status_counts = Counter(
        json.loads(row["data"]).get("estado") or "Sin estado" for row in quotes
    )
    totals = defaultdict(Decimal)
    for row in quotes:
        if row["currency"] and row["total"]:
            totals[row["currency"]] += Decimal(row["total"])
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
    return render_template(
        "dashboard.html",
        total_quotes=len(quotes),
        workflow_counts=workflow_counts,
        request_counts=request_counts,
        status_counts=status_counts,
        currency_totals=dict(totals),
        pending_count=pending_count,
        in_progress_count=in_progress_count,
        overdue_count=len(overdue),
        delivered_count=delivered_count,
        overdue=overdue[:10],
        recent=enriched[:10],
    )
