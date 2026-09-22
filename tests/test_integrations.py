import hashlib
import io
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import ANY, Mock, patch
import zipfile

from tests.environment import configure_test_environment


configure_test_environment()

from models.integration import (  # noqa: E402
    MAVEN,
    MODRINTH,
    ExternalDependency,
    ExternalProject,
    ExternalVersion,
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
    def test_modrinth_parses_only_required_project_dependencies(self):
        version = ModrinthProvider._version(
            {
                "project_id": "ROOTPROJ",
                "id": "ROOTVER",
                "version_number": "1.0",
                "game_versions": ["1.21.1"],
                "loaders": ["fabric"],
                "dependencies": [
                    {
                        "dependency_type": "required",
                        "project_id": "DEPPROJ1",
                        "version_id": "DEPVER01",
                    },
                    {
                        "dependency_type": "optional",
                        "project_id": "OPTIONAL1",
                        "version_id": "OPTIONALVER",
                    },
                    {
                        "dependency_type": "required",
                        "project_id": None,
                        "version_id": "ONLYVER1",
                    },
                ],
                "files": [
                    {
                        "filename": "root.jar",
                        "url": "https://cdn.modrinth.com/data/root.jar",
                        "primary": True,
                        "size": 123,
                        "hashes": {"sha512": "a" * 128},
                    }
                ],
            }
        )

        self.assertEqual(
            version.dependencies,
            (
                ExternalDependency("DEPPROJ1", "DEPVER01"),
                ExternalDependency(None, "ONLYVER1"),
            ),
        )

    def test_provider_api_redirect_is_rejected(self):
        http = Mock()
        http.get.return_value = json_response({}, status=302)

        with self.assertRaisesRegex(IntegrationError, "redirect"):
            ModrinthProvider(http=http).get_project("PROJECT")

        self.assertFalse(http.get.call_args.kwargs["allow_redirects"])

    def test_modrinth_lists_all_versions_without_a_minecraft_filter(self):
        http = Mock()
        http.get.return_value = json_response(
            [
                {
                    "project_id": "PROJECT",
                    "id": "VERSION",
                    "name": "Version 1",
                    "version_number": "1.0",
                    "game_versions": ["1.20.1", "1.21.1"],
                    "loaders": ["fabric"],
                    "version_type": "release",
                    "date_published": "2026-01-01T00:00:00Z",
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
        )

        versions = ModrinthProvider(http=http).list_all_versions("PROJECT")

        self.assertEqual(versions[0].game_versions, ("1.20.1", "1.21.1"))
        self.assertEqual(
            http.get.call_args.kwargs["params"],
            {"include_changelog": "false"},
        )

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

    def test_modrinth_versions_are_sorted_by_publication_not_provider_id(self):
        def version(version_id, published):
            return {
                "id": version_id,
                "project_id": "PROJECT",
                "name": version_id,
                "version_number": version_id,
                "game_versions": ["1.21.1"],
                "loaders": ["fabric"],
                "version_type": "release",
                "date_published": published,
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

        http = Mock()
        http.get.return_value = json_response(
            [
                version("ZZZZ-OLDER", "2025-01-01T00:00:00Z"),
                version("AAAA-NEWER", "2026-01-01T00:00:00Z"),
            ]
        )

        versions = ModrinthProvider(http=http).list_versions(
            "PROJECT", "1.21.1", "FABRIC"
        )

        self.assertEqual(
            [version.version_id for version in versions],
            ["AAAA-NEWER", "ZZZZ-OLDER"],
        )

    def test_modrinth_exact_versions_are_resolved_in_one_batch(self):
        def version(project_id, version_id):
            return {
                "id": version_id,
                "project_id": project_id,
                "name": version_id,
                "version_number": "1.0",
                "game_versions": ["1.21.1"],
                "loaders": ["fabric"],
                "version_type": "release",
                "files": [
                    {
                        "filename": f"{project_id}.jar",
                        "url": (
                            "https://cdn.modrinth.com/data/"
                            f"{project_id}/{version_id}.jar"
                        ),
                        "primary": True,
                        "size": 123,
                        "hashes": {"sha512": "a" * 128},
                    }
                ],
            }

        http = Mock()
        http.get.return_value = json_response(
            [version("PROJECT1", "VERSION1"), version("PROJECT2", "VERSION2")]
        )

        versions = ModrinthProvider(http=http).get_versions(
            [("PROJECT1", "VERSION1"), ("PROJECT2", "VERSION2")],
            "1.21.1",
            "FABRIC",
        )

        self.assertEqual(set(versions), {"VERSION1", "VERSION2"})
        self.assertTrue(http.get.call_args.args[0].endswith("/versions"))
        self.assertEqual(
            http.get.call_args.kwargs["params"]["ids"],
            '["VERSION1", "VERSION2"]',
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


class MaterializationTests(unittest.TestCase):
    @staticmethod
    def jar_bytes():
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as jar:
            jar.writestr("fabric.mod.json", "{}")
        return data.getvalue()

    def test_verified_maven_source_is_persistable_for_bootstrap(self):
        external = ExternalVersion(
            MAVEN,
            "7",
            "VERSION",
            "Version",
            "1.0",
            ("1.7.10",),
            ("FORGE",),
            "release",
            None,
            "example.jar",
            "https://maven.example.test/example.jar",
            {"sha1": "1" * 40, "sha512": "2" * 128},
            123,
        )

        source = ModIntegration._provider_download_source(
            external, md5="a" * 32, filesize=123
        )

        self.assertEqual(
            source,
            {
                "provider": MAVEN,
                "url": "https://maven.example.test/example.jar",
                "filename": "example.jar",
                "md5": "a" * 32,
                "sha1": "1" * 40,
                "sha512": "2" * 128,
                "filesize": 123,
            },
        )

    def test_private_http_maven_source_uses_solder_bootstrap_fallback(self):
        external = ExternalVersion(
            MAVEN,
            "7",
            "VERSION",
            "Version",
            "1.0",
            ("1.7.10",),
            ("FORGE",),
            "release",
            None,
            "example.jar",
            "http://maven.internal.example/example.jar",
            {"sha256": "2" * 64},
            123,
        )

        self.assertIsNone(
            ModIntegration._provider_download_source(
                external, md5="a" * 32, filesize=123
            )
        )

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
            patch("models.integration.Mod.get_by_name_api", return_value=None),
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

    def test_bootstrap_project_import_only_fetches_missing_projects(self):
        loader = SimpleNamespace(id=8)
        relauncher = SimpleNamespace(id=9)

        def existing(_provider, project_id):
            return loader if project_id == "5LpwENAj" else None

        with (
            patch(
                "models.integration.Mod.get_by_integration",
                side_effect=existing,
            ),
            patch.object(
                ModIntegration,
                "import_project",
                return_value=(relauncher, True),
            ) as import_project,
            patch("models.integration.Mod.set_modtypes") as set_modtypes,
        ):
            mods, created = ModIntegration.ensure_bootstrap_projects(
                ("5LpwENAj", "zCFNaupz"), 7
            )

        self.assertEqual(mods, (loader, relauncher))
        self.assertEqual(created, 1)
        import_project.assert_called_once_with(
            MODRINTH,
            "zCFNaupz",
            7,
            http=None,
            _change_context=ANY,
        )
        set_modtypes.assert_called_once()
        self.assertEqual(tuple(set_modtypes.call_args.args[0]), (8, 9))
        self.assertEqual(set_modtypes.call_args.args[1], "BOOTSTRAP")

    def test_import_links_an_existing_manual_slug_and_keeps_its_versions(self):
        project = ExternalProject(
            MODRINTH,
            "PROJECT",
            "example-mod",
            "Example Mod",
            "Upstream description",
            "Upstream Author",
            "https://modrinth.com/mod/example-mod",
        )
        provider = Mock()
        provider.get_project.return_value = project
        existing = SimpleNamespace(
            id=9,
            name="example-mod",
            pretty_name="Local Example Mod",
            integration_provider=None,
            integration_project_id=None,
        )
        linked = SimpleNamespace(
            id=9,
            name="example-mod",
            pretty_name="Local Example Mod",
            integration_provider=MODRINTH,
            integration_project_id="PROJECT",
        )

        with (
            patch("models.integration.provider_for_user", return_value=provider),
            patch("models.integration.Mod.get_by_integration", return_value=None),
            patch("models.integration.Mod.get_by_name_api", return_value=existing),
            patch(
                "models.integration.Mod.link_integration", return_value=linked
            ) as link,
            patch("models.integration.Mod.new") as new,
        ):
            mod, created = ModIntegration.import_project(
                MODRINTH, "PROJECT", 7
            )

        self.assertIs(mod, linked)
        self.assertFalse(created)
        link.assert_called_once_with(9, MODRINTH, "PROJECT")
        new.assert_not_called()

    def test_import_does_not_replace_another_slug_integration(self):
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
        existing = SimpleNamespace(
            id=9,
            name="example-mod",
            integration_provider="MAVEN",
            integration_project_id="4",
        )

        with (
            patch("models.integration.provider_for_user", return_value=provider),
            patch("models.integration.Mod.get_by_integration", return_value=None),
            patch("models.integration.Mod.get_by_name_api", return_value=existing),
            patch("models.integration.Mod.link_integration") as link,
        ):
            with self.assertRaisesRegex(IntegrationError, "another integration"):
                ModIntegration.import_project(MODRINTH, "PROJECT", 7)

        link.assert_not_called()

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
        self.assertEqual(new.call_args.kwargs["jarfilesize"], len(jar_data))
        self.assertEqual(
            new.call_args.kwargs["download_source"],
            {
                "provider": MODRINTH,
                "url": "https://cdn.modrinth.com/data/upstream.jar",
                "filename": "upstream.jar",
                "md5": hashlib.md5(
                    jar_data, usedforsecurity=False
                ).hexdigest(),
                "sha1": None,
                "sha512": hashlib.sha512(jar_data).hexdigest(),
                "filesize": len(jar_data),
            },
        )

    def test_selected_modrinth_version_imports_required_dependencies(self):
        jar_data = self.jar_bytes()
        provider = Mock()
        provider.http = Mock()
        provider.get_project.return_value = ExternalProject(
            MODRINTH,
            "ROOTPROJ",
            "root-mod",
            "Root Mod",
            "Description",
            "Author",
            "https://modrinth.com/mod/root-mod",
        )
        root_version = ExternalVersion(
            MODRINTH,
            "ROOTPROJ",
            "ROOTVER",
            "Root 1",
            "1.0.0",
            ("1.21.1",),
            ("FABRIC",),
            "release",
            None,
            "root.jar",
            "https://cdn.modrinth.com/data/root.jar",
            {"sha512": hashlib.sha512(jar_data).hexdigest()},
            len(jar_data),
            (ExternalDependency(None, "DEPVER01"),),
        )
        dependency_version = ExternalVersion(
            MODRINTH,
            "DEPPROJ1",
            "DEPVER01",
            "Library 1",
            "2.0.0",
            ("1.21.1",),
            ("FABRIC",),
            "release",
            None,
            "library.jar",
            "https://cdn.modrinth.com/data/library.jar",
            {"sha512": hashlib.sha512(jar_data).hexdigest()},
            len(jar_data),
        )
        provider.get_version.return_value = root_version
        provider.get_version_by_id.return_value = dependency_version

        def download(_version, destination):
            Path(destination).write_bytes(jar_data)
            return (
                len(jar_data),
                hashlib.md5(jar_data, usedforsecurity=False).hexdigest(),
            )

        provider.download.side_effect = download
        root_mod = SimpleNamespace(
            id=9,
            name="root-mod",
            pretty_name="Root Mod",
            integration_provider=MODRINTH,
            integration_project_id="ROOTPROJ",
        )
        dependency_mod = SimpleNamespace(
            id=10,
            name="library",
            pretty_name="Library",
            integration_provider=MODRINTH,
            integration_project_id="DEPPROJ1",
        )
        build = SimpleNamespace(minecraft="1.21.1", modloader="FABRIC")
        stored_dependency = SimpleNamespace(id=42)
        stored_root = SimpleNamespace(id=43)

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("models.integration.provider_for_user", return_value=provider),
            patch(
                "models.integration.ModIntegration.import_project",
                return_value=(dependency_mod, True),
            ) as import_project,
            patch(
                "models.integration.Modversion.get_by_integration",
                return_value=None,
            ),
            patch(
                "models.integration.Modversion.version_exists",
                return_value=False,
            ),
            patch(
                "models.integration.Modversion.new",
                side_effect=[stored_dependency, stored_root],
            ) as new,
            patch("models.integration.ModDependency.ensure") as ensure,
        ):
            result = ModIntegration.materialize(
                root_mod, build, "ROOTVER", 7, directory
            )

        self.assertIs(result.version, stored_root)
        import_project.assert_called_once_with(
            MODRINTH,
            "DEPPROJ1",
            7,
            http=provider.http,
            _change_context=import_project.call_args.kwargs["_change_context"],
        )
        provider.get_version_by_id.assert_called_once_with(
            "DEPVER01", "1.21.1", "FABRIC"
        )
        self.assertEqual(
            [call.kwargs["integration_version_id"] for call in new.call_args_list],
            ["DEPVER01", "ROOTVER"],
        )
        ensure.assert_called_once_with(9, 10)

    def test_management_import_keeps_multi_compatibility_out_of_provider_lookup(self):
        mod = SimpleNamespace(id=9)
        stored = SimpleNamespace(id=42)
        with patch.object(
            ModIntegration, "materialize", return_value=stored
        ) as materialize:
            result = ModIntegration.materialize_for_management(
                mod,
                "VERSION",
                ["1.20.1", "1.20.2"],
                ["FORGE", "NEOFORGE"],
                7,
                "uploads",
            )

        self.assertIs(result, stored)
        provider_build = materialize.call_args.args[1]
        self.assertEqual(provider_build.minecraft, "1.20.1")
        self.assertEqual(provider_build.modloader, "FORGE")
        self.assertEqual(
            materialize.call_args.kwargs["_stored_minecraft"],
            "1.20.1,1.20.2",
        )
        self.assertEqual(
            materialize.call_args.kwargs["_stored_modloader"],
            "FORGE,NEOFORGE",
        )

    def test_modrinth_dependency_without_pinned_version_uses_latest_compatible(self):
        dependency_version = ExternalVersion(
            MODRINTH,
            "DEPPROJ1",
            "LATEST01",
            "Latest Library",
            "2.0.0",
            ("1.21.1",),
            ("FABRIC",),
            "release",
            None,
            "library.jar",
            "https://cdn.modrinth.com/data/library.jar",
            {},
            1,
        )
        external = SimpleNamespace(
            dependencies=(ExternalDependency("DEPPROJ1"),),
            loader_for_build=Mock(return_value="FABRIC"),
        )
        provider = Mock()
        provider.http = Mock()
        provider.list_versions.return_value = [dependency_version]
        root_mod = SimpleNamespace(id=9)
        dependency_mod = SimpleNamespace(
            id=10,
            pretty_name="Library",
        )
        build = SimpleNamespace(minecraft="1.21.1", modloader="FABRIC")

        with (
            patch(
                "models.integration.ModIntegration.import_project",
                return_value=(dependency_mod, True),
            ),
            patch("models.integration.ModIntegration.materialize") as materialize,
            patch("models.integration.ModDependency.ensure") as ensure,
        ):
            ModIntegration._materialize_modrinth_dependencies(
                root_mod,
                build,
                external,
                provider,
                7,
                "uploads",
                dependency_path=("ROOTPROJ",),
                stored_minecraft="1.21.1,1.21.2",
                stored_modloader="FABRIC",
            )

        provider.list_versions.assert_called_once_with(
            "DEPPROJ1", "1.21.1", "FABRIC"
        )
        self.assertEqual(materialize.call_args.args[2], "LATEST01")
        self.assertEqual(
            materialize.call_args.kwargs["_stored_minecraft"],
            "1.21.1,1.21.2",
        )
        self.assertEqual(
            materialize.call_args.kwargs["_stored_modloader"], "FABRIC"
        )
        ensure.assert_called_once_with(9, 10)

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

    def test_unimported_catalog_excludes_stored_integration_versions(self):
        imported = ExternalVersion(
            MODRINTH, "PROJECT", "IMPORTED", "Imported", "1.0",
            ("1.21.1",), ("FABRIC",), "release", None, "old.jar", None,
            {}, 0,
        )
        available = ExternalVersion(
            MODRINTH, "PROJECT", "AVAILABLE", "Available", "2.0",
            ("1.21.1",), ("FABRIC",), "release", None, "new.jar", None,
            {}, 0,
        )
        provider = Mock()
        provider.get_project.return_value = ExternalProject(
            MODRINTH, "PROJECT", "example", "Example", "", "Author",
            "https://modrinth.com/mod/example",
        )
        provider.list_all_versions.return_value = [available, imported]
        mod = SimpleNamespace(
            id=9,
            integration_provider=MODRINTH,
            integration_project_id="PROJECT",
        )
        with (
            patch("models.integration.provider_for_user", return_value=provider),
            patch(
                "models.integration.Modversion.get_integration_version_ids",
                return_value={"IMPORTED"},
            ),
        ):
            versions = ModIntegration.list_unimported_versions(mod, 7)

        self.assertEqual([version.version_id for version in versions], ["AVAILABLE"])

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

    def test_failed_dependency_graph_rolls_back_created_nodes(self):
        mod = SimpleNamespace(
            id=9,
            name="example-mod",
            integration_provider=MODRINTH,
            integration_project_id="PROJECT",
        )
        build = SimpleNamespace(minecraft="1.21.1", modloader="FABRIC")

        def fail_after_dependency(*_args, **kwargs):
            context = kwargs["_dependency_context"]
            context["created_mods"].add(10)
            context["created_versions"].add(42)
            context["dependency_edges"].append((9, 10))
            raise IntegrationError("root download failed")

        with (
            patch.object(
                ModIntegration, "_materialize", side_effect=fail_after_dependency
            ),
            patch.object(
                ModIntegration, "_rollback_modrinth_materialization"
            ) as rollback,
            self.assertRaisesRegex(IntegrationError, "root download failed"),
        ):
            ModIntegration.materialize(
                mod, build, "VERSION", 7, "uploads"
            )

        context = rollback.call_args.args[0]
        self.assertEqual(context["created_mods"], {10})
        self.assertEqual(context["created_versions"], {42})
        self.assertEqual(context["dependency_edges"], [(9, 10)])


if __name__ == "__main__":
    unittest.main()
