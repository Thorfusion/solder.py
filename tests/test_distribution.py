import hashlib
import io
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
from models.advanced_optional import (  # noqa: E402
    AdvancedOptionalGroup,
    AdvancedOptionalItem,
    SINGLE,
)
from models.distribution import (  # noqa: E402
    DistributionBuild,
    DistributionExport,
    DistributionExportError,
    DistributionPackage,
    FileDirectorExport,
    PackwizExport,
)
from models.distribution_settings import DistributionSettings  # noqa: E402
from models.platform_export_override import (  # noqa: E402
    PlatformExportOverride,
    PlatformExportOverrideError,
)


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
    integration_provider=None,
    integration_project_id=None,
    integration_version_id=None,
    optional_state=None,
    membership_id=None,
):
    if optional_state is None:
        optional_state = 1 if optional else 0
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
        optional_state=optional_state,
        membership_id=membership_id,
        integration_provider=integration_provider,
        integration_project_id=integration_project_id,
        integration_version_id=integration_version_id,
    )


class DistributionRendererTests(unittest.TestCase):
    def test_filedirector_named_group_includes_basic_excluded_choice(self):
        selected = package(optional_state=2, membership_id=44)
        group = AdvancedOptionalGroup(
            id=3,
            build_id=7,
            name="Choose a map",
            description="",
            selection_type=SINGLE,
            sort_order=0,
            items=[
                AdvancedOptionalItem(
                    id=9,
                    group_id=3,
                    build_modversion_id=44,
                    selected_by_default=True,
                    sort_order=0,
                    optional_state=2,
                )
            ],
        )

        content = FileDirectorExport.bundle(
            [selected], REPOSITORY, optional_groups=[group]
        )
        entry = json.loads(content)["url"][0]

        self.assertEqual(entry["installationPolicy"]["optionalKey"], "Choose a map")
        self.assertTrue(entry["installationPolicy"]["selectedByDefault"])

    def test_filedirector_omits_ungrouped_basic_excluded_package(self):
        content = FileDirectorExport.bundle(
            [package(optional_state=2, membership_id=44)], REPOSITORY
        )
        self.assertEqual(json.loads(content)["url"], [])

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

    def test_packwiz_removes_minecraft_prefix_from_loader_version(self):
        selected_build = DistributionBuild(
            **{
                **build().__dict__,
                "minecraft": "1.7.10",
                "modloader_version": "1.7.10-10.13.4.1614",
            }
        )
        content = PackwizExport.pack_toml(
            selected_build, b'hash-format = "sha256"\n'
        )
        self.assertEqual(
            tomllib.loads(content.decode())["versions"]["forge"],
            "10.13.4.1614",
        )

    def test_hybrid_renderers_use_modrinth_url_with_stored_jar_md5(self):
        selected = package(integration_version_id="version")
        native = SimpleNamespace(
            download_url="https://cdn.modrinth.com/data/project/versions/version/mod.jar"
        )
        mod_content = PackwizExport.mod_toml(selected, REPOSITORY, native)
        bundle_content = FileDirectorExport.bundle(
            [selected], REPOSITORY, native_files={"version": native}
        )

        mod_data = tomllib.loads(mod_content.decode())
        bundle_entry = json.loads(bundle_content)["url"][0]
        self.assertEqual(mod_data["download"]["url"], native.download_url)
        self.assertEqual(mod_data["download"]["hash"], JAR_MD5)
        self.assertEqual(bundle_entry["url"], native.download_url)
        self.assertEqual(bundle_entry["metadata"]["hash"], {"MD5": JAR_MD5})

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
                "optionalKey": "$",
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
        self.assertFalse(values[DistributionSettings.MCIL])
        self.assertFalse(values[DistributionSettings.SOLDERPY_LOADER])
        self.assertFalse(values[DistributionSettings.FILEDIRECTOR])
        self.assertFalse(values[DistributionSettings.MODPACK_DIRECTOR])
        self.assertFalse(values[DistributionSettings.PRISM])
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()

    def test_settings_update_all_values_in_one_transaction(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        with patch(
            "models.distribution_settings.Database.get_connection",
            return_value=connection,
        ):
            DistributionSettings.update_exports(
                packwiz=True,
                filedirector=False,
                modpack_director=True,
                mrpack=True,
                curseforge=False,
                mcil=True,
                solderpy_loader=True,
                prism=True,
            )

        self.assertEqual(cursor.execute.call_count, 8)
        first_parameters = cursor.execute.call_args_list[0].args[1]
        second_parameters = cursor.execute.call_args_list[1].args[1]
        third_parameters = cursor.execute.call_args_list[2].args[1]
        fourth_parameters = cursor.execute.call_args_list[3].args[1]
        fifth_parameters = cursor.execute.call_args_list[4].args[1]
        sixth_parameters = cursor.execute.call_args_list[5].args[1]
        seventh_parameters = cursor.execute.call_args_list[6].args[1]
        eighth_parameters = cursor.execute.call_args_list[7].args[1]
        self.assertEqual(first_parameters, ("mcil_enabled", "1", "1"))
        self.assertEqual(
            second_parameters, ("solderpy_loader_enabled", "1", "1")
        )
        self.assertEqual(third_parameters, ("packwiz_enabled", "1", "1"))
        self.assertEqual(
            fourth_parameters, ("filedirector_enabled", "0", "0")
        )
        self.assertEqual(
            fifth_parameters, ("modpack_director_enabled", "1", "1")
        )
        self.assertEqual(sixth_parameters, ("mrpack_enabled", "1", "1"))
        self.assertEqual(
            seventh_parameters, ("curseforge_export_enabled", "0", "0")
        )
        self.assertEqual(
            eighth_parameters, ("prism_export_enabled", "1", "1")
        )
        connection.commit.assert_called_once_with()
        connection.rollback.assert_not_called()

    def test_current_schema_contains_shared_distribution_settings(self):
        schema = Database.SOLDER_SETTINGS_TABLE_SQL
        self.assertIn("solder_settings", schema)
        self.assertIn("PRIMARY KEY", schema)
        self.assertIn("DEFAULT CURRENT_TIMESTAMP", schema)
        self.assertIn("ON UPDATE CURRENT_TIMESTAMP", schema)


class PlatformExportOverrideTests(unittest.TestCase):
    def test_schema_seeds_tx_loader_disabled(self):
        schema = Database.PLATFORM_EXPORT_OVERRIDES_TABLE_SQL
        default = Database.PLATFORM_EXPORT_OVERRIDES_DEFAULT_SQL

        self.assertIn("platform_export_overrides", schema)
        self.assertIn("UNIQUE KEY", schema)
        self.assertIn("eh8us8FY", default)
        self.assertIn("706505", default)
        self.assertIn("'CLIENT', 0, 0, 1", default)

    def test_enabled_overrides_are_loaded_as_typed_rows(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchall.return_value = [
            {
                "id": 1,
                "name": "TX Loader",
                "modrinth_project_id": "eh8us8FY",
                "curseforge_project_id": 706505,
                "side": "CLIENT",
                "enabled": 1,
                "built_in": 1,
            }
        ]
        with patch(
            "models.platform_export_override.Database.get_connection",
            return_value=connection,
        ):
            overrides = PlatformExportOverride.get_enabled()

        self.assertEqual(len(overrides), 1)
        self.assertEqual(overrides[0].modrinth_project_id, "eh8us8FY")
        self.assertTrue(overrides[0].enabled)
        cursor.execute.assert_called_once_with(
            "SELECT * FROM platform_export_overrides WHERE enabled = %s "
            "ORDER BY built_in DESC, name, id",
            (1,),
        )

    def test_custom_override_is_disabled_when_created(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        with patch(
            "models.platform_export_override.Database.get_connection",
            return_value=connection,
        ):
            PlatformExportOverride.create(
                "Bootstrap", "project_1", "12345", "SERVER"
            )

        parameters = cursor.execute.call_args.args[1]
        self.assertEqual(
            parameters, ("Bootstrap", "project_1", 12345, "SERVER", 0)
        )
        connection.commit.assert_called_once_with()

    def test_built_in_override_cannot_be_deleted(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.rowcount = 0
        with (
            patch(
                "models.platform_export_override.Database.get_connection",
                return_value=connection,
            ),
            self.assertRaisesRegex(
                PlatformExportOverrideError, "Built-in"
            ),
        ):
            PlatformExportOverride.delete(1)

        connection.rollback.assert_called_once_with()

    def test_sync_manifest_round_trip_fields_are_portable(self):
        mapping = PlatformExportOverride(
            id=1,
            name="TX Loader",
            modrinth_project_id="eh8us8FY",
            curseforge_project_id=706505,
            side="CLIENT",
            enabled=True,
            built_in=True,
            override_solder_only=True,
        )
        with patch.object(
            PlatformExportOverride, "get_all", return_value=[mapping]
        ):
            payload = json.loads(
                PlatformExportOverride.render_manifest().read().decode("utf-8")
            )

        self.assertEqual(
            payload["format"], "solder.py-modrinth-curseforge-sync"
        )
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["mappings"][0]["curseforge_project_id"], 706505)
        self.assertTrue(payload["mappings"][0]["override_solder_only"])

    def test_sync_manifest_updates_existing_and_adds_new_mapping(self):
        payload = {
            "format": "solder.py-modrinth-curseforge-sync",
            "version": 1,
            "mappings": [
                {
                    "name": "TX Loader",
                    "modrinth_project_id": "eh8us8FY",
                    "curseforge_project_id": 706505,
                    "side": "CLIENT",
                    "enabled": True,
                    "override_solder_only": False,
                },
                {
                    "name": "Bootstrap",
                    "modrinth_project_id": "project_2",
                    "curseforge_project_id": 12345,
                    "side": "BOTH",
                    "enabled": False,
                    "override_solder_only": True,
                },
            ],
        }
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchall.side_effect = [[{"id": 1}], []]
        with patch(
            "models.platform_export_override.Database.get_connection",
            return_value=connection,
        ):
            result = PlatformExportOverride.import_manifest(
                io.BytesIO(json.dumps(payload).encode("utf-8"))
            )

        self.assertEqual(result, (1, 1))
        self.assertTrue(
            any(
                "UPDATE platform_export_overrides" in call.args[0]
                for call in cursor.execute.call_args_list
            )
        )
        self.assertTrue(
            any(
                "INSERT INTO platform_export_overrides" in call.args[0]
                for call in cursor.execute.call_args_list
            )
        )
        connection.commit.assert_called_once_with()
        connection.rollback.assert_not_called()


class DistributionDeploymentDocumentationTests(unittest.TestCase):
    def test_caddy_compose_routes_dynamic_exports_and_includes_mysql(self):
        readme = (
            Path(__file__).resolve().parents[1] / "README.md"
        ).read_text(encoding="utf-8")

        self.assertIn("handle /packwiz/*", readme)
        self.assertIn("handle /filedirector/*", readme)
        self.assertIn("handle /modpackdirector/*", readme)
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
            modpack_director = self.client.get(
                "/modpackdirector/example-pack/1.0/mods.bundle.json"
            )

        self.assertEqual(packwiz.status_code, 404)
        self.assertEqual(filedirector.status_code, 404)
        self.assertEqual(modpack_director.status_code, 404)

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
        load.assert_called_once_with(
            7, optional=None, include_excluded=True
        )

    def test_modpack_director_uses_the_compatible_bundle_schema(self):
        with (
            patch.object(
                routes.DistributionSettings, "is_enabled", return_value=True
            ) as enabled,
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
                "/modpackdirector/example-pack/1.0/mods.bundle.json"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.get_json()["url"]), 1)
        enabled.assert_called_once_with(DistributionSettings.MODPACK_DIRECTOR)

    def test_filedirector_modrinth_fallback_excludes_native_files(self):
        native = package(
            integration_provider="MODRINTH",
            integration_project_id="project",
            integration_version_id="version",
        )
        fallback = package("manual-mod")
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
                return_value=[native, fallback],
            ),
        ):
            response = self.client.get(
                "/filedirector/example-pack/1.0/modrinth-fallback.bundle.json"
            )

        self.assertEqual(response.status_code, 200)
        entries = response.get_json()["url"]
        self.assertEqual(len(entries), 1)
        self.assertIn("manual-mod-2.0.jar", entries[0]["url"])

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

    def test_modpack_director_remote_and_version_routes_use_own_namespace(self):
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
            remote = self.client.get(
                "/modpackdirector/example-pack/recommended/mods.remote.json"
            )
            version = self.client.get(
                "/modpackdirector/example-pack/recommended/version.txt"
            )

        self.assertEqual(
            remote.get_json()["url"],
            "https://solder.example.test/modpackdirector/example-pack/"
            "recommended/mods.bundle.json",
        )
        self.assertEqual(version.get_data(as_text=True), "1.0\n")

    def test_hybrid_hosted_routes_use_validated_modrinth_urls(self):
        native_package = package(
            integration_provider="MODRINTH",
            integration_project_id="project",
            integration_version_id="version",
        )
        native_file = SimpleNamespace(
            download_url="https://cdn.modrinth.com/data/project/versions/version/mod.jar"
        )
        with (
            patch.object(
                routes.DistributionSettings, "is_enabled", return_value=True
            ),
            patch.object(
                routes.DistributionExport, "load_build", return_value=build()
            ),
            patch.object(
                routes.DistributionExport,
                "load_package",
                return_value=native_package,
            ),
            patch.object(
                routes.DistributionExport,
                "load_packages",
                return_value=[native_package],
            ),
            patch.object(
                routes.PlatformPackExport,
                "native_modrinth_files",
                return_value={"version": native_file},
            ),
        ):
            packwiz = self.client.get(
                "/packwiz/example-pack/1.0/hybrid/mods/example-mod.pw.toml"
            )
            filedirector = self.client.get(
                "/filedirector/example-pack/1.0/mods.bundle.json?source=hybrid"
            )

        self.assertEqual(packwiz.status_code, 200)
        self.assertEqual(filedirector.status_code, 200)
        self.assertEqual(
            tomllib.loads(packwiz.text)["download"]["url"],
            native_file.download_url,
        )
        self.assertEqual(
            filedirector.get_json()["url"][0]["url"],
            native_file.download_url,
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
