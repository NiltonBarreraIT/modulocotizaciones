"""Copy an existing ITS SQLite database into an empty PostgreSQL database."""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

import psycopg
from psycopg import sql

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SQLITE = PROJECT_ROOT / "cotizaciones.db"
POSTGRES_SCHEMA = PROJECT_ROOT / "migrations_postgresql" / "001_initial.sql"

TABLES = (
    "users",
    "login_attempts",
    "quotes",
    "audit",
    "import_batches",
    "quote_estimators",
    "quote_commercial",
    "quote_items",
    "quote_versions",
    "quote_comments",
    "quote_attachments",
    "quote_deliveries",
    "quote_number_sequence",
    "catalog_entries",
)

IDENTITY_TABLES = (
    "users",
    "quotes",
    "audit",
    "quote_items",
    "quote_versions",
    "quote_comments",
    "quote_attachments",
    "quote_deliveries",
    "catalog_entries",
)


def arguments():
    parser = argparse.ArgumentParser(
        description=(
            "Migra cotizaciones.db a una base PostgreSQL vacía y verifica todos "
            "los registros copiados."
        )
    )
    parser.add_argument(
        "--sqlite",
        type=Path,
        default=DEFAULT_SQLITE,
        help=f"Base SQLite de origen (predeterminado: {DEFAULT_SQLITE})",
    )
    parser.add_argument(
        "--postgres-url",
        default=os.environ.get("DATABASE_URL"),
        help="URL PostgreSQL; por seguridad también puede usar DATABASE_URL.",
    )
    return parser.parse_args()


def sqlite_columns(database, table):
    return [row[1] for row in database.execute(f'PRAGMA table_info("{table}")')]


def rows_digest(rows):
    serialized = [
        json.dumps(list(row), ensure_ascii=False, separators=(",", ":"), default=str)
        for row in rows
    ]
    serialized.sort()
    digest = hashlib.sha256()
    for row in serialized:
        digest.update(row.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def ensure_empty_target(target):
    occupied = []
    with target.cursor() as cursor:
        for table in TABLES:
            if table == "quote_number_sequence":
                continue
            cursor.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))
            )
            count = cursor.fetchone()[0]
            if count:
                occupied.append(f"{table}={count}")
    if occupied:
        raise RuntimeError(
            "La base PostgreSQL de destino no está vacía: " + ", ".join(occupied)
        )


def copy_table(source, target, table):
    columns = sqlite_columns(source, table)
    source_rows = source.execute(
        f'SELECT {", ".join(f'"{column}"' for column in columns)} FROM "{table}"'
    ).fetchall()

    if table == "quote_number_sequence":
        with target.cursor() as cursor:
            cursor.execute("DELETE FROM quote_number_sequence")

    if source_rows:
        statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
            sql.Identifier(table),
            sql.SQL(", ").join(map(sql.Identifier, columns)),
            sql.SQL(", ").join(sql.Placeholder() for _ in columns),
        )
        with target.cursor() as cursor:
            cursor.executemany(statement, source_rows)

    with target.cursor() as cursor:
        cursor.execute(
            sql.SQL("SELECT {} FROM {}").format(
                sql.SQL(", ").join(map(sql.Identifier, columns)),
                sql.Identifier(table),
            )
        )
        target_rows = cursor.fetchall()

    if len(source_rows) != len(target_rows):
        raise RuntimeError(
            f"Verificación fallida en {table}: "
            f"SQLite={len(source_rows)}, PostgreSQL={len(target_rows)}"
        )
    if rows_digest(source_rows) != rows_digest(target_rows):
        raise RuntimeError(f"El contenido copiado de {table} no coincide")
    return len(source_rows)


def reset_identities(target):
    with target.cursor() as cursor:
        for table in IDENTITY_TABLES:
            statement = sql.SQL(
                """SELECT setval(
                    pg_get_serial_sequence(%s, 'id'),
                    COALESCE(MAX(id), 1),
                    MAX(id) IS NOT NULL
                ) FROM {}"""
            ).format(sql.Identifier(table))
            cursor.execute(statement, (table,))


def migrate(sqlite_path, postgres_url):
    if not postgres_url:
        raise RuntimeError(
            "Indique --postgres-url o defina la variable de entorno DATABASE_URL."
        )
    sqlite_path = sqlite_path.resolve()
    if not sqlite_path.is_file():
        raise RuntimeError(f"No existe la base SQLite: {sqlite_path}")

    source_uri = sqlite_path.as_uri() + "?mode=ro"
    source = sqlite3.connect(source_uri, uri=True)
    try:
        integrity = source.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_errors = source.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_key_errors:
            raise RuntimeError(
                "La base SQLite no superó las comprobaciones de integridad."
            )

        with psycopg.connect(postgres_url, application_name="its-migration") as target:
            with target.cursor() as cursor:
                cursor.execute(
                    """CREATE TABLE IF NOT EXISTS schema_migrations(
                    version TEXT PRIMARY KEY,
                    applied_at TEXT NOT NULL
                    )"""
                )
                cursor.execute(POSTGRES_SCHEMA.read_text(encoding="utf-8"))
            ensure_empty_target(target)

            counts = {}
            for table in TABLES:
                counts[table] = copy_table(source, target, table)
                print(f"{table}: {counts[table]} registros verificados")

            reset_identities(target)
            with target.cursor() as cursor:
                cursor.execute(
                    """INSERT INTO schema_migrations(version,applied_at)
                    VALUES('001_initial',to_char(now() AT TIME ZONE 'UTC',
                        'YYYY-MM-DD\"T\"HH24:MI:SS+00:00'))
                    ON CONFLICT(version) DO NOTHING"""
                )
        return counts
    finally:
        source.close()


def main():
    options = arguments()
    try:
        counts = migrate(options.sqlite, options.postgres_url)
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(
        "Migración completada: "
        f"{sum(counts.values())} registros verificados en PostgreSQL."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
