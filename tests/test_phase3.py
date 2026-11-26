import json
import os
import tempfile
import unittest
from io import BytesIO
from unittest.mock import patch
from zipfile import ZipFile

from docx import Document
from openpyxl import load_workbook
from werkzeug.security import generate_password_hash

from cotizaciones import create_app
from cotizaciones.db import connection
from cotizaciones.deadlines import local_today


class PhaseThreeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.app = create_app(
            {
                "TESTING": True,
                "DATABASE_PATH": os.path.join(cls.tmp.name, "test.db"),
                "UPLOAD_FOLDER": os.path.join(cls.tmp.name, "attachments"),
                "ADMIN_PASSWORD": "AdminPassword123!",
                "SECRET_KEY": "phase-three-test-secret",
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
            for username in ("user1", "user2", "estimator"):
                database.execute(
                    "INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                    (
                        username,
                        generate_password_hash("UserPassword123!"),
                        "usuario",
                    ),
                )
            self.user_ids = {
                row["username"]: row["id"]
                for row in database.execute("SELECT id,username FROM users")
            }
            database.execute(
                """UPDATE users SET first_name='Segundo',last_name='Vendedor',
                email='user2@i-tsolutions.cl',phone='+56 9 2222 2222'
                WHERE id=?""",
                (self.user_ids["user2"],),
            )
            database.execute(
                """UPDATE users SET first_name='Ana',last_name='Comercial',
                email='ana.comercial@i-tsolutions.cl',phone='+56 9 1111 1111'
                WHERE id=?""",
                (self.user_ids["user1"],),
            )
            database.execute(
                "INSERT INTO quote_estimators(user_id) VALUES(?)",
                (self.user_ids["estimator"],),
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

    def create_quote(self, client, token, name="Cliente comercial"):
        response = client.post(
            "/new",
            data={
                "csrf": token,
                "cliente": name,
                "descripcion": "Solución tecnológica integral",
                "client_email": "contacto@cliente.cl",
                "vendedor": "Ana Comercial",
                "rfq": "RFQ-300",
                "fecha": "2026-11-17",
                "estado": "Referencial",
            },
        )
        self.assertEqual(response.status_code, 302)
        return int(response.headers["Location"].rsplit("/", 1)[-1])

    def assign_estimator(self, quote_id):
        with self.app.app_context(), connection() as database:
            database.execute(
                """UPDATE quotes SET estimator_id=?,request_status='en_cotizacion'
                WHERE id=?""",
                (self.user_ids["estimator"], quote_id),
            )

    def save_commercial(
        self, client, token, quote_id, version=1, currency="USD", profit_usd="38,30"
    ):
        return client.post(
            f"/quote/{quote_id}/commercial",
            data={
                "csrf": token,
                "version": str(version),
                "currency": currency,
                "global_discount": "10",
                "tax_percent": "19",
                "validity_days": "30",
                "payment_terms": "50% anticipo, saldo contra entrega",
                "notes": "Incluye instalación remota.",
                "delivery_time": "4 semanas desde la orden de compra",
                "supply_scope": "Suministro de 2 equipos y servicio de instalación",
                "profit_percent": "18,50",
                "profit_usd": profit_usd,
                "shared_user_id": str(self.user_ids["user2"]),
                "shared_percent": "40",
                "technical_support_user_id": str(self.user_ids["admin"]),
                "item_kind": ["producto", "servicio"],
                "item_description": ["Equipo principal", "Instalación"],
                "item_details": [
                    "Diámetro nominal: DN25\nSeñal de salida: 4-20 mA + HART",
                    "Configuración y puesta en marcha",
                ],
                "item_quantity": ["2", "1"],
                "item_unit_price": ["100", "50"],
                "item_discount": ["10", "0"],
            },
        )

    def test_commercial_calculation_versions_and_pdf(self):
        owner, owner_token = self.login()
        quote_id = self.create_quote(owner, owner_token)
        self.assign_estimator(quote_id)
        client, token = self.login("estimator")
        response = self.save_commercial(client, token, quote_id)
        self.assertEqual(response.status_code, 302)

        with self.app.app_context(), connection() as database:
            commercial = database.execute(
                "SELECT * FROM quote_commercial WHERE quote_id=?", (quote_id,)
            ).fetchone()
            items = database.execute(
                "SELECT count(*) FROM quote_items WHERE quote_id=?", (quote_id,)
            ).fetchone()[0]
            versions = database.execute(
                "SELECT count(*) FROM quote_versions WHERE quote_id=?", (quote_id,)
            ).fetchone()[0]
            quote_data = json.loads(
                database.execute(
                    "SELECT data FROM quotes WHERE id=?", (quote_id,)
                ).fetchone()[0]
            )
            database.execute(
                "UPDATE quotes SET request_status='registrada' WHERE id=?",
                (quote_id,),
            )
        self.assertEqual(commercial["subtotal"], "230.00")
        self.assertEqual(commercial["discount_total"], "23.00")
        self.assertEqual(commercial["tax_percent"], "0.00")
        self.assertEqual(commercial["tax_total"], "0.00")
        self.assertEqual(commercial["total"], "207.00")
        self.assertEqual(commercial["profit_percent"], "18.50")
        self.assertEqual(commercial["profit_usd"], "38.30")
        self.assertEqual(commercial["shared_user_id"], self.user_ids["user2"])
        self.assertEqual(commercial["shared_percent"], "40.00")
        self.assertEqual(
            commercial["technical_support_user_id"], self.user_ids["admin"]
        )
        self.assertEqual(quote_data["usd"], "207.00")
        self.assertEqual(quote_data["clp"], "")
        self.assertEqual(quote_data["eur"], "")
        self.assertEqual(quote_data["margen"], "18.50")
        self.assertEqual(quote_data["utilidad_usd"], "38.30")
        self.assertEqual(items, 2)
        self.assertEqual(versions, 2)
        detail = client.get(f"/quote/{quote_id}").get_data(as_text=True)
        self.assertIn("207,00", detail)
        self.assertIn("18.50%", detail)
        self.assertIn("38,30", detail)
        self.assertIn("Negocio compartido con", detail)
        self.assertIn("Segundo Vendedor", detail)
        self.assertIn("40.00%", detail)
        self.assertIn("Apoyo Técnico PM", detail)
        self.assertIn("Monto USD", detail)
        self.assertIn("Utilidad US$", detail)
        self.assertNotIn("Monto CLP", detail)
        self.assertNotIn("Monto EUR", detail)
        self.assertLess(
            detail.index("Negocio compartido con"),
            detail.index("Productos y servicios"),
        )
        self.assertEqual(detail.count("Negocio compartido con"), 1)
        general_form = client.get(f"/quote/{quote_id}/edit").get_data(as_text=True)
        self.assertIn('id="fecha" name="fecha" type="date"', general_form)
        self.assertIn('id="usd" name="usd" type="number"', general_form)
        self.assertIn('value="207.00" readonly', general_form)
        self.assertEqual(client.get(f"/quote/{quote_id}/version/1").status_code, 200)
        version_two = client.get(f"/quote/{quote_id}/version/2")
        self.assertEqual(version_two.status_code, 200)
        self.assertIn("Segundo Vendedor", version_two.get_data(as_text=True))

        exported = client.get("/export")
        export_workbook = load_workbook(BytesIO(exported.data), data_only=True)
        try:
            headers = [cell.value for cell in export_workbook.active[1]]
            values = [cell.value for cell in export_workbook.active[2]]
            self.assertEqual(
                values[headers.index("Negocio compartido con")], "Segundo Vendedor"
            )
            self.assertEqual(values[headers.index("Porcentaje compartido %")], "40.00")
            self.assertEqual(values[headers.index("Apoyo Técnico PM")], "admin")
        finally:
            export_workbook.close()

        with patch(
            "cotizaciones.commercial.convert_docx_to_pdf",
            return_value=BytesIO(b"%PDF-1.4\n%%EOF"),
        ) as converter:
            pdf = client.get(f"/quote/{quote_id}/pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertEqual(pdf.mimetype, "application/pdf")
        self.assertTrue(pdf.data.startswith(b"%PDF"))
        self.assertTrue(converter.called)
        generated_docx = converter.call_args.args[0]
        self.assertTrue(generated_docx.getvalue().startswith(b"PK"))
        self.assertIn(
            "Oferta-Comercial.pdf", pdf.headers.get("Content-Disposition", "")
        )

        word = client.get(f"/quote/{quote_id}/offer.docx")
        self.assertEqual(word.status_code, 200)
        self.assertEqual(
            word.mimetype,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        document = Document(BytesIO(word.data))
        generated_text = "\n".join(
            [paragraph.text for paragraph in document.paragraphs]
            + [
                cell.text
                for table in document.tables
                for row in table.rows
                for cell in row.cells
            ]
        )
        self.assertIn("Cliente comercial", generated_text)
        self.assertIn(
            "Suministro de 2 equipos y servicio de instalación", generated_text
        )
        self.assertIn("Producto - Equipo principal", generated_text)
        self.assertIn("Servicio - Instalación", generated_text)
        self.assertIn("• Diámetro nominal: DN25", generated_text)
        self.assertIn("• Señal de salida: 4-20 mA + HART", generated_text)
        self.assertLess(
            generated_text.index("• Señal de salida: 4-20 mA + HART"),
            generated_text.index("Descuento de línea: 10%"),
        )
        self.assertIn("USD $207,00", generated_text)
        self.assertIn("Total Neto", generated_text)
        self.assertIn("Notas para la cotización", generated_text)
        self.assertIn("Incluye instalación remota.", generated_text)
        self.assertIn("4 semanas desde la orden de compra", generated_text)
        with ZipFile(BytesIO(word.data)) as package:
            document_xml = package.read("word/document.xml").decode("utf-8")
            embedded_fonts = [
                name for name in package.namelist() if name.startswith("word/fonts/")
            ]
        self.assertEqual(len(embedded_fonts), 11)
        self.assertIn("Ana Comercial", document_xml)
        self.assertIn("ana.comercial@i-tsolutions.cl", document_xml)
        self.assertIn("+56 9 1111 1111", document_xml)
        self.assertNotIn("Impuesto", generated_text)
        self.assertNotIn("SQM S.A.", generated_text)
        self.assertNotIn("FLUJÓMETRO ELECTROMAGNÉTICO", generated_text)

    def test_detail_only_shows_values_for_selected_clp_currency(self):
        owner, owner_token = self.login()
        quote_id = self.create_quote(owner, owner_token, "Cliente CLP")
        self.assign_estimator(quote_id)
        client, token = self.login("estimator")
        response = self.save_commercial(
            client, token, quote_id, currency="CLP", profit_usd=""
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context(), connection() as database:
            database.execute(
                "UPDATE quotes SET request_status='registrada' WHERE id=?",
                (quote_id,),
            )

        detail = client.get(f"/quote/{quote_id}").get_data(as_text=True)
        self.assertIn("Monto CLP", detail)
        self.assertNotIn("Monto USD", detail)
        self.assertNotIn("Monto EUR", detail)
        self.assertNotIn("Utilidad US$", detail)
        self.assertIn("% utilidad", detail)

    def test_clp_thousands_are_parsed_and_displayed_with_dots(self):
        owner, owner_token = self.login()
        quote_id = self.create_quote(owner, owner_token, "Cliente CLP")
        self.assign_estimator(quote_id)
        client, token = self.login("estimator")
        response = client.post(
            f"/quote/{quote_id}/commercial",
            data={
                "csrf": token,
                "version": "1",
                "currency": "CLP",
                "global_discount": "0,00",
                "tax_percent": "19,00",
                "validity_days": "15",
                "item_kind": ["producto", "producto"],
                "item_description": ["Transmisor", "Sensor"],
                "item_quantity": ["5", "10"],
                "item_unit_price": ["150.000", "250.000"],
                "item_discount": ["0", "0"],
            },
        )
        self.assertEqual(response.status_code, 302)
        with self.app.app_context(), connection() as database:
            commercial = database.execute(
                "SELECT * FROM quote_commercial WHERE quote_id=?", (quote_id,)
            ).fetchone()
            prices = [
                row[0]
                for row in database.execute(
                    """SELECT unit_price FROM quote_items
                    WHERE quote_id=? ORDER BY position""",
                    (quote_id,),
                )
            ]
            database.execute(
                "UPDATE quotes SET request_status='registrada' WHERE id=?",
                (quote_id,),
            )
        self.assertEqual(prices, ["150000.00", "250000.00"])
        self.assertEqual(commercial["subtotal"], "3250000.00")
        self.assertEqual(commercial["tax_total"], "0.00")
        self.assertEqual(commercial["total"], "3250000.00")

        detail = client.get(f"/quote/{quote_id}").get_data(as_text=True)
        self.assertIn("3.250.000", detail)
        self.assertIn("Total neto CLP 3.250.000", detail)
        self.assertNotIn("Impuesto", detail)
        self.assertIn("actualizó el detalle comercial", detail)
        self.assertNotIn("editar_comercial", detail)
        self.assertIn("Ver detalles", detail)
        self.assertIn("Descargar oferta PDF", detail)
        self.assertNotIn("Descargar oferta Word", detail)
        form = client.get(f"/quote/{quote_id}/commercial").get_data(as_text=True)
        self.assertIn('value="150.000"', form)
        self.assertIn('value="250.000"', form)

    def test_approval_workflow_locks_and_sends_approved_version(self):
        owner, owner_token = self.login()
        quote_id = self.create_quote(owner, owner_token)
        self.assign_estimator(quote_id)
        estimator, estimator_token = self.login("estimator")
        self.assertEqual(
            self.save_commercial(estimator, estimator_token, quote_id).status_code, 302
        )
        owner_detail = owner.get(f"/quote/{quote_id}").get_data(as_text=True)
        self.assertNotIn("Solicitar aprobación", owner_detail)
        forbidden = owner.post(
            f"/quote/{quote_id}/workflow",
            data={"csrf": owner_token, "action": "submit"},
        )
        self.assertEqual(forbidden.status_code, 403)

        estimator_detail = estimator.get(f"/quote/{quote_id}").get_data(as_text=True)
        self.assertIn("Solicitar aprobación", estimator_detail)
        submitted = estimator.post(
            f"/quote/{quote_id}/workflow",
            data={"csrf": estimator_token, "action": "submit"},
        )
        self.assertEqual(submitted.status_code, 302)
        self.assertEqual(owner.get(f"/quote/{quote_id}/edit").status_code, 403)
        self.assertEqual(owner.get(f"/quote/{quote_id}/commercial").status_code, 403)
        self.assertEqual(owner.get("/approvals").status_code, 403)

        admin, admin_token = self.login("admin", "AdminPassword123!")
        approvals = admin.get("/approvals").get_data(as_text=True)
        self.assertIn("Ofertas pendientes de aprobación", approvals)
        self.assertIn("Cliente comercial", approvals)
        self.assertIn("1 pendiente(s)", approvals)
        self.assertIn("Aprobaciones", approvals)
        approved = admin.post(
            f"/quote/{quote_id}/workflow",
            data={"csrf": admin_token, "action": "approve"},
        )
        self.assertEqual(approved.status_code, 302)
        with self.app.app_context(), connection() as database:
            quote = database.execute(
                """SELECT workflow_status,version,approved_version
                FROM quotes WHERE id=?""",
                (quote_id,),
            ).fetchone()
        self.assertEqual(quote["workflow_status"], "aprobada")
        self.assertEqual(quote["approved_version"], quote["version"])
        approvals = admin.get("/approvals").get_data(as_text=True)
        self.assertIn("0 pendiente(s)", approvals)
        self.assertNotIn("Cliente comercial", approvals)

        sent = estimator.post(
            f"/quote/{quote_id}/workflow",
            data={"csrf": estimator_token, "action": "send"},
        )
        self.assertEqual(sent.status_code, 302)
        with self.app.app_context(), connection() as database:
            status = database.execute(
                """SELECT workflow_status,request_status,data
                FROM quotes WHERE id=?""",
                (quote_id,),
            ).fetchone()
            expected_quote_date = local_today().isoformat()
        self.assertEqual(status["workflow_status"], "enviada")
        self.assertEqual(status["request_status"], "entregada")
        self.assertEqual(json.loads(status["data"])["fecha"], expected_quote_date)
        self.assertEqual(json.loads(status["data"])["estado"], "Abierta")

    def test_comments_attachments_permissions_and_dashboard(self):
        owner, owner_token = self.login()
        quote_id = self.create_quote(owner, owner_token, "Cliente tablero")
        self.assign_estimator(quote_id)
        estimator, estimator_token = self.login("estimator")
        comment = estimator.post(
            f"/quote/{quote_id}/comments",
            data={
                "csrf": estimator_token,
                "body": "Validar plazo con operaciones.",
            },
        )
        self.assertEqual(comment.status_code, 302)
        upload = estimator.post(
            f"/quote/{quote_id}/attachments",
            data={
                "csrf": estimator_token,
                "file": (BytesIO(b"%PDF-1.4\n% test attachment"), "respaldo.pdf"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(upload.status_code, 302)
        with self.app.app_context(), connection() as database:
            attachment_id = database.execute(
                "SELECT id FROM quote_attachments WHERE quote_id=?", (quote_id,)
            ).fetchone()[0]
        download = owner.get(f"/quote/{quote_id}/attachments/{attachment_id}")
        self.assertEqual(download.status_code, 200)
        download.close()
        other, _ = self.login("user2", "UserPassword123!")
        self.assertEqual(
            other.get(f"/quote/{quote_id}/attachments/{attachment_id}").status_code,
            403,
        )
        detail = owner.get(f"/quote/{quote_id}").get_data(as_text=True)
        self.assertIn("Validar plazo con operaciones", detail)
        self.assertIn("respaldo.pdf", detail)
        dashboard = owner.get("/dashboard").get_data(as_text=True)
        self.assertIn("Cliente tablero", dashboard)


if __name__ == "__main__":
    unittest.main()
