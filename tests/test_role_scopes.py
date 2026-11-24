import json
import os
import tempfile
import unittest
from io import BytesIO

from openpyxl import load_workbook
from werkzeug.security import generate_password_hash

from cotizaciones import create_app
from cotizaciones.db import connection, iso


class RoleScopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.app = create_app(
            {
                "TESTING": True,
                "DATABASE_PATH": os.path.join(cls.tmp.name, "test.db"),
                "UPLOAD_FOLDER": os.path.join(cls.tmp.name, "attachments"),
                "ADMIN_PASSWORD": "AdminPassword123!",
                "SECRET_KEY": "role-scope-test-secret",
            }
        )

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        with self.app.app_context(), connection() as database:
            database.execute("DELETE FROM audit")
            database.execute("DELETE FROM quotes")
            database.execute("DELETE FROM login_attempts")
            database.execute("DELETE FROM users WHERE role!='admin'")
            hector_id = database.execute(
                """INSERT INTO users(
                username,password_hash,role,first_name,last_name,email
                ) VALUES(?,?,?,?,?,?)""",
                (
                    "hector@its.cl",
                    generate_password_hash("UserPassword123!"),
                    "usuario",
                    "Hector",
                    "Martinez",
                    "hector@its.cl",
                ),
            ).lastrowid
            other_id = database.execute(
                """INSERT INTO users(
                username,password_hash,role,first_name,last_name,email
                ) VALUES(?,?,?,?,?,?)""",
                (
                    "ana@its.cl",
                    generate_password_hash("UserPassword123!"),
                    "usuario",
                    "Ana",
                    "Comercial",
                    "ana@its.cl",
                ),
            ).lastrowid
            estimator_id = database.execute(
                "INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                (
                    "cotizador",
                    generate_password_hash("UserPassword123!"),
                    "usuario",
                ),
            ).lastrowid
            database.execute(
                "INSERT INTO quote_estimators(user_id) VALUES(?)", (estimator_id,)
            )
            admin_id = database.execute(
                "SELECT id FROM users WHERE username='admin'"
            ).fetchone()[0]
            timestamp = iso()
            quote_ids = []
            for number, seller, client, amount in (
                ("ITS-LEG-000001", "Hector Martinez", "Cliente Hector", "1000000"),
                ("ITS-LEG-000002", "Ana Comercial", "Cliente Ana", "2500000"),
            ):
                data = {
                    "solicitud": "2026-01-10",
                    "fecha": "2026-01-15",
                    "vendedor": seller,
                    "cliente": client,
                    "descripcion": "Cotización histórica",
                    "clp": amount,
                    "usd": "",
                    "eur": "",
                    "margen": "",
                    "utilidad_usd": "",
                    "estado": "Adjudicada",
                }
                quote_ids.append(
                    database.execute(
                        """INSERT INTO quotes(
                        number,owner_id,created_at,last_user_edit_at,data,
                        request_status
                        ) VALUES(?,?,?,?,?,?)""",
                        (
                            number,
                            admin_id,
                            timestamp,
                            timestamp,
                            json.dumps(data, ensure_ascii=False),
                            "registrada",
                        ),
                    ).lastrowid
                )
        self.hector_id = hector_id
        self.other_id = other_id
        self.estimator_id = estimator_id
        self.hector_quote_id, self.other_quote_id = quote_ids

    def login(self, username, password="UserPassword123!"):
        client = self.app.test_client()
        client.get("/login")
        with client.session_transaction() as session:
            csrf = session["csrf"]
        response = client.post(
            "/login",
            data={"csrf": csrf, "username": username, "password": password},
        )
        self.assertEqual(response.status_code, 302)
        with client.session_transaction() as session:
            return client, session["csrf"]

    def test_seller_dashboard_and_listing_are_personal_but_ranking_is_global(self):
        seller, _ = self.login("hector@its.cl")

        dashboard = seller.get("/dashboard?year=2026").get_data(as_text=True)
        quote_card = dashboard.split("Cotizaciones del año", 1)[1].split(
            "</div>", 1
        )[0]
        won_card = dashboard.split("Adjudicadas", 1)[1].split("</div>", 1)[0]
        self.assertIn("<strong>1</strong>", quote_card)
        self.assertIn("<strong>1</strong>", won_card)
        self.assertIn("Cliente Hector", dashboard)
        self.assertNotIn("Cliente Ana", dashboard)
        ranking = dashboard.split("Top vendedores · 2026", 1)[1].split(
            "</section>", 1
        )[0]
        self.assertIn("Hector Martinez", ranking)
        self.assertIn("Ana Comercial", ranking)

        listing = seller.get("/").get_data(as_text=True)
        self.assertIn("Cliente Hector", listing)
        self.assertNotIn("Cliente Ana", listing)
        self.assertEqual(seller.get(f"/quote/{self.hector_quote_id}").status_code, 200)
        self.assertEqual(seller.get(f"/quote/{self.other_quote_id}").status_code, 403)

        exported = seller.get("/export")
        workbook = load_workbook(BytesIO(exported.data), read_only=True, data_only=True)
        try:
            rows = list(workbook.active.iter_rows(values_only=True))
        finally:
            workbook.close()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][0], "ITS-LEG-000001")

    def test_seller_is_read_only_after_creating_a_request(self):
        seller, csrf = self.login("hector@its.cl")
        detail = seller.get(f"/quote/{self.hector_quote_id}").get_data(as_text=True)
        self.assertIn("Solo lectura", detail)
        self.assertNotIn("Datos generales", detail)
        self.assertNotIn("Actualizar estado", detail)
        self.assertNotIn("Subir archivo", detail)
        self.assertNotIn(">Comentar<", detail)

        self.assertEqual(
            seller.post(
                f"/quote/{self.hector_quote_id}/edit",
                data={
                    "csrf": csrf,
                    "version": "1",
                    "cliente": "Manipulado",
                    "descripcion": "No autorizado",
                },
            ).status_code,
            403,
        )
        self.assertEqual(
            seller.post(
                f"/quote/{self.hector_quote_id}/commercial-status",
                data={
                    "csrf": csrf,
                    "estado": "Perdida",
                    "justificacion": "No autorizado",
                },
            ).status_code,
            403,
        )
        self.assertEqual(
            seller.post(
                f"/quote/{self.hector_quote_id}/comments",
                data={"csrf": csrf, "body": "No autorizado"},
            ).status_code,
            403,
        )

    def test_estimator_sees_all_quotes_but_only_edits_assigned_work(self):
        estimator, _ = self.login("cotizador")
        dashboard = estimator.get("/dashboard?year=2026").get_data(as_text=True)
        quote_card = dashboard.split("Cotizaciones del año", 1)[1].split(
            "</div>", 1
        )[0]
        self.assertIn("<strong>2</strong>", quote_card)
        listing = estimator.get("/").get_data(as_text=True)
        self.assertIn("Cliente Hector", listing)
        self.assertIn("Cliente Ana", listing)
        self.assertEqual(
            estimator.get(f"/quote/{self.hector_quote_id}").status_code, 200
        )
        self.assertEqual(
            estimator.get(f"/quote/{self.hector_quote_id}/edit").status_code, 403
        )


if __name__ == "__main__":
    unittest.main()
