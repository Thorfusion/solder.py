import datetime
import hashlib
import tempfile
import unittest
import zipfile

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from mysql.connector import IntegrityError, errorcode
from requests import RequestException

from tests.environment import configure_test_environment


configure_test_environment()

from models.build import (  # noqa: E402
    Build,
    InvalidJavaRuntimeError,
    normalize_java_runtime,
)
from models.build_modversion import Build_modversion  # noqa: E402
from models.common import common  # noqa: E402
from models.database import Database  # noqa: E402
from models.dashboard import Dashboard  # noqa: E402
from models.mod import DuplicateModError, Mod, UploadVerificationError  # noqa: E402
from models.mod_dependency import (  # noqa: E402
    CircularDependencyError,
    DuplicateDependencyError,
    ModDependency,
)
from models.modpack import Modpack  # noqa: E402
from models.modversion import (  # noqa: E402
    IncompatibleModVersionError,
    MissingDependencyVersionError,
    Modversion,
)
from models.passhasher import Passhasher  # noqa: E402
from models.session import Session  # noqa: E402
from models.user import User  # noqa: E402


class PasswordHasherTests(unittest.TestCase):
    def test_password_round_trip(self):
        password = Passhasher.from_password("correct horse", "user@example.test")

        self.assertTrue(password.verify("correct horse"))
        self.assertFalse(password.verify("wrong password"))

    def test_argon2_uses_a_fresh_embedded_salt(self):
        first = Passhasher.hasher("secret", "first-user")
        second = Passhasher.hasher("secret", "first-user")

        self.assertTrue(first.startswith("$argon2id$"))
        self.assertTrue(second.startswith("$argon2id$"))
        self.assertNotEqual(first, second)
        self.assertTrue(Passhasher(first, "first-user").verify("secret"))
        self.assertTrue(Passhasher(second, "first-user").verify("secret"))

    def test_legacy_solder_hash_is_upgraded_after_verification(self):
        legacy = Passhasher.legacy_hasher("secret", "legacy-user")

        valid, replacement = Passhasher(
            legacy, "legacy-user"
        ).verify_and_rehash("secret")

        self.assertTrue(valid)
        self.assertTrue(replacement.startswith("$argon2id$"))
        self.assertFalse(
            Passhasher(legacy, "legacy-user").verify("wrong password")
        )

    def test_successful_legacy_login_updates_the_hash_conditionally(self):
        legacy = Passhasher.legacy_hasher("secret", "legacy-user")
        user = User(
            7,
            "legacy-user",
            "legacy@example.test",
            legacy,
            "127.0.0.1",
            "127.0.0.1",
            None,
            None,
            "127.0.0.1",
            1,
            1,
        )
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.rowcount = 1

        with patch.object(Database, "get_connection", return_value=connection):
            self.assertTrue(user.verify_password("secret"))

        query, parameters = cursor.execute.call_args.args
        self.assertIn("WHERE id = %s AND password = %s", query)
        self.assertTrue(parameters[0].startswith("$argon2id$"))
        self.assertEqual(parameters[1:], (7, legacy))
        self.assertTrue(user.password.get_hash().startswith("$argon2id$"))
        connection.commit.assert_called_once_with()

    def test_hash_upgrade_failure_does_not_reject_a_valid_login(self):
        legacy = Passhasher.legacy_hasher("secret", "legacy-user")
        user = User(
            7,
            "legacy-user",
            "legacy@example.test",
            legacy,
            "127.0.0.1",
            "127.0.0.1",
            None,
            None,
            "127.0.0.1",
            1,
            1,
        )

        with (
            patch.object(
                Database,
                "get_connection",
                side_effect=RuntimeError("database unavailable"),
            ),
            patch("models.user.logger.exception") as log_error,
        ):
            self.assertTrue(user.verify_password("secret"))

        self.assertEqual(user.password.get_hash(), legacy)
        log_error.assert_called_once_with(
            "Could not upgrade the stored password hash"
        )


class SessionTests(unittest.TestCase):
    def test_active_session_is_extended_and_resources_are_closed(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        expiry = datetime.datetime(2026, 1, 1)
        cursor.fetchone.return_value = (
            "token",
            "127.0.0.1",
            expiry,
            17,
            1,
        )

        with patch.object(Database, "get_connection", return_value=connection):
            stored = Session.get_and_update_from_token("token")

        self.assertEqual(
            stored,
            Session("token", "127.0.0.1", expiry, 17, True),
        )
        self.assertIn("expiry > NOW()", cursor.execute.call_args_list[0].args[0])
        self.assertIn("users.night_mode", cursor.execute.call_args_list[0].args[0])
        connection.commit.assert_called_once_with()
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_expired_session_is_not_extended(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = None

        with patch.object(Database, "get_connection", return_value=connection):
            self.assertIsNone(Session.get_and_update_from_token("expired"))

        self.assertEqual(cursor.execute.call_count, 1)
        connection.commit.assert_not_called()
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_new_session_replaces_only_the_same_users_session(self):
        connection = Mock()
        cursor = connection.cursor.return_value

        with (
            patch.object(Database, "get_connection", return_value=connection),
            patch("models.session.secrets.token_hex", return_value="new-token"),
        ):
            token = Session.new_session("10.0.0.1", 17)

        self.assertEqual(token, "new-token")
        cursor.execute.assert_any_call(
            "DELETE FROM sessions WHERE user_id = %s", (17,)
        )
        self.assertNotIn("DELETE FROM sessions WHERE ip", str(cursor.mock_calls))

    def test_user_night_mode_is_persisted_with_audit_metadata(self):
        connection = Mock()
        cursor = connection.cursor.return_value

        with patch.object(Database, "get_connection", return_value=connection):
            User.set_night_mode(9, True, "10.0.0.1", 4)

        query, parameters = cursor.execute.call_args.args
        self.assertIn("SET night_mode = %s", query)
        self.assertEqual(parameters[0:4:2], (1, 4))
        self.assertEqual(parameters[-1], 9)
        connection.commit.assert_called_once_with()
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()


class ModelSerializationTests(unittest.TestCase):
    def test_build_cleanup_removes_optional_loader_and_membership_rows(self):
        cursor = Mock()

        with (
            patch(
                "models.advanced_optional.AdvancedOptional.delete_build"
            ) as optionals,
            patch(
                "models.technic_solderpy_loader."
                "TechnicSolderPyLoader.delete_build"
            ) as loader,
        ):
            Build.delete_related_rows(cursor, 12)

        optionals.assert_called_once_with(cursor, 12)
        loader.assert_called_once_with(cursor, 12)
        cursor.execute.assert_called_once_with(
            "DELETE FROM build_modversion WHERE build_id = %s", (12,)
        )

    def test_modpack_cleanup_removes_all_pack_mappings(self):
        cursor = Mock()

        Modpack.delete_related_rows(cursor, 3)

        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertTrue(
            any("DELETE modpack_publication_runs" in sql for sql in statements)
        )
        self.assertTrue(any("client_modpack" in sql for sql in statements))
        self.assertTrue(any("user_modpack" in sql for sql in statements))
        self.assertTrue(
            any("UPDATE user_permissions" in sql for sql in statements)
        )

    def test_linking_an_integration_only_updates_the_existing_mod_row(self):
        connection = MagicMock()
        cursor = MagicMock()
        cursor.rowcount = 1
        connection.cursor.return_value = cursor
        linked = SimpleNamespace(id=9)

        with (
            patch.object(Database, "get_connection", return_value=connection),
            patch.object(Mod, "get_by_id", return_value=linked) as get_by_id,
        ):
            result = Mod.link_integration(9, "modrinth", "PROJECT")

        query, parameters = cursor.execute.call_args.args
        self.assertIn("UPDATE mods", query)
        self.assertIn("integration_provider IS NULL", query)
        self.assertNotIn("modversions", query)
        self.assertEqual(parameters[0:2], ("MODRINTH", "PROJECT"))
        self.assertEqual(parameters[3], 9)
        connection.commit.assert_called_once_with()
        get_by_id.assert_called_once_with(9)
        self.assertIs(result, linked)

    def test_mojang_java_runtime_components_are_strictly_validated(self):
        self.assertEqual(
            normalize_java_runtime(" java-runtime-delta "),
            "java-runtime-delta",
        )
        self.assertIsNone(normalize_java_runtime(""))
        with self.assertRaises(InvalidJavaRuntimeError):
            normalize_java_runtime("1.8.0_401")

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
            "MODRINTH",
            "AABBCCDD",
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
                "modtype": "MOD",
            },
        )
        self.assertEqual(mod.notes, "internal")
        self.assertNotIn("notes", mod.to_json())
        self.assertNotIn("integration_provider", mod.to_json())
        self.assertNotIn("integration_project_id", mod.to_json())

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
        self.assertEqual(
            modpack.to_json()["capabilities"],
            {
                "advanced_optionals": False,
                "bootstrap_manifest": True,
                "optional": True,
                "server": True,
            },
        )

    def test_modversion_serialization_matches_the_api_contract(self):
        version = Modversion(
            2,
            1,
            "3.0",
            "1.21.1",
            "abc123",
            None,
            None,
            4096,
            integration_version_id="VERSION",
        )

        self.assertEqual(
            version.to_json(),
            {"mod_id": 1, "version": "3.0", "md5": "abc123", "filesize": 4096},
        )
        self.assertNotIn("integration_version_id", version.to_json())


