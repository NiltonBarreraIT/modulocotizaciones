ALTER TABLE quotes ADD COLUMN version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE quotes ADD COLUMN fingerprint TEXT;

CREATE INDEX IF NOT EXISTS ix_quotes_fingerprint ON quotes(fingerprint);

CREATE TABLE IF NOT EXISTS import_batches(
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    source_name TEXT NOT NULL,
    rows TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS ix_import_batches_user ON import_batches(user_id);
