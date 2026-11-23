ALTER TABLE users ADD COLUMN first_name TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN last_name TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT '';
ALTER TABLE users ADD COLUMN phone TEXT NOT NULL DEFAULT '';

UPDATE users
SET email = lower(username)
WHERE instr(username, '@') > 1;

CREATE UNIQUE INDEX IF NOT EXISTS ux_users_email
ON users(email COLLATE NOCASE)
WHERE email <> '';
