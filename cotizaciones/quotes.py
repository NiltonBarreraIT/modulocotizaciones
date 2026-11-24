import hashlib
import json
import secrets
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from .attachments import insert_attachment, remove_attachment, store_attachment
from .auth import current_user, login_required
from .catalog_service import active_catalog_options
from .commercial_service import format_amount, get_commercial, get_items, snapshot_quote
from .constants import (
    FIELDS,
    INITIAL_STATUSES,
    REQUEST_STATUS_LABELS,
    STATUS_TRANSITIONS,
    STATUSES,
)
from .db import connection, iso
from .deadlines import overdue_sql_condition, overdue_sql_parameters, quote_timing
from .formatting import format_local_date, format_local_datetime
from .numbering import next_correlative, quote_number

bp = Blueprint("quotes", __name__)
FIELD_LABELS = {key: label for key, label, _ in FIELDS}
COMMERCIAL_SUMMARY_FIELDS = {
    "fecha",
    "clp",
    "usd",
    "eur",
    "margen",
    "utilidad_usd",
    "estado",
}


@bp.get("/live/revision")
@login_required
def live_revision():
    with connection() as database:
        revision = database.execute(
            "SELECT COALESCE(MAX(id), 0) FROM audit"
        ).fetchone()[0]
    return {"revision": revision}


def editable(quote, user):
    if user["role"] == "admin":
        return True
    if quote["workflow_status"] not in ("borrador", "rechazada"):
        return False
    if user["role"] == "cotizador":
        return quote["estimator_id"] == user["id"]
    return False


def _normalized_seller(value):
    return " ".join(str(value or "").split()).casefold()


def belongs_to_seller(quote, user):
    """Support current ownership and historical imports assigned by seller name."""
    if quote["owner_id"] == user["id"]:
        return True
    data = json.loads(quote["data"])
    assigned_seller = _normalized_seller(data.get("vendedor"))
    identities = {
        _normalized_seller(user["display_name"]),
        _normalized_seller(user["username"]),
    }
    identities.discard("")
    return assigned_seller in identities


def seller_scope_sql(user, table="quotes"):
    """Return a portable SQL predicate for a salesperson's visible quotes."""
    seller_field = f"json_extract({table}.data,'$.vendedor')"
    return (
        f"({table}.owner_id=? OR "
        f"lower(trim(COALESCE({seller_field},''))) IN (lower(trim(?)),lower(trim(?))))",
        [user["id"], user["display_name"], user["username"]],
    )


def can_access_quote(quote, user):
    if user["role"] in ("admin", "cotizador"):
        return True
    return user["role"] == "usuario" and belongs_to_seller(quote, user)


def deadline(quote):
    value = datetime.fromisoformat(quote["last_user_edit_at"]) + timedelta(hours=24)
    return value.isoformat(timespec="seconds")


def load_quote(quote):
    quote_data = dict(quote)
    loaded = quote_data | {
        "d": json.loads(quote["data"]),
        "deadline": deadline(quote),
        "estimator_name": quote_data.get("estimator_name"),
        "request_status_label": REQUEST_STATUS_LABELS.get(
            quote["request_status"], quote["request_status"].replace("_", " ").title()
        ),
    }
    return loaded | quote_timing(loaded)


def get_quote(quote_id):
    with connection() as database:
        quote = database.execute(
            """SELECT quotes.*,
            COALESCE(NULLIF(trim(estimator.first_name || ' ' ||
                estimator.last_name), ''), estimator.email, estimator.username)
                AS estimator_name,
            COALESCE(NULLIF(trim(owner.first_name || ' ' || owner.last_name), ''),
                owner.username) AS owner_name,
            COALESCE(NULLIF(owner.email, ''), owner.username) AS owner_email,
            COALESCE(owner.phone, '') AS owner_phone
            FROM quotes
            JOIN users AS owner ON owner.id=quotes.owner_id
            LEFT JOIN users AS estimator ON estimator.id=quotes.estimator_id
            WHERE quotes.id=?""",
            (quote_id,),
        ).fetchone()
    if not quote:
        abort(404)
    return quote


def validate_quote_data(data):
    if not data["cliente"] or not data["descripcion"]:
        abort(400, "Cliente y descripción obligatorios")
    if data["estado"] and data["estado"] not in STATUSES:
        abort(400, "Estado de cotización inválido")
    for key in ("solicitud", "fecha"):
        if data[key]:
            try:
                date.fromisoformat(data[key])
            except ValueError:
                abort(400, f"Fecha inválida: {key}")
    for key in ("clp", "usd", "eur", "utilidad_usd"):
        try:
            value = Decimal(data[key]) if data[key] else None
            if value is not None and (not value.is_finite() or value < 0):
                raise InvalidOperation
        except InvalidOperation:
            abort(400, f"Monto inválido o negativo: {key}")
    if data["margen"]:
        try:
            margin = Decimal(data["margen"])
            if not margin.is_finite() or not Decimal("0") <= margin <= Decimal("100"):
                raise InvalidOperation
        except InvalidOperation:
            abort(400, "El margen debe estar entre 0 y 100")
    return data


