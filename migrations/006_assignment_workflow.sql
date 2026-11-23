CREATE TABLE IF NOT EXISTS quote_estimators(
    user_id INTEGER PRIMARY KEY,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

ALTER TABLE quotes ADD COLUMN estimator_id INTEGER REFERENCES users(id);
ALTER TABLE quotes ADD COLUMN taken_at TEXT;

CREATE INDEX IF NOT EXISTS ix_quotes_estimator
ON quotes(estimator_id,request_status,created_at);
