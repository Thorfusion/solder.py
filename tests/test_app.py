import hashlib
import io
import importlib
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tests.environment import configure_test_environment


configure_test_environment()

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
        self.assertEqual(self.app_module.__version__, "1.7.4")

    def test_api_blueprint_is_registered(self):
        response = self.client.get("/api/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["api"], "solder.py")

    def test_management_routes_are_registered(self):
        routes = {rule.rule for rule in self.app_module.app.url_map.iter_rules()}

        self.assertIn("/login", routes)
        self.assertIn("/modlibrary", routes)
        self.assertIn("/modpackbuild/<int:id>/mcinstance", routes)
        self.assertIn("/api/modpack/<slugstring>/<buildstring>", routes)

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
            "dropdownsearches('dependency_search', 'dependency_mod_id');",
            version_source,
        )
        self.assertIn('id="dependency_mod_id"', version_source)
        self.assertNotIn('id="table"', version_source)

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

if __name__ == "__main__":
    unittest.main()