def form_data(*, validate=True):
    data = {key: request.form.get(key, "").strip() for key, _, _ in FIELDS}
    return validate_quote_data(data) if validate else data


def request_clients(user, current_client_id=None):
    """Return the active client portfolio available to the connected user."""
    with connection() as database:
        if user["role"] == "admin":
            parameters = []
            condition = "clients.active=1"
            if current_client_id:
                condition = "(clients.active=1 OR clients.id=?)"
                parameters.append(current_client_id)
            return database.execute(
                f"""SELECT clients.id,clients.name,clients.tax_id,clients.active
                FROM clients WHERE {condition} ORDER BY clients.name""",
                parameters,
            ).fetchall()
        return database.execute(
            """SELECT clients.id,clients.name,clients.tax_id,clients.active
            FROM clients JOIN client_sellers
            ON client_sellers.client_id=clients.id
            WHERE client_sellers.user_id=? AND clients.active=1
            ORDER BY clients.name""",
            (user["id"],),
        ).fetchall()


def resolve_request_client(database, user, fallback_name=""):
    """Validate the selected client against the seller's server-side portfolio."""
    raw_client_id = request.form.get("client_id", "").strip()
    if not raw_client_id:
        # Backwards compatibility for administrative integrations that predate
        # the client registry. Until the administrator creates the first client,
        # legacy requests can still arrive; the browser UI remains disabled.
        registry_is_empty = not database.execute(
            "SELECT 1 FROM clients LIMIT 1"
        ).fetchone()
        if fallback_name and (user["role"] == "admin" or registry_is_empty):
            return None, fallback_name
        abort(400, "Debe seleccionar un cliente de su cartera")
    try:
        client_id = int(raw_client_id)
    except ValueError:
        abort(400, "Cliente inválido")
    if user["role"] == "admin":
        client = database.execute(
            "SELECT id,name FROM clients WHERE id=? AND active=1", (client_id,)
        ).fetchone()
    else:
        client = database.execute(
            """SELECT clients.id,clients.name FROM clients
            JOIN client_sellers ON client_sellers.client_id=clients.id
            WHERE clients.id=? AND client_sellers.user_id=? AND clients.active=1""",
            (client_id, user["id"]),
        ).fetchone()
    if not client:
        abort(403, "El cliente no está activo o no pertenece a su cartera")
    return client["id"], client["name"]


def quote_fingerprint(data):
    normalized = [
        " ".join(str(data.get(key, "")).split()).casefold() for key, _, _ in FIELDS
    ]
    content = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(content.encode()).hexdigest()


def allowed_statuses(current_status, user, creating=False):
    if user["role"] == "admin":
        return STATUSES
    if creating:
        return INITIAL_STATUSES
    transitions = STATUS_TRANSITIONS.get(current_status, [])
    return [
        status
        for status in STATUSES
        if status == current_status or status in transitions
    ]


def validate_status_transition(old_status, new_status, user):
    if user["role"] == "admin" or old_status == new_status:
        return
    if new_status not in STATUS_TRANSITIONS.get(old_status, []):
        old_label = old_status or "Sin estado"
        new_label = new_status or "Sin estado"
        abort(
            400,
            f"Transición de estado no permitida: {old_label} → {new_label}",
        )


