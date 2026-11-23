CREATE TABLE IF NOT EXISTS catalog_entries(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK(kind IN ('area','fabrica')),
    value TEXT NOT NULL COLLATE NOCASE,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(kind, value)
);

CREATE INDEX IF NOT EXISTS ix_catalog_entries_kind_active
ON catalog_entries(kind, active, value);
