"""Application-level smoke tests: accounts, access control, and the TinyFish fallback.

Run from the EditMatch folder with:
    python -m unittest discover -s tests -v
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app as app_module  # noqa: E402
import database  # noqa: E402
import tinyfish_service as tinyfish  # noqa: E402

PASSWORD = "Password123"


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        app_module.app.config.update(
            TESTING=True,
            WTF_CSRF_ENABLED=False,  # CSRF is tested in the browser, not here
            DATABASE=str(Path(self.temp_dir.name) / "test.db"),
            UPLOAD_FOLDER=str(Path(self.temp_dir.name) / "uploads"),
        )
        with app_module.app.app_context():
            database.init_db()
        self.client = app_module.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def register(self, email, role, name="Test User"):
        self.client.post("/logout")  # register redirects logged-in users, so log out first
        return self.client.post("/register", data={
            "full_name": name, "email": email, "password": PASSWORD,
            "confirm_password": PASSWORD, "role": role,
        })

    def query(self, sql, params=()):
        with app_module.app.app_context():
            return database.get_db().execute(sql, params).fetchall()

    def test_registration_stores_a_hash_not_the_password(self):
        self.register("editor@example.com", "editor")
        rows = self.query("SELECT password_hash FROM users WHERE email = ?", ("editor@example.com",))
        self.assertEqual(len(rows), 1)
        self.assertNotEqual(rows[0][0], PASSWORD)
        self.assertTrue(rows[0][0].startswith("scrypt:") or rows[0][0].startswith("pbkdf2:"))

    def test_duplicate_email_is_rejected(self):
        self.register("same@example.com", "editor")
        response = self.register("same@example.com", "client")
        self.assertIn("already exists", response.get_data(as_text=True))
        count = self.query("SELECT COUNT(*) FROM users WHERE email = ?", ("same@example.com",))[0][0]
        self.assertEqual(count, 1)

    def test_logged_out_user_is_sent_to_login(self):
        response = self.client.get("/editor/dashboard")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response.headers["Location"])

    def test_client_cannot_open_editor_dashboard(self):
        self.register("client@example.com", "client")
        response = self.client.get("/editor/dashboard")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/client/dashboard", response.headers["Location"])

    def test_login_with_wrong_password_is_refused(self):
        self.register("login@example.com", "editor")
        self.client.post("/logout")
        response = self.client.post("/login", data={"email": "login@example.com", "password": "wrong"})
        self.assertIn("Email or password is incorrect", response.get_data(as_text=True))

    def test_editor_profile_page_hides_contact_email_from_visitors(self):
        self.register("hidden@example.com", "editor")
        profile_row = self.query("SELECT id FROM users WHERE email = ?", ("hidden@example.com",))
        self.assertEqual(len(profile_row), 1)
        with app_module.app.app_context():
            db = database.get_db()
            user_id = db.execute("SELECT id FROM users WHERE email = ?", ("hidden@example.com",)).fetchone()[0]
            db.execute(
                "INSERT INTO editor_profiles (user_id, contact_email, categories, availability) VALUES (?, ?, ?, ?)",
                (user_id, "private-contact@example.com", "Gaming", "Available"),
            )
            db.commit()
            profile_id = db.execute("SELECT id FROM editor_profiles WHERE user_id = ?", (user_id,)).fetchone()[0]
        self.client.post("/logout")
        page = self.client.get(f"/editor/{profile_id}").get_data(as_text=True)
        self.assertNotIn("private-contact@example.com", page)

    def test_hire_page_requires_a_client_login(self):
        response = self.client.get("/hire")
        self.assertEqual(response.status_code, 302)


class TinyFishFallbackTests(unittest.TestCase):
    def test_localhost_is_rejected(self):
        url, error = tinyfish.validate_public_url("http://localhost:8000/portfolio")
        self.assertIsNone(url)
        self.assertIsNotNone(error)

    def test_private_ip_address_is_rejected(self):
        url, error = tinyfish.validate_public_url("http://192.168.1.10/work")
        self.assertIsNone(url)
        self.assertIsNotNone(error)

    def test_non_http_scheme_is_rejected(self):
        url, error = tinyfish.validate_public_url("file:///C:/secrets.txt")
        self.assertIsNone(url)
        self.assertIsNotNone(error)

    def test_missing_key_reports_not_configured_without_calling_tinyfish(self):
        with mock.patch.dict(os.environ, {"TINYFISH_API_KEY": ""}):
            self.assertFalse(tinyfish.is_configured())
            outcome = tinyfish.analyse_editor_portfolio("https://example.com/portfolio")
        self.assertEqual(outcome["status"], "not_configured")
        self.assertIsNone(outcome["data"])


if __name__ == "__main__":
    unittest.main()