def build_audit_events(rows):
    workflow_labels = {
        "borrador": "Borrador",
        "en_revision": "En revisión",
        "aprobada": "Aprobada",
        "rechazada": "Rechazada",
        "enviada": "Enviada",
    }
    action_summaries = {
        "crear": "Creó la cotización",
        "crear_solicitud": "Creó la solicitud",
        "importar": "Importó la cotización",
        "editar": "Actualizó los datos de la solicitud",
        "editar_comercial": "Actualizó el detalle comercial",
        "tomar_solicitud": "Tomó la solicitud",
        "solicitar_informacion": "Solicitó información adicional",
        "informacion_enviada": "Envió la información solicitada",
        "entregar_oferta": "Entregó la oferta comercial",
        "solicitar_correccion": "Solicitó una corrección",
        "actualizar_plazo": "Actualizó la fecha límite",
        "cambiar_estado_comercial": "Cambió el estado comercial",
    }

    events = []
    for row in rows:
        event = dict(row)
        event["changes"] = []
        event["summary"] = action_summaries.get(
            row["action"], "Registró una actualización"
        )
        if row["action"] == "editar":
            before = json.loads(row["before"] or "{}")
            after = json.loads(row["after"] or "{}")
            for key, label, _ in FIELDS:
                old_value = before.get(key, "")
                new_value = after.get(key, "")
                if old_value != new_value:
                    if key in ("solicitud", "fecha"):
                        old_value = format_local_date(old_value) if old_value else ""
                        new_value = format_local_date(new_value) if new_value else ""
                    elif key in ("clp", "usd", "eur"):
                        currency = key.upper()
                        old_value = (
                            format_amount(old_value, currency) if old_value else ""
                        )
                        new_value = (
                            format_amount(new_value, currency) if new_value else ""
                        )
                    event["changes"].append(
                        {"label": label, "before": old_value, "after": new_value}
                    )
        elif row["action"] == "editar_comercial":
            before = json.loads(row["before"] or "{}")
            after = json.loads(row["after"] or "{}")
            before_commercial = before.get("commercial", {})
            after_commercial = after.get("commercial", {})
            commercial_labels = {
                "currency": "Moneda",
                "global_discount": "Descuento global",
                "total": "Total",
            }
            currency = after_commercial.get("currency") or before_commercial.get(
                "currency", "CLP"
            )
            for key, label in commercial_labels.items():
                old_value = before_commercial.get(key, "")
                new_value = after_commercial.get(key, "")
                if old_value != new_value:
                    if key == "total":
                        old_value = format_amount(old_value, currency)
                        new_value = format_amount(new_value, currency)
                    elif key == "global_discount":
                        old_value = f"{old_value}%" if old_value else ""
                        new_value = f"{new_value}%" if new_value else ""
                    event["changes"].append(
                        {"label": label, "before": old_value, "after": new_value}
                    )
            old_count = len(before.get("items", []))
            new_count = len(after.get("items", []))
            if old_count != new_count:
                event["changes"].append(
                    {
                        "label": "Líneas comerciales",
                        "before": old_count,
                        "after": new_count,
                    }
                )
        elif row["action"] == "flujo":
            before = json.loads(row["before"] or "{}")
            after = json.loads(row["after"] or "{}")
            new_status = after.get("workflow_status", "")
            event["summary"] = {
                "borrador": "Reabrió la cotización como borrador",
                "en_revision": "Envió la cotización a revisión",
                "aprobada": "Aprobó la cotización",
                "rechazada": "Rechazó la cotización",
                "enviada": "Marcó la cotización como enviada",
            }.get(new_status, "Actualizó el flujo de aprobación")
            event["changes"].append(
                {
                    "label": "Flujo de aprobación",
                    "before": workflow_labels.get(
                        before.get("workflow_status", ""),
                        before.get("workflow_status", ""),
                    ),
                    "after": workflow_labels.get(new_status, new_status),
                }
            )
        elif row["action"] in ("tomar_solicitud", "reasignar_solicitud"):
            before = json.loads(row["before"] or "{}")
            after = json.loads(row["after"] or "{}")
            if row["action"] == "reasignar_solicitud":
                event["summary"] = (
                    f"Asignó la solicitud a {after['estimator']}"
                    if after.get("estimator")
                    else "Dejó la solicitud sin asignar"
                )
            event["changes"].extend(
                [
                    {
                        "label": "Cotizador",
                        "before": before.get("estimator", ""),
                        "after": after.get("estimator", ""),
                    },
                    {
                        "label": "Estado de solicitud",
                        "before": REQUEST_STATUS_LABELS.get(
                            before.get("request_status", ""),
                            before.get("request_status", ""),
                        ),
                        "after": REQUEST_STATUS_LABELS.get(
                            after.get("request_status", ""),
                            after.get("request_status", ""),
                        ),
                    },
                ]
            )
        elif row["action"] in (
            "solicitar_informacion",
            "informacion_enviada",
            "entregar_oferta",
            "solicitar_correccion",
        ):
            before = json.loads(row["before"] or "{}")
            after = json.loads(row["after"] or "{}")
            event["changes"].append(
                {
                    "label": "Estado de solicitud",
                    "before": REQUEST_STATUS_LABELS.get(
                        before.get("request_status", ""),
                        before.get("request_status", ""),
                    ),
                    "after": REQUEST_STATUS_LABELS.get(
                        after.get("request_status", ""),
                        after.get("request_status", ""),
                    ),
                }
            )
            if after.get("attachment"):
                event["changes"].append(
                    {
                        "label": "Oferta entregada",
                        "before": "",
                        "after": after["attachment"],
                    }
                )
        elif row["action"] == "actualizar_plazo":
            before = json.loads(row["before"] or "{}")
            after = json.loads(row["after"] or "{}")
            event["changes"].append(
                {
                    "label": "Fecha límite",
                    "before": format_local_date(before["due_date"])
                    if before.get("due_date")
                    else "",
                    "after": format_local_date(after["due_date"])
                    if after.get("due_date")
                    else "",
                }
            )
        elif row["action"] == "cambiar_estado_comercial":
            before = json.loads(row["before"] or "{}")
            after = json.loads(row["after"] or "{}")
            event["changes"].append(
                {
                    "label": "Estado comercial",
                    "before": before.get("estado", ""),
                    "after": after.get("estado", ""),
                }
            )
            if after.get("justificacion"):
                event["changes"].append(
                    {
                        "label": "Justificación",
                        "before": "",
                        "after": after["justificacion"],
                    }
                )
            if after.get("orden_compra"):
                event["changes"].append(
                    {
                        "label": "Orden de compra",
                        "before": "",
                        "after": after["orden_compra"],
                    }
                )
        event["change_count"] = len(event["changes"])
        events.append(event)
    return events


