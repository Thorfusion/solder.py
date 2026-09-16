from dataclasses import replace
from types import SimpleNamespace
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from flask import Flask

from tests.environment import configure_test_environment


configure_test_environment()

from api_write import write_api_blueprint  # noqa: E402
from models.api_token import ApiPrincipal, ApiToken, TOKENABLE_TYPE  # noqa: E402
from models.integration import MAVEN, MODRINTH, ExternalVersion  # noqa: E402
from models.maven import MavenArtifact, MavenRepository, MavenVersion  # noqa: E402


FULL_PRINCIPAL = ApiPrincipal(1, 7, {"solder_full": True})


def modpack_row(**changes):
    row = {
        "id": 2,
        "name": "Example Pack",
        "slug": "example-pack",
        "recommended": None,
        "latest": None,
        "order": 0,
        "hidden": 1,
        "private": 0,
        "pinned": 0,
        "enable_optionals": 0,
        "enable_server": 0,
        "created_at": None,
        "updated_at": None,
    }
    row.update(changes)
    return row


def build_row(**changes):
    row = {
        "id": 3,
        "modpack_id": 2,
        "version": "1.0",
        "minecraft": "1.20.1",
        "forge": "47.2.0",
        "modloader": "FORGE",
        "is_published": 0,
        "private": 0,
        "min_java": None,
        "java_runtime": None,
        "min_memory": None,
        "created_at": None,
        "updated_at": None,
    }
    row.update(changes)
    return row


def mod_row(**changes):
    row = {
        "id": 4,
        "name": "example-mod",
        "pretty_name": "Example Mod",
        "author": "Author",
        "description": "Description",
        "link": "https://example.invalid/mod",
        "notes": None,
        "side": "BOTH",
        "modtype": "MOD",
        "integration_provider": None,
        "integration_project_id": None,
        "created_at": None,
        "updated_at": None,
    }
    row.update(changes)
    return row


def version_row(**changes):
    row = {
        "id": 5,
        "mod_id": 4,
        "version": "1.0",
        "mcversion": "1.20.1",
        "modloader": "FORGE",
        "md5": "a" * 32,
        "jarmd5": None,
        "filesize": 123,
        "integration_version_id": None,
        "created_at": None,
        "updated_at": None,
    }
    row.update(changes)
    return row


