import hashlib
import json
import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from werkzeug.security import generate_password_hash

from cotizaciones import create_app
from cotizaciones.db import connection
from cotizaciones.deadlines import local_today


class PhaseSixTests(unittest.TestCase):
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
                "SECRET_KEY": "phase-six-test-secret",
            }
        )
        cls.template_path = Path(cls.app.config["COMMERCIAL_OFFER_TEMPLATE"])
        cls.template_content = cls.template_path.read_bytes()

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
            for username in ("vendedor", "jean"):
                cursor = database.execute(
                    "INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                    (
                        username,
                        generate_password_hash("UserPassword123!"),
                        "usuario",
                    ),
                )
                user_ids[username] = cursor.lastrowid
            database.execute(
                "INSERT INTO quote_estimators(user_id) VALUES(?)",
                (user_ids["jean"],),
            )
        self.user_ids = user_ids
        if os.path.isdir(self.upload_folder):
            for filename in os.listdir(self.upload_folder):
                os.unlink(os.path.join(self.upload_folder, filename))

    def login(self, username, password="UserPassword123!"):
        client = self.app.test_client()
        client.get("/login")
        with client.session_transaction() as current_session:
            token = current_session["csrf"]
        response = client.post(
            "/login",
            data={
                "csrf": token,
                "username": username,
                "password": password,
            },
        )
        self.assertEqual(response.status_code, 302)
        with client.session_transaction() as current_session:
            return client, current_session["csrf"]

    def create_and_take_request(self):
        seller, seller_token = self.login("vendedor")
        response = seller.post(
            "/new",
            data={
                "csrf": seller_token,
                "cliente": "Cliente fase 6",
                "descripcion": "Oferta comercial con el formato corporativo.",
            },
        )
        quote_id = int(response.headers["Location"].rsplit("/", 1)[-1])
        estimator, estimator_token = self.login("jean")
        taken = estimator.post(
            f"/quote/{quote_id}/take", data={"csrf": estimator_token}
        )
        self.assertEqual(taken.status_code, 302)
        return quote_id, seller, seller_token, estimator, estimator_token

    def test_information_delivery_and_correction_cycle(self):
        quote_id, seller, seller_token, estimator, estimator_token = (
            self.create_and_take_request()
        )

        self.assertEqual(seller.get("/commercial-offer-template").status_code, 403)
        template = estimator.get("/commercial-offer-template")
        self.assertEqual(template.status_code, 200)
        self.assertEqual(
            hashlib.sha256(template.data).hexdigest(),
            "3e48cf219b9f97db814298a4fe68823fd8f89b0b80bfd90d7dada7868d0cc96b",
        )
        template.close()

        information = estimator.post(
            f"/quote/{quote_id}/request-workflow",
            data={
                "csrf": estimator_token,
                "action": "need_info",
                "body": "Indicar dirección final de entrega.",
            },
        )
        self.assertEqual(information.status_code, 302)
        admin, admin_token = self.login("admin", "AdminPassword123!")
        response = admin.post(
            f"/quote/{quote_id}/request-workflow",
            data={
                "csrf": admin_token,
                "action": "resume",
                "body": "Entregar en bodega central del cliente.",
            },
        )
        self.assertEqual(response.status_code, 302)

        invalid = estimator.post(
            f"/quote/{quote_id}/deliver",
            data={
                "csrf": estimator_token,
                "file": (BytesIO(b"%PDF"), "oferta.pdf"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(invalid.status_code, 400)

        delivered = estimator.post(
            f"/quote/{quote_id}/deliver",
            data={
                "csrf": estimator_token,
                "body": "Oferta preparada según antecedentes recibidos.",
                "file": (BytesIO(self.template_content), "oferta_cliente.docx"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(delivered.status_code, 302)

        with self.app.app_context(), connection() as database:
            quote = database.execute(
                "SELECT request_status,data FROM quotes WHERE id=?", (quote_id,)
            ).fetchone()
            first_delivery = database.execute(
                "SELECT * FROM quote_deliveries WHERE quote_id=?", (quote_id,)
            ).fetchone()
            comment_count = database.execute(
                "SELECT count(*) FROM quote_comments WHERE quote_id=?", (quote_id,)
            ).fetchone()[0]
            expected_quote_date = local_today().isoformat()
        self.assertEqual(quote["request_status"], "entregada")
        self.assertEqual(json.loads(quote["data"])["fecha"], expected_quote_date)
        self.assertEqual(json.loads(quote["data"])["estado"], "Abierta")
        self.assertEqual(first_delivery["delivered_by"], self.user_ids["jean"])
        self.assertGreaterEqual(comment_count, 3)

        estimator_detail = estimator.get(f"/quote/{quote_id}").get_data(as_text=True)
        self.assertIn("Datos generales", estimator_detail)
        self.assertIn("Detalle comercial", estimator_detail)
        self.assertEqual(estimator.get(f"/quote/{quote_id}/edit").status_code, 200)
        self.assertEqual(
            estimator.get(f"/quote/{quote_id}/commercial").status_code,
            200,
        )

        seller_detail = seller.get(f"/quote/{quote_id}").get_data(as_text=True)
        self.assertIn("Descargar última oferta Word", seller_detail)
        download = seller.get(
            f"/quote/{quote_id}/attachments/{first_delivery['attachment_id']}"
        )
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download.data, self.template_content)
        download.close()

        correction = admin.post(
            f"/quote/{quote_id}/request-workflow",
            data={
                "csrf": admin_token,
                "action": "correction",
                "body": "Corregir plazo de entrega a seis semanas.",
            },
        )
        self.assertEqual(correction.status_code, 302)
        corrected = estimator.post(
            f"/quote/{quote_id}/deliver",
            data={
                "csrf": estimator_token,
                "body": "Se corrige el plazo solicitado.",
                "file": (BytesIO(self.template_content), "oferta_cliente_rev1.docx"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(corrected.status_code, 302)
        with self.app.app_context(), connection() as database:
            status = database.execute(
                "SELECT request_status FROM quotes WHERE id=?", (quote_id,)
            ).fetchone()[0]
            deliveries = database.execute(
                "SELECT count(*) FROM quote_deliveries WHERE quote_id=?", (quote_id,)
            ).fetchone()[0]
        self.assertEqual(status, "entregada")
        self.assertEqual(deliveries, 2)


if __name__ == "__main__":
    unittest.main()
