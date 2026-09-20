from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

from models.github_config import (
    GitHubClient,
    GitHubConfigError,
    GitHubConfigPack,
    GitHubReference,
    GitHubRepository,
    MAX_SUBMODULES,
    github_repository_reference,
)
from models.integration import (
    GITHUB,
    ExternalVersion,
    GitHubProvider,
    ModIntegration,
)


def json_response(payload, status=200):
    response = Mock()
    response.status_code = status
    response.headers = {}
    response.json.return_value = payload
    return response


class GitHubClientTests(unittest.TestCase):
    def test_repository_reference_accepts_url_or_owner_repository(self):
        self.assertEqual(
            github_repository_reference(
                "https://github.com/DrParadox7/Lost-Era-Modpack.git"
            ),
            "DrParadox7/Lost-Era-Modpack",
        )
        self.assertEqual(
            github_repository_reference("maggi373/TerralizationModcore"),
            "maggi373/TerralizationModcore",
        )

    def test_repository_reference_rejects_another_host(self):
        with self.assertRaisesRegex(GitHubConfigError, "GitHub"):
            github_repository_reference("https://example.test/owner/repo")

    def test_api_redirect_is_rejected(self):
        http = Mock()
        http.get.return_value = json_response({}, status=302)

        with self.assertRaisesRegex(GitHubConfigError, "redirect"):
            GitHubClient(http=http).repository("owner/repository")

        self.assertFalse(http.get.call_args.kwargs["allow_redirects"])

    def test_provider_exposes_tags_as_versioned_config_choices(self):
        http = Mock()
        repository = {
            "id": 1234,
            "full_name": "owner/config-pack",
            "name": "config-pack",
            "owner": {"login": "owner"},
            "html_url": "https://github.com/owner/config-pack",
            "default_branch": "main",
            "private": False,
            "archived": False,
        }
        http.get.side_effect = [
            json_response(repository),
            json_response(
                [
                    {"name": "v1.9", "commit": {"sha": "1" * 40}},
                    {"name": "v1.10", "commit": {"sha": "2" * 40}},
                ]
            ),
        ]

        versions = GitHubProvider(http=http).list_versions(
            "1234", "1.7.10", "FORGE"
        )

        self.assertEqual([version.version_number for version in versions], ["v1.10", "v1.9"])
        self.assertEqual(versions[0].provider, GITHUB)
        self.assertEqual(versions[0].game_versions, ("1.7.10",))
        self.assertEqual(versions[0].loaders, ("FORGE",))
        self.assertEqual(len(versions[0].version_id), 64)


class GitHubConfigPackTests(unittest.TestCase):
    @staticmethod
    def repository(name="owner/config-pack", repository_id="1234"):
        return GitHubRepository(
            repository_id,
            name,
            name.split("/", 1)[1],
            name.split("/", 1)[0],
            "",
            f"https://github.com/{name}",
            "main",
            False,
            False,
            None,
        )

    @staticmethod
    def archive(path, root, files):
        with zipfile.ZipFile(path, "w") as result:
            for name, content in files.items():
                result.writestr(f"{root}/{name}", content)

    def test_archive_must_match_the_resolved_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "source.zip")
            self.archive(
                source,
                "owner-config-pack-bbbbbbb",
                {"config/example.cfg": b"config"},
            )
            client = Mock()
            client.download_archive.side_effect = (
                lambda _repository, _sha, destination: shutil.copyfile(
                    source, destination
                )
            )
            destination = Path(directory, "config.zip")

            with self.assertRaisesRegex(GitHubConfigError, "resolved commit"):
                GitHubConfigPack(client).build(
                    self.repository(),
                    GitHubReference("v1", "a" * 40, "b" * 40),
                    destination,
                )

    def test_repository_metadata_is_removed_and_instance_files_are_kept(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "source.zip")
            self.archive(
                source,
                "owner-config-pack-aaaaaaa",
                {
                    "config/example.cfg": b"config",
                    "scripts/example.zs": b"script",
                    "servers.json": b"{}",
                    "README.md": b"documentation",
                    ".github/workflows/test.yml": b"workflow",
                },
            )
            client = Mock()
            client.download_archive.side_effect = (
                lambda _repository, _sha, destination: shutil.copyfile(source, destination)
            )
            client.gitlinks.return_value = []
            destination = Path(directory, "config.zip")

            GitHubConfigPack(client).build(
                self.repository(),
                GitHubReference("v1", "a" * 40, "b" * 40),
                destination,
            )

            with zipfile.ZipFile(destination) as package:
                self.assertEqual(
                    sorted(package.namelist()),
                    ["config/example.cfg", "scripts/example.zs", "servers.json"],
                )

    def test_solderpyignore_excludes_files_and_can_reinclude_a_path(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, "source.zip")
            self.archive(
                source,
                "owner-config-pack-aaaaaaa",
                {
                    ".solderpyignore": (
                        b"# Local and generated files\n"
                        b"*.bak\n"
                        b"/servers.json\n"
                        b"generated/**\n"
                        b"!generated/keep.cfg\n"
                    ),
                    "config/example.cfg": b"config",
                    "config/debug.bak": b"backup",
                    "servers.json": b"root server list",
                    "nested/servers.json": b"nested server list",
                    "generated/remove.cfg": b"remove",
                    "generated/keep.cfg": b"keep",
                },
            )
            client = Mock()
            client.download_archive.side_effect = (
                lambda _repository, _sha, destination: shutil.copyfile(source, destination)
            )
            client.gitlinks.return_value = []
            destination = Path(directory, "config.zip")

            GitHubConfigPack(client).build(
                self.repository(),
                GitHubReference("v1", "a" * 40, "b" * 40),
                destination,
            )

            with zipfile.ZipFile(destination) as package:
                self.assertEqual(
                    sorted(package.namelist()),
                    [
                        "config/example.cfg",
                        "generated/keep.cfg",
                        "nested/servers.json",
                    ],
                )

    def test_pinned_github_submodule_is_included_at_its_repository_path(self):
        with tempfile.TemporaryDirectory() as directory:
            parent_archive = Path(directory, "parent.zip")
            child_archive = Path(directory, "child.zip")
            self.archive(
                parent_archive,
                "owner-parent-aaaaaaa",
                {
                    ".gitmodules": (
                        b'[submodule "resources"]\n'
                        b"path = resources\n"
                        b"url = https://github.com/owner/resources\n"
                    ),
                    ".solderpyignore": b"resources/lang/private.lang\n",
                    "config/example.cfg": b"config",
                },
            )
            self.archive(
                child_archive,
                "owner-resources-ccccccc",
                {
                    "lang/en_us.lang": b"language",
                    "lang/private.lang": b"private",
                },
            )
            parent = self.repository("owner/parent", "1")
            child = self.repository("owner/resources", "2")
            client = Mock()

            def download(repository, _sha, destination):
                shutil.copyfile(
                    parent_archive if repository.full_name == parent.full_name else child_archive,
                    destination,
                )

            client.download_archive.side_effect = download
            client.gitlinks.side_effect = [
                [("resources", "c" * 40)],
                [],
            ]
            client.repository.return_value = child
            client.resolve_ref.return_value = GitHubReference(
                "c" * 40, "c" * 40, "d" * 40
            )
            destination = Path(directory, "config.zip")

            GitHubConfigPack(client).build(
                parent,
                GitHubReference("v1", "a" * 40, "b" * 40),
                destination,
            )

            with zipfile.ZipFile(destination) as package:
                self.assertIn("resources/lang/en_us.lang", package.namelist())
                self.assertNotIn("resources/lang/private.lang", package.namelist())

    def test_total_submodule_budget_applies_across_the_tree(self):
        pack = GitHubConfigPack(Mock())
        pack._submodule_count = MAX_SUBMODULES

        with self.assertRaisesRegex(GitHubConfigError, "too many submodules"):
            pack._add_repository(
                self.repository(),
                GitHubReference("v1", "a" * 40, "b" * 40),
                Path("unused"),
                "nested",
                1,
                Path("unused"),
            )


