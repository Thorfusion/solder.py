import io
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tests.environment import configure_test_environment


configure_test_environment()

from models.integration_manifest import (  # noqa: E402
    IntegrationManifest,
    IntegrationManifestError,
)


def upload(payload):
    return io.BytesIO(json.dumps(payload).encode("utf-8"))


class IntegrationManifestTests(unittest.TestCase):
    def test_parses_modrinth_metadata_and_fixed_maven_mapping(self):
        manifest = IntegrationManifest.parse(
            upload(
                {
                    "format": "solder.py-integration-manifest",
                    "version": 1,
                    "mods": [
                        {
                            "provider": "modrinth",
                            "project_id": "AANobbMI",
                            "name": "Sodium",
                            "author": "CaffeineMC",
                        },
                        {
                            "provider": "maven",
                            "repository": {
                                "name": "GTNH",
                                "url": "https://nexus.example/releases/",
                            },
                            "group_id": "com.github.GTNewHorizons",
                            "artifact_id": "ExampleArtifact",
                            "minecraft": {
                                "mode": "FIXED",
                                "version": "1.7.10",
                            },
                        },
                    ],
                }
            )
        )

        self.assertTrue(manifest.includes_maven)
        self.assertEqual(manifest.mods[0].metadata["author"], "CaffeineMC")
        self.assertEqual(manifest.mods[1].version_pattern, "{version}")
        self.assertEqual(manifest.mods[1].fixed_minecraft, "1.7.10")

    def test_rejects_unknown_provider_before_importing_anything(self):
        with self.assertRaisesRegex(IntegrationManifestError, "Mod 1"):
            IntegrationManifest.parse(
                upload(
                    {
                        "format": "solder.py-integration-manifest",
                        "version": 1,
                        "mods": [{"provider": "curseforge"}],
                    }
                )
            )

    def test_modrinth_import_passes_reviewed_metadata(self):
        manifest = IntegrationManifest.parse(
            upload(
                {
                    "format": "solder.py-integration-manifest",
                    "version": 1,
                    "mods": [
                        {
                            "provider": "modrinth",
                            "project_id": "AANobbMI",
                            "name": "Sodium",
                            "description": "Fast renderer",
                        }
                    ],
                }
            )
        )
        with patch(
            "models.integration_manifest.ModIntegration.import_project",
            return_value=(SimpleNamespace(id=9), True),
        ) as import_project:
            result = manifest.import_all(4)

        self.assertEqual(result.created, 1)
        self.assertEqual(result.errors, [])
        self.assertEqual(
            import_project.call_args.kwargs["metadata"]["description"],
            "Fast renderer",
        )

    def test_maven_import_creates_repository_and_artifact_from_coordinates(self):
        manifest = IntegrationManifest.parse(
            upload(
                {
                    "format": "solder.py-integration-manifest",
                    "version": 1,
                    "mods": [
                        {
                            "provider": "maven",
                            "repository": {
                                "name": "GTNH",
                                "url": "https://nexus.example/releases/",
                            },
                            "group_id": "com.github.GTNewHorizons",
                            "artifact_id": "ExampleArtifact",
                            "minecraft": {
                                "mode": "FIXED",
                                "version": "1.7.10",
                            },
                        }
                    ],
                }
            )
        )
        repository = SimpleNamespace(
            id=3,
            name="GTNH",
            base_url="https://nexus.example/releases/",
        )
        artifact = SimpleNamespace(id=8, mod_id=None)
        with (
            patch(
                "models.integration_manifest.MavenRepository.get_by_name",
                return_value=None,
            ),
            patch(
                "models.integration_manifest.MavenRepository.new",
                return_value=repository,
            ) as new_repository,
            patch(
                "models.integration_manifest.MavenArtifact.get_by_coordinates",
                return_value=None,
            ),
            patch(
                "models.integration_manifest.MavenArtifact.new",
                return_value=artifact,
            ) as new_artifact,
            patch("models.integration_manifest.MavenCatalog.refresh"),
            patch(
                "models.integration_manifest.ModIntegration.import_project",
                return_value=(SimpleNamespace(id=9), True),
            ),
            patch(
                "models.integration_manifest.MavenArtifact.attach_mod"
            ) as attach,
        ):
            result = manifest.import_all(4)

        self.assertEqual(result.created, 1)
        new_repository.assert_called_once_with(
            "GTNH", "https://nexus.example/releases/"
        )
        self.assertEqual(new_artifact.call_args.args[9], None)
        self.assertEqual(new_artifact.call_args.args[10], "ExampleArtifact")
        attach.assert_called_once_with(8, 9)


if __name__ == "__main__":
    unittest.main()
