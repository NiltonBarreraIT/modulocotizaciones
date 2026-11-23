ALTER TABLE quotes ADD COLUMN correlative INTEGER;

UPDATE quotes
SET correlative = CAST(substr(number, -6) AS INTEGER)
WHERE number LIKE 'ITS-LEG-%' OR number LIKE 'ITS-SOL-%';

CREATE UNIQUE INDEX IF NOT EXISTS ux_quotes_correlative
ON quotes(correlative)
WHERE correlative IS NOT NULL;

CREATE TABLE IF NOT EXISTS quote_number_sequence(
    id INTEGER PRIMARY KEY CHECK(id = 1),
    current_value INTEGER NOT NULL CHECK(current_value >= 0)
);

INSERT INTO quote_number_sequence(id, current_value)
VALUES(1, COALESCE((SELECT MAX(correlative) FROM quotes), 0));
