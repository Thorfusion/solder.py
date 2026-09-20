import io
import hashlib
import json
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

from tests.environment import configure_test_environment


configure_test_environment()

from models.integration import ExternalVersion, IntegrationError  # noqa: E402
from models.mcinstance import MCInstanceBuild, MCInstancePackage  # noqa: E402
from models.platform_export import (  # noqa: E402
    CurseForgeDownloaderAPI,
    CurseForgeFile,
    NativeModrinthFile,
    PlatformExportError,
    PlatformPackExport,
)


SHA1 = "1" * 40
SHA512 = "2" * 128
REPOSITORY = "https://cdn.example.test/mods/"
APPLICATION = "https://solder.example.test/"


class FakeResponse:
    def __init__(self, payload, status_code=200, body=b""):
        self.payload = payload
        self.status_code = status_code
        self.body = body
        self.headers = {"content-length": str(len(body))} if body else {}
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("request failed")

    def json(self):
        return self.payload

    def iter_content(self, chunk_size):
        for start in range(0, len(self.body), chunk_size):
            yield self.body[start : start + chunk_size]

    def close(self):
        self.closed = True


class FakeHTTP:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def build(minecraft="1.7.10", loader="FORGE"):
    return MCInstanceBuild(
        id=7,
        version="2.0",
        minecraft=minecraft,
        forge=f"{minecraft}-10.13.4.1614",
        modpack_id=3,
        modpack_name="Example Pack",
        modpack_slug="example-pack",
        modloader=loader,
        is_published=True,
        private=False,
    )


def package(slug="manual-mod", **changes):
    values = {
        "mod_id": 11,
        "name": slug,
        "pretty_name": slug.replace("-", " ").title(),
        "description": "Example package",
        "version": "1.0",
        "md5": "a" * 32,
        "jarmd5": "b" * 32,
        "side": "BOTH",
        "modtype": "MOD",
        "optional": False,
        "modloader": "FORGE",
        "minecraft": "1.7.10",
        "integration_provider": None,
        "integration_project_id": None,
        "integration_version_id": None,
    }
    values.update(changes)
    return MCInstancePackage(**values)


def modrinth_package():
    return package(
        "native-mod",
        mod_id=12,
        side="CLIENT",
        optional=True,
        integration_provider="MODRINTH",
        integration_project_id="project123",
        integration_version_id="version123",
    )


def external_version():
    return ExternalVersion(
        provider="MODRINTH",
        project_id="project123",
        version_id="version123",
        name="Native Mod",
        version_number="1.0",
        game_versions=("1.7.10",),
        loaders=("FORGE",),
        release_type="release",
        date_published=None,
        filename="native-mod.jar",
        download_url=(
            "https://cdn.modrinth.com/data/project123/versions/"
            "version123/native-mod.jar"
        ),
        hashes={"sha1": SHA1, "sha512": SHA512},
        size=1234,
    )


def override_version():
    return ExternalVersion(
        provider="MODRINTH",
        project_id="eh8us8FY",
        version_id="tx-version",
        name="TX Loader",
        version_number="1.0",
        game_versions=("1.7.10",),
        loaders=("FORGE",),
        release_type="release",
        date_published="2025-01-01T00:00:00Z",
        filename="txloader.jar",
        download_url=(
            "https://cdn.modrinth.com/data/eh8us8FY/versions/"
            "tx-version/txloader.jar"
        ),
        hashes={"sha1": SHA1, "sha512": SHA512},
        size=4321,
    )


def export_override():
    return SimpleNamespace(
        id=1,
        name="TX Loader",
        modrinth_project_id="eh8us8FY",
        curseforge_project_id=706505,
        side="CLIENT",
        enabled=True,
        built_in=True,
    )


def overridden_package():
    return package(
        "native-mod",
        mod_id=12,
        side="CLIENT",
        optional=True,
        integration_provider="MODRINTH",
        integration_project_id="eh8us8FY",
        integration_version_id="tx-version",
    )


def downloader_version(project_id, version_id):
    versions = {
        ("5LpwENAj", "loader-version"): (
            "0.1.0",
            "solderpy-loader-0.1.0.jar",
        ),
        ("zCFNaupz", "relauncher-version"): (
            "1.1.1",
            "relauncher-universal-1.1.1.jar",
        ),
        ("cUtsYbG5", "6Qimuf4A"): ("2.7", "mcinstanceloader-2.7.jar"),
        ("cUtsYbG5", "ett5hobb"): ("2.6", "mcinstanceloader-2.6.jar"),
        ("4dRu1OUz", "V3i1l5tv"): (
            "1.9.1",
            "!mod-director-launchwrapper-1.9.1.jar",
        ),
    }
    version, filename = versions[(project_id, version_id)]
    return ExternalVersion(
        provider="MODRINTH",
        project_id=project_id,
        version_id=version_id,
        name=version,
        version_number=version,
        game_versions=("1.7.10",),
        loaders=("FORGE",),
        release_type="release",
        date_published="2025-01-01T00:00:00Z",
        filename=filename,
        download_url=(
            f"https://cdn.modrinth.com/data/{project_id}/versions/"
            f"{version_id}/{filename}"
        ),
        hashes={"sha1": SHA1, "sha512": SHA512},
        size=1234,
    )


def curseforge_file(project_id, file_id):
    names = {
        (1702825, 7000000): "SolderPy Loader 0.1.0",
        (1491728, 7100000): "Relauncher 1.1.1",
        (576287, 4920730): "1.7.10 - 2.7",
        (576287, 4428492): "1.7.10-2.6",
        (650242, 6436962): "1.9.1",
        (969109, 5071845): "1.0-pre1",
    }
    return CurseForgeFile(
        project_id=project_id,
        file_id=file_id,
        display_name=names[(project_id, file_id)],
        filename=f"downloader-{file_id}.jar",
        game_versions=("1.7.10", "Forge"),
        published="2025-01-01T00:00:00Z",
    )


