import os
import tempfile
import unittest
from datetime import timedelta

from werkzeug.security import generate_password_hash

from cotizaciones import create_app
from cotizaciones.db import connection, iso, now


class PhaseOneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.database_path = os.path.join(cls.tmp.name, "test.db")
        cls.config = {
            "TESTING": True,
            "DATABASE_PATH": cls.database_path,
            "ADMIN_PASSWORD": "AdminPassword123!",
            "SECRET_KEY": "phase-one-test-secret",
        }
        cls.app = create_app(cls.config)

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
            database.execute(
                "INSERT INTO users(username,password_hash,role) VALUES(?,?,?)",
                ("user2", generate_password_hash("UserPassword123!"), "usuario"),
            )

    def login(self, username, password):
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

    def test_factory_applies_migrations_without_losing_users(self):
        with self.app.app_context(), connection() as database:
            versions = database.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
            users_before = database.execute("SELECT count(*) FROM users").fetchone()[0]
        self.assertEqual(
            [row["version"] for row in versions],
            [
                "001_initial",
                "002_login_attempts",
                "003_integrity_and_import_preview",
                "004_commercial_workflow",
                "005_request_intake",
                "006_assignment_workflow",
                "007_offer_delivery",
                "008_deadlines_and_overdue",
                "009_net_commercial_values",
                "010_sync_sent_requests",
                "011_open_initial_commercial_status",
                "012_commercial_delivery_time",
                "013_quote_item_details",
                "014_commercial_supply_scope",
                "015_commercial_profit_fields",
                "016_quote_correlative",
                "017_commercial_status_after_delivery",
                "018_commercial_collaboration",
                "019_user_profiles",
                "020_master_catalogs",
                "021_password_change_required",
                "022_clients_and_portfolios",
            ],
        )

        second_app = create_app(self.config)
        with second_app.app_context(), connection() as database:
            users_after = database.execute("SELECT count(*) FROM users").fetchone()[0]
        self.assertEqual(users_after, users_before)

    def test_users_cannot_access_each_others_quotes(self):
        owner, owner_csrf = self.login("user1", "UserPassword123!")
        other, _ = self.login("user2", "UserPassword123!")
        admin, _ = self.login("admin", "AdminPassword123!")

        response = owner.post(
            "/new",
            data={
                "csrf": owner_csrf,
                "cliente": "Cliente privado",
                "descripcion": "Descripción",
            },
        )
        self.assertEqual(response.status_code, 302)
        quote_path = response.headers["Location"]
        self.assertEqual(owner.get(quote_path).status_code, 200)
        self.assertEqual(other.get(quote_path).status_code, 403)
        self.assertEqual(admin.get(quote_path).status_code, 200)
        self.assertEqual(other.get("/users").status_code, 403)

    def test_admin_creates_and_updates_complete_user_profile(self):
        admin, token = self.login("admin", "AdminPassword123!")
        created = admin.post(
            "/users",
            data={
                "csrf": token,
                "first_name": "María",
                "last_name": "González",
                "email": "maria.gonzalez@i-tsolutions.cl",
                "phone": "+56 9 1234 5678",
                "password": "ProfilePassword123!",
                "role": "usuario",
            },
        )
        self.assertEqual(created.status_code, 200)
        self.assertIn("María", created.get_data(as_text=True))
        with self.app.app_context(), connection() as database:
            profile = database.execute(
                "SELECT * FROM users WHERE email=?",
                ("maria.gonzalez@i-tsolutions.cl",),
            ).fetchone()
        self.assertEqual(profile["first_name"], "María")
        self.assertEqual(profile["last_name"], "González")
        self.assertEqual(profile["phone"], "+56 9 1234 5678")
        self.assertEqual(profile["must_change_password"], 1)

        updated = admin.post(
            f"/users/{profile['id']}/profile",
            data={
                "csrf": token,
                "first_name": "María José",
                "last_name": "González",
                "email": "maria.gonzalez@i-tsolutions.cl",
                "phone": "+56 9 8765 4321",
                "role": "usuario",
            },
        )
        self.assertEqual(updated.status_code, 302)
        with self.app.app_context(), connection() as database:
            profile = database.execute(
                "SELECT * FROM users WHERE id=?", (profile["id"],)
            ).fetchone()
        self.assertEqual(profile["first_name"], "María José")
        self.assertEqual(profile["phone"], "+56 9 8765 4321")

        user, user_token = self.login(
            "maria.gonzalez@i-tsolutions.cl", "ProfilePassword123!"
        )
        forced = user.get("/")
        self.assertEqual(forced.status_code, 302)
        self.assertTrue(forced.headers["Location"].endswith("/change-password"))
        unchanged = user.post(
            "/change-password",
            data={
                "csrf": user_token,
                "password": "ProfilePassword123!",
                "password_confirmation": "ProfilePassword123!",
            },
        )
        self.assertEqual(unchanged.status_code, 400)
        changed = user.post(
            "/change-password",
            data={
                "csrf": user_token,
                "password": "PersonalPassword456!",
                "password_confirmation": "PersonalPassword456!",
            },
        )
        self.assertEqual(changed.status_code, 302)
        self.assertEqual(user.get("/").status_code, 200)

        admin_reset = admin.post(
            f"/users/{profile['id']}/password",
            data={
                "csrf": token,
                "password": "TemporaryPassword789!",
                "password_confirmation": "TemporaryPassword789!",
            },
        )
        self.assertEqual(admin_reset.status_code, 302)
        with self.app.app_context(), connection() as database:
            reset_profile = database.execute(
                "SELECT must_change_password FROM users WHERE id=?", (profile["id"],)
            ).fetchone()
        self.assertEqual(reset_profile["must_change_password"], 1)

    def test_edit_window_expires_for_owner_but_not_admin(self):
        owner, owner_csrf = self.login("user1", "UserPassword123!")
        response = owner.post(
            "/new",
            data={
                "csrf": owner_csrf,
                "cliente": "Cliente",
                "descripcion": "Descripción",
            },
        )
        quote_id = int(response.headers["Location"].rsplit("/", 1)[-1])
        with self.app.app_context(), connection() as database:
            database.execute(
                "UPDATE quotes SET last_user_edit_at=? WHERE id=?",
                (iso(now() - timedelta(hours=25)), quote_id),
            )

        self.assertEqual(owner.get(f"/quote/{quote_id}/edit").status_code, 403)
        admin, _ = self.login("admin", "AdminPassword123!")
        self.assertEqual(admin.get(f"/quote/{quote_id}/edit").status_code, 200)


if __name__ == "__main__":
    unittest.main()
