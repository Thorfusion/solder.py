import hashlib
import json
from pathlib import Path
import tomllib
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask

from tests.environment import configure_test_environment


configure_test_environment()

import distribution_api as routes  # noqa: E402
from models.database import Database  # noqa: E402
from models.distribution import (  # noqa: E402
    DistributionBuild,
    DistributionExport,
    DistributionExportError,
    DistributionPackage,
    FileDirectorExport,
    PackwizExport,
)
from models.distribution_settings import DistributionSettings  # noqa: E402


JAR_MD5 = "0123456789abcdef0123456789abcdef"
ZIP_MD5 = "fedcba9876543210fedcba9876543210"
REPOSITORY = "https://cdn.example.test/mods/"


def build(selector="1.0"):
    return DistributionBuild(
        id=7,
        pack_name="Example Pack",
        pack_slug="example-pack",
        version="1.0",
        minecraft="1.20.1",
        modloader="FORGE",
        modloader_version="47.3.0",
        requested_selector=selector,
    )


def package(
    slug="example-mod",
    *,
    optional=False,
    side="BOTH",
    modtype="MOD",
    jar_md5=JAR_MD5,
):
    return DistributionPackage(
        id=11,
        mod_slug=slug,
        pretty_name="Example Mod",
        description="An example mod",
        version="2.0",
        zip_md5=ZIP_MD5,
        jar_md5=jar_md5,
        side=side,
        modtype=modtype,
        optional=optional,
    )


class DistributionRendererTests(unittest.TestCase):
    def test_packwiz_hash_chain_side_optional_and_loader_are_valid_toml(self):
        selected = package(optional=True, side="CLIENT")
        mod_content = PackwizExport.mod_toml(selected, REPOSITORY)
        index_content, excluded = PackwizExport.index_toml(
            [selected], REPOSITORY
        )
        pack_content = PackwizExport.pack_toml(build(), index_content)

        mod_data = tomllib.loads(mod_content.decode())
        index_data = tomllib.loads(index_content.decode())
        pack_data = tomllib.loads(pack_content.decode())

        self.assertEqual(excluded, 0)
        self.assertEqual(mod_data["side"], "client")
        self.assertEqual(mod_data["download"]["hash-format"], "md5")
        self.assertEqual(mod_data["download"]["hash"], JAR_MD5)
        self.assertFalse(mod_data["option"]["default"])
        self.assertEqual(
            index_data["files"][0]["hash"],
            hashlib.sha256(mod_content).hexdigest(),
        )
        self.assertEqual(
            pack_data["index"]["hash"],
            hashlib.sha256(index_content).hexdigest(),
        )
        self.assertEqual(pack_data["versions"]["minecraft"], "1.20.1")
        self.assertEqual(pack_data["versions"]["forge"], "47.3.0")

    def test_packwiz_excludes_packages_without_a_raw_mod_jar(self):
        index_content, excluded = PackwizExport.index_toml(
            [
                package(),
                package("configuration", modtype="CONFIG", jar_md5=None),
                package("legacy-mod", jar_md5=None),
            ],
            REPOSITORY,
        )

        index = tomllib.loads(index_content.decode())
        self.assertEqual(excluded, 2)
        self.assertEqual(len(index["files"]), 1)
        self.assertEqual(index["files"][0]["file"], "mods/example-mod.pw.toml")

    def test_packwiz_rejects_duplicate_mods_in_one_build(self):
        with self.assertRaisesRegex(
            DistributionExportError, "more than one version"
        ):
            PackwizExport.index_toml([package(), package()], REPOSITORY)

    def test_packwiz_requires_a_version_for_a_configured_modloader(self):
        selected_build = build()
        selected_build = DistributionBuild(
            **{
                **selected_build.__dict__,
                "modloader_version": None,
            }
        )
        with self.assertRaisesRegex(
            DistributionExportError, "modloader version"
        ):
            PackwizExport.pack_toml(selected_build, b' hash-format = "sha256"')

    def test_filedirector_preserves_side_and_extracts_solder_zips(self):
        content = FileDirectorExport.bundle(
            [
                package(side="CLIENT"),
                package(
                    "configuration",
                    side="SERVER",
                    modtype="CONFIG",
                    jar_md5=None,
                ),
                package("mcil", modtype="MCIL"),
            ],
            REPOSITORY,
        )
        entries = json.loads(content)["url"]

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["metadata"]["side"], "CLIENT")
        self.assertEqual(entries[0]["metadata"]["hash"], {"MD5": JAR_MD5})
        self.assertTrue(entries[0]["url"].endswith("example-mod-2.0.jar"))
        self.assertEqual(
            entries[1]["metadata"],
            {"hash": {"MD5": ZIP_MD5}, "side": "SERVER"},
        )
        self.assertEqual(entries[1]["folder"], ".")
        self.assertEqual(
            entries[1]["installationPolicy"],
            {"extract": True, "deleteAfterExtract": True},
        )
        self.assertTrue(entries[1]["url"].endswith("configuration-2.0.zip"))

    def test_filedirector_uses_native_optional_selection(self):
        content = FileDirectorExport.bundle(
            [package(optional=True)], REPOSITORY
        )
        entry = json.loads(content)["url"][0]

        self.assertEqual(
            entry["installationPolicy"],
            {
                "optionalKey": "example-mod",
                "selectedByDefault": False,
                "name": "Example Mod",
                "description": "An example mod",
            },
        )

    def test_repository_url_must_be_public_http_without_credentials(self):
        for invalid in (
            None,
            "C:/mods",
            "file:///mods",
            "https://user:secret@example.test/mods/",
        ):
            with self.subTest(invalid=invalid), self.assertRaises(
                DistributionExportError
            ):
                DistributionExport.repository_base(invalid)

    def test_export_rejects_slugs_that_cannot_be_safe_relative_paths(self):
        with self.assertRaisesRegex(DistributionExportError, "mod slug"):
            DistributionExport._package_from_row(
                {
                    "id": 1,
                    "mod_slug": "unsafe#fragment",
                    "version": "1.0",
                    "md5": ZIP_MD5,
                    "jarmd5": JAR_MD5,
                    "side": "BOTH",
                    "modtype": "MOD",
                    "optional": 0,
                }
            )