class ExistingModLinkTests(unittest.TestCase):
    def test_github_cannot_be_imported_as_a_new_mod(self):
        with self.assertRaisesRegex(Exception, "existing CONFIG"):
            ModIntegration.import_project(GITHUB, "owner/config", 7)

    def test_manual_modrinth_link_keeps_the_local_slug(self):
        mod = SimpleNamespace(
            id=9,
            name="different-local-slug",
            modtype="MOD",
            integration_provider=None,
            integration_project_id=None,
        )
        project = SimpleNamespace(
            provider="MODRINTH",
            project_id="PROJECT",
            title="Upstream project",
            available=True,
            distribution_allowed=True,
        )
        provider = Mock()
        provider.get_project.return_value = project
        linked = SimpleNamespace(id=9, name=mod.name)
        with (
            patch("models.integration.ModrinthProvider", return_value=provider),
            patch("models.integration.Mod.get_by_integration", return_value=None),
            patch("models.integration.Mod.link_integration", return_value=linked) as link,
        ):
            result, returned_project = ModIntegration.link_existing(
                mod,
                "MODRINTH",
                "https://modrinth.com/mod/upstream-slug",
                7,
            )

        self.assertIs(result, linked)
        self.assertIs(returned_project, project)
        link.assert_called_once_with(9, "MODRINTH", "PROJECT")

    def test_github_link_is_rejected_for_a_normal_mod(self):
        mod = SimpleNamespace(
            id=9,
            modtype="MOD",
            integration_provider=None,
            integration_project_id=None,
        )
        with self.assertRaisesRegex(Exception, "only be linked to CONFIG"):
            ModIntegration.link_existing(mod, GITHUB, "owner/config", 7)

    def test_github_materialization_creates_only_a_config_zip(self):
        mod = SimpleNamespace(
            id=9,
            name="config-pack",
            modtype="CONFIG",
        )
        build = SimpleNamespace(minecraft="1.7.10", modloader="FORGE")
        external = ExternalVersion(
            GITHUB,
            "1234",
            "a" * 64,
            "v2.0",
            "v2.0",
            ("1.7.10",),
            ("FORGE",),
            "release",
            None,
            "config-pack-v2.0.zip",
            None,
            {"git": "b" * 40},
            0,
        )
        provider = Mock()

        def build_package(_external, destination):
            with zipfile.ZipFile(destination, "w") as package:
                package.writestr("config/example.cfg", "config")

        provider.build_config_package.side_effect = build_package
        stored = SimpleNamespace(id=33, version="1.7.10-v2.0")
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("models.integration.Modversion.version_exists", return_value=False),
            patch("models.integration.Modversion.get_by_integration", return_value=None),
            patch("models.integration.Modversion.new", return_value=stored) as new,
        ):
            result = ModIntegration._materialize_github_config(
                mod,
                build,
                external,
                provider,
                directory,
            )

            package_path = Path(
                directory,
                "config-pack",
                "config-pack-1.7.10-v2.0.zip",
            )
            self.assertTrue(package_path.is_file())
            self.assertFalse(package_path.with_suffix(".jar").exists())

        self.assertTrue(result.created)
        self.assertEqual(new.call_args.args[0:3], (9, "1.7.10-v2.0", "1.7.10"))
        self.assertEqual(new.call_args.args[7], "0")
        self.assertEqual(new.call_args.kwargs["integration_version_id"], "a" * 64)


if __name__ == "__main__":
    unittest.main()
