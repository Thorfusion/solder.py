import hashlib
import io
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from tests.environment import configure_test_environment


configure_test_environment()

from models.integration import (  # noqa: E402
    CURSEFORGE,
    MODRINTH,
    CurseForgeProvider,
    ExternalProject,
    ExternalVersion,
    IntegrationCredential,
    IntegrationError,
    ModIntegration,
    ModrinthProvider,
)


def json_response(payload, status=200):
    response = Mock()
    response.status_code = status
    response.headers = {}
    response.json.return_value = payload
    if status >= 400:
        response.raise_for_status.side_effect = Exception("request failed")
    return response


class ProviderTests(unittest.TestCase):
    def test_modrinth_search_and_versions_use_minecraft_filters(self):
        http = Mock()
        http.get.side_effect = [
            json_response(
                {
                    "hits": [
                        {
                            "project_id": "AABBCCDD",
                            "project_type": "mod",
                            "slug": "example-mod",
                            "title": "Example Mod",
                            "description": "An example",
                            "author": "Author",
                            "versions": ["1.21.1"],
                            "environment": ["client_and_server"],
                            "license": "MIT",
                        }
                    ]
                }
            ),
            json_response(
                [
                    {
                        "id": "VERSION1",
                        "project_id": "AABBCCDD",
                        "name": "Version 1",
                        "version_number": "1.0.0",
                        "game_versions": ["1.21.1"],
                        "loaders": ["fabric"],
                        "version_type": "release",
                        "date_published": "2026-01-01T00:00:00Z",
                        "dependencies": [],
                        "files": [
                            {
                                "filename": "example.jar",
                                "url": "https://cdn.modrinth.com/data/example.jar",
                                "primary": True,
                                "size": 123,
                                "hashes": {"sha512": "a" * 128},
                            }
                        ],
                    }
                ]
            ),
        ]
        provider = ModrinthProvider(http=http)

        projects = provider.search("example")
        versions = provider.list_versions("AABBCCDD", "1.21.1", "FABRIC")

        self.assertEqual(projects[0].project_id, "AABBCCDD")
        self.assertEqual(projects[0].side, "BOTH")
        self.assertEqual(versions[0].version_id, "VERSION1")
        self.assertEqual(versions[0].loaders, ("FABRIC",))
        version_params = http.get.call_args_list[1].kwargs["params"]
        self.assertEqual(version_params["game_versions"], '["1.21.1"]')
        self.assertEqual(version_params["loaders"], '["fabric"]')

    def test_modrinth_project_uses_accepted_team_members_as_authors(self):
        http = Mock()
        http.get.side_effect = [
            json_response(
                {
                    "id": "AABBCCDD",
                    "project_type": "mod",
                    "team": "MMNNOOPP",
                    "slug": "example-mod",
                    "title": "Example Mod",
                    "description": "An example",
                    "status": "approved",
                }
            ),
            json_response(
                [
                    {
                        "accepted": True,
                        "ordering": 1,
                        "user": {"username": "helper", "name": "Helper"},
                    },
                    {
                        "accepted": True,
                        "ordering": 0,
                        "user": {"username": "owner", "name": None},
                    },
                    {
                        "accepted": False,
                        "user": {"username": "pending"},
                    },
                ]
            ),
        ]

        project = ModrinthProvider(http=http).get_project("AABBCCDD")

        self.assertEqual(project.author, "owner, Helper")
        self.assertIn(
            "/project/AABBCCDD/members", http.get.call_args_list[1].args[0]
        )

    def test_curseforge_project_uses_the_returned_authors(self):
        project = CurseForgeProvider._project(
            {
                "id": 123,
                "name": "Example Mod",
                "slug": "example-mod",
                "authors": [
                    {"name": "Owner"},
                    {"name": "Contributor"},
                ],
                "allowModDistribution": True,
                "isAvailable": True,
            }
        )

        self.assertEqual(project.author, "Owner, Contributor")

    def test_curseforge_requires_a_user_key(self):
        with self.assertRaisesRegex(IntegrationError, "API key"):
            CurseForgeProvider("")

    def test_curseforge_distribution_flag_is_preserved(self):
        http = Mock()
        http.get.return_value = json_response(
            {
                "data": {
                    "id": 123,
                    "gameId": 432,
                    "classId": 6,
                    "name": "Restricted Mod",
                    "slug": "restricted-mod",
                    "summary": "No redistribution",
                    "links": {},
                    "authors": [{"name": "Owner"}],
                    "allowModDistribution": False,
                    "isAvailable": True,
                }
            }
        )

        project = CurseForgeProvider("user-key", http=http).get_project("123")

        self.assertFalse(project.distribution_allowed)
        self.assertTrue(project.available)
        self.assertEqual(
            http.get.call_args.kwargs["headers"]["x-api-key"], "user-key"
        )

    def test_untrusted_provider_download_url_is_rejected(self):
        provider = ModrinthProvider(http=Mock())
        version = ExternalVersion(
            MODRINTH,
            "PROJECT",
            "VERSION",
            "Version",
            "1.0",
            ("1.21.1",),
            ("FABRIC",),
            "release",
            None,
            "example.jar",
            "https://127.0.0.1/private.jar",
            {"sha512": "a" * 128},
            1,
        )

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(IntegrationError, "untrusted"):
                provider.download(version, Path(directory, "example.jar"))

    def test_modrinth_download_verifies_hash_size_and_jar(self):
        jar_data = MaterializationTests.jar_bytes()
        response = Mock()
        response.status_code = 200
        response.headers = {"Content-Length": str(len(jar_data))}
        response.iter_content.return_value = [jar_data]
        http = Mock()
        http.get.return_value = response
        provider = ModrinthProvider(http=http)
        version = ExternalVersion(
            MODRINTH,
            "PROJECT",
            "VERSION",
            "Version",
            "1.0",
            ("1.21.1",),
            ("FABRIC",),
            "release",
            None,
            "example.jar",
            "https://cdn.modrinth.com/data/example.jar",
            {"sha512": hashlib.sha512(jar_data).hexdigest()},
            len(jar_data),
        )

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory, "example.jar")
            size, md5 = provider.download(version, destination)

        self.assertEqual(size, len(jar_data))
        self.assertEqual(
            md5, hashlib.md5(jar_data, usedforsecurity=False).hexdigest()
        )
        response.close.assert_called_once_with()

    def test_modrinth_download_rejects_hash_mismatch(self):
        jar_data = MaterializationTests.jar_bytes()
        response = Mock()
        response.status_code = 200
        response.headers = {"Content-Length": str(len(jar_data))}
        response.iter_content.return_value = [jar_data]
        http = Mock()
        http.get.return_value = response
        provider = ModrinthProvider(http=http)
        version = ExternalVersion(
            MODRINTH,
            "PROJECT",
            "VERSION",
            "Version",
            "1.0",
            ("1.21.1",),
            ("FABRIC",),
            "release",
            None,
            "example.jar",
            "https://cdn.modrinth.com/data/example.jar",
            {"sha512": "0" * 128},
            len(jar_data),
        )

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(IntegrationError, "SHA512"):
                provider.download(version, Path(directory, "example.jar"))