def excel_safe(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _validated_filter_date(value, name):
    if not value:
        return ""
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        abort(400, f"Fecha de filtro inválida: {name}")


def _validated_due_date(value, request_date=""):
    if not value:
        return None
    try:
        value = date.fromisoformat(value).isoformat()
    except ValueError:
        abort(400, "Fecha límite inválida")
    if request_date and value < request_date:
        abort(400, "La fecha límite no puede ser anterior a la solicitud")
    return value


@bp.route("/")
@login_required
def index():
    user = current_user()
    filters = {
        "q": request.args.get("q", "").strip(),
        "cliente": request.args.get("cliente", "").strip(),
        "vendedor": request.args.get("vendedor", "").strip(),
        "cotizador": request.args.get("cotizador", "").strip(),
        "estado": request.args.get("estado", "").strip(),
        "request_status": request.args.get("request_status", "").strip(),
        "timing": request.args.get("timing", "").strip(),
        "desde": _validated_filter_date(request.args.get("desde", ""), "desde"),
        "hasta": _validated_filter_date(request.args.get("hasta", ""), "hasta"),
    }
    if filters["estado"] and filters["estado"] not in STATUSES:
        abort(400, "Estado de filtro inválido")
    if (
        filters["request_status"]
        and filters["request_status"] not in REQUEST_STATUS_LABELS
    ):
        abort(400, "Estado de solicitud inválido")
    if filters["timing"] not in ("", "atrasadas"):
        abort(400, "Situación de plazo inválida")
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        abort(400, "Página inválida")

    conditions = []
    parameters = []
    if user["role"] == "usuario":
        seller_condition, seller_parameters = seller_scope_sql(user)
        conditions.append(seller_condition)
        parameters.extend(seller_parameters)
    if filters["q"]:
        pattern = f"%{filters['q'].lower()}%"
        conditions.append(
            "(lower(number) LIKE ? OR "
            "lower(COALESCE(json_extract(data,'$.cliente'),'')) LIKE ? OR "
            "lower(COALESCE(json_extract(data,'$.descripcion'),'')) LIKE ?)"
        )
        parameters.extend([pattern, pattern, pattern])
    for key in ("cliente", "vendedor"):
        if filters[key]:
            conditions.append(
                f"lower(COALESCE(json_extract(data,'$.{key}'),'')) LIKE ?"
            )
            parameters.append(f"%{filters[key].lower()}%")
    if filters["cotizador"]:
        conditions.append(
            "lower(COALESCE((SELECT username FROM users "
            "WHERE id=quotes.estimator_id),'')) LIKE ?"
        )
        parameters.append(f"%{filters['cotizador'].lower()}%")
    if filters["estado"]:
        conditions.append("json_extract(data,'$.estado')=?")
        parameters.append(filters["estado"])
    if filters["request_status"]:
        conditions.append("request_status=?")
        parameters.append(filters["request_status"])
    if filters["timing"] == "atrasadas":
        conditions.append(overdue_sql_condition())
        parameters.extend(overdue_sql_parameters())
    if filters["desde"]:
        conditions.append("json_extract(data,'$.solicitud')>=?")
        parameters.append(filters["desde"])
    if filters["hasta"]:
        conditions.append("json_extract(data,'$.solicitud')<=?")
        parameters.append(filters["hasta"])

    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    page_size = current_app.config["QUOTE_PAGE_SIZE"]
    with connection() as database:
        total = database.execute(
            "SELECT count(*) FROM quotes" + where, parameters
        ).fetchone()[0]
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, total_pages)
        raw_quotes = database.execute(
            """SELECT quotes.*,
            (SELECT username FROM users WHERE id=quotes.estimator_id)
            AS estimator_name FROM quotes"""
            + where
            + " ORDER BY id DESC LIMIT ? OFFSET ?",
            [*parameters, page_size, (page - 1) * page_size],
        ).fetchall()

    active_filters = {key: value for key, value in filters.items() if value}
    previous_url = (
        url_for("quotes.index", page=page - 1, **active_filters) if page > 1 else None
    )
    next_url = (
        url_for("quotes.index", page=page + 1, **active_filters)
        if page < total_pages
        else None
    )
    return render_template(
        "index.html",
        quotes=[load_quote(row) for row in raw_quotes],
        filters=filters,
        can_edit=editable,
        page=page,
        total=total,
        total_pages=total_pages,
        previous_url=previous_url,
        next_url=next_url,
    )


