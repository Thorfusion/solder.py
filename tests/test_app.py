import importlib
import unittest
from unittest.mock import patch

from tests.environment import configure_test_environment


configure_test_environment()

from models.mod import DuplicateModError  # noqa: E402


class ApplicationSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Importing asite normally starts the database session-cleanup thread and
        # creates an S3 client. Neither external service belongs in a smoke test.
        with (
            patch("models.session.Session.start_session_loop"),
            patch("boto3.client"),
        ):
            cls.app_module = importlib.import_module("app")

        cls.app_module.app.config.update(TESTING=True)
        cls.client = cls.app_module.app.test_client()

    def test_application_exposes_its_version(self):
        self.assertEqual(self.app_module.__version__, "1.7.4")

    def test_api_blueprint_is_registered(self):
        response = self.client.get("/api/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["api"], "solder.py")

    def test_management_routes_are_registered(self):
        routes = {rule.rule for rule in self.app_module.app.url_map.iter_rules()}

        self.assertIn("/login", routes)
        self.assertIn("/modlibrary", routes)
        self.assertIn("/api/modpack/<slugstring>/<buildstring>", routes)

    def test_unknown_route_uses_the_solder_404_page(self):
        response = self.client.get("/this-route-does-not-exist")

        self.assertEqual(response.status_code, 404)
        self.assertIn(b"404", response.data)

    def test_unknown_api_route_returns_json(self):
        response = self.client.get("/api/this-route-does-not-exist")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json(), {"error": "Not Found"})

    def test_wrong_api_method_returns_json(self):
        response = self.client.post("/api/modpack")

        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.get_json(), {"error": "Method Not Allowed"})

    def test_duplicate_mod_shows_an_error_and_preserves_the_form(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        form = {
            "pretty_name": "Existing Mod",
            "name": "existing-mod",
            "author": "Test Author",
            "description": "Test description",
            "link": "https://example.test/mod",
            "flexRadioDefault": "BOTH",
            "type": "MOD",
            "internal_note": "Keep this value",
        }
        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Mod.new", side_effect=DuplicateModError("existing-mod")),
            patch("asite.Build.get_marked_build", return_value=0),
            patch("asite.Modpack.get_by_pinned", return_value=[]),
        ):
            response = self.client.post("/newmod", data=form)

        self.assertEqual(response.status_code, 409)
        self.assertIn(b'existing-mod&#34; already exists', response.data)
        self.assertIn(b'value="Existing Mod"', response.data)
        self.assertIn(b"Keep this value", response.data)


if __name__ == "__main__":
    unittest.main()
