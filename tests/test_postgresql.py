import unittest
from unittest.mock import MagicMock

from cotizaciones.db import CompatRow, Database, postgres_sql


class PostgreSQLCompatibilityTests(unittest.TestCase):
    def test_qmark_translation_preserves_quoted_question_marks(self):
        translated = postgres_sql(
            "SELECT '?' AS literal, value FROM example WHERE id=? AND note='it''s?'"
        )
        self.assertEqual(
            translated,
            "SELECT '?' AS literal, value FROM example WHERE id=%s AND note='it''s?'",
        )

    def test_json_and_nocase_sql_are_translated(self):
        translated = postgres_sql(
            "SELECT json_extract(data,'$.cliente') FROM quotes "
            "ORDER BY number COLLATE NOCASE"
        )
        self.assertIn("data::jsonb ->> 'cliente'", translated)
        self.assertNotIn("json_extract", translated)
        self.assertNotIn("NOCASE", translated)

    def test_postgres_inserts_return_ids_and_immediate_begin_locks(self):
        database = Database(None, "postgresql")
        insert = database._postgres_statement(
            "INSERT INTO users(username,password_hash,role) VALUES(?,?,?)"
        )
        self.assertTrue(insert.endswith("RETURNING id"))
        self.assertIn("%s", insert)
        self.assertIn(
            "pg_advisory_xact_lock",
            database._postgres_statement("BEGIN IMMEDIATE"),
        )

    def test_execute_omits_empty_parameters_for_literal_percent_signs(self):
        raw_connection = MagicMock()
        cursor = raw_connection.cursor.return_value
        database = Database(raw_connection, "postgresql")

        database.execute("SELECT 1 WHERE 'en_revision' LIKE '%en_revision%'")

        cursor.execute.assert_called_once_with(
            "SELECT 1 WHERE 'en_revision' LIKE '%en_revision%'"
        )

    def test_execute_passes_non_empty_parameters(self):
        raw_connection = MagicMock()
        cursor = raw_connection.cursor.return_value
        database = Database(raw_connection, "postgresql")

        database.execute("SELECT * FROM quotes WHERE id=?", (7,))

        cursor.execute.assert_called_once_with("SELECT * FROM quotes WHERE id=%s", (7,))

    def test_compat_row_supports_mapping_and_numeric_access(self):
        row = CompatRow(("id", "name"), (7, "ITS"))
        self.assertEqual(row[0], 7)
        self.assertEqual(row["name"], "ITS")
        self.assertEqual(dict(row), {"id": 7, "name": "ITS"})


if __name__ == "__main__":
    unittest.main()
