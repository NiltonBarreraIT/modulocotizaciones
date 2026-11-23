import mimetypes
import secrets
from pathlib import Path

from flask import abort, current_app
from werkzeug.utils import secure_filename

from .db import iso

ALLOWED_ATTACHMENTS = {
    ".pdf": "application/pdf",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def store_attachment(upload, *, required=False, allowed_extensions=None):
    if not upload or not upload.filename:
        if required:
            abort(400, "Debe seleccionar un archivo")
        return None

    original_name = secure_filename(upload.filename)
    if not original_name:
        abort(400, "El archivo no tiene un nombre válido")
    extension = Path(original_name).suffix.lower()
    permitted = set(allowed_extensions or ALLOWED_ATTACHMENTS)
    if extension not in ALLOWED_ATTACHMENTS or extension not in permitted:
        abort(400, "Tipo de archivo no permitido")

    stored_name = secrets.token_hex(24) + extension
    upload_folder = Path(current_app.config["UPLOAD_FOLDER"])
    upload_folder.mkdir(parents=True, exist_ok=True)
    target = upload_folder / stored_name
    upload.save(target)
    size = target.stat().st_size
    if size == 0 or size > current_app.config["ATTACHMENT_MAX_BYTES"]:
        target.unlink(missing_ok=True)
        abort(413, "El adjunto está vacío o excede el límite permitido")

    return {
        "original_name": original_name,
        "stored_name": stored_name,
        "mime_type": ALLOWED_ATTACHMENTS.get(extension)
        or mimetypes.guess_type(original_name)[0],
        "size": size,
    }


def insert_attachment(database, quote_id, user_id, attachment):
    cursor = database.execute(
        """INSERT INTO quote_attachments(
        quote_id,user_id,original_name,stored_name,mime_type,size,created_at
        ) VALUES(?,?,?,?,?,?,?)""",
        (
            quote_id,
            user_id,
            attachment["original_name"],
            attachment["stored_name"],
            attachment["mime_type"],
            attachment["size"],
            iso(),
        ),
    )
    return cursor.lastrowid


def remove_attachment(attachment):
    if attachment:
        target = Path(current_app.config["UPLOAD_FOLDER"]) / attachment["stored_name"]
        target.unlink(missing_ok=True)