@bp.route("/new", methods=["GET", "POST"])
@login_required
def create():
    user = current_user()
    available_statuses = allowed_statuses("", user, creating=True)
    if request.method == "POST":
        data = form_data(validate=False)
        with connection() as database:
            client_id, client_name = resolve_request_client(
                database, user, data["cliente"]
            )
        data["cliente"] = client_name
        validate_quote_data(data)
        data["solicitud"] = data["solicitud"] or date.today().isoformat()
        data["vendedor"] = user["display_name"]
        due_date = _validated_due_date(
            request.form.get("due_date", "").strip(), data["solicitud"]
        )
        if data["estado"] and data["estado"] not in available_statuses:
            abort(400, "Estado inicial no permitido")
        data["estado"] = ""
        timestamp = iso()
        serialized = json.dumps(data, ensure_ascii=False)
        attachment = store_attachment(request.files.get("file"))
        try:
            with connection() as database:
                cursor = database.execute(
                    """INSERT INTO quotes(
                    number,owner_id,created_at,last_user_edit_at,data,fingerprint,
                    request_status,due_date,client_id
                    ) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        f"TEMP-{secrets.token_hex(8)}",
                        user["id"],
                        timestamp,
                        timestamp,
                        serialized,
                        quote_fingerprint(data),
                        "pendiente_asignacion",
                        due_date,
                        client_id,
                    ),
                )
                quote_id = cursor.lastrowid
                correlative = next_correlative(database)
                number = quote_number("SOL", correlative)
                database.execute(
                    "UPDATE quotes SET number=?,correlative=? WHERE id=?",
                    (number, correlative, quote_id),
                )
                database.execute(
                    """INSERT INTO audit(
                    quote_id,user_id,action,at,before,after
                    ) VALUES(?,?,?,?,?,?)""",
                    (
                        quote_id,
                        user["id"],
                        "crear_solicitud",
                        timestamp,
                        None,
                        serialized,
                    ),
                )
                if attachment:
                    insert_attachment(database, quote_id, user["id"], attachment)
                snapshot_quote(database, quote_id, user["id"])
        except Exception:
            remove_attachment(attachment)
            raise
        flash("Solicitud de cotización registrada")
        return redirect(url_for("quotes.detail", quote_id=quote_id))
    return render_template(
        "form.html",
        q=None,
        values={
            "solicitud": date.today().isoformat(),
            "vendedor": user["display_name"],
            "due_date": "",
        },
        title="Nueva solicitud de cotización",
        allowed_statuses=available_statuses,
        is_request_form=True,
        catalogs=active_catalog_options(),
        client_options=request_clients(user),
    )


@bp.route("/quote/<int:quote_id>")
@login_required
def detail(quote_id):
    quote = get_quote(quote_id)
    user = current_user()
    if not can_access_quote(quote, user):
        abort(403)
    with connection() as database:
        audit_rows = database.execute(
            """SELECT audit.*,users.username FROM audit
            LEFT JOIN users ON users.id=audit.user_id
            WHERE quote_id=? ORDER BY audit.id DESC LIMIT 100""",
            (quote_id,),
        ).fetchall()
        commercial = get_commercial(database, quote_id)
        items = get_items(database, quote_id)
        comments = database.execute(
            """SELECT quote_comments.*,users.username FROM quote_comments
            LEFT JOIN users ON users.id=quote_comments.user_id
            WHERE quote_id=? ORDER BY quote_comments.id DESC""",
            (quote_id,),
        ).fetchall()
        attachments = database.execute(
            """SELECT quote_attachments.*,users.username FROM quote_attachments
            LEFT JOIN users ON users.id=quote_attachments.user_id
            WHERE quote_id=? ORDER BY quote_attachments.id DESC""",
            (quote_id,),
        ).fetchall()
        versions = database.execute(
            """SELECT quote_versions.version,quote_versions.created_at,users.username
            FROM quote_versions LEFT JOIN users ON users.id=quote_versions.user_id
            WHERE quote_id=? ORDER BY quote_versions.version DESC""",
            (quote_id,),
        ).fetchall()
        deliveries = database.execute(
            """SELECT quote_deliveries.*,quote_attachments.original_name,
            quote_attachments.size,users.username
            FROM quote_deliveries
            JOIN quote_attachments
            ON quote_attachments.id=quote_deliveries.attachment_id
            LEFT JOIN users ON users.id=quote_deliveries.delivered_by
            WHERE quote_deliveries.quote_id=?
            ORDER BY quote_deliveries.id DESC""",
            (quote_id,),
        ).fetchall()
        estimators = (
            database.execute(
                """SELECT users.id,users.username,users.email,
                COALESCE(NULLIF(trim(users.first_name || ' ' || users.last_name), ''),
                    users.username) AS display_name FROM quote_estimators
                JOIN users ON users.id=quote_estimators.user_id
                ORDER BY display_name"""
            ).fetchall()
            if user["role"] == "admin"
            else []
        )
    return render_template(
        "detail.html",
        q=load_quote(quote),
        audit_events=build_audit_events(audit_rows),
        can_edit=editable(quote, user),
        commercial=commercial,
        items=items,
        comments=comments,
        attachments=attachments,
        versions=versions,
        deliveries=deliveries,
        estimators=estimators,
    )


@bp.route("/quote/<int:quote_id>/take", methods=["POST"])
@login_required
def take_request(quote_id):
    user = current_user()
    if user["role"] != "cotizador":
        abort(403)
    timestamp = iso()
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        cursor = database.execute(
            """UPDATE quotes SET estimator_id=?,taken_at=?,
            request_status='en_cotizacion'
            WHERE id=? AND request_status='pendiente_asignacion'
            AND estimator_id IS NULL""",
            (user["id"], timestamp, quote_id),
        )
        if cursor.rowcount != 1:
            exists = database.execute(
                "SELECT 1 FROM quotes WHERE id=?", (quote_id,)
            ).fetchone()
            if not exists:
                abort(404)
            abort(409, "La solicitud ya fue tomada por otro cotizador")
        database.execute(
            """INSERT INTO audit(quote_id,user_id,action,at,before,after)
            VALUES(?,?,?,?,?,?)""",
            (
                quote_id,
                user["id"],
                "tomar_solicitud",
                timestamp,
                json.dumps(
                    {
                        "estimator": "",
                        "request_status": "pendiente_asignacion",
                    }
                ),
                json.dumps(
                    {
                        "estimator": user["username"],
                        "request_status": "en_cotizacion",
                    }
                ),
            ),
        )
    flash("Solicitud asignada correctamente")
    return redirect(url_for("quotes.detail", quote_id=quote_id))


@bp.route("/quote/<int:quote_id>/commercial-status", methods=["POST"])
@login_required
def update_commercial_status(quote_id):
    get_quote(quote_id)
    user = current_user()
    if user["role"] != "admin":
        abort(403)

    new_status = request.form.get("estado", "").strip()
    if new_status not in STATUSES:
        abort(400, "Estado comercial inválido")
    justification = request.form.get("justificacion", "").strip()

    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        fresh = database.execute(
            "SELECT data,request_status FROM quotes WHERE id=?", (quote_id,)
        ).fetchone()
        if not fresh:
            abort(404)
        data = json.loads(fresh["data"])
        old_status = data.get("estado", "")
        if not old_status:
            abort(
                409,
                "El estado comercial se habilita cuando el cotizador entrega la oferta",
            )
        if new_status == old_status:
            flash("El estado comercial no cambió")
            return redirect(url_for("quotes.detail", quote_id=quote_id))
        if not justification:
            if new_status == "Adjudicada":
                abort(400, "Debe ingresar la orden de compra")
            abort(400, "Debe justificar el cambio de estado comercial")

        updated_data = data | {"estado": new_status}
        audit_detail = {"estado": new_status}
        if new_status == "Adjudicada":
            updated_data["orden_compra"] = justification[:1000]
            audit_detail["orden_compra"] = justification[:1000]
        else:
            audit_detail["justificacion"] = justification[:1000]
        serialized = json.dumps(updated_data, ensure_ascii=False)
        database.execute(
            "UPDATE quotes SET data=?,fingerprint=? WHERE id=?",
            (serialized, quote_fingerprint(updated_data), quote_id),
        )
        database.execute(
            """INSERT INTO audit(quote_id,user_id,action,at,before,after)
            VALUES(?,?,?,?,?,?)""",
            (
                quote_id,
                user["id"],
                "cambiar_estado_comercial",
                iso(),
                json.dumps({"estado": old_status}, ensure_ascii=False),
                json.dumps(audit_detail, ensure_ascii=False),
            ),
        )
    flash(f"Estado comercial actualizado a {new_status}")
    return redirect(url_for("quotes.detail", quote_id=quote_id))


@bp.route("/quote/<int:quote_id>/reassign", methods=["POST"])
@login_required
def reassign_request(quote_id):
    user = current_user()
    if user["role"] != "admin":
        abort(403)
    raw_estimator_id = request.form.get("estimator_id", "").strip()
    try:
        estimator_id = int(raw_estimator_id) if raw_estimator_id else None
    except ValueError:
        abort(400, "Cotizador inválido")
    timestamp = iso()
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        quote = database.execute(
            """SELECT quotes.*,
            (SELECT username FROM users WHERE id=quotes.estimator_id)
            AS estimator_name FROM quotes WHERE quotes.id=?""",
            (quote_id,),
        ).fetchone()
        if not quote:
            abort(404)
        estimator = None
        if estimator_id is not None:
            estimator = database.execute(
                """SELECT users.id,users.username FROM quote_estimators
                JOIN users ON users.id=quote_estimators.user_id
                WHERE users.id=?""",
                (estimator_id,),
            ).fetchone()
            if not estimator:
                abort(400, "El usuario seleccionado no es cotizador")
        new_status = "en_cotizacion" if estimator else "pendiente_asignacion"
        database.execute(
            """UPDATE quotes SET estimator_id=?,taken_at=?,request_status=?
            WHERE id=?""",
            (
                estimator["id"] if estimator else None,
                timestamp if estimator else None,
                new_status,
                quote_id,
            ),
        )
        database.execute(
            """INSERT INTO audit(quote_id,user_id,action,at,before,after)
            VALUES(?,?,?,?,?,?)""",
            (
                quote_id,
                user["id"],
                "reasignar_solicitud",
                timestamp,
                json.dumps(
                    {
                        "estimator": quote["estimator_name"] or "",
                        "request_status": quote["request_status"],
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "estimator": estimator["username"] if estimator else "",
                        "request_status": new_status,
                    },
                    ensure_ascii=False,
                ),
            ),
        )
    flash("Asignación actualizada")
    return redirect(url_for("quotes.detail", quote_id=quote_id))


@bp.route("/quote/<int:quote_id>/edit", methods=["GET", "POST"])
@login_required
def edit(quote_id):
    quote = get_quote(quote_id)
    user = current_user()
    if not editable(quote, user):
        abort(
            403,
            "Edición restringida: ventana de 24 horas vencida o usuario no autorizado",
        )
    current_data = json.loads(quote["data"])
    available_statuses = allowed_statuses(current_data.get("estado", ""), user)
    if request.method == "POST":
        request_only_fields = {
            "csrf",
            "version",
            "solicitud",
            "due_date",
            "client_id",
            "cliente",
            "descripcion",
        }
        is_simple_request = quote["request_status"] == "pendiente_asignacion" and set(
            request.form
        ).issubset(request_only_fields)
        data = form_data(validate=not is_simple_request)
        for key in COMMERCIAL_SUMMARY_FIELDS:
            data[key] = current_data.get(key, "")
        selected_client_id = quote["client_id"]
        if is_simple_request:
            with connection() as database:
                selected_client_id, client_name = resolve_request_client(
                    database, user, data["cliente"]
                )
            data = current_data | {
                "solicitud": data["solicitud"] or current_data.get("solicitud", ""),
                "vendedor": current_data.get("vendedor", user["username"]),
                "cliente": client_name,
                "descripcion": data["descripcion"],
            }
            validate_quote_data(data)
        due_date = (
            _validated_due_date(
                request.form.get("due_date", "").strip(), data["solicitud"]
            )
            if "due_date" in request.form
            else quote["due_date"]
        )
        try:
            submitted_version = int(request.form.get("version", ""))
        except ValueError:
            abort(400, "Versión de cotización inválida")
        timestamp = iso()
        serialized = json.dumps(data, ensure_ascii=False)
        with connection() as database:
            database.execute("BEGIN IMMEDIATE")
            fresh = database.execute(
                "SELECT * FROM quotes WHERE id=?", (quote_id,)
            ).fetchone()
            if not editable(fresh, user):
                abort(403, "Plazo vencido al guardar")
            if submitted_version != fresh["version"]:
                return (
                    render_template(
                        "conflict.html",
                        q=load_quote(fresh),
                        submitted=data,
                    ),
                    409,
                )
            fresh_data = json.loads(fresh["data"])
            validate_status_transition(
                fresh_data.get("estado", ""), data.get("estado", ""), user
            )
            last_user_edit_at = (
                timestamp if user["role"] == "usuario" else fresh["last_user_edit_at"]
            )
            cursor = database.execute(
                """UPDATE quotes SET data=?,last_user_edit_at=?,due_date=?,client_id=?,
                version=version+1,fingerprint=?,workflow_status='borrador',
                approved_by=NULL,approved_at=NULL,approved_version=NULL,sent_at=NULL,
                request_status=CASE
                    WHEN request_status='entregada' AND estimator_id IS NOT NULL
                    THEN 'en_cotizacion'
                    WHEN request_status='entregada' THEN 'registrada'
                    ELSE request_status END
                WHERE id=? AND version=?""",
                (
                    serialized,
                    last_user_edit_at,
                    due_date,
                    selected_client_id,
                    quote_fingerprint(data),
                    quote_id,
                    submitted_version,
                ),
            )
            if cursor.rowcount != 1:
                abort(409, "La cotización cambió durante el guardado")
            database.execute(
                """INSERT INTO audit(
                quote_id,user_id,action,at,before,after
                ) VALUES(?,?,?,?,?,?)""",
                (
                    quote_id,
                    user["id"],
                    "editar",
                    timestamp,
                    fresh["data"],
                    serialized,
                ),
            )
            if due_date != fresh["due_date"]:
                database.execute(
                    """INSERT INTO audit(
                    quote_id,user_id,action,at,before,after
                    ) VALUES(?,?,?,?,?,?)""",
                    (
                        quote_id,
                        user["id"],
                        "actualizar_plazo",
                        timestamp,
                        json.dumps({"due_date": fresh["due_date"]}),
                        json.dumps({"due_date": due_date}),
                    ),
                )
            snapshot_quote(database, quote_id, user["id"])
        flash("Cotización actualizada")
        return redirect(url_for("quotes.detail", quote_id=quote_id))
    return render_template(
        "form.html",
        q=load_quote(quote),
        values=current_data | {"due_date": quote["due_date"] or ""},
        title="Editar " + quote["number"],
        allowed_statuses=available_statuses,
        is_request_form=quote["request_status"] == "pendiente_asignacion",
        catalogs=active_catalog_options(),
        client_options=request_clients(user, quote["client_id"]),
    )


@bp.route("/export")
@login_required
def export():
    from openpyxl import Workbook

    user = current_user()
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Cotizaciones"
    sheet.append(
        ["Número"]
        + [label for _, label, _ in FIELDS]
        + [
            "Estado solicitud",
            "Fecha límite",
            "Creado (hora Santiago)",
            "Última edición (hora Santiago)",
            "Versión",
            "Negocio compartido con",
            "Porcentaje compartido %",
            "Apoyo Técnico PM",
        ]
    )
    export_query = """SELECT quotes.*,
        COALESCE(quote_commercial.shared_percent, '') AS shared_percent,
        COALESCE(NULLIF(trim(shared_user.first_name || ' ' ||
            shared_user.last_name), ''),
            shared_user.username, '') AS shared_user_name,
        COALESCE(NULLIF(trim(technical_user.first_name || ' ' ||
            technical_user.last_name), ''),
            technical_user.username, '') AS technical_support_user_name
        FROM quotes
        LEFT JOIN quote_commercial ON quote_commercial.quote_id=quotes.id
        LEFT JOIN users AS shared_user
        ON shared_user.id=quote_commercial.shared_user_id
        LEFT JOIN users AS technical_user
        ON technical_user.id=quote_commercial.technical_support_user_id"""
    with connection() as database:
        if user["role"] in ("admin", "cotizador"):
            rows = database.execute(export_query + " ORDER BY quotes.id").fetchall()
        else:
            seller_condition, seller_parameters = seller_scope_sql(user)
            rows = database.execute(
                export_query + " WHERE " + seller_condition + " ORDER BY quotes.id",
                seller_parameters,
            ).fetchall()
    for quote in rows:
        data = json.loads(quote["data"])
        sheet.append(
            [quote["number"]]
            + [
                excel_safe(
                    format_local_date(data.get(key, ""))
                    if key in ("solicitud", "fecha") and data.get(key)
                    else data.get(key, "")
                )
                for key, _, _ in FIELDS
            ]
            + [
                REQUEST_STATUS_LABELS.get(
                    quote["request_status"], quote["request_status"]
                ),
                format_local_date(quote["due_date"]) if quote["due_date"] else "",
                format_local_datetime(quote["created_at"]),
                format_local_datetime(quote["last_user_edit_at"]),
                quote["version"],
                excel_safe(quote["shared_user_name"]),
                quote["shared_percent"],
                excel_safe(quote["technical_support_user_name"]),
            ]
        )
    content = BytesIO()
    workbook.save(content)
    workbook.close()
    content.seek(0)
    return send_file(
        content,
        as_attachment=True,
        download_name="cotizaciones_ITS.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
