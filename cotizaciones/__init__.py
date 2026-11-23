import os
import secrets
from pathlib import Path

from flask import Flask, session

from . import admin, auth, commercial, dashboard, quotes
from .commercial_service import format_amount
from .constants import (
    FIELDS,
    REQUEST_STATUS_LABELS,
    SELLER_COMMERCIAL_STATUSES,
    STATUSES,
)
from .db import connection, migrate_database
from .formatting import format_local_date, format_local_datetime
from .security import register_security


def _env_int(name, default):
    return int(os.environ.get(name, str(default)))


def create_app(test_config=None):
    project_root = Path(__file__).resolve().parent.parent
    app = Flask(
        __name__,
        template_folder=str(project_root / "templates"),
    )
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
        DATABASE_URL=os.environ.get("DATABASE_URL"),
        DATABASE_PATH=os.environ.get(
            "DATABASE_PATH", str(project_root / "cotizaciones.db")
        ),
        UPLOAD_FOLDER=os.environ.get(
            "UPLOAD_FOLDER", str(project_root / "data" / "attachments")
        ),
        COMMERCIAL_OFFER_TEMPLATE=os.environ.get(
            "COMMERCIAL_OFFER_TEMPLATE",
            str(
                project_root
                / "resources"
                / "commercial_offer"
                / "formato_oferta_comercial_ITS.docx"
            ),
        ),
        MIGRATIONS_PATH=str(project_root / "migrations"),
        POSTGRES_MIGRATIONS_PATH=str(project_root / "migrations_postgresql"),
        ADMIN_USER=os.environ.get("ADMIN_USER", "admin"),
        ADMIN_PASSWORD=os.environ.get("ADMIN_PASSWORD"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
        MAX_CONTENT_LENGTH=20 * 1024 * 1024,
        LOGIN_MAX_ATTEMPTS=_env_int("LOGIN_MAX_ATTEMPTS", 5),
        LOGIN_WINDOW_MINUTES=_env_int("LOGIN_WINDOW_MINUTES", 15),
        LOGIN_LOCK_MINUTES=_env_int("LOGIN_LOCK_MINUTES", 15),
        IMPORT_MAX_ROWS=_env_int("IMPORT_MAX_ROWS", 5000),
        IMPORT_PREVIEW_ROWS=_env_int("IMPORT_PREVIEW_ROWS", 100),
        IMPORT_BATCH_HOURS=_env_int("IMPORT_BATCH_HOURS", 24),
        QUOTE_PAGE_SIZE=_env_int("QUOTE_PAGE_SIZE", 25),
        ASSIGNMENT_TARGET_HOURS=_env_int("ASSIGNMENT_TARGET_HOURS", 24),
        ATTACHMENT_MAX_BYTES=_env_int("ATTACHMENT_MAX_BYTES", 10 * 1024 * 1024),
        MAX_XLSX_UNCOMPRESSED=_env_int("MAX_XLSX_UNCOMPRESSED", 100 * 1024 * 1024),
        DISPLAY_TIMEZONE=os.environ.get("DISPLAY_TIMEZONE", "America/Santiago"),
    )
    if test_config:
        app.config.update(test_config)
        if "DATABASE_PATH" in test_config and "DATABASE_URL" not in test_config:
            app.config["DATABASE_URL"] = None

    register_security(app)
    app.add_template_filter(format_local_datetime, "local_datetime")
    app.add_template_filter(format_local_date, "local_date")
    app.register_blueprint(auth.bp)
    app.register_blueprint(quotes.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(commercial.bp)
    app.register_blueprint(dashboard.bp)

    @app.context_processor
    def inject_template_context():
        user = auth.current_user()
        pending_approval_count = 0
        platform_revision = 0
        if user:
            with connection() as database:
                platform_revision = database.execute(
                    "SELECT COALESCE(MAX(id), 0) FROM audit"
                ).fetchone()[0]
        if user and user["role"] == "admin":
            with connection() as database:
                pending_approval_count = database.execute(
                    "SELECT count(*) FROM quotes WHERE workflow_status='en_revision'"
                ).fetchone()[0]
        return {
            "me": user,
            "fields": FIELDS,
            "statuses": STATUSES,
            "request_status_labels": REQUEST_STATUS_LABELS,
            "seller_commercial_statuses": SELLER_COMMERCIAL_STATUSES,
            "format_amount": format_amount,
            "csrf": session.get("csrf", ""),
            "pending_approval_count": pending_approval_count,
            "platform_revision": platform_revision,
        }

    with app.app_context():
        migrate_database()
    return app
