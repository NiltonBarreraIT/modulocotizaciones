import json
import os
import sqlite3
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook
from werkzeug.security import generate_password_hash

from cotizaciones import create_app
from cotizaciones.db import connection


class PhaseTwoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.app = create_app(
            {
                "TESTING": True,
                "DATABASE_PATH": os.path.join(cls.tmp.name, "test.db"),
                "ADMIN_PASSWORD": "AdminPassword123!",
                "SECRET_KEY": "phase-two-test-secret",
                "QUOTE_PAGE_SIZE": 2,
            }
        )

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        with self.app.app_context(), connection() as database:
            database.execute("DELETE FROM import_batches")
            database.execute("DELETE FROM audit")
            database.execute("DELETE FROM quotes")
            database.execute("DELETE FROM login_attempts")
            database.execute("DELETE FROM users WHERE role!='admin'")
            database.execute(
                "INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                ("user1", generate_password_hash("UserPassword123!"), "usuario"),
            )

    def login(self, username="user1", password="UserPassword123!"):
        client = self.app.test_client()
        client.get("/login")
        with client.session_transaction() as current_session:
            token = current_session["csrf"]
        response = client.post(
            "/login",
            data={"csrf": token, "username": username, "password": password},
        )
        self.assertEqual(response.status_code, 302)
        with client.session_transaction() as current_session:
            return client, current_session["csrf"]

    def create_quote(self, client, token, client_name, **extra):
        payload = {
            "csrf": token,
            "cliente": client_name,
            "descripcion": f"Descripción de {client_name}",
        } | extra
        response = client.post("/new", data=payload)
        self.assertEqual(response.status_code, 302)
        return int(response.headers["Location"].rsplit("/", 1)[-1])

    def workbook_content(self, duplicate_rows=1):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "COTIZACIONES"
        sheet.append(
            [
                "Fecha solicitud",
                "Código",
                "N",
                "Fecha cotización",
                "Vendedor",
                "Detección",
                "Levantamiento",
                "Propuesta",
                "Fábrica",
                "Área",
                "RFQ",
                "Cliente final",
                "Descripción",
                "Monto CLP",
                "Monto USD",
                "Monto EUR",
                "Margen",
                "Utilidad USD",
                "Estado",
            ]
        )
        row = [""] * 19
        row[2], row[3], row[4], row[10] = (
            1081,
            "2026-11-02",
            "Vendedor A",
            "RFQ-100",
        )
        row[11], row[12], row[13], row[16], row[18] = (
            "Cliente legado",
            "Descripción legada",
            2000,
            20,
            "Enviada",
        )
        for _ in range(duplicate_rows):
            sheet.append(row)
        content = BytesIO()
        workbook.save(content)
        workbook.close()
        content.seek(0)
        return content

    def test_seller_cannot_edit_after_registering_request(self):
        client, token = self.login()
        quote_id = self.create_quote(client, token, "Cliente original")
        first = client.post(
            f"/quote/{quote_id}/edit",
            data={
                "csrf": token,
                "version": "1",
                "cliente": "Primera edición",
                "descripcion": "Contenido guardado",
            },
        )
        self.assertEqual(first.status_code, 403)

        with self.app.app_context(), connection() as database:
            quote = database.execute(
                "SELECT data,version FROM quotes WHERE id=?", (quote_id,)
            ).fetchone()
            audit_count = database.execute(
                "SELECT count(*) FROM audit WHERE quote_id=?", (quote_id,)
            ).fetchone()[0]
        self.assertEqual(json.loads(quote["data"])["cliente"], "Cliente original")
        self.assertEqual(quote["version"], 1)
        self.assertEqual(audit_count, 1)

    def test_phase1_database_migrates_without_losing_quotes(self):
        legacy_path = os.path.join(self.tmp.name, "phase1.db")
        project_root = Path(__file__).resolve().parent.parent
        database = sqlite3.connect(legacy_path)
        try:
            database.executescript(
                (project_root / "migrations" / "001_initial.sql").read_text(
                    encoding="utf-8"
                )
            )
            database.executescript(
                (project_root / "migrations" / "002_login_attempts.sql").read_text(
                    encoding="utf-8"
                )
            )
            database.execute(
                """CREATE TABLE schema_migrations(
                version TEXT PRIMARY KEY, applied_at TEXT NOT NULL
                )"""
            )
            database.executemany(
                "INSERT INTO schema_migrations(version,applied_at) VALUES(?,?)",
                [
                    ("001_initial", "2026-01-01T00:00:00+00:00"),
                    ("002_login_attempts", "2026-01-01T00:00:00+00:00"),
                ],
            )
            database.execute(
                "INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                ("legacy-admin", generate_password_hash("LegacyPassword123!"), "admin"),
            )
            data = json.dumps({"cliente": "Cliente conservado"})
            database.execute(
                """INSERT INTO quotes(
                number,owner_id,created_at,last_user_edit_at,data
                ) VALUES(?,?,?,?,?)""",
                (
                    "ITS-COT-000001",
                    1,
                    "2026-01-01T00:00:00+00:00",
                    "2026-01-01T00:00:00+00:00",
                    data,
                ),
            )
            database.commit()
        finally:
            database.close()

        migrated_app = create_app(
            {
                "TESTING": True,
                "DATABASE_PATH": legacy_path,
                "SECRET_KEY": "migration-test-secret",
            }
        )
        with migrated_app.app_context(), connection() as migrated:
            quote = migrated.execute(
                "SELECT data,version,fingerprint FROM quotes WHERE id=1"
            ).fetchone()
            version = migrated.execute(
                "SELECT 1 FROM schema_migrations WHERE version=?",
                ("003_integrity_and_import_preview",),
            ).fetchone()
        self.assertEqual(json.loads(quote["data"])["cliente"], "Cliente conservado")
        self.assertEqual(quote["version"], 1)
        self.assertIsNone(quote["fingerprint"])
        self.assertIsNotNone(version)

    def test_seller_cannot_edit_or_change_commercial_status(self):
        client, token = self.login()
        quote_id = self.create_quote(client, token, "Cliente", estado="Referencial")
        invalid = client.post(
            f"/quote/{quote_id}/edit",
            data={
                "csrf": token,
                "version": "1",
                "cliente": "Cliente",
                "descripcion": "Descripción",
                "estado": "Perdida",
            },
        )
        self.assertEqual(invalid.status_code, 403)
        with self.app.app_context(), connection() as database:
            row = database.execute(
                "SELECT data FROM quotes WHERE id=?", (quote_id,)
            ).fetchone()
            delivered_data = json.loads(row["data"])
            delivered_data["estado"] = "Abierta"
            database.execute(
                "UPDATE quotes SET request_status='entregada',data=? WHERE id=?",
                (json.dumps(delivered_data, ensure_ascii=False), quote_id),
            )
        status_change = client.post(
            f"/quote/{quote_id}/commercial-status",
            data={
                "csrf": token,
                "estado": "Postergada",
                "justificacion": "Cliente solicitó revisar el proyecto más adelante.",
            },
        )
        self.assertEqual(status_change.status_code, 403)
        detail = client.get(f"/quote/{quote_id}").get_data(as_text=True)
        self.assertIn("Cliente", detail)
        self.assertIn("Abierta", detail)
        self.assertNotIn("Cliente actualizado", detail)

    def test_filters_and_pagination(self):
        client, token = self.login()
        adjudicated_id = self.create_quote(client, token, "Acme Norte", vendedor="Ana")
        self.create_quote(client, token, "Acme Sur", vendedor="Ana")
        self.create_quote(client, token, "Beta", vendedor="Bruno")

        first_page = client.get("/").get_data(as_text=True)
        self.assertIn("Página 1 de 2", first_page)
        self.assertIn("Siguiente", first_page)
        filtered = client.get("/?cliente=Acme&vendedor=user1").get_data(as_text=True)
        self.assertIn("2 resultado(s)", filtered)
        self.assertIn("Acme Norte", filtered)
        self.assertIn("Acme Sur", filtered)
        self.assertNotIn(">Beta<", filtered)

        with self.app.app_context(), connection() as database:
            row = database.execute(
                "SELECT data FROM quotes WHERE id=?", (adjudicated_id,)
            ).fetchone()
            data = json.loads(row["data"])
            data["estado"] = "Adjudicada"
            database.execute(
                "UPDATE quotes SET data=? WHERE id=?",
                (json.dumps(data, ensure_ascii=False), adjudicated_id),
            )
        adjudicated = client.get("/?estado=Adjudicada").get_data(as_text=True)
        self.assertIn("1 resultado(s)", adjudicated)
        self.assertIn("Acme Norte", adjudicated)
        self.assertIn("Adjudicada", adjudicated)
        self.assertNotIn("Acme Sur", adjudicated)

    def test_import_preview_skips_duplicates(self):
        client, token = self.login("admin", "AdminPassword123!")
        response = client.post(
            "/import",
            data={
                "csrf": token,
                "file": (self.workbook_content(duplicate_rows=2), "legacy.xlsx"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)
        preview_path = response.headers["Location"]
        preview = client.get(preview_path).get_data(as_text=True)
        self.assertIn("1 nuevas", preview)
        self.assertIn("1 duplicadas", preview)
        batch_id = preview_path.rsplit("/", 1)[-1]
        response = client.post(f"/import/confirm/{batch_id}", data={"csrf": token})
        self.assertEqual(response.status_code, 302)

        response = client.post(
            "/import",
            data={
                "csrf": token,
                "file": (self.workbook_content(), "legacy.xlsx"),
            },
            content_type="multipart/form-data",
        )
        repeated_preview = client.get(response.headers["Location"]).get_data(
            as_text=True
        )
        self.assertIn("0 nuevas", repeated_preview)
        self.assertIn("1 duplicadas", repeated_preview)
        with self.app.app_context(), connection() as database:
            quote_count = database.execute("SELECT count(*) FROM quotes").fetchone()[0]
            audit_count = database.execute("SELECT count(*) FROM audit").fetchone()[0]
        self.assertEqual(quote_count, 1)
        self.assertEqual(audit_count, 1)

    def test_import_reserves_legacy_number_and_moves_newer_requests(self):
        client, token = self.login("admin", "AdminPassword123!")
        with self.app.app_context(), connection() as database:
            database.execute(
                "UPDATE quote_number_sequence SET current_value=1080 WHERE id=1"
            )
        first_id = self.create_quote(client, token, "Solicitud uno")
        second_id = self.create_quote(client, token, "Solicitud dos")

        response = client.post(
            "/import",
            data={
                "csrf": token,
                "file": (self.workbook_content(), "legacy.xlsx"),
            },
            content_type="multipart/form-data",
        )
        batch_id = response.headers["Location"].rsplit("/", 1)[-1]
        client.post(f"/import/confirm/{batch_id}", data={"csrf": token})

        third_id = self.create_quote(client, token, "Solicitud tres")
        with self.app.app_context(), connection() as database:
            numbers = {
                row["id"]: row["number"]
                for row in database.execute("SELECT id,number FROM quotes")
            }
        self.assertIn("ITS-LEG-001081", numbers.values())
        self.assertEqual(numbers[first_id], "ITS-SOL-001082")
        self.assertEqual(numbers[second_id], "ITS-SOL-001083")
        self.assertEqual(numbers[third_id], "ITS-SOL-001084")

    def test_reimport_reconciles_number_from_column_c(self):
        client, token = self.login("admin", "AdminPassword123!")
        response = client.post(
            "/import",
            data={
                "csrf": token,
                "file": (self.workbook_content(), "legacy.xlsx"),
            },
            content_type="multipart/form-data",
        )
        batch_id = response.headers["Location"].rsplit("/", 1)[-1]
        client.post(f"/import/confirm/{batch_id}", data={"csrf": token})
        with self.app.app_context(), connection() as database:
            quote_id = database.execute("SELECT id FROM quotes").fetchone()[0]
            database.execute(
                "UPDATE quotes SET number='ITS-LEG-000077',correlative=77 WHERE id=?",
                (quote_id,),
            )

        response = client.post(
            "/import",
            data={
                "csrf": token,
                "file": (self.workbook_content(), "legacy.xlsx"),
            },
            content_type="multipart/form-data",
        )
        preview = client.get(response.headers["Location"]).get_data(as_text=True)
        self.assertIn("1 números por actualizar", preview)
        batch_id = response.headers["Location"].rsplit("/", 1)[-1]
        client.post(f"/import/confirm/{batch_id}", data={"csrf": token})
        with self.app.app_context(), connection() as database:
            quote = database.execute(
                "SELECT number,correlative FROM quotes WHERE id=?", (quote_id,)
            ).fetchone()
        self.assertEqual(quote["number"], "ITS-LEG-001081")
        self.assertEqual(quote["correlative"], 1081)


if __name__ == "__main__":
    unittest.main()
