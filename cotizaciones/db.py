import re
import sqlite3
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from flask import current_app
from werkzeug.security import generate_password_hash

try:
    import psycopg
except ImportError:  # SQLite development and tests can still start without it.
    psycopg = None


INTEGRITY_ERRORS = (sqlite3.IntegrityError,)
if psycopg is not None:
    INTEGRITY_ERRORS += (psycopg.IntegrityError,)

_POSTGRES_LOCK_ID = 4_921_847_337
_ID_TABLES = {
    "audit",
    "catalog_entries",
    "quote_attachments",
    "quote_comments",
    "quote_deliveries",
    "quote_items",
    "quote_versions",
    "quotes",
    "users",
}


def now():
    return datetime.now(UTC)


def iso(value=None):
    return (value or now()).isoformat(timespec="seconds")


def _replace_qmark_parameters(sql):
    """Convert DB-API qmark placeholders without touching quoted question marks."""
    result = []
    quote = None
    index = 0
    while index < len(sql):
        character = sql[index]
        if quote:
            result.append(character)
            if character == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    result.append(sql[index + 1])
                    index += 1
                else:
                    quote = None
        elif character in ("'", '"'):
            quote = character
            result.append(character)
        elif character == "?":
            result.append("%s")
        else:
            result.append(character)
        index += 1
    return "".join(result)


def postgres_sql(sql):
    """Translate the small SQLite SQL surface used by the application."""
    translated = re.sub(
        r"json_extract\(\s*([^,]+?)\s*,\s*'\$\.([A-Za-z0-9_]+)'\s*\)",
        r"(\1::jsonb ->> '\2')",
        sql,
        flags=re.IGNORECASE,
    )
    translated = re.sub(r"\s+COLLATE\s+NOCASE\b", "", translated, flags=re.I)
    return _replace_qmark_parameters(translated)


class CompatRow(Mapping):
    """Mapping row that also supports the numeric indexing used by sqlite3.Row."""

    def __init__(self, columns, values):
        self._columns = tuple(columns)
        self._values = tuple(values)
        self._positions = {name: index for index, name in enumerate(self._columns)}

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return self._values[self._positions[key]]

    def __iter__(self):
        return iter(self._columns)

    def __len__(self):
        return len(self._columns)


class CompatCursor:
    def __init__(self, cursor, *, postgres=False):
        self._cursor = cursor
        self._postgres = postgres
        self._lastrowid = None

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def lastrowid(self):
        if not self._postgres:
            return self._cursor.lastrowid
        if self._lastrowid is None:
            row = self._cursor.fetchone()
            self._lastrowid = row[0] if row else None
        return self._lastrowid

    def _convert(self, row):
        if row is None or not self._postgres:
            return row
        columns = [column.name for column in self._cursor.description]
        return CompatRow(columns, row)

    def fetchone(self):
        return self._convert(self._cursor.fetchone())

    def fetchall(self):
        return [self._convert(row) for row in self._cursor.fetchall()]

    def __iter__(self):
        while True:
            row = self.fetchone()
            if row is None:
                return
            yield row


class Database:
    def __init__(self, raw_connection, engine):
        self.raw_connection = raw_connection
        self.engine = engine

    def _postgres_statement(self, sql):
        if sql.strip().upper() == "BEGIN IMMEDIATE":
            return f"SELECT pg_advisory_xact_lock({_POSTGRES_LOCK_ID})"

        translated = postgres_sql(sql)
        insert = re.match(
            r"\s*INSERT\s+INTO\s+([A-Za-z_][A-Za-z0-9_]*)\b",
            translated,
            flags=re.IGNORECASE,
        )
        if (
            insert
            and insert.group(1).lower() in _ID_TABLES
            and not re.search(r"\bRETURNING\b", translated, flags=re.IGNORECASE)
        ):
            translated = translated.rstrip().rstrip(";") + " RETURNING id"
        return translated

    def execute(self, sql, parameters=()):
        if self.engine == "sqlite":
            return CompatCursor(self.raw_connection.execute(sql, parameters))
        cursor = self.raw_connection.cursor()
        statement = self._postgres_statement(sql)
        if parameters:
            cursor.execute(statement, parameters)
        else:
            cursor.execute(statement)
        return CompatCursor(cursor, postgres=True)

    def executemany(self, sql, parameters):
        if self.engine == "sqlite":
            return CompatCursor(self.raw_connection.executemany(sql, parameters))
        cursor = self.raw_connection.cursor()
        cursor.executemany(postgres_sql(sql), parameters)
        return CompatCursor(cursor, postgres=True)

    def executescript(self, script):
        if self.engine == "sqlite":
            return self.raw_connection.executescript(script)
        cursor = self.raw_connection.cursor()
        cursor.execute(script)
        return CompatCursor(cursor, postgres=True)


def database_engine():
    return "postgresql" if current_app.config.get("DATABASE_URL") else "sqlite"


@contextmanager
def connection():
    engine = database_engine()
    if engine == "postgresql":
        if psycopg is None:
            raise RuntimeError(
                "PostgreSQL requiere instalar psycopg: pip install -r requirements.txt"
            )
        raw_database = psycopg.connect(
            current_app.config["DATABASE_URL"],
            connect_timeout=10,
            application_name="its-cotizaciones",
        )
    else:
        raw_database = sqlite3.connect(current_app.config["DATABASE_PATH"], timeout=30)
        raw_database.row_factory = sqlite3.Row
        raw_database.execute("PRAGMA foreign_keys=ON")
        raw_database.execute("PRAGMA busy_timeout=5000")

    database = Database(raw_database, engine)
    try:
        yield database
        raw_database.commit()
    except Exception:
        raw_database.rollback()
        raise
    finally:
        raw_database.close()


def _migration_files():
    setting = (
        "POSTGRES_MIGRATIONS_PATH"
        if database_engine() == "postgresql"
        else "MIGRATIONS_PATH"
    )
    path = Path(current_app.config[setting])
    return sorted(path.glob("[0-9][0-9][0-9]_*.sql"))


def _ensure_migration_table(database):
    database.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations(
        version TEXT PRIMARY KEY,
        applied_at TEXT NOT NULL
        )"""
    )


def migrate_database():
    engine = database_engine()
    if engine == "sqlite":
        database_path = Path(current_app.config["DATABASE_PATH"])
        database_path.parent.mkdir(parents=True, exist_ok=True)

    with connection() as database:
        _ensure_migration_table(database)
        if engine == "postgresql":
            database.execute("BEGIN IMMEDIATE")

        for migration in _migration_files():
            version = migration.stem
            applied = database.execute(
                "SELECT 1 FROM schema_migrations WHERE version=?", (version,)
            ).fetchone()
            if applied:
                continue
            script = migration.read_text(encoding="utf-8")
            if engine == "sqlite":
                escaped_version = version.replace("'", "''")
                database.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + script
                    + "\nINSERT OR IGNORE INTO schema_migrations(version,applied_at) "
                    + f"VALUES('{escaped_version}','{iso()}');\nCOMMIT;"
                )
            else:
                database.executescript(script)
                database.execute(
                    """INSERT INTO schema_migrations(version,applied_at)
                    VALUES(?,?) ON CONFLICT(version) DO NOTHING""",
                    (version, iso()),
                )

    with connection() as database:
        if database.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            return
        password = current_app.config.get("ADMIN_PASSWORD")
        if not password:
            raise RuntimeError(
                "Establezca ADMIN_PASSWORD antes de iniciar por primera vez."
            )
        database.execute(
            "INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
            (
                current_app.config.get("ADMIN_USER", "admin"),
                generate_password_hash(password),
                "admin",
            ),
        )
