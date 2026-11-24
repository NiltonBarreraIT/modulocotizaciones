CREATE TABLE IF NOT EXISTS clients(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    tax_id TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_clients_name
ON clients(name COLLATE NOCASE);

CREATE UNIQUE INDEX IF NOT EXISTS ux_clients_tax_id
ON clients(tax_id COLLATE NOCASE)
WHERE tax_id <> '';

CREATE TABLE IF NOT EXISTS client_sellers(
    client_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    PRIMARY KEY(client_id,user_id),
    FOREIGN KEY(client_id) REFERENCES clients(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_client_sellers_user
ON client_sellers(user_id,client_id);

ALTER TABLE quotes ADD COLUMN client_id INTEGER REFERENCES clients(id);
CREATE INDEX IF NOT EXISTS ix_quotes_client ON quotes(client_id);
