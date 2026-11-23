ALTER TABLE quotes ADD COLUMN due_date TEXT;

CREATE INDEX idx_quotes_request_due
ON quotes(request_status, due_date, created_at);
