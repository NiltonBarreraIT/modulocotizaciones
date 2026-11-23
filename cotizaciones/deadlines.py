from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import current_app

from .db import now

ACTIVE_DUE_STATUSES = {
    "en_cotizacion",
    "requiere_informacion",
    "en_correccion",
}


def assignment_cutoff(reference=None):
    hours = current_app.config["ASSIGNMENT_TARGET_HOURS"]
    return (reference or now()) - timedelta(hours=hours)


def local_today(reference=None):
    timezone = ZoneInfo(current_app.config["DISPLAY_TIMEZONE"])
    return (reference or now()).astimezone(timezone).date()


def overdue_sql_condition():
    return """(
        (request_status='pendiente_asignacion' AND created_at<?)
        OR
        (request_status IN ('en_cotizacion','requiere_informacion','en_correccion')
         AND due_date IS NOT NULL AND due_date!='' AND due_date<?)
    )"""


def overdue_sql_parameters(reference=None):
    return [
        assignment_cutoff(reference).isoformat(timespec="seconds"),
        local_today(reference).isoformat(),
    ]


def _human_age(start, reference):
    elapsed = max(timedelta(), reference - start)
    total_hours = int(elapsed.total_seconds() // 3600)
    if total_hours < 1:
        return "Menos de 1 hora"
    if total_hours < 24:
        return f"{total_hours} h"
    days = total_hours // 24
    return f"{days} día" if days == 1 else f"{days} días"


def quote_timing(quote, reference=None):
    reference = reference or now()
    created_at = datetime.fromisoformat(quote["created_at"])
    due_value = quote.get("due_date")
    status = quote["request_status"]
    due_date = date.fromisoformat(due_value) if due_value else None
    assignment_due = created_at + timedelta(
        hours=current_app.config["ASSIGNMENT_TARGET_HOURS"]
    )

    is_overdue = False
    overdue_reason = ""
    target_at = None
    if status == "pendiente_asignacion":
        target_at = assignment_due
        is_overdue = reference > assignment_due
        if is_overdue:
            overdue_reason = "Pendiente de tomar fuera de plazo"
    elif status in ACTIVE_DUE_STATUSES and due_date:
        is_overdue = local_today(reference) > due_date
        if is_overdue:
            overdue_reason = "Fecha límite vencida"

    return {
        "age_label": _human_age(created_at, reference),
        "is_overdue": is_overdue,
        "overdue_reason": overdue_reason,
        "target_at": target_at,
        "target_date": due_date,
    }
