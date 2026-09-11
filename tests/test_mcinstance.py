import hashlib
import io
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from tests.environment import configure_test_environment


configure_test_environment()

from models.mcinstance import (  # noqa: E402
    MCInstanceBuild,
    MCInstanceExport,
    MCInstanceExportError,
    MCInstancePackage,
)


def md5(data):
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


class MCInstanceExportTests(unittest.TestCase):
    def setUp(self):
        self.build = MCInstanceBuild(
            id=7,
            version="2.0",
            minecraft="1.7.10",
            forge="10.13.4.1614",
            modpack_id=3,
            modpack_name="Example Pack",
            modpack_slug="example-pack",
        )

    @staticmethod
    def package(**changes):
        values = {
            "mod_id": 11,
            "name": "example-mod",
            "pretty_name": "Example Mod",
            "description": "An example",
            "version": "1.7.10-1.0",
            "md5": "0",
            "jarmd5": "d41d8cd98f00b204e9800998ecf8427e",
            "side": "BOTH",
            "modtype": "MOD",
            "optional": False,
        }
        values.update(changes)
        return MCInstancePackage(**values)

    def test_export_maps_resources_optionals_sides_and_skipped_loader_packages(self):
        config_zip = io.BytesIO()
        with zipfile.ZipFile(config_zip, "w") as package:
            package.writestr("config/example.cfg", b"setting=true")
        config_data = config_zip.getvalue()

        with tempfile.TemporaryDirectory() as directory:
            config_dir = Path(directory, "example-config")
            config_dir.mkdir()
            Path(config_dir, "example-config-1.7.10-2.zip").write_bytes(config_data)

            packages = [
                self.package(),
                self.package(
                    mod_id=12,
                    name="optional-mod",
                    pretty_name="Optional # Mod",
                    description="Optional\nclient mod",
                    optional=True,
                    side="CLIENT",
                ),
                self.package(
                    mod_id=13,
                    name="example-config",
                    version="1.7.10-2",
                    md5=md5(config_data),
                    jarmd5="0",
                    side="CLIENT",
                    modtype="CONFIG",
                ),
                self.package(mod_id=14, name="mcil", modtype="MCIL"),
                self.package(mod_id=15, name="launcher", modtype="LAUNCHER"),
            ]
            result = MCInstanceExport.render(
                self.build, packages, "https://cdn.example.test/mods/", directory
            )

        with zipfile.ZipFile(result) as archive:
            self.assertEqual(
                archive.read("client-overrides/config/example.cfg"), b"setting=true"
            )
            metadata = archive.read("metadata.packconfig").decode()
            resources = archive.read("resources.packconfig").decode()
            optionals = archive.read("optionals.packconfig").decode()

        self.assertIn("formatVersion = 1", metadata)
        self.assertIn("minecraftVersion = 1.7.10", metadata)
        self.assertIn("name = Example Pack", metadata)
        self.assertIn("version = 2.0", metadata)
        self.assertIn("[solder-mod-11]", resources)
        self.assertIn(
            "url = https://cdn.example.test/mods/example-mod/"
            "example-mod-1.7.10-1.0.jar",
            resources,
        )
        self.assertIn("destination = mods/example-mod-1.7.10-1.0.jar", resources)
        self.assertIn("side = client\noptional = true", resources)
        self.assertNotIn("mcil", resources)
        self.assertNotIn("launcher", resources)
        self.assertIn("maxchoices = 1", optionals)
        self.assertIn("option1.name = Optional - Mod", optionals)
        self.assertIn("option1.description = Optional client mod", optionals)
        self.assertIn("option1.resources = solder-mod-12", optionals)

    def test_empty_build_still_produces_a_valid_archive(self):
        result = MCInstanceExport.render(
            self.build, [], "https://cdn.example.test/mods", "unused"
        )

        with zipfile.ZipFile(result) as archive:
            self.assertEqual(archive.read("resources.packconfig"), b"")
            self.assertEqual(archive.read("optionals.packconfig"), b"")
            self.assertIn("overrides/", archive.namelist())
            self.assertIn("client-overrides/", archive.namelist())
            self.assertIn("server-overrides/", archive.namelist())

    def test_build_modloader_is_written_to_metadata(self):
        build = replace(self.build, modloader="FABRIC", forge="0.16.14")
        result = MCInstanceExport.render(
            build, [], "https://cdn.example.test/mods", "unused"
        )

        with zipfile.ZipFile(result) as archive:
            metadata = archive.read("metadata.packconfig").decode()
        self.assertIn("type = fabric", metadata)
        self.assertIn("version = 0.16.14", metadata)

    def test_incompatible_package_modloader_is_rejected(self):
        build = replace(self.build, modloader="FABRIC")
        package = self.package(modloader="FORGE")

        with self.assertRaisesRegex(MCInstanceExportError, "modloader"):
            MCInstanceExport.render(
                build,
                [package],
                "https://cdn.example.test/mods",
                "unused",
            )

    def test_optional_bundled_package_is_rejected(self):
        package = self.package(jarmd5="0", optional=True, modtype="CONFIG")

        with self.assertRaisesRegex(MCInstanceExportError, "verified raw JAR"):
            MCInstanceExport.render(
                self.build, [package], "https://cdn.example.test/mods", "unused"
            )

    def test_optional_server_package_is_rejected(self):
        package = self.package(optional=True, side="SERVER")

        with self.assertRaisesRegex(MCInstanceExportError, "server-only"):
            MCInstanceExport.render(
                self.build, [package], "https://cdn.example.test/mods", "unused"
            )

    def test_archive_traversal_is_rejected(self):
        package_zip = io.BytesIO()
        with zipfile.ZipFile(package_zip, "w") as package:
            package.writestr("../outside.txt", b"bad")
        data = package_zip.getvalue()

        with tempfile.TemporaryDirectory() as directory:
            package_dir = Path(directory, "bad-config")
            package_dir.mkdir()
            Path(package_dir, "bad-config-1.zip").write_bytes(data)
            package = self.package(
                name="bad-config",
                version="1",
                md5=md5(data),
                jarmd5="0",
                modtype="CONFIG",
            )

            with self.assertRaisesRegex(MCInstanceExportError, "unsafe path"):
                MCInstanceExport.render(
                    self.build,
                    [package],
                    "https://cdn.example.test/mods",
                    directory,
                )

    def test_stored_package_md5_is_verified(self):
        package_zip = io.BytesIO()
        with zipfile.ZipFile(package_zip, "w") as package:
            package.writestr("config/test.cfg", b"value")

        with tempfile.TemporaryDirectory() as directory:
            package_dir = Path(directory, "bad-hash")
            package_dir.mkdir()
            Path(package_dir, "bad-hash-1.zip").write_bytes(package_zip.getvalue())
            package = self.package(
                name="bad-hash",
                version="1",
                md5="a" * 32,
                jarmd5="0",
                modtype="CONFIG",
            )

            with self.assertRaisesRegex(MCInstanceExportError, "does not match"):
                MCInstanceExport.render(
                    self.build,
                    [package],
                    "https://cdn.example.test/mods",
                    directory,
                )

    def test_load_uses_one_query_and_closes_database_resources(self):
        row = {
            "build_id": 7,
            "build_version": "2.0",
            "minecraft": "1.7.10",
            "forge": None,
            "modpack_id": 3,
            "modpack_name": "Example Pack",
            "modpack_slug": "example-pack",
            "mod_id": None,
        }
        connection = Mock()
        connection.cursor.return_value.fetchall.return_value = [row]

        with patch(
            "models.mcinstance.Database.get_connection", return_value=connection
        ):
            build, packages = MCInstanceExport.load(7)

        self.assertEqual(build.id, 7)
        self.assertEqual(packages, [])
        self.assertEqual(connection.cursor.return_value.execute.call_count, 1)
        connection.cursor.return_value.close.assert_called_once_with()
        connection.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
