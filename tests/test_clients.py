import os
import tempfile
import unittest

from werkzeug.security import generate_password_hash

from cotizaciones import create_app
from cotizaciones.db import connection


class ClientPortfolioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.app = create_app(
            {
                "TESTING": True,
                "DATABASE_PATH": os.path.join(cls.tmp.name, "clients.db"),
                "ADMIN_PASSWORD": "AdminPassword123!",
                "SECRET_KEY": "client-portfolio-tests",
            }
        )
        with cls.app.app_context(), connection() as database:
            test_sellers = (
                ("seller1@its.cl", "Ana"),
                ("seller2@its.cl", "Luis"),
            )
            for username, first_name in test_sellers:
                database.execute(
                    """INSERT INTO users(
                    username,password_hash,role,first_name,last_name,email,phone
                    ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        username,
                        generate_password_hash("UserPassword123!"),
                        "usuario",
                        first_name,
                        "Vendedor",
                        username,
                        "+56912345678",
                    ),
                )

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def login(self, username, password):
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

    def setUp(self):
        with self.app.app_context(), connection() as database:
            database.execute("DELETE FROM audit")
            database.execute("DELETE FROM quotes")
            database.execute("DELETE FROM client_sellers")
            database.execute("DELETE FROM clients")

    def test_admin_assigns_clients_and_server_enforces_portfolio(self):
        admin, token = self.login("admin", "AdminPassword123!")
        with self.app.app_context(), connection() as database:
            sellers = database.execute(
                "SELECT id,username FROM users WHERE username LIKE 'seller%'"
            ).fetchall()
            seller_ids = {row["username"]: row["id"] for row in sellers}

        created = admin.post(
            "/clients",
            data={
                "csrf": token,
                "name": "Cliente Norte",
                "tax_id": "76.111.111-1",
                "seller_ids": str(seller_ids["seller1@its.cl"]),
            },
        )
        self.assertEqual(created.status_code, 302)
        admin.post(
            "/clients",
            data={
                "csrf": token,
                "name": "Cliente Sur",
                "tax_id": "76.222.222-2",
                "seller_ids": str(seller_ids["seller2@its.cl"]),
            },
        )

        with self.app.app_context(), connection() as database:
            rows = database.execute(
                "SELECT id,name FROM clients ORDER BY name"
            ).fetchall()
            client_ids = {row["name"]: row["id"] for row in rows}

        seller, seller_token = self.login("seller1@its.cl", "UserPassword123!")
        form = seller.get("/new").get_data(as_text=True)
        self.assertIn("Cliente Norte", form)
        self.assertNotIn("Cliente Sur", form)

        allowed = seller.post(
            "/new",
            data={
                "csrf": seller_token,
                "client_id": client_ids["Cliente Norte"],
                "descripcion": "Solicitud autorizada",
                "client_email": "compras@norte.cl",
                "client_phone": "+56 2 2345 6789",
            },
        )
        self.assertEqual(allowed.status_code, 302)
        with self.app.app_context(), connection() as database:
            quote = database.execute(
                "SELECT id,version,client_id,data,client_email,client_phone FROM quotes"
            ).fetchone()
            self.assertEqual(quote["client_id"], client_ids["Cliente Norte"])
            self.assertIn("Cliente Norte", quote["data"])
            self.assertEqual(quote["client_email"], "compras@norte.cl")
            self.assertEqual(quote["client_phone"], "+56 2 2345 6789")

        admin, admin_token = self.login("admin", "AdminPassword123!")
        edited = admin.post(
            f"/quote/{quote['id']}/edit",
            data={
                "csrf": admin_token,
                "version": quote["version"],
                "client_id": client_ids["Cliente Norte"],
                "solicitud": "2026-11-26",
                "descripcion": "Solicitud autorizada actualizada",
                "client_email": "proyectos@norte.cl",
                "client_phone": "",
            },
        )
        self.assertEqual(edited.status_code, 302)
        with self.app.app_context(), connection() as database:
            updated = database.execute(
                "SELECT client_email,client_phone FROM quotes WHERE id=?",
                (quote["id"],),
            ).fetchone()
        self.assertEqual(updated["client_email"], "proyectos@norte.cl")
        self.assertEqual(updated["client_phone"], "")

        rejected = seller.post(
            "/new",
            data={
                "csrf": seller_token,
                "client_id": client_ids["Cliente Sur"],
                "descripcion": "Intento fuera de cartera",
                "client_email": "contacto@sur.cl",
            },
        )
        self.assertEqual(rejected.status_code, 403)

    def test_only_admin_can_manage_clients(self):
        seller, _ = self.login("seller1@its.cl", "UserPassword123!")
        self.assertEqual(seller.get("/clients").status_code, 403)


if __name__ == "__main__":
    unittest.main()
