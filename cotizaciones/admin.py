import json
import os
import re
import secrets
import unicodedata
from datetime import datetime, timedelta
from zipfile import ZipFile

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from werkzeug.exceptions import HTTPException
from werkzeug.security import generate_password_hash

from .auth import admin_required, current_user
from .catalog_service import CATALOG_TYPES
from .commercial_service import snapshot_quote
from .constants import STATUSES
from .db import INTEGRITY_ERRORS, connection, iso, now
from .numbering import bump_correlative, quote_number, shift_solution_conflicts
from .quotes import quote_fingerprint, validate_quote_data

bp = Blueprint("admin", __name__)


def _catalog_value():
    value = " ".join(request.form.get("value", "").split())
    if not value or len(value) > 120:
        abort(400, "El nombre es obligatorio y admite hasta 120 caracteres")
    return value


def _audit_catalog(database, user_id, action, before, after):
    database.execute(
        """INSERT INTO audit(quote_id,user_id,action,at,before,after)
        VALUES(NULL,?,?,?,?,?)""",
        (
            user_id,
            action,
            iso(),
            json.dumps(before, ensure_ascii=False) if before else None,
            json.dumps(after, ensure_ascii=False),
        ),
    )


def normalize_header(value):
    text = (
        unicodedata.normalize("NFKD", str(value or ""))
        .encode("ascii", "ignore")
        .decode()
        .casefold()
    )
    return " ".join(
        "".join(character if character.isalnum() else " " for character in text).split()
    )


def normalize_import_status(value):
    """Convert legacy spreadsheet labels to the platform's canonical statuses."""
    normalized_value = normalize_header(value)
    if not normalized_value:
        return ""

    aliases = {normalize_header(status): status for status in STATUSES}
    aliases.update(
        {
            "abierto": "Abierta",
            "enviado": "Enviada",
            "adjudicado": "Adjudicada",
            "perdido": "Perdida",
            "postergado": "Postergada",
            "anulado": "Anulada",
        }
    )
    return aliases.get(normalized_value)


def _legacy_correlative(value):
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number <= 0 or str(value).strip() not in (str(number), f"{number}.0"):
        return None
    return number


def _has_business_data(row):
    relevant_columns = (0, 1, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 18)
    return any(
        row[index] is not None and str(row[index]).strip() for index in relevant_columns
    )


def _existing_fingerprints(database):
    fingerprints = set()
    for row in database.execute("SELECT data,fingerprint FROM quotes"):
        fingerprints.add(
            row["fingerprint"] or quote_fingerprint(json.loads(row["data"]))
        )
    return fingerprints


