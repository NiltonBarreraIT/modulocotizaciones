import json
import os
import tempfile
import unittest
from datetime import timedelta

from werkzeug.security import generate_password_hash

from cotizaciones import create_app
from cotizaciones.db import connection, iso, now
from cotizaciones.deadlines import local_today


class PhaseSevenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.app = create_app(
            {
                "TESTING": True,
                "DATABASE_PATH": os.path.join(cls.tmp.name, "test.db"),
                "UPLOAD_FOLDER": os.path.join(cls.tmp.name, "attachments"),
                "ADMIN_PASSWORD": "AdminPassword123!",
                "SECRET_KEY": "phase-seven-test-secret",
                "ASSIGNMENT_TARGET_HOURS": 1,
            }
        )

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        with self.app.app_context(), connection() as database:
            database.execute("DELETE FROM catalog_entries")
            database.execute("DELETE FROM audit")
            database.execute("DELETE FROM quotes")
            database.execute("DELETE FROM login_attempts")
            database.execute("DELETE FROM users WHERE role!='admin'")
            seller = database.execute(
                "INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                (
                    "vendedor",
                    generate_password_hash("UserPassword123!"),
                    "usuario",
                ),
            ).lastrowid
            estimator = database.execute(
                "INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                (
                    "jean",
                    generate_password_hash("UserPassword123!"),
                    "usuario",
                ),
            ).lastrowid
            database.execute(
                "INSERT INTO quote_estimators(user_id) VALUES(?)", (estimator,)
            )
        self.seller_id = seller
        self.estimator_id = estimator

    def test_admin_manages_master_catalogs_used_in_general_data(self):
        seller, _ = self.login("vendedor")
        self.assertEqual(seller.get("/catalogs").status_code, 403)

        admin, csrf = self.login("admin")
        page = admin.get("/catalogs").get_data(as_text=True)
        self.assertIn("Catálogos maestros", page)
        self.assertIn("Áreas", page)
        self.assertIn("Fábricas", page)

        area = admin.post(
            "/catalogs",
            data={"csrf": csrf, "kind": "area", "value": "Instrumentación"},
        )
        factory = admin.post(
            "/catalogs",
            data={"csrf": csrf, "kind": "fabrica", "value": "ABB"},
        )
        self.assertEqual(area.status_code, 302)
        self.assertEqual(factory.status_code, 302)

        seller, seller_csrf = self.login("vendedor")
        quote_id = self.create_request(
            seller, seller_csrf, "Cliente catálogo", "2026-11-18"
        )
        with self.app.app_context(), connection() as database:
            database.execute(
                "UPDATE quotes SET request_status='en_cotizacion' WHERE id=?",
                (quote_id,),
            )
            area_id = database.execute(
                "SELECT id FROM catalog_entries WHERE kind='area'"
            ).fetchone()[0]

        form = admin.get(f"/quote/{quote_id}/edit").get_data(as_text=True)
        self.assertIn('list="catalog-area"', form)
        self.assertIn("Instrumentación", form)
        self.assertIn("ABB", form)

        deactivated = admin.post(
            f"/catalogs/{area_id}",
            data={"csrf": csrf, "value": "Instrumentación", "active": "0"},
        )
        self.assertEqual(deactivated.status_code, 302)
        form = admin.get(f"/quote/{quote_id}/edit").get_data(as_text=True)
        self.assertNotIn('<option value="Instrumentación">', form)

    def test_admin_panel_groups_restricted_management_tools(self):
        seller, _ = self.login("vendedor")
        self.assertEqual(seller.get("/admin").status_code, 403)

        admin, _ = self.login("admin")
        response = admin.get("/admin")
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Panel de administración", page)
        for module in ("Catálogos", "Usuarios", "Importar", "Exportar"):
            self.assertIn(module, page)
        self.assertIn('href="/admin"', page)

    def login(self, username, password="UserPassword123!"):
        if username == "admin":
            password = "AdminPassword123!"
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

    def create_request(self, client, csrf, name, request_date, due_date=""):
        response = client.post(
            "/new",
            data={
                "csrf": csrf,
                "solicitud": request_date,
                "due_date": due_date,
                "cliente": name,
                "descripcion": f"Solicitud para {name}",
            },
        )
        self.assertEqual(response.status_code, 302)
        return int(response.headers["Location"].rsplit("/", 1)[-1])

    def test_deadline_age_overdue_dashboard_and_filters(self):
        with self.app.app_context():
            today = local_today()
        yesterday = (today - timedelta(days=1)).isoformat()
        seller, seller_csrf = self.login("vendedor")
        old_pending = self.create_request(
            seller, seller_csrf, "Pendiente atrasada", yesterday
        )
        active_overdue = self.create_request(
            seller, seller_csrf, "Cotización atrasada", yesterday, yesterday
        )
        fresh = self.create_request(
            seller, seller_csrf, "Solicitud vigente", today.isoformat()
        )

        with self.app.app_context(), connection() as database:
            database.execute(
                "UPDATE quotes SET created_at=? WHERE id=?",
                (iso(now() - timedelta(hours=2)), old_pending),
            )
            database.execute(
                """UPDATE quotes SET request_status='en_cotizacion',
                estimator_id=?,taken_at=? WHERE id=?""",
                (self.estimator_id, iso(), active_overdue),
            )
            saved_due = database.execute(
                "SELECT due_date FROM quotes WHERE id=?", (active_overdue,)
            ).fetchone()[0]
        self.assertEqual(saved_due, yesterday)

        admin, _ = self.login("admin")
        dashboard = admin.get("/dashboard").get_data(as_text=True)
        self.assertIn("Pendiente atrasada", dashboard)
        self.assertIn("Cotización atrasada", dashboard)
        self.assertIn("Pendiente de tomar fuera de plazo", dashboard)
        self.assertIn("Fecha límite vencida", dashboard)

        overdue = admin.get("/?timing=atrasadas").get_data(as_text=True)
        self.assertIn("Pendiente atrasada", overdue)
        self.assertIn("Cotización atrasada", overdue)
        self.assertNotIn("Solicitud vigente", overdue)
        self.assertIn("Atrasada", overdue)

        estimator_filter = admin.get("/?cotizador=jean").get_data(as_text=True)
        self.assertIn("Cotización atrasada", estimator_filter)
        self.assertNotIn("Solicitud vigente", estimator_filter)

        detail = admin.get(f"/quote/{active_overdue}").get_data(as_text=True)
        self.assertIn("Fecha límite", detail)
        self.assertIn("Antigüedad", detail)
        self.assertIn("Atrasada", detail)

        self.assertNotEqual(fresh, old_pending)

    def test_invalid_deadline_is_rejected_and_deadline_change_is_audited(self):
        seller, csrf = self.login("vendedor")
        invalid = seller.post(
            "/new",
            data={
                "csrf": csrf,
                "solicitud": "2026-11-17",
                "due_date": "2026-11-16",
                "cliente": "Plazo inválido",
                "descripcion": "La fecha no debe aceptarse",
            },
        )
        self.assertEqual(invalid.status_code, 400)

        quote_id = self.create_request(
            seller, csrf, "Editar plazo", "2026-11-17", "2026-11-18"
        )
        with self.app.app_context(), connection() as database:
            quote = database.execute(
                "SELECT version FROM quotes WHERE id=?", (quote_id,)
            ).fetchone()
        edited = seller.post(
            f"/quote/{quote_id}/edit",
            data={
                "csrf": csrf,
                "version": quote["version"],
                "solicitud": "2026-11-17",
                "due_date": "2026-11-20",
                "cliente": "Editar plazo",
                "descripcion": "Plazo corregido",
            },
        )
        self.assertEqual(edited.status_code, 302)
        with self.app.app_context(), connection() as database:
            saved = database.execute(
                "SELECT due_date FROM quotes WHERE id=?", (quote_id,)
            ).fetchone()[0]
            event = database.execute(
                """SELECT after FROM audit
                WHERE quote_id=? AND action='actualizar_plazo'""",
                (quote_id,),
            ).fetchone()
        self.assertEqual(saved, "2026-11-20")
        self.assertEqual(json.loads(event["after"])["due_date"], "2026-11-20")


if __name__ == "__main__":
    unittest.main()