class PlatformPackExportTests(unittest.TestCase):
    def setUp(self):
        modrinth = patch(
            "models.platform_export.ModrinthProvider.get_version",
            side_effect=lambda project_id, version_id, _minecraft, _loader: (
                downloader_version(project_id, version_id)
            ),
        )
        curseforge = patch(
            "models.platform_export.CurseForgeDownloaderAPI.get_file",
            side_effect=lambda project_id, file_id, _minecraft, _loader=None: (
                curseforge_file(project_id, int(file_id))
            ),
        )
        self.get_downloader_version = modrinth.start()
        self.get_curseforge_file = curseforge.start()
        self.addCleanup(modrinth.stop)
        self.addCleanup(curseforge.stop)

    def test_curseforge_api_lists_compatible_files_with_secret_header(self):
        response = FakeResponse(
            {
                "data": [
                    {
                        "id": 4920730,
                        "modId": 576287,
                        "isAvailable": True,
                        "displayName": "1.7.10 - 2.7",
                        "fileName": "mcinstanceloader-2.7.jar",
                        "fileDate": "2023-12-02T00:00:00Z",
                        "fileLength": 4321,
                        "hashes": [
                            {"algo": 2, "value": "a" * 32},
                            {"algo": 1, "value": SHA1},
                        ],
                        "gameVersions": ["1.7.10", "Forge"],
                    }
                ],
                "pagination": {"resultCount": 1, "totalCount": 1},
            }
        )
        http = FakeHTTP([response])

        files = CurseForgeDownloaderAPI("secret", http=http).list_files(
            576287, "1.7.10"
        )

        self.assertEqual([file.file_id for file in files], [4920730])
        self.assertEqual(files[0].sha1, SHA1)
        self.assertEqual(files[0].size, 4321)
        url, request_data = http.calls[0]
        self.assertEqual(
            url, "https://api.curseforge.com/v1/mods/576287/files"
        )
        self.assertEqual(request_data["headers"]["x-api-key"], "secret")
        self.assertEqual(request_data["params"]["gameVersion"], "1.7.10")
        self.assertFalse(request_data["allow_redirects"])
        self.assertTrue(response.closed)

    def test_curseforge_sync_prefers_an_exact_sha1_match(self):
        hash_match = CurseForgeFile(
            project_id=706505,
            file_id=10,
            display_name="Unrelated display name",
            filename="different.jar",
            game_versions=("1.7.10", "Forge"),
            published="2025-01-01T00:00:00Z",
            sha1=SHA1,
            size=999,
        )
        filename_match = replace(
            hash_match,
            file_id=11,
            filename=override_version().filename,
            sha1=None,
            size=override_version().size,
        )

        matched = PlatformPackExport._matching_curseforge_file(
            export_override(),
            override_version(),
            (filename_match, hash_match),
        )

        self.assertEqual(matched.file_id, 10)

    def test_curseforge_sync_matches_filename_and_filesize(self):
        matching = CurseForgeFile(
            project_id=706505,
            file_id=12,
            display_name="Unrelated display name",
            filename=override_version().filename.upper(),
            game_versions=("1.7.10", "Forge"),
            published="2025-01-01T00:00:00Z",
            size=override_version().size,
        )

        matched = PlatformPackExport._matching_curseforge_file(
            export_override(), override_version(), (matching,)
        )

        self.assertEqual(matched.file_id, 12)

    def test_curseforge_sync_matches_modrinth_version_number(self):
        matching = CurseForgeFile(
            project_id=706505,
            file_id=13,
            display_name="TX Loader v1.0 for legacy Minecraft",
            filename="tx-loader-release.jar",
            game_versions=("1.7.10", "Forge"),
            published="2025-01-01T00:00:00Z",
        )

        matched = PlatformPackExport._matching_curseforge_file(
            export_override(), override_version(), (matching,)
        )

        self.assertEqual(matched.file_id, 13)

    def test_curseforge_sync_does_not_match_a_longer_version_number(self):
        wrong_version = CurseForgeFile(
            project_id=706505,
            file_id=14,
            display_name="TX Loader 1.0.1",
            filename="tx-loader-1.0.1.jar",
            game_versions=("1.7.10", "Forge"),
            published="2025-01-01T00:00:00Z",
        )

        with self.assertRaisesRegex(
            PlatformExportError, "no CurseForge file matching"
        ):
            PlatformPackExport._matching_curseforge_file(
                export_override(), override_version(), (wrong_version,)
            )

    def test_curseforge_api_rejects_files_for_another_modloader(self):
        response = FakeResponse(
            {
                "data": [
                    {
                        "id": 4920730,
                        "modId": 576287,
                        "isAvailable": True,
                        "fileName": "downloader.jar",
                        "gameVersions": ["1.7.10", "Fabric"],
                    }
                ],
                "pagination": {"resultCount": 1, "totalCount": 1},
            }
        )
        files = CurseForgeDownloaderAPI(
            "secret", http=FakeHTTP([response])
        ).list_files(576287, "1.7.10", "FORGE")

        self.assertEqual(files, ())

    def test_curseforge_api_requires_an_installation_key(self):
        with self.assertRaisesRegex(PlatformExportError, "CURSEFORGE_API_KEY"):
            CurseForgeDownloaderAPI(None)

    def test_solderpy_loader_export_contains_only_api_config(self):
        archive = PlatformPackExport.render_solderpy_loader(
            build(), APPLICATION, selector="recommended"
        )

        with archive, zipfile.ZipFile(archive) as result:
            self.assertEqual(
                set(result.namelist()),
                {"config/solderpy-loader.json"},
            )
            config = json.loads(result.read("config/solderpy-loader.json"))
        self.assertEqual(config["api"], "https://solder.example.test/api/")
        self.assertEqual(config["modpack"], "example-pack")
        self.assertEqual(config["build"], "recommended")

    def test_solderpy_loader_export_adds_relauncher_java_policy(self):
        archive = PlatformPackExport.render_solderpy_loader(
            replace(build(), min_java="1.8.0_422"), APPLICATION
        )

        with archive, zipfile.ZipFile(archive) as result:
            self.assertEqual(
                set(result.namelist()),
                {
                    "config/solderpy-loader.json",
                    "config/relauncher/config.cfg",
                },
            )
            relauncher = result.read(
                "config/relauncher/config.cfg"
            ).decode("utf-8")

        self.assertIn("java.versions = 8\n", relauncher)
        self.assertIn("enabled = false\n", relauncher)

    def test_solderpy_loader_rejects_invalid_minimum_java(self):
        with self.assertRaisesRegex(
            PlatformExportError, "Minimum Java Version"
        ):
            PlatformPackExport.render_solderpy_loader(
                replace(build(), min_java="newest"), APPLICATION
            )

    def test_server_export_bundles_loader_relauncher_and_server_launcher(self):
        loader_body = b"verified solderpy loader"
        relauncher_body = b"verified relauncher"
        launcher_body = b"verified crucible server"

        def native(project_id, version_id, filename, body):
            return NativeModrinthFile(
                project_id=project_id,
                version_id=version_id,
                filename=filename,
                download_url=(
                    f"https://cdn.modrinth.com/data/{project_id}/versions/"
                    f"{version_id}/{filename}"
                ),
                sha1=hashlib.sha1(body, usedforsecurity=False).hexdigest(),
                sha512=hashlib.sha512(body).hexdigest(),
                size=len(body),
            )

        selected = SimpleNamespace(
            key="solderpyloader",
            modrinth=native(
                "5LpwENAj",
                "loader-version",
                "solderpy-loader.jar",
                loader_body,
            ),
            modrinth_dependencies=(
                native(
                    "zCFNaupz",
                    "relauncher-version",
                    "relauncher.jar",
                    relauncher_body,
                ),
            ),
        )
        launcher = package(
            "crucible",
            modtype="LAUNCHER",
            side="SERVER",
            version="1.7.10-5.4",
            jarmd5=hashlib.md5(
                launcher_body, usedforsecurity=False
            ).hexdigest(),
        )

        with tempfile.TemporaryDirectory() as repository:
            launcher_path = Path(repository, "crucible")
            launcher_path.mkdir()
            Path(launcher_path, launcher.jar_filename).write_bytes(
                launcher_body
            )
            with patch.object(
                PlatformPackExport,
                "resolve_downloader",
                return_value=selected,
            ):
                archive = PlatformPackExport.render_server(
                    replace(build(), min_java="1.8.0_422"),
                    [package(), launcher],
                    "solderpyloader:loader-version",
                    REPOSITORY,
                    repository,
                    APPLICATION,
                    selector="recommended",
                    http=FakeHTTP(
                        [
                            FakeResponse({}, body=loader_body),
                            FakeResponse({}, body=relauncher_body),
                        ]
                    ),
                )

            with archive, zipfile.ZipFile(archive) as result:
                self.assertEqual(
                    set(result.namelist()),
                    {
                        "mods/!solderpy-loader.jar",
                        "mods/!relauncher.jar",
                        "config/solderpy-loader.json",
                        "config/relauncher/config.cfg",
                        launcher.jar_filename,
                    },
                )
                config = json.loads(
                    result.read("config/solderpy-loader.json")
                )
                self.assertEqual(
                    result.read("mods/!solderpy-loader.jar"), loader_body
                )
                self.assertEqual(
                    result.read("mods/!relauncher.jar"), relauncher_body
                )
                self.assertEqual(
                    result.read(launcher.jar_filename), launcher_body
                )

        self.assertEqual(config["target"], "server")
        self.assertEqual(config["build"], "recommended")

    def test_server_export_requires_one_server_side_launcher(self):
        with self.assertRaisesRegex(
            PlatformExportError, "server-side LAUNCHER"
        ):
            PlatformPackExport.render_server(
                build(),
                [package(modtype="LAUNCHER", side="BOTH")],
                "solderpyloader:loader-version",
                REPOSITORY,
                "./mods/",
                APPLICATION,
            )

    def test_server_export_requires_a_verified_launcher_jar(self):
        with self.assertRaisesRegex(PlatformExportError, "verified raw JAR"):
            PlatformPackExport.render_server(
                build(),
                [
                    package(
                        "crucible",
                        modtype="LAUNCHER",
                        side="SERVER",
                        jarmd5="0",
                    )
                ],
                "solderpyloader:loader-version",
                REPOSITORY,
                "./mods/",
                APPLICATION,
            )

    def test_mrpack_routes_native_modrinth_and_solder_fallback_separately(self):
        with patch(
            "models.platform_export.ModrinthProvider.get_versions",
            return_value={"version123": external_version()},
        ):
            archive = PlatformPackExport.render_mrpack(
                build(),
                [modrinth_package(), package()],
                "filedirector:V3i1l5tv",
                REPOSITORY,
                "./mods/",
                APPLICATION,
            )

        with archive, zipfile.ZipFile(archive) as result:
            index = json.loads(result.read("modrinth.index.json"))
            remote = json.loads(
                result.read(
                    "overrides/config/mod-director/solder.remote.json"
                )
            )

        self.assertEqual(index["dependencies"]["minecraft"], "1.7.10")
        self.assertEqual(index["dependencies"]["forge"], "10.13.4.1614")
        paths = {entry["path"] for entry in index["files"]}
        self.assertIn("mods/native-mod.jar", paths)
        self.assertIn(
            "mods/!mod-director-launchwrapper-1.9.1.jar", paths
        )
        self.assertFalse(any("manual-mod" in path for path in paths))
        native = next(
            entry for entry in index["files"]
            if entry["path"] == "mods/native-mod.jar"
        )
        self.assertEqual(
            native["env"],
            {"client": "optional", "server": "unsupported"},
        )
        self.assertEqual(
            remote["url"],
            "https://solder.example.test/filedirector/example-pack/2.0/"
            "modrinth-fallback.bundle.json",
        )
        self.assertNotIn(REPOSITORY, json.dumps(index))
        self.get_downloader_version.assert_called_with(
            "4dRu1OUz", "V3i1l5tv", "1.7.10", "FORGE"
        )

    def test_mrpack_can_bootstrap_solderpy_loader_from_api(self):
        with patch(
            "models.platform_export.ModrinthProvider.list_versions",
            return_value=[
                downloader_version("zCFNaupz", "relauncher-version")
            ],
        ):
            archive = PlatformPackExport.render_mrpack(
                replace(build(), min_java="1.8.0_422"),
                [package()],
                "solderpyloader:loader-version",
                REPOSITORY,
                "./mods/",
                APPLICATION,
                source_mode="solder",
                selector="recommended",
            )

        with archive, zipfile.ZipFile(archive) as result:
            index = json.loads(result.read("modrinth.index.json"))
            config = json.loads(
                result.read("overrides/config/solderpy-loader.json")
            )
            relauncher = result.read(
                "overrides/config/relauncher/config.cfg"
            ).decode("utf-8")

        self.assertEqual(
            [entry["path"] for entry in index["files"]],
            [
                "mods/solderpy-loader-0.1.0.jar",
                "mods/relauncher-universal-1.1.1.jar",
            ],
        )
        self.assertEqual(
            config,
            {
                "enabled": True,
                "api": "https://solder.example.test/api/",
                "modpack": "example-pack",
                "build": "recommended",
                "target": "auto",
                "source": "solder",
                "platform": "modrinth",
                "launcherOwnedMemberships": [],
            },
        )
        self.assertIn("java.versions = 8\n", relauncher)

    def test_solderpy_loader_supports_hybrid_source(self):
        with patch(
            "models.platform_export.ModrinthProvider.list_versions",
            return_value=[
                downloader_version("zCFNaupz", "relauncher-version")
            ],
        ):
            archive = PlatformPackExport.render_mrpack(
                build(),
                [package()],
                "solderpyloader:loader-version",
                REPOSITORY,
                "./mods/",
                APPLICATION,
                source_mode="hybrid",
            )

        with archive, zipfile.ZipFile(archive) as result:
            config = json.loads(
                result.read("overrides/config/solderpy-loader.json")
            )
        self.assertEqual(config["source"], "hybrid")
        self.assertEqual(config["platform"], "modrinth")
        self.assertEqual(config["launcherOwnedMemberships"], [])

    def test_solderpy_loader_config_records_only_files_native_export_owns(self):
        selected = replace(modrinth_package(), membership_id=44)
        with (
            patch(
                "models.platform_export.ModrinthProvider.list_versions",
                return_value=[
                    downloader_version("zCFNaupz", "relauncher-version")
                ],
            ),
            patch(
                "models.platform_export.ModrinthProvider.get_versions",
                return_value={"version123": external_version()},
            ),
        ):
            archive = PlatformPackExport.render_mrpack(
                build(),
                [selected, package()],
                "solderpyloader:loader-version",
                REPOSITORY,
                "./mods/",
                APPLICATION,
                source_mode="hybrid",
            )

        with archive, zipfile.ZipFile(archive) as result:
            config = json.loads(
                result.read("overrides/config/solderpy-loader.json")
            )

        self.assertEqual(config["launcherOwnedMemberships"], [44])

    def test_solderpy_loader_owns_native_mapping_when_export_falls_back(self):
        selected = replace(modrinth_package(), membership_id=44)
        with (
            patch(
                "models.platform_export.ModrinthProvider.list_versions",
                return_value=[
                    downloader_version("zCFNaupz", "relauncher-version")
                ],
            ),
            patch(
                "models.platform_export.ModrinthProvider.get_versions",
                return_value={},
            ),
        ):
            archive = PlatformPackExport.render_mrpack(
                build(),
                [selected],
                "solderpyloader:loader-version",
                REPOSITORY,
                "./mods/",
                APPLICATION,
                source_mode="hybrid",
            )

        with archive, zipfile.ZipFile(archive) as result:
            config = json.loads(
                result.read("overrides/config/solderpy-loader.json")
            )

        self.assertEqual(config["launcherOwnedMemberships"], [])

    def test_curseforge_manifest_contains_only_the_downloader(self):
        archive = PlatformPackExport.render_curseforge(
            build(),
            [modrinth_package(), package()],
            "filedirector:6436962",
            REPOSITORY,
            "./mods/",
            APPLICATION,
            curseforge_api_key="test-key",
            source_mode="solder",
        )

        with archive, zipfile.ZipFile(archive) as result:
            manifest = json.loads(result.read("manifest.json"))
            remote = json.loads(
                result.read(
                    "overrides/config/mod-director/solder.remote.json"
                )
            )

        self.assertEqual(
            manifest["files"],
            [{"projectID": 650242, "fileID": 6436962, "required": True}],
        )
        self.assertEqual(
            manifest["minecraft"]["modLoaders"],
            [{"id": "forge-10.13.4.1614", "primary": True}],
        )
        self.assertEqual(
            remote["url"],
            "https://solder.example.test/filedirector/example-pack/2.0/"
            "mods.bundle.json",
        )

    def test_curseforge_solderpy_loader_includes_relauncher(self):
        with patch(
            "models.platform_export.CurseForgeDownloaderAPI.list_files",
            return_value=(curseforge_file(1491728, 7100000),),
        ):
            archive = PlatformPackExport.render_curseforge(
                build(),
                [package()],
                "solderpyloader:7000000",
                REPOSITORY,
                "./mods/",
                APPLICATION,
                curseforge_api_key="test-key",
                source_mode="solder",
                selector="latest",
            )

        with archive, zipfile.ZipFile(archive) as result:
            manifest = json.loads(result.read("manifest.json"))
            config = json.loads(
                result.read("overrides/config/solderpy-loader.json")
            )

        self.assertEqual(
            manifest["files"],
            [
                {"projectID": 1702825, "fileID": 7000000, "required": True},
                {"projectID": 1491728, "fileID": 7100000, "required": True},
            ],
        )
        self.assertEqual(config["build"], "latest")

    def test_curseforge_can_bootstrap_official_modpack_director(self):
        archive = PlatformPackExport.render_curseforge(
            build(),
            [package()],
            "modpackdirector:5071845",
            REPOSITORY,
            "./mods/",
            APPLICATION,
            curseforge_api_key="test-key",
            source_mode="solder",
            delivery="hosted",
            selector="latest",
        )

        with archive, zipfile.ZipFile(archive) as result:
            manifest = json.loads(result.read("manifest.json"))
            metadata = json.loads(
                result.read("overrides/config/mod-director/modpack.json")
            )
            remote = json.loads(
                result.read(
                    "overrides/config/mod-director/solder.remote.json"
                )
            )

        self.assertEqual(
            manifest["files"],
            [{"projectID": 969109, "fileID": 5071845, "required": True}],
        )
        self.assertEqual(metadata["packName"], "Example Pack")
        self.assertEqual(metadata["localVersion"], "2.0")
        self.assertEqual(
            metadata["remoteVersion"],
            "https://solder.example.test/modpackdirector/example-pack/"
            "latest/version.txt",
        )
        self.assertEqual(
            remote["url"],
            "https://solder.example.test/modpackdirector/example-pack/latest/"
            "mods.bundle.json",
        )

    def test_mrpack_always_installs_enabled_override_natively(self):
        with patch(
            "models.platform_export.ModrinthProvider.get_versions",
            return_value={"tx-version": override_version()},
        ) as versions:
            archive = PlatformPackExport.render_mrpack(
                build(),
                [overridden_package()],
                "none",
                REPOSITORY,
                "./mods/",
                APPLICATION,
                source_mode="solder",
                export_overrides=[export_override()],
            )

        with archive, zipfile.ZipFile(archive) as result:
            index = json.loads(result.read("modrinth.index.json"))

        self.assertEqual(
            [entry["path"] for entry in index["files"]],
            ["mods/txloader.jar"],
        )
        self.assertEqual(
            index["files"][0]["env"],
            {"client": "required", "server": "unsupported"},
        )
        versions.assert_called_once_with(
            [("eh8us8FY", "tx-version")], "1.7.10", "FORGE"
        )

    def test_curseforge_always_installs_enabled_override_natively(self):
        tx_file = CurseForgeFile(
            project_id=706505,
            file_id=7000001,
            display_name="TX Loader 1.0",
            filename="txloader.jar",
            game_versions=("1.7.10", "Forge"),
            published="2025-01-01T00:00:00Z",
        )
        with (
            patch(
                "models.platform_export.ModrinthProvider.get_versions",
                return_value={"tx-version": override_version()},
            ),
            patch(
                "models.platform_export.CurseForgeDownloaderAPI.list_files",
                return_value=(tx_file,),
            ) as files,
        ):
            archive = PlatformPackExport.render_curseforge(
                build(),
                [overridden_package()],
                "filedirector:6436962",
                REPOSITORY,
                "./mods/",
                APPLICATION,
                curseforge_api_key="test-key",
                source_mode="solder",
                delivery="bundled",
                export_overrides=[export_override()],
            )

        with archive, zipfile.ZipFile(archive) as result:
            manifest = json.loads(result.read("manifest.json"))
            bundle = json.loads(
                result.read(
                    "overrides/config/mod-director/solder.bundle.json"
                )
            )

        self.assertEqual(
            manifest["files"],
            [
                {"projectID": 650242, "fileID": 6436962, "required": True},
                {"projectID": 706505, "fileID": 7000001, "required": True},
            ],
        )
        self.assertEqual(bundle["url"], [])
        files.assert_called_once_with(706505, "1.7.10", "FORGE")

    def test_curseforge_hybrid_bundle_uses_modrinth_and_solder_urls(self):
        with patch(
            "models.platform_export.ModrinthProvider.get_versions",
            return_value={"version123": external_version()},
        ):
            archive = PlatformPackExport.render_curseforge(
                build(),
                [modrinth_package(), package()],
                "filedirector:6436962",
                REPOSITORY,
                "./mods/",
                APPLICATION,
                curseforge_api_key="test-key",
                source_mode="hybrid",
                delivery="bundled",
            )

        with archive, zipfile.ZipFile(archive) as result:
            bundle = json.loads(
                result.read(
                    "overrides/config/mod-director/solder.bundle.json"
                )
            )

        urls = [entry["url"] for entry in bundle["url"]]
        self.assertIn(external_version().download_url, urls)
        self.assertTrue(any(url.startswith(REPOSITORY) for url in urls))

    def test_common_package_plan_filters_downloaders_and_native_files(self):
        packages = [
            modrinth_package(),
            package(),
            package("legacy-mcil", modtype="MCIL"),
            package("legacy-launcher", modtype="LAUNCHER"),
        ]
        with patch(
            "models.platform_export.ModrinthProvider.get_versions",
            return_value={"version123": external_version()},
        ):
            mrpack = PlatformPackExport._package_plan(
                build(),
                packages,
                "hybrid",
                launcher_handles_modrinth=True,
            )
            curseforge = PlatformPackExport._package_plan(
                build(),
                packages,
                "hybrid",
            )

        self.assertEqual(
            [item.name for item in mrpack.packages],
            ["native-mod", "manual-mod"],
        )
        self.assertEqual(
            [item.name for item in mrpack.downloader_packages],
            ["manual-mod"],
        )
        self.assertEqual(
            [entry["path"] for entry in mrpack.native_entries],
            ["mods/native-mod.jar"],
        )
        self.assertEqual(set(mrpack.native_files), {"version123"})
        self.assertEqual(
            [item.name for item in curseforge.downloader_packages],
            ["native-mod", "manual-mod"],
        )

    def test_basic_excluded_modrinth_choice_stays_with_downloader(self):
        selected = replace(
            modrinth_package(), optional_state=2, membership_id=44
        )
        with patch(
            "models.platform_export.ModrinthProvider.get_versions",
            return_value={"version123": external_version()},
        ):
            plan = PlatformPackExport._package_plan(
                build(),
                [selected],
                "hybrid",
                launcher_handles_modrinth=True,
            )

        self.assertEqual(plan.native_entries, ())
        self.assertEqual(plan.downloader_packages, (selected,))
        self.assertEqual(set(plan.native_files), {"version123"})

    def test_advanced_group_choice_stays_with_downloader_in_hybrid_pack(self):
        selected = replace(
            modrinth_package(), optional_state=2, membership_id=44
        )
        group = SimpleNamespace(
            items=(SimpleNamespace(build_modversion_id=44),)
        )

        with patch.object(
            PlatformPackExport, "native_modrinth_files"
        ) as native_files:
            plan = PlatformPackExport._package_plan(
                build(),
                [selected],
                "hybrid",
                launcher_handles_modrinth=True,
                optional_groups=(group,),
            )

        native_files.assert_not_called()
        self.assertEqual(plan.native_entries, ())
        self.assertEqual(plan.native_files, {})
        self.assertEqual(plan.downloader_packages, (selected,))

    def test_packwiz_omits_basic_excluded_choices(self):
        with patch.object(
            PlatformPackExport, "native_modrinth_files"
        ) as native_files:
            archive = PlatformPackExport.render_packwiz(
                build(),
                [
                    replace(
                        modrinth_package(),
                        optional_state=2,
                        membership_id=44,
                    )
                ],
                REPOSITORY,
                source_mode="hybrid",
            )
        with archive, zipfile.ZipFile(archive) as result:
            self.assertEqual(
                sorted(result.namelist()), ["index.toml", "pack.toml"]
            )
        native_files.assert_not_called()

    def test_solder_only_override_checkbox_controls_native_override(self):
        disabled_for_solder = SimpleNamespace(override_solder_only=False)
        enabled_for_solder = SimpleNamespace(override_solder_only=True)

        self.assertEqual(
            PlatformPackExport._active_export_overrides(
                [disabled_for_solder, enabled_for_solder], "solder"
            ),
            (enabled_for_solder,),
        )
        self.assertEqual(
            PlatformPackExport._active_export_overrides(
                [disabled_for_solder], "hybrid"
            ),
            (disabled_for_solder,),
        )

    def test_mrpack_solder_only_routes_every_mod_through_bundled_config(self):
        archive = PlatformPackExport.render_mrpack(
            build(),
            [modrinth_package(), package()],
            "filedirector:V3i1l5tv",
            REPOSITORY,
            "./mods/",
            APPLICATION,
            source_mode="solder",
            delivery="bundled",
        )

        with archive, zipfile.ZipFile(archive) as result:
            index = json.loads(result.read("modrinth.index.json"))
            bundle = json.loads(
                result.read(
                    "overrides/config/mod-director/solder.bundle.json"
                )
            )

        self.assertEqual(
            [entry["path"] for entry in index["files"]],
            ["mods/!mod-director-launchwrapper-1.9.1.jar"],
        )
        urls = [entry["url"] for entry in bundle["url"]]
        self.assertEqual(len(urls), 2)
        self.assertTrue(all(url.startswith(REPOSITORY) for url in urls))

    def test_hybrid_hosted_filedirector_can_follow_latest(self):
        with patch(
            "models.platform_export.ModrinthProvider.get_versions",
            return_value={"version123": external_version()},
        ):
            archive = PlatformPackExport.render_curseforge(
                build(),
                [modrinth_package()],
                "filedirector:6436962",
                REPOSITORY,
                "./mods/",
                APPLICATION,
                curseforge_api_key="test-key",
                source_mode="hybrid",
                delivery="hosted",
                selector="latest",
            )

        with archive, zipfile.ZipFile(archive) as result:
            remote = json.loads(
                result.read(
                    "overrides/config/mod-director/solder.remote.json"
                )
            )
        self.assertEqual(
            remote["url"],
            "https://solder.example.test/filedirector/example-pack/latest/"
            "mods.bundle.json?source=hybrid",
        )

    def test_mcil_is_bootstrapped_without_becoming_a_solder_package(self):
        nested = io.BytesIO(b"mcinstance archive")
        with patch(
            "models.platform_export.MCInstanceExport.render",
            return_value=nested,
        ) as render:
            archive = PlatformPackExport.render_curseforge(
                build(),
                [package(), package("old-loader", modtype="LAUNCHER")],
                "mcil:4920730",
                REPOSITORY,
                "./mods/",
                APPLICATION,
                curseforge_api_key="test-key",
            )

        with archive, zipfile.ZipFile(archive) as result:
            manifest = json.loads(result.read("manifest.json"))
            embedded = result.read(
                "overrides/config/mcinstanceloader/pack.mcinstance"
            )

        self.assertEqual(embedded, b"mcinstance archive")
        self.assertEqual(
            manifest["files"],
            [{"projectID": 576287, "fileID": 4920730, "required": True}],
        )
        rendered_packages = render.call_args.args[1]
        self.assertEqual([item.name for item in rendered_packages], ["manual-mod"])
        self.assertFalse(render.call_args.kwargs["include_modloader"])

    def test_mcil_always_bundles_config_with_shared_delivery_setting(self):
        archive = PlatformPackExport.render_curseforge(
            build(),
            [package()],
            "mcil:4920730",
            REPOSITORY,
            "./mods/",
            APPLICATION,
            curseforge_api_key="test-key",
            delivery="hosted",
        )

        with archive, zipfile.ZipFile(archive) as result:
            self.assertIn(
                "overrides/config/mcinstanceloader/pack.mcinstance",
                result.namelist(),
            )

    def test_config_delivery_uses_downloader_capabilities(self):
        mcil = PlatformPackExport.resolve_downloader(
            "mcil:4920730", build(), "curseforge", curseforge_api_key="test-key"
        )
        filedirector = PlatformPackExport.resolve_downloader(
            "filedirector:6436962",
            build(),
            "curseforge",
            curseforge_api_key="test-key",
        )

        self.assertEqual(PlatformPackExport.config_delivery(mcil, None), "bundled")
        self.assertEqual(
            PlatformPackExport.config_delivery(filedirector, None), "hosted"
        )
        self.assertEqual(
            PlatformPackExport.config_delivery(mcil, "hosted"), "bundled"
        )

    def test_standalone_config_archives_contain_expected_files(self):
        packwiz = PlatformPackExport.render_packwiz(
            build(), [package()], REPOSITORY
        )
        filedirector = PlatformPackExport.render_filedirector(
            build(), [package()], REPOSITORY
        )
        modpack_director = PlatformPackExport.render_modpack_director(
            build(), [package()], REPOSITORY, APPLICATION
        )

        with packwiz, zipfile.ZipFile(packwiz) as result:
            self.assertEqual(
                set(result.namelist()),
                {"pack.toml", "index.toml", "mods/manual-mod.pw.toml"},
            )
        with filedirector, zipfile.ZipFile(filedirector) as result:
            self.assertEqual(
                result.namelist(),
                ["config/mod-director/solder.bundle.json"],
            )
        with modpack_director, zipfile.ZipFile(modpack_director) as result:
            self.assertEqual(
                set(result.namelist()),
                {
                    "config/mod-director/modpack.json",
                    "config/mod-director/solder.bundle.json",
                },
            )
            metadata = json.loads(
                result.read("config/mod-director/modpack.json")
            )
            self.assertEqual(metadata["packName"], "Example Pack")
            self.assertEqual(metadata["localVersion"], "2.0")

    def test_hosted_modpack_director_requires_a_public_build(self):
        private_build = replace(build(), private=True)
        with self.assertRaisesRegex(
            PlatformExportError, "published, non-private"
        ):
            PlatformPackExport.render_modpack_director(
                private_build,
                [package()],
                REPOSITORY,
                APPLICATION,
                delivery="hosted",
            )

    def test_prism_export_is_a_self_contained_instance_using_optional_defaults(self):
        packages = [
            package("required", membership_id=1),
            package(
                "selected-choice",
                membership_id=2,
                optional_state=2,
            ),
            package("unselected-choice", membership_id=3),
            package("basic-optional", optional=True, optional_state=1),
            package("server-only", side="SERVER"),
            package("legacy-loader", modtype="LAUNCHER"),
        ]
        group = SimpleNamespace(
            items=(
                SimpleNamespace(
                    build_modversion_id=2, selected_by_default=True
                ),
                SimpleNamespace(
                    build_modversion_id=3, selected_by_default=False
                ),
            )
        )

        with tempfile.TemporaryDirectory() as repository:
            rendered = []
            for item in packages:
                folder = Path(repository, item.name)
                folder.mkdir()
                path = folder / item.zip_filename
                with zipfile.ZipFile(path, "w") as package_zip:
                    package_zip.writestr(
                        f"config/{item.name}.cfg", item.name.encode("utf-8")
                    )
                digest = hashlib.md5(
                    path.read_bytes(), usedforsecurity=False
                ).hexdigest()
                rendered.append(replace(item, md5=digest))

            archive = PlatformPackExport.render_prism(
                build(),
                rendered,
                REPOSITORY,
                repository,
                optional_groups=(group,),
            )

            with archive, zipfile.ZipFile(archive) as result:
                manifest = json.loads(result.read("mmc-pack.json"))
                config = result.read("instance.cfg").decode("utf-8")
                names = set(result.namelist())

        self.assertEqual(
            manifest,
            {
                "formatVersion": 1,
                "components": [
                    {
                        "uid": "net.minecraft",
                        "version": "1.7.10",
                        "important": True,
                    },
                    {
                        "uid": "net.minecraftforge",
                        "version": "10.13.4.1614",
                    },
                ],
            },
        )
        self.assertIn("InstanceType=OneSix", config)
        self.assertIn("name=Example Pack", config)
        self.assertIn(".minecraft/config/required.cfg", names)
        self.assertIn(".minecraft/config/selected-choice.cfg", names)
        self.assertNotIn(".minecraft/config/unselected-choice.cfg", names)
        self.assertNotIn(".minecraft/config/basic-optional.cfg", names)
        self.assertNotIn(".minecraft/config/server-only.cfg", names)
        self.assertNotIn(".minecraft/config/legacy-loader.cfg", names)

    def test_prism_component_ids_match_each_supported_loader(self):
        cases = (
            (
                "FORGE",
                "1.7.10-10.13.4.1614",
                "net.minecraftforge",
                "10.13.4.1614",
            ),
            (
                "NEOFORGE",
                "1.21.1-21.1.1",
                "net.neoforged",
                "21.1.1",
            ),
            (
                "FABRIC",
                "1.21.1-0.16.10",
                "net.fabricmc.fabric-loader",
                "0.16.10",
            ),
            (
                "QUILT",
                "1.21.1-0.27.1",
                "org.quiltmc.quilt-loader",
                "0.27.1",
            ),
            (
                "LITELOADER",
                "1.7.10_04",
                "com.mumfrey.liteloader",
                "1.7.10_04",
            ),
        )
        for loader, version, uid, expected_version in cases:
            with self.subTest(loader=loader):
                selected = replace(
                    build(
                        minecraft=(
                            "1.7.10"
                            if loader in {"FORGE", "LITELOADER"}
                            else "1.21.1"
                        )
                    ),
                    modloader=loader,
                    forge=version,
                )
                components = PlatformPackExport._prism_components(selected)
                self.assertEqual(components[1]["uid"], uid)
                self.assertEqual(components[1]["version"], expected_version)

    def test_prism_filedirector_export_contains_only_bootstrap_and_config(self):
        def write_downloader(target, _file, destination, **_kwargs):
            target.writestr(destination, b"filedirector jar")

        with patch.object(
            PlatformPackExport,
            "_write_native_file",
            side_effect=write_downloader,
        ):
            archive = PlatformPackExport.render_prism(
                build(),
                [package()],
                REPOSITORY,
                "./mods/",
                downloader="filedirector:V3i1l5tv",
                application_url=APPLICATION,
                source_mode="solder",
                delivery="bundled",
            )

        with archive, zipfile.ZipFile(archive) as result:
            names = set(result.namelist())
            bundle = json.loads(
                result.read(
                    ".minecraft/config/mod-director/solder.bundle.json"
                )
            )

        self.assertIn(
            ".minecraft/mods/!mod-director-launchwrapper-1.9.1.jar",
            names,
        )
        self.assertNotIn(".minecraft/config/manual-mod.cfg", names)
        self.assertTrue(bundle["url"][0]["url"].startswith(REPOSITORY))

    def test_prism_solderpy_loader_bundles_relauncher(self):
        def write_downloader(target, _file, destination, **_kwargs):
            target.writestr(destination, b"bootstrap jar")

        with (
            patch(
                "models.platform_export.ModrinthProvider.list_versions",
                return_value=[
                    downloader_version("zCFNaupz", "relauncher-version")
                ],
            ),
            patch.object(
                PlatformPackExport,
                "_write_native_file",
                side_effect=write_downloader,
            ),
        ):
            archive = PlatformPackExport.render_prism(
                build(),
                [package()],
                REPOSITORY,
                "./mods/",
                downloader="solderpyloader:loader-version",
                application_url=APPLICATION,
                source_mode="solder",
            )

        with archive, zipfile.ZipFile(archive) as result:
            names = set(result.namelist())

        self.assertIn(
            ".minecraft/mods/solderpy-loader-0.1.0.jar", names
        )
        self.assertIn(
            ".minecraft/mods/relauncher-universal-1.1.1.jar", names
        )
        self.assertIn(
            ".minecraft/config/solderpy-loader.json", names
        )

    def test_prism_mcil_export_contains_bootstrap_and_nested_config(self):
        def write_downloader(target, _file, destination, **_kwargs):
            target.writestr(destination, b"mcil jar")

        with (
            patch.object(
                PlatformPackExport,
                "_write_native_file",
                side_effect=write_downloader,
            ),
            patch(
                "models.platform_export.MCInstanceExport.render",
                return_value=io.BytesIO(b"mcinstance config"),
            ) as render,
        ):
            archive = PlatformPackExport.render_prism(
                build(),
                [package()],
                REPOSITORY,
                "./mods/",
                downloader="mcil:6Qimuf4A",
                application_url=APPLICATION,
                source_mode="solder",
                delivery="hosted",
            )

        with archive, zipfile.ZipFile(archive) as result:
            names = set(result.namelist())
            config = result.read(
                ".minecraft/config/mcinstanceloader/pack.mcinstance"
            )

        self.assertIn(
            ".minecraft/mods/mcinstanceloader-2.7.jar", names
        )
        self.assertEqual(config, b"mcinstance config")
        self.assertFalse(render.call_args.kwargs["include_modloader"])

    def test_prism_filedirector_can_use_a_hosted_channel(self):
        def write_downloader(target, _file, destination, **_kwargs):
            target.writestr(destination, b"filedirector jar")

        with patch.object(
            PlatformPackExport,
            "_write_native_file",
            side_effect=write_downloader,
        ):
            archive = PlatformPackExport.render_prism(
                build(),
                [package()],
                REPOSITORY,
                "./mods/",
                downloader="filedirector:V3i1l5tv",
                application_url=APPLICATION,
                source_mode="hybrid",
                delivery="hosted",
                selector="latest",
            )

        with archive, zipfile.ZipFile(archive) as result:
            remote = json.loads(
                result.read(
                    ".minecraft/config/mod-director/solder.remote.json"
                )
            )
        self.assertEqual(
            remote["url"],
            "https://solder.example.test/filedirector/example-pack/latest/"
            "mods.bundle.json?source=hybrid",
        )

    def test_prism_downloader_download_is_verified_before_export(self):
        body = b"verified downloader jar"
        native_file = NativeModrinthFile(
            project_id="project",
            version_id="version",
            filename="downloader.jar",
            download_url=(
                "https://cdn.modrinth.com/data/project/versions/"
                "version/downloader.jar"
            ),
            sha1=hashlib.sha1(body, usedforsecurity=False).hexdigest(),
            sha512=hashlib.sha512(body).hexdigest(),
            size=len(body),
        )
        response = FakeResponse(None, body=body)
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as target:
            PlatformPackExport._write_native_file(
                target,
                native_file,
                ".minecraft/mods/downloader.jar",
                http=FakeHTTP([response]),
            )

        with zipfile.ZipFile(output) as result:
            self.assertEqual(
                result.read(".minecraft/mods/downloader.jar"), body
            )
        self.assertTrue(response.closed)

    def test_mrpack_requires_a_downloader_for_non_modrinth_files(self):
        with self.assertRaisesRegex(PlatformExportError, "Select a compatible"):
            PlatformPackExport.render_mrpack(
                build(),
                [package()],
                "none",
                REPOSITORY,
                "./mods/",
                APPLICATION,
            )

    def test_modrinth_downloader_versions_are_loaded_from_the_api(self):
        def versions(project_id, _minecraft, _loader):
            if project_id == "5LpwENAj":
                return [downloader_version(project_id, "loader-version")]
            if project_id == "zCFNaupz":
                return [
                    downloader_version(project_id, "relauncher-version")
                ]
            if project_id == "cUtsYbG5":
                return [
                    downloader_version(project_id, "6Qimuf4A"),
                    downloader_version(project_id, "ett5hobb"),
                ]
            return [downloader_version(project_id, "V3i1l5tv")]

        with patch(
            "models.platform_export.ModrinthProvider.list_versions",
            side_effect=versions,
        ) as list_versions:
            available = PlatformPackExport.available_downloaders(
                build(), "modrinth"
            )

        self.assertEqual(
            [
                (downloader.key, release.selector, release.version)
                for downloader in available
                for release in downloader.releases
            ],
            [
                ("solderpyloader", "loader-version", "0.1.0"),
                ("mcil", "6Qimuf4A", "2.7"),
                ("mcil", "ett5hobb", "2.6"),
                ("filedirector", "V3i1l5tv", "1.9.1"),
            ],
        )
        self.assertEqual(list_versions.call_count, 4)

    def test_unavailable_modrinth_downloader_does_not_hide_other_families(self):
        solderpy_loader = PlatformPackExport.downloader_spec("solderpyloader")
        mcil = PlatformPackExport.downloader_spec("mcil")

        def versions(project_id, _minecraft, _loader):
            if project_id == "5LpwENAj":
                raise IntegrationError("project is not public")
            return [downloader_version(project_id, "6Qimuf4A")]

        with patch(
            "models.platform_export.ModrinthProvider.list_versions",
            side_effect=versions,
        ):
            available = PlatformPackExport.available_downloaders(
                build(),
                "modrinth",
                specs=(solderpy_loader, mcil),
            )

        self.assertEqual([downloader.key for downloader in available], ["mcil"])

    def test_single_unavailable_modrinth_downloader_still_reports_error(self):
        solderpy_loader = PlatformPackExport.downloader_spec("solderpyloader")

        with (
            patch(
                "models.platform_export.ModrinthProvider.list_versions",
                side_effect=IntegrationError("project is not public"),
            ),
            self.assertRaisesRegex(
                PlatformExportError,
                "SolderPy Loader downloader versions could not be loaded",
            ),
        ):
            PlatformPackExport.available_downloaders(
                build(), "modrinth", specs=(solderpy_loader,)
            )

    def test_curseforge_downloader_versions_are_loaded_from_the_api(self):
        def files(project_id, _minecraft, _loader):
            if project_id == 1702825:
                return (curseforge_file(project_id, 7000000),)
            if project_id == 1491728:
                return (curseforge_file(project_id, 7100000),)
            if project_id == 576287:
                return (
                    curseforge_file(project_id, 4920730),
                    curseforge_file(project_id, 4428492),
                )
            if project_id == 650242:
                return (curseforge_file(project_id, 6436962),)
            return (curseforge_file(project_id, 5071845),)

        with patch(
            "models.platform_export.CurseForgeDownloaderAPI.list_files",
            side_effect=files,
        ) as list_files:
            available = PlatformPackExport.available_downloaders(
                build(), "curseforge", curseforge_api_key="test-key"
            )

        self.assertEqual(
            [
                (downloader.key, release.selector)
                for downloader in available
                for release in downloader.releases
            ],
            [
                ("solderpyloader", "7000000"),
                ("mcil", "4920730"),
                ("mcil", "4428492"),
                ("filedirector", "6436962"),
                ("modpackdirector", "5071845"),
            ],
        )
        self.assertEqual(list_files.call_count, 5)

    def test_selected_downloader_version_controls_curseforge_file(self):
        with patch(
            "models.platform_export.MCInstanceExport.render",
            return_value=io.BytesIO(b"mcinstance archive"),
        ):
            archive = PlatformPackExport.render_curseforge(
                build(),
                [package()],
                "mcil:4428492",
                REPOSITORY,
                "./mods/",
                APPLICATION,
                curseforge_api_key="test-key",
                source_mode="solder",
            )

        with archive, zipfile.ZipFile(archive) as result:
            manifest = json.loads(result.read("manifest.json"))
        self.assertEqual(
            manifest["files"],
            [{"projectID": 576287, "fileID": 4428492, "required": True}],
        )
        self.get_curseforge_file.assert_called_with(
            576287, "4428492", "1.7.10", "FORGE"
        )

    def test_modloader_override_changes_only_export_copy(self):
        original = build()
        overridden = PlatformPackExport.override_modloader_version(
            original, "1.7.10-10.13.4.1558"
        )

        self.assertEqual(original.forge, "1.7.10-10.13.4.1614")
        self.assertEqual(overridden.forge, "1.7.10-10.13.4.1558")
