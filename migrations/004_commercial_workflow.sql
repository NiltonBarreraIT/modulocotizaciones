ALTER TABLE quotes ADD COLUMN workflow_status TEXT NOT NULL DEFAULT 'borrador';
ALTER TABLE quotes ADD COLUMN approved_by INTEGER;
ALTER TABLE quotes ADD COLUMN approved_at TEXT;
ALTER TABLE quotes ADD COLUMN approved_version INTEGER;
ALTER TABLE quotes ADD COLUMN sent_at TEXT;

CREATE TABLE IF NOT EXISTS quote_commercial(
    quote_id INTEGER PRIMARY KEY,
    currency TEXT NOT NULL DEFAULT 'CLP' CHECK(currency IN ('CLP','USD','EUR')),
    global_discount TEXT NOT NULL DEFAULT '0',
    tax_percent TEXT NOT NULL DEFAULT '19',
    validity_days INTEGER NOT NULL DEFAULT 15,
    payment_terms TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    subtotal TEXT NOT NULL DEFAULT '0',
    discount_total TEXT NOT NULL DEFAULT '0',
    tax_total TEXT NOT NULL DEFAULT '0',
    total TEXT NOT NULL DEFAULT '0',
    updated_at TEXT NOT NULL,
    FOREIGN KEY(quote_id) REFERENCES quotes(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS quote_items(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('producto','servicio')),
    description TEXT NOT NULL,
    quantity TEXT NOT NULL,
    unit_price TEXT NOT NULL,
    discount_percent TEXT NOT NULL DEFAULT '0',
    line_total TEXT NOT NULL,
    FOREIGN KEY(quote_id) REFERENCES quotes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_quote_items_quote ON quote_items(quote_id,position);

CREATE TABLE IF NOT EXISTS quote_versions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id INTEGER NOT NULL,
    version INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    user_id INTEGER,
    data TEXT NOT NULL,
    commercial_data TEXT NOT NULL,
    items_data TEXT NOT NULL,
    UNIQUE(quote_id,version),
    FOREIGN KEY(quote_id) REFERENCES quotes(id) ON DELETE CASCADE
);

INSERT OR IGNORE INTO quote_versions(
    quote_id,version,created_at,user_id,data,commercial_data,items_data
)
SELECT id,version,created_at,owner_id,data,'{}','[]' FROM quotes;

CREATE TABLE IF NOT EXISTS quote_comments(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id INTEGER NOT NULL,
    user_id INTEGER,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(quote_id) REFERENCES quotes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_quote_comments_quote ON quote_comments(quote_id,id);

CREATE TABLE IF NOT EXISTS quote_attachments(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    quote_id INTEGER NOT NULL,
    user_id INTEGER,
    original_name TEXT NOT NULL,
    stored_name TEXT UNIQUE NOT NULL,
    mime_type TEXT NOT NULL,
    size INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(quote_id) REFERENCES quotes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_quote_attachments_quote ON quote_attachments(quote_id,id);
