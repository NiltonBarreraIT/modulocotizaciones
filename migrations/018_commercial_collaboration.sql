ALTER TABLE quote_commercial ADD COLUMN shared_user_id INTEGER;
ALTER TABLE quote_commercial ADD COLUMN shared_percent TEXT NOT NULL DEFAULT '';
ALTER TABLE quote_commercial ADD COLUMN technical_support_user_id INTEGER;

CREATE INDEX IF NOT EXISTS ix_quote_commercial_shared_user
ON quote_commercial(shared_user_id);

CREATE INDEX IF NOT EXISTS ix_quote_commercial_technical_support
ON quote_commercial(technical_support_user_id);
