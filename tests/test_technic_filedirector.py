import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import zipfile

from tests.environment import configure_test_environment


configure_test_environment()

from models.integration import ModrinthProvider  # noqa: E402
from models.technic_filedirector import TechnicFileDirector  # noqa: E402


class TechnicFileDirectorTests(unittest.TestCase):
    def test_bootstrap_and_remote_config_use_technic_instance_paths(self):
        version = SimpleNamespace(version_id="release", version_number="1.9.1")

        def download(_provider, _version, destination):
            Path(destination).write_bytes(b"filedirector jar")

        with tempfile.TemporaryDirectory() as directory, patch.object(
            ModrinthProvider, "download", download
        ):
            bootstrap, config = TechnicFileDirector._write_archives(
                Path(directory),
                version,
                "https://solder.example.test/filedirector/pack/1/technic.bundle.json",
            )
            try:
                with zipfile.ZipFile(bootstrap) as archive:
                    self.assertEqual(
                        archive.namelist(),
                        ["mods/!solderpy-filedirector.jar"],
                    )
                with zipfile.ZipFile(config) as archive:
                    self.assertEqual(
                        archive.namelist(),
                        ["config/mod-director/solderpy.remote.json"],
                    )
                    content = archive.read(
                        "config/mod-director/solderpy.remote.json"
                    )
                    self.assertIn(b"technic.bundle.json", content)
            finally:
                bootstrap.unlink(missing_ok=True)
                config.unlink(missing_ok=True)

    def test_manifest_entries_use_public_repository_paths(self):
        configured = TechnicFileDirector(
            build_id=7,
            version_id="release",
            version="1.9.1",
            bootstrap_path="_solderpy/filedirector/7/bootstrap-a.zip",
            bootstrap_md5="a" * 32,
            bootstrap_filesize=100,
            config_path="_solderpy/filedirector/7/config-b.zip",
            config_md5="b" * 32,
            config_filesize=200,
        )

        entries = configured.manifest_entries(
            "https://cdn.example.test/mods/"
        )

        self.assertEqual(
            entries[0]["url"],
            "https://cdn.example.test/mods/_solderpy/filedirector/7/bootstrap-a.zip",
        )
        self.assertEqual(entries[1]["name"], "solderpy-filedirector-config")

    def test_configure_materializes_repository_artifacts_and_saves_metadata(self):
        build = SimpleNamespace(
            id=7,
            version="1.0",
            minecraft="1.7.10",
            modloader="FORGE",
            is_published=1,
            private=0,
        )
        modpack = SimpleNamespace(
            slug="example-pack",
            optional_mode=1,
            hidden=0,
            private=0,
        )
        version = SimpleNamespace(
            project_id="4dRu1OUz",
            version_id="release",
            version_number="1.9.1",
        )
        connection = Mock()
        cursor = connection.cursor.return_value
        stored = Mock()

        def download(_provider, _version, destination):
            Path(destination).write_bytes(b"filedirector jar")

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(ModrinthProvider, "download", download),
            patch(
                "models.technic_filedirector.Database.get_connection",
                return_value=connection,
            ),
            patch.object(TechnicFileDirector, "get", return_value=stored),
        ):
            result = TechnicFileDirector.configure(
                build,
                modpack,
                version,
                directory,
                "https://cdn.example.test/mods/",
                "https://solder.example.test/",
            )
            artifacts = sorted(
                (Path(directory) / "_solderpy" / "filedirector" / "7").glob(
                    "*.zip"
                )
            )

        self.assertIs(result, stored)
        self.assertEqual(len(artifacts), 2)
        self.assertIn("technic_filedirector_builds", cursor.execute.call_args.args[0])
        connection.commit.assert_called_once_with()
        connection.rollback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
