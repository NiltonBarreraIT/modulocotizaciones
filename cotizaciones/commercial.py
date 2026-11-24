import json
from hashlib import sha256
from pathlib import Path

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
)

from .attachments import insert_attachment, remove_attachment, store_attachment
from .auth import current_user, login_required
from .commercial_service import (
    CURRENCIES,
    get_commercial,
    get_items,
    parse_commercial_form,
    save_commercial,
    snapshot_quote,
)
from .db import connection, iso
from .deadlines import local_today
from .offer_docx import build_offer_docx
from .quotes import (
    can_access_quote,
    editable,
    get_quote,
    load_quote,
    quote_fingerprint,
)
from .word_pdf import PdfConversionError, convert_docx_to_pdf

bp = Blueprint("commercial", __name__)
COMMERCIAL_TEMPLATE_SHA256 = (
    "200e8c0e9fbd30c9666adefff65a2562bf9d2e484ebecedbcd4417e8663c646a"
)


def _authorized_quote(quote_id):
    quote = get_quote(quote_id)
    user = current_user()
    if not can_access_quote(quote, user):
        abort(403)
    return quote, user


def _assigned_estimator(quote, user):
    return user["role"] == "cotizador" and quote["estimator_id"] == user["id"]


def _collaboration_users(database, quote):
    shared_sellers = database.execute(
        """SELECT users.id,users.username,users.email,
        COALESCE(NULLIF(trim(users.first_name || ' ' || users.last_name), ''),
            users.username) AS display_name FROM users
        LEFT JOIN quote_estimators ON quote_estimators.user_id=users.id
        WHERE users.role='usuario' AND quote_estimators.user_id IS NULL
        AND users.id!=? ORDER BY display_name""",
        (quote["owner_id"],),
    ).fetchall()
    technical_users = database.execute(
        """SELECT id,username,email,
        COALESCE(NULLIF(trim(first_name || ' ' || last_name), ''), username)
        AS display_name FROM users ORDER BY display_name"""
    ).fetchall()
    return shared_sellers, technical_users


def _validate_collaboration_users(database, quote, commercial):
    shared_user_id = commercial["shared_user_id"]
    if shared_user_id:
        shared_user = database.execute(
            """SELECT users.id FROM users
            LEFT JOIN quote_estimators ON quote_estimators.user_id=users.id
            WHERE users.id=? AND users.role='usuario'
            AND quote_estimators.user_id IS NULL AND users.id!=?""",
            (shared_user_id, quote["owner_id"]),
        ).fetchone()
        if not shared_user:
            abort(400, "El usuario de negocio compartido no es un vendedor válido")
    technical_support_user_id = commercial["technical_support_user_id"]
    if (
        technical_support_user_id
        and not database.execute(
            "SELECT 1 FROM users WHERE id=?", (technical_support_user_id,)
        ).fetchone()
    ):
        abort(400, "El usuario de apoyo técnico PM no existe")


def _commercial_summary_data(data, commercial):
    updated = data | {
        "clp": "",
        "usd": "",
        "eur": "",
        "margen": commercial.get("profit_percent", ""),
        "utilidad_usd": commercial.get("profit_usd", ""),
    }
    updated[commercial["currency"].lower()] = commercial["total"]
    return updated


def _dated_quote_data(serialized):
    data = json.loads(serialized)
    data["fecha"] = local_today().isoformat()
    data["estado"] = "Abierta"
    return data


