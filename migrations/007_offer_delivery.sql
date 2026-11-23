CREATE TABLE IF NOT EXISTS quote_deliveries(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id INTEGER NOT NULL,
    attachment_id INTEGER NOT NULL UNIQUE,
    delivered_by INTEGER NOT NULL,
    delivered_at TEXT NOT NULL,
    FOREIGN KEY(quote_id) REFERENCES quotes(id) ON DELETE CASCADE,
    FOREIGN KEY(attachment_id) REFERENCES quote_attachments(id) ON DELETE CASCADE,
    FOREIGN KEY(delivered_by) REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS ix_quote_deliveries_quote
ON quote_deliveries(quote_id,delivered_at DESC);
