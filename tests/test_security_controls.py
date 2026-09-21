import re
import unittest
from unittest.mock import Mock, patch

from flask import Blueprint, Flask

from tests.environment import configure_test_environment


configure_test_environment()

from models.cache_revision import CacheRevision  # noqa: E402
from models.csrf import CsrfProtection  # noqa: E402
from models.login_throttle import LoginThrottle  # noqa: E402


class CsrfProtectionTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.secret_key = "csrf-test-key"
        management = Blueprint("asite", __name__)

        @management.route("/form", methods=["GET", "POST"])
        def form():
            return (
                '<form method="post"><button>Save</button></form>'
                '<form method="get"><button>Read</button></form>'
            )

        app.register_blueprint(management)
        app.before_request(CsrfProtection.verify_request)
        app.after_request(CsrfProtection.inject_forms)
        self.client = app.test_client()

    def test_post_forms_receive_one_session_bound_token(self):
        response = self.client.get("/form")
        page = response.get_data(as_text=True)

        tokens = re.findall(
            r'name="_csrf_token" value="([^"]+)"', page
        )
        self.assertEqual(len(tokens), 1)
        self.assertNotIn(
            '<form method="get"><input type="hidden"', page
        )

        accepted = self.client.post("/form", data={"_csrf_token": tokens[0]})
        self.assertEqual(accepted.status_code, 200)

    def test_missing_or_wrong_token_is_rejected(self):
        self.client.get("/form")

        self.assertEqual(self.client.post("/form").status_code, 400)
        self.assertEqual(
            self.client.post("/form", data={"_csrf_token": "wrong"}).status_code,
            400,
        )


class LoginThrottleTests(unittest.TestCase):
    def test_account_budget_cannot_be_reset_by_changing_address(self):
        first = LoginThrottle._entries("ExampleUser", "192.0.2.1")
        second = LoginThrottle._entries("exampleuser", "198.51.100.2")

        self.assertEqual(first[0], second[0])
        self.assertNotEqual(first[1][0], second[1][0])

    def test_address_budget_is_shared_across_account_names(self):
        first = LoginThrottle._entries("first", "192.0.2.1")
        second = LoginThrottle._entries("second", "192.0.2.1")

        self.assertNotEqual(first[0][0], second[0][0])
        self.assertEqual(first[1], second[1])
        self.assertNotIn("first", first[0][0])
        self.assertEqual(len(first[0][0]), 64)

    def test_success_clears_only_the_account_budget(self):
        cursor = Mock()
        connection = Mock()
        connection.cursor.return_value = cursor

        with patch(
            "models.login_throttle.Database.get_connection",
            return_value=connection,
        ):
            LoginThrottle.success("ExampleUser", "192.0.2.1")

        account_key = LoginThrottle._entries(
            "ExampleUser", "192.0.2.1"
        )[0][0]
        cursor.execute.assert_called_once_with(
            "DELETE FROM login_attempts WHERE attempt_key = %s",
            (account_key,),
        )
        connection.commit.assert_called_once_with()
        connection.close.assert_called_once_with()


class CacheRevisionTests(unittest.TestCase):
    def test_current_revision_is_loaded_from_shared_settings(self):
        cursor = Mock()
        cursor.fetchone.return_value = {"value": "42"}
        connection = Mock()
        connection.cursor.return_value = cursor

        with patch(
            "models.cache_revision.Database.get_connection",
            return_value=connection,
        ):
            revision = CacheRevision.current()

        self.assertEqual(revision, 42)
        self.assertIn("solder_settings", cursor.execute.call_args.args[0])
        connection.close.assert_called_once_with()

    def test_bump_commits_the_shared_revision(self):
        cursor = Mock()
        connection = Mock()
        connection.cursor.return_value = cursor

        with patch(
            "models.cache_revision.Database.get_connection",
            return_value=connection,
        ):
            CacheRevision.bump()

        self.assertIn("ON DUPLICATE KEY UPDATE", cursor.execute.call_args.args[0])
        connection.commit.assert_called_once_with()
        connection.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
