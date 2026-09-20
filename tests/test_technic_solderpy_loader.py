import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import zipfile

from tests.environment import configure_test_environment


configure_test_environment()

from models.platform_export import (  # noqa: E402
    DownloaderRelease,
    NativeModrinthFile,
    PlatformPackExport,
    SelectedDownloader,
)
from models.technic_solderpy_loader import TechnicSolderPyLoader  # noqa: E402


class FakeResponse:
    def __init__(self, body):
        self.body = body
        self.status_code = 200
        self.headers = {"content-length": str(len(body))}

    def raise_for_status(self):
        return None

    def iter_content(self, chunk_size):
        yield self.body

    def close(self):
        return None


class FakeHTTP:
    def __init__(self, bodies):
        self.bodies = iter(bodies)

    def get(self, _url, **_kwargs):
        return FakeResponse(next(self.bodies))


def native(project, version, filename, body):
    return NativeModrinthFile(
        project_id=project,
        version_id=version,
        filename=filename,
        download_url=(
            f"https://cdn.modrinth.com/data/{project}/versions/"
            f"{version}/{filename}"
        ),
        sha1=hashlib.sha1(body, usedforsecurity=False).hexdigest(),
        sha512=hashlib.sha512(body).hexdigest(),
        size=len(body),
    )


def selected_downloader():
    loader_body = b"solderpy loader"
    runtime_body = b"relauncher"
    release = DownloaderRelease(
        selector="loader-release",
        version="0.1.0",
        default=True,
        modrinth=native("5LpwENAj", "loader-release", "loader.jar", loader_body),
        modrinth_dependencies=(
            native("zCFNaupz", "runtime-release", "relauncher.jar", runtime_body),
        ),
    )
    return (
        SelectedDownloader(
            PlatformPackExport.downloader_spec("solderpyloader"), release
        ),
        loader_body,
        runtime_body,
    )


class TechnicSolderPyLoaderTests(unittest.TestCase):
    def test_delivery_mode_is_validated_and_old_rows_default_to_loader(self):
        configured = TechnicSolderPyLoader._from_row(
            {
                "build_id": 7,
                "version_id": "release",
                "version": "0.1.0",
                "bootstrap_path": "bootstrap.zip",
                "bootstrap_md5": "a" * 32,
                "bootstrap_filesize": 100,
            }
        )

        self.assertEqual(
            configured.delivery_mode,
            TechnicSolderPyLoader.LOADER_DELIVERY,
        )
        with self.assertRaisesRegex(ValueError, "Technic Solder API"):
            TechnicSolderPyLoader.normalize_delivery_mode("invalid")

    def test_archive_contains_loader_runtime_and_build_config(self):
        selected, loader_body, runtime_body = selected_downloader()
        config = json.dumps(
            {
                "enabled": True,
                "api": "https://solder.example.test/api/",
                "modpack": "example-pack",
                "build": "1.0",
                "target": "auto",
            }
        ).encode()

        with tempfile.TemporaryDirectory() as directory:
            archive_path = TechnicSolderPyLoader._write_archive(
                Path(directory),
                selected,
                config,
                relauncher_config=b"enabled = false\njava.versions = 8\n",
                http=FakeHTTP((loader_body, runtime_body)),
            )
            try:
                with zipfile.ZipFile(archive_path) as archive:
                    self.assertEqual(
                        set(archive.namelist()),
                        {
                            "mods/!solderpy-loader.jar",
                            "mods/!relauncher.jar",
                            "config/solderpy-loader.json",
                            "config/relauncher/config.cfg",
                        },
                    )
                    self.assertEqual(
                        archive.read("mods/!solderpy-loader.jar"), loader_body
                    )
                    self.assertEqual(
                        archive.read("mods/!relauncher.jar"),
                        runtime_body,
                    )
                    self.assertEqual(
                        archive.read("config/relauncher/config.cfg"),
                        b"enabled = false\njava.versions = 8\n",
                    )
            finally:
                archive_path.unlink(missing_ok=True)

    def test_manifest_entry_uses_bootstrap_type_and_public_path(self):
        configured = TechnicSolderPyLoader(
            build_id=7,
            version_id="release",
            version="0.1.0",
            bootstrap_path="_solderpy/solderpy-loader/7/bootstrap-a.zip",
            bootstrap_md5="a" * 32,
            bootstrap_filesize=100,
        )

        entry = configured.manifest_entries(
            "https://cdn.example.test/mods/", extended=True
        )[0]

        self.assertEqual(entry["type"], "BOOTSTRAP")
        self.assertEqual(
            entry["url"],
            "https://cdn.example.test/mods/_solderpy/solderpy-loader/7/bootstrap-a.zip",
        )

    def test_configure_materializes_repository_artifact_and_saves_metadata(self):
        selected, loader_body, runtime_body = selected_downloader()
        build = SimpleNamespace(
            id=7,
            version="1.0",
            minecraft="1.7.10",
            modloader="FORGE",
            is_published=1,
            private=0,
        )
        modpack = SimpleNamespace(
            id=3, slug="example-pack", hidden=0, private=0,
            enable_optionals=1,
        )
        connection = Mock()
        cursor = connection.cursor.return_value
        stored = Mock()
        r2 = Mock()

        with (
            tempfile.TemporaryDirectory() as directory,
            patch(
                "models.technic_solderpy_loader.Database.get_connection",
                return_value=connection,
            ),
            patch.object(TechnicSolderPyLoader, "get", return_value=stored),
        ):
            result = TechnicSolderPyLoader.configure(
                build,
                modpack,
                selected,
                directory,
                "https://cdn.example.test/mods/",
                "https://solder.example.test/",
                delivery_mode="TECHNIC",
                r2_client=r2,
                r2_bucket="bucket",
                http=FakeHTTP((loader_body, runtime_body)),
            )
            artifacts = list(
                (Path(directory) / "_solderpy" / "solderpy-loader" / "7").glob(
                    "*.zip"
                )
            )
            with zipfile.ZipFile(artifacts[0]) as archive:
                loader_config = json.loads(
                    archive.read("config/solderpy-loader.json")
                )

        self.assertIs(result, stored)
        self.assertEqual(len(artifacts), 1)
        statements = [call.args for call in cursor.execute.call_args_list]
        self.assertTrue(
            any("technic_solderpy_loader_builds" in args[0] for args in statements)
        )
        insert = next(
            args
            for args in statements
            if "INSERT INTO technic_solderpy_loader_builds" in args[0]
        )
        self.assertEqual(insert[1][3], "TECHNIC")
        self.assertEqual(loader_config["target"], "client")
        self.assertEqual(loader_config["platform"], "technic")
        self.assertTrue(
            any(
                "SET modpacks.enable_optionals = 0" in args[0]
                and args[1] == (7,)
                for args in statements
            )
        )
        connection.commit.assert_called_once_with()
        connection.rollback.assert_not_called()
        uploaded_key = r2.upload_file.call_args.args[2]
        self.assertTrue(uploaded_key.startswith("mods/_solderpy/"))


if __name__ == "__main__":
    unittest.main()
