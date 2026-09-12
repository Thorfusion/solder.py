import hashlib
import io
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch
import zipfile

from tests.environment import configure_test_environment


configure_test_environment()

from models.mcinstance import (  # noqa: E402
    MCInstanceBuild,
    MCInstanceExport,
    MCInstanceExportError,
    MCInstanceJar,
    MCInstanceJarError,
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
            self.addCleanup(result.close)

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
        self.assertIn("[example-mod]", resources)
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
        self.assertIn("option1.resources = optional-mod", optionals)

    def test_empty_build_still_produces_a_valid_archive(self):
        result = MCInstanceExport.render(
            self.build, [], "https://cdn.example.test/mods", "unused"
        )
        self.addCleanup(result.close)

        with zipfile.ZipFile(result) as archive:
            self.assertEqual(archive.read("resources.packconfig"), b"")
            self.assertEqual(archive.read("optionals.packconfig"), b"")
            self.assertIn("overrides/", archive.namelist())
            self.assertIn("client-overrides/", archive.namelist())
            self.assertIn("server-overrides/", archive.namelist())

    def test_large_export_spills_from_memory_to_a_temporary_file(self):
        with patch("models.mcinstance._SPOOL_MEMORY_LIMIT", 1):
            result = MCInstanceExport.render(
                self.build, [], "https://cdn.example.test/mods", "unused"
            )

        try:
            self.assertTrue(result._rolled)
            with zipfile.ZipFile(result) as archive:
                self.assertIn("metadata.packconfig", archive.namelist())
        finally:
            result.close()

    def test_build_modloader_is_written_to_metadata(self):
        build = replace(self.build, modloader="FABRIC", forge="0.16.14")
        result = MCInstanceExport.render(
            build, [], "https://cdn.example.test/mods", "unused"
        )
        self.addCleanup(result.close)

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

    def test_local_package_is_streamed_without_reading_the_whole_file(self):
        package_zip = io.BytesIO()
        with zipfile.ZipFile(package_zip, "w") as package:
            package.writestr("config/test.cfg", b"value")
        data = package_zip.getvalue()

        with tempfile.TemporaryDirectory() as directory:
            package_dir = Path(directory, "streamed-config")
            package_dir.mkdir()
            Path(package_dir, "streamed-config-1.zip").write_bytes(data)
            package = self.package(
                name="streamed-config",
                version="1",
                md5=md5(data),
                jarmd5="0",
                modtype="CONFIG",
            )

            with patch.object(
                Path,
                "read_bytes",
                side_effect=AssertionError("whole-file read used"),
            ):
                result = MCInstanceExport.render(
                    self.build,
                    [package],
                    "https://cdn.example.test/mods",
                    directory,
                )

        try:
            with zipfile.ZipFile(result) as archive:
                self.assertEqual(
                    archive.read("overrides/config/test.cfg"), b"value"
                )
        finally:
            result.close()

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


class MCInstanceJarTests(unittest.TestCase):
    @staticmethod
    def legacy_package(jar_data=b"legacy jar"):
        package = io.BytesIO()
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("mods/original-name.jar", jar_data)
            archive.writestr("config/example.cfg", b"enabled=true")
        return package.getvalue()

    @staticmethod
    def mod():
        return SimpleNamespace(id=3, name="example-mod", modtype="MOD")

    @staticmethod
    def version(package_data):
        return SimpleNamespace(
            id=9,
            mod_id=3,
            version="1.7.10-1.0",
            md5=md5(package_data),
            jarmd5=None,
        )

    def test_create_legacy_jar_from_local_solder_package(self):
        jar_data = b"legacy local jar"
        package_data = self.legacy_package(jar_data)
        r2 = Mock()

        with tempfile.TemporaryDirectory() as directory:
            package_dir = Path(directory, "example-mod")
            package_dir.mkdir()
            Path(package_dir, "example-mod-1.7.10-1.0.zip").write_bytes(
                package_data
            )
            with patch(
                "models.mcinstance.Modversion.update_modversion_jarmd5"
            ) as update_hash:
                jar_hash = MCInstanceJar.create(
                    self.mod(),
                    self.version(package_data),
                    "https://repo.example.test/mods/",
                    directory,
                    r2,
                    "bucket",
                )

            final_jar = Path(
                package_dir, "example-mod-1.7.10-1.0.jar"
            )
            self.assertEqual(final_jar.read_bytes(), jar_data)
            self.assertEqual(jar_hash, md5(jar_data))
            update_hash.assert_called_once_with(9, md5(jar_data))
            r2.upload_file.assert_called_once_with(
                str(final_jar),
                "bucket",
                "mods/example-mod/example-mod-1.7.10-1.0.jar",
                ExtraArgs={"ContentType": "application/jar"},
            )

    def test_create_legacy_jar_downloads_missing_local_package(self):
        jar_data = b"legacy remote jar"
        package_data = self.legacy_package(jar_data)
        response = MagicMock()
        response.status_code = 200
        response.headers = {"content-length": str(len(package_data))}
        response.iter_content.return_value = [package_data]
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)

        with tempfile.TemporaryDirectory() as directory, patch(
            "models.mcinstance.requests.get", return_value=response
        ) as get, patch(
            "models.mcinstance.Modversion.update_modversion_jarmd5"
        ) as update_hash:
            jar_hash = MCInstanceJar.create(
                self.mod(),
                self.version(package_data),
                "https://repo.example.test/mods/",
                directory,
            )

            self.assertEqual(
                Path(
                    directory,
                    "example-mod",
                    "example-mod-1.7.10-1.0.jar",
                ).read_bytes(),
                jar_data,
            )

        self.assertEqual(jar_hash, md5(jar_data))
        get.assert_called_once_with(
            "https://repo.example.test/mods/example-mod/"
            "example-mod-1.7.10-1.0.zip",
            stream=True,
            allow_redirects=False,
            timeout=(5, 60),
        )
        response.raise_for_status.assert_called_once_with()
        update_hash.assert_called_once_with(9, md5(jar_data))

    def test_create_legacy_jar_reads_md5_repository_path(self):
        jar_data = b"legacy repository jar"
        package_data = self.legacy_package(jar_data)

        with (
            tempfile.TemporaryDirectory() as repository_directory,
            tempfile.TemporaryDirectory() as local_directory,
        ):
            repository_mod = Path(repository_directory, "example-mod")
            repository_mod.mkdir()
            Path(
                repository_mod, "example-mod-1.7.10-1.0.zip"
            ).write_bytes(package_data)
            with (
                patch("models.mcinstance.requests.get") as get,
                patch(
                    "models.mcinstance.Modversion.update_modversion_jarmd5"
                ) as update_hash,
            ):
                jar_hash = MCInstanceJar.create(
                    self.mod(),
                    self.version(package_data),
                    repository_directory,
                    local_directory,
                )

            final_jar = Path(
                local_directory,
                "example-mod",
                "example-mod-1.7.10-1.0.jar",
            )
            self.assertEqual(final_jar.read_bytes(), jar_data)

        self.assertEqual(jar_hash, md5(jar_data))
        get.assert_not_called()
        update_hash.assert_called_once_with(9, md5(jar_data))

    def test_create_legacy_jar_rejects_a_changed_package(self):
        package_data = self.legacy_package()
        version = self.version(package_data)
        version.md5 = "a" * 32

        with tempfile.TemporaryDirectory() as directory:
            package_dir = Path(directory, "example-mod")
            package_dir.mkdir()
            Path(package_dir, "example-mod-1.7.10-1.0.zip").write_bytes(
                package_data
            )
            with patch(
                "models.mcinstance.Modversion.update_modversion_jarmd5"
            ) as update_hash, self.assertRaisesRegex(
                MCInstanceJarError, "MD5 verification"
            ):
                MCInstanceJar.create(
                    self.mod(),
                    version,
                    "https://repo.example.test/mods/",
                    directory,
                )

            update_hash.assert_not_called()
            self.assertFalse(
                Path(package_dir, "example-mod-1.7.10-1.0.jar").exists()
            )


if __name__ == "__main__":
    unittest.main()
