import hashlib
import io
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from tests.environment import configure_test_environment


configure_test_environment()

from models.database import Database  # noqa: E402
from models.integration import (  # noqa: E402
    MAVEN,
    ExternalVersion,
    IntegrationError,
    MavenProvider,
    provider_for_user,
)
from models.maven import (  # noqa: E402
    MavenArtifact,
    MavenError,
    MavenMetadataClient,
    MavenRepository,
    MavenVersion,
    artifact_file_url,
    normalize_base_url,
    split_maven_version,
)


def bytes_response(content=b"", status=200, headers=None):
    response = Mock()
    response.status_code = status
    response.headers = headers or {"Content-Length": str(len(content))}
    response.iter_content.return_value = [content]
    return response


def artifact(**overrides):
    values = {
        "id": 7,
        "repository_id": 2,
        "repository_name": "Thorfusion",
        "repository_url": "https://maven.example.test/releases/",
        "group_id": "mekanism",
        "artifact_id": "Mekanism-Community-Edition",
        "classifier": "ALL",
        "extension": "jar",
        "version_mode": "EMBEDDED",
        "version_pattern": "{minecraft}-{version}",
        "fixed_minecraft": None,
        "modloader": "FORGE",
        "slug": "mekanism-community-edition",
        "title": "Mekanism Community Edition",
        "description": "A mod",
        "author": "Thorfusion",
        "link": "https://github.com/Thorfusion/Mekanism-Community-Edition",
        "side": "BOTH",
        "mod_id": 12,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class MavenRuleTests(unittest.TestCase):
    def test_embedded_rule_marks_the_minecraft_part(self):
        self.assertEqual(
            split_maven_version(
                "1.7.10-9.10.48",
                "EMBEDDED",
                "{minecraft}-{version}",
                None,
            ),
            ("1.7.10", "9.10.48"),
        )
        self.assertEqual(
            split_maven_version(
                "5.2.0+mc1.20.1",
                "EMBEDDED",
                "{version}+mc{minecraft}",
                None,
            ),
            ("1.20.1", "5.2.0"),
        )

    def test_fixed_rule_applies_one_minecraft_version_to_every_release(self):
        self.assertEqual(
            split_maven_version("9.10.48", "FIXED", "", "1.7.10"),
            ("1.7.10", "9.10.48"),
        )

    def test_manual_rule_does_not_guess_compatibility(self):
        self.assertEqual(
            split_maven_version("release-48", "MANUAL", "", None),
            (None, None),
        )

    def test_embedded_rule_requires_both_placeholders(self):
        with self.assertRaisesRegex(MavenError, "must contain"):
            split_maven_version(
                "1.7.10-9.10.48", "EMBEDDED", "{minecraft}", None
            )


class MavenMetadataTests(unittest.TestCase):
    def test_standard_metadata_lists_versions_without_downloading_jars(self):
        metadata = b"""<?xml version="1.0"?>
        <metadata><groupId>mekanism</groupId>
        <artifactId>Mekanism-Community-Edition</artifactId><versioning><versions>
        <version>1.7.10-9.10.47</version><version>1.7.10-9.10.48</version>
        </versions></versioning></metadata>"""
        http = Mock()
        http.get.return_value = bytes_response(metadata)
        repository = MavenRepository(
            2, "Thorfusion", "https://maven.example.test/releases/"
        )

        versions = MavenMetadataClient(repository, http=http).versions(
            "mekanism", "Mekanism-Community-Edition"
        )

        self.assertEqual(
            versions, ["1.7.10-9.10.47", "1.7.10-9.10.48"]
        )
        requested_url = http.get.call_args.args[0]
        self.assertEqual(
            requested_url,
            "https://maven.example.test/releases/mekanism/"
            "Mekanism-Community-Edition/maven-metadata.xml",
        )
        self.assertNotIn(".jar", requested_url)

    def test_metadata_redirect_to_another_origin_is_rejected(self):
        http = Mock()
        http.get.return_value = bytes_response(
            status=302, headers={"Location": "https://attacker.example/metadata.xml"}
        )
        repository = MavenRepository(
            2, "Example", "https://maven.example.test/releases/"
        )

        with self.assertRaisesRegex(MavenError, "another host"):
            MavenMetadataClient(repository, http=http).versions(
                "example.group", "example-artifact"
            )

        self.assertEqual(http.get.call_count, 1)

    def test_unsafe_xml_entities_are_rejected(self):
        http = Mock()
        http.get.return_value = bytes_response(
            b'<!DOCTYPE metadata [<!ENTITY x "unsafe">]><metadata>&x;</metadata>'
        )
        repository = MavenRepository(
            2, "Example", "https://maven.example.test/releases/"
        )
        with self.assertRaisesRegex(MavenError, "unsafe XML"):
            MavenMetadataClient(repository, http=http).versions(
                "example.group", "example-artifact"
            )

    def test_checksum_prefers_the_strongest_available_sidecar(self):
        sha256 = "a" * 64
        http = Mock()
        http.get.side_effect = [
            bytes_response(status=404),
            bytes_response(f"{sha256}  example.jar\n".encode()),
        ]
        repository = MavenRepository(
            2, "Example", "https://maven.example.test/releases/"
        )

        checksums = MavenMetadataClient(repository, http=http).checksums(
            "https://maven.example.test/releases/example/example/1/example-1.jar"
        )

        self.assertEqual(checksums, {"sha256": sha256})

    def test_empty_checksum_sidecar_is_ignored(self):
        sha1 = "b" * 40
        http = Mock()
        http.get.side_effect = [
            bytes_response(status=404),
            bytes_response(b""),
            bytes_response(f"{sha1}  example.jar\n".encode()),
        ]
        repository = MavenRepository(
            2, "Example", "https://maven.example.test/releases/"
        )

        checksums = MavenMetadataClient(repository, http=http).checksums(
            "https://maven.example.test/releases/example/example/1/example-1.jar"
        )

        self.assertEqual(checksums, {"sha1": sha1})

    def test_snapshot_metadata_selects_the_matching_classifier(self):
        metadata = b"""<metadata><versioning><snapshotVersions>
        <snapshotVersion><extension>jar</extension><classifier>ALL</classifier>
        <value>1.0-20260913.120000-4</value><updated>20260913120000</updated>
        </snapshotVersion>
        <snapshotVersion><extension>jar</extension><classifier>sources</classifier>
        <value>1.0-20260913.120000-4</value><updated>20260913120000</updated>
        </snapshotVersion></snapshotVersions></versioning></metadata>"""
        http = Mock()
        http.get.return_value = bytes_response(metadata)
        repository = MavenRepository(
            2, "Example", "https://maven.example.test/releases/"
        )

        value = MavenMetadataClient(repository, http=http).snapshot_value(
            artifact(), "1.0-SNAPSHOT"
        )

        self.assertEqual(value, "1.0-20260913.120000-4")

    def test_snapshot_metadata_supports_timestamp_and_build_number(self):
        metadata = b"""<metadata><versioning><snapshot>
        <timestamp>20260913.120000</timestamp><buildNumber>4</buildNumber>
        </snapshot></versioning></metadata>"""
        http = Mock()
        http.get.return_value = bytes_response(metadata)
        repository = MavenRepository(
            2, "Example", "https://maven.example.test/releases/"
        )

        value = MavenMetadataClient(repository, http=http).snapshot_value(
            artifact(), "1.0-SNAPSHOT"
        )

        self.assertEqual(value, "1.0-20260913.120000-4")


class MavenProviderTests(unittest.TestCase):
    def test_provider_is_selected_by_the_generic_integration_flow(self):
        self.assertIsInstance(provider_for_user(MAVEN, 4), MavenProvider)

    def test_compatible_versions_use_metadata_order_and_explicit_mapping(self):
        configured = artifact()
        mapping = MavenVersion(
            1,
            configured.id,
            "1.7.10-9.10.48",
            "f" * 64,
            "1.7.10",
            "9.10.48",
            "FORGE",
            "RULE",
            True,
            True,
            24,
        )
        with (
            patch("models.integration.MavenProvider._artifact", return_value=configured),
            patch("models.integration.MavenCatalog.refresh") as refresh,
            patch(
                "models.integration.MavenVersion.get_compatible",
                return_value=[mapping],
            ) as get_compatible,
        ):
            versions = MavenProvider(http=Mock()).list_versions(
                "7", "1.7.10", "FORGE"
            )

        refresh.assert_called_once()
        get_compatible.assert_called_once_with(7, "1.7.10", "FORGE")
        self.assertEqual(versions[0].version_number, "9.10.48")
        self.assertEqual(versions[0].version_id, "f" * 64)
        self.assertEqual(versions[0].loaders, ("FORGE",))

    def test_selected_version_uses_exact_maven_coordinates_and_checksum(self):
        configured = artifact()
        mapping = MavenVersion(
            1, 7, "1.7.10-9.10.48", "f" * 64, "1.7.10", "9.10.48",
            "FORGE", "RULE", True, True, 24,
        )
        with (
            patch("models.integration.MavenProvider._artifact", return_value=configured),
            patch(
                "models.integration.MavenVersion.get_by_integration_id",
                return_value=mapping,
            ),
            patch(
                "models.integration.MavenMetadataClient.snapshot_value",
                return_value="1.7.10-9.10.48",
            ),
            patch(
                "models.integration.MavenMetadataClient.checksums",
                return_value={"sha256": "a" * 64},
            ) as checksums,
        ):
            version = MavenProvider(http=Mock()).get_version(
                "7", "f" * 64, "1.7.10", "FORGE"
            )

        expected = (
            "https://maven.example.test/releases/mekanism/"
            "Mekanism-Community-Edition/1.7.10-9.10.48/"
            "Mekanism-Community-Edition-1.7.10-9.10.48-ALL.jar"
        )
        self.assertEqual(version.download_url, expected)
        self.assertEqual(version.hashes, {"sha256": "a" * 64})
        checksums.assert_called_once_with(expected)

    def test_maven_download_without_sidecar_is_still_hashed_and_jar_checked(self):
        jar = io.BytesIO()
        with zipfile.ZipFile(jar, "w") as archive:
            archive.writestr("mcmod.info", "[]")
        jar_data = jar.getvalue()
        http = Mock()
        http.get.return_value = bytes_response(jar_data)
        provider = MavenProvider(http=http)
        provider._download_origin = ("https", "maven.example.test", None)
        version = ExternalVersion(
            MAVEN, "7", "f" * 64, "1.0", "1.0", ("1.7.10",),
            ("FORGE",), "release", None, "example.jar",
            "https://maven.example.test/example.jar", {}, 0,
        )

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory, "example.jar")
            size, md5 = provider.download(version, destination)

        self.assertEqual(size, len(jar_data))
        self.assertEqual(
            md5, hashlib.md5(jar_data, usedforsecurity=False).hexdigest()
        )

    def test_maven_download_verifies_sha256_sidecar(self):
        jar = io.BytesIO()
        with zipfile.ZipFile(jar, "w") as archive:
            archive.writestr("mcmod.info", "[]")
        jar_data = jar.getvalue()
        http = Mock()
        http.get.return_value = bytes_response(jar_data)
        provider = MavenProvider(http=http)
        provider._download_origin = ("https", "maven.example.test", None)
        version = ExternalVersion(
            MAVEN, "7", "f" * 64, "1.0", "1.0", ("1.7.10",),
            ("FORGE",), "release", None, "example.jar",
            "https://maven.example.test/example.jar",
            {"sha256": hashlib.sha256(jar_data).hexdigest()}, 0,
        )

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory, "example.jar")
            size, _md5 = provider.download(version, destination)

        self.assertEqual(size, len(jar_data))

    def test_maven_download_rejects_sha256_mismatch(self):
        jar = io.BytesIO()
        with zipfile.ZipFile(jar, "w") as archive:
            archive.writestr("mcmod.info", "[]")
        http = Mock()
        http.get.return_value = bytes_response(jar.getvalue())
        provider = MavenProvider(http=http)
        provider._download_origin = ("https", "maven.example.test", None)
        version = ExternalVersion(
            MAVEN, "7", "f" * 64, "1.0", "1.0", ("1.7.10",),
            ("FORGE",), "release", None, "example.jar",
            "https://maven.example.test/example.jar", {"sha256": "0" * 64}, 0,
        )

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(IntegrationError, "SHA256"):
                provider.download(version, Path(directory, "example.jar"))

    def test_incompatible_maven_mapping_is_rejected(self):
        configured = artifact()
        mapping = MavenVersion(
            1, 7, "1.7.10-9.10.48", "f" * 64, "1.7.10", "9.10.48",
            "FORGE", "RULE", True, True, 24,
        )
        with (
            patch("models.integration.MavenProvider._artifact", return_value=configured),
            patch(
                "models.integration.MavenVersion.get_by_integration_id",
                return_value=mapping,
            ),
        ):
            with self.assertRaisesRegex(IntegrationError, "Minecraft"):
                MavenProvider(http=Mock()).get_version(
                    "7", "f" * 64, "1.20.1", "FORGE"
                )


