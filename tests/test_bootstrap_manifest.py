import unittest
from types import SimpleNamespace

from models.bootstrap_manifest import BootstrapManifest


def package(identifier, membership_id, slug, state=0):
    return SimpleNamespace(
        id=identifier,
        mod_id=identifier,
        membership_id=membership_id,
        modname=slug,
        pretty_name=slug.title(),
        author="CI",
        description=None,
        link=None,
        version="1.0",
        mcversion="1.20.1",
        modloader="FORGE",
        side="BOTH",
        modtype="MOD",
        md5=str(identifier) * 32,
        jarmd5="a" * 32,
        jarfilesize=80,
        filesize=100,
        optional=state,
        integration_provider=None,
        integration_project_id=None,
        integration_version_id=None,
    )


def modpack(remove_unlisted_mod_files=False):
    return SimpleNamespace(
        id=1,
        slug="example",
        name="Example",
        optional_mode=1,
        remove_unlisted_mod_files=remove_unlisted_mod_files,
    )


def build(identifier, version):
    return SimpleNamespace(
        id=identifier,
        version=version,
        minecraft="1.20.1",
        modloader="FORGE",
        forge="47.3.0",
        min_java="17",
        java_runtime="java-runtime-gamma",
        min_memory=4096,
    )


def group(identifier, membership_id):
    return SimpleNamespace(
        id=identifier,
        name="Graphics",
        description="Choose one preset.",
        selection_type=1,
        is_single=True,
        sort_order=1,
        items=[
            SimpleNamespace(
                build_modversion_id=membership_id,
                modversion_id=membership_id,
                mod_slug="graphics",
                pretty_name="Graphics",
                version="1.0",
                optional_state=2,
                selected_by_default=True,
                sort_order=1,
            )
        ],
    )