class CredentialTests(unittest.TestCase):
    def test_user_can_store_their_own_curseforge_key(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        with patch(
            "models.integration.Database.get_connection",
            return_value=connection,
        ):
            IntegrationCredential.set(7, CURSEFORGE, "personal-key")

        parameters = cursor.execute.call_args.args[1]
        self.assertEqual(parameters, (7, CURSEFORGE, "personal-key"))
        connection.commit.assert_called_once_with()
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()


class MaterializationTests(unittest.TestCase):
    @staticmethod
    def jar_bytes():
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as jar:
            jar.writestr("fabric.mod.json", "{}")
        return data.getvalue()

    def test_import_links_metadata_without_downloading_files(self):
        project = ExternalProject(
            MODRINTH,
            "PROJECT",
            "example-mod",
            "Example Mod",
            "Description",
            "Author",
            "https://modrinth.com/mod/example-mod",
        )
        provider = Mock()
        provider.get_project.return_value = project
        stored = SimpleNamespace(id=9)

        with (
            patch("models.integration.provider_for_user", return_value=provider),
            patch("models.integration.Mod.get_by_integration", return_value=None),
            patch("models.integration.Mod.new", return_value=stored) as new,
        ):
            mod, created = ModIntegration.import_project(
                MODRINTH, "PROJECT", 7
            )

        self.assertIs(mod, stored)
        self.assertTrue(created)
        self.assertEqual(new.call_args.kwargs["integration_provider"], MODRINTH)
        self.assertEqual(new.call_args.kwargs["integration_project_id"], "PROJECT")
        provider.download.assert_not_called()

    def test_selected_remote_version_is_packaged_once(self):
        jar_data = self.jar_bytes()
        provider = Mock()
        provider.get_project.return_value = ExternalProject(
            MODRINTH,
            "PROJECT",
            "example-mod",
            "Example Mod",
            "Description",
            "Author",
            "https://modrinth.com/mod/example-mod",
        )
        provider.get_version.return_value = ExternalVersion(
            MODRINTH,
            "PROJECT",
            "VERSION",
            "Version 1",
            "1.0.0",
            ("1.21.1",),
            ("FABRIC",),
            "release",
            None,
            "upstream.jar",
            "https://cdn.modrinth.com/data/upstream.jar",
            {"sha512": hashlib.sha512(jar_data).hexdigest()},
            len(jar_data),
        )

        def download(_version, destination):
            Path(destination).write_bytes(jar_data)
            return (
                len(jar_data),
                hashlib.md5(jar_data, usedforsecurity=False).hexdigest(),
            )

        provider.download.side_effect = download
        mod = SimpleNamespace(
            id=9,
            name="example-mod",
            integration_provider=MODRINTH,
            integration_project_id="PROJECT",
        )
        build = SimpleNamespace(minecraft="1.21.1", modloader="FABRIC")
        stored = SimpleNamespace(id=42)

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("models.integration.provider_for_user", return_value=provider),
            patch(
                "models.integration.Modversion.get_by_integration",
                return_value=None,
            ),
            patch(
                "models.integration.Modversion.version_exists",
                return_value=False,
            ),
            patch("models.integration.Modversion.new", return_value=stored) as new,
        ):
            result = ModIntegration.materialize(
                mod, build, "VERSION", 7, directory
            )
            package_path = Path(
                directory,
                "example-mod",
                "example-mod-1.21.1-1.0.0.zip",
            )
            with zipfile.ZipFile(package_path) as package:
                packaged_jar = package.read(
                    "mods/example-mod-1.21.1-1.0.0.jar"
                )

        self.assertTrue(result.created)
        self.assertEqual(packaged_jar, jar_data)
        self.assertEqual(new.call_args.kwargs["integration_version_id"], "VERSION")
        self.assertEqual(new.call_args.kwargs["modloader"], "FABRIC")

    def test_existing_materialized_version_is_reused_without_provider_call(self):
        existing = SimpleNamespace(id=42)
        mod = SimpleNamespace(
            id=9,
            name="example-mod",
            integration_provider=MODRINTH,
            integration_project_id="PROJECT",
        )
        build = SimpleNamespace(minecraft="1.21.1", modloader="FABRIC")
        with patch(
            "models.integration.Modversion.get_by_integration",
            return_value=existing,
        ), patch("models.integration.provider_for_user") as provider:
            result = ModIntegration.materialize(
                mod, build, "VERSION", 7, "unused"
            )

        self.assertFalse(result.created)
        self.assertIs(result.version, existing)
        provider.assert_not_called()

    def test_managed_mod_rejects_an_unsafe_local_slug(self):
        mod = SimpleNamespace(
            id=9,
            name="../example-mod",
            integration_provider=MODRINTH,
            integration_project_id="PROJECT",
        )
        build = SimpleNamespace(minecraft="1.21.1", modloader="FABRIC")
        with patch(
            "models.integration.Modversion.get_by_integration",
            return_value=None,
        ), patch("models.integration.provider_for_user") as provider:
            with self.assertRaisesRegex(IntegrationError, "unsafe"):
                ModIntegration.materialize(
                    mod, build, "VERSION", 7, "unused"
                )

        provider.assert_not_called()


if __name__ == "__main__":
    unittest.main()
