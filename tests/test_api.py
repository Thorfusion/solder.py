import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

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
        self.get_build_dependencies = patch.object(
            api_module.ModDependency, "get_for_build_api", return_value={}
        ).start()
        self.get_mod_dependencies = patch.object(
            api_module.ModDependency, "get_by_mod_api", return_value=[]
        ).start()
        self.addCleanup(patch.stopall)

        # cachetools caches route responses at module scope. Clearing between
        # tests prevents one mocked data set from leaking into another test.
        for view in (
            api_module.modpack,
            api_module.modpack_slug,
            api_module.modpack_slug_build,
            api_module.mod,
            api_module.mod_name,
            api_module.mod_name_version,
        ):
            view.cache.clear()

    def test_api_info_reports_name_and_version(self):
        response = self.client.get("/api/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {
                "api": "solder.py",
                "version": "v1.9.0",
                "stream": "DEV",
                "capabilities": {
                    "build_channels": True,
                    "build_comparison": True,
                    "optional_manifests": True,
                    "server_manifests": True,
                    "write_api": False,
                },
            },
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

    @patch.object(api_module, "write_api", True)
    @patch.object(api_module.Key, "get_key", return_value=None)
    @patch.object(api_module.Modpack, "get_by_cid_api", return_value=[])
    @patch.object(api_module.Modpack, "get_all_api")
    @patch.object(api_module.ApiToken, "authenticate")
    def test_bearer_read_lists_only_the_users_private_modpacks(
        self, authenticate, get_all, _get_by_cid, _get_key
    ):
        from models.api_token import ApiPrincipal

        authenticate.return_value = ApiPrincipal(
            3, 7, {"modpacks": "2"}, frozenset()
        )
        get_all.return_value = [
            SimpleNamespace(
                id=1, slug="public", name="Public", hidden=0, private=0
            ),
            SimpleNamespace(
                id=2, slug="assigned", name="Assigned", hidden=1, private=1
            ),
            SimpleNamespace(
                id=3, slug="other", name="Other", hidden=1, private=1
            ),
        ]

        response = self.client.get(
            "/api/modpack", headers={"Authorization": "Bearer 3|secret"}
        )

        self.assertEqual(
            response.get_json()["modpacks"],
            {"assigned": "Assigned"},
        )
        authenticate.assert_called_once_with("Bearer 3|secret", touch=False)

    @patch.object(api_module, "write_api", True)
    @patch.object(api_module.Key, "get_key", return_value=None)
    @patch.object(api_module.Modpack, "get_by_cid_slug_api", return_value=None)
    @patch.object(api_module.Modpack, "get_all_by_slug_api")
    @patch.object(api_module.ApiToken, "authenticate")
    def test_bearer_read_can_fetch_an_assigned_private_build(
        self, authenticate, get_modpack, _get_by_cid, _get_key
    ):
        from models.api_token import ApiPrincipal

        authenticate.return_value = ApiPrincipal(
            3, 7, {}, frozenset({2})
        )
        build = Mock(
            id=7,
            minecraft="1.21.1",
            min_java="1.8.0_51",
            java_runtime="java-runtime-delta",
            min_memory=4096,
            forge=None,
        )
        build.get_modversions_api.return_value = []
        modpack = Mock(id=2, private=1)
        modpack.get_build_api.return_value = build
        get_modpack.return_value = modpack

        response = self.client.get(
            "/api/modpack/private/42",
            headers={"Authorization": "Bearer 3|secret"},
        )

        self.assertEqual(response.status_code, 200)
        modpack.get_build_api.assert_called_once_with(
            "42", cid=None, api_key=True
        )

    @patch.object(api_module.Modpack, "get_by_cid_api")
    @patch.object(api_module.Key, "get_key", return_value=None)
    def test_full_modpack_list_uses_already_loaded_packs(
        self, _get_key, get_modpacks
    ):
        modpack = Mock(slug="stable")
        modpack.get_builds_api.return_value = [SimpleNamespace(version="42")]
        modpack.to_json.return_value = {
            "id": 1,
            "name": "stable",
            "display_name": "Stable Pack",
            "builds": ["42"],
        }
        get_modpacks.return_value = [modpack]

        response = self.client.get(
            "/api/modpack?cid=client-123&include=full"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["modpacks"]["stable"]["builds"], ["42"])
        modpack.get_builds_api.assert_called_once_with(
            cid="client-123", api_key=False
        )
        modpack.to_json.assert_called_once_with()

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
        modpack.get_builds_api.assert_called_once_with(
            cid="client-123", api_key=False
        )

    @patch.object(api_module.Modpack, "get_by_cid_slug_api", return_value=None)
    @patch.object(api_module.Key, "get_key", return_value=None)
    def test_missing_modpack_returns_404(self, _get_key, _get_modpack):
        response = self.client.get("/api/modpack/missing?cid=client-123")

        self.assertEqual(response.status_code, 404)
        self.assertIn("does not exist", response.get_json()["error"])

    @patch.object(api_module.Modpack, "get_by_cid_slug_api")
    @patch.object(api_module.Modpack, "get_all_by_slug_api")
    @patch.object(api_module.Key, "get_key")
    def test_api_key_is_enforced_on_build_requests(
        self, get_key, get_by_key, get_by_cid
    ):
        get_key.return_value = SimpleNamespace(name="CI key")
        modpack = Mock()
        build = Mock(
            id=7,
            minecraft="1.21.1",
            min_java="21",
            min_memory=4096,
            forge=None,
        )
        build.get_modversions_api.return_value = []
        modpack.get_build_api.return_value = build
        get_by_key.return_value = modpack

        response = self.client.get(
            "/api/modpack/private/42?k=valid-api-key"
        )

        self.assertEqual(response.status_code, 200)
        get_by_key.assert_called_once_with("private")
        get_by_cid.assert_not_called()
        modpack.get_build_api.assert_called_once_with(
            "42", cid=None, api_key=True
        )

    @patch.object(api_module.Modpack, "get_by_cid_slug_api")
    @patch.object(api_module.Key, "get_key", return_value=None)
    def test_build_manifest_contains_download_information(
        self, _get_key, get_modpack
    ):
        modpack = Mock()
        build = Mock(
            id=7,
            minecraft="1.21.1",
            min_java="1.8.0_51",
            java_runtime="java-runtime-delta",
            min_memory=4096,
            forge="52.0.1",
            modloader="FORGE",
        )
        build.get_modversions_api.return_value = [
            SimpleNamespace(
                id=9,
                mod_id=3,
                modname="example",
                version="2.0",
                md5="abc123",
                filesize=1234,
            )
        ]
        modpack.get_build_api.return_value = build
        get_modpack.return_value = modpack

        response = self.client.get(
            "/api/modpack/stable/42?cid=client-123"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["minecraft"], "1.21.1")
        self.assertEqual(response.get_json()["java"], "1.8.0_51")
        self.assertEqual(
            response.get_json()["java_runtime"], "java-runtime-delta"
        )
        self.assertEqual(
            response.get_json()["mods"],
            [
                {
                    "id": 9,
                    "name": "example",
                    "version": "2.0",
                    "md5": "abc123",
                    "filesize": 1234,
                    "url": "https://cdn.example.test/mods/example/example-2.0.zip",
                }
            ],
        )
        self.assertEqual(response.get_json()["id"], 7)
        self.assertEqual(response.get_json()["forge"], "52.0.1")
        modpack.get_build_api.assert_called_once_with(
            "42", cid="client-123", api_key=False
        )
        build.get_modversions_api.assert_called_once_with(
            target="client", include_optional=False
        )

    @patch.object(api_module.Modpack, "get_by_cid_slug_api")
    @patch.object(api_module.Key, "get_key", return_value=None)
    def test_optional_build_can_include_full_mod_metadata(
        self, _get_key, get_modpack
    ):
        modpack = Mock(enable_optionals=1, enable_server=0)
        build = Mock(
            id=7,
            minecraft="1.21.1",
            min_java="21",
            min_memory=4096,
            forge=None,
        )
        build.get_modversions_api.return_value = [
            SimpleNamespace(
                id=9,
                mod_id=3,
                modname="example",
                version="2.0",
                md5="abc123",
                filesize=1234,
                pretty_name="Example Mod",
                author="Example Author",
                description="Example description",
                link="https://example.test/mod",
                side="BOTH",
                modtype="MOD",
                modloader="FORGE",
                optional=1,
            )
        ]
        self.get_build_dependencies.return_value = {
            3: [
                {
                    "id": 4,
                    "name": "library",
                    "pretty_name": "Library",
                    "side": "BOTH",
                    "modtype": "MOD",
                }
            ]
        }
        modpack.get_build_api.side_effect = [None, build]
        get_modpack.return_value = modpack

        response = self.client.get(
            "/api/modpack/stable/42-optional?cid=client-123&include=mods"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["mods"][0]["pretty_name"], "Example Mod")
        self.assertEqual(response.get_json()["mods"][0]["author"], "Example Author")
        self.assertEqual(response.get_json()["mods"][0]["side"], "BOTH")
        self.assertEqual(response.get_json()["mods"][0]["modtype"], "MOD")
        self.assertTrue(response.get_json()["mods"][0]["optional"])
        self.assertEqual(
            response.get_json()["mods"][0]["dependencies"][0]["name"],
            "library",
        )
        self.assertEqual(
            modpack.get_build_api.call_args_list,
            [
                call("42-optional", cid="client-123", api_key=False),
                call("42", cid="client-123", api_key=False),
            ],
        )
        build.get_modversions_api.assert_called_once_with(
            target="client", include_optional=True
        )

    @patch.object(api_module.Modpack, "get_by_cid_slug_api")
    @patch.object(api_module.Key, "get_key", return_value=None)
    def test_hyphenated_build_name_is_not_truncated(self, _get_key, get_modpack):
        modpack = Mock()
        build = Mock(
            id=8,
            minecraft="1.21.1",
            min_java="21",
            min_memory=4096,
            forge=None,
        )
        build.get_modversions_api.return_value = []
        modpack.get_build_api.return_value = build
        get_modpack.return_value = modpack

        response = self.client.get(
            "/api/modpack/stable/1.20.1-beta-2?cid=client-123"
        )

        self.assertEqual(response.status_code, 200)
        modpack.get_build_api.assert_called_once_with(
            "1.20.1-beta-2", cid="client-123", api_key=False
        )
        build.get_modversions_api.assert_called_once_with(
            target="client", include_optional=False
        )

    @patch.object(api_module.Modpack, "get_by_cid_slug_api")
    @patch.object(api_module.Key, "get_key", return_value=None)
    def test_server_manifest_query_has_deployment_metadata(
        self, _get_key, get_modpack
    ):
        modpack = Mock(
            slug="stable",
            enable_server=1,
            enable_optionals=1,
        )
        build = Mock(
            id=7,
            version="42",
            minecraft="1.21.1",
            min_java="21",
            min_memory=4096,
            forge="52.0.1",
            modloader="FORGE",
        )
        build.get_modversions_api.return_value = [
            SimpleNamespace(
                id=9,
                mod_id=3,
                modname="example",
                version="2.0",
                md5="abc123",
                filesize=1234,
                side="BOTH",
                modtype="MOD",
                modloader="FORGE",
                optional=0,
            )
        ]
        self.get_build_dependencies.return_value = {
            3: [
                {
                    "id": 4,
                    "name": "library",
                    "pretty_name": "Library",
                    "side": "BOTH",
                    "modtype": "MOD",
                }
            ]
        }
        modpack.get_build_api.return_value = build
        get_modpack.return_value = modpack

        response = self.client.get(
            "/api/modpack/stable/42?target=server&optional=true"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["modpack"], "stable")
        self.assertEqual(payload["version"], "42")
        self.assertEqual(payload["modloader"], "FORGE")
        self.assertEqual(payload["target"], "server")
        self.assertTrue(payload["optional"])
        self.assertEqual(payload["mods"][0]["side"], "BOTH")
        self.assertEqual(payload["mods"][0]["modtype"], "MOD")
        self.assertEqual(payload["mods"][0]["modloader"], "FORGE")
        self.assertFalse(payload["mods"][0]["optional"])
        self.assertEqual(payload["mods"][0]["dependencies"][0]["id"], 4)
        self.assertEqual(len(payload["manifest_hash"]), 64)
        self.assertEqual(
            response.headers["ETag"], f'"{payload["manifest_hash"]}"'
        )
        build.get_modversions_api.assert_called_once_with(
            target="server", include_optional=True
        )

    @patch.object(api_module.Modpack, "get_by_cid_slug_api")
    @patch.object(api_module.Key, "get_key", return_value=None)
    def test_recommended_channel_resolves_to_real_build(
        self, _get_key, get_modpack
    ):
        modpack = Mock(
            slug="stable",
            recommended="42",
            latest="43",
            enable_server=1,
            enable_optionals=0,
        )
        build = Mock(
            id=7,
            version="42",
            minecraft="1.21.1",
            min_java="21",
            min_memory=4096,
            forge=None,
        )
        build.get_modversions_api.return_value = []
        modpack.get_build_api.side_effect = [None, build]
        get_modpack.return_value = modpack

        response = self.client.get(
            "/api/modpack/stable/recommended?target=server"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["version"], "42")
        self.assertEqual(
            modpack.get_build_api.call_args_list,
            [
                call("recommended", cid=None, api_key=False),
                call("42", cid=None, api_key=False),
            ],
        )

    @patch.object(api_module.Modpack, "get_by_cid_slug_api")
    @patch.object(api_module.Key, "get_key", return_value=None)
    def test_server_manifest_can_compare_with_an_installed_build(
        self, _get_key, get_modpack
    ):
        modpack = Mock(
            slug="stable",
            enable_server=1,
            enable_optionals=0,
        )
        current = Mock(
            id=2,
            version="2",
            minecraft="1.21.1",
            min_java="21",
            min_memory=4096,
            forge=None,
        )
        previous = Mock(id=1, version="1")

        def version(identifier, name, release, checksum):
            return SimpleNamespace(
                id=identifier,
                mod_id=identifier,
                modname=name,
                version=release,
                md5=checksum,
                filesize=1024,
                side="SERVER",
                modtype="MOD",
                optional=0,
            )

        current.get_modversions_api.return_value = [
            version(2, "added", "1.0", "a"),
            version(3, "updated", "2.0", "b"),
        ]
        previous.get_modversions_api.return_value = [
            version(4, "removed", "1.0", "c"),
            version(5, "updated", "1.0", "d"),
        ]
        modpack.get_build_api.side_effect = [current, previous]
        get_modpack.return_value = modpack

        response = self.client.get(
            "/api/modpack/stable/2?target=server&from=1"
        )

        self.assertEqual(response.status_code, 200)
        changes = response.get_json()["changes"]
        self.assertEqual(changes["from"], "1")
        self.assertEqual(changes["to"], "2")
        self.assertEqual([mod["name"] for mod in changes["added"]], ["added"])
        self.assertEqual(
            [change["to"]["name"] for change in changes["updated"]],
            ["updated"],
        )
        self.assertEqual(
            [mod["name"] for mod in changes["removed"]], ["removed"]
        )

    @patch.object(api_module.Modpack, "get_by_cid_slug_api")
    @patch.object(api_module.Key, "get_key", return_value=None)
    def test_invalid_manifest_options_are_rejected(
        self, _get_key, get_modpack
    ):
        modpack = Mock(
            enable_server=1,
            enable_optionals=1,
        )
        modpack.get_build_api.return_value = Mock()
        get_modpack.return_value = modpack

        target_response = self.client.get(
            "/api/modpack/stable/42?target=dedicated"
        )
        optional_response = self.client.get(
            "/api/modpack/stable/42?optional=perhaps"
        )

        self.assertEqual(target_response.status_code, 400)
        self.assertEqual(optional_response.status_code, 400)
        self.assertEqual(
            target_response.get_json(), {"error": "Invalid manifest options"}
        )
        self.assertEqual(
            optional_response.get_json(), {"error": "Invalid manifest options"}
        )

    @patch.object(api_module.Mod, "get_all_api")
    def test_mod_catalog_matches_technic_read_api(self, get_mods):
        get_mods.return_value = [
            SimpleNamespace(name="first", pretty_name="First Mod"),
            SimpleNamespace(name="second", pretty_name="Second Mod"),
        ]

        response = self.client.get("/api/mod")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {"mods": {"first": "First Mod", "second": "Second Mod"}},
        )

    @patch.object(api_module.Mod, "get_by_name_api")
    def test_mod_details_list_available_versions(self, get_mod):
        mod = Mock(id=3)
        mod.to_json.return_value = {
            "name": "example",
            "pretty_name": "Example Mod",
        }
        mod.get_versions_api.return_value = [
            {"version": "1.0"},
            {"version": "2.0"},
        ]
        get_mod.return_value = mod
        self.get_mod_dependencies.return_value = [
            {
                "id": 4,
                "name": "library",
                "pretty_name": "Library",
                "side": "BOTH",
                "modtype": "MOD",
            }
        ]

        response = self.client.get("/api/mod/example")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["versions"], ["1.0", "2.0"])
        self.assertEqual(response.get_json()["dependencies"][0]["name"], "library")
        self.get_mod_dependencies.assert_called_once_with(3)

    @patch.object(api_module.Mod, "get_by_name_api", return_value=None)
    def test_missing_mod_returns_404(self, _get_mod):
        response = self.client.get("/api/mod/missing")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json(), {"error": "Mod does not exist"})

    @patch.object(api_module.Mod, "get_by_name_api")
    def test_mod_version_has_download_url(self, get_mod):
        mod = Mock(id=1, side="BOTH", modtype="MOD")
        version = Mock(id=4, version="2.0", modloader="FABRIC")
        version.to_json.return_value = {
            "mod_id": 1,
            "version": "2.0",
            "md5": "abc123",
            "filesize": 1234,
        }
        version.get_builds_api.return_value = []
        mod.get_version_api.return_value = version
        get_mod.return_value = mod
        self.get_mod_dependencies.return_value = [
            {
                "id": 4,
                "name": "library",
                "pretty_name": "Library",
                "side": "BOTH",
                "modtype": "MOD",
            }
        ]

        response = self.client.get("/api/mod/example/2.0")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["url"],
            "https://cdn.example.test/mods/example/example-2.0.zip",
        )
        self.assertEqual(response.get_json()["id"], 4)
        self.assertEqual(response.get_json()["builds"], [])
        self.assertEqual(response.get_json()["side"], "BOTH")
        self.assertEqual(response.get_json()["modtype"], "MOD")
        self.assertEqual(response.get_json()["modloader"], "FABRIC")
        self.assertEqual(response.get_json()["dependencies"][0]["id"], 4)
        self.get_mod_dependencies.assert_called_once_with(1)
        version.get_builds_api.assert_called_once_with(cid=None, api_key=False)


if __name__ == "__main__":
    unittest.main()