def _parse_upload(upload):
    from openpyxl import load_workbook

    if os.path.splitext(upload.filename or "")[1].lower() != ".xlsx":
        abort(400, "Solo se aceptan archivos .xlsx")

    workbook = None
    try:
        try:
            upload.stream.seek(0)
            with ZipFile(upload.stream) as archive:
                uncompressed_size = sum(item.file_size for item in archive.infolist())
                if uncompressed_size > current_app.config["MAX_XLSX_UNCOMPRESSED"]:
                    abort(
                        413,
                        "El contenido descomprimido del Excel "
                        "excede el límite permitido",
                    )
            upload.stream.seek(0)
            workbook = load_workbook(upload.stream, read_only=True, data_only=True)
        except HTTPException:
            raise
        except Exception:
            abort(400, "El archivo Excel está dañado o no es un .xlsx válido")

        sheet = workbook.active
        if "COTIZACIONES" in workbook.sheetnames:
            sheet = workbook["COTIZACIONES"]
        header = next(
            sheet.iter_rows(min_row=1, max_row=1, max_col=19, values_only=True),
            None,
        )
        normalized = [normalize_header(value) for value in (header or ())]
        if len(normalized) < 19 or sum(bool(value) for value in normalized) < 10:
            abort(
                400,
                "El Excel no contiene la estructura esperada de 19 columnas A–S",
            )
        if "cliente" not in normalized[11] or not any(
            word in normalized[12] for word in ("descripcion", "detalle")
        ):
            abort(
                400,
                "Las columnas L y M deben corresponder a cliente y descripción",
            )
        if normalized[2] not in ("n", "no", "numero", "n cotizacion"):
            abort(400, "La columna C debe corresponder al N° de cotización")

        mapping = {
            "solicitud": 0,
            "vendedor": 4,
            "fecha": 3,
            "deteccion": 5,
            "levantamiento": 6,
            "propuesta": 7,
            "fabrica": 8,
            "area": 9,
            "rfq": 10,
            "cliente": 11,
            "descripcion": 12,
            "clp": 13,
            "usd": 14,
            "eur": 15,
            "margen": 16,
            "utilidad_usd": 17,
            "estado": 18,
        }
        rows = []
        for row_number, row in enumerate(
            sheet.iter_rows(min_row=2, max_col=19, values_only=True), start=2
        ):
            if row_number - 1 > current_app.config["IMPORT_MAX_ROWS"]:
                abort(
                    413,
                    f"El archivo supera el máximo de "
                    f"{current_app.config['IMPORT_MAX_ROWS']} filas",
                )
            if not _has_business_data(row):
                continue
            legacy_number = _legacy_correlative(row[2])
            data = {
                key: (
                    ""
                    if row[index] is None
                    else (
                        row[index].date().isoformat()
                        if isinstance(row[index], datetime)
                        else str(row[index]).strip()
                    )
                )
                for key, index in mapping.items()
            }
            raw_status = data["estado"]
            normalized_status = normalize_import_status(raw_status)
            validation_error = None
            if legacy_number is None:
                validation_error = "N° de cotización inválido en la columna C"
            elif normalized_status is None:
                validation_error = f"Estado de cotización no reconocido: {raw_status!r}"
            else:
                data["estado"] = normalized_status
                try:
                    validate_quote_data(data)
                except HTTPException as error:
                    if error.code != 400:
                        raise
                    validation_error = error.description
            rows.append(
                {
                    "data": data,
                    "legacy_number": legacy_number,
                    "row_number": row_number,
                    "error": validation_error,
                }
            )
        if not rows:
            abort(400, "El archivo no contiene cotizaciones para importar")
        return rows
    finally:
        if workbook:
            workbook.close()


def _load_batch(batch_id, user_id):
    with connection() as database:
        batch = database.execute(
            "SELECT * FROM import_batches WHERE id=? AND user_id=?",
            (batch_id, user_id),
        ).fetchone()
    if not batch:
        abort(404)
    return batch


def _profile_form():
    first_name = request.form.get("first_name", "").strip()
    last_name = request.form.get("last_name", "").strip()
    email = request.form.get("email", "").strip().casefold()
    phone = request.form.get("phone", "").strip()
    if not first_name or len(first_name) > 100:
        abort(400, "Nombre obligatorio de hasta 100 caracteres")
    if not last_name or len(last_name) > 100:
        abort(400, "Apellido obligatorio de hasta 100 caracteres")
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 254:
        abort(400, "Correo electrónico inválido")
    if not re.fullmatch(r"[+0-9() .-]{6,30}", phone):
        abort(400, "Número de teléfono inválido")
    return first_name, last_name, email, phone


def _set_user_role(database, user_id, role, actor_id):
    if role not in ("admin", "usuario", "cotizador"):
        abort(400, "Rol inválido")
    if user_id == actor_id and role != "admin":
        abort(400, "El administrador conectado no puede quitarse su propio rol")
    target = database.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone()
    if not target:
        abort(404)
    if role != "cotizador":
        active_assignments = database.execute(
            """SELECT count(*) FROM quotes WHERE estimator_id=?
            AND request_status='en_cotizacion'""",
            (user_id,),
        ).fetchone()[0]
        if active_assignments:
            abort(400, "Reasigne las solicitudes activas antes de cambiar este rol")
    database.execute("DELETE FROM quote_estimators WHERE user_id=?", (user_id,))
    database.execute(
        "UPDATE users SET role=? WHERE id=?",
        ("usuario" if role == "cotizador" else role, user_id),
    )
    if role == "cotizador":
        database.execute("INSERT INTO quote_estimators(user_id) VALUES(?)", (user_id,))