class BootstrapManifestTests(unittest.TestCase):
    def test_modpack_can_request_strict_mod_folder_cleanup(self):
        manifest = BootstrapManifest.render(
            modpack(remove_unlisted_mod_files=True),
            build(1, "1.0"),
            [package(4, 10, "example-mod")],
            [],
            "https://cdn.example.test/mods/",
            {},
        )

        self.assertTrue(
            manifest["update_policy"]["remove_unlisted_mod_files"]
        )

    def test_launcher_owned_dependency_stays_in_the_complete_graph(self):
        dependent = package(4, 10, "dependent")
        dependency = package(5, 11, "dependency")
        manifest = BootstrapManifest.render(
            modpack(),
            build(1, "1.0"),
            [dependent, dependency],
            [],
            "https://cdn.example.test/mods/",
            {4: [{"id": 5, "name": "dependency"}]},
            install_owners={10: "loader", 11: "launcher"},
        )

        by_name = {item["name"]: item for item in manifest["packages"]}
        self.assertEqual(by_name["dependency"]["install_owner"], "launcher")
        self.assertFalse(by_name["dependency"]["bootstrap_managed"])
        self.assertTrue(by_name["dependent"]["dependencies"][0]["present"])
        self.assertEqual(
            by_name["dependent"]["dependencies"][0]["membership_id"], 11
        )
        self.assertEqual(manifest["selection_policy"]["required_memberships"], [10])

    def test_advanced_group_forces_loader_ownership(self):
        grouped = package(4, 10, "graphics", state=2)
        manifest = BootstrapManifest.render(
            modpack(),
            build(1, "1.0"),
            [grouped],
            [group(8, 10)],
            "https://cdn.example.test/mods/",
            {},
            install_owners={10: "launcher"},
        )

        self.assertEqual(manifest["packages"][0]["install_owner"], "loader")
        self.assertTrue(manifest["packages"][0]["bootstrap_managed"])

    def test_mod_download_uses_the_verified_raw_jar(self):
        manifest = BootstrapManifest.render(
            modpack(),
            build(1, "1.0"),
            [package(4, 10, "example-mod")],
            [],
            "https://cdn.example.test/mods/",
            {},
        )

        result = manifest["packages"][0]
        self.assertEqual(
            result["url"],
            "https://cdn.example.test/mods/example-mod/example-mod-1.0.jar",
        )
        self.assertEqual(result["md5"], "a" * 32)
        self.assertEqual(result["filesize"], 80)
        self.assertEqual(result["download"]["format"], "jar")
        self.assertEqual(
            result["download"]["path"], "mods/example-mod-1.0.jar"
        )
        self.assertNotIn("extract_to", result["download"])

    def test_mod_without_a_verified_raw_jar_uses_solder_zip(self):
        selected = package(4, 10, "example-mod")
        selected.jarmd5 = "0"

        manifest = BootstrapManifest.render(
            modpack(),
            build(1, "1.0"),
            [selected],
            [],
            "https://cdn.example.test/mods/",
            {},
        )

        result = manifest["packages"][0]
        self.assertEqual(
            result["url"],
            "https://cdn.example.test/mods/example-mod/example-mod-1.0.zip",
        )
        self.assertEqual(result["md5"], "4" * 32)
        self.assertEqual(result["filesize"], 100)
        self.assertEqual(result["download"]["format"], "solder_zip")
        self.assertEqual(result["download"]["extract_to"], ".")

    def test_modrinth_mod_prefers_native_jar_and_keeps_solder_fallback(self):
        selected = package(4, 10, "example-mod")
        selected.integration_provider = "MODRINTH"
        selected.integration_project_id = "project-id"
        selected.integration_version_id = "version-id"
        modrinth_url = (
            "https://cdn.modrinth.com/data/project-id/versions/"
            "version-id/upstream-name.jar"
        )

        manifest = BootstrapManifest.render(
            modpack(),
            build(1, "1.0"),
            [selected],
            [],
            "https://cdn.example.test/mods/",
            {},
            native_downloads={
                ("MODRINTH", "project-id", "version-id"): modrinth_url
            },
        )

        download = manifest["packages"][0]["download"]
        self.assertEqual(
            download["sources"],
            [
                {"provider": "modrinth", "url": modrinth_url},
                {
                    "provider": "solder",
                    "url": (
                        "https://cdn.example.test/mods/example-mod/"
                        "example-mod-1.0.jar"
                    ),
                },
            ],
        )
        self.assertEqual(download["url"], download["sources"][1]["url"])

    def test_solder_source_keeps_override_provider_and_solder_urls(self):
        selected = package(4, 10, "example-mod")
        selected.integration_provider = "MODRINTH"
        selected.integration_project_id = "project-id"
        selected.integration_version_id = "version-id"
        selected.jar_url_override = "https://override.example/mod.jar"
        selected.download_source_provider = "MODRINTH"
        selected.download_source_url = "https://cdn.modrinth.com/mod.jar"

        manifest = BootstrapManifest.render(
            modpack(),
            build(1, "1.0"),
            [selected],
            [],
            "https://cdn.example.test/mods/",
            {},
            source_mode="solder",
        )

        download = manifest["packages"][0]["download"]
        self.assertEqual(
            download["sources"],
            [
                {"provider": "override", "url": "https://override.example/mod.jar"},
                {"provider": "modrinth", "url": "https://cdn.modrinth.com/mod.jar"},
                {
                    "provider": "solder",
                    "url": "https://cdn.example.test/mods/example-mod/example-mod-1.0.jar",
                },
            ],
        )
        self.assertEqual(
            download["url"],
            "https://cdn.example.test/mods/example-mod/example-mod-1.0.jar",
        )
        self.assertEqual(manifest["source"], "solder")
        self.assertEqual(download["md5"], "a" * 32)
        self.assertEqual(download["filesize"], 80)

    def test_override_precedes_maven_and_solder_sources(self):
        selected = package(4, 10, "example-mod")
        selected.integration_provider = "MAVEN"
        selected.integration_project_id = "7"
        selected.integration_version_id = "version-id"
        selected.jar_url_override = "https://override.example/mod.jar"
        maven_url = "https://maven.example/releases/mod-1.0.jar"

        for source_mode in ("hybrid", "solder"):
            with self.subTest(source_mode=source_mode):
                manifest = BootstrapManifest.render(
                    modpack(),
                    build(1, "1.0"),
                    [selected],
                    [],
                    "https://cdn.example.test/mods/",
                    {},
                    native_downloads={
                        ("MAVEN", "7", "version-id"): maven_url
                    },
                    source_mode=source_mode,
                )

                self.assertEqual(
                    manifest["packages"][0]["download"]["sources"],
                    [
                        {
                            "provider": "override",
                            "url": "https://override.example/mod.jar",
                        },
                        {"provider": "maven", "url": maven_url},
                        {
                            "provider": "solder",
                            "url": (
                                "https://cdn.example.test/mods/example-mod/"
                                "example-mod-1.0.jar"
                            ),
                        },
                    ],
                )

    def test_config_download_remains_a_solder_zip(self):
        selected = package(4, 10, "config-pack")
        selected.modtype = "CONFIG"

        manifest = BootstrapManifest.render(
            modpack(),
            build(1, "1.0"),
            [selected],
            [],
            "https://cdn.example.test/mods/",
            {},
        )

        result = manifest["packages"][0]
        self.assertTrue(result["url"].endswith("config-pack-1.0.zip"))
        self.assertEqual(result["download"]["format"], "solder_zip")
        self.assertEqual(result["download"]["extract_to"], ".")
        self.assertTrue(result["enforce"])

    def test_package_can_disable_launch_enforcement(self):
        selected = package(4, 10, "config-pack")
        selected.modtype = "CONFIG"
        selected.enforce = False

        manifest = BootstrapManifest.render(
            modpack(),
            build(1, "1.0"),
            [selected],
            [],
            "https://cdn.example.test/mods/",
            {},
        )

        self.assertFalse(
            manifest["packages"][0]["enforce"]
        )

    def test_build_local_ids_do_not_report_selection_change(self):
        previous = BootstrapManifest.render(
            modpack(),
            build(1, "1.0"),
            [package(4, 10, "graphics", 2)],
            [group(5, 10)],
            "https://cdn.example.test/mods/",
            {},
        )
        current = BootstrapManifest.render(
            modpack(),
            build(2, "2.0"),
            [package(4, 20, "graphics", 2)],
            [group(9, 20)],
            "https://cdn.example.test/mods/",
            {},
        )

        changes = BootstrapManifest.changes(previous, current)

        self.assertFalse(changes["selection_changed"])
        self.assertFalse(changes["update_policy_changed"])
        self.assertEqual(changes["updated"], [])

    def test_mod_cleanup_policy_change_is_reported(self):
        previous = BootstrapManifest.render(
            modpack(),
            build(1, "1.0"),
            [package(4, 10, "example-mod")],
            [],
            "https://cdn.example.test/mods/",
            {},
        )
        current = BootstrapManifest.render(
            modpack(remove_unlisted_mod_files=True),
            build(2, "2.0"),
            [package(4, 10, "example-mod")],
            [],
            "https://cdn.example.test/mods/",
            {},
        )

        changes = BootstrapManifest.changes(previous, current)

        self.assertTrue(changes["update_policy_changed"])

    def test_selection_change_is_reported_without_artifact_update(self):
        previous_package = package(4, 10, "graphics", 2)
        current_package = package(4, 10, "graphics", 2)
        previous_group = group(5, 10)
        current_group = group(5, 10)
        current_group.items[0].selected_by_default = False
        previous = BootstrapManifest.render(
            modpack(),
            build(1, "1.0"),
            [previous_package],
            [previous_group],
            "https://cdn.example.test/mods/",
            {},
        )
        current = BootstrapManifest.render(
            modpack(),
            build(2, "2.0"),
            [current_package],
            [current_group],
            "https://cdn.example.test/mods/",
            {},
        )

        changes = BootstrapManifest.changes(previous, current)

        self.assertTrue(changes["selection_changed"])
        self.assertEqual(changes["updated"], [])

    def test_enforcement_policy_change_is_reported_as_package_update(self):
        previous_package = package(4, 10, "config-pack")
        current_package = package(4, 10, "config-pack")
        current_package.enforce = False
        previous = BootstrapManifest.render(
            modpack(),
            build(1, "1.0"),
            [previous_package],
            [],
            "https://cdn.example.test/mods/",
            {},
        )
        current = BootstrapManifest.render(
            modpack(),
            build(2, "2.0"),
            [current_package],
            [],
            "https://cdn.example.test/mods/",
            {},
        )

        changes = BootstrapManifest.changes(previous, current)

        self.assertEqual(len(changes["updated"]), 1)
        self.assertEqual(changes["updated"][0]["to"]["name"], "config-pack")
        self.assertFalse(
            changes["updated"][0]["to"]["enforce"]
        )


if __name__ == "__main__":
    unittest.main()
