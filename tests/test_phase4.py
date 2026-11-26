import json
import os
import tempfile
import unittest
from io import BytesIO

from werkzeug.security import generate_password_hash

from cotizaciones import create_app
from cotizaciones.db import connection


class PhaseFourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.upload_folder = os.path.join(cls.tmp.name, "attachments")
        cls.app = create_app(
            {
                "TESTING": True,
                "DATABASE_PATH": os.path.join(cls.tmp.name, "test.db"),
                "UPLOAD_FOLDER": cls.upload_folder,
                "ADMIN_PASSWORD": "AdminPassword123!",
                "SECRET_KEY": "phase-four-test-secret",
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
            database.execute(
                "INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                (
                    "vendedor1",
                    generate_password_hash("UserPassword123!"),
                    "usuario",
                ),
            )
            database.execute(
                "INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                (
                    "vendedor2",
                    generate_password_hash("UserPassword123!"),
                    "usuario",
                ),
            )
        if os.path.isdir(self.upload_folder):
            for filename in os.listdir(self.upload_folder):
                os.unlink(os.path.join(self.upload_folder, filename))

    def login(self, username="vendedor1"):
        client = self.app.test_client()
        client.get("/login")
        with client.session_transaction() as current_session:
            token = current_session["csrf"]
        response = client.post(
            "/login",
            data={
                "csrf": token,
                "username": username,
                "password": "UserPassword123!",
            },
        )
        self.assertEqual(response.status_code, 302)
        with client.session_transaction() as current_session:
            return client, current_session["csrf"]

    def test_simplified_request_uses_authenticated_seller_and_initial_attachment(self):
        client, token = self.login()
        form = client.get("/new").get_data(as_text=True)
        self.assertIn("Nueva solicitud de cotización", form)
        self.assertIn("Requerimiento o comentarios", form)
        self.assertIn('name="client_contact_name"', form)
        self.assertIn('name="client_email"', form)
        self.assertIn('name="client_phone"', form)
        self.assertNotIn("Monto CLP", form)
        self.assertIn("data-platform-revision-url", form)
        initial_revision = client.get("/live/revision").get_json()["revision"]

        response = client.post(
            "/new",
            data={
                "csrf": token,
                "solicitud": "2026-11-17",
                "vendedor": "usuario-manipulado",
                "cliente": "Cliente fase 4",
                "descripcion": "Preparar una propuesta técnica y comercial.",
                "client_contact_name": "Camila Cliente",
                "client_email": "contacto.fase4@cliente.cl",
                "client_phone": "+56 9 1234 5678",
                "file": (BytesIO(b"documento de respaldo"), "antecedentes.docx"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 302)
        quote_id = int(response.headers["Location"].rsplit("/", 1)[-1])
        current_revision = client.get("/live/revision").get_json()["revision"]
        self.assertGreater(current_revision, initial_revision)

        with self.app.app_context(), connection() as database:
            quote = database.execute(
                "SELECT * FROM quotes WHERE id=?", (quote_id,)
            ).fetchone()
            attachment = database.execute(
                "SELECT original_name FROM quote_attachments WHERE quote_id=?",
                (quote_id,),
            ).fetchone()
            event = database.execute(
                "SELECT action FROM audit WHERE quote_id=? ORDER BY id LIMIT 1",
                (quote_id,),
            ).fetchone()

        data = json.loads(quote["data"])
        self.assertTrue(quote["number"].startswith("ITS-SOL-"))
        self.assertEqual(quote["request_status"], "pendiente_asignacion")
        self.assertEqual(data["vendedor"], "vendedor1")
        self.assertEqual(data["solicitud"], "2026-11-17")
        self.assertEqual(data["estado"], "")
        self.assertEqual(quote["client_contact_name"], "Camila Cliente")
        self.assertEqual(quote["client_email"], "contacto.fase4@cliente.cl")
        self.assertEqual(quote["client_phone"], "+56 9 1234 5678")
        self.assertEqual(attachment["original_name"], "antecedentes.docx")
        self.assertEqual(event["action"], "crear_solicitud")

        detail = client.get(response.headers["Location"]).get_data(as_text=True)
        self.assertIn("Pendiente de asignación", detail)
        self.assertIn("Solo lectura", detail)
        self.assertIn("antecedentes.docx", detail)
        self.assertIn("Camila Cliente", detail)
        self.assertIn("contacto.fase4@cliente.cl", detail)
        self.assertIn("+56 9 1234 5678", detail)
        self.assertIn("creó la solicitud", detail)
        self.assertNotIn("crear_solicitud", detail)
        listing = client.get("/?request_status=pendiente_asignacion").get_data(
            as_text=True
        )
        self.assertIn("Cliente fase 4", listing)

    def test_client_email_is_required_and_validated_by_server(self):
        client, token = self.login()
        base = {
            "csrf": token,
            "cliente": "Cliente contacto",
            "descripcion": "Solicitud con datos de contacto",
            "client_contact_name": "Contacto Cliente",
        }

        missing_name = client.post(
            "/new",
            data={
                "csrf": token,
                "cliente": "Cliente contacto",
                "descripcion": "Solicitud sin nombre de contacto",
                "client_email": "contacto@cliente.cl",
            },
        )
        missing = client.post("/new", data=base)
        invalid = client.post(
            "/new", data=base | {"client_email": "correo-invalido"}
        )

        self.assertEqual(missing_name.status_code, 400)
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(invalid.status_code, 400)
        with self.app.app_context(), connection() as database:
            count = database.execute("SELECT count(*) FROM quotes").fetchone()[0]
        self.assertEqual(count, 0)

    def test_invalid_initial_attachment_does_not_create_request(self):
        client, token = self.login()
        response = client.post(
            "/new",
            data={
                "csrf": token,
                "cliente": "Cliente inválido",
                "descripcion": "Solicitud con adjunto no permitido",
                "client_contact_name": "Contacto Cliente",
                "client_email": "contacto@cliente.cl",
                "file": (BytesIO(b"contenido"), "archivo.exe"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)
        with self.app.app_context(), connection() as database:
            count = database.execute("SELECT count(*) FROM quotes").fetchone()[0]
        self.assertEqual(count, 0)

    def test_seller_cannot_update_commercial_status(self):
        client, token = self.login()
        response = client.post(
            "/new",
            data={
                "csrf": token,
                "cliente": "Cliente seguimiento",
                "descripcion": "Solicitud que requiere seguimiento comercial",
                "client_contact_name": "Contacto Cliente",
                "client_email": "contacto@cliente.cl",
            },
        )
        quote_id = int(response.headers["Location"].rsplit("/", 1)[-1])

        blocked_before_delivery = client.post(
            f"/quote/{quote_id}/commercial-status",
            data={
                "csrf": token,
                "estado": "Postergada",
                "justificacion": "Aún no se entrega la oferta.",
            },
        )
        self.assertEqual(blocked_before_delivery.status_code, 403)
        with self.app.app_context(), connection() as database:
            saved = database.execute(
                "SELECT data FROM quotes WHERE id=?", (quote_id,)
            ).fetchone()
        self.assertEqual(json.loads(saved["data"])["estado"], "")


if __name__ == "__main__":
    unittest.main()
