import hashlib
from io import BytesIO
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.environment import configure_test_environment


configure_test_environment()

from models.platform_publishing import (  # noqa: E402
    AlreadyPublishedError,
    CURSEFORGE,
    MODRINTH,
    PlatformPublishing,
    PublicationClaim,
    PublicationTarget,
    PublishingConfigurationError,
    PublishingError,
)


class PlatformPublishingTests(unittest.TestCase):
    def target(self, provider):
        return PublicationTarget(
            id=7,
            modpack_id=3,
            provider_account_id=5,
            project_id="project-id" if provider == MODRINTH else "12345",
            enabled=True,
            provider=provider,
            account_name="Release account",
            modpack_name="Example Pack",
            modpack_slug="example-pack",
        )

    def build(self):
        return SimpleNamespace(
            id=11,
            version="2.0.0",
            minecraft="1.20.1",
            modloader="FORGE",
        )

    def test_new_account_stores_token_and_only_exposes_hint(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.lastrowid = 19

        with patch(
            "models.platform_publishing.Database.get_connection",
            return_value=connection,
        ):
            account_id = PlatformPublishing.save_account(
                None,
                MODRINTH,
                "Releases",
                "private-token",
                True,
                4,
            )

        self.assertEqual(account_id, 19)
        parameters = cursor.execute.call_args.args[1]
        self.assertEqual(parameters[0:2], (MODRINTH, "Releases"))
        self.assertEqual(parameters[2], "private-token")
        self.assertEqual(parameters[3], "oken")

    def test_accounts_are_selected_for_only_the_current_user(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchall.return_value = []

        with patch(
            "models.platform_publishing.Database.get_connection",
            return_value=connection,
        ):
            self.assertEqual(PlatformPublishing.get_accounts(42), [])

        query, parameters = cursor.execute.call_args.args
        self.assertIn("WHERE user_id = %s", query)
        self.assertEqual(parameters, (42,))

    def test_account_update_cannot_use_another_users_account(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = None

        with (
            patch(
                "models.platform_publishing.Database.get_connection",
                return_value=connection,
            ),
            self.assertRaisesRegex(
                PublishingConfigurationError,
                "account no longer exists",
            ),
        ):
            PlatformPublishing.save_account(
                19,
                MODRINTH,
                "Releases",
                "replacement-token",
                True,
                42,
            )

        query, parameters = cursor.execute.call_args_list[0].args
        self.assertIn("user_id = %s", query)
        self.assertEqual(parameters, (19, 42))
        connection.rollback.assert_called_once()

    def test_publish_target_lookup_requires_token_owner(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = {
            "id": 7,
            "modpack_id": 3,
            "provider_account_id": 5,
            "project_id": "project-id",
            "enabled": 1,
            "provider": MODRINTH,
            "account_name": "Releases",
            "token": "private-token",
            "modpack_name": "Example Pack",
            "modpack_slug": "example-pack",
        }

        with patch(
            "models.platform_publishing.Database.get_connection",
            return_value=connection,
        ):
            target, token = PlatformPublishing._target_for_publish(7, 3, 42)

        query, parameters = cursor.execute.call_args.args
        self.assertIn("accounts.user_id = %s", query)
        self.assertEqual(parameters, (7, 3, 42))
        self.assertEqual(target.id, 7)
        self.assertEqual(token, "private-token")

    def test_build_publication_state_selects_the_next_patch(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchall.return_value = [
            {"target_id": 7, "successful_runs": 2, "active_runs": 0},
            {"target_id": 8, "successful_runs": 1, "active_runs": 1},
        ]
        targets = [SimpleNamespace(id=7), SimpleNamespace(id=8)]

        with patch(
            "models.platform_publishing.Database.get_connection",
            return_value=connection,
        ):
            states = PlatformPublishing.get_build_publication_states(
                targets, 11, "2.9.9", 4
            )

        self.assertEqual(states[7].version_number, "2.9.9-Patch-2")
        self.assertEqual(states[7].patch_number, 2)
        self.assertTrue(states[7].published)
        self.assertFalse(states[7].locked)
        self.assertEqual(states[8].version_number, "2.9.9-Patch-1")
        self.assertTrue(states[8].locked)
        self.assertEqual(cursor.execute.call_args.args[1], (11, 4, 7, 8))

    def test_begin_run_reserves_the_next_patch_under_a_target_lock(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchone.side_effect = [(7,), (1, 0), ("b" * 64,)]
        cursor.lastrowid = 31

        with patch(
            "models.platform_publishing.Database.get_connection",
            return_value=connection,
        ):
            claim = PlatformPublishing._begin_run(
                7,
                11,
                "a" * 64,
                "release",
                "Example Pack",
                "2.9.9",
                4,
                expected_version="2.9.9-Patch-1",
            )

        self.assertEqual(claim.version_number, "2.9.9-Patch-1")
        self.assertEqual(claim.patch_number, 1)
        self.assertIn("FOR UPDATE", cursor.execute.call_args_list[0].args[0])
        insert_parameters = cursor.execute.call_args_list[3].args[1]
        self.assertEqual(
            insert_parameters[3],
            hashlib.sha256(b"build:11:release:2.9.9-Patch-1").hexdigest(),
        )
        self.assertEqual(insert_parameters[5], "Example Pack 2.9.9-Patch-1")
        connection.commit.assert_called_once_with()

    def test_begin_run_blocks_a_pending_or_unknown_attempt(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchone.side_effect = [(7,), (1, 1)]

        with (
            patch(
                "models.platform_publishing.Database.get_connection",
                return_value=connection,
            ),
            self.assertRaisesRegex(AlreadyPublishedError, "unknown result"),
        ):
            PlatformPublishing._begin_run(
                7, 11, "a" * 64, "release", "Example Pack", "2.9.9", 4
            )

        connection.rollback.assert_called_once_with()

    def test_begin_run_rejects_an_identical_patch_archive(self):
        connection = Mock()
        cursor = connection.cursor.return_value
        digest = "a" * 64
        cursor.fetchone.side_effect = [(7,), (1, 0), (digest,)]

        with (
            patch(
                "models.platform_publishing.Database.get_connection",
                return_value=connection,
            ),
            self.assertRaisesRegex(AlreadyPublishedError, "identical"),
        ):
            PlatformPublishing._begin_run(
                7, 11, digest, "release", "Example Pack", "2.9.9", 4
            )

        connection.rollback.assert_called_once_with()

    def test_modrinth_publish_uses_version_upload_contract(self):
        response = Mock(status_code=201)
        response.json.return_value = {"id": "remote-version"}
        http = Mock()
        http.post.return_value = response
        archive = BytesIO(b"mrpack contents")

        with (
            patch.object(
                PlatformPublishing,
                "_target_for_publish",
                return_value=(self.target(MODRINTH), "mrp_secret"),
            ),
            patch.object(
                PlatformPublishing,
                "_begin_run",
                return_value=PublicationClaim(
                    23,
                    "2.0.0-Patch-1",
                    "Example Pack 2.0.0-Patch-1",
                    1,
                ),
            ) as begin,
            patch.object(PlatformPublishing, "_finish_run") as finish,
        ):
            result = PlatformPublishing.publish(
                7,
                3,
                self.build(),
                archive,
                "example-pack-2.0.0.mrpack",
                "beta",
                "Changes",
                4,
                http=http,
            )

        self.assertEqual(result.remote_file_id, "remote-version")
        request = http.post.call_args
        self.assertEqual(
            request.args[0], "https://api.modrinth.com/v2/version"
        )
        self.assertEqual(request.kwargs["headers"]["Authorization"], "mrp_secret")
        metadata = json.loads(request.kwargs["files"]["data"][1])
        self.assertEqual(metadata["project_id"], "project-id")
        self.assertEqual(metadata["version_number"], "2.0.0-Patch-1")
        self.assertEqual(metadata["version_type"], "beta")
        self.assertEqual(metadata["game_versions"], ["1.20.1"])
        self.assertEqual(metadata["loaders"], ["forge"])
        self.assertEqual(metadata["file_parts"], ["file"])
        digest = hashlib.sha256(b"mrpack contents").hexdigest()
        self.assertEqual(begin.call_args.args[2], digest)
        self.assertEqual(begin.call_args.args[3], "beta")
        self.assertEqual(
            request.kwargs["files"]["file"][0],
            "example-pack-2.0.0-Patch-1.mrpack",
        )
        finish.assert_called_once_with(
            23, "SUCCEEDED", remote_file_id="remote-version"
        )
        response.close.assert_called_once_with()

    def test_curseforge_publish_uses_author_upload_contract(self):
        response = Mock(status_code=200)
        response.json.return_value = {"id": 9876}
        http = Mock()
        http.post.return_value = response

        with (
            patch.object(
                PlatformPublishing,
                "_target_for_publish",
                return_value=(self.target(CURSEFORGE), "cf_secret"),
            ),
            patch.object(
                PlatformPublishing,
                "_begin_run",
                return_value=PublicationClaim(
                    24,
                    "2.0.0-Patch-1",
                    "Example Pack 2.0.0-Patch-1",
                    1,
                ),
            ),
            patch.object(PlatformPublishing, "_finish_run") as finish,
        ):
            result = PlatformPublishing.publish(
                7,
                3,
                self.build(),
                BytesIO(b"curseforge contents"),
                "example-pack-2.0.0-curseforge.zip",
                "release",
                "Changes",
                4,
                http=http,
            )

        self.assertIsNone(result.remote_file_id)
        request = http.post.call_args
        self.assertEqual(
            request.args[0],
            "https://minecraft.curseforge.com/api/projects/12345/upload-file",
        )
        self.assertEqual(request.kwargs["headers"]["X-Api-Token"], "cf_secret")
        metadata = json.loads(request.kwargs["files"]["metadata"][1])
        self.assertEqual(
            metadata["displayName"], "Example Pack 2.0.0-Patch-1"
        )
        self.assertEqual(metadata["gameVersionNames"], ["1.20.1"])
        self.assertEqual(metadata["releaseType"], "release")
        self.assertEqual(
            request.kwargs["files"]["file"][0],
            "example-pack-2.0.0-curseforge-Patch-1.zip",
        )
        finish.assert_called_once_with(
            24, "SUCCEEDED", remote_file_id=None
        )
        response.json.assert_not_called()
        response.close.assert_called_once_with()

    def test_remote_failure_releases_deduplication_key_for_retry(self):
        response = Mock(status_code=401)
        response.json.return_value = {
            "error": "unauthorized",
            "description": "Invalid token",
        }
        http = Mock()
        http.post.return_value = response

        with (
            patch.object(
                PlatformPublishing,
                "_target_for_publish",
                return_value=(self.target(MODRINTH), "bad-token"),
            ),
            patch.object(
                PlatformPublishing,
                "_begin_run",
                return_value=PublicationClaim(
                    25, "2.0.0", "Example Pack 2.0.0", 0
                ),
            ),
            patch.object(PlatformPublishing, "_finish_run") as finish,
        ):
            with self.assertRaisesRegex(PublishingError, "HTTP 401"):
                PlatformPublishing.publish(
                    7,
                    3,
                    self.build(),
                    BytesIO(b"archive"),
                    "pack.mrpack",
                    "release",
                    "",
                    4,
                    http=http,
                )

        finish.assert_called_once_with(
            25,
            "FAILED",
            error_message="Modrinth rejected the upload (HTTP 401): Invalid token",
        )
        response.close.assert_called_once_with()

    def test_curseforge_failure_does_not_persist_response_data(self):
        response = Mock(status_code=401)
        response.json.return_value = {
            "error": "unauthorized",
            "description": "sensitive response detail",
        }
        http = Mock()
        http.post.return_value = response

        with (
            patch.object(
                PlatformPublishing,
                "_target_for_publish",
                return_value=(self.target(CURSEFORGE), "bad-token"),
            ),
            patch.object(
                PlatformPublishing,
                "_begin_run",
                return_value=PublicationClaim(
                    26, "2.0.0", "Example Pack 2.0.0", 0
                ),
            ),
            patch.object(PlatformPublishing, "_finish_run") as finish,
        ):
            with self.assertRaisesRegex(
                PublishingError, "CurseForge rejected the upload"
            ):
                PlatformPublishing.publish(
                    7,
                    3,
                    self.build(),
                    BytesIO(b"archive"),
                    "pack.zip",
                    "release",
                    "",
                    4,
                    http=http,
                )

        finish.assert_called_once_with(
            26,
            "FAILED",
            error_message="CurseForge rejected the upload.",
        )
        response.json.assert_not_called()
        response.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
