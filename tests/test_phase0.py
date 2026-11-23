import json
import os
import tempfile
import unittest
from io import BytesIO

from openpyxl import Workbook, load_workbook

from cotizaciones import create_app
from cotizaciones.db import connection
from cotizaciones.formatting import format_local_date, format_local_datetime


class PhaseZeroTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.app = create_app(
            {
                "TESTING": True,
                "DATABASE_PATH": os.path.join(cls.tmp.name, "test.db"),
                "ADMIN_PASSWORD": "AdminPassword123!",
                "SECRET_KEY": "phase-zero-test-secret",
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

    def csrf(self, client):
        client.get("/login")
        with client.session_transaction() as current_session:
            return current_session["csrf"]

    def login(self, client):
        token = self.csrf(client)
        response = client.post(
            "/login",
            data={"csrf": token, "username": "admin", "password": "AdminPassword123!"},
        )
        self.assertEqual(response.status_code, 302)
        with client.session_transaction() as current_session:
            return current_session["csrf"]

    def test_login_requires_csrf_and_locks_repeated_failures(self):
        client = self.app.test_client()
        response = client.post(
            "/login", data={"username": "admin", "password": "AdminPassword123!"}
        )
        self.assertEqual(response.status_code, 400)

        token = self.csrf(client)
        statuses = []
        for _ in range(self.app.config["LOGIN_MAX_ATTEMPTS"]):
            response = client.post(
                "/login",
                data={"csrf": token, "username": "admin", "password": "incorrecta"},
            )
            statuses.append(response.status_code)
        self.assertEqual(statuses[-1], 429)

        response = client.post(
            "/login",
            data={"csrf": token, "username": "admin", "password": "AdminPassword123!"},
        )
        self.assertEqual(response.status_code, 429)

    def test_quote_validation_and_security_headers(self):
        client = self.app.test_client()
        token = self.login(client)
        base = {"csrf": token, "cliente": "Cliente", "descripcion": "Descripción"}

        invalid_values = (
            {"estado": "Manipulado"},
            {"fecha": "2026-99-99"},
            {"clp": "-1"},
            {"usd": "NaN"},
            {"margen": "100.01"},
        )
        for invalid in invalid_values:
            with self.subTest(invalid=invalid):
                response = client.post("/new", data=base | invalid)
                self.assertEqual(response.status_code, 400)

        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        for header in (
            "Content-Security-Policy",
            "X-Frame-Options",
            "X-Content-Type-Options",
            "Permissions-Policy",
        ):
            self.assertIn(header, response.headers)

    def test_dates_are_displayed_in_chilean_format_and_santiago_time(self):
        with self.app.app_context():
            self.assertEqual(
                format_local_datetime("2026-11-17T13:28:44+00:00"),
                "17-11-2026 10:28",
            )
            self.assertEqual(format_local_date("2026-11-17"), "17-11-2026")

        client = self.app.test_client()
        token = self.login(client)
        response = client.post(
            "/new",
            data={
                "csrf": token,
                "solicitud": "2026-11-17",
                "cliente": "Cliente fecha",
                "descripcion": "Validar formato visible de fechas",
            },
        )
        detail = client.get(response.headers["Location"]).get_data(as_text=True)
        self.assertIn("17-11-2026", detail)
        self.assertNotIn("+00:00", detail)

    def test_export_neutralizes_spreadsheet_formulas(self):
        client = self.app.test_client()
        token = self.login(client)
        response = client.post(
            "/new",
            data={"csrf": token, "cliente": "=2+2", "descripcion": "Descripción"},
        )
        self.assertEqual(response.status_code, 302)

        response = client.get("/export")
        workbook = load_workbook(BytesIO(response.data), data_only=False)
        try:
            cell = workbook.active.cell(2, 11)
            self.assertEqual(cell.data_type, "s")
            self.assertEqual(cell.value, "'=2+2")
        finally:
            workbook.close()

    def test_import_validates_file_and_creates_audit_event(self):
        client = self.app.test_client()
        token = self.login(client)
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
        row[0], row[2], row[3] = "2026-11-01", 1081, "2026-11-02"
        row[11], row[12], row[13], row[16], row[18] = (
            "Cliente legado",
            "Descripción legada",
            2000,
            20,
            "referencial",
        )
        sheet.append(row)
        second_row = row.copy()
        second_row[2], second_row[11], second_row[12], second_row[18] = (
            1082,
            "Otro cliente legado",
            "Otra descripción legada",
            "Adjudicado",
        )
        sheet.append(second_row)
        invalid_row = row.copy()
        invalid_row[2], invalid_row[11], invalid_row[12] = 1083, "", ""
        sheet.append(invalid_row)
        content = BytesIO()
        workbook.save(content)
        workbook.close()
        content.seek(0)

        response = client.post(
            "/import",
            data={"csrf": token, "file": (content, "legacy.xlsx")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)
        preview_path = response.headers["Location"]
        preview = client.get(preview_path)
        self.assertEqual(preview.status_code, 200)
        self.assertIn("1 inválida", preview.get_data(as_text=True))
        batch_id = preview_path.rsplit("/", 1)[-1]
        response = client.post(f"/import/confirm/{batch_id}", data={"csrf": token})
        self.assertEqual(response.status_code, 302)
        with self.app.app_context(), connection() as database:
            audit_count = database.execute(
                "SELECT count(*) FROM audit WHERE action='importar'"
            ).fetchone()[0]
            imported_statuses = [
                json.loads(record[0])["estado"]
                for record in database.execute("SELECT data FROM quotes ORDER BY id")
            ]
            imported_numbers = [
                record[0]
                for record in database.execute("SELECT number FROM quotes ORDER BY id")
            ]
        self.assertEqual(audit_count, 2)
        self.assertEqual(imported_statuses, ["Referencial", "Adjudicada"])
        self.assertEqual(imported_numbers, ["ITS-LEG-001081", "ITS-LEG-001082"])

        response = client.post(
            "/import",
            data={"csrf": token, "file": (BytesIO(b"not a zip"), "bad.xlsx")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("No se pudo revisar el archivo", response.get_data(as_text=True))

    def test_import_accepts_reordered_columns_and_platform_headers(self):
        client = self.app.test_client()
        token = self.login(client)
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Cotizaciones"
        sheet.append(
            [
                "Descripción",
                "Estado",
                "Número",
                "Cliente final",
                "Fecha solicitud",
                "Deteccion oportunidad",
                "Realizo levantamiento en terreno, convirtio oportunidad en "
                "requerimiento formal, obtuvo toda la informacion para cotizar",
                "% de Utilidad",
                "UTILIDAD US$",
            ]
        )
        sheet.append(
            [
                "Servicio exportado por la plataforma",
                "Referencial",
                "ITS-LEG-001099",
                "Cliente reordenado",
                "23-11-2026",
                "Oportunidad detectada",
                "Visita realizada",
                20,
                1000,
            ]
        )
        content = BytesIO()
        workbook.save(content)
        workbook.close()
        content.seek(0)

        response = client.post(
            "/import",
            data={"csrf": token, "file": (content, "cotizaciones_ITS.xlsx")},
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 302)
        preview = client.get(response.headers["Location"]).get_data(as_text=True)
        self.assertIn("Cliente reordenado", preview)
        batch_id = response.headers["Location"].rsplit("/", 1)[-1]
        confirmed = client.post(f"/import/confirm/{batch_id}", data={"csrf": token})
        self.assertEqual(confirmed.status_code, 302)
        with self.app.app_context(), connection() as database:
            imported = json.loads(
                database.execute("SELECT data FROM quotes").fetchone()["data"]
            )
        self.assertEqual(imported["solicitud"], "2026-11-23")
        self.assertEqual(imported["deteccion"], "Oportunidad detectada")
        self.assertEqual(imported["levantamiento"], "Visita realizada")
        self.assertEqual(imported["margen"], "20")
        self.assertEqual(imported["utilidad_usd"], "1000")


if __name__ == "__main__":
    unittest.main()