class DistributionSettingsTests(unittest.TestCase):
    def test_settings_default_off_and_load_known_values(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchall.return_value = [
            {"name": DistributionSettings.PACKWIZ, "value": "yes"},
        ]
        with patch(
            "models.distribution_settings.Database.get_connection",
            return_value=connection,
        ):
            values = DistributionSettings.get_all()

        self.assertTrue(values[DistributionSettings.PACKWIZ])
        self.assertFalse(values[DistributionSettings.FILEDIRECTOR])
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_settings_update_both_values_in_one_transaction(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        with patch(
            "models.distribution_settings.Database.get_connection",
            return_value=connection,
        ):
            DistributionSettings.update_exports(True, False)

        self.assertEqual(cursor.execute.call_count, 2)
        first_parameters = cursor.execute.call_args_list[0].args[1]
        second_parameters = cursor.execute.call_args_list[1].args[1]
        self.assertEqual(first_parameters, ("packwiz_enabled", "1", "1"))
        self.assertEqual(
            second_parameters, ("filedirector_enabled", "0", "0")
        )
        connection.commit.assert_called_once_with()
        connection.rollback.assert_not_called()

    def test_current_schema_contains_shared_distribution_settings(self):
        schema = Database.SOLDER_SETTINGS_TABLE_SQL
        self.assertIn("solder_settings", schema)
        self.assertIn("PRIMARY KEY", schema)
        self.assertIn("DEFAULT CURRENT_TIMESTAMP", schema)
        self.assertIn("ON UPDATE CURRENT_TIMESTAMP", schema)


class DistributionDeploymentDocumentationTests(unittest.TestCase):
    def test_caddy_compose_routes_dynamic_exports_and_includes_mysql(self):
        readme = (
            Path(__file__).resolve().parents[1] / "README.md"
        ).read_text(encoding="utf-8")

        self.assertIn("handle /packwiz/*", readme)
        self.assertIn("handle /filedirector/*", readme)
        self.assertIn("reverse_proxy solderpy:5000", readme)
        self.assertIn("image: mysql:8.4", readme)
        self.assertIn("mysql_data:/var/lib/mysql", readme)
        self.assertIn("APP_URL: https://solder.example.com/", readme)
        self.assertIn("solder_database:\n    internal: true", readme)


class DistributionRouteTests(unittest.TestCase):
    def setUp(self):
        self.application = Flask(__name__)
        self.application.config.update(TESTING=True)
        self.application.register_blueprint(routes.distribution_api)
        self.client = self.application.test_client()

    def test_formats_are_not_public_until_enabled(self):
        with patch.object(
            routes.DistributionSettings, "is_enabled", return_value=False
        ):
            packwiz = self.client.get(
                "/packwiz/example-pack/1.0/pack.toml"
            )
            filedirector = self.client.get(
                "/filedirector/example-pack/1.0/mods.bundle.json"
            )

        self.assertEqual(packwiz.status_code, 404)
        self.assertEqual(filedirector.status_code, 404)

    def test_packwiz_channel_redirects_to_exact_build(self):
        with (
            patch.object(
                routes.DistributionSettings, "is_enabled", return_value=True
            ),
            patch.object(
                routes.DistributionExport,
                "load_build",
                return_value=build("latest"),
            ),
            patch.object(routes.DistributionExport, "load_packages") as load,
        ):
            response = self.client.get(
                "/packwiz/example-pack/latest/pack.toml"
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            response.headers["Location"],
            "/packwiz/example-pack/1.0/pack.toml",
        )
        load.assert_not_called()

    def test_packwiz_route_supports_conditional_etag_requests(self):
        with (
            patch.object(
                routes.DistributionSettings, "is_enabled", return_value=True
            ),
            patch.object(
                routes.DistributionExport, "load_build", return_value=build()
            ),
            patch.object(
                routes.DistributionExport,
                "load_packages",
                return_value=[package()],
            ),
        ):
            response = self.client.get(
                "/packwiz/example-pack/1.0/pack.toml"
            )
            unchanged = self.client.get(
                "/packwiz/example-pack/1.0/pack.toml",
                headers={"If-None-Match": response.headers["ETag"]},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/toml")
        self.assertEqual(unchanged.status_code, 304)

    def test_filedirector_required_and_optional_bundles_are_separate(self):
        optional_mod = package(optional=True)
        with (
            patch.object(
                routes.DistributionSettings, "is_enabled", return_value=True
            ),
            patch.object(
                routes.DistributionExport, "load_build", return_value=build()
            ),
            patch.object(
                routes.DistributionExport,
                "load_packages",
                return_value=[optional_mod],
            ) as load,
        ):
            response = self.client.get(
                "/filedirector/example-pack/latest/optional.bundle.json"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.get_json()["url"]), 1)
        load.assert_called_once_with(7, optional=True)

    def test_filedirector_main_bundle_includes_required_and_optional(self):
        with (
            patch.object(
                routes.DistributionSettings, "is_enabled", return_value=True
            ),
            patch.object(
                routes.DistributionExport, "load_build", return_value=build()
            ),
            patch.object(
                routes.DistributionExport,
                "load_packages",
                return_value=[package()],
            ) as load,
        ):
            response = self.client.get(
                "/filedirector/example-pack/latest/mods.bundle.json"
            )

        self.assertEqual(response.status_code, 200)
        load.assert_called_once_with(7, optional=None)

    def test_filedirector_remote_file_keeps_channel_url(self):
        with (
            patch.object(
                routes.DistributionSettings, "is_enabled", return_value=True
            ),
            patch.object(
                routes.DistributionExport,
                "load_build",
                return_value=build("recommended"),
            ),
        ):
            response = self.client.get(
                "/filedirector/example-pack/recommended/mods.remote.json",
                headers={"Host": "attacker.example"},
            )

        self.assertEqual(
            response.get_json(),
            {
                "url": "https://solder.example.test/filedirector/example-pack/"
                "recommended/mods.bundle.json"
            },
        )

    def test_export_errors_are_logged_without_exposing_exception_text(self):
        private_detail = "database password: do-not-return-this"
        cases = (
            ("/packwiz/example-pack/1.0/pack.toml", "load_build"),
            ("/packwiz/example-pack/1.0/pack.toml", "load_packages"),
            ("/packwiz/example-pack/1.0/index.toml", "load_packages"),
            ("/packwiz/example-pack/1.0/mods/example-mod.pw.toml", "load_package"),
            ("/filedirector/example-pack/1.0/mods.bundle.json", "load_packages"),
            ("/filedirector/example-pack/1.0/mods.remote.json", "application_base"),
        )

        for endpoint, failing_method in cases:
            with self.subTest(endpoint=endpoint, failing_method=failing_method):
                error = DistributionExportError(private_detail)
                with (
                    patch.object(
                        routes.DistributionSettings,
                        "is_enabled",
                        return_value=True,
                    ),
                    patch.object(
                        routes.DistributionExport,
                        "load_build",
                        return_value=build(),
                    ) as load_build,
                    patch.object(
                        routes.DistributionExport,
                        failing_method,
                        side_effect=error,
                    ),
                    patch.object(
                        self.application.logger, "warning"
                    ) as warning,
                ):
                    if failing_method == "load_build":
                        load_build.side_effect = error
                    response = self.client.get(endpoint)

                self.assertEqual(response.status_code, 422)
                self.assertEqual(
                    response.get_data(as_text=True),
                    routes.PUBLIC_EXPORT_ERROR,
                )
                self.assertNotIn(private_detail, response.get_data(as_text=True))
                warning.assert_called_once_with(
                    "Distribution export failed.", exc_info=True
                )


class PublicBuildLoadingTests(unittest.TestCase):
    def test_channel_uses_existing_anonymous_api_visibility_rules(self):
        modpack = SimpleNamespace(
            id=3,
            name="Example Pack",
            slug="example-pack",
            latest="2.0",
            recommended="1.0",
        )
        resolved = SimpleNamespace(
            id=8,
            version="2.0",
            minecraft="1.21.1",
            modloader="NEOFORGE",
            forge="21.1.0",
        )
        modpack.get_build_api = Mock(return_value=resolved)
        with patch(
            "models.distribution.Modpack.get_by_cid_slug_api",
            return_value=modpack,
        ) as get_pack:
            loaded = DistributionExport.load_build("example-pack", "latest")

        get_pack.assert_called_once_with(None, "example-pack")
        modpack.get_build_api.assert_called_once_with("2.0", cid=None)
        self.assertEqual(loaded.version, "2.0")
        self.assertTrue(loaded.is_channel)
