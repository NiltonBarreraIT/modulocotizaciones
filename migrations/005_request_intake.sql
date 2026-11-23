ALTER TABLE quotes ADD COLUMN request_status TEXT NOT NULL DEFAULT 'registrada';

CREATE INDEX IF NOT EXISTS ix_quotes_request_status
ON quotes(request_status,created_at);
