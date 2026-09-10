import importlib
import unittest
from unittest.mock import patch

from tests.environment import configure_test_environment


configure_test_environment()


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


if __name__ == "__main__":
    unittest.main()
