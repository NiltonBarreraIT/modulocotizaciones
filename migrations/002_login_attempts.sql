CREATE TABLE IF NOT EXISTS login_attempts(
    key TEXT PRIMARY KEY,
    failures INTEGER NOT NULL,
    first_failed_at TEXT NOT NULL,
    locked_until TEXT
);
