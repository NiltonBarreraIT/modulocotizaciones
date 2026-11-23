ALTER TABLE quote_commercial
ADD COLUMN profit_percent TEXT NOT NULL DEFAULT '';

ALTER TABLE quote_commercial
ADD COLUMN profit_usd TEXT NOT NULL DEFAULT '';
