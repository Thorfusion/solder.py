import datetime
import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from mysql.connector import IntegrityError, errorcode

from tests.environment import configure_test_environment


configure_test_environment()

from models.build import Build  # noqa: E402
from models.build_modversion import Build_modversion  # noqa: E402
from models.common import common  # noqa: E402
from models.database import Database  # noqa: E402
from models.mod import DuplicateModError, Mod, UploadVerificationError  # noqa: E402
from models.mod_dependency import CircularDependencyError, ModDependency  # noqa: E402
from models.modpack import Modpack  # noqa: E402
from models.modversion import (  # noqa: E402
    IncompatibleModVersionError,
    MissingDependencyVersionError,
    Modversion,
)
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
            {"optional": True, "server": True},
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
            [{"id": 3, "name": "other", "pretty_name": "Other"}],
        ]

        with patch(
            "models.mod_dependency.Database.get_connection",
            return_value=connection,
        ):
            dependencies, available = ModDependency.get_management_data(1)

        self.assertEqual(dependencies[0]["dependency_mod_id"], 2)
        self.assertEqual(available[0]["id"], 3)
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
            (2, "1.21.1", "FABRIC", "FABRIC", "1.21.1", "FABRIC"),
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
                modloader="fabric",
            )

        query, parameters = connection.cursor.return_value.execute.call_args.args
        self.assertIn("jarmd5", query)
        self.assertEqual(parameters[3], "FABRIC")
        self.assertEqual(parameters[6], jar_md5)
        self.assertEqual(version.id, 42)
        self.assertEqual(version.modloader, "FABRIC")
        connection.cursor.return_value.close.assert_called_once_with()
        connection.close.assert_called_once_with()

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
        self.assertEqual(parameters, (1, 0))

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
        self.assertEqual(parameters, (1, 1))

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
                },
                {
                    "id": 12,
                    "optional": 1,
                    "version": "2.0",
                    "modverid": 201,
                    "name": "second",
                    "pretty_name": "Second Mod",
                    "modid": 2,
                },
            ],
            [
                {"id": 1, "pretty_name": "First Mod"},
                {"id": 2, "pretty_name": "Second Mod"},
                {"id": 3, "pretty_name": "Available Mod"},
                {"id": 4, "pretty_name": "No Compatible Version"},
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
        self.assertEqual(
            [version["id"] for version in editor.listmodversions], [301]
        )
        self.assertEqual(
            [version["id"] for version in editor.buildlist[0]["versions"]],
            [102, 101],
        )
        self.assertEqual(
            [version["id"] for version in editor.buildlist[1]["versions"]],
            [201],
        )
        self.assertEqual(cursor.execute.call_count, 4)
        self.assertEqual(
            cursor.execute.call_args.args[1],
            ("1.21.1", "FABRIC", "FABRIC"),
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