@bp.route("/users", methods=["GET", "POST"])
@admin_required
def users():
    if request.method == "POST":
        first_name, last_name, email, phone = _profile_form()
        password = request.form.get("password", "")
        role = request.form.get("role", "usuario")
        if len(password) < 12 or role not in (
            "admin",
            "usuario",
            "cotizador",
        ):
            abort(400, "Usuario obligatorio y contraseña de 12 caracteres mínimo")
        try:
            with connection() as database:
                cursor = database.execute(
                    """INSERT INTO users(
                    username,password_hash,role,first_name,last_name,email,phone,
                    must_change_password
                    ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        email,
                        generate_password_hash(password),
                        "usuario" if role == "cotizador" else role,
                        first_name,
                        last_name,
                        email,
                        phone,
                        1,
                    ),
                )
                if role == "cotizador":
                    database.execute(
                        "INSERT INTO quote_estimators(user_id) VALUES(?)",
                        (cursor.lastrowid,),
                    )
            flash("Usuario creado")
        except INTEGRITY_ERRORS:
            flash("Ya existe un usuario con ese correo")
    with connection() as database:
        rows = database.execute(
            """SELECT users.id,users.username,users.first_name,users.last_name,
            users.email,users.phone,users.must_change_password,
            CASE WHEN quote_estimators.user_id IS NOT NULL
                THEN 'cotizador' ELSE users.role END AS role
            FROM users LEFT JOIN quote_estimators
            ON quote_estimators.user_id=users.id ORDER BY users.id"""
        ).fetchall()
    return render_template("users.html", users=rows)


@bp.route("/users/<int:user_id>/edit")
@admin_required
def edit_user(user_id):
    with connection() as database:
        user = database.execute(
            """SELECT users.id,users.username,users.first_name,users.last_name,
            users.email,users.phone,users.must_change_password,
            CASE WHEN quote_estimators.user_id IS NOT NULL
                THEN 'cotizador' ELSE users.role END AS role
            FROM users LEFT JOIN quote_estimators
            ON quote_estimators.user_id=users.id WHERE users.id=?""",
            (user_id,),
        ).fetchone()
    if not user:
        abort(404)
    return render_template("user_edit.html", edited_user=user)


@bp.route("/users/<int:user_id>/role", methods=["POST"])
@admin_required
def update_user_role(user_id):
    role = request.form.get("role", "")
    user = current_user()
    with connection() as database:
        _set_user_role(database, user_id, role, user["id"])
    flash("Rol actualizado")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:user_id>/profile", methods=["POST"])
@admin_required
def update_user_profile(user_id):
    first_name, last_name, email, phone = _profile_form()
    role = request.form.get("role", "")
    actor = current_user()
    try:
        with connection() as database:
            _set_user_role(database, user_id, role, actor["id"])
            database.execute(
                """UPDATE users SET username=?,first_name=?,last_name=?,email=?,phone=?
                WHERE id=?""",
                (email, first_name, last_name, email, phone, user_id),
            )
        flash("Perfil actualizado")
    except INTEGRITY_ERRORS:
        flash("Ya existe un usuario con ese correo")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:user_id>/password", methods=["POST"])
@admin_required
def update_user_password(user_id):
    password = request.form.get("password", "")
    confirmation = request.form.get("password_confirmation", "")
    if len(password) < 12:
        abort(400, "La contraseña temporal debe tener al menos 12 caracteres")
    if password != confirmation:
        abort(400, "Las contraseñas no coinciden")
    with connection() as database:
        target = database.execute(
            "SELECT id FROM users WHERE id=?", (user_id,)
        ).fetchone()
        if not target:
            abort(404)
        database.execute(
            """UPDATE users SET password_hash=?,must_change_password=1
            WHERE id=?""",
            (generate_password_hash(password), user_id),
        )
    flash("Contraseña temporal actualizada. El usuario deberá cambiarla al ingresar.")
    return redirect(url_for("admin.edit_user", user_id=user_id))


@bp.route("/catalogs", methods=["GET", "POST"])
@admin_required
def catalogs():
    user = current_user()
    if request.method == "POST":
        kind = request.form.get("kind", "")
        if kind not in CATALOG_TYPES:
            abort(400, "Tipo de catálogo inválido")
        value = _catalog_value()
        try:
            with connection() as database:
                cursor = database.execute(
                    """INSERT INTO catalog_entries(
                    kind,value,active,created_at,updated_at
                    ) VALUES(?,?,1,?,?)""",
                    (kind, value, iso(), iso()),
                )
                _audit_catalog(
                    database,
                    user["id"],
                    "crear_catalogo",
                    None,
                    {"id": cursor.lastrowid, "tipo": kind, "valor": value},
                )
            flash(f"{CATALOG_TYPES[kind][:-1]} agregada")
        except INTEGRITY_ERRORS:
            flash("Esa opción ya existe en el catálogo")
        return redirect(url_for("admin.catalogs"))

    with connection() as database:
        rows = database.execute(
            """SELECT id,kind,value,active FROM catalog_entries
            ORDER BY kind,active DESC,value COLLATE NOCASE"""
        ).fetchall()
    grouped = {kind: [] for kind in CATALOG_TYPES}
    for row in rows:
        grouped[row["kind"]].append(row)
    return render_template(
        "catalogs.html", catalog_types=CATALOG_TYPES, catalogs=grouped
    )


@bp.post("/catalogs/<int:entry_id>")
@admin_required
def update_catalog(entry_id):
    value = _catalog_value()
    active = 1 if request.form.get("active") == "1" else 0
    user = current_user()
    try:
        with connection() as database:
            current = database.execute(
                "SELECT id,kind,value,active FROM catalog_entries WHERE id=?",
                (entry_id,),
            ).fetchone()
            if not current:
                abort(404)
            database.execute(
                """UPDATE catalog_entries SET value=?,active=?,updated_at=?
                WHERE id=?""",
                (value, active, iso(), entry_id),
            )
            _audit_catalog(
                database,
                user["id"],
                "editar_catalogo",
                {"valor": current["value"], "activo": current["active"]},
                {
                    "id": entry_id,
                    "tipo": current["kind"],
                    "valor": value,
                    "activo": active,
                },
            )
        flash("Catálogo actualizado")
    except INTEGRITY_ERRORS:
        flash("Esa opción ya existe en el catálogo")
    return redirect(url_for("admin.catalogs"))


@bp.route("/import", methods=["GET", "POST"])
@admin_required
def import_excel():
    if request.method == "POST":
        upload = request.files.get("file")
        if not upload:
            abort(400, "Debe seleccionar un archivo")
        parsed_rows = _parse_upload(upload)
        user = current_user()
        batch_id = secrets.token_urlsafe(24)
        cutoff = iso(now() - timedelta(hours=current_app.config["IMPORT_BATCH_HOURS"]))
        with connection() as database:
            database.execute("DELETE FROM import_batches WHERE created_at<?", (cutoff,))
            existing = _existing_fingerprints(database)
            existing_quotes = {
                row["fingerprint"] or quote_fingerprint(json.loads(row["data"])): row
                for row in database.execute(
                    "SELECT id,number,correlative,data,fingerprint FROM quotes"
                )
            }
            seen = set()
            seen_numbers = set()
            staged_rows = []
            for parsed in parsed_rows:
                data = parsed["data"]
                legacy_number = parsed["legacy_number"]
                error = parsed["error"]
                number_conflict = False
                preliminary_fingerprint = (
                    quote_fingerprint(data) if legacy_number is not None else ""
                )
                repeated_content = preliminary_fingerprint in seen
                if legacy_number in seen_numbers and not repeated_content:
                    duplicate_error = f"N° {legacy_number} repetido dentro del archivo"
                    error = f"{error}; {duplicate_error}" if error else duplicate_error
                    number_conflict = True
                invalid = bool(error)
                fingerprint = preliminary_fingerprint
                existing_quote = existing_quotes.get(fingerprint)
                duplicate_in_file = repeated_content
                expected_number = (
                    quote_number("LEG", legacy_number) if legacy_number else ""
                )
                staged_rows.append(
                    {
                        "data": data,
                        "legacy_number": legacy_number,
                        "fingerprint": fingerprint,
                        "duplicate": not invalid
                        and (fingerprint in existing or duplicate_in_file),
                        "duplicate_in_file": duplicate_in_file,
                        "number_conflict": number_conflict,
                        "reconcile": bool(
                            existing_quote
                            and not duplicate_in_file
                            and not number_conflict
                            and existing_quote["number"] != expected_number
                        ),
                        "invalid": invalid,
                        "error": error,
                        "row_number": parsed["row_number"],
                    }
                )
                if fingerprint:
                    seen.add(fingerprint)
                if legacy_number is not None:
                    seen_numbers.add(legacy_number)
            database.execute(
                """INSERT INTO import_batches(
                id,user_id,created_at,source_name,rows
                ) VALUES(?,?,?,?,?)""",
                (
                    batch_id,
                    user["id"],
                    iso(),
                    (upload.filename or "cotizaciones.xlsx")[:255],
                    json.dumps(staged_rows, ensure_ascii=False),
                ),
            )
        return redirect(url_for("admin.import_preview", batch_id=batch_id))
    return render_template("import.html")


@bp.route("/import/preview/<batch_id>")
@admin_required
def import_preview(batch_id):
    user = current_user()
    batch = _load_batch(batch_id, user["id"])
    rows = json.loads(batch["rows"])
    duplicates = sum(row["duplicate"] for row in rows)
    reconciliations = sum(row.get("reconcile", False) for row in rows)
    invalid = sum(row.get("invalid", False) for row in rows)
    return render_template(
        "import_preview.html",
        batch=batch,
        batch_rows=rows[: current_app.config["IMPORT_PREVIEW_ROWS"]],
        total=len(rows),
        duplicates=duplicates,
        reconciliations=reconciliations,
        invalid=invalid,
        importable=len(rows) - duplicates - invalid,
    )


@bp.route("/import/confirm/<batch_id>", methods=["POST"])
@admin_required
def import_confirm(batch_id):
    user = current_user()
    timestamp = iso()
    imported = 0
    skipped = 0
    invalid = 0
    reconciled = 0
    conflicts = 0
    shifted = 0
    with connection() as database:
        database.execute("BEGIN IMMEDIATE")
        batch = database.execute(
            "SELECT * FROM import_batches WHERE id=? AND user_id=?",
            (batch_id, user["id"]),
        ).fetchone()
        if not batch:
            abort(404)
        staged_rows = json.loads(batch["rows"])
        reserved_numbers = {
            staged["legacy_number"]
            for staged in staged_rows
            if staged.get("legacy_number") is not None
            and not staged.get("number_conflict", False)
            and not staged.get("duplicate_in_file", False)
        }
        shifted = shift_solution_conflicts(database, reserved_numbers)
        existing_quotes = {
            row["fingerprint"] or quote_fingerprint(json.loads(row["data"])): row
            for row in database.execute(
                "SELECT id,number,correlative,data,fingerprint FROM quotes"
            )
        }
        candidates = [
            (staged, existing_quotes.get(staged["fingerprint"]))
            for staged in staged_rows
            if not staged.get("duplicate_in_file", False)
            and not staged.get("number_conflict", False)
            and staged.get("reconcile", False)
            and existing_quotes.get(staged["fingerprint"])
        ]
        candidate_ids = {quote["id"] for _, quote in candidates}
        reconciliation_targets = {
            quote["id"]: staged["legacy_number"] for staged, quote in candidates
        }
        dependencies = {}
        displacements = {}
        for staged, quote in candidates:
            occupant = database.execute(
                "SELECT id,number,correlative FROM quotes WHERE correlative=?",
                (staged["legacy_number"],),
            ).fetchone()
            dependency = occupant["id"] if occupant else None
            if (
                occupant
                and occupant["id"] not in candidate_ids
                and occupant["number"].startswith("ITS-LEG-")
                and quote["correlative"] is not None
            ):
                displacements[occupant["id"]] = (
                    quote["number"],
                    quote["correlative"],
                    occupant["number"],
                )
                dependency = None
            dependencies[quote["id"]] = dependency

        safe_ids = {
            quote_id
            for quote_id, dependency in dependencies.items()
            if dependency is None or dependency in candidate_ids
        }
        changed = True
        while changed:
            changed = False
            for quote_id in tuple(safe_ids):
                dependency = dependencies[quote_id]
                if dependency is not None and dependency not in safe_ids:
                    safe_ids.remove(quote_id)
                    changed = True
        safe_reconciliations = {
            quote_id: reconciliation_targets[quote_id] for quote_id in safe_ids
        }

        temporary_ids = set(safe_reconciliations) | set(displacements)
        for quote_id in temporary_ids:
            database.execute(
                "UPDATE quotes SET number=?,correlative=NULL WHERE id=?",
                (f"TEMP-RECON-{quote_id}", quote_id),
            )
        for quote_id, (number, correlative, previous_number) in displacements.items():
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
                    "liberar_correlativo_legacy",
                    timestamp,
                    json.dumps({"number": previous_number}, ensure_ascii=False),
                    json.dumps({"number": number}, ensure_ascii=False),
                ),
            )

        for staged in staged_rows:
            if staged.get("duplicate_in_file", False):
                skipped += 1
                continue
            fingerprint = staged["fingerprint"]
            existing_quote = existing_quotes.get(fingerprint)
            legacy_number = staged["legacy_number"]
            can_reconcile = bool(
                existing_quote and existing_quote["id"] in safe_reconciliations
            )
            if staged.get("invalid", False) and not can_reconcile:
                invalid += 1
                continue
            expected_number = quote_number("LEG", legacy_number)
            if existing_quote:
                if can_reconcile:
                    occupant = database.execute(
                        "SELECT id FROM quotes WHERE correlative=?",
                        (legacy_number,),
                    ).fetchone()
                    if occupant and occupant["id"] != existing_quote["id"]:
                        old_correlative = existing_quote["correlative"]
                        old_available = (
                            old_correlative is not None
                            and not database.execute(
                                "SELECT 1 FROM quotes WHERE correlative=?",
                                (old_correlative,),
                            ).fetchone()
                        )
                        database.execute(
                            "UPDATE quotes SET number=?,correlative=? WHERE id=?",
                            (
                                existing_quote["number"]
                                if old_available
                                else f"ITS-LEG-PEND-{existing_quote['id']}",
                                old_correlative if old_available else None,
                                existing_quote["id"],
                            ),
                        )
                        conflicts += 1
                        skipped += 1
                        continue
                    database.execute(
                        "UPDATE quotes SET number=?,correlative=? WHERE id=?",
                        (expected_number, legacy_number, existing_quote["id"]),
                    )
                    database.execute(
                        """INSERT INTO audit(
                        quote_id,user_id,action,at,before,after
                        ) VALUES(?,?,?,?,?,?)""",
                        (
                            existing_quote["id"],
                            user["id"],
                            "reconciliar_numero",
                            timestamp,
                            json.dumps(
                                {"number": existing_quote["number"]},
                                ensure_ascii=False,
                            ),
                            json.dumps({"number": expected_number}, ensure_ascii=False),
                        ),
                    )
                    reconciled += 1
                    if staged.get("invalid", False):
                        invalid += 1
                else:
                    if existing_quote["number"] != expected_number:
                        conflicts += 1
                    skipped += 1
                continue
            data = staged["data"]
            serialized = json.dumps(data, ensure_ascii=False)
            old_timestamp = iso(now() - timedelta(hours=25))
            if database.execute(
                "SELECT 1 FROM quotes WHERE correlative=?", (legacy_number,)
            ).fetchone():
                conflicts += 1
                skipped += 1
                continue
            quote_id = database.execute(
                """INSERT INTO quotes(
                number,correlative,owner_id,created_at,last_user_edit_at,data,fingerprint
                ) VALUES(?,?,?,?,?,?,?)""",
                (
                    expected_number,
                    legacy_number,
                    user["id"],
                    timestamp,
                    old_timestamp,
                    serialized,
                    fingerprint,
                ),
            ).lastrowid
            database.execute(
                """INSERT INTO audit(
                quote_id,user_id,action,at,before,after
                ) VALUES(?,?,?,?,?,?)""",
                (
                    quote_id,
                    user["id"],
                    "importar",
                    timestamp,
                    None,
                    serialized,
                ),
            )
            snapshot_quote(database, quote_id, user["id"])
            existing_quotes[fingerprint] = database.execute(
                """SELECT id,number,correlative,data,fingerprint
                FROM quotes WHERE id=?""",
                (quote_id,),
            ).fetchone()
            imported += 1
        bump_correlative(database, max(reserved_numbers, default=0))
        database.execute("DELETE FROM import_batches WHERE id=?", (batch_id,))
    flash(
        f"Se importaron {imported} cotizaciones y se reconciliaron "
        f"{reconciled} números; {skipped} duplicadas, {invalid} inválidas y "
        f"{conflicts} conflictos se omitieron. "
        f"Se renumeraron {shifted} solicitudes nuevas para mantener el correlativo."
    )
    return redirect(url_for("quotes.index"))


@bp.route("/import/cancel/<batch_id>", methods=["POST"])
@admin_required
def import_cancel(batch_id):
    user = current_user()
    with connection() as database:
        database.execute(
            "DELETE FROM import_batches WHERE id=? AND user_id=?",
            (batch_id, user["id"]),
        )
    flash("Importación cancelada")
    return redirect(url_for("admin.import_excel"))