class ModelBehaviorTests(unittest.TestCase):
    def test_runtime_schema_migrates_export_override_before_seeding(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = None

        with (
            patch("models.database.DISABLE_is_setup", False),
            patch.object(Database, "get_connection", return_value=connection),
            patch.object(Database, "normalize_legacy_timestamps"),
            patch.object(Database, "migrate_legacy_mod_notes"),
            patch.object(Database, "migrate_jar_hash_mod_types"),
        ):
            self.assertTrue(Database.ensure_runtime_schema())

        statements = [call.args[0] for call in cursor.execute.call_args_list]
        migration = next(
            index
            for index, statement in enumerate(statements)
            if statement.startswith(
                "ALTER TABLE platform_export_overrides ADD COLUMN override_solder_only"
            )
        )
        seed = statements.index(Database.PLATFORM_EXPORT_OVERRIDES_DEFAULT_SQL)
        self.assertLess(migration, seed)
        self.assertTrue(
            any(
                statement.startswith(
                    "ALTER TABLE users ADD COLUMN night_mode"
                )
                for statement in statements
            )
        )
        connection.commit.assert_called_once_with()

    def test_technic_delivery_mode_is_in_current_schema_and_upgrade(self):
        self.assertIn(
            "delivery_mode VARCHAR(16) NOT NULL DEFAULT 'LOADER'",
            Database.TECHNIC_SOLDERPY_LOADER_TABLE_SQL,
        )
        migration = Database.TECHNIC_SOLDERPY_LOADER_COLUMN_MIGRATIONS[0]
        self.assertEqual(
            migration[:2],
            ("technic_solderpy_loader_builds", "delivery_mode"),
        )

    def test_dashboard_checks_updates_only_on_each_modpacks_newest_build(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchone.side_effect = [
            {"solder_full": 1, "mods_manage": 1, "modpacks_manage": 1},
            {"modpacks": 0, "builds": 0, "unpublished_builds": 0},
            None,
            {"item_count": 0, "target_id": None},
            {"item_count": 0, "target_id": None},
            {"item_count": 0, "target_id": None},
            {"mods": 0, "modversions": 0},
            {"item_count": 0},
            {"item_count": 0},
        ]
        cursor.fetchall.side_effect = [[], [], [], []]

        with patch(
            "models.dashboard.Database.get_connection", return_value=connection
        ):
            Dashboard.load(4)

        update_query_calls = [
            call
            for call in cursor.execute.call_args_list
            if "SELECT MAX(latest_build.id)" in call.args[0]
        ]
        self.assertEqual(len(update_query_calls), 1)
        update_query, parameters = update_query_calls[0].args
        self.assertIn(
            "latest_build.modpack_id = builds.modpack_id", update_query
        )
        self.assertEqual(parameters, (1, 4))

    def test_dependency_read_api_exposes_public_mod_metadata(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchall.return_value = [
            {
                "dependency_mod_id": 2,
                "name": "library",
                "pretty_name": "Library",
                "side": "BOTH",
                "modtype": "MOD",
            }
        ]

        with patch(
            "models.mod_dependency.Database.get_connection",
            return_value=connection,
        ):
            dependencies = ModDependency.get_by_mod_api(1)

        self.assertEqual(
            dependencies,
            [
                {
                    "id": 2,
                    "name": "library",
                    "pretty_name": "Library",
                    "side": "BOTH",
                    "modtype": "MOD",
                }
            ],
        )
        self.assertEqual(cursor.execute.call_args.args[1], (1,))
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_build_dependency_read_api_groups_dependencies_by_mod(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchall.return_value = [
            {
                "mod_id": 1,
                "dependency_mod_id": 2,
                "name": "library",
                "pretty_name": "Library",
                "side": "SERVER",
                "modtype": "CONFIG",
            }
        ]

        with patch(
            "models.mod_dependency.Database.get_connection",
            return_value=connection,
        ):
            dependencies = ModDependency.get_for_build_api(9)

        self.assertEqual(dependencies[1][0]["id"], 2)
        self.assertEqual(dependencies[1][0]["side"], "SERVER")
        self.assertEqual(dependencies[1][0]["modtype"], "CONFIG")
        self.assertEqual(cursor.execute.call_args.args[1], (9,))
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_dependency_management_lists_configured_and_available_mods(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchall.side_effect = [
            [
                {
                    "id": 9,
                    "dependency_mod_id": 2,
                    "name": "library",
                    "pretty_name": "Library",
                }
            ],
            [
                {
                    "id": 3,
                    "name": "other",
                    "pretty_name": "Other",
                    "integration_provider": "MODRINTH",
                }
            ],
        ]

        with patch(
            "models.mod_dependency.Database.get_connection",
            return_value=connection,
        ):
            dependencies, available = ModDependency.get_management_data(1)

        self.assertEqual(dependencies[0]["dependency_mod_id"], 2)
        self.assertEqual(available[0]["id"], 3)
        self.assertEqual(available[0]["integration_provider"], "MODRINTH")
        self.assertEqual(cursor.execute.call_count, 2)
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_dependency_cycle_is_rejected(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchall.side_effect = [
            [{"id": 1}, {"id": 2}],
            [{"mod_id": 2, "dependency_mod_id": 1}],
        ]

        with (
            patch(
                "models.mod_dependency.Database.get_connection",
                return_value=connection,
            ),
            self.assertRaises(CircularDependencyError),
        ):
            ModDependency.add(1, 2)

        connection.commit.assert_not_called()
        connection.rollback.assert_called_once_with()
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_required_dependencies_are_added_recursively_without_replacing_existing(self):
        cursor = Mock()
        cursor.fetchall.side_effect = [
            [
                {
                    "mod_id": 1,
                    "dependency_mod_id": 2,
                    "dependency_name": "Library A",
                },
                {
                    "mod_id": 2,
                    "dependency_mod_id": 3,
                    "dependency_name": "Library B",
                },
            ],
            [{"mod_id": 1}, {"mod_id": 3}],
        ]
        cursor.fetchone.return_value = {"id": 202}

        added = Modversion._add_required_dependencies(
            cursor, 10, "1.21.1", 1, "FABRIC"
        )

        self.assertEqual(added, ["Library A"])
        version_queries = [
            call
            for call in cursor.execute.call_args_list
            if "FROM modversions" in call.args[0]
            and "mcversion" in call.args[0]
        ]
        self.assertEqual(len(version_queries), 1)
        self.assertEqual(
            version_queries[0].args[1],
            (
                2,
                "1.21.1",
                "1.21.1",
                "1.21.1",
                "FABRIC",
                "FABRIC",
                "1.21.1",
                "1.21.1",
                "1.21.1",
                "FABRIC",
            ),
        )
        cursor.execute.assert_any_call(
            """INSERT INTO build_modversion
                          (modversion_id, build_id, optional)
                   VALUES (%s, %s, 0)""",
            (202, 10),
        )

    def test_missing_required_dependency_version_blocks_the_addition(self):
        cursor = Mock()
        cursor.fetchall.side_effect = [
            [
                {
                    "mod_id": 1,
                    "dependency_mod_id": 2,
                    "dependency_name": "Missing Library",
                }
            ],
            [{"mod_id": 1}],
        ]
        cursor.fetchone.return_value = None

        with self.assertRaisesRegex(
            MissingDependencyVersionError,
            "Missing Library has no version compatible with Minecraft 1.21.1",
        ):
            Modversion._add_required_dependencies(cursor, 10, "1.21.1", 1)

    def test_missing_dependency_rolls_back_the_parent_build_change(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchone.side_effect = [
            {"mod_id": 1, "minecraft": "1.21.1"},
            None,
            None,
        ]
        cursor.fetchall.side_effect = [
            [
                {
                    "mod_id": 1,
                    "dependency_mod_id": 2,
                    "dependency_name": "Missing Library",
                }
            ],
            [{"mod_id": 1}],
        ]

        with (
            patch(
                "models.modversion.Database.get_connection",
                return_value=connection,
            ),
            self.assertRaises(MissingDependencyVersionError),
        ):
            Modversion.add_modversion_to_selected_build(101, 1, 10, "0", "0")

        connection.commit.assert_not_called()
        connection.rollback.assert_called_once_with()
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_new_mod_uses_the_insert_id_and_closes_the_connection(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.lastrowid = 42

        with patch("models.mod.Database.get_connection", return_value=connection):
            mod = Mod.new(
                "new-mod",
                "Description",
                "Author",
                "https://example.test/mod",
                "New Mod",
                "BOTH",
                "MOD",
                "Note",
            )

        self.assertEqual(mod.id, 42)
        self.assertEqual(mod.notes, "Note")
        self.assertIn("notes", cursor.execute.call_args.args[0])
        self.assertEqual(cursor.execute.call_count, 1)
        connection.commit.assert_called_once_with()
        connection.rollback.assert_not_called()
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_dependency_ensure_reuses_existing_relationship(self):
        with patch.object(
            ModDependency,
            "add",
            side_effect=DuplicateDependencyError(
                "That required dependency is already configured."
            ),
        ):
            self.assertFalse(ModDependency.ensure(1, 2))

    def test_adding_launcher_version_syncs_build_modloader_metadata(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchone.side_effect = [
            {
                "mod_id": 1,
                "version": "1.7.10-10.13.4.1614",
                "mcversion": "1.7.10",
                "modloader": "FORGE",
                "modtype": "LAUNCHER",
                "minecraft": "1.7.10",
                "build_modloader": None,
            },
            None,
        ]

        with (
            patch(
                "models.modversion.Database.get_connection",
                return_value=connection,
            ),
            patch.object(
                Modversion, "_add_required_dependencies", return_value=[]
            ) as add_dependencies,
        ):
            Modversion.add_modversion_to_selected_build(
                101, 1, 10, "0", "0"
            )

        cursor.execute.assert_any_call(
            """UPDATE builds
                   SET forge = %s, modloader = %s
                   WHERE id = %s""",
            ("1.7.10-10.13.4.1614", "FORGE", 10),
        )
        add_dependencies.assert_called_once_with(
            cursor, 10, "1.7.10", 1, "FORGE"
        )
        connection.commit.assert_called_once_with()

    def test_legacy_launcher_without_loader_keeps_build_loader(self):
        cursor = Mock()

        synchronized = Modversion.sync_launcher_build_metadata(
            cursor,
            10,
            "LAUNCHER",
            "1.7.10-10.13.4.1614",
        )

        self.assertTrue(synchronized)
        cursor.execute.assert_called_once_with(
            """UPDATE builds
                   SET forge = %s
                   WHERE id = %s""",
            ("1.7.10-10.13.4.1614", 10),
        )

    def test_changing_launcher_version_syncs_build_modloader_version(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = {
            "mod_id": 1,
            "version": "1.7.10-10.13.4.1614",
            "mcversion": "1.7.10",
            "modloader": "FORGE",
            "modtype": "LAUNCHER",
            "current_mod_id": 1,
            "minecraft": "1.7.10",
            "build_modloader": "FORGE",
        }

        with patch(
            "models.modversion.Database.get_connection",
            return_value=connection,
        ):
            Modversion.update_modversion_in_build(100, 101, 10)

        cursor.execute.assert_any_call(
            """UPDATE builds
                   SET forge = %s, modloader = %s
                   WHERE id = %s""",
            ("1.7.10-10.13.4.1614", "FORGE", 10),
        )
        connection.commit.assert_called_once_with()

    def test_legacy_solderpy_mod_note_is_moved_to_technic_notes(self):
        cursor = Mock()
        cursor.fetchone.return_value = (1,)

        Database.migrate_legacy_mod_notes(cursor)

        self.assertEqual(cursor.execute.call_count, 3)
        self.assertIn("COLUMN_NAME = %s", cursor.execute.call_args_list[0].args[0])
        self.assertEqual(
            cursor.execute.call_args_list[0].args[1],
            ("solder_test", "mods", "note"),
        )
        self.assertIn("SET notes = note", cursor.execute.call_args_list[1].args[0])
        self.assertEqual(
            cursor.execute.call_args_list[2].args[0],
            "ALTER TABLE mods DROP COLUMN note",
        )

    def test_jar_hash_migration_promotes_parent_mod_type(self):
        cursor = Mock()

        Database.migrate_jar_hash_mod_types(cursor)

        query = cursor.execute.call_args.args[0]
        self.assertIn("INNER JOIN modversions", query)
        self.assertIn("SET mods.modtype = 'MOD'", query)
        self.assertIn("CHAR_LENGTH(TRIM(modversions.jarmd5)) = 32", query)
        self.assertIn("NOT REGEXP '[^0-9A-Fa-f]'", query)
        self.assertIn("NOT IN ('MOD', 'BOOTSTRAP', 'LAUNCHER')", query)

    def test_jar_filesize_column_is_in_the_current_database_migration(self):
        migration = next(
            item
            for item in Database.JAR_COLUMN_MIGRATIONS
            if item[1] == "jarfilesize"
        )

        self.assertEqual(migration[0], "modversions")
        self.assertIn("BIGINT UNSIGNED", migration[2])
        self.assertIn("AFTER jarmd5", migration[2])

    def test_jar_override_uses_a_sparse_table_not_modversions(self):
        self.assertNotIn(
            "jar_url_override",
            "\n".join(query for _table, _column, query in Database.JAR_COLUMN_MIGRATIONS),
        )
        self.assertIn(
            "PRIMARY KEY",
            Database.MODVERSION_DOWNLOAD_OVERRIDES_TABLE_SQL,
        )

    def test_integration_download_metadata_uses_a_sparse_table(self):
        schema = Database.MODVERSION_DOWNLOAD_SOURCES_TABLE_SQL

        self.assertIn("PRIMARY KEY (modversion_id, provider)", schema)
        self.assertIn("url VARCHAR(2048) NOT NULL", schema)
        self.assertIn("filename VARCHAR(255) NOT NULL", schema)
        self.assertIn("sha512 CHAR(128)", schema)
        self.assertNotIn("download_source", Database.JAR_COLUMN_MIGRATIONS[0][2])

    def test_multiple_minecraft_versions_use_an_indexed_relation(self):
        schema = Database.MODVERSION_MINECRAFT_VERSIONS_TABLE_SQL

        self.assertIn(
            "PRIMARY KEY (modversion_id, minecraft_version)", schema
        )
        self.assertIn(
            "(minecraft_version, modversion_id)", schema
        )

    def test_extension_tables_use_technic_compatible_collation(self):
        schemas = (
            Database.MODVERSION_DOWNLOAD_SOURCES_TABLE_SQL,
            Database.MODVERSION_MINECRAFT_VERSIONS_TABLE_SQL,
            *Database.PUBLISHING_TABLES_SQL,
            *Database.MAVEN_TABLES_SQL,
        )
        for schema in schemas:
            with self.subTest(schema=schema.split("(", 1)[0]):
                self.assertIn("COLLATE=utf8mb4_unicode_ci", schema)

    def test_platform_publishing_uses_generic_sparse_tables(self):
        schema = "\n".join(Database.PUBLISHING_TABLES_SQL)

        self.assertIn("publishing_provider_accounts", schema)
        self.assertIn("token TEXT NOT NULL", schema)
        self.assertIn("user_id INT NOT NULL", schema)
        self.assertIn("modpack_publication_targets", schema)
        self.assertIn("provider_account_id INT NOT NULL", schema)
        self.assertIn("modpack_publication_runs", schema)
        self.assertIn("remote_file_id VARCHAR(191)", schema)
        self.assertIn(
            "UNIQUE KEY uq_modpack_publication_run_deduplication", schema
        )
        self.assertIn(
            "jar_url VARCHAR(2048) NOT NULL",
            Database.MODVERSION_DOWNLOAD_OVERRIDES_TABLE_SQL,
        )

    @patch("models.database.db_name", "solder_test")
    def test_mcil_package_type_is_migrated_to_bootstrap(self):
        cursor = Mock()
        cursor.fetchone.side_effect = [
            (1,),
            ("enum('MOD','LAUNCHER','RES','CONFIG','MCIL','NONE')",),
        ]

        Database.migrate_bootstrap_modtype(cursor)

        queries = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertTrue(any("'MCIL','BOOTSTRAP'" in query for query in queries))
        self.assertTrue(
            any("SET modtype = 'BOOTSTRAP'" in query for query in queries)
        )
        self.assertIn(
            "ENUM('MOD','LAUNCHER','RES','CONFIG','BOOTSTRAP','NONE')",
            queries[-1],
        )

    @patch("models.database.db_name", "solder_test")
    def test_modversion_modloader_column_expands_for_compatibility_sets(self):
        cursor = Mock()
        cursor.fetchone.return_value = (32,)

        Database.expand_modversion_compatibility(cursor)

        self.assertIn(
            "MODIFY modloader VARCHAR(255)",
            cursor.execute.call_args_list[-1].args[0],
        )

    def test_technic_modpack_permissions_are_migrated_without_duplicates(self):
        cursor = Mock()

        Database.migrate_technic_modpack_permissions(cursor)

        queries = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertEqual(len(queries), 3)
        self.assertIn("INSERT INTO user_modpack", queries[0])
        self.assertIn("FIND_IN_SET", queries[0])
        self.assertIn("WHERE user_modpack.id IS NULL", queries[0])
        self.assertIn("MIN(user_id)", queries[1])
        self.assertIn("SELECT MIN(id) FROM users", queries[2])
        self.assertNotIn("user_id = 1", "\n".join(queries))

    def test_duplicate_mod_rolls_back_and_raises_a_specific_error(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.execute.side_effect = IntegrityError(
            msg="Duplicate entry", errno=errorcode.ER_DUP_ENTRY
        )

        with (
            patch("models.mod.Database.get_connection", return_value=connection),
            self.assertRaises(DuplicateModError),
        ):
            Mod.new(
                "existing-mod",
                "Description",
                "Author",
                "https://example.test/mod",
                "Existing Mod",
                "BOTH",
                "MOD",
                "Note",
            )

        connection.commit.assert_not_called()
        connection.rollback.assert_called_once_with()
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()

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

    def test_extract_launcher_jar_from_bin_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory, "launcher.zip")
            with zipfile.ZipFile(archive, "w") as zip_file:
                zip_file.writestr("bin/modpack.jar", b"server launcher")

            jar_name = Mod.extract_jar_from_zip(
                archive,
                output_name="crucible-1.7.10-5.4.jar",
                allow_any_jar=True,
            )

            self.assertEqual(jar_name, "crucible-1.7.10-5.4.jar")
            self.assertEqual(
                Path(directory, jar_name).read_bytes(), b"server launcher"
            )

    def test_new_modversion_stores_verified_jar_hash_in_the_insert(self):
        connection = Mock()
        connection.cursor.return_value.lastrowid = 42
        jar_md5 = "d41d8cd98f00b204e9800998ecf8427e"

        with patch(
            "models.modversion.Database.get_connection", return_value=connection
        ):
            version = Modversion.new(
                3,
                "1.20.1-1.0",
                "1.20.1",
                "a" * 32,
                123,
                "0",
                jarmd5=jar_md5,
                jarfilesize=99,
                modloader="fabric",
            )

        calls = connection.cursor.return_value.execute.call_args_list
        query, parameters = calls[0].args
        self.assertIn("jarmd5", query)
        self.assertEqual(parameters[3], "FABRIC")
        self.assertEqual(parameters[6], jar_md5)
        self.assertEqual(parameters[7], 99)
        self.assertEqual(
            calls[1].args[1],
            (3,),
        )
        self.assertIn("SET modtype = 'MOD'", calls[1].args[0])
        self.assertIn("NOT IN ('MOD', 'BOOTSTRAP', 'LAUNCHER')", calls[1].args[0])
        self.assertEqual(version.id, 42)
        self.assertEqual(version.modloader, "FABRIC")
        self.assertEqual(version.jarfilesize, 99)
        connection.cursor.return_value.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_new_modversion_without_jar_hash_keeps_parent_mod_type(self):
        connection = Mock()
        connection.cursor.return_value.lastrowid = 42

        with patch(
            "models.modversion.Database.get_connection", return_value=connection
        ):
            Modversion.new(
                3,
                "1.20.1-1.0",
                "1.20.1",
                "a" * 32,
                123,
                "0",
                jarmd5="0",
                modloader="fabric",
            )

        self.assertEqual(connection.cursor.return_value.execute.call_count, 1)

    def test_new_modversion_stores_multiple_minecraft_versions_separately(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.lastrowid = 42

        with patch(
            "models.modversion.Database.get_connection",
            return_value=connection,
        ):
            version = Modversion.new(
                3,
                "MULTI-example-1.0",
                ["1.20.1", "1.20.2"],
                "a" * 32,
                123,
                "0",
            )

        self.assertEqual(cursor.execute.call_args_list[0].args[1][2], "MULTI")
        cursor.executemany.assert_called_once_with(
            """INSERT INTO modversion_minecraft_versions
                          (modversion_id, minecraft_version)
                   VALUES (%s, %s)""",
            [(42, "1.20.1"), (42, "1.20.2")],
        )
        self.assertEqual(version.mcversion, "MULTI")
        self.assertEqual(version.minecraft_versions, ("1.20.1", "1.20.2"))

    def test_new_modversion_stores_url_override_in_sparse_table(self):
        connection = Mock()
        connection.cursor.return_value.lastrowid = 42

        with patch(
            "models.modversion.Database.get_connection",
            return_value=connection,
        ):
            version = Modversion.new(
                3,
                "1.20.1-1.0",
                "1.20.1",
                "a" * 32,
                123,
                "0",
                jarmd5="b" * 32,
                jarfilesize=99,
                jar_url_override="https://downloads.example/mod.jar",
            )

        calls = connection.cursor.return_value.execute.call_args_list
        self.assertNotIn("jar_url_override", calls[0].args[0])
        self.assertIn(
            "INSERT INTO modversion_download_overrides", calls[1].args[0]
        )
        self.assertEqual(
            calls[1].args[1],
            (42, "https://downloads.example/mod.jar"),
        )
        self.assertEqual(
            version.jar_url_override, "https://downloads.example/mod.jar"
        )

    def test_new_modversion_stores_integration_download_source(self):
        connection = Mock()
        connection.cursor.return_value.lastrowid = 42
        source = {
            "provider": "modrinth",
            "url": "https://cdn.modrinth.com/data/project/version/mod.jar",
            "filename": "mod.jar",
            "md5": "a" * 32,
            "sha1": "b" * 40,
            "sha512": "c" * 128,
            "filesize": 99,
        }

        with patch(
            "models.modversion.Database.get_connection",
            return_value=connection,
        ):
            Modversion.new(
                3,
                "1.20.1-1.0",
                "1.20.1",
                "d" * 32,
                123,
                "0",
                jarmd5="a" * 32,
                jarfilesize=99,
                download_source=source,
            )

        calls = connection.cursor.return_value.execute.call_args_list
        source_call = next(
            call for call in calls
            if "INSERT INTO modversion_download_sources" in call.args[0]
        )
        self.assertEqual(source_call.args[1][0:4], (
            42,
            "MODRINTH",
            source["url"],
            "mod.jar",
        ))
        self.assertEqual(source_call.args[1][4:], (
            "a" * 32,
            "b" * 40,
            "c" * 128,
            99,
        ))

    def test_generated_jar_stores_hash_and_filesize_together(self):
        connection = Mock()
        cursor = connection.cursor.return_value

        with patch(
            "models.modversion.Database.get_connection",
            return_value=connection,
        ):
            Modversion.update_modversion_jarmd5(9, "a" * 32, 456)

        query, parameters = cursor.execute.call_args_list[0].args
        self.assertIn("jarfilesize = %s", query)
        self.assertEqual(parameters, ("a" * 32, 456, 9))
        promotion_query = cursor.execute.call_args_list[1].args[0]
        self.assertIn("NOT IN", promotion_query)
        self.assertIn("('MOD', 'BOOTSTRAP', 'LAUNCHER')", promotion_query)
        connection.commit.assert_called_once_with()

    def test_repository_file_source_keeps_the_configured_origin(self):
        url = Modversion.repository_file_source(
            "https://repo.example.test/mods/",
            "@127.0.0.1",
            "1.0+build #1",
        )

        self.assertEqual(
            url,
            "https://repo.example.test/mods/%40127.0.0.1/"
            "%40127.0.0.1-1.0%2Bbuild%20%231.zip",
        )

    def test_repository_file_source_rejects_path_separators(self):
        invalid_values = ("../admin", "..", "nested/path", "nested\\path")

        for invalid in invalid_values:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                Modversion.repository_file_source(
                    "https://repo.example.test/mods/", invalid, "1.0"
                )

    def test_get_file_size_reads_a_confined_local_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory, "example", "example-1.0.zip")
            package.parent.mkdir()
            package.write_bytes(b"local package")

            with patch("models.modversion.requests.head") as head:
                file_size = Modversion.get_file_size(
                    directory, "example", "1.0"
                )

        self.assertEqual(file_size, 13)
        head.assert_not_called()

    def test_get_file_size_uses_safe_url_without_redirects(self):
        response = Mock(
            status_code=200,
            headers={"content-length": "123"},
        )

        with patch(
            "models.modversion.requests.head", return_value=response
        ) as head:
            file_size = Modversion.get_file_size(
                "https://repo.example.test/mods/", "example", "1.0"
            )

        self.assertEqual(file_size, 123)
        head.assert_called_once_with(
            "https://repo.example.test/mods/example/example-1.0.zip",
            allow_redirects=False,
            timeout=(5, 30),
        )
        response.raise_for_status.assert_called_once_with()
        response.close.assert_called_once_with()

    def test_get_file_size_rejects_repository_redirects(self):
        response = Mock(
            status_code=302,
            headers={"location": "http://127.0.0.1/"},
        )

        with (
            patch("models.modversion.requests.head", return_value=response),
            self.assertRaisesRegex(RequestException, "redirects are not allowed"),
        ):
            Modversion.get_file_size(
                "https://repo.example.test/mods/", "example", "1.0"
            )

        response.close.assert_called_once_with()

    def test_rehash_uses_safe_url_and_does_not_follow_redirects(self):
        response = MagicMock(status_code=200)
        response.__enter__.return_value = response
        response.iter_content.return_value = [b"file", b" contents"]
        session = MagicMock()
        session.__enter__.return_value = session
        session.get.return_value = response
        version = Modversion(
            1,
            2,
            "1.0",
            "1.20.1",
            "0",
            datetime.datetime.now(),
            datetime.datetime.now(),
            -1,
        )

        with (
            patch("models.modversion.requests.Session", return_value=session),
            patch.object(version, "update_hash") as update_hash,
        ):
            version.rehash(
                "https://repo.example.test/mods/", "example"
            )

        session.get.assert_called_once_with(
            "https://repo.example.test/mods/example/example-1.0.zip",
            stream=True,
            allow_redirects=False,
            timeout=(5, 60),
        )
        response.raise_for_status.assert_called_once_with()
        update_hash.assert_called_once_with(
            hashlib.md5(b"file contents", usedforsecurity=False).hexdigest(),
            "https://repo.example.test/mods/",
            "example",
            file_size=13,
        )

    def test_rehash_reads_a_local_repository_without_http(self):
        version = Modversion(
            1,
            2,
            "1.0",
            "1.20.1",
            "0",
            datetime.datetime.now(),
            datetime.datetime.now(),
            -1,
        )
        package_data = b"local package"

        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory, "example", "example-1.0.zip")
            package.parent.mkdir()
            package.write_bytes(package_data)
            with (
                patch("models.modversion.requests.Session") as session,
                patch.object(version, "update_hash") as update_hash,
            ):
                version.rehash(directory, "example")

        session.assert_not_called()
        update_hash.assert_called_once_with(
            hashlib.md5(package_data, usedforsecurity=False).hexdigest(),
            directory,
            "example",
            file_size=len(package_data),
        )

    def test_verified_zip_hash_and_size_are_saved_together(self):
        connection = Mock()
        version = Modversion(
            1,
            2,
            "1.0",
            "1.20.1",
            "0",
            datetime.datetime.now(),
            datetime.datetime.now(),
            -1,
        )

        with patch(
            "models.modversion.Database.get_connection",
            return_value=connection,
        ):
            result = version.update_hash(
                "a" * 32,
                "https://repo.example.test/mods/",
                "example",
                file_size=456,
            )

        query, parameters = connection.cursor.return_value.execute.call_args.args
        self.assertIn("SET md5 = %s, filesize = %s", query)
        self.assertEqual(parameters, ("a" * 32, 456, 1))
        self.assertIs(result, version)
        self.assertEqual(version.md5, "a" * 32)
        self.assertEqual(version.filesize, 456)
        connection.commit.assert_called_once_with()

    def test_incompatible_modloader_is_rejected_before_adding_version(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = {
            "mod_id": 1,
            "mcversion": "1.21.1",
            "modloader": "FORGE",
            "minecraft": "1.21.1",
            "build_modloader": "FABRIC",
        }

        with (
            patch(
                "models.modversion.Database.get_connection",
                return_value=connection,
            ),
            self.assertRaises(IncompatibleModVersionError),
        ):
            Modversion.add_modversion_to_selected_build(101, 1, 10, "0", "0")

        connection.commit.assert_not_called()
        connection.rollback.assert_called_once_with()
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_uploaded_jar_is_renamed_and_verified_server_side(self):
        jar_contents = b"verified jar contents"
        expected_md5 = hashlib.md5(
            jar_contents, usedforsecurity=False
        ).hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory, "example.zip")
            with zipfile.ZipFile(archive, "w") as zip_file:
                zip_file.writestr("mods/client-name.jar", jar_contents)

            jar_name = Mod.extract_jar_from_zip(
                archive,
                output_name="canonical-name.jar",
                expected_md5=expected_md5,
            )

            self.assertEqual(jar_name, "canonical-name.jar")
            self.assertEqual(Path(directory, jar_name).read_bytes(), jar_contents)

    def test_uploaded_jar_hash_mismatch_is_rejected_and_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory, "example.zip")
            with zipfile.ZipFile(archive, "w") as zip_file:
                zip_file.writestr("mods/example.jar", b"different contents")

            with self.assertRaisesRegex(UploadVerificationError, "MD5 verification"):
                Mod.extract_jar_from_zip(
                    archive,
                    output_name="example.jar",
                    expected_md5="d41d8cd98f00b204e9800998ecf8427e",
                )

            self.assertFalse(Path(directory, "example.jar").exists())

    def test_uploaded_jar_rejects_an_unsafe_output_name(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory, "example.zip")
            with zipfile.ZipFile(archive, "w") as zip_file:
                zip_file.writestr("mods/example.jar", b"jar contents")

            with self.assertRaisesRegex(UploadVerificationError, "filename"):
                Mod.extract_jar_from_zip(
                    archive,
                    output_name="../outside.jar",
                )

            self.assertFalse((Path(directory).parent / "outside.jar").exists())

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

    def test_hidden_pack_direct_lookup_only_enforces_privacy(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = None

        with patch("models.modpack.Database.get_connection", return_value=connection):
            result = Modpack.get_by_cid_slug_api(None, "hidden-pack")

        self.assertIsNone(result)
        query, parameters = cursor.execute.call_args.args
        self.assertIn("private = 0", query)
        self.assertNotIn("hidden = 0", query)
        self.assertEqual(parameters, ("hidden-pack", None))
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_modpack_listing_is_deterministic_and_client_scoped(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchall.return_value = []

        with patch("models.modpack.Database.get_connection", return_value=connection):
            result = Modpack.get_by_cid_api("client-id")

        self.assertEqual(result, [])
        query, parameters = cursor.execute.call_args.args
        self.assertIn("hidden = 0 AND private = 0", query)
        self.assertIn("ORDER BY id ASC", query)
        self.assertEqual(parameters, ("client-id",))

    def test_build_api_enforces_publish_and_private_access(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = None

        with patch("models.build.Database.get_connection", return_value=connection):
            result = Build.get_by_modpack_version_api(
                SimpleNamespace(id=3),
                "1.0",
                cid="client-id",
                api_key=False,
            )

        self.assertIsNone(result)
        query, parameters = cursor.execute.call_args.args
        self.assertIn("builds.is_published = 1", query)
        self.assertIn("builds.private = 0", query)
        self.assertIn("c.uuid = %s", query)
        self.assertEqual(parameters, (3, "1.0", "client-id"))

    def test_api_key_still_cannot_read_unpublished_builds(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = None

        with patch("models.build.Database.get_connection", return_value=connection):
            result = Build.get_by_modpack_version_api(
                SimpleNamespace(id=3), "1.0", api_key=True
            )

        self.assertIsNone(result)
        query, parameters = cursor.execute.call_args.args
        self.assertIn("builds.is_published = 1", query)
        self.assertNotIn("builds.private = 0", query)
        self.assertEqual(parameters, (3, "1.0"))

    def test_build_manifest_mods_use_natural_name_order(self):
        connection = Mock()
        cursor = connection.cursor.return_value

        def modversion_row(identifier, name):
            return {
                "id": identifier,
                "mod_id": identifier,
                "version": "1.0",
                "mcversion": "1.21.1",
                "md5": str(identifier) * 32,
                "created_at": None,
                "updated_at": None,
                "filesize": 1024,
                "modname": name,
                "pretty_name": name,
                "author": "CI",
                "link": None,
                "description": None,
                "optional": 0,
            }

        cursor.fetchall.return_value = [
            modversion_row(10, "example10"),
            modversion_row(2, "Example2"),
            modversion_row(1, "alpha"),
        ]
        build = Build(
            1, 2, "3", None, None, "1.21.1", None, 1, 0, "21", 4096, 0
        )

        with patch("models.build.Database.get_connection", return_value=connection):
            versions = build.get_modversions_api("")

        self.assertEqual(
            [version.modname for version in versions],
            ["alpha", "Example2", "example10"],
        )
        query, parameters = cursor.execute.call_args.args
        self.assertIn("mods.side IN ('CLIENT', 'BOTH')", query)
        self.assertNotIn("LEFT JOIN modversion_download_overrides", query)
        self.assertNotIn("LEFT JOIN modversion_download_sources", query)
        self.assertEqual(parameters, (1, 0, 0))

    def test_bootstrap_build_query_loads_sparse_download_overrides(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchall.return_value = []
        build = Build(
            1, 2, "3", None, None, "1.21.1", None, 1, 0, "21", 4096, 0
        )

        with patch(
            "models.build.Database.get_connection", return_value=connection
        ):
            build.get_modversions_api(include_download_overrides=True)

        query = cursor.execute.call_args.args[0]
        self.assertIn("LEFT JOIN modversion_download_overrides", query)
        self.assertIn("AS jar_url_override", query)

    def test_bootstrap_build_query_loads_sparse_download_sources(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchall.return_value = []
        build = Build(
            1, 2, "3", None, None, "1.21.1", None, 1, 0, "21", 4096, 0
        )

        with patch(
            "models.build.Database.get_connection", return_value=connection
        ):
            build.get_modversions_api(include_download_sources=True)

        query = cursor.execute.call_args.args[0]
        self.assertIn("LEFT JOIN modversion_download_sources", query)
        self.assertIn("AS download_source_url", query)

    def test_server_manifest_can_include_optional_server_mods(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchall.return_value = [
            {
                "id": 3,
                "mod_id": 2,
                "version": "1.0",
                "mcversion": "1.21.1",
                "md5": "abc",
                "created_at": None,
                "updated_at": None,
                "filesize": 1024,
                "modname": "server-library",
                "pretty_name": "Server Library",
                "author": "CI",
                "link": None,
                "description": None,
                "side": "SERVER",
                "modtype": "MOD",
                "optional": 1,
            }
        ]
        build = Build(
            1, 2, "3", None, None, "1.21.1", None, 1, 0, "21", 4096, 0
        )

        with patch("models.build.Database.get_connection", return_value=connection):
            versions = build.get_modversions_api(
                target="server", include_optional=True
            )

        self.assertEqual([version.modname for version in versions], ["server-library"])
        self.assertEqual(versions[0].side, "SERVER")
        self.assertEqual(versions[0].optional, 1)
        query, parameters = cursor.execute.call_args.args
        self.assertIn("mods.side IN ('SERVER', 'BOTH')", query)
        self.assertEqual(parameters, (1, 1, 0))

    def test_modversion_build_memberships_are_shaped_for_the_read_api(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchall.return_value = [
            {
                "build_id": 4,
                "build_version": "2.0",
                "modpack_id": 3,
                "modpack_slug": "example-pack",
                "modpack_name": "Example Pack",
                "optional": 0,
            }
        ]
        version = Modversion(2, 1, "1.0", "1.21.1", "abc", None, None, 1)

        with patch(
            "models.modversion.Database.get_connection", return_value=connection
        ):
            builds = version.get_builds_api(cid="client-id")

        self.assertEqual(
            builds,
            [
                {
                    "id": 4,
                    "version": "2.0",
                    "optional": False,
                    "modpack": {
                        "id": 3,
                        "name": "example-pack",
                        "display_name": "Example Pack",
                    },
                }
            ],
        )
        query, parameters = cursor.execute.call_args.args
        self.assertIn("builds.is_published = 1", query)
        self.assertIn("build_modversion.optional IN (0, 1)", query)
        self.assertEqual(parameters, (2, "client-id"))

    def test_build_editor_groups_versions_and_excludes_assigned_mods(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = {
            "id": 7,
            "modpack_id": 3,
            "version": "2.0",
            "created_at": None,
            "updated_at": None,
            "minecraft": "1.21.1",
            "forge": None,
            "modloader": "FABRIC",
            "is_published": 1,
            "private": 0,
            "min_java": "21",
            "min_memory": 4096,
            "marked": 0,
            "modpack_name": "Example Pack",
        }
        cursor.fetchall.side_effect = [
            [
                {
                    "id": 11,
                    "optional": 0,
                    "version": "1.0",
                    "modverid": 101,
                    "name": "first",
                    "pretty_name": "First Mod",
                    "modid": 1,
                    "modtype": "MOD",
                    "integration_provider": "MODRINTH",
                },
                {
                    "id": 12,
                    "optional": 1,
                    "version": "2.0",
                    "modverid": 201,
                    "name": "second",
                    "pretty_name": "Second Mod",
                    "modid": 2,
                    "modtype": "CONFIG",
                },
            ],
            [
                {"id": 1, "pretty_name": "First Mod", "modtype": "MOD"},
                {"id": 2, "pretty_name": "Second Mod", "modtype": "CONFIG"},
                {"id": 3, "pretty_name": "Available Mod", "modtype": "RES"},
                {"id": 4, "pretty_name": "No Compatible Version", "modtype": "NONE"},
            ],
            [
                {"id": 102, "mod_id": 1, "version": "1.1", "mcversion": None, "modloader": None},
                {
                    "id": 101,
                    "mod_id": 1,
                    "version": "1.0",
                    "mcversion": "1.21.1",
                    "modloader": "FABRIC",
                },
                {
                    "id": 201,
                    "mod_id": 2,
                    "version": "2.0",
                    "mcversion": "1.21.1",
                    "modloader": None,
                },
                {
                    "id": 301,
                    "mod_id": 3,
                    "version": "3.0",
                    "mcversion": "1.21.1",
                    "modloader": "FABRIC",
                },
            ],
        ]

        with patch(
            "models.build_modversion.Database.get_connection",
            return_value=connection,
        ):
            editor = Build_modversion.get_build_editor_data(7)

        self.assertEqual(editor.packbuild.id, 7)
        self.assertEqual(editor.packbuildname, "Example Pack")
        self.assertEqual([mod["id"] for mod in editor.listmod], [3, 4])
        self.assertEqual(editor.listmod[0]["modtype"], "RES")
        self.assertEqual(
            [version["id"] for version in editor.listmodversions], [301]
        )
        self.assertEqual(
            [version["id"] for version in editor.buildlist[0]["versions"]],
            [102, 101],
        )
        self.assertEqual(
            editor.buildlist[0]["integration_provider"], "MODRINTH"
        )
        self.assertEqual(editor.buildlist[0]["modtype"], "MOD")
        self.assertEqual(
            [version["id"] for version in editor.buildlist[1]["versions"]],
            [201],
        )
        self.assertEqual(cursor.execute.call_count, 4)
        self.assertEqual(
            cursor.execute.call_args.args[1],
            ("1.21.1", "1.21.1", "1.21.1", "FABRIC", "FABRIC"),
        )
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_missing_build_editor_data_closes_its_connection(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = None

        with patch(
            "models.build_modversion.Database.get_connection",
            return_value=connection,
        ):
            editor = Build_modversion.get_build_editor_data(404)

        self.assertIsNone(editor)
        self.assertEqual(cursor.execute.call_count, 1)
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_update_all_uses_newest_compatible_version_per_build_entry(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchall.return_value = [
            {
                "membership_id": 11,
                "current_version_id": 101,
                "mod_id": 1,
                "replacement_version_id": 103,
                "build_minecraft": "1.21.1",
                "build_modloader": "FABRIC",
            },
            {
                "membership_id": 11,
                "current_version_id": 101,
                "mod_id": 1,
                "replacement_version_id": 102,
                "build_minecraft": "1.21.1",
                "build_modloader": "FABRIC",
            },
            {
                "membership_id": 12,
                "current_version_id": 201,
                "mod_id": 2,
                "replacement_version_id": 201,
                "build_minecraft": "1.21.1",
                "build_modloader": "FABRIC",
            },
        ]

        with (
            patch(
                "models.build_modversion.Database.get_connection",
                return_value=connection,
            ),
            patch(
                "models.modversion.Modversion._add_required_dependencies"
            ) as add_dependencies,
        ):
            updated = Build_modversion.update_all_compatible(7)

        self.assertEqual(updated, 1)
        self.assertEqual(cursor.execute.call_args.args[1], (7,))
        cursor.executemany.assert_called_once_with(
            """UPDATE build_modversion
                       SET modversion_id = %s
                       WHERE id = %s""",
            [(103, 11)],
        )
        connection.commit.assert_called_once_with()
        connection.rollback.assert_not_called()
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()
        add_dependencies.assert_called_once_with(
            cursor, 7, "1.21.1", 1, "FABRIC"
        )

    def test_update_all_prefers_provider_version_over_newer_local_id(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchall.return_value = [
            {
                "membership_id": 11,
                "current_version_id": 101,
                "mod_id": 9,
                "replacement_version_id": 103,
                "build_minecraft": "1.21.1",
                "build_modloader": "FABRIC",
            },
            {
                "membership_id": 11,
                "current_version_id": 101,
                "mod_id": 9,
                "replacement_version_id": 102,
                "build_minecraft": "1.21.1",
                "build_modloader": "FABRIC",
            },
        ]

        with (
            patch(
                "models.build_modversion.Database.get_connection",
                return_value=connection,
            ),
            patch(
                "models.modversion.Modversion._add_required_dependencies"
            ),
        ):
            updated = Build_modversion.update_all_compatible(7, {9: 102})

        self.assertEqual(updated, 1)
        cursor.executemany.assert_called_once_with(
            """UPDATE build_modversion
                       SET modversion_id = %s
                       WHERE id = %s""",
            [(102, 11)],
        )

    def test_update_all_syncs_an_updated_launcher_version_to_the_build(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchall.return_value = [
            {
                "membership_id": 11,
                "current_version_id": 101,
                "mod_id": 1,
                "replacement_version_id": 103,
                "replacement_version": "1.7.10-10.13.4.1614",
                "replacement_modloader": "FORGE",
                "build_minecraft": "1.7.10",
                "build_modloader": None,
                "modtype": "LAUNCHER",
            }
        ]

        with (
            patch(
                "models.build_modversion.Database.get_connection",
                return_value=connection,
            ),
            patch(
                "models.modversion.Modversion._add_required_dependencies"
            ) as add_dependencies,
        ):
            updated = Build_modversion.update_all_compatible(7)

        self.assertEqual(updated, 1)
        cursor.execute.assert_any_call(
            """UPDATE builds
                   SET forge = %s, modloader = %s
                   WHERE id = %s""",
            ("1.7.10-10.13.4.1614", "FORGE", 7),
        )
        add_dependencies.assert_called_once_with(
            cursor, 7, "1.7.10", 1, "FORGE"
        )

    def test_jar_override_requires_a_plain_https_url(self):
        self.assertEqual(
            Modversion.normalize_jar_url_override(
                " https://downloads.example/mod.jar?channel=stable "
            ),
            "https://downloads.example/mod.jar?channel=stable",
        )
        self.assertIsNone(Modversion.normalize_jar_url_override(""))
        for invalid in (
            "http://downloads.example/mod.jar",
            "https://user:secret@downloads.example/mod.jar",
            "https://downloads.example/mod.jar#fragment",
            "https://downloads.example/mod file.jar",
            "not-a-url",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                Modversion.normalize_jar_url_override(invalid)

    def test_jar_override_rejects_private_network_destinations(self):
        for invalid in (
            "https://127.0.0.1/mod.jar",
            "https://[::1]/mod.jar",
            "https://169.254.169.254/latest/meta-data",
        ):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                ValueError, "public host"
            ):
                Modversion.normalize_jar_url_override(invalid)

        with (
            patch.object(Modversion, "_open_verified_https_response") as open_url,
            self.assertRaisesRegex(ValueError, "public host"),
        ):
            Modversion.verify_jar_url_override(
                "https://internal.example/mod.jar",
                "a" * 32,
                resolver=lambda *_args, **_kwargs: [
                    (None, None, None, None, ("10.0.0.4", 443))
                ],
            )
        open_url.assert_not_called()

    def test_build_clone_rolls_back_as_one_transaction(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.lastrowid = 22
        cursor.fetchall.return_value = [
            {"id": 8, "modversion_id": 9, "optional": 1}
        ]
        with (
            patch.object(Database, "get_connection", return_value=connection),
            patch(
                "models.advanced_optional.AdvancedOptional.clone_build",
                side_effect=RuntimeError("clone failed"),
            ),
            self.assertRaisesRegex(RuntimeError, "clone failed"),
        ):
            Build.new(
                3,
                "2.0",
                "1.20.1",
                0,
                0,
                None,
                2048,
                7,
                forge="47.3.0",
                modloader="FORGE",
            )

        connection.commit.assert_not_called()
        connection.rollback.assert_called_once_with()
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_jar_override_update_is_scoped_to_parent_mod(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = {"jarmd5": "b" * 32}
        with (
            patch(
                "models.modversion.Database.get_connection",
                return_value=connection,
            ),
            patch.object(
                Modversion,
                "verify_jar_url_override",
                return_value=456,
            ) as verify,
        ):
            stored = Modversion.update_jar_url_override(
                12, 9, "https://downloads.example/mod.jar"
            )

        self.assertEqual(stored, "https://downloads.example/mod.jar")
        verify.assert_called_once_with(
            "https://downloads.example/mod.jar", "b" * 32
        )
        self.assertEqual(
            cursor.execute.call_args_list[0].args[1],
            (12, 9),
        )
        filesize_call = next(
            call
            for call in cursor.execute.call_args_list
            if "SET jarfilesize" in call.args[0]
        )
        self.assertEqual(filesize_call.args[1], (456, 12, 9))
        override_call = next(
            call
            for call in cursor.execute.call_args_list
            if "INSERT INTO modversion_download_overrides" in call.args[0]
        )
        self.assertIn(
            "INSERT INTO modversion_download_overrides",
            override_call.args[0],
        )
        self.assertEqual(
            override_call.args[1],
            (12, "https://downloads.example/mod.jar"),
        )
        connection.commit.assert_called_once_with()

    def test_jar_override_verification_rejects_a_different_md5(self):
        connection = Mock()
        response = Mock(status=200)
        response.getheader.return_value = "9"
        response.read.side_effect = [b"different", b""]

        with (
            patch.object(
                Modversion,
                "_open_verified_https_response",
                return_value=(connection, response),
            ) as open_url,
            self.assertRaisesRegex(ValueError, "does not match"),
        ):
            Modversion.verify_jar_url_override(
                "https://downloads.example/mod.jar",
                hashlib.md5(b"expected", usedforsecurity=False).hexdigest(),
                resolver=lambda *_args, **_kwargs: [
                    (None, None, None, None, ("93.184.216.34", 443))
                ],
            )

        open_url.assert_called_once_with(
            "https://downloads.example/mod.jar",
            ("93.184.216.34",),
        )
        response.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_jar_override_connection_is_pinned_to_the_validated_address(self):
        connection = Mock()
        response = Mock()
        connection.getresponse.return_value = response

        with patch(
            "models.modversion._PinnedHTTPSConnection",
            return_value=connection,
        ) as pinned_connection:
            returned_connection, returned_response = (
                Modversion._open_verified_https_response(
                    "https://downloads.example:8443/files/mod jar.jar?build=one two",
                    ("93.184.216.34",),
                )
            )

        self.assertIs(returned_connection, connection)
        self.assertIs(returned_response, response)
        pinned_connection.assert_called_once_with(
            "downloads.example",
            8443,
            "93.184.216.34",
            connect_timeout=5,
            read_timeout=60,
        )
        connection.request.assert_called_once_with(
            "GET",
            "/files/mod%20jar.jar?build=one%20two",
            headers={
                "Host": "downloads.example:8443",
                "Accept-Encoding": "identity",
                "User-Agent": "solder.py jar verifier",
            },
        )

    def test_mismatched_jar_override_is_not_saved(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = {"jarmd5": "b" * 32}
        with (
            patch(
                "models.modversion.Database.get_connection",
                return_value=connection,
            ),
            patch.object(
                Modversion,
                "verify_jar_url_override",
                side_effect=ValueError(
                    "The override JAR MD5 does not match the stored JAR MD5."
                ),
            ),
            self.assertRaisesRegex(ValueError, "does not match"),
        ):
            Modversion.update_jar_url_override(
                12, 9, "https://downloads.example/mod.jar"
            )

        queries = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertFalse(
            any("modversion_download_overrides" in query for query in queries)
        )
        connection.commit.assert_not_called()

    def test_clearing_jar_override_deletes_the_sparse_row(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = {"jarmd5": "b" * 32}
        with patch(
            "models.modversion.Database.get_connection",
            return_value=connection,
        ):
            stored = Modversion.update_jar_url_override(12, 9, "")

        self.assertIsNone(stored)
        self.assertIn(
            "DELETE FROM modversion_download_overrides",
            cursor.execute.call_args_list[1].args[0],
        )
        self.assertEqual(cursor.execute.call_args_list[1].args[1], (12,))

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
