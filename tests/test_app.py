import hashlib
import io
import importlib
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from tests.environment import configure_test_environment


configure_test_environment()

from models.integration import IntegrationError  # noqa: E402
from models.dashboard import Dashboard  # noqa: E402
from models.mod import DuplicateModError  # noqa: E402


class ApplicationSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Importing asite normally starts the database session-cleanup thread and
        # creates an S3 client. Neither external service belongs in a smoke test.
        with (
            patch("models.session.Session.start_session_loop"),
            patch("boto3.client"),
        ):
            cls.app_module = importlib.import_module("app")

        cls.app_module.app.config.update(TESTING=True)
        cls.client = cls.app_module.app.test_client()

    def test_application_exposes_its_version(self):
        self.assertEqual(self.app_module.__version__, "1.8.0")

    def test_api_blueprint_is_registered(self):
        response = self.client.get("/api/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["api"], "solder.py")

    def test_management_routes_are_registered(self):
        routes = {rule.rule for rule in self.app_module.app.url_map.iter_rules()}

        self.assertIn("/login", routes)
        self.assertIn("/modlibrary", routes)
        self.assertIn("/integrations", routes)
        self.assertIn("/maven", routes)
        self.assertIn("/maven/<int:artifact_id>", routes)
        self.assertIn("/modpackbuild/<int:id>/mcinstance", routes)
        self.assertIn(
            "/modpackbuild/<int:build_id>/integration-versions/<int:mod_id>",
            routes,
        )
        self.assertIn("/api/modpack/<slugstring>/<buildstring>", routes)

    def test_dashboard_is_a_status_view_without_duplicate_action_buttons(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        changed_at = datetime(2026, 9, 12, 10, 30)
        dashboard = {
            "counts": {
                "modpacks": 2,
                "builds": 5,
                "unpublished_builds": 1,
                "mods": 20,
                "modversions": 40,
            },
            "attention": [
                {
                    "title": "Example Pack",
                    "detail": "Latest build is unpublished",
                    "target": "modpack",
                    "target_id": 3,
                }
            ],
            "marked_build": {
                "id": 7,
                "modpack_name": "Example Pack",
                "version": "2.0",
                "minecraft": "1.21.1",
                "modloader": "FABRIC",
                "forge": None,
                "mod_count": 20,
                "is_published": 0,
            },
            "recent": [
                {
                    "item_id": 12,
                    "item_type": "modversion",
                    "title": "Example Mod - 2.0",
                    "detail": "1.21.1 / FABRIC",
                    "integration_provider": "MODRINTH",
                    "updated_at": changed_at,
                }
            ],
        }
        health = [
            {
                "name": "Public repository",
                "ready": True,
                "detail": "Configured",
            }
        ]
        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.Session.get_user_id", return_value=4),
            patch("asite.Dashboard.load", return_value=dashboard) as load,
            patch("asite.Dashboard.repository_health", return_value=health),
            patch("asite.Build.get_marked_build", return_value=7),
            patch("asite.Modpack.get_by_pinned", return_value=[]),
        ):
            response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Needs attention", response.data)
        self.assertIn(b"Latest build is unpublished", response.data)
        self.assertIn(b"Marked build", response.data)
        self.assertIn(b"Recent changes", response.data)
        self.assertIn(b">Modrinth</span>", response.data)
        self.assertIn(b"Repository health", response.data)
        self.assertNotIn(b"<button", response.data)
        self.assertNotIn(b"solderpy.js", response.data)
        load.assert_called_once_with(4)

    def test_dashboard_repository_health_accepts_url_or_local_md5_source(self):
        with tempfile.TemporaryDirectory() as directory:
            local_health = Dashboard.repository_health(
                "https://cdn.example.test/mods/", directory
            )
        remote_health = Dashboard.repository_health(
            "https://cdn.example.test/mods/",
            "https://private.example.test/mods/",
            "bucket",
        )

        self.assertTrue(local_health[0]["ready"])
        self.assertTrue(local_health[1]["ready"])
        self.assertEqual(local_health[1]["detail"], "Local path accessible")
        self.assertTrue(remote_health[1]["ready"])
        self.assertEqual(remote_health[1]["detail"], "Remote source configured")
        self.assertEqual(remote_health[2]["detail"], "Enabled")

    def test_modloader_controls_and_dependency_search_use_existing_ui_styles(self):
        template_root = Path(__file__).resolve().parents[1] / "templates"
        loader_templates = (
            "modlibrary.html",
            "modpack.html",
            "modpackbuild.html",
            "modversion.html",
        )
        for template_name in loader_templates:
            source = (template_root / template_name).read_text(encoding="utf-8")
            self.assertIn(
                '<select class="form-select" name="modloader" id="modloader">',
                source,
            )
            self.assertNotIn('<datalist id="modloaders">', source)
            self.assertNotIn("or 'ANY'", source)

        version_source = (template_root / "modversion.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('id="dependency_search"', version_source)
        self.assertIn(
            "dropdownsearches('dependency_search', 'dependency_dropdown_options', 'dependency_no_results');",
            version_source,
        )
        self.assertIn(
            'id="dependency_dropdown" aria-haspopup="listbox" aria-expanded="false" onclick="togglesearchabledropdown(this);"',
            version_source,
        )
        self.assertIn(
            'type="hidden" name="dependency_mod_id" id="dependency_mod_id"',
            version_source,
        )
        self.assertIn("selectsearchabledropdown(this", version_source)
        self.assertIn(
            '<span class="badge bg-info text-dark">{{dependency.integration_provider|title}}</span>',
            version_source,
        )
        self.assertIn(
            '{{dependency.pretty_name}} ({{dependency.name}}){%if dependency.integration_provider%} '
            '<span class="badge bg-info text-dark">',
            version_source,
        )
        self.assertIn(">Create JAR</button>", version_source)
        self.assertIn('id="createmciljar_submit"', version_source)
        self.assertNotIn('id="table"', version_source)
        self.assertNotIn('name="rehash_url"', version_source)
        self.assertNotIn('name="newmodvermanual_url"', version_source)
        self.assertIn("submitform('newmodvermanual_submit')", version_source)

        build_source = (template_root / "modpackbuild.html").read_text(
            encoding="utf-8"
        )
        self.assertIn('id="build_mod_search"', build_source)
        self.assertIn(
            "dropdownsearches('build_mod_search', 'build_mod_dropdown_options', 'build_mod_no_results');",
            build_source,
        )
        self.assertIn(
            'id="build_mod_dropdown" aria-haspopup="listbox" aria-expanded="false" onclick="togglesearchabledropdown(this);"',
            build_source,
        )
        self.assertIn('type="hidden" name="modnames" id="modnames"', build_source)
        self.assertIn("selectbuildmod(this", build_source)
        self.assertIn(
            '<span class="badge bg-info text-dark">{{lmod.integration_provider|title}}</span>',
            build_source,
        )
        self.assertIn(
            '<span class="badge bg-info text-dark">{{combo.integration_provider|title}}</span>',
            build_source,
        )
        self.assertIn('name="update_all_mods_submit"', build_source)
        self.assertIn('form="update_all_mods_form"', build_source)
        self.assertIn('id="update_all_mods_form"', build_source)
        self.assertIn('<div class="d-flex gap-2 mt-3">', build_source)

        script_source = (
            Path(__file__).resolve().parents[1] / "static" / "js" / "solderpy.js"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "function dropdownsearches(inputId, optionsId, noResultsId)",
            script_source,
        )
        self.assertIn(
            "function hideoptions(optiontoshow, integrationUrl)", script_source
        )
        self.assertIn("function togglesearchabledropdown(toggle)", script_source)
        self.assertNotIn("bootstrap.Dropdown", script_source)
        self.assertNotIn('document.addEventListener("click"', script_source)
        self.assertNotIn("solderpy_js_version", version_source)
        self.assertNotIn("solderpy_js_version", build_source)

    def test_user_can_create_a_write_api_token_from_management(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        created = {
            "id": 9,
            "name": "Deployment",
            "plaintext": "9|copy-this-once",
            "created_at": None,
        }
        with (
            patch("asite.write_api", True),
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.Session.get_user_id", return_value=4),
            patch("asite.ApiToken.create", return_value=created) as create,
            patch("asite.ApiToken.get_all", return_value=[]),
            patch("asite.Build.get_marked_build", return_value=0),
            patch("asite.Modpack.get_by_pinned", return_value=[]),
        ):
            response = self.client.post(
                "/apitokens",
                data={"create_token": "1", "token_name": "Deployment"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"9|copy-this-once", response.data)
        create.assert_called_once_with(4, "Deployment")

    def test_authenticated_user_can_download_mcinstance_export(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        build = SimpleNamespace(modpack_slug="example-pack", version="2.0")
        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Build.get_modpackid_by_id", return_value=3),
            patch(
                "asite.User_modpack.get_user_modpackpermission", return_value=True
            ),
            patch("asite.MCInstanceExport.load", return_value=(build, [])),
            patch(
                "asite.MCInstanceExport.render",
                return_value=io.BytesIO(b"mcinstance archive"),
            ),
        ):
            response = self.client.get("/modpackbuild/7/mcinstance")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"mcinstance archive")
        self.assertEqual(response.mimetype, "application/zip")
        self.assertIn(
            "example-pack-2.0.mcinstance",
            response.headers["Content-Disposition"],
        )

    def test_management_permission_redirects_do_not_trust_referer(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=0),
        ):
            integration_response = self.client.get(
                "/integrations",
                headers={"Referer": "https://attacker.example/redirect"},
            )
            export_response = self.client.get(
                "/modpackbuild/7/mcinstance",
                headers={"Referer": "https://attacker.example/redirect"},
            )

        self.assertEqual(integration_response.status_code, 302)
        self.assertEqual(integration_response.headers["Location"], "/")
        self.assertEqual(export_response.status_code, 302)
        self.assertEqual(export_response.headers["Location"], "/modpacklibrary")

    def test_maven_management_page_renders_with_configured_repositories(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        repository = SimpleNamespace(
            id=2,
            name="Example Maven",
            base_url="https://maven.example.test/releases/",
        )
        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.MavenRepository.get_all", return_value=[repository]),
            patch("asite.MavenArtifact.get_all", return_value=[]),
            patch("asite.Build.get_marked_build", return_value=0),
            patch("asite.Modpack.get_by_pinned", return_value=[]),
        ):
            response = self.client.get("/maven")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Example Maven", response.data)
        self.assertIn(b"Contained in Maven version", response.data)
        self.assertIn(b"Add Maven mod", response.data)

    def test_integration_version_error_does_not_expose_exception_details(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.Session.get_user_id", return_value=4),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Build.get_modpackid_by_id", return_value=3),
            patch(
                "asite.User_modpack.get_user_modpackpermission",
                return_value=True,
            ),
            patch("asite.Mod.get_by_id", return_value=SimpleNamespace(id=9)),
            patch("asite.Build.get_by_id", return_value=SimpleNamespace(id=7)),
            patch(
                "asite.ModIntegration.list_versions",
                side_effect=IntegrationError("private upstream detail"),
            ),
            patch("asite.ErrorPrinter.message") as log_error,
        ):
            response = self.client.get(
                "/modpackbuild/7/integration-versions/9"
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.get_json(),
            {"error": "Compatible provider versions could not be loaded."},
        )
        self.assertNotIn(b"private upstream detail", response.data)
        log_error.assert_called_once()

    def test_authenticated_user_can_create_legacy_mcil_jar(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        mod = SimpleNamespace(id=9, name="example-mod", modtype="MOD")
        version = SimpleNamespace(id=12, mod_id=9, version="1.0", jarmd5=None)
        jar_md5 = "d41d8cd98f00b204e9800998ecf8427e"
        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Mod.get_by_id", return_value=mod),
            patch("asite.Modversion.get_by_id", return_value=version),
            patch("asite.MCInstanceJar.create", return_value=jar_md5) as create,
        ):
            response = self.client.post(
                "/modversion/9",
                data={
                    "createmciljar_submit": "1",
                    "createmciljar_id": "12",
                },
            )

        self.assertEqual(response.status_code, 302)
        arguments = create.call_args.args
        self.assertIs(arguments[0], mod)
        self.assertIs(arguments[1], version)
        self.assertEqual(arguments[2], "https://cdn.example.test/mods/")
        self.assertEqual(arguments[3], "./mods/")

    def test_update_all_mods_imports_latest_provider_version_then_updates_build(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        mod = SimpleNamespace(id=9)
        build = SimpleNamespace(id=7, minecraft="1.21.1", modloader="FABRIC")
        latest = SimpleNamespace(version_id="LATEST")
        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.Session.get_user_id", return_value=4),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Build.get_modpackid_by_id", return_value=3),
            patch("asite.Build.get_by_id", return_value=build),
            patch(
                "asite.User_modpack.get_user_modpackpermission",
                return_value=True,
            ),
            patch(
                "asite.Build_modversion.get_integrated_mods",
                return_value=[
                    {"id": 9, "name": "example", "pretty_name": "Example"}
                ],
            ),
            patch("asite.Mod.get_by_id", return_value=mod),
            patch(
                "asite.ModIntegration.list_versions", return_value=[latest]
            ),
            patch(
                "asite._materialize_integration_version",
                return_value=SimpleNamespace(version=SimpleNamespace(id=33)),
            ) as materialize,
            patch(
                "asite.Build_modversion.update_all_compatible", return_value=2
            ) as update_all,
        ):
            response = self.client.post(
                "/modpackbuild/7", data={"update_all_mods_submit": "1"}
            )

        self.assertEqual(response.status_code, 302)
        materialize.assert_called_once_with(9, "7", "LATEST")
        update_all.assert_called_once_with("7", {9: 33})

    def test_unknown_route_uses_the_solder_404_page(self):
        response = self.client.get("/this-route-does-not-exist")

        self.assertEqual(response.status_code, 404)
        self.assertIn(b"404", response.data)

    def test_unknown_api_route_returns_json(self):
        response = self.client.get("/api/this-route-does-not-exist")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.get_json(), {"error": "Not Found"})

    def test_wrong_api_method_returns_json(self):
        response = self.client.post("/api/modpack")

        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.get_json(), {"error": "Method Not Allowed"})

    def test_duplicate_mod_shows_an_error_and_preserves_the_form(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        form = {
            "pretty_name": "Existing Mod",
            "name": "existing-mod",
            "author": "Test Author",
            "description": "Test description",
            "link": "https://example.test/mod",
            "flexRadioDefault": "BOTH",
            "type": "MOD",
            "notes": "Keep this value",
        }
        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Mod.new", side_effect=DuplicateModError("existing-mod")),
            patch("asite.Build.get_marked_build", return_value=0),
            patch("asite.Modpack.get_by_pinned", return_value=[]),
        ):
            response = self.client.post("/newmod", data=form)

        self.assertEqual(response.status_code, 409)
        self.assertIn(b'existing-mod&#34; already exists', response.data)
        self.assertIn(b'value="Existing Mod"', response.data)
        self.assertIn(b"Keep this value", response.data)

    def test_mod_upload_hashes_are_verified_before_the_database_insert(self):
        jar_data = b"raw jar"
        jar_md5 = hashlib.md5(jar_data, usedforsecurity=False).hexdigest()
        package = io.BytesIO()
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("mods/browser-name.jar", jar_data)
        package_data = package.getvalue()
        package_md5 = hashlib.md5(
            package_data, usedforsecurity=False
        ).hexdigest()

        with tempfile.TemporaryDirectory() as directory:
            with self.client.session_transaction() as flask_session:
                flask_session["token"] = "valid-test-token"
            with (
                patch("asite.Session.verify_session", return_value=True),
                patch("asite.User.get_permission_token", return_value=1),
                patch(
                    "asite.Mod.get_by_id",
                    return_value=SimpleNamespace(
                        name="example-mod", integration_provider=None
                    ),
                ),
                patch("asite.Modversion.new") as new_version,
                patch("asite.UPLOAD_FOLDER", directory),
                patch("asite.R2_BUCKET", None),
            ):
                response = self.client.post(
                    "/modlibrary",
                    data={
                        "form-submit": "1",
                        "modid": "9",
                        "mod": "example-mod",
                        "mcversion": "1.7.10",
                        "version": "1.0",
                        "md5": package_md5,
                        "jarmd5": jar_md5,
                        "filesize": "untrusted client value",
                        "file": (io.BytesIO(package_data), "upload.zip"),
                    },
                    content_type="multipart/form-data",
                )

            self.assertEqual(response.status_code, 302)
            new_version.assert_called_once_with(
                "9",
                "1.7.10-1.0",
                "1.7.10",
                package_md5,
                len(package_data),
                "0",
                "0",
                jar_md5,
                modloader=None,
            )
            artifact_dir = Path(directory, "example-mod")
            self.assertEqual(
                Path(artifact_dir, "example-mod-1.7.10-1.0.jar").read_bytes(),
                jar_data,
            )

    def test_mod_upload_rejects_a_client_jar_hash_mismatch(self):
        package = io.BytesIO()
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("mods/example.jar", b"not empty")
        package_data = package.getvalue()
        package_md5 = hashlib.md5(
            package_data, usedforsecurity=False
        ).hexdigest()

        with tempfile.TemporaryDirectory() as directory:
            with self.client.session_transaction() as flask_session:
                flask_session["token"] = "valid-test-token"
            with (
                patch("asite.Session.verify_session", return_value=True),
                patch("asite.User.get_permission_token", return_value=1),
                patch(
                    "asite.Mod.get_by_id",
                    return_value=SimpleNamespace(
                        name="example-mod", integration_provider=None
                    ),
                ),
                patch("asite.Modversion.new") as new_version,
                patch("asite.UPLOAD_FOLDER", directory),
                patch("asite.R2_BUCKET", None),
            ):
                response = self.client.post(
                    "/modlibrary",
                    data={
                        "form-submit": "1",
                        "modid": "9",
                        "mod": "example-mod",
                        "mcversion": "1.7.10",
                        "version": "1.0",
                        "md5": package_md5,
                        "jarmd5": "d41d8cd98f00b204e9800998ecf8427e",
                        "filesize": str(len(package_data)),
                        "file": (io.BytesIO(package_data), "upload.zip"),
                    },
                    content_type="multipart/form-data",
                )

            self.assertEqual(response.status_code, 302)
            new_version.assert_not_called()
            self.assertFalse(
                Path(
                    directory,
                    "example-mod",
                    "example-mod-1.7.10-1.0.jar",
                ).exists()
            )

    def test_mod_upload_rejects_an_unsafe_stored_slug(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.client.session_transaction() as flask_session:
                flask_session["token"] = "valid-test-token"
            with (
                patch("asite.Session.verify_session", return_value=True),
                patch("asite.User.get_permission_token", return_value=1),
                patch(
                    "asite.Mod.get_by_id",
                    return_value=SimpleNamespace(
                        name="../escape", integration_provider=None
                    ),
                ),
                patch("asite.Modversion.new") as new_version,
                patch("asite.UPLOAD_FOLDER", directory),
                patch("asite.R2_BUCKET", None),
            ):
                response = self.client.post(
                    "/modlibrary",
                    data={
                        "form-submit": "1",
                        "modid": "9",
                        "mod": "../escape",
                        "mcversion": "1.7.10",
                        "version": "1.0",
                        "file": (io.BytesIO(b"unused"), "upload.zip"),
                    },
                    content_type="multipart/form-data",
                )

            self.assertEqual(response.status_code, 302)
            new_version.assert_not_called()
            self.assertFalse((Path(directory).parent / "escape").exists())

    def test_provider_managed_mod_rejects_manual_upload(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch(
                "asite.Mod.get_by_id",
                return_value=SimpleNamespace(
                    name="example-mod", integration_provider="MODRINTH"
                ),
            ),
            patch("asite.Modversion.new") as new_version,
        ):
            response = self.client.post(
                "/modlibrary",
                data={
                    "form-submit": "1",
                    "modid": "9",
                    "mod": "example-mod",
                    "file": (io.BytesIO(b"unused"), "upload.zip"),
                },
                content_type="multipart/form-data",
            )

        self.assertEqual(response.status_code, 302)
        new_version.assert_not_called()

    def test_rehash_ignores_a_client_supplied_repository_url(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        update_hash = Mock()
        version = SimpleNamespace(mod_id=9, update_hash=update_hash)
        mod = SimpleNamespace(id=9, name="example-mod")
        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Mod.get_by_id", return_value=mod),
            patch("asite.Modversion.get_by_id", return_value=version),
        ):
            response = self.client.post(
                "/modversion/9",
                data={
                    "rehash_submit": "1",
                    "rehash_id": "12",
                    "rehash_md5": "a" * 32,
                    "rehash_url": "http://127.0.0.1/private",
                },
            )

        self.assertEqual(response.status_code, 302)
        update_hash.assert_called_once_with(
            "a" * 32,
            "https://cdn.example.test/mods/",
            "example-mod",
        )

if __name__ == "__main__":
    unittest.main()
