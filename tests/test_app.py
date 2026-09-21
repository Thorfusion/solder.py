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
from models.distribution_settings import DistributionSettings  # noqa: E402
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
        self.assertEqual(self.app_module.__version__, "1.10.1")

    def test_management_session_cookie_has_safe_defaults(self):
        self.assertTrue(self.app_module.app.config["SESSION_COOKIE_HTTPONLY"])
        self.assertTrue(self.app_module.app.config["SESSION_COOKIE_SECURE"])
        self.assertEqual(
            self.app_module.app.config["SESSION_COOKIE_SAMESITE"], "Lax"
        )

    def test_login_redirects_an_existing_session(self):
        with self.client.session_transaction() as browser_session:
            browser_session["token"] = "active-token"
        with patch("alogin.Session.verify_session", return_value=True) as verify:
            response = self.client.get("/login")

        self.assertEqual(response.status_code, 302)
        verify.assert_called_once_with("active-token", "127.0.0.1")

    def test_failed_login_does_not_echo_the_password(self):
        with (
            patch("alogin.LoginThrottle.retry_after", return_value=0),
            patch("alogin.LoginThrottle.failure", return_value=0),
            patch("alogin.Passhasher.verify_dummy") as verify_dummy,
            patch("alogin.User.get_by_username", return_value=None),
        ):
            response = self.client.post(
                "/login",
                data={"username": "review-user", "password": "do-not-echo"},
            )

        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("review-user", page)
        self.assertNotIn("do-not-echo", page)
        verify_dummy.assert_called_once_with("do-not-echo")

    def test_login_throttle_stops_password_lookup_and_sets_retry_after(self):
        with (
            patch("alogin.LoginThrottle.retry_after", return_value=73),
            patch("alogin.User.get_by_username") as get_user,
        ):
            response = self.client.post(
                "/login",
                data={"username": "review-user", "password": "guess"},
            )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], "73")
        get_user.assert_not_called()

    def test_modpack_build_mutation_is_scoped_to_the_route_pack(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        selected_pack = SimpleNamespace(
            get_builds=lambda: [SimpleNamespace(id=7, version="1.0")]
        )
        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch(
                "asite.User_modpack.get_user_modpackpermission",
                return_value=True,
            ),
            patch("asite.Modpack.get_by_id", return_value=selected_pack),
            patch("asite.common.update_checkbox") as update_checkbox,
        ):
            response = self.client.post(
                "/modpack/3",
                data={
                    "is_published_submit": "1",
                    "modid": "99",
                    "check": "1",
                },
            )

        self.assertEqual(response.status_code, 302)
        update_checkbox.assert_not_called()

    def test_user_id_prefix_does_not_authorize_another_users_password_change(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.Session.get_user_id", return_value=1),
            patch("asite.User.get_permission_token", return_value=0),
            patch("asite.User.change") as change,
        ):
            response = self.client.post(
                "/userlibrary",
                data={
                    "changeuser_submit": "1",
                    "changeuser_id": "10",
                    "changeuser_password": "replacement",
                },
            )

        self.assertEqual(response.status_code, 302)
        change.assert_not_called()

    def test_manage_user_updates_the_saved_night_mode(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        managed_user = SimpleNamespace(id=9, night_mode=False)
        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.Session.get_user_id", return_value=4),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.User.get_by_id", return_value=managed_user),
            patch(
                "asite.User_modpack.get_all_user_modpacks", return_value=[]
            ),
            patch(
                "asite.User_modpack.get_user_permission",
                return_value=SimpleNamespace(),
            ),
            patch("asite.User.set_night_mode") as set_night_mode,
        ):
            response = self.client.post(
                "/user/9",
                data={"appearance-submit": "1", "night_mode": "1"},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/user/9")
        set_night_mode.assert_called_once_with(9, True, "127.0.0.1", 4)

        user_template = (
            Path(__file__).resolve().parents[1] / "templates" / "user.html"
        ).read_text(encoding="utf-8")
        self.assertIn('name="night_mode"', user_template)
        self.assertIn("managed_user.night_mode", user_template)
        self.assertIn('name="appearance-submit"', user_template)

    def test_manage_user_shows_the_saved_night_mode(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        permissions = SimpleNamespace(
            solder_full=0,
            solder_users=0,
            solder_keys=0,
            solder_clients=0,
            solder_env=0,
            mods_create=0,
            mods_manage=0,
            mods_delete=0,
            modpacks_create=0,
            modpacks_manage=0,
            modpacks_delete=0,
        )
        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch(
                "asite.User.get_by_id",
                return_value=SimpleNamespace(id=9, night_mode=True),
            ),
            patch(
                "asite.User_modpack.get_all_user_modpacks", return_value=[]
            ),
            patch(
                "asite.User_modpack.get_user_permission",
                return_value=permissions,
            ),
            patch("asite.Modpack.get_all", return_value=[]),
            patch("asite.Build.get_marked_build", return_value=0),
            patch("asite.Modpack.get_by_pinned", return_value=[]),
        ):
            response = self.client.get("/user/9")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'id="night_mode" name="night_mode" checked', response.data)
        self.assertIn(b"does not follow the operating-system theme", response.data)

    def test_authenticated_user_theme_does_not_follow_the_operating_system(self):
        from flask import g

        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        def verify_session(token, _ip):
            g.solder_session_token = token
            g.solder_user_id = 4
            g.solder_night_mode = True
            return True

        with (
            patch("asite.Session.verify_session", side_effect=verify_session),
            patch("asite.Build.get_marked_build", return_value=0),
            patch("asite.Modpack.get_by_pinned", return_value=[]),
        ):
            response = self.client.get("/help")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'data-bs-theme="dark"', response.data)
        layout = (
            Path(__file__).resolve().parents[1] / "templates" / "layout.html"
        ).read_text(encoding="utf-8")
        self.assertNotIn("prefers-color-scheme", layout)
        self.assertNotIn("matchMedia", layout)
        theme_css = (
            Path(__file__).resolve().parents[1]
            / "static"
            / "css"
            / "layout.css"
        ).read_text(encoding="utf-8")
        self.assertIn('[data-bs-theme="dark"] .asideright', theme_css)
        self.assertIn('[data-bs-theme="dark"] .table-light', theme_css)

    def test_api_blueprint_is_registered(self):
        response = self.client.get("/api/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["api"], "solder.py")

    def test_management_routes_are_registered(self):
        routes = {rule.rule for rule in self.app_module.app.url_map.iter_rules()}

        self.assertIn("/login", routes)
        self.assertIn("/modlibrary", routes)
        self.assertIn("/integrations", routes)
        self.assertIn("/github", routes)
        self.assertIn("/maven", routes)
        self.assertIn("/maven/<int:artifact_id>", routes)
        self.assertIn(
            "/modversion/<int:mod_id>/manage/<int:version_id>", routes
        )
        self.assertIn("/integrations/manifest", routes)
        self.assertIn("/integrations/manifest/export", routes)
        self.assertIn("/help", routes)
        self.assertIn("/help/<document>", routes)
        self.assertIn("/platform-export-overrides", routes)
        self.assertIn("/platform-export-overrides/export", routes)
        self.assertIn("/publishing", routes)
        self.assertIn("/modpackbuild/<int:id>/solderpy-loader", routes)
        self.assertIn("/modpackbuild/<int:id>/server", routes)
        self.assertIn("/modpackbuild/<int:id>/mcinstance", routes)
        self.assertIn("/modpackbuild/<int:id>/csv", routes)
        self.assertIn("/modpackbuild/<int:id>/packwiz", routes)
        self.assertIn("/modpackbuild/<int:id>/filedirector", routes)
        self.assertIn("/modpackbuild/<int:id>/mrpack", routes)
        self.assertIn("/modpackbuild/<int:id>/curseforge", routes)
        self.assertIn(
            "/modpackbuild/<int:id>/publish/<int:target_id>", routes
        )
        self.assertIn("/modpackbuild/<int:id>/prism", routes)
        self.assertIn(
            "/modpackbuild/<int:build_id>/integration-versions/<int:mod_id>",
            routes,
        )
        self.assertIn("/api/modpack/<slugstring>/<buildstring>", routes)
        self.assertIn(
            "/api/modpack/<slugstring>/<buildstring>/bootstrap", routes
        )
        self.assertIn(
            "/packwiz/<pack_slug>/<selector>/pack.toml", routes
        )
        self.assertIn(
            "/filedirector/<pack_slug>/<selector>/<bundle_name>.bundle.json",
            routes,
        )

    def test_publishing_page_only_loads_the_current_users_configuration(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Session.get_user_id", return_value=42),
            patch(
                "asite.PlatformPublishing.get_accounts", return_value=[]
            ) as accounts,
            patch(
                "asite.PlatformPublishing.get_targets", return_value=[]
            ) as targets,
            patch(
                "asite.PlatformPublishing.get_recent_runs", return_value=[]
            ) as runs,
            patch("asite.Modpack.get_all_for_user", return_value=[]) as packs,
            patch("asite.render_template", return_value="publishing"),
        ):
            response = self.client.get("/publishing")

        self.assertEqual(response.status_code, 200)
        accounts.assert_called_once_with(42)
        targets.assert_called_once_with(42)
        runs.assert_called_once_with(42)
        packs.assert_called_once_with(42)

    def test_navbar_help_links_to_the_project_documentation_index(self):
        layout = (
            Path(__file__).resolve().parents[1] / "templates" / "layout.html"
        ).read_text(encoding="utf-8")

        self.assertIn('data-bs-target="#settings-collapse"', layout)
        self.assertIn("url_for('asite.help_index')", layout)
        self.assertIn(">Help</a>", layout)
        self.assertNotIn("help-collapse", layout)
        self.assertNotIn(">Manual MD5</a>", layout)
        self.assertNotIn("solder.py#installation", layout)
        self.assertNotIn("github.com/Thorfusion/solder.py/blob", layout)

    def test_flash_messages_are_bottom_centered_and_auto_dismiss(self):
        project_root = Path(__file__).resolve().parents[1]
        layout = (project_root / "templates" / "layout.html").read_text(
            encoding="utf-8"
        )
        layout_css = (project_root / "static" / "css" / "layout.css").read_text(
            encoding="utf-8"
        )
        flash_script = (
            project_root / "static" / "js" / "flash-messages.js"
        ).read_text(encoding="utf-8")

        self.assertIn("get_flashed_messages(with_categories=true)", layout)
        self.assertIn('class="flash-message-container"', layout)
        self.assertIn("data-flash-message", layout)
        self.assertIn("js/flash-messages.js", layout)
        self.assertLess(
            layout.index("{%block aside%}"),
            layout.index("flash-message-container"),
        )
        self.assertIn("bottom: 1.5rem", layout_css)
        self.assertIn("left: 50%", layout_css)
        self.assertIn("bootstrap.Alert.getOrCreateInstance", flash_script)
        self.assertIn("5000", flash_script)

    def test_direct_publish_uses_the_shared_rendered_archive(self):
        target = SimpleNamespace(
            id=12,
            provider="MODRINTH",
            provider_label="Modrinth",
            account_name="Release account",
        )
        build = SimpleNamespace(id=7, modpack_id=3, version="2.0")
        archive = io.BytesIO(b"generated mrpack")
        result = SimpleNamespace(
            remote_file_id="remote-version",
            version_number="2.0-Patch-1",
        )
        publication_state = SimpleNamespace(
            locked=False,
            published=True,
            version_number="2.0-Patch-1",
            patch_number=1,
        )

        with (
            patch(
                "asite._platform_export_build",
                return_value=((build, [SimpleNamespace()]), None),
            ),
            patch(
                "asite.PlatformPublishing.get_targets",
                return_value=[target],
            ),
            patch(
                "asite.PlatformPublishing.get_build_publication_states",
                return_value={12: publication_state},
            ) as get_states,
            patch(
                "asite.DistributionSettings.is_enabled", return_value=True
            ),
            patch(
                "asite._render_native_platform_archive",
                return_value=(
                    archive,
                    "example-2.0.mrpack",
                    "application/x-modrinth-modpack+zip",
                ),
            ) as render_archive,
            patch(
                "asite.PlatformPublishing.publish", return_value=result
            ) as publish,
            patch("asite.Session.get_user_id", return_value=4),
        ):
            with self.client.session_transaction() as flask_session:
                flask_session["token"] = "valid-test-token"
            response = self.client.post(
                "/modpackbuild/7/publish/12",
                data={
                    "release_type": "beta",
                    "changelog": "Changes",
                    "source": "hybrid",
                },
            )

        self.assertEqual(response.status_code, 302)
        get_states.assert_called_once_with((target,), 7, "2.0", 4)
        render_archive.assert_called_once_with(
            "MODRINTH",
            build,
            [SimpleNamespace()],
            7,
            version_override="2.0-Patch-1",
        )
        publish.assert_called_once_with(
            12,
            3,
            build,
            archive,
            "example-2.0.mrpack",
            "beta",
            "Changes",
            4,
            expected_version="2.0-Patch-1",
        )

    def test_patch_archive_keeps_hosted_loader_on_the_real_solder_build(self):
        asite_module = importlib.import_module("asite")
        build = SimpleNamespace(
            id=7,
            version="2.0",
            modpack_slug="example-pack",
        )
        with (
            self.client.application.test_request_context(
                "/modpackbuild/7/publish/12",
                method="POST",
                data={
                    "source": "hybrid",
                    "delivery": "hosted",
                    "selector": "build",
                    "modrinth_downloader": "solderpyloader:release-id",
                },
            ),
            patch(
                "asite.PlatformPackExport.render_mrpack",
                return_value=io.BytesIO(b"archive"),
            ) as render,
            patch("asite._downloader_export_enabled", return_value=True),
            patch("asite.PlatformExportOverride.get_enabled", return_value=[]),
            patch(
                "asite.AdvancedOptional.get_active_groups_for_packages",
                return_value=[],
            ),
        ):
            asite_module._render_native_platform_archive(
                "MODRINTH",
                build,
                [],
                7,
                version_override="2.0-Patch-1",
            )

        self.assertEqual(render.call_args.args[0].version, "2.0-Patch-1")
        self.assertEqual(render.call_args.kwargs["selector"], "2.0")

    def test_help_pages_render_bundled_documentation(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.Build.get_marked_build", return_value=0),
            patch("asite.Modpack.get_by_pinned", return_value=[]),
        ):
            index = self.client.get("/help")
            document = self.client.get("/help/github-config")

        self.assertEqual(index.status_code, 200)
        self.assertIn(b"Documentation included with this solder.py version", index.data)
        self.assertIn(b"GitHub config repositories", index.data)
        self.assertEqual(document.status_code, 200)
        self.assertIn(b"GitHub config repository guide", document.data)
        self.assertIn(b".solderpyignore", document.data)
        self.assertNotIn(b"github.com/Thorfusion/solder.py/blob", document.data)

    def test_github_page_links_an_existing_config(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        mod = SimpleNamespace(
            id=9,
            name="lost-era-config",
            pretty_name="Lost Era Config",
            modtype="CONFIG",
            integration_provider=None,
            integration_project_id=None,
        )
        linked = SimpleNamespace(id=9, pretty_name="Lost Era Config")
        project = SimpleNamespace(author="DrParadox7", slug="Lost-Era-Modpack")
        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.Session.get_user_id", return_value=7),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Mod.get_by_id", return_value=mod),
            patch(
                "asite.ModIntegration.link_existing",
                return_value=(linked, project),
            ) as link,
        ):
            response = self.client.post(
                "/github",
                data={
                    "mod_id": "9",
                    "repository": "DrParadox7/Lost-Era-Modpack",
                    "link_repository": "1",
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/modversion/9")
        link.assert_called_once_with(
            mod,
            "GITHUB",
            "DrParadox7/Lost-Era-Modpack",
            7,
        )

    def test_github_page_lists_only_available_config_entries(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        available = SimpleNamespace(
            id=3,
            name="available-config",
            pretty_name="Available Config",
            modtype="CONFIG",
            integration_provider=None,
            integration_project_id=None,
        )
        ordinary_mod = SimpleNamespace(
            id=4,
            name="ordinary-mod",
            pretty_name="Ordinary Mod",
            modtype="MOD",
            integration_provider=None,
            integration_project_id=None,
        )
        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.Session.get_user_id", return_value=7),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Mod.get_all", return_value=[ordinary_mod, available]),
            patch("asite.provider_for_user"),
            patch("asite.Build.get_marked_build", return_value=0),
            patch("asite.Modpack.get_by_pinned", return_value=[]),
        ):
            response = self.client.get("/github")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Available Config", response.data)
        self.assertNotIn(b"Ordinary Mod", response.data)

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
        self.assertNotIn(b"<button", response.data.split(b"</main>", 1)[0])
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
        project_root = Path(__file__).resolve().parents[1]
        layout_source = (template_root / "layout.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("js/bootstrap.bundle.min.js", layout_source)
        self.assertNotIn("js/bootstrap.min.js", layout_source)
        bootstrap_bundle = (
            project_root / "static" / "js" / "bootstrap.bundle.min.js"
        ).read_text(encoding="utf-8")
        self.assertIn("Bootstrap v5.3.8", bootstrap_bundle[:500])
        self.assertIn("popperGenerator", bootstrap_bundle)

        loader_templates = (
            "modpack.html",
            "modpackbuild.html",
        )
        for template_name in loader_templates:
            source = (template_root / template_name).read_text(encoding="utf-8")
            self.assertIn(
                '<select class="form-select" name="modloader" id="modloader">',
                source,
            )
            self.assertNotIn('<datalist id="modloaders">', source)
            self.assertNotIn("or 'ANY'", source)

        version_loader_source = (template_root / "modversion.html").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "{{modloader_dropdown(",
            version_loader_source,
        )
        self.assertNotIn(
            "Select every compatible loader; no selection supports all.",
            version_loader_source,
        )
        self.assertNotIn('name="modloader" id="modloader" multiple', version_loader_source)
        modlibrary_source = (template_root / "modlibrary.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("{{modloader_dropdown(", modlibrary_source)
        self.assertIn("input_name='modloader'", modlibrary_source)
        self.assertNotIn('name="modloader" id="modloader">', modlibrary_source)
        self.assertNotIn(
            "Select every compatible loader; no selection supports all.",
            modlibrary_source,
        )
        loader_dropdown_source = (
            template_root / "_modloader_dropdown.html"
        ).read_text(encoding="utf-8")
        self.assertIn('data-bs-toggle="dropdown"', loader_dropdown_source)
        self.assertIn('data-bs-auto-close="outside"', loader_dropdown_source)
        self.assertIn('type="checkbox"', loader_dropdown_source)
        self.assertIn("data-multiselect-option", loader_dropdown_source)

        for template_name in ("modpack.html", "modpackbuild.html"):
            source = (template_root / template_name).read_text(encoding="utf-8")
            self.assertIn(
                '<input type="text" class="form-control" name="min_java"',
                source,
            )
            self.assertNotIn(
                '<select class="form-select" name="min_java"', source
            )
            self.assertIn(
                '<select class="form-select" name="java_runtime"', source
            )
            self.assertNotIn(">Advanced</span>", source)

        version_source = (template_root / "modversion.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("Versions available from", version_source)
        self.assertIn("import_integration_version_submit", version_source)
        self.assertIn("ZIP / JAR", version_source)
        self.assertNotIn("Download URL", version_source)
        self.assertNotIn("versions.jarfilesize", version_source)
        new_mod_source = (template_root / "newmod.html").read_text(
            encoding="utf-8"
        )
        library_source = (template_root / "modlibrary.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("LAUNCHER (MODLOADER)", version_source)
        self.assertIn("LAUNCHER (MODLOADER)", new_mod_source)
        self.assertIn("Launcher (modloader)", library_source)
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
            "integration_badge(dependency.integration_provider, dependency.integration_label)",
            version_source,
        )
        self.assertNotIn(">Verify / Create JAR</button>", version_source)
        self.assertNotIn(">Verify ZIP</button>", version_source)
        self.assertNotIn(">Create JAR</button>", version_source)
        self.assertNotIn(">Verify JAR</button>", version_source)
        self.assertNotIn(">Rehash</button>", version_source)
        self.assertNotIn('name="rehash_md5"', version_source)
        self.assertNotIn('id="createmciljar_submit"', version_source)
        manage_version_source = (template_root / "manage_modversion.html").read_text(
            encoding="utf-8"
        )
        self.assertIn(">Verify ZIP</button>", manage_version_source)
        self.assertIn("Verify JAR", manage_version_source)
        self.assertIn("Create JAR", manage_version_source)
        self.assertIn('name="verify_zip_submit"', manage_version_source)
        self.assertIn('name="jar_action_submit"', manage_version_source)
        self.assertNotIn('id="table"', version_source)
        self.assertNotIn('name="rehash_url"', version_source)
        self.assertNotIn('name="newmodvermanual_url"', version_source)
        self.assertIn("submitform('newmodvermanual_submit')", version_source)
        self.assertIn(
            "mod.integration_provider or not legacy_modversion_adding",
            version_source,
        )
        self.assertIn('name="link_modrinth_submit"', version_source)
        self.assertIn('name="link_github_submit"', version_source)
        self.assertIn("mod.modtype == 'CONFIG'", version_source)
        self.assertIn('name="sync_github_ref_submit"', version_source)
        self.assertIn('name="sync_github_tag_submit"', version_source)

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
        self.assertIn("modtype_badge(lmod.modtype)", build_source)
        self.assertIn("modtype_badge(combo.modtype)", build_source)
        self.assertIn(
            "integration_badge(lmod.integration_provider, lmod.integration_label)",
            build_source,
        )
        self.assertIn(
            "integration_badge(combo.integration_provider, combo.integration_label)",
            build_source,
        )
        badge_source = (template_root / "_badges.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("provider|upper == 'MODRINTH'", badge_source)
        self.assertIn("bg-success", badge_source)
        self.assertIn("bg-secondary", badge_source)
        self.assertIn("LAUNCHER (MODLOADER)", badge_source)
        self.assertIn("modtype_badge(mod.modtype)", library_source)
        self.assertIn('name="update_all_mods_submit"', build_source)
        self.assertIn('form="update_all_mods_form"', build_source)
        self.assertIn('id="update_all_mods_form"', build_source)
        self.assertIn("check_updates=1", build_source)
        self.assertIn("combo.update_available", build_source)
        self.assertIn("build-name-cell", build_source)
        self.assertIn("build-update-indicator", build_source)
        self.assertIn("&#x2B07;&#xFE0E;", build_source)
        self.assertIn("{%if not combo.update_available%} invisible{%endif%}", build_source)
        self.assertLess(
            build_source.index("build-update-indicator"),
            build_source.index('<td class="tablewidth20">'),
        )
        self.assertLess(
            build_source.index("Check for updates"),
            build_source.index("Advanced optionals"),
        )
        self.assertIn("{%if optional_mode == 1%}List in advanced optional page{%else%}Optional{%endif%}", build_source)
        self.assertIn('name="newoptional"', build_source)
        self.assertIn('name="newadvancedoptional"', build_source)
        self.assertIn("{%if combo.optional%}checked{%endif%}", build_source)
        self.assertIn("{%if combo.advanced_listed%}checked{%endif%}", build_source)
        self.assertIn('<div class="d-flex gap-2 mt-3">', build_source)
        self.assertIn(
            "url_for('asite.modpackbuild', id=packbuild.id, export=1)",
            build_source,
        )
        self.assertIn('>Export</a>', build_source)
        self.assertIn("{%if export_requested%}", build_source)
        self.assertIn('document.body.appendChild(exportModalElement)', build_source)
        self.assertIn("downloader_select('modrinth_downloader'", build_source)
        self.assertNotIn("downloader_select('solderpy_loader_downloader'", build_source)
        self.assertIn("downloader_select('curseforge_downloader'", build_source)
        self.assertIn("{{export_settings(packbuild)}}", build_source)
        self.assertIn('id="export_destination"', build_source)
        self.assertIn("function updateexportdestination(select)", build_source)
        self.assertIn('data-label="Export CSV">CSV</option>', build_source)
        self.assertEqual(build_source.count('name="source"'), 0)
        self.assertEqual(build_source.count('name="forge_version"'), 0)
        self.assertEqual(build_source.count('name="delivery"'), 0)
        self.assertEqual(build_source.count('name="selector"'), 0)
        self.assertNotIn('>Export mod list</button>', build_source)
        self.assertNotIn('>MCIL</a>', build_source)
        self.assertLess(
            build_source.index('id="add_mod_submit"'),
            build_source.index('id="export_modpack_modal"'),
        )
        self.assertIn(
            '<div class="mb-3 gridswrapper" id="build_version_fields">',
            build_source,
        )

        modpack_source = (template_root / "modpack.html").read_text(
            encoding="utf-8"
        )
        self.assertIn(">Export</button>", modpack_source)
        self.assertIn("export=1", modpack_source)
        self.assertNotIn("export_mcinstance", modpack_source)

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
        self.assertIn(
            "function updatemultiselectdropdown(", script_source
        )
        self.assertNotIn("function filterconfigdelivery", script_source)
        self.assertNotIn("bootstrap.Dropdown", script_source)
        self.assertNotIn('document.addEventListener("click"', script_source)
        self.assertNotIn("solderpy_js_version", version_source)
        self.assertNotIn("solderpy_js_version", build_source)

        export_fields_source = (template_root / "_export_fields.html").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "{{downloader.key}}:{{release.selector}}", export_fields_source
        )
        self.assertIn(
            "downloader.key == 'solderpyloader' and release.default",
            export_fields_source,
        )
        self.assertEqual(export_fields_source.count('name="source"'), 1)
        self.assertEqual(export_fields_source.count('name="forge_version"'), 1)
        self.assertEqual(export_fields_source.count('name="delivery"'), 2)
        self.assertIn('value="hosted" checked', export_fields_source)
        self.assertIn("Include in archive", export_fields_source)
        self.assertIn("Web hosted", export_fields_source)
        self.assertEqual(export_fields_source.count('name="selector"'), 1)
        self.assertIn("MCInstance Loader stores its configuration", export_fields_source)
        self.assertIn("Hybrid (Modrinth/Curse where mapped)", export_fields_source)
        self.assertIn("Solder API only", export_fields_source)
        self.assertIn("numbered patch", export_fields_source)

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

    def test_environment_settings_can_toggle_public_distribution_formats(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch(
                "asite.DistributionSettings.get_all",
                return_value={
                    "mcil_enabled": True,
                    "solderpy_loader_enabled": True,
                    "packwiz_enabled": True,
                    "filedirector_enabled": False,
                    "modpack_director_enabled": True,
                    "mrpack_enabled": False,
                    "curseforge_export_enabled": False,
                    "prism_export_enabled": True,
                },
            ),
            patch("asite.Build.get_marked_build", return_value=0),
            patch("asite.Modpack.get_by_pinned", return_value=[]),
        ):
            response = self.client.get("/mainsettings")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Distribution formats", response.data)
        self.assertIn(b'id="mcil_enabled"', response.data)
        self.assertIn(b'id="solderpy_loader_enabled"', response.data)
        self.assertIn(b'id="packwiz_enabled"', response.data)
        self.assertIn(b'id="filedirector_enabled"', response.data)
        self.assertIn(b'id="modpack_director_enabled"', response.data)
        self.assertIn(b'id="mrpack_enabled"', response.data)
        self.assertIn(b'id="curseforge_export_enabled"', response.data)
        self.assertIn(b'id="prism_export_enabled"', response.data)
        self.assertIn(b"API-only mode", response.data)
        self.assertLess(
            response.data.index(b'id="manual_md5_hashing"'),
            response.data.index(b"DEBUG ENV"),
        )

        settings_source = (
            Path(__file__).resolve().parents[1]
            / "templates"
            / "mainsettings.html"
        ).read_text(encoding="utf-8")
        self.assertGreater(
            settings_source.index('id="distribution_settings"'),
            settings_source.index("{%block aside%}"),
        )
        self.assertIn('<div class="row g-3 mb-3">', settings_source)
        self.assertEqual(settings_source.count('class="col-md-6"'), 2)

        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.DistributionSettings.update_exports") as update,
        ):
            saved = self.client.post(
                "/mainsettings",
                data={
                    "export_settings_submit": "1",
                    "solderpy_loader_enabled": "on",
                    "filedirector_enabled": "on",
                    "modpack_director_enabled": "on",
                },
            )

        self.assertEqual(saved.status_code, 302)
        self.assertEqual(saved.headers["Location"], "/mainsettings")
        update.assert_called_once_with(
            mcil=False,
            solderpy_loader=True,
            packwiz=False,
            filedirector=True,
            modpack_director=True,
            mrpack=False,
            curseforge=False,
            prism=False,
        )

    def test_environment_settings_can_scan_legacy_jar_packages(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        result = SimpleNamespace(
            scanned_mods=4,
            scanned_versions=7,
            converted_mods=2,
            converted_versions=5,
            failures=("mixed-package: contains additional files",),
        )
        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch(
                "asite.MCInstanceJar.promote_jar_only_none_mods",
                return_value=result,
            ) as promote,
            patch("asite.ErrorPrinter.message") as report,
        ):
            response = self.client.post(
                "/mainsettings",
                data={"convert_none_mods_submit": "1"},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/mainsettings")
        promote.assert_called_once()
        report.assert_called_once_with(
            "Legacy JAR scan left a mod unchanged",
            "mixed-package: contains additional files",
        )
        with self.client.session_transaction() as flask_session:
            flashes = flask_session.get("_flashes", [])
        self.assertTrue(
            any("2 of 4 NONE mods converted" in message for _, message in flashes)
        )

    def test_export_override_settings_render_and_toggle_tx_loader(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        override = SimpleNamespace(
            id=1,
            name="TX Loader",
            modrinth_project_id="eh8us8FY",
            curseforge_project_id=706505,
            side="CLIENT",
            enabled=False,
            built_in=True,
        )
        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch(
                "asite.PlatformExportOverride.get_all",
                return_value=[override],
            ),
            patch("asite.Build.get_marked_build", return_value=0),
            patch("asite.Modpack.get_by_pinned", return_value=[]),
        ):
            response = self.client.get("/platform-export-overrides")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Modrinth-CurseForge sync", response.data)
        self.assertIn(b"How CurseForge matching works", response.data)
        self.assertIn(b"No CurseForge file metadata", response.data)
        self.assertIn(b"same filename and filesize", response.data)
        self.assertIn(b"CurseForge filename or display name", response.data)
        self.assertIn(b"TX Loader", response.data)
        self.assertIn(b"eh8us8FY", response.data)
        self.assertIn(b"706505", response.data)
        self.assertIn(b"submitecheckedpress", response.data)
        self.assertNotIn(b">Save</button>", response.data)

        template = (
            Path(__file__).resolve().parents[1]
            / "templates"
            / "platform_export_overrides.html"
        ).read_text(encoding="utf-8")
        main, aside = template.split("{%block aside%}", 1)
        self.assertNotIn("Export list", main)
        self.assertIn("Export list", aside)
        self.assertIn('name="sync_manifest"', aside)
        self.assertLess(aside.index("Add mapping"), aside.index("Export list"))

        layout = (
            Path(__file__).resolve().parents[1] / "templates" / "layout.html"
        ).read_text(encoding="utf-8")
        self.assertIn(">Modrinth-Curse sync</a>", layout)
        self.assertNotIn(">Modrinth-CurseForge sync</a>", layout)

        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.PlatformExportOverride.set_enabled") as enable,
        ):
            saved = self.client.post(
                "/platform-export-overrides",
                data={
                    "set_override_enabled": "1",
                    "override_id": "1",
                    "override_enabled": "1",
                },
            )

        self.assertEqual(saved.status_code, 302)
        self.assertEqual(saved.headers["Location"], "/platform-export-overrides")
        enable.assert_called_once_with("1", True)

    def test_export_override_can_bypass_solder_only_mode(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch(
                "asite.PlatformExportOverride.set_override_solder_only"
            ) as update,
        ):
            saved = self.client.post(
                "/platform-export-overrides",
                data={
                    "set_override_solder_only": "1",
                    "override_id": "1",
                    "override_solder_only": "1",
                },
            )

        self.assertEqual(saved.status_code, 302)
        update.assert_called_once_with("1", True)

    def test_export_override_add_resolves_modrinth_project(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        project = SimpleNamespace(
            title="TX Loader",
            project_id="eh8us8FY",
            side="CLIENT",
        )
        provider = Mock()
        provider.get_project.return_value = project
        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.ModrinthProvider", return_value=provider),
            patch("asite.PlatformExportOverride.create") as create,
        ):
            saved = self.client.post(
                "/platform-export-overrides",
                data={
                    "create_override": "1",
                    "modrinth_project": "https://modrinth.com/mod/tx-loader",
                    "curseforge_project_id": "706505",
                    "side": "CLIENT",
                },
            )

        self.assertEqual(saved.status_code, 302)
        provider.get_project.assert_called_once_with(
            "https://modrinth.com/mod/tx-loader"
        )
        create.assert_called_once_with(
            "TX Loader", "eh8us8FY", "706505", "CLIENT", False
        )

    def test_sync_mapping_list_can_be_exported(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch(
                "asite.PlatformExportOverride.render_manifest",
                return_value=io.BytesIO(b'{"format":"sync"}'),
            ) as render,
        ):
            response = self.client.get("/platform-export-overrides/export")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/json")
        self.assertIn(
            "solder.py-modrinth-curseforge-sync.json",
            response.headers["Content-Disposition"],
        )
        render.assert_called_once_with()

    def test_authenticated_user_can_download_mcinstance_export(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        build = SimpleNamespace(modpack_slug="example-pack", version="2.0")
        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.DistributionSettings.is_enabled", return_value=True),
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

    def test_mcinstance_export_is_unavailable_when_disabled(self):
        with (
            patch("asite.DistributionSettings.is_enabled", return_value=False),
            patch("asite.render_template", return_value="disabled"),
            patch("asite.Session.verify_session") as verify_session,
        ):
            response = self.client.get("/modpackbuild/7/mcinstance")

        self.assertEqual(response.status_code, 404)
        verify_session.assert_not_called()

    def test_authenticated_user_can_download_solderpy_loader_export(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        build = SimpleNamespace(modpack_slug="example-pack", version="2.0")
        with (
            patch("asite.DistributionSettings.is_enabled", return_value=True),
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Build.get_modpackid_by_id", return_value=3),
            patch(
                "asite.User_modpack.get_user_modpackpermission",
                return_value=True,
            ),
            patch("asite.MCInstanceExport.load", return_value=(build, [])),
            patch(
                "asite.PlatformPackExport.render_solderpy_loader",
                return_value=io.BytesIO(b"solderpy loader archive"),
            ) as render,
        ):
            response = self.client.get(
                "/modpackbuild/7/solderpy-loader?selector=recommended"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"solderpy loader archive")
        self.assertEqual(response.mimetype, "application/zip")
        self.assertIn(
            "example-pack-2.0-solderpy-loader.zip",
            response.headers["Content-Disposition"],
        )
        self.assertEqual(render.call_args.kwargs["selector"], "recommended")

    def test_authenticated_user_can_download_server_export(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        build = SimpleNamespace(modpack_slug="example-pack", version="2.0")
        packages = [SimpleNamespace(name="crucible")]
        with (
            patch("asite.DistributionSettings.is_enabled", return_value=True),
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Build.get_modpackid_by_id", return_value=3),
            patch(
                "asite.User_modpack.get_user_modpackpermission",
                return_value=True,
            ),
            patch(
                "asite.MCInstanceExport.load",
                return_value=(build, packages),
            ),
            patch(
                "asite.PlatformPackExport.render_server",
                return_value=io.BytesIO(b"server archive"),
            ) as render,
        ):
            response = self.client.get(
                "/modpackbuild/7/server?"
                "server_downloader=solderpyloader:loader-version&"
                "selector=latest"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"server archive")
        self.assertEqual(response.mimetype, "application/zip")
        self.assertIn(
            "example-pack-2.0-server.zip",
            response.headers["Content-Disposition"],
        )
        self.assertEqual(render.call_args.args[1], packages)
        self.assertEqual(
            render.call_args.args[2], "solderpyloader:loader-version"
        )
        self.assertEqual(render.call_args.kwargs["selector"], "latest")

    def test_authenticated_user_can_download_prism_instance_export(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        build = SimpleNamespace(modpack_slug="example-pack", version="2.0")
        with (
            patch("asite.DistributionSettings.is_enabled", return_value=True),
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Build.get_modpackid_by_id", return_value=3),
            patch(
                "asite.User_modpack.get_user_modpackpermission",
                return_value=True,
            ),
            patch("asite.MCInstanceExport.load", return_value=(build, [])),
            patch(
                "asite.PlatformPackExport.render_prism",
                return_value=io.BytesIO(b"prism archive"),
            ) as render,
        ):
            response = self.client.get(
                "/modpackbuild/7/prism?"
                "prism_downloader=filedirector:V3i1l5tv&"
                "source=hybrid&delivery=hosted&selector=latest"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"prism archive")
        self.assertEqual(response.mimetype, "application/zip")
        self.assertIn(
            "example-pack-2.0-prism.zip",
            response.headers["Content-Disposition"],
        )
        self.assertEqual(
            render.call_args.kwargs["downloader"],
            "filedirector:V3i1l5tv",
        )
        self.assertEqual(render.call_args.kwargs["source_mode"], "hybrid")
        self.assertEqual(render.call_args.kwargs["delivery"], "hosted")
        self.assertEqual(render.call_args.kwargs["selector"], "latest")

    def test_platform_exports_reject_disabled_mcil_downloader(self):
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
            patch(
                "asite.DistributionSettings.is_enabled",
                side_effect=lambda name: name != DistributionSettings.MCIL,
            ),
            patch("asite.MCInstanceExport.load", return_value=(build, [])),
            patch("asite.PlatformPackExport.render_mrpack") as render_mrpack,
            patch("asite.PlatformPackExport.render_curseforge") as render_curseforge,
            patch("asite.PlatformPackExport.render_prism") as render_prism,
        ):
            mrpack = self.client.get(
                "/modpackbuild/7/mrpack?modrinth_downloader=mcil:6Qimuf4A"
            )
            curseforge = self.client.get(
                "/modpackbuild/7/curseforge?curseforge_downloader=mcil:4920730"
            )
            prism = self.client.get(
                "/modpackbuild/7/prism?prism_downloader=mcil:6Qimuf4A"
            )

        self.assertEqual(mrpack.status_code, 302)
        self.assertEqual(curseforge.status_code, 302)
        self.assertEqual(prism.status_code, 302)
        self.assertTrue(mrpack.headers["Location"].endswith("/modpackbuild/7"))
        self.assertTrue(curseforge.headers["Location"].endswith("/modpackbuild/7"))
        self.assertTrue(prism.headers["Location"].endswith("/modpackbuild/7"))
        render_mrpack.assert_not_called()
        render_curseforge.assert_not_called()
        render_prism.assert_not_called()

    def test_authenticated_user_can_download_mrpack_export(self):
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
            patch(
                "asite.DistributionSettings.is_enabled", return_value=True
            ),
            patch("asite.MCInstanceExport.load", return_value=(build, [])),
            patch(
                "asite.PlatformExportOverride.get_enabled",
                return_value=["override"],
            ),
            patch(
                "asite.PlatformPackExport.render_mrpack",
                return_value=io.BytesIO(b"mrpack archive"),
            ) as render,
        ):
            response = self.client.get(
                "/modpackbuild/7/mrpack?modrinth_downloader=mcil:6Qimuf4A"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"mrpack archive")
        self.assertEqual(
            response.mimetype, "application/x-modrinth-modpack+zip"
        )
        self.assertIn(
            "example-pack-2.0.mrpack",
            response.headers["Content-Disposition"],
        )
        self.assertEqual(render.call_args.args[2], "mcil:6Qimuf4A")
        self.assertEqual(render.call_args.kwargs["export_overrides"], ["override"])

    def test_curseforge_export_passes_the_installation_api_key(self):
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
            patch("asite.DistributionSettings.is_enabled", return_value=True),
            patch("asite.MCInstanceExport.load", return_value=(build, [])),
            patch(
                "asite.PlatformExportOverride.get_enabled",
                return_value=["override"],
            ),
            patch("asite.curseforge_api_key", "secret"),
            patch(
                "asite.PlatformPackExport.render_curseforge",
                return_value=io.BytesIO(b"curseforge archive"),
            ) as render,
        ):
            response = self.client.get(
                "/modpackbuild/7/curseforge?curseforge_downloader=mcil:4920730"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"curseforge archive")
        self.assertEqual(render.call_args.kwargs["curseforge_api_key"], "secret")
        self.assertEqual(render.call_args.kwargs["export_overrides"], ["override"])

    def test_authenticated_user_can_download_bundled_packwiz(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        build = SimpleNamespace(
            modpack_slug="example-pack",
            version="2.0",
            is_published=True,
            private=False,
        )
        with (
            patch("asite.DistributionSettings.is_enabled", return_value=True),
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Build.get_modpackid_by_id", return_value=3),
            patch(
                "asite.User_modpack.get_user_modpackpermission", return_value=True
            ),
            patch("asite.MCInstanceExport.load", return_value=(build, [])),
            patch(
                "asite.PlatformPackExport.render_packwiz",
                return_value=io.BytesIO(b"packwiz archive"),
            ) as render,
        ):
            response = self.client.get(
                "/modpackbuild/7/packwiz?delivery=bundled&source=hybrid"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"packwiz archive")
        self.assertIn("example-pack-2.0-packwiz.zip", response.headers["Content-Disposition"])
        self.assertEqual(render.call_args.kwargs["source_mode"], "hybrid")

    def test_authenticated_user_can_export_modpack_director(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        build = SimpleNamespace(
            modpack_slug="example-pack",
            version="2.0",
            is_published=True,
            private=False,
        )
        with (
            patch("asite.DistributionSettings.is_enabled", return_value=True),
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Build.get_modpackid_by_id", return_value=3),
            patch(
                "asite.User_modpack.get_user_modpackpermission",
                return_value=True,
            ),
            patch("asite.MCInstanceExport.load", return_value=(build, [])),
            patch(
                "asite.PlatformPackExport.render_modpack_director",
                return_value=io.BytesIO(b"modpack director archive"),
            ) as render,
        ):
            response = self.client.get(
                "/modpackbuild/7/modpackdirector"
                "?delivery=hosted&source=hybrid&selector=latest"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"modpack director archive")
        self.assertIn(
            "example-pack-2.0-modpack-director.zip",
            response.headers["Content-Disposition"],
        )
        self.assertEqual(render.call_args.kwargs["source_mode"], "hybrid")
        self.assertEqual(render.call_args.kwargs["delivery"], "hosted")
        self.assertEqual(render.call_args.kwargs["selector"], "latest")

    def test_hosted_filedirector_export_can_follow_latest(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        build = SimpleNamespace(
            modpack_slug="example-pack",
            version="2.0",
            is_published=True,
            private=False,
        )
        with (
            patch("asite.DistributionSettings.is_enabled", return_value=True),
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Build.get_modpackid_by_id", return_value=3),
            patch(
                "asite.User_modpack.get_user_modpackpermission", return_value=True
            ),
            patch("asite.MCInstanceExport.load", return_value=(build, [])),
        ):
            response = self.client.get(
                "/modpackbuild/7/filedirector?delivery=hosted&source=hybrid&selector=latest"
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.headers["Location"],
            "/filedirector/example-pack/latest/mods.remote.json?source=hybrid",
        )

    def test_management_permission_redirects_do_not_trust_referer(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=0),
            patch("asite.DistributionSettings.is_enabled", return_value=True),
        ):
            integration_response = self.client.get(
                "/integrations",
                headers={"Referer": "https://attacker.example/redirect"},
            )
            export_response = self.client.get(
                "/modpackbuild/7/mcinstance",
                headers={"Referer": "https://attacker.example/redirect"},
            )
            same_page_response = self.client.get(
                "/modversion/9",
                headers={
                    "Referer": "http://localhost/modlibrary?search=forge"
                },
            )
            external_back_response = self.client.get(
                "/modversion/9",
                headers={"Referer": "https://attacker.example/redirect"},
            )

        self.assertEqual(integration_response.status_code, 302)
        self.assertEqual(integration_response.headers["Location"], "/")
        self.assertEqual(export_response.status_code, 302)
        self.assertEqual(export_response.headers["Location"], "/modpacklibrary")
        self.assertEqual(same_page_response.status_code, 302)
        self.assertEqual(
            same_page_response.headers["Location"],
            "/modlibrary?search=forge",
        )
        self.assertEqual(external_back_response.status_code, 302)
        self.assertEqual(external_back_response.headers["Location"], "/")

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
        self.assertLess(
            response.data.index(b"Add Maven mod"),
            response.data.index(b"Configured artifacts"),
        )
        self.assertIn(b'data-name="Example Maven"', response.data)
        self.assertIn(b"updatemavenmodname", response.data)

    def test_modversion_management_page_shows_stored_metadata(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        mod = SimpleNamespace(
            id=9,
            name="example-mod",
            pretty_name="Example Mod",
            modtype="MOD",
            integration_provider="MODRINTH",
            integration_project_id="project-id",
            integration_label="Modrinth",
        )
        version = SimpleNamespace(
            id=12,
            mod_id=9,
            version="1.20.1-2.0",
            mcversion="1.20.1",
            modloader="FORGE",
            integration_version_id="version-id",
            md5="a" * 32,
            jarmd5="b" * 32,
            filesize=1234,
            jarfilesize=5678,
            jar_url_override="https://override.example/mod.jar",
            created_at=datetime(2026, 9, 20),
            updated_at=datetime(2026, 9, 20),
            get_management_builds=Mock(return_value=[]),
        )
        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Mod.get_by_id", return_value=mod),
            patch("asite.Modversion.get_by_id", return_value=version),
            patch("asite.Build.get_marked_build", return_value=0),
            patch("asite.Modpack.get_by_pinned", return_value=[]),
        ):
            response = self.client.get("/modversion/9/manage/12")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Stored version metadata", response.data)
        self.assertIn(b"version-id", response.data)
        self.assertIn(b"https://override.example/mod.jar", response.data)
        self.assertIn(b">Verify ZIP</button>", response.data)
        self.assertIn(b">Verify JAR</button>", response.data)
        self.assertNotIn(b">Create JAR</button>", response.data)
        self.assertIn(b"JAR filesize:</strong> 5678 bytes", response.data)
        self.assertIn(b"A native Maven or Modrinth URL", response.data)
        self.assertLess(
            response.data.index(b"JAR MD5:"),
            response.data.index(b"JAR filesize:"),
        )
        self.assertLess(
            response.data.index(b"JAR filesize:"),
            response.data.index(b">Verify ZIP</button>"),
        )

    def test_modversion_management_updates_override_and_clears_api_cache(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        mod = SimpleNamespace(id=9)
        version = SimpleNamespace(
            id=12,
            mod_id=9,
            jarmd5="b" * 32,
        )
        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Mod.get_by_id", return_value=mod),
            patch("asite.Modversion.get_by_id", return_value=version),
            patch("asite.Modversion.update_jar_url_override") as update,
            patch("api.clear_api_caches") as clear_caches,
        ):
            response = self.client.post(
                "/modversion/9/manage/12",
                data={"jar_url_override": "https://override.example/mod.jar"},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/modversion/9/manage/12")
        update.assert_called_once_with(
            12, 9, "https://override.example/mod.jar"
        )
        clear_caches.assert_called_once_with()

    def test_authenticated_user_can_export_technic_csv(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        build = SimpleNamespace(modpack_slug="example-pack", version="2.0")
        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Build.get_modpackid_by_id", return_value=3),
            patch(
                "asite.User_modpack.get_user_modpackpermission",
                return_value=True,
            ),
            patch(
                "asite.BuildCsvExport.load",
                return_value=(build, [{"mod_name": "Example"}]),
            ),
            patch(
                "asite.BuildCsvExport.render",
                return_value=io.BytesIO(b"mod_name,mod_slug,version,md5,filesize\n"),
            ),
        ):
            response = self.client.get("/modpackbuild/7/csv")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/csv")
        self.assertIn(
            "example-pack_2.0.csv", response.headers["Content-Disposition"]
        )

    def test_maven_manifest_requires_environment_permission(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        manifest = Mock(includes_maven=True)

        def permission(_token, permission):
            return 0 if permission == "solder_env" else 1

        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", side_effect=permission),
            patch(
                "asite.IntegrationManifest.parse", return_value=manifest
            ),
        ):
            response = self.client.post(
                "/integrations/manifest",
                data={"redistribution_confirmed": "1"},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/integrations")
        manifest.import_all.assert_not_called()

    def test_integration_manifest_export_downloads_json(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        manifest = Mock()
        manifest.render.return_value = io.BytesIO(
            b'{"format":"solder.py-integration-manifest","version":1,'
            b'"mods":[{"provider":"modrinth","project_id":"AANobbMI"}]}'
        )
        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch(
                "asite.IntegrationManifest.from_database",
                return_value=manifest,
            ),
        ):
            response = self.client.get("/integrations/manifest/export")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/json")
        self.assertIn(
            "solder.py-integration-manifest.json",
            response.headers["Content-Disposition"],
        )
        manifest.render.assert_called_once_with()

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

    def test_manage_modversion_can_create_legacy_jar(self):
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
            patch("api.clear_api_caches") as clear_caches,
        ):
            response = self.client.post(
                "/modversion/9/manage/12",
                data={"jar_action_submit": "1"},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/modversion/9/manage/12")
        arguments = create.call_args.args
        self.assertIs(arguments[0], mod)
        self.assertIs(arguments[1], version)
        self.assertEqual(arguments[2], "https://cdn.example.test/mods/")
        self.assertEqual(arguments[3], "./mods/")
        clear_caches.assert_called_once_with()

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

    def test_check_updates_marks_rows_without_importing_or_updating(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        build = SimpleNamespace(id=7, minecraft="1.21.1", modloader="FABRIC")
        provider_update = {
            "id": 41,
            "modid": 9,
            "modverid": 12,
            "integration_provider": "MODRINTH",
            "integration_version_id": "OLD",
            "versions": [{"id": 12, "version": "1.0"}],
        }
        stored_update = {
            "id": 42,
            "modid": 10,
            "modverid": 21,
            "integration_provider": None,
            "integration_version_id": None,
            "versions": [
                {"id": 22, "version": "2.0"},
                {"id": 21, "version": "1.0"},
            ],
        }
        provider_current = {
            "id": 43,
            "modid": 11,
            "modverid": 31,
            "integration_provider": "MAVEN",
            "integration_version_id": "CURRENT",
            # The provider result is authoritative even if a later local row
            # exists for an integration-managed mod.
            "versions": [
                {"id": 32, "version": "local-newer"},
                {"id": 31, "version": "current"},
            ],
        }
        editor = SimpleNamespace(
            listmod=[],
            packbuild=build,
            packbuildname="Example Pack",
            listmodversions=[],
            buildlist=[provider_update, stored_update, provider_current],
            optional_mode=0,
        )

        def provider_versions(mod, _build, _user_id):
            if mod.id == 9:
                return [
                    SimpleNamespace(
                        version_id="NEW", version_number="1.1"
                    )
                ]
            return [
                SimpleNamespace(
                    version_id="CURRENT", version_number="current"
                )
            ]

        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.Session.get_user_id", return_value=4),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Build.get_modpackid_by_id", return_value=3),
            patch(
                "asite.User_modpack.get_user_modpackpermission",
                return_value=True,
            ),
            patch(
                "asite.Build_modversion.get_build_editor_data",
                return_value=editor,
            ),
            patch(
                "asite.Build_modversion.get_integrated_mods",
                return_value=[
                    {"id": 9, "pretty_name": "Provider Update"},
                    {"id": 11, "pretty_name": "Provider Current"},
                ],
            ),
            patch(
                "asite.Mod.get_by_id",
                side_effect=lambda mod_id: SimpleNamespace(id=mod_id),
            ),
            patch(
                "asite.ModIntegration.list_versions",
                side_effect=provider_versions,
            ),
            patch("asite._materialize_integration_version") as materialize,
            patch("asite.Build_modversion.update_all_compatible") as update_all,
            patch(
                "asite.DistributionSettings.get_all",
                return_value=dict(DistributionSettings.DEFAULTS),
            ),
            patch("asite.render_template", return_value="checked") as render,
        ):
            response = self.client.get("/modpackbuild/7?check_updates=1")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(provider_update["update_available"])
        self.assertEqual(provider_update["available_version"], "1.1")
        self.assertTrue(stored_update["update_available"])
        self.assertEqual(stored_update["available_version"], "2.0")
        self.assertFalse(provider_current["update_available"])
        self.assertNotIn("available_version", provider_current)
        self.assertIs(render.call_args.kwargs["buildlist"], editor.buildlist)
        materialize.assert_not_called()
        update_all.assert_not_called()

    def test_build_checkbox_updates_only_the_advanced_optional_work_list(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Build.get_modpackid_by_id", return_value=3),
            patch(
                "asite.User_modpack.get_user_modpackpermission",
                return_value=True,
            ),
            patch(
                "asite.Modpack.get_by_id",
                return_value=SimpleNamespace(optional_mode=1),
            ),
            patch("asite.AdvancedOptional.set_listing") as set_listing,
            patch("asite.Build_modversion.update_optional") as update_optional,
        ):
            response = self.client.post(
                "/modpackbuild/7",
                data={
                    "optional_submit": "1",
                    "optional_modid": "41",
                    "optional_check": "1",
                },
            )

        self.assertEqual(response.status_code, 302)
        set_listing.assert_called_once_with("7", "41", "1")
        update_optional.assert_not_called()

    def test_build_checkbox_updates_legacy_optional_state_in_basic_mode(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Build.get_modpackid_by_id", return_value=3),
            patch(
                "asite.User_modpack.get_user_modpackpermission",
                return_value=True,
            ),
            patch(
                "asite.Modpack.get_by_id",
                return_value=SimpleNamespace(optional_mode=0),
            ),
            patch("asite.AdvancedOptional.set_listing") as set_listing,
            patch("asite.Build_modversion.update_optional") as update_optional,
        ):
            response = self.client.post(
                "/modpackbuild/7",
                data={
                    "optional_submit": "1",
                    "optional_modid": "12",
                    "optional_check": "1",
                },
            )

        self.assertEqual(response.status_code, 302)
        update_optional.assert_called_once_with("12", "1", "7")
        set_listing.assert_not_called()

    def test_add_mod_uses_legacy_optional_state_in_basic_mode(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Build.get_modpackid_by_id", return_value=3),
            patch(
                "asite.User_modpack.get_user_modpackpermission",
                return_value=True,
            ),
            patch(
                "asite.Modpack.get_by_id",
                return_value=SimpleNamespace(optional_mode=0),
            ),
            patch(
                "asite.Modversion.add_modversion_to_selected_build",
                return_value=[],
            ) as add_modversion,
            patch(
                "asite.AdvancedOptional.set_modversion_listing"
            ) as set_listing,
        ):
            response = self.client.post(
                "/modpackbuild/7",
                data={
                    "add_mod_submit": "1",
                    "modnames": "9",
                    "modversion": "12",
                    "newoptional": "1",
                },
            )

        self.assertEqual(response.status_code, 302)
        add_modversion.assert_called_once_with("12", "9", "7", "0", "1")
        set_listing.assert_not_called()

    def test_add_mod_uses_work_list_checkbox_in_advanced_mode(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Build.get_modpackid_by_id", return_value=3),
            patch(
                "asite.User_modpack.get_user_modpackpermission",
                return_value=True,
            ),
            patch(
                "asite.Modpack.get_by_id",
                return_value=SimpleNamespace(optional_mode=1),
            ),
            patch(
                "asite.Mod.get_by_id",
                return_value=SimpleNamespace(modtype="MOD"),
            ),
            patch(
                "asite.Modversion.add_modversion_to_selected_build",
                return_value=[],
            ) as add_modversion,
            patch(
                "asite.AdvancedOptional.set_modversion_listing"
            ) as set_listing,
        ):
            response = self.client.post(
                "/modpackbuild/7",
                data={
                    "add_mod_submit": "1",
                    "modnames": "9",
                    "modversion": "12",
                    "newadvancedoptional": "1",
                },
            )

        self.assertEqual(response.status_code, 302)
        add_modversion.assert_called_once_with("12", "9", "7", "0", "0")
        set_listing.assert_called_once_with("7", "12")

    def test_advanced_optional_page_lists_only_selected_build_mods(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        modpack = SimpleNamespace(id=3, optional_mode=1)
        editor = SimpleNamespace(
            packbuild=SimpleNamespace(id=7),
            packbuildname="Example Pack",
            buildlist=[
                {"id": 41, "advanced_listed": 1, "optional": 0},
                {"id": 42, "advanced_listed": 0, "optional": 1},
            ],
        )
        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Build.get_modpackid_by_id", return_value=3),
            patch(
                "asite.User_modpack.get_user_modpackpermission",
                return_value=True,
            ),
            patch("asite.Modpack.get_by_id", return_value=modpack),
            patch(
                "asite.Build_modversion.get_build_editor_data",
                return_value=editor,
            ),
            patch("asite.AdvancedOptional.get_groups", return_value=[]),
            patch(
                "asite.DistributionSettings.get_all",
                return_value=dict(DistributionSettings.DEFAULTS),
            ),
            patch("asite.TechnicSolderPyLoader.get", return_value=None),
            patch("asite.render_template", return_value="optionals") as render,
        ):
            response = self.client.get("/modpackbuild/7/optionals")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(render.call_args.kwargs["buildlist"], [editor.buildlist[0]])

    def test_basic_optional_page_lists_only_legacy_optional_mods(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        modpack = SimpleNamespace(id=3, optional_mode=0)
        editor = SimpleNamespace(
            packbuild=SimpleNamespace(id=7),
            packbuildname="Example Pack",
            buildlist=[
                {"id": 41, "advanced_listed": 1, "optional": 0},
                {"id": 42, "advanced_listed": 0, "optional": 1},
                {"id": 43, "advanced_listed": 1, "optional": 2},
            ],
        )
        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Build.get_modpackid_by_id", return_value=3),
            patch(
                "asite.User_modpack.get_user_modpackpermission",
                return_value=True,
            ),
            patch("asite.Modpack.get_by_id", return_value=modpack),
            patch(
                "asite.Build_modversion.get_build_editor_data",
                return_value=editor,
            ),
            patch("asite.AdvancedOptional.get_groups", return_value=[]),
            patch(
                "asite.DistributionSettings.get_all",
                return_value=dict(DistributionSettings.DEFAULTS),
            ),
            patch("asite.TechnicSolderPyLoader.get", return_value=None),
            patch("asite.render_template", return_value="optionals") as render,
        ):
            response = self.client.get("/modpackbuild/7/optionals")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            render.call_args.kwargs["buildlist"], [editor.buildlist[1]]
        )

    def test_build_editor_defers_downloader_releases_until_export(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        packbuild = SimpleNamespace(
            id=7,
            minecraft="1.7.10",
            modloader="FORGE",
        )
        editor = SimpleNamespace(
            listmod=[],
            packbuild=packbuild,
            packbuildname="Example Pack",
            listmodversions=[],
            buildlist=[],
        )
        settings = dict(DistributionSettings.DEFAULTS)
        settings.update(
            {
                DistributionSettings.MCIL: True,
                DistributionSettings.FILEDIRECTOR: True,
                DistributionSettings.MRPACK: True,
                DistributionSettings.CURSEFORGE: True,
            }
        )
        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Build.get_modpackid_by_id", return_value=3),
            patch(
                "asite.User_modpack.get_user_modpackpermission",
                return_value=True,
            ),
            patch(
                "asite.Build_modversion.get_build_editor_data",
                return_value=editor,
            ),
            patch("asite.DistributionSettings.get_all", return_value=settings),
            patch(
                "asite.PlatformPackExport.available_downloaders",
                side_effect=[("modrinth-release",), ("curseforge-file",)],
            ) as available,
            patch("asite.render_template", return_value="build") as render,
        ):
            response = self.client.get("/modpackbuild/7")

            self.assertEqual(response.status_code, 200)
            available.assert_not_called()
            context = render.call_args.kwargs
            self.assertFalse(context["export_requested"])
            self.assertEqual(context["modrinth_downloaders"], ())
            self.assertEqual(context["prism_downloaders"], ())
            self.assertEqual(context["curseforge_downloaders"], ())

            response = self.client.get("/modpackbuild/7?export=1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item.args[1] for item in available.call_args_list],
            ["modrinth", "curseforge"],
        )
        context = render.call_args.kwargs
        self.assertTrue(context["export_requested"])
        self.assertEqual(context["modrinth_downloaders"], ("modrinth-release",))
        self.assertEqual(context["prism_downloaders"], ())
        self.assertEqual(
            context["curseforge_downloaders"], ("curseforge-file",)
        )

    def test_build_export_modal_renders_one_shared_settings_form(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        packbuild = SimpleNamespace(
            id=7,
            version="2.0",
            minecraft="1.7.10",
            forge="1.7.10-10.13.4.1614",
            modloader="FORGE",
            min_java=None,
            java_runtime=None,
            min_memory=2048,
            is_published=True,
            private=False,
        )
        editor = SimpleNamespace(
            listmod=[],
            packbuild=packbuild,
            packbuildname="Example Pack",
            listmodversions=[],
            buildlist=[],
        )
        release = SimpleNamespace(
            selector="release-id", version="1.0", default=True
        )
        downloader = SimpleNamespace(
            key="mcil",
            label="MCInstance Loader",
            supports_remote_config=False,
            releases=(release,),
        )
        solderpy_downloader = SimpleNamespace(
            key="solderpyloader",
            label="SolderPy Loader",
            supports_remote_config=True,
            releases=(release,),
        )
        publication_target = SimpleNamespace(
            id=12,
            provider="MODRINTH",
            provider_label="Modrinth",
            account_name="Release account",
        )
        publication_state = SimpleNamespace(
            locked=False,
            published=True,
            version_number="2.0-Patch-1",
            patch_number=1,
        )
        settings = {name: True for name in DistributionSettings.DEFAULTS}
        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.Session.get_user_id", return_value=4),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Build.get_modpackid_by_id", return_value=3),
            patch("asite.Build.get_marked_build", return_value=0),
            patch("asite.Modpack.get_by_pinned", return_value=[]),
            patch(
                "asite.User_modpack.get_user_modpackpermission",
                return_value=True,
            ),
            patch(
                "asite.Build_modversion.get_build_editor_data",
                return_value=editor,
            ),
            patch("asite.DistributionSettings.get_all", return_value=settings),
            patch(
                "asite.PlatformPackExport.available_downloaders",
                side_effect=[
                    (solderpy_downloader, downloader),
                    (downloader,),
                ],
            ),
            patch(
                "asite.PlatformPublishing.get_targets",
                return_value=[publication_target],
            ),
            patch(
                "asite.PlatformPublishing.get_build_publication_states",
                return_value={12: publication_state},
            ),
        ):
            response = self.client.get("/modpackbuild/7?export=1")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.count(b'id="export_modpack_form"'), 1)
        self.assertEqual(response.data.count(b'name="source"'), 1)
        self.assertEqual(response.data.count(b'name="forge_version"'), 1)
        self.assertEqual(response.data.count(b'name="delivery"'), 2)
        self.assertEqual(response.data.count(b'name="selector"'), 1)
        self.assertIn(b'name="modrinth_downloader"', response.data)
        self.assertIn(b'name="server_downloader"', response.data)
        self.assertNotIn(b'name="solderpy_loader_downloader"', response.data)
        self.assertIn(b'name="prism_downloader"', response.data)
        self.assertIn(b'name="curseforge_downloader"', response.data)
        self.assertIn(b"Self-contained archive", response.data)
        self.assertIn(b'>FileDirector</option>', response.data)
        self.assertIn(b'value="solderpy"', response.data)
        self.assertIn(b'>Dedicated server</option>', response.data)
        self.assertIn(b'>Modpack Director</option>', response.data)
        self.assertIn(b'>Packwiz</option>', response.data)
        self.assertIn(b'>Modrinth</option>', response.data)
        self.assertIn(b'>CurseForge</option>', response.data)
        self.assertIn(b'>Prism Launcher</option>', response.data)
        self.assertIn(
            b"Send patch 2.0-Patch-1 to Modrinth", response.data
        )

    def test_build_update_accepts_arbitrary_minimum_java_text(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Build.get_modpackid_by_id", return_value=3),
            patch(
                "asite.User_modpack.get_user_modpackpermission",
                return_value=True,
            ),
            patch("asite.Build.update") as update,
        ):
            response = self.client.post(
                "/modpackbuild/7",
                data={
                    "form-submit": "1",
                    "version": "2.0",
                    "mcversion": "1.21.1",
                    "min_java": " 1.8.0_51 ",
                    "java_runtime": "java-runtime-delta",
                    "memory": "4096",
                    "forge": "",
                    "modloader": "",
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/modpackbuild/7")
        update.assert_called_once_with(
            "7", "2.0", "1.21.1", "0", "0", "1.8.0_51", "4096", None, "",
            "java-runtime-delta"
        )

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
                patch("asite.R2_BUCKET", ""),
                patch("asite.R2.upload_file") as upload_file,
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
                        "modloader": ["FORGE", "FABRIC"],
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
                modloader=["FORGE", "FABRIC"],
                jarfilesize=len(jar_data),
            )
            artifact_dir = Path(directory, "example-mod")
            self.assertEqual(
                Path(artifact_dir, "example-mod-1.7.10-1.0.jar").read_bytes(),
                jar_data,
            )
            upload_file.assert_not_called()

    def test_mod_upload_removes_published_files_when_database_insert_fails(self):
        package = io.BytesIO()
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("config/example.cfg", b"enabled=true")
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
                        name="example-config",
                        integration_provider=None,
                        modtype="CONFIG",
                    ),
                ),
                patch(
                    "asite.Modversion.new",
                    side_effect=RuntimeError("database failed"),
                ),
                patch("asite.UPLOAD_FOLDER", directory),
                patch("asite.R2_BUCKET", None),
            ):
                with self.assertRaisesRegex(RuntimeError, "database failed"):
                    self.client.post(
                        "/modlibrary",
                        data={
                            "form-submit": "1",
                            "modid": "9",
                            "mod": "example-config",
                            "mcversion": "1.20.1",
                            "version": "1.0",
                            "md5": package_md5,
                            "jarmd5": "0",
                            "file": (io.BytesIO(package_data), "upload.zip"),
                        },
                        content_type="multipart/form-data",
                    )

            artifact = Path(
                directory,
                "example-config",
                "example-config-1.20.1-1.0.zip",
            )
            self.assertFalse(artifact.exists())

    def test_multi_minecraft_upload_uses_a_stable_multi_filename(self):
        package = io.BytesIO()
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("config/example.cfg", b"enabled=true")
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
                        name="example-config",
                        integration_provider=None,
                        modtype="CONFIG",
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
                        "mod": "example-config",
                        "mcversion": "1.20.1,1.20.2",
                        "version": "1.0",
                        "md5": package_md5,
                        "jarmd5": "0",
                        "file": (io.BytesIO(package_data), "upload.zip"),
                    },
                    content_type="multipart/form-data",
                )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(new_version.call_args.args[1], "MULTI-1.0")
        self.assertEqual(new_version.call_args.args[2], "1.20.1,1.20.2")

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

    def test_legacy_manual_version_form_is_enforced_server_side(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.legacy_modversion_adding", False),
            patch("asite.Mod.get_by_id") as get_mod,
            patch("asite.Modversion.new") as new_version,
        ):
            response = self.client.post(
                "/modversion/9",
                data={"newmodvermanual_submit": "1"},
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/modversion/9")
        get_mod.assert_not_called()
        new_version.assert_not_called()

    def test_existing_mod_can_be_manually_linked_to_modrinth(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        mod = SimpleNamespace(id=9, pretty_name="Local Mod")
        linked = SimpleNamespace(id=9, pretty_name="Local Mod")
        project = SimpleNamespace(provider="MODRINTH", title="Upstream Mod")
        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.Session.get_user_id", return_value=7),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Mod.get_by_id", return_value=mod),
            patch(
                "asite.ModIntegration.link_existing",
                return_value=(linked, project),
            ) as link,
        ):
            response = self.client.post(
                "/modversion/9",
                data={
                    "link_modrinth_submit": "1",
                    "modrinth_reference": "https://modrinth.com/mod/upstream-mod",
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/modversion/9")
        link.assert_called_once_with(
            mod,
            "MODRINTH",
            "https://modrinth.com/mod/upstream-mod",
            7,
        )

    def test_linked_github_config_can_sync_an_explicit_ref(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        mod = SimpleNamespace(id=9, pretty_name="Config Pack")
        result = SimpleNamespace(
            created=True,
            version=SimpleNamespace(version="1.7.10-2.0"),
        )
        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.Session.get_user_id", return_value=7),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Mod.get_by_id", return_value=mod),
            patch("asite.R2_BUCKET", None),
            patch(
                "asite.ModIntegration.materialize_github_ref",
                return_value=result,
            ) as sync,
        ):
            response = self.client.post(
                "/modversion/9",
                data={
                    "sync_github_ref_submit": "1",
                    "github_minecraft": "1.7.10",
                    "github_modloader": "FORGE",
                    "github_ref": "main",
                    "github_version": "2.0",
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/modversion/9")
        sync.assert_called_once_with(
            mod,
            "1.7.10",
            "FORGE",
            "main",
            "2.0",
            7,
            "./mods/",
            r2_client=None,
            r2_bucket=None,
        )

    def test_managed_version_can_be_imported_from_mod_page(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        mod = SimpleNamespace(id=9, pretty_name="Managed Mod")
        result = SimpleNamespace(
            created=True,
            version=SimpleNamespace(version="1.21.1-2.0"),
        )
        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.Session.get_user_id", return_value=7),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Mod.get_by_id", return_value=mod),
            patch("asite.R2_BUCKET", None),
            patch(
                "asite.ModIntegration.materialize_for_management",
                return_value=result,
            ) as materialize,
        ):
            response = self.client.post(
                "/modversion/9",
                data={
                    "import_integration_version_submit": "1",
                    "integration_version_id": "VERSION",
                    "integration_minecraft": "1.21.1",
                    "integration_modloader": "FABRIC",
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/modversion/9")
        materialize.assert_called_once_with(
            mod,
            "VERSION",
            ["1.21.1"],
            ["FABRIC"],
            7,
            "./mods/",
            r2_client=None,
            r2_bucket=None,
        )

    def test_verify_zip_recalculates_hash_and_size_from_configured_repository(self):
        with self.client.session_transaction() as flask_session:
            flask_session["token"] = "valid-test-token"

        version = SimpleNamespace(
            id=12,
            mod_id=9,
            md5="calculated-md5",
            filesize=123,
            rehash=Mock(),
        )
        mod = SimpleNamespace(id=9, name="example-mod")
        with (
            patch("asite.Session.verify_session", return_value=True),
            patch("asite.User.get_permission_token", return_value=1),
            patch("asite.Mod.get_by_id", return_value=mod),
            patch("asite.Modversion.get_by_id", return_value=version),
            patch("api.clear_api_caches") as clear_caches,
        ):
            response = self.client.post(
                "/modversion/9/manage/12",
                data={
                    "verify_zip_submit": "1",
                    "rehash_url": "http://127.0.0.1/private",
                },
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/modversion/9/manage/12")
        version.rehash.assert_called_once_with(
            "https://cdn.example.test/mods/", "example-mod"
        )
        clear_caches.assert_called_once_with()

if __name__ == "__main__":
    unittest.main()
