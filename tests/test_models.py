import datetime
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.environment import configure_test_environment


configure_test_environment()

from models.build import Build  # noqa: E402
from models.common import common  # noqa: E402
from models.database import Database  # noqa: E402
from models.mod import Mod  # noqa: E402
from models.modpack import Modpack  # noqa: E402
from models.modversion import Modversion  # noqa: E402
from models.passhasher import Passhasher  # noqa: E402
from models.session import Session  # noqa: E402


class PasswordHasherTests(unittest.TestCase):
    def test_password_round_trip(self):
        password = Passhasher.from_password("correct horse", "user@example.test")

        self.assertTrue(password.verify("correct horse"))
        self.assertFalse(password.verify("wrong password"))

    def test_password_hash_is_deterministic_for_the_same_salt(self):
        first = Passhasher.hasher("secret", "first-user")
        second = Passhasher.hasher("secret", "first-user")
        other_user = Passhasher.hasher("secret", "second-user")

        self.assertEqual(first, second)
        self.assertNotEqual(first, other_user)


class ModelSerializationTests(unittest.TestCase):
    def test_mod_serialization_matches_the_api_contract(self):
        mod = Mod(
            1,
            "example",
            "Description",
            "Author",
            "https://example.test",
            None,
            None,
            "Example Mod",
            "BOTH",
            "MOD",
            "internal",
        )

        self.assertEqual(
            mod.to_json(),
            {
                "name": "example",
                "pretty_name": "Example Mod",
                "author": "Author",
                "description": "Description",
                "link": "https://example.test",
                "side": "BOTH",
                "type": "MOD",
            },
        )

    def test_modpack_serialization_adds_optional_and_server_builds(self):
        modpack = Modpack(
            1,
            "Example Pack",
            "example-pack",
            "2",
            "3",
            None,
            None,
            0,
            0,
            0,
            0,
            enable_optionals=1,
            enable_server=1,
        )
        modpack.builds = [SimpleNamespace(version="3")]

        self.assertEqual(
            modpack.to_json()["builds"],
            ["3", "3-optional", "3-server"],
        )

    def test_modversion_serialization_matches_the_api_contract(self):
        version = Modversion(2, 1, "3.0", "1.21.1", "abc123", None, None, 4096)

        self.assertEqual(
            version.to_json(),
            {"mod_id": 1, "version": "3.0", "md5": "abc123", "filesize": 4096},
        )


class ModelBehaviorTests(unittest.TestCase):
    def test_failed_database_probe_is_safe_outside_a_request(self):
        with patch(
            "models.database.connector.connect",
            side_effect=ConnectionError("database unavailable"),
        ):
            self.assertIsNone(Database.get_connection())

    def test_failed_setup_query_is_safe_outside_a_request(self):
        connection = Mock()
        connection.cursor.return_value.execute.side_effect = RuntimeError(
            "query unavailable"
        )

        with (
            patch("models.database.DISABLE_is_setup", False),
            patch("models.database.Database.get_connection", return_value=connection),
        ):
            self.assertEqual(Database.is_setup(), 2)

        connection.close.assert_called_once_with()

    def test_checkbox_update_uses_an_allowlisted_query(self):
        connection = Mock()

        with patch("models.common.Database.get_connection", return_value=connection):
            common.update_checkbox(3, 1, "pinned", "modpacks")

        connection.cursor.return_value.execute.assert_called_once_with(
            "UPDATE modpacks SET pinned = %s WHERE id = %s", (1, 3)
        )
        connection.commit.assert_called_once_with()

    def test_checkbox_update_rejects_an_unknown_database_target(self):
        with (
            patch("models.common.Database.get_connection") as get_connection,
            self.assertRaisesRegex(ValueError, "Unsupported update target"),
        ):
            common.update_checkbox(3, 1, "password", "users")

        get_connection.assert_not_called()

    def test_extract_jar_from_uploaded_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory, "example.zip")
            with zipfile.ZipFile(archive, "w") as zip_file:
                zip_file.writestr("mods/example.jar", b"jar contents")
                zip_file.writestr("config/example.toml", b"config contents")

            jar_name = Mod.extract_jar_from_zip(archive)

            self.assertEqual(jar_name, "example.jar")
            self.assertEqual(Path(directory, jar_name).read_bytes(), b"jar contents")

    def test_empty_build_returns_an_empty_mod_list(self):
        connection = Mock()
        connection.cursor.return_value.fetchall.return_value = []
        build = Build(
            1,
            2,
            "3",
            datetime.datetime.now(),
            datetime.datetime.now(),
            "1.21.1",
            None,
            1,
            0,
            "21",
            4096,
            0,
        )

        with patch("models.build.Database.get_connection", return_value=connection):
            versions = build.get_modversions_api("")

        self.assertEqual(versions, [])

    def test_empty_database_returns_empty_public_modpack_lists(self):
        connection = Mock()
        connection.cursor.return_value.fetchall.return_value = []

        with patch(
            "models.modpack.Database.get_connection", return_value=connection
        ):
            self.assertEqual(Modpack.get_by_cid_api("new-client"), [])
            self.assertEqual(Modpack.get_all_api(), [])

    def test_ipv4_address_is_converted_to_the_stored_integer_format(self):
        self.assertEqual(Session.ip_to_int("127.0.0.1"), 127001)


if __name__ == "__main__":
    unittest.main()
