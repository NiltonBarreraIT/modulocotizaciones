import secrets
from datetime import datetime, timedelta
from functools import wraps

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash

from .db import connection, iso, now

bp = Blueprint("auth", __name__)


def current_user():
    if "user_id" not in session:
        return None
    with connection() as database:
        return database.execute(
            """SELECT users.id,users.username,users.email,users.phone,
            COALESCE(NULLIF(trim(users.first_name || ' ' || users.last_name), ''),
                users.username) AS display_name,
            CASE WHEN quote_estimators.user_id IS NOT NULL
                THEN 'cotizador' ELSE users.role END AS role
            FROM users LEFT JOIN quote_estimators
            ON quote_estimators.user_id=users.id
            WHERE users.id=?""",
            (session["user_id"],),
        ).fetchone()


def login_required(function):
    @wraps(function)
    def inner(*args, **kwargs):
        if not current_user():
            return redirect(url_for("auth.login"))
        return function(*args, **kwargs)

    return inner


def admin_required(function):
    @wraps(function)
    def inner(*args, **kwargs):
        user = current_user()
        if not user:
            return redirect(url_for("auth.login"))
        if user["role"] != "admin":
            from flask import abort

            abort(403)
        return function(*args, **kwargs)

    return inner


def _login_key(username):
    ip = request.remote_addr or "unknown"
    return f"{ip}|{username.casefold()}"


def _login_locked(key):
    with connection() as database:
        row = database.execute(
            "SELECT locked_until FROM login_attempts WHERE key=?", (key,)
        ).fetchone()
    return bool(
        row
        and row["locked_until"]
        and now() < datetime.fromisoformat(row["locked_until"])
    )


def _record_login_failure(key):
    current_time = now()
    window = current_time - timedelta(
        minutes=current_app.config["LOGIN_WINDOW_MINUTES"]
    )
    with connection() as database:
        row = database.execute(
            "SELECT * FROM login_attempts WHERE key=?", (key,)
        ).fetchone()
        if not row or datetime.fromisoformat(row["first_failed_at"]) < window:
            failures = 1
            first_failed_at = iso(current_time)
        else:
            failures = row["failures"] + 1
            first_failed_at = row["first_failed_at"]
        locked_until = None
        if failures >= current_app.config["LOGIN_MAX_ATTEMPTS"]:
            locked_until = iso(
                current_time
                + timedelta(minutes=current_app.config["LOGIN_LOCK_MINUTES"])
            )
        database.execute(
            """INSERT INTO login_attempts(key,failures,first_failed_at,locked_until)
            VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET
            failures=excluded.failures,
            first_failed_at=excluded.first_failed_at,
            locked_until=excluded.locked_until""",
            (key, failures, first_failed_at, locked_until),
        )
    return locked_until


def _clear_login_failures(key):
    with connection() as database:
        database.execute("DELETE FROM login_attempts WHERE key=?", (key,))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        key = _login_key(username)
        lock_minutes = current_app.config["LOGIN_LOCK_MINUTES"]
        if _login_locked(key):
            flash(f"Demasiados intentos. Intente nuevamente en {lock_minutes} minutos.")
            return (
                render_template("login.html"),
                429,
                {"Retry-After": str(lock_minutes * 60)},
            )

        with connection() as database:
            user = database.execute(
                "SELECT * FROM users WHERE username=?", (username,)
            ).fetchone()
        if user and check_password_hash(
            user["password_hash"], request.form.get("password", "")
        ):
            _clear_login_failures(key)
            session.clear()
            session["user_id"] = user["id"]
            session["csrf"] = secrets.token_urlsafe(32)
            return redirect(url_for("quotes.index"))

        locked_until = _record_login_failure(key)
        flash(
            f"Demasiados intentos. Intente nuevamente en {lock_minutes} minutos."
            if locked_until
            else "Credenciales incorrectas"
        )
        if locked_until:
            return (
                render_template("login.html"),
                429,
                {"Retry-After": str(lock_minutes * 60)},
            )
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
