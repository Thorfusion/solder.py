import unittest

from models.compatibility import (
    minecraft_version_storage,
    normalize_minecraft_versions,
    normalize_modloaders,
    primary_modloader,
    version_is_compatible,
)


class CompatibilityTests(unittest.TestCase):
    def test_multiple_minecraft_and_modloader_values_are_canonical(self):
        self.assertEqual(
            normalize_minecraft_versions(["1.20.1", "1.20.2", "1.20.1"]),
            "1.20.1,1.20.2",
        )
        self.assertEqual(
            normalize_modloaders(["forge", "neoforge", "FORGE"]),
            "FORGE,NEOFORGE",
        )
        self.assertEqual(primary_modloader("FORGE,NEOFORGE"), "FORGE")

    def test_version_matches_any_declared_compatibility_pair(self):
        self.assertTrue(
            version_is_compatible(
                "1.20.1,1.20.2", "FORGE,NEOFORGE", "1.20.2", "NEOFORGE"
            )
        )
        self.assertFalse(
            version_is_compatible(
                "1.20.1,1.20.2", "FORGE,NEOFORGE", "1.21.1", "NEOFORGE"
            )
        )

    def test_multiple_minecraft_versions_use_related_storage(self):
        marker, versions = minecraft_version_storage(
            ["1.20.1", "1.20.2", "1.20.1"]
        )
        self.assertEqual(marker, "MULTI")
        self.assertEqual(versions, ("1.20.1", "1.20.2"))
        self.assertTrue(
            version_is_compatible(
                "MULTI", "FORGE", "1.20.2", "FORGE", versions
            )
        )
        self.assertFalse(
            version_is_compatible(
                "MULTI", "FORGE", "1.21.1", "FORGE", versions
            )
        )
        self.assertFalse(
            version_is_compatible(
                "MULTI", "FORGE", "1.20.1", "FORGE", None
            )
        )
        self.assertFalse(
            version_is_compatible(
                "1.20.1,1.20.2", "FORGE,NEOFORGE", "1.20.2", "FABRIC"
            )
        )


if __name__ == "__main__":
    unittest.main()
