import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask

from tests.environment import configure_test_environment


configure_test_environment()

import api as api_module  # noqa: E402 - settings must exist before this import


class ApiTests(unittest.TestCase):
    def setUp(self):
        application = Flask(__name__)
        application.config.update(TESTING=True)
        application.register_blueprint(api_module.api)
        self.client = application.test_client()

        # cachetools caches route responses at module scope. Clearing between
        # tests prevents one mocked data set from leaking into another test.
        for view in (
            api_module.modpack,
            api_module.modpack_slug,
            api_module.modpack_slug_build,
            api_module.mod_name,
            api_module.mod_name_version,
        ):
            view.cache.clear()

    def test_api_info_reports_name_and_version(self):
        response = self.client.get("/api/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {"api": "solder.py", "version": "v1.7.4", "stream": "DEV"},
        )

    def test_verify_requires_an_api_key(self):
        response = self.client.get("/api/verify")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"error": "No API key provided."})

    @patch.object(api_module.Key, "get_key")
    def test_verify_rejects_an_unknown_api_key(self, get_key):
        get_key.return_value = None

        response = self.client.get("/api/verify/not-valid")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"error": "Invalid key provided."})

    @patch.object(api_module.Key, "get_key")
    def test_verify_accepts_a_known_api_key(self, get_key):
        get_key.return_value = SimpleNamespace(name="CI key")

        response = self.client.get("/api/verify/valid-key")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["valid"], "Key validated.")
        self.assertEqual(response.get_json()["name"], "CI key")

    @patch.object(api_module.Modpack, "get_by_cid_api")
    @patch.object(api_module.Key, "get_key", return_value=None)
    def test_modpack_list_is_filtered_by_client(self, _get_key, get_modpacks):
        get_modpacks.return_value = [
            SimpleNamespace(slug="stable", name="Stable Pack"),
            SimpleNamespace(slug="testing", name="Testing Pack"),
        ]

        response = self.client.get("/api/modpack?cid=client-123")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {
                "modpacks": {
                    "stable": "Stable Pack",
                    "testing": "Testing Pack",
                },
                "mirror_url": "https://cdn.example.test/mods/",
            },
        )
        get_modpacks.assert_called_once_with("client-123")

    @patch.object(api_module.Modpack, "get_by_cid_api", return_value=[])
    @patch.object(api_module.Key, "get_key", return_value=None)
    def test_empty_modpack_list_is_valid(self, _get_key, _get_modpacks):
        response = self.client.get("/api/modpack?cid=new-client")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["modpacks"], {})

    @patch.object(api_module.Modpack, "get_by_cid_slug_api")
    @patch.object(api_module.Key, "get_key", return_value=None)
    def test_modpack_details_include_client_visible_builds(
        self, _get_key, get_modpack
    ):
        modpack = Mock()
        modpack.get_builds_cid_api.return_value = [SimpleNamespace(version="42")]
        modpack.to_json.return_value = {
            "name": "stable",
            "display_name": "Stable Pack",
            "builds": ["42"],
        }
        get_modpack.return_value = modpack

        response = self.client.get("/api/modpack/stable?cid=client-123")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["builds"], ["42"])
        modpack.get_builds_cid_api.assert_called_once_with("client-123")

    @patch.object(api_module.Modpack, "get_by_cid_slug_api", return_value=None)
    @patch.object(api_module.Key, "get_key", return_value=None)
    def test_missing_modpack_returns_404(self, _get_key, _get_modpack):
        response = self.client.get("/api/modpack/missing?cid=client-123")

        self.assertEqual(response.status_code, 404)
        self.assertIn("does not exist", response.get_json()["error"])

    @patch.object(api_module.Modpack, "get_by_cid_slug_api")
    @patch.object(api_module.Key, "get_key", return_value=None)
    def test_build_manifest_contains_download_information(
        self, _get_key, get_modpack
    ):
        modpack = Mock()
        build = Mock(minecraft="1.21.1", min_java="21", min_memory=4096)
        build.get_modversions_api.return_value = [
            SimpleNamespace(modname="example", version="2.0", md5="abc123")
        ]
        modpack.get_build_api.return_value = build
        get_modpack.return_value = modpack

        response = self.client.get(
            "/api/modpack/stable/42?cid=client-123"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["minecraft"], "1.21.1")
        self.assertEqual(
            response.get_json()["mods"],
            [
                {
                    "name": "example",
                    "version": "2.0",
                    "md5": "abc123",
                    "url": "https://cdn.example.test/mods/example/example-2.0.zip",
                }
            ],
        )
        build.get_modversions_api.assert_called_once_with("")

    @patch.object(api_module.Modpack, "get_by_cid_slug_api")
    @patch.object(api_module.Key, "get_key", return_value=None)
    def test_optional_build_can_include_full_mod_metadata(
        self, _get_key, get_modpack
    ):
        modpack = Mock()
        build = Mock(minecraft="1.21.1", min_java="21", min_memory=4096)
        build.get_modversions_api.return_value = [
            SimpleNamespace(
                modname="example",
                version="2.0",
                md5="abc123",
                pretty_name="Example Mod",
                author="Example Author",
                description="Example description",
                link="https://example.test/mod",
            )
        ]
        modpack.get_build_api.return_value = build
        get_modpack.return_value = modpack

        response = self.client.get(
            "/api/modpack/stable/42-optional?cid=client-123&include=mods"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["mods"][0]["pretty_name"], "Example Mod")
        self.assertEqual(response.get_json()["mods"][0]["author"], "Example Author")
        build.get_modversions_api.assert_called_once_with("optional")

    @patch.object(api_module.Mod, "get_versions_api")
    @patch.object(api_module.Mod, "get_by_name_api")
    def test_mod_details_list_available_versions(self, get_mod, get_versions):
        mod = Mock()
        mod.to_json.return_value = {
            "name": "example",
            "pretty_name": "Example Mod",
        }
        get_mod.return_value = mod
        get_versions.return_value = [{"version": "1.0"}, {"version": "2.0"}]

        response = self.client.get("/api/mod/example")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["versions"], ["1.0", "2.0"])

    @patch.object(api_module.Mod, "get_by_name_api", return_value=None)
    def test_missing_mod_returns_404(self, _get_mod):
        response = self.client.get("/api/mod/missing")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json(), {"error": "Mod does not exist"})

    @patch.object(api_module.Mod, "get_by_name_api")
    def test_mod_version_has_download_url(self, get_mod):
        mod = Mock()
        version = Mock(version="2.0")
        version.to_json.return_value = {
            "mod_id": 1,
            "version": "2.0",
            "md5": "abc123",
            "filesize": 1234,
        }
        mod.get_version_api.return_value = version
        get_mod.return_value = mod

        response = self.client.get("/api/mod/example/2.0")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["url"],
            "https://cdn.example.test/mods/example/2.0.zip",
        )


if __name__ == "__main__":
    unittest.main()