class WriteApiRouteTests(unittest.TestCase):
    def setUp(self):
        app = Flask(__name__)
        app.json.sort_keys = False
        app.register_blueprint(write_api_blueprint)
        app.config.update(TESTING=True)
        self.client = app.test_client()
        self.auth = patch(
            "api_write.ApiToken.authenticate", return_value=FULL_PRINCIPAL
        )
        self.auth.start()
        self.addCleanup(self.auth.stop)
        self.headers = {"Authorization": "Bearer 1|secret"}

    def test_every_write_route_requires_a_bearer_token(self):
        with patch("api_write.ApiToken.authenticate", return_value=None):
            response = self.client.post(
                "/api/modpack", json={"name": "Pack", "slug": "pack"}
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json(), {"error": "Unauthenticated."})

    def test_create_modpack_includes_solderpy_capabilities(self):
        created = modpack_row(
            hidden=0, pinned=1, enable_optionals=1, enable_server=1
        )
        with patch(
            "api_write.WriteApiStore.create_modpack", return_value=created
        ) as create:
            response = self.client.post(
                "/api/modpack",
                headers=self.headers,
                json={
                    "name": "Example Pack",
                    "slug": "example-pack",
                    "hidden": False,
                    "pinned": True,
                    "enable_optionals": True,
                    "enable_server": True,
                },
            )

        self.assertEqual(response.status_code, 201)
        values, user_id = create.call_args.args
        self.assertEqual(user_id, 7)
        self.assertTrue(values["pinned"])
        self.assertTrue(values["enable_optionals"])
        self.assertTrue(values["enable_server"])
        self.assertTrue(response.get_json()["enable_server"])

    def test_create_build_preserves_java_version_and_runtime_override(self):
        created = build_row(
            min_java="1.8.0_51", java_runtime="java-runtime-delta"
        )
        with (
            patch(
                "api_write.WriteApiStore.get_modpack",
                return_value=modpack_row(),
            ),
            patch(
                "api_write.WriteApiStore.create_build", return_value=created
            ) as create,
        ):
            response = self.client.post(
                "/api/modpack/example-pack/build",
                headers=self.headers,
                json={
                    "version": "1.0",
                    "minecraft": "1.20.1",
                    "min_java": "1.8.0_51",
                    "java_runtime": "java-runtime-delta",
                },
            )

        self.assertEqual(response.status_code, 201)
        values = create.call_args.args[1]
        self.assertEqual(values["min_java"], "1.8.0_51")
        self.assertEqual(values["java_runtime"], "java-runtime-delta")
        self.assertEqual(response.get_json()["min_java"], "1.8.0_51")
        self.assertEqual(
            response.get_json()["java_runtime"], "java-runtime-delta"
        )

    def test_build_rejects_an_unknown_java_runtime_component(self):
        with (
            patch(
                "api_write.WriteApiStore.get_modpack",
                return_value=modpack_row(),
            ),
            patch("api_write.WriteApiStore.create_build") as create,
        ):
            response = self.client.post(
                "/api/modpack/example-pack/build",
                headers=self.headers,
                json={
                    "version": "1.0",
                    "minecraft": "1.20.1",
                    "java_runtime": "1.8.0_401",
                },
            )

        self.assertEqual(response.status_code, 422)
        self.assertIn("java_runtime", response.get_json()["error"])
        create.assert_not_called()

    def test_build_update_can_clear_the_runtime_override(self):
        existing = build_row(java_runtime="java-runtime-delta")
        updated = build_row(java_runtime=None)
        with (
            patch(
                "api_write.WriteApiStore.get_modpack",
                return_value=modpack_row(),
            ),
            patch(
                "api_write.WriteApiStore.get_build", return_value=existing
            ),
            patch(
                "api_write.WriteApiStore.update_build", return_value=updated
            ) as update,
        ):
            response = self.client.put(
                "/api/modpack/example-pack/1.0",
                headers=self.headers,
                json={"java_runtime": ""},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(update.call_args.args[1]["java_runtime"])
        self.assertIsNone(response.get_json()["java_runtime"])

    def test_permission_is_enforced_before_mod_creation(self):
        restricted = ApiPrincipal(2, 8, {})
        with patch("api_write.ApiToken.authenticate", return_value=restricted):
            response = self.client.post(
                "/api/mod",
                headers=self.headers,
                json={"name": "example", "pretty_name": "Example"},
            )

        self.assertEqual(response.status_code, 403)

    def test_create_mod_accepts_side_type_notes_and_dependencies(self):
        created = mod_row(side="SERVER", modtype="CONFIG", notes="private")
        with (
            patch("api_write.WriteApiStore.create_mod", return_value=created) as create,
            patch("api_write.ModDependency.get_by_mod_api", return_value=[]),
        ):
            response = self.client.post(
                "/api/mod",
                headers=self.headers,
                json={
                    "name": "example-mod",
                    "pretty_name": "Example Mod",
                    "side": "server",
                    "modtype": "config",
                    "notes": "private",
                    "dependencies": [9, "library-mod"],
                },
            )

        self.assertEqual(response.status_code, 201)
        values, dependencies = create.call_args.args
        self.assertEqual(values["side"], "SERVER")
        self.assertEqual(values["modtype"], "CONFIG")
        self.assertEqual(dependencies, [9, "library-mod"])

    def test_create_modversion_accepts_compatibility_and_jar_hash(self):
        created = version_row(jarmd5="b" * 32)
        with (
            patch("api_write.WriteApiStore.get_mod", return_value=mod_row()),
            patch(
                "api_write.WriteApiStore.create_modversion", return_value=created
            ) as create,
        ):
            response = self.client.post(
                "/api/mod/example-mod/version",
                headers=self.headers,
                json={
                    "version": "1.0",
                    "md5": "A" * 32,
                    "jarmd5": "B" * 32,
                    "filesize": 123,
                    "mcversion": "1.20.1",
                    "modloader": "forge",
                },
            )

        self.assertEqual(response.status_code, 201)
        values = create.call_args.args[1]
        self.assertEqual(values["md5"], "a" * 32)
        self.assertEqual(values["jarmd5"], "b" * 32)
        self.assertEqual(values["modloader"], "FORGE")

    def test_modversion_rejects_an_invalid_md5(self):
        with patch("api_write.WriteApiStore.get_mod", return_value=mod_row()):
            response = self.client.post(
                "/api/mod/example-mod/version",
                headers=self.headers,
                json={"version": "1.0", "md5": "not-a-hash"},
            )

        self.assertEqual(response.status_code, 422)
        self.assertIn("md5", response.get_json()["error"])

    def test_integer_fields_reject_json_floats(self):
        with (
            patch("api_write.WriteApiStore.get_mod", return_value=mod_row()),
            patch("api_write.WriteApiStore.create_modversion") as create,
        ):
            response = self.client.post(
                "/api/mod/example-mod/version",
                headers=self.headers,
                json={
                    "version": "1.0",
                    "md5": "a" * 32,
                    "filesize": 123.0,
                },
            )

        self.assertEqual(response.status_code, 422)
        self.assertIn("filesize", response.get_json()["error"])
        create.assert_not_called()

    def test_boolean_fields_reject_json_floats(self):
        with patch("api_write.WriteApiStore.create_modpack") as create:
            response = self.client.post(
                "/api/modpack",
                headers=self.headers,
                json={
                    "name": "Example Pack",
                    "slug": "example-pack",
                    "hidden": 1.0,
                },
            )

        self.assertEqual(response.status_code, 422)
        self.assertIn("hidden", response.get_json()["error"])
        create.assert_not_called()

    def test_add_build_mod_persists_optional_and_reports_dependencies(self):
        with (
            patch("api_write.WriteApiStore.get_modpack", return_value=modpack_row()),
            patch("api_write.WriteApiStore.get_build", return_value=build_row()),
            patch("api_write.WriteApiStore.get_mod", return_value=mod_row()),
            patch(
                "api_write.WriteApiStore.get_modversion", return_value=version_row()
            ),
            patch(
                "api_write.WriteApiStore.add_build_mod", return_value=["Library"]
            ) as add,
        ):
            response = self.client.post(
                "/api/modpack/example-pack/1.0/mod",
                headers=self.headers,
                json={
                    "mod_slug": "example-mod",
                    "mod_version": "1.0",
                    "optional": True,
                },
            )

        self.assertEqual(response.status_code, 201)
        self.assertIs(add.call_args.args[3], True)
        self.assertEqual(response.get_json()["dependencies_added"], ["Library"])

    def test_integrated_build_mod_is_materialized_on_demand(self):
        integrated = mod_row(
            integration_provider=MODRINTH,
            integration_project_id="PROJECT",
        )
        materialized = SimpleNamespace(version=SimpleNamespace(version="2.0"))
        with (
            patch("api_write.WriteApiStore.get_modpack", return_value=modpack_row()),
            patch("api_write.WriteApiStore.get_build", return_value=build_row()),
            patch("api_write.WriteApiStore.get_mod", return_value=integrated),
            patch("api_write.Mod.get_by_id", return_value=SimpleNamespace(id=4)),
            patch("api_write.Build.get_by_id", return_value=SimpleNamespace(id=3)),
            patch("api_write.ModIntegration.materialize", return_value=materialized) as materialize,
            patch(
                "api_write.WriteApiStore.get_modversion",
                return_value=version_row(version="2.0"),
            ),
            patch("api_write.WriteApiStore.add_build_mod", return_value=[]),
        ):
            response = self.client.post(
                "/api/modpack/example-pack/1.0/mod",
                headers=self.headers,
                json={
                    "mod_slug": "example-mod",
                    "integration_version_id": "VERSION_2",
                },
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(materialize.call_args.args[2], "VERSION_2")

    def test_client_modpack_assignment_is_a_validated_sync(self):
        client = {
            "id": 12,
            "name": "Client",
            "uuid": "client-uuid",
            "created_at": None,
            "updated_at": None,
        }
        with (
            patch("api_write.WriteApiStore.get_client", return_value=client),
            patch("api_write.WriteApiStore.update_client", return_value=client) as update,
        ):
            response = self.client.put(
                "/api/client/client-uuid",
                headers=self.headers,
                json={"modpacks": [1, "2"]},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(update.call_args.args[2], [1, 2])

    def test_client_modpack_assignment_rejects_fractional_ids(self):
        client = {
            "id": 12,
            "name": "Client",
            "uuid": "client-uuid",
            "created_at": None,
            "updated_at": None,
        }
        with (
            patch("api_write.WriteApiStore.get_client", return_value=client),
            patch("api_write.WriteApiStore.update_client") as update,
        ):
            response = self.client.put(
                "/api/client/client-uuid",
                headers=self.headers,
                json={"modpacks": [1.5]},
            )

        self.assertEqual(response.status_code, 422)
        self.assertIn("modpacks.0", response.get_json()["error"])
        update.assert_not_called()

    def test_tokens_are_scoped_to_the_authenticated_user(self):
        with patch("api_write.ApiToken.get_all", return_value=[]) as get_all:
            response = self.client.get("/api/token", headers=self.headers)
        self.assertEqual(response.status_code, 200)
        get_all.assert_called_once_with(7)

        with patch("api_write.ApiToken.delete", return_value=False) as delete:
            response = self.client.delete("/api/token/42", headers=self.headers)
        self.assertEqual(response.status_code, 404)
        delete.assert_called_once_with(42, 7)

    def test_modrinth_import_uses_authenticated_user(self):
        imported = SimpleNamespace(name="example-mod")
        with (
            patch(
                "api_write.ModIntegration.import_project",
                return_value=(imported, True),
            ) as import_project,
            patch("api_write.WriteApiStore.get_mod", return_value=mod_row()),
            patch("api_write.ModDependency.get_by_mod_api", return_value=[]),
        ):
            response = self.client.post(
                "/api/integration/modrinth/mod",
                headers=self.headers,
                json={"project_id": "PROJECT"},
            )

        self.assertEqual(response.status_code, 201)
        import_project.assert_called_once_with(MODRINTH, "PROJECT", 7)

    def test_maven_repository_can_be_created_through_write_api(self):
        repository = MavenRepository(
            8, "Example Maven", "https://maven.example.test/releases/"
        )
        with patch(
            "api_write.MavenRepository.new", return_value=repository
        ) as create:
            response = self.client.post(
                "/api/integration/maven/repository",
                headers=self.headers,
                json={
                    "name": "Example Maven",
                    "base_url": "https://maven.example.test/releases/",
                },
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["repository"]["id"], 8)
        create.assert_called_once_with(
            "Example Maven", "https://maven.example.test/releases/"
        )

    def test_maven_artifact_creation_refreshes_metadata_and_links_mod(self):
        configured = MavenArtifact(
            9, 8, "Example Maven", "https://maven.example.test/releases/",
            "example.group", "example-mod", "all", "jar", "EMBEDDED",
            "{minecraft}-{version}", None, "FORGE", "example-mod",
            "Example Mod", "Description", "Author",
            "https://example.test/mod", "BOTH", None,
        )
        attached = replace(configured, mod_id=4)
        mapping = MavenVersion(
            1, 9, "1.20.1-2.0", "f" * 64, "1.20.1", "2.0",
            "FORGE", "RULE", True, True, 1,
        )
        with (
            patch("api_write.MavenArtifact.new", return_value=configured),
            patch("api_write.MavenCatalog.refresh", return_value=[mapping]) as refresh,
            patch(
                "api_write.ModIntegration.import_project",
                return_value=(SimpleNamespace(id=4), True),
            ) as import_project,
            patch("api_write.MavenArtifact.attach_mod") as attach,
            patch("api_write.MavenArtifact.get", return_value=attached),
        ):
            response = self.client.post(
                "/api/integration/maven/artifact",
                headers=self.headers,
                json={
                    "repository_id": 8,
                    "group_id": "example.group",
                    "artifact_id": "example-mod",
                    "classifier": "all",
                    "slug": "example-mod",
                    "title": "Example Mod",
                    "description": "Description",
                    "author": "Author",
                    "link": "https://example.test/mod",
                    "side": "BOTH",
                    "modloader": "FORGE",
                    "version_mode": "EMBEDDED",
                    "version_pattern": "{minecraft}-{version}",
                    "redistribution_confirmed": True,
                },
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.get_json()["artifact"]["mod_id"], 4)
        self.assertEqual(response.get_json()["versions"][0]["minecraft"], "1.20.1")
        refresh.assert_called_once_with(configured)
        import_project.assert_called_once_with(MAVEN, "9", 7)
        attach.assert_called_once_with(9, 4)

    def test_maven_versions_use_the_generic_integration_listing_route(self):
        external = ExternalVersion(
            MAVEN, "9", "f" * 64, "1.20.1-2.0", "2.0",
            ("1.20.1",), ("FORGE",), "release", None,
            "example.jar", "https://maven.example.test/example.jar", {}, 0,
        )
        with (
            patch("api_write.WriteApiStore.get_modpack", return_value=modpack_row()),
            patch("api_write.WriteApiStore.get_build", return_value=build_row()),
            patch(
                "api_write.WriteApiStore.get_mod",
                return_value=mod_row(
                    integration_provider=MAVEN,
                    integration_project_id="9",
                ),
            ),
            patch("api_write.Mod.get_by_id", return_value=SimpleNamespace(id=4)),
            patch("api_write.Build.get_by_id", return_value=SimpleNamespace(id=3)),
            patch("api_write.ModIntegration.list_versions", return_value=[external]),
            patch("api_write.Modversion.get_integration_version_ids", return_value=set()),
        ):
            response = self.client.get(
                "/api/modpack/example-pack/1.0/mod/example-mod/integration-versions",
                headers=self.headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["versions"][0]["id"], "f" * 64)

    def test_mcil_jar_generation_uses_configured_repository(self):
        with (
            patch("api_write.WriteApiStore.get_mod", return_value=mod_row()),
            patch(
                "api_write.WriteApiStore.get_modversion", return_value=version_row()
            ),
            patch("api_write.Mod.get_by_id", return_value=SimpleNamespace(id=4)),
            patch(
                "api_write.Modversion.get_by_id", return_value=SimpleNamespace(id=5)
            ),
            patch("api_write.MCInstanceJar.create", return_value="b" * 32) as create,
        ):
            response = self.client.post(
                "/api/mod/example-mod/1.0/mcil-jar", headers=self.headers
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["jarmd5"], "b" * 32)
        self.assertEqual(create.call_args.args[2], "https://cdn.example.test/mods/")


class ApiTokenTests(unittest.TestCase):
    def test_authenticate_accepts_sanctum_token_format_and_hashes_secret(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = {
            "token_id": 3,
            "token": ApiToken._digest("secret"),
            "user_id": 7,
            "solder_full": 1,
        }
        with patch(
            "models.api_token.Database.get_connection", return_value=connection
        ):
            principal = ApiToken.authenticate("bearer 3|secret")

        self.assertEqual(principal.user_id, 7)
        self.assertTrue(principal.allows("mods_manage"))
        self.assertTrue(principal.can_access_modpack(12))
        self.assertEqual(cursor.execute.call_args_list[0].args[1], (3, TOKENABLE_TYPE))
        connection.commit.assert_called_once_with()

    def test_read_authentication_does_not_write_last_used_timestamp(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = {
            "token_id": 3,
            "token": ApiToken._digest("secret"),
            "user_id": 7,
            "solder_full": 0,
            "modpacks": "4,9",
        }
        cursor.fetchall.return_value = []
        with patch(
            "models.api_token.Database.get_connection", return_value=connection
        ):
            principal = ApiToken.authenticate("Bearer 3|secret", touch=False)

        self.assertEqual(principal.accessible_modpack_ids, (4, 9))
        self.assertEqual(len(cursor.execute.call_args_list), 2)
        connection.commit.assert_not_called()

    def test_authenticate_rejects_a_wrong_secret(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = {
            "token_id": 3,
            "token": ApiToken._digest("correct"),
            "user_id": 7,
        }
        with patch(
            "models.api_token.Database.get_connection", return_value=connection
        ):
            principal = ApiToken.authenticate("Bearer 3|wrong")

        self.assertIsNone(principal)
        connection.commit.assert_not_called()


class WriteApiPackagingTests(unittest.TestCase):
    def test_production_image_copies_the_write_api_module(self):
        dockerfile = Path(__file__).resolve().parents[1].joinpath("Dockerfile")
        self.assertIn("COPY /api_write.py /app/", dockerfile.read_text())


if __name__ == "__main__":
    unittest.main()
