from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import current_app


def _parse_date(value):
    if isinstance(value, (datetime, date)):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None


def _display_timezone():
    try:
        return ZoneInfo(current_app.config["DISPLAY_TIMEZONE"])
    except ZoneInfoNotFoundError:
        current_app.logger.warning(
            "No se encontró la zona horaria %s; se usará la zona local del sistema",
            current_app.config["DISPLAY_TIMEZONE"],
        )
        return datetime.now().astimezone().tzinfo


def format_local_datetime(value):
    parsed = _parse_date(value)
    if parsed is None:
        return str(value or "")
    if not isinstance(parsed, datetime):
        return parsed.strftime("%d-%m-%Y")
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(_display_timezone())
    return parsed.strftime("%d-%m-%Y %H:%M")


def format_local_date(value):
    parsed = _parse_date(value)
    if parsed is None:
        return str(value or "")
    if isinstance(parsed, datetime) and parsed.tzinfo is not None:
        parsed = parsed.astimezone(_display_timezone())
    return parsed.strftime("%d-%m-%Y")
