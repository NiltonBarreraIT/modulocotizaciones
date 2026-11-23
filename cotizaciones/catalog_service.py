from .db import connection

CATALOG_TYPES = {
    "area": "Áreas",
    "fabrica": "Fábricas",
}


def active_catalog_options():
    options = {kind: [] for kind in CATALOG_TYPES}
    with connection() as database:
        rows = database.execute(
            """SELECT kind,value FROM catalog_entries
            WHERE active=1 ORDER BY kind,value COLLATE NOCASE"""
        ).fetchall()
    for row in rows:
        options[row["kind"]].append(row["value"])
    return options
