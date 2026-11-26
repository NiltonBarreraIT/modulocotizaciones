import json
import os
import tempfile
import unittest

from werkzeug.security import generate_password_hash

from cotizaciones import create_app
from cotizaciones.db import connection


class PhaseFiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.app = create_app(
            {
                "TESTING": True,
                "DATABASE_PATH": os.path.join(cls.tmp.name, "test.db"),
                "UPLOAD_FOLDER": os.path.join(cls.tmp.name, "attachments"),
                "ADMIN_PASSWORD": "AdminPassword123!",
                "SECRET_KEY": "phase-five-test-secret",
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
            user_ids = {}
            for username in ("vendedor", "jean", "emilio"):
                cursor = database.execute(
                    "INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                    (
                        username,
                        generate_password_hash("UserPassword123!"),
                        "usuario",
                    ),
                )
                user_ids[username] = cursor.lastrowid
            database.executemany(
                "INSERT INTO quote_estimators(user_id) VALUES(?)",
                [(user_ids["jean"],), (user_ids["emilio"],)],
            )
        self.user_ids = user_ids

    def login(self, username, password="UserPassword123!"):
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

    def create_request(self):
        seller, token = self.login("vendedor")
        response = seller.post(
            "/new",
            data={
                "csrf": token,
                "solicitud": "2026-11-17",
                "cliente": "Cliente asignación",
                "descripcion": "Preparar cotización de instrumentación.",
                "client_contact_name": "Contacto Cliente",
                "client_email": "contacto@cliente.cl",
            },
        )
        self.assertEqual(response.status_code, 302)
        return int(response.headers["Location"].rsplit("/", 1)[-1])

    def test_estimator_takes_request_atomically_and_admin_reassigns_it(self):
        quote_id = self.create_request()
        jean, jean_token = self.login("jean")
        emilio, emilio_token = self.login("emilio")

        jean_inbox = jean.get("/").get_data(as_text=True)
        self.assertIn("Cliente asignación", jean_inbox)
        self.assertIn("Tomar solicitud", jean_inbox)
        taken = jean.post(f"/quote/{quote_id}/take", data={"csrf": jean_token})
        self.assertEqual(taken.status_code, 302)

        with self.app.app_context(), connection() as database:
            quote = database.execute(
                "SELECT estimator_id,taken_at,request_status FROM quotes WHERE id=?",
                (quote_id,),
            ).fetchone()
            event = database.execute(
                "SELECT action,after FROM audit WHERE quote_id=? ORDER BY id DESC",
                (quote_id,),
            ).fetchone()
        self.assertEqual(quote["estimator_id"], self.user_ids["jean"])
        self.assertIsNotNone(quote["taken_at"])
        self.assertEqual(quote["request_status"], "en_cotizacion")
        self.assertEqual(event["action"], "tomar_solicitud")
        self.assertEqual(json.loads(event["after"])["estimator"], "jean")

        jean_detail = jean.get(f"/quote/{quote_id}").get_data(as_text=True)
        self.assertIn("Datos generales", jean_detail)
        self.assertIn("Detalle comercial", jean_detail)
        self.assertIn("Edición asignada al cotizador", jean_detail)
        self.assertEqual(jean.get(f"/quote/{quote_id}/edit").status_code, 200)
        self.assertEqual(
            jean.get(f"/quote/{quote_id}/commercial").status_code,
            200,
        )

        conflict = emilio.post(f"/quote/{quote_id}/take", data={"csrf": emilio_token})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(emilio.get(f"/quote/{quote_id}").status_code, 200)
        self.assertIn("Cliente asignación", emilio.get("/").get_data(as_text=True))

        admin, admin_token = self.login("admin", "AdminPassword123!")
        admin_detail = admin.get(f"/quote/{quote_id}").get_data(as_text=True)
        self.assertIn("Datos generales", admin_detail)
        self.assertIn("Detalle comercial", admin_detail)
        blocked_role_change = admin.post(
            f"/users/{self.user_ids['jean']}/role",
            data={"csrf": admin_token, "role": "usuario"},
        )
        self.assertEqual(blocked_role_change.status_code, 400)
        reassigned = admin.post(
            f"/quote/{quote_id}/reassign",
            data={
                "csrf": admin_token,
                "estimator_id": str(self.user_ids["emilio"]),
            },
        )
        self.assertEqual(reassigned.status_code, 302)
        self.assertEqual(jean.get(f"/quote/{quote_id}").status_code, 200)
        emilio_detail = emilio.get(f"/quote/{quote_id}")
        self.assertEqual(emilio_detail.status_code, 200)
        self.assertIn("emilio", emilio_detail.get_data(as_text=True))

        unassigned = admin.post(
            f"/quote/{quote_id}/reassign",
            data={"csrf": admin_token, "estimator_id": ""},
        )
        self.assertEqual(unassigned.status_code, 302)
        with self.app.app_context(), connection() as database:
            quote = database.execute(
                "SELECT estimator_id,taken_at,request_status FROM quotes WHERE id=?",
                (quote_id,),
            ).fetchone()
        self.assertIsNone(quote["estimator_id"])
        self.assertIsNone(quote["taken_at"])
        self.assertEqual(quote["request_status"], "pendiente_asignacion")

    def test_admin_can_promote_existing_user_to_estimator_role(self):
        admin, token = self.login("admin", "AdminPassword123!")
        response = admin.post(
            f"/users/{self.user_ids['vendedor']}/role",
            data={"csrf": token, "role": "cotizador"},
        )
        self.assertEqual(response.status_code, 302)
        promoted, _ = self.login("vendedor")
        users_page = promoted.get("/users")
        self.assertEqual(users_page.status_code, 403)
        with self.app.app_context(), connection() as database:
            membership = database.execute(
                "SELECT 1 FROM quote_estimators WHERE user_id=?",
                (self.user_ids["vendedor"],),
            ).fetchone()
        self.assertIsNotNone(membership)


if __name__ == "__main__":
    unittest.main()
