import unittest
from types import SimpleNamespace

from models.bootstrap_manifest import BootstrapManifest, BootstrapManifestError


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
        filesize=100,
        optional=state,
    )


def modpack():
    return SimpleNamespace(
        id=1,
        slug="example",
        name="Example",
        optional_mode=1,
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
        self.assertIsNone(result["filesize"])
        self.assertEqual(result["download"]["format"], "jar")
        self.assertEqual(
            result["download"]["path"], "mods/example-mod-1.0.jar"
        )
        self.assertNotIn("extract_to", result["download"])

    def test_mod_without_a_verified_raw_jar_is_rejected(self):
        selected = package(4, 10, "example-mod")
        selected.jarmd5 = "0"

        with self.assertRaisesRegex(
            BootstrapManifestError, "does not have a verified raw JAR"
        ):
            BootstrapManifest.render(
                modpack(),
                build(1, "1.0"),
                [selected],
                [],
                "https://cdn.example.test/mods/",
                {},
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
        self.assertEqual(changes["updated"], [])

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


if __name__ == "__main__":
    unittest.main()