@bp.route("/commercial-offer-template")
@login_required
def commercial_offer_template():
    user = current_user()
    if user["role"] not in ("admin", "cotizador"):
        abort(403)
    template = Path(current_app.config["COMMERCIAL_OFFER_TEMPLATE"])
    if not template.is_file():
        abort(500, "No se encontró el formato oficial de oferta comercial")
    if sha256(template.read_bytes()).hexdigest() != COMMERCIAL_TEMPLATE_SHA256:
        abort(500, "El formato oficial fue modificado y requiere validación")
    return send_file(
        template,
        as_attachment=True,
        download_name="Formato_Oferta_Comercial_ITS.docx",
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@bp.route("/quote/<int:quote_id>/deliver", methods=["POST"])
@login_required
def deliver_offer(quote_id):
    quote = get_quote(quote_id)
    user = current_user()
    if not _assigned_estimator(quote, user):
        abort(403)
    attachment = store_attachment(
        request.files.get("file"), required=True, allowed_extensions={".docx"}
    )
    comment = request.form.get("body", "").strip()
    timestamp = iso()
    try:
        with connection() as database:
            database.execute("BEGIN IMMEDIATE")
            fresh = database.execute(
                "SELECT * FROM quotes WHERE id=?", (quote_id,)
            ).fetchone()
            if (
                not fresh
                or fresh["estimator_id"] != user["id"]
                or fresh["request_status"] not in ("en_cotizacion", "en_correccion")
            ):
                abort(409, "La solicitud ya no está disponible para entrega")
            attachment_id = insert_attachment(
                database, quote_id, user["id"], attachment
            )
            database.execute(
                """INSERT INTO quote_deliveries(
                quote_id,attachment_id,delivered_by,delivered_at
                ) VALUES(?,?,?,?)""",
                (quote_id, attachment_id, user["id"], timestamp),
            )
            database.execute(
                """UPDATE quotes SET request_status='entregada',data=?,fingerprint=?
                WHERE id=?""",
                (
                    json.dumps(_dated_quote_data(fresh["data"]), ensure_ascii=False),
                    quote_fingerprint(_dated_quote_data(fresh["data"])),
                    quote_id,
                ),
            )
            database.execute(
                """INSERT INTO audit(quote_id,user_id,action,at,before,after)
                VALUES(?,?,?,?,?,?)""",
                (
                    quote_id,
                    user["id"],
                    "entregar_oferta",
                    timestamp,
                    json.dumps({"request_status": fresh["request_status"]}),
                    json.dumps(
                        {
                            "request_status": "entregada",
                            "attachment": attachment["original_name"],
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            if comment:
                database.execute(
                    """INSERT INTO quote_comments(
                    quote_id,user_id,body,created_at
                    ) VALUES(?,?,?,?)""",
                    (quote_id, user["id"], comment[:2000], timestamp),
                )
            snapshot_quote(database, quote_id, user["id"])
    except Exception:
        remove_attachment(attachment)
        raise
    flash("Oferta comercial entregada al vendedor")
    return redirect(url_for("quotes.detail", quote_id=quote_id))


@bp.route("/quote/<int:quote_id>/request-workflow", methods=["POST"])
@login_required
def request_workflow(quote_id):
    quote = get_quote(quote_id)
    user = current_user()
    action = request.form.get("action", "")
    body = request.form.get("body", "").strip()
    if not body:
        abort(400, "Debe ingresar un comentario")

    if action == "need_info":
        if not _assigned_estimator(quote, user) or quote["request_status"] not in (
            "en_cotizacion",
            "en_correccion",
        ):
            abort(403)
        new_status = "requiere_informacion"
        audit_action = "solicitar_informacion"
    elif action == "resume":
        if (user["role"] != "admin" and quote["owner_id"] != user["id"]) or quote[
            "request_status"
        ] != "requiere_informacion":
            abort(403)
        new_status = "en_cotizacion"
        audit_action = "informacion_enviada"
    elif action == "correction":
        if (user["role"] != "admin" and quote["owner_id"] != user["id"]) or quote[
            "request_status"
        ] != "entregada":
            abort(403)
        new_status = "en_correccion"
        audit_action = "solicitar_correccion"
    else:
        abort(400, "Acción de solicitud inválida")

    timestamp = iso()
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        fresh = database.execute(
            "SELECT request_status,estimator_id,owner_id FROM quotes WHERE id=?",
            (quote_id,),
        ).fetchone()
        if not fresh or fresh["request_status"] != quote["request_status"]:
            abort(409, "La solicitud cambió antes de guardar")
        database.execute(
            "UPDATE quotes SET request_status=? WHERE id=?", (new_status, quote_id)
        )
        database.execute(
            """INSERT INTO quote_comments(quote_id,user_id,body,created_at)
            VALUES(?,?,?,?)""",
            (quote_id, user["id"], body[:2000], timestamp),
        )
        database.execute(
            """INSERT INTO audit(quote_id,user_id,action,at,before,after)
            VALUES(?,?,?,?,?,?)""",
            (
                quote_id,
                user["id"],
                audit_action,
                timestamp,
                json.dumps({"request_status": quote["request_status"]}),
                json.dumps({"request_status": new_status}),
            ),
        )
    flash(f"Solicitud actualizada a {new_status.replace('_', ' ')}")
    return redirect(url_for("quotes.detail", quote_id=quote_id))


@bp.route("/quote/<int:quote_id>/commercial", methods=["GET", "POST"])
@login_required
def commercial_form(quote_id):
    quote, user = _authorized_quote(quote_id)
    if not editable(quote, user):
        abort(403, "La cotización no está disponible para edición comercial")
    with connection() as database:
        commercial = get_commercial(database, quote_id)
        items = get_items(database, quote_id)
        shared_sellers, technical_users = _collaboration_users(database, quote)
    if request.method == "POST":
        try:
            submitted_version = int(request.form.get("version", ""))
            submitted_commercial, submitted_items = parse_commercial_form(request.form)
        except ValueError as error:
            abort(400, str(error))
        with connection() as database:
            database.execute("BEGIN IMMEDIATE")
            fresh = database.execute(
                "SELECT * FROM quotes WHERE id=?", (quote_id,)
            ).fetchone()
            if not editable(fresh, user):
                abort(403, "La cotización dejó de estar disponible para edición")
            if submitted_version != fresh["version"]:
                return (
                    render_template(
                        "commercial_conflict.html",
                        q=load_quote(fresh),
                        submitted_commercial=submitted_commercial,
                        submitted_items=submitted_items,
                    ),
                    409,
                )
            _validate_collaboration_users(database, fresh, submitted_commercial)
            before = {
                "commercial": get_commercial(database, quote_id),
                "items": get_items(database, quote_id),
            }
            last_user_edit_at = (
                iso() if user["role"] == "usuario" else fresh["last_user_edit_at"]
            )
            updated_data = _commercial_summary_data(
                json.loads(fresh["data"]), submitted_commercial
            )
            serialized_data = json.dumps(updated_data, ensure_ascii=False)
            database.execute(
                """UPDATE quotes SET version=version+1,last_user_edit_at=?,data=?,
                fingerprint=?,
                workflow_status='borrador',approved_by=NULL,approved_at=NULL,
                approved_version=NULL,sent_at=NULL,
                request_status=CASE
                    WHEN request_status='entregada' AND estimator_id IS NOT NULL
                    THEN 'en_cotizacion'
                    WHEN request_status='entregada' THEN 'registrada'
                    ELSE request_status END
                WHERE id=? AND version=?""",
                (
                    last_user_edit_at,
                    serialized_data,
                    quote_fingerprint(updated_data),
                    quote_id,
                    submitted_version,
                ),
            )
            save_commercial(database, quote_id, submitted_commercial, submitted_items)
            after = {
                "commercial": get_commercial(database, quote_id),
                "items": submitted_items,
            }
            database.execute(
                """INSERT INTO audit(
                quote_id,user_id,action,at,before,after
                ) VALUES(?,?,?,?,?,?)""",
                (
                    quote_id,
                    user["id"],
                    "editar_comercial",
                    iso(),
                    json.dumps(before, ensure_ascii=False),
                    json.dumps(after, ensure_ascii=False),
                ),
            )
            snapshot_quote(database, quote_id, user["id"])
        flash("Detalle comercial actualizado; la aprobación quedó en borrador")
        return redirect(url_for("quotes.detail", quote_id=quote_id))
    return render_template(
        "commercial_form.html",
        q=load_quote(quote),
        commercial=commercial,
        items=items,
        currencies=CURRENCIES,
        shared_sellers=shared_sellers,
        technical_users=technical_users,
    )


@bp.route("/quote/<int:quote_id>/workflow", methods=["POST"])
@login_required
def workflow(quote_id):
    quote, user = _authorized_quote(quote_id)
    action = request.form.get("action", "")
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        fresh = database.execute(
            "SELECT * FROM quotes WHERE id=?", (quote_id,)
        ).fetchone()
        old_status = fresh["workflow_status"]
        updates = {
            "approved_by": fresh["approved_by"],
            "approved_at": fresh["approved_at"],
            "approved_version": fresh["approved_version"],
            "sent_at": fresh["sent_at"],
        }
        quote_data = json.loads(fresh["data"])
        new_request_status = fresh["request_status"]
        if action == "submit":
            if user["role"] != "cotizador" or fresh["estimator_id"] != user["id"]:
                abort(403, "Solo el cotizador asignado puede solicitar aprobación")
            if old_status not in ("borrador", "rechazada"):
                abort(400, "Solo los borradores pueden enviarse a revisión")
            item_count = database.execute(
                "SELECT count(*) FROM quote_items WHERE quote_id=?", (quote_id,)
            ).fetchone()[0]
            if item_count == 0:
                abort(400, "Agregue al menos una línea antes de solicitar aprobación")
            new_status = "en_revision"
        elif action in ("approve", "reject"):
            if user["role"] != "admin" or old_status != "en_revision":
                abort(403)
            if action == "reject" and not request.form.get("reason", "").strip():
                abort(400, "Debe indicar el motivo del rechazo")
            new_status = "aprobada" if action == "approve" else "rechazada"
            if action == "approve":
                updates.update(
                    {
                        "approved_by": user["id"],
                        "approved_at": iso(),
                        "approved_version": fresh["version"],
                    }
                )
            else:
                updates.update(
                    {
                        "approved_by": None,
                        "approved_at": None,
                        "approved_version": None,
                    }
                )
        elif action == "send":
            if (
                old_status != "aprobada"
                or fresh["approved_version"] != fresh["version"]
            ):
                abort(400, "Solo puede enviarse la versión aprobada vigente")
            new_status = "enviada"
            updates["sent_at"] = iso()
            new_request_status = "entregada"
            quote_data["fecha"] = local_today().isoformat()
            quote_data["estado"] = "Abierta"
        elif action == "reopen":
            if user["role"] != "admin":
                abort(403)
            new_status = "borrador"
            updates = {
                "approved_by": None,
                "approved_at": None,
                "approved_version": None,
                "sent_at": None,
            }
            if fresh["request_status"] == "entregada":
                new_request_status = (
                    "en_cotizacion" if fresh["estimator_id"] else "registrada"
                )
        else:
            abort(400, "Acción de flujo inválida")

        database.execute(
            """UPDATE quotes SET workflow_status=?,approved_by=?,approved_at=?,
            approved_version=?,sent_at=?,request_status=?,data=?,fingerprint=?
            WHERE id=?""",
            (
                new_status,
                updates["approved_by"],
                updates["approved_at"],
                updates["approved_version"],
                updates["sent_at"],
                new_request_status,
                json.dumps(quote_data, ensure_ascii=False),
                quote_fingerprint(quote_data),
                quote_id,
            ),
        )
        database.execute(
            """INSERT INTO audit(
            quote_id,user_id,action,at,before,after
            ) VALUES(?,?,?,?,?,?)""",
            (
                quote_id,
                user["id"],
                "flujo",
                iso(),
                json.dumps({"workflow_status": old_status}),
                json.dumps({"workflow_status": new_status}),
            ),
        )
        reason = request.form.get("reason", "").strip()
        if reason:
            database.execute(
                """INSERT INTO quote_comments(
                quote_id,user_id,body,created_at
                ) VALUES(?,?,?,?)""",
                (quote_id, user["id"], reason[:2000], iso()),
            )
        if action == "send":
            snapshot_quote(database, quote_id, user["id"])
    flash(f"Estado de aprobación actualizado a {new_status.replace('_', ' ')}")
    return redirect(url_for("quotes.detail", quote_id=quote_id))


@bp.route("/quote/<int:quote_id>/comments", methods=["POST"])
@login_required
def add_comment(quote_id):
    _, user = _authorized_quote(quote_id)
    body = request.form.get("body", "").strip()
    if not body:
        abort(400, "El comentario no puede estar vacío")
    with connection() as database:
        database.execute(
            """INSERT INTO quote_comments(
            quote_id,user_id,body,created_at
            ) VALUES(?,?,?,?)""",
            (quote_id, user["id"], body[:2000], iso()),
        )
    flash("Comentario agregado")
    return redirect(url_for("quotes.detail", quote_id=quote_id))


@bp.route("/quote/<int:quote_id>/attachments", methods=["POST"])
@login_required
def add_attachment(quote_id):
    _, user = _authorized_quote(quote_id)
    attachment = store_attachment(request.files.get("file"), required=True)
    try:
        with connection() as database:
            insert_attachment(database, quote_id, user["id"], attachment)
    except Exception:
        remove_attachment(attachment)
        raise
    flash("Adjunto cargado")
    return redirect(url_for("quotes.detail", quote_id=quote_id))


@bp.route("/quote/<int:quote_id>/attachments/<int:attachment_id>")
@login_required
def download_attachment(quote_id, attachment_id):
    _authorized_quote(quote_id)
    with connection() as database:
        attachment = database.execute(
            "SELECT * FROM quote_attachments WHERE id=? AND quote_id=?",
            (attachment_id, quote_id),
        ).fetchone()
    if not attachment:
        abort(404)
    return send_from_directory(
        current_app.config["UPLOAD_FOLDER"],
        attachment["stored_name"],
        as_attachment=True,
        download_name=attachment["original_name"],
        mimetype=attachment["mime_type"],
    )


@bp.route("/quote/<int:quote_id>/version/<int:version>")
@login_required
def version_detail(quote_id, version):
    quote, _ = _authorized_quote(quote_id)
    with connection() as database:
        snapshot = database.execute(
            """SELECT quote_versions.*,users.username FROM quote_versions
            LEFT JOIN users ON users.id=quote_versions.user_id
            WHERE quote_id=? AND version=?""",
            (quote_id, version),
        ).fetchone()
    if not snapshot:
        abort(404)
    return render_template(
        "version_detail.html",
        q=load_quote(quote),
        snapshot=snapshot,
        snapshot_data=json.loads(snapshot["data"]),
        snapshot_commercial=json.loads(snapshot["commercial_data"]),
        snapshot_items=json.loads(snapshot["items_data"]),
    )


@bp.route("/quote/<int:quote_id>/pdf")
@login_required
def quote_pdf(quote_id):
    quote, _ = _authorized_quote(quote_id)
    loaded_quote = load_quote(quote)
    template = Path(current_app.config["COMMERCIAL_OFFER_TEMPLATE"])
    if not template.is_file():
        abort(500, "No se encontró el formato oficial de oferta comercial")
    if sha256(template.read_bytes()).hexdigest() != COMMERCIAL_TEMPLATE_SHA256:
        abort(500, "El formato oficial fue modificado y requiere validación")
    with connection() as database:
        commercial = get_commercial(database, quote_id)
        items = get_items(database, quote_id)
    if not items:
        abort(400, "Agregue al menos una línea en el detalle comercial")
    offer_docx = build_offer_docx(loaded_quote, commercial, items, template)
    try:
        content = convert_docx_to_pdf(offer_docx)
    except PdfConversionError as error:
        current_app.logger.exception("No fue posible convertir la oferta a PDF")
        abort(503, str(error))
    return send_file(
        content,
        as_attachment=True,
        download_name=f"{quote['number']}-Oferta-Comercial.pdf",
        mimetype="application/pdf",
    )


@bp.route("/quote/<int:quote_id>/offer.docx")
@login_required
def quote_offer_docx(quote_id):
    quote, _ = _authorized_quote(quote_id)
    template = Path(current_app.config["COMMERCIAL_OFFER_TEMPLATE"])
    if not template.is_file():
        abort(500, "No se encontró el formato oficial de oferta comercial")
    if sha256(template.read_bytes()).hexdigest() != COMMERCIAL_TEMPLATE_SHA256:
        abort(500, "El formato oficial fue modificado y requiere validación")
    with connection() as database:
        commercial = get_commercial(database, quote_id)
        items = get_items(database, quote_id)
    if not items:
        abort(400, "Agregue al menos una línea en el detalle comercial")
    content = build_offer_docx(load_quote(quote), commercial, items, template)
    return send_file(
        content,
        as_attachment=True,
        download_name=f"{quote['number']}-Oferta-Comercial.docx",
        mimetype=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
    )