class MavenSchemaAndUiTests(unittest.TestCase):
    def test_schema_contains_catalog_tables_and_compatibility_index(self):
        schema = "\n".join(Database.MAVEN_TABLES_SQL)
        self.assertIn("maven_repositories", schema)
        self.assertIn("maven_artifacts", schema)
        self.assertIn("maven_versions", schema)
        self.assertIn("idx_maven_version_compatibility", schema)
        self.assertIn("DEFAULT CURRENT_TIMESTAMP", schema)
        self.assertIn("ON UPDATE CURRENT_TIMESTAMP", schema)

    def test_management_page_explains_all_mapping_modes(self):
        source = (
            Path(__file__).resolve().parents[1] / "templates" / "maven.html"
        ).read_text(encoding="utf-8")
        self.assertIn("Contained in Maven version", source)
        self.assertIn("Always one Minecraft version", source)
        self.assertIn("Manual per version", source)
        self.assertIn("{minecraft}", source)
        self.assertIn("{version}", source)

    def test_artifact_url_preserves_classifier_as_a_separate_coordinate(self):
        self.assertEqual(
            artifact_file_url(artifact(), "1.7.10-9.10.48"),
            "https://maven.example.test/releases/mekanism/"
            "Mekanism-Community-Edition/1.7.10-9.10.48/"
            "Mekanism-Community-Edition-1.7.10-9.10.48-ALL.jar",
        )

    def test_repository_url_normalization_does_not_accept_credentials(self):
        self.assertEqual(
            normalize_base_url("https://maven.example.test/releases"),
            "https://maven.example.test/releases/",
        )
        with self.assertRaises(MavenError):
            normalize_base_url("https://user:secret@maven.example.test/releases")


if __name__ == "__main__":
    unittest.main()
