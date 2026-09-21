"""Database-backed credentials, targets, and direct platform publishing."""

from dataclasses import dataclass
import hashlib
import json
import re

from mysql import connector
import requests

from .database import Database
from .integration import USER_AGENT


MODRINTH = "MODRINTH"
CURSEFORGE = "CURSEFORGE"
SUPPORTED_PUBLISHING_PROVIDERS = (MODRINTH, CURSEFORGE)
RELEASE_TYPES = ("release", "beta", "alpha")
_MODRINTH_PROJECT_RE = re.compile(r"^[A-Za-z0-9_-]{2,64}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUEST_TIMEOUT = (10, 300)


class PublishingError(RuntimeError):
    """Raised for safe, user-facing publishing failures."""


class PublishingConfigurationError(PublishingError):
    """Raised when accounts or targets are invalid."""


class AlreadyPublishedError(PublishingError):
    """Raised when the build is already uploaded or in flight."""


class PublishingOutcomeUnknownError(PublishingError):
    """Raised when the provider may have accepted an upload."""


@dataclass(frozen=True)
class PublishingAccount:
    id: int
    provider: str
    name: str
    token_hint: str
    enabled: bool
    user_id: int
    created_at: object = None
    updated_at: object = None

    @property
    def provider_label(self):
        return "Modrinth" if self.provider == MODRINTH else "CurseForge"


@dataclass(frozen=True)
class PublicationTarget:
    id: int
    modpack_id: int
    provider_account_id: int
    project_id: str
    enabled: bool
    provider: str
    account_name: str
    modpack_name: str = ""
    modpack_slug: str = ""

    @property
    def provider_label(self):
        return "Modrinth" if self.provider == MODRINTH else "CurseForge"


@dataclass(frozen=True)
class PublicationResult:
    run_id: int
    provider: str
    remote_file_id: str | None
    artifact_sha256: str
    version_number: str
    patch_number: int


@dataclass(frozen=True)
class PublicationState:
    published: bool
    locked: bool
    version_number: str
    patch_number: int


@dataclass(frozen=True)
class PublicationClaim:
    run_id: int
    version_number: str
    display_name: str
    patch_number: int


class PlatformPublishing:
    """Manage provider accounts and publish generated pack archives."""

    @staticmethod
    def _release_version(base_version, patch_number):
        base_version = str(base_version)
        patch_number = int(patch_number)
        if patch_number <= 0:
            return base_version
        return f"{base_version}-Patch-{patch_number}"

    @staticmethod
    def _provider(value):
        provider = str(value or "").strip().upper()
        if provider not in SUPPORTED_PUBLISHING_PROVIDERS:
            raise PublishingConfigurationError(
                "Select a supported publishing provider."
            )
        return provider

    @staticmethod
    def _name(value):
        value = str(value or "").strip()
        if not value or len(value) > 255:
            raise PublishingConfigurationError(
                "Enter an account name no longer than 255 characters."
            )
        return value

    @staticmethod
    def _token(value):
        value = str(value or "").strip()
        if not value or len(value) > 4096:
            raise PublishingConfigurationError(
                "Enter a provider token no longer than 4096 characters."
            )
        return value

    @classmethod
    def _project_id(cls, provider, value):
        provider = cls._provider(provider)
        value = str(value or "").strip()
        if provider == MODRINTH:
            if _MODRINTH_PROJECT_RE.fullmatch(value) is None:
                raise PublishingConfigurationError(
                    "Enter a valid Modrinth project ID or slug."
                )
            return value
        try:
            project_id = int(value)
        except (TypeError, ValueError) as error:
            raise PublishingConfigurationError(
                "Enter a numeric CurseForge project ID."
            ) from error
        if project_id <= 0:
            raise PublishingConfigurationError(
                "Enter a numeric CurseForge project ID."
            )
        return str(project_id)

    @staticmethod
    def _account(row):
        return PublishingAccount(
            id=row["id"],
            provider=row["provider"],
            name=row["name"],
            token_hint=row.get("token_hint") or "",
            enabled=bool(row.get("enabled")),
            user_id=row["user_id"],
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    @staticmethod
    def _target(row):
        return PublicationTarget(
            id=row["id"],
            modpack_id=row["modpack_id"],
            provider_account_id=row["provider_account_id"],
            project_id=str(row["project_id"]),
            enabled=bool(row.get("enabled")),
            provider=row["provider"],
            account_name=row["account_name"],
            modpack_name=row.get("modpack_name") or "",
            modpack_slug=row.get("modpack_slug") or "",
        )

    @classmethod
    def get_accounts(cls, user_id):
        user_id = int(user_id)
        conn = Database.get_connection()
        if conn is None:
            return []
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT id, provider, name, token_hint, enabled,
                          user_id, created_at, updated_at
                   FROM publishing_provider_accounts
                   WHERE user_id = %s
                   ORDER BY provider, name, id"""
                ,
                (user_id,),
            )
            return [cls._account(row) for row in cur.fetchall()]
        finally:
            cur.close()
            conn.close()

    @classmethod
    def save_account(
        cls, account_id, provider, name, token, enabled, user_id
    ):
        provider = cls._provider(provider)
        name = cls._name(name)
        token = str(token or "").strip()
        user_id = int(user_id)
        stored_token = cls._token(token) if token else None
        hint = token[-4:] if token else None
        conn = Database.get_connection()
        cur = conn.cursor()
        try:
            if account_id:
                account_id = int(account_id)
                cur.execute(
                    """SELECT provider FROM publishing_provider_accounts
                       WHERE id = %s AND user_id = %s""",
                    (account_id, user_id),
                )
                existing = cur.fetchone()
                if existing is None:
                    raise PublishingConfigurationError(
                        "The publishing account no longer exists."
                    )
                existing_provider = (
                    existing[0] if not isinstance(existing, dict)
                    else existing["provider"]
                )
                if existing_provider != provider:
                    cur.execute(
                        """SELECT 1 FROM modpack_publication_targets
                           WHERE provider_account_id = %s LIMIT 1""",
                        (account_id,),
                    )
                    if cur.fetchone() is not None:
                        raise PublishingConfigurationError(
                            "Delete this account's modpack targets before "
                            "changing its provider."
                        )
                if stored_token is None:
                    cur.execute(
                        """UPDATE publishing_provider_accounts
                           SET provider = %s, name = %s, enabled = %s
                           WHERE id = %s AND user_id = %s""",
                        (
                            provider,
                            name,
                            int(bool(enabled)),
                            account_id,
                            user_id,
                        ),
                    )
                else:
                    cur.execute(
                        """UPDATE publishing_provider_accounts
                           SET provider = %s, name = %s,
                               token = %s, token_hint = %s,
                               enabled = %s
                           WHERE id = %s AND user_id = %s""",
                        (
                            provider,
                            name,
                            stored_token,
                            hint,
                            int(bool(enabled)),
                            account_id,
                            user_id,
                        ),
                    )
            else:
                if stored_token is None:
                    raise PublishingConfigurationError(
                        "A token is required for a new publishing account."
                    )
                cur.execute(
                    """INSERT INTO publishing_provider_accounts
                              (provider, name, token, token_hint,
                               enabled, user_id)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (
                        provider,
                        name,
                        stored_token,
                        hint,
                        int(bool(enabled)),
                        user_id,
                    ),
                )
                account_id = cur.lastrowid
            conn.commit()
            return account_id
        except connector.IntegrityError as error:
            conn.rollback()
            raise PublishingConfigurationError(
                "You already have a publishing account with that provider and name."
            ) from error
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def delete_account(account_id, user_id):
        user_id = int(user_id)
        conn = Database.get_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                """SELECT 1 FROM modpack_publication_targets
                   INNER JOIN publishing_provider_accounts accounts
                       ON accounts.id = modpack_publication_targets.provider_account_id
                   WHERE provider_account_id = %s AND accounts.user_id = %s
                   LIMIT 1""",
                (account_id, user_id),
            )
            if cur.fetchone() is not None:
                raise PublishingConfigurationError(
                    "Delete this account's modpack targets first."
                )
            cur.execute(
                """DELETE FROM publishing_provider_accounts
                   WHERE id = %s AND user_id = %s""",
                (account_id, user_id),
            )
            if cur.rowcount != 1:
                raise PublishingConfigurationError(
                    "The publishing account no longer exists."
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @classmethod
    def get_targets(cls, user_id, modpack_id=None, *, enabled_only=False):
        user_id = int(user_id)
        conn = Database.get_connection()
        if conn is None:
            return []
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT targets.id, targets.modpack_id,
                          targets.provider_account_id, targets.project_id,
                          targets.enabled, accounts.provider,
                          accounts.name AS account_name,
                          modpacks.name AS modpack_name,
                          modpacks.slug AS modpack_slug
                   FROM modpack_publication_targets targets
                   INNER JOIN publishing_provider_accounts accounts
                       ON accounts.id = targets.provider_account_id
                   INNER JOIN modpacks ON modpacks.id = targets.modpack_id"""
                " WHERE (%s = 0 OR targets.modpack_id = %s)"
                "   AND (%s = 0 OR (targets.enabled = 1 AND accounts.enabled = 1))"
                "   AND accounts.user_id = %s"
                " ORDER BY modpacks.name, accounts.provider, accounts.name",
                (
                    int(modpack_id is not None),
                    modpack_id if modpack_id is not None else 0,
                    int(bool(enabled_only)),
                    user_id,
                ),
            )
            return [cls._target(row) for row in cur.fetchall()]
        finally:
            cur.close()
            conn.close()

    @classmethod
    def get_build_publication_states(
        cls, targets, build_id, base_version, user_id
    ):
        """Return the next remote version and lock state for each target."""
        target_ids = tuple(
            dict.fromkeys(int(getattr(target, "id", target)) for target in targets)
        )
        states = {
            target_id: PublicationState(
                published=False,
                locked=False,
                version_number=cls._release_version(base_version, 0),
                patch_number=0,
            )
            for target_id in target_ids
        }
        if not target_ids:
            return states

        conn = Database.get_connection()
        if conn is None:
            return states
        cur = conn.cursor(dictionary=True)
        try:
            placeholders = ", ".join(["%s"] * len(target_ids))
            cur.execute(
                f"""SELECT targets.id AS target_id,
                            COALESCE(SUM(CASE
                                WHEN runs.status = 'SUCCEEDED' THEN 1 ELSE 0
                            END), 0) AS successful_runs,
                            COALESCE(SUM(CASE
                                WHEN runs.status IN ('PENDING', 'UNKNOWN')
                                THEN 1 ELSE 0
                            END), 0) AS active_runs
                     FROM modpack_publication_targets targets
                     INNER JOIN publishing_provider_accounts accounts
                         ON accounts.id = targets.provider_account_id
                     LEFT JOIN modpack_publication_runs runs
                         ON runs.target_id = targets.id
                        AND runs.build_id = %s
                     WHERE accounts.user_id = %s
                       AND targets.id IN ({placeholders})
                     GROUP BY targets.id""",  # nosec B608
                (int(build_id), int(user_id), *target_ids),
            )
            for row in cur.fetchall():
                target_id = int(row["target_id"])
                if target_id not in states:
                    continue
                successful_runs = int(row["successful_runs"] or 0)
                states[target_id] = PublicationState(
                    published=successful_runs > 0,
                    locked=int(row["active_runs"] or 0) > 0,
                    version_number=cls._release_version(
                        base_version, successful_runs
                    ),
                    patch_number=successful_runs,
                )
            return states
        finally:
            cur.close()
            conn.close()

    @classmethod
    def save_target(
        cls,
        target_id,
        modpack_id,
        provider_account_id,
        project_id,
        enabled,
        user_id,
    ):
        modpack_id = int(modpack_id)
        provider_account_id = int(provider_account_id)
        user_id = int(user_id)
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT provider FROM publishing_provider_accounts
                   WHERE id = %s AND user_id = %s""",
                (provider_account_id, user_id),
            )
            account = cur.fetchone()
            if account is None:
                raise PublishingConfigurationError(
                    "The selected publishing account does not exist."
                )
            cur.execute(
                """SELECT 1
                   FROM modpacks
                   INNER JOIN user_permissions
                       ON user_permissions.user_id = %s
                   LEFT JOIN user_modpack
                       ON user_modpack.user_id = %s
                      AND user_modpack.modpack_id = modpacks.id
                   WHERE modpacks.id = %s
                     AND (user_permissions.solder_full = 1
                          OR user_modpack.modpack_id IS NOT NULL)""",
                (user_id, user_id, modpack_id),
            )
            if cur.fetchone() is None:
                raise PublishingConfigurationError(
                    "The selected modpack does not exist."
                )
            project_id = cls._project_id(account["provider"], project_id)
            if target_id:
                cur.execute(
                    """UPDATE modpack_publication_targets
                       SET modpack_id = %s, provider_account_id = %s,
                           project_id = %s, enabled = %s
                       WHERE id = %s
                         AND provider_account_id IN (
                             SELECT id FROM publishing_provider_accounts
                             WHERE user_id = %s
                         )""",
                    (
                        modpack_id,
                        provider_account_id,
                        project_id,
                        int(bool(enabled)),
                        int(target_id),
                        user_id,
                    ),
                )
                if cur.rowcount != 1:
                    cur.execute(
                        """SELECT 1 FROM modpack_publication_targets targets
                           INNER JOIN publishing_provider_accounts accounts
                               ON accounts.id = targets.provider_account_id
                           WHERE targets.id = %s AND accounts.user_id = %s""",
                        (int(target_id), user_id),
                    )
                    if cur.fetchone() is None:
                        raise PublishingConfigurationError(
                            "The publication target no longer exists."
                        )
            else:
                cur.execute(
                    """INSERT INTO modpack_publication_targets
                              (modpack_id, provider_account_id, project_id,
                               enabled)
                       VALUES (%s, %s, %s, %s)""",
                    (
                        modpack_id,
                        provider_account_id,
                        project_id,
                        int(bool(enabled)),
                    ),
                )
                target_id = cur.lastrowid
            conn.commit()
            return target_id
        except connector.IntegrityError as error:
            conn.rollback()
            raise PublishingConfigurationError(
                "That account already has a target for this modpack."
            ) from error
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def delete_target(target_id, user_id):
        user_id = int(user_id)
        conn = Database.get_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                """SELECT 1 FROM modpack_publication_targets targets
                   INNER JOIN publishing_provider_accounts accounts
                       ON accounts.id = targets.provider_account_id
                   WHERE targets.id = %s AND accounts.user_id = %s""",
                (target_id, user_id),
            )
            if cur.fetchone() is None:
                raise PublishingConfigurationError(
                    "The publication target no longer exists."
                )
            cur.execute(
                "DELETE FROM modpack_publication_runs WHERE target_id = %s",
                (target_id,),
            )
            cur.execute(
                "DELETE FROM modpack_publication_targets WHERE id = %s",
                (target_id,),
            )
            if cur.rowcount != 1:
                raise PublishingConfigurationError(
                    "The publication target no longer exists."
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def get_recent_runs(user_id, limit=100):
        user_id = int(user_id)
        limit = max(1, min(int(limit), 500))
        conn = Database.get_connection()
        if conn is None:
            return []
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT runs.id, runs.build_id, runs.artifact_sha256,
                          runs.release_type, runs.display_name, runs.status,
                          runs.remote_file_id, runs.error_message,
                          runs.created_by_user_id, runs.created_at,
                          runs.updated_at, targets.project_id,
                          accounts.provider,
                          accounts.name AS account_name,
                          modpacks.name AS modpack_name
                   FROM modpack_publication_runs runs
                   INNER JOIN modpack_publication_targets targets
                       ON targets.id = runs.target_id
                   INNER JOIN publishing_provider_accounts accounts
                       ON accounts.id = targets.provider_account_id
                   INNER JOIN modpacks ON modpacks.id = targets.modpack_id
                   WHERE accounts.user_id = %s
                   ORDER BY runs.id DESC LIMIT %s""",
                (user_id, limit),
            )
            rows = cur.fetchall()
            for row in rows:
                row["provider_label"] = (
                    "Modrinth"
                    if row["provider"] == MODRINTH
                    else "CurseForge"
                )
                row["can_retry"] = row["status"] in {"PENDING", "UNKNOWN"}
            return rows
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def release_run_for_retry(run_id, user_id):
        user_id = int(user_id)
        conn = Database.get_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                """UPDATE modpack_publication_runs runs
                   INNER JOIN modpack_publication_targets targets
                       ON targets.id = runs.target_id
                   INNER JOIN publishing_provider_accounts accounts
                       ON accounts.id = targets.provider_account_id
                   SET status = 'FAILED', deduplication_key = NULL,
                       error_message = 'Retry manually approved by an administrator.'
                   WHERE runs.id = %s AND accounts.user_id = %s
                     AND runs.status IN ('PENDING', 'UNKNOWN')""",
                (run_id, user_id),
            )
            if cur.rowcount != 1:
                raise PublishingConfigurationError(
                    "Only pending or unknown publication attempts can be released."
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @classmethod
    def _target_for_publish(cls, target_id, modpack_id, user_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT targets.id, targets.modpack_id,
                          targets.provider_account_id, targets.project_id,
                          targets.enabled, accounts.provider,
                          accounts.name AS account_name,
                          accounts.token,
                          modpacks.name AS modpack_name,
                          modpacks.slug AS modpack_slug
                   FROM modpack_publication_targets targets
                   INNER JOIN publishing_provider_accounts accounts
                       ON accounts.id = targets.provider_account_id
                      AND accounts.enabled = 1
                   INNER JOIN modpacks ON modpacks.id = targets.modpack_id
                   WHERE targets.id = %s AND targets.modpack_id = %s
                     AND accounts.user_id = %s
                     AND targets.enabled = 1""",
                (target_id, modpack_id, int(user_id)),
            )
            row = cur.fetchone()
            if row is None:
                raise PublishingConfigurationError(
                    "The selected publication target is not enabled."
                )
            return cls._target(row), row["token"]
        finally:
            cur.close()
            conn.close()

    @classmethod
    def _begin_run(
        cls,
        target_id,
        build_id,
        digest,
        release_type,
        modpack_name,
        base_version,
        user_id,
        expected_version=None,
    ):
        conn = Database.get_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT id FROM modpack_publication_targets "
                "WHERE id = %s FOR UPDATE",
                (target_id,),
            )
            if cur.fetchone() is None:
                raise PublishingConfigurationError(
                    "The publication target no longer exists."
                )
            cur.execute(
                """SELECT
                           COALESCE(SUM(CASE
                               WHEN status = 'SUCCEEDED' THEN 1 ELSE 0
                           END), 0),
                           COALESCE(SUM(CASE
                               WHEN status IN ('PENDING', 'UNKNOWN')
                               THEN 1 ELSE 0
                           END), 0)
                   FROM modpack_publication_runs
                   WHERE target_id = %s AND build_id = %s""",
                (target_id, build_id),
            )
            successful_runs, active_runs = cur.fetchone() or (0, 0)
            if int(active_runs or 0) > 0:
                raise AlreadyPublishedError(
                    "This build already has a publishing attempt in progress "
                    "or with an unknown result. Check the provider before "
                    "allowing a retry."
                )
            if int(successful_runs or 0) > 0:
                cur.execute(
                    """SELECT artifact_sha256
                       FROM modpack_publication_runs
                       WHERE target_id = %s AND build_id = %s
                         AND status = 'SUCCEEDED'
                       ORDER BY id DESC LIMIT 1""",
                    (target_id, build_id),
                )
                latest_success = cur.fetchone()
                if latest_success and latest_success[0] == digest:
                    raise AlreadyPublishedError(
                        "The generated archive is identical to the last "
                        "successful upload. Change the build before sending "
                        "a patch."
                    )

            patch_number = int(successful_runs or 0)
            version_number = cls._release_version(base_version, patch_number)
            if (
                expected_version is not None
                and str(expected_version) != version_number
            ):
                raise AlreadyPublishedError(
                    "The publication state changed while the archive was being "
                    "created. Open Export again and retry."
                )
            display_name = f"{modpack_name} {version_number}"[:255]
            deduplication_key = hashlib.sha256(
                f"build:{build_id}:release:{version_number}".encode("utf-8")
            ).hexdigest()
            cur.execute(
                """INSERT INTO modpack_publication_runs
                          (target_id, build_id, artifact_sha256,
                           deduplication_key, release_type, display_name,
                           status, created_by_user_id)
                   VALUES (%s, %s, %s, %s, %s, %s, 'PENDING', %s)""",
                (
                    target_id,
                    build_id,
                    digest,
                    deduplication_key,
                    release_type,
                    display_name,
                    user_id,
                ),
            )
            run_id = cur.lastrowid
            conn.commit()
            return PublicationClaim(
                run_id=run_id,
                version_number=version_number,
                display_name=display_name,
                patch_number=patch_number,
            )
        except connector.IntegrityError as error:
            conn.rollback()
            raise AlreadyPublishedError(
                "This build has already been published to this target or is "
                "currently uploading."
            ) from error
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def _finish_run(run_id, status, *, remote_file_id=None, error_message=None):
        conn = Database.get_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                """UPDATE modpack_publication_runs
                   SET status = %s, remote_file_id = %s, error_message = %s,
                       deduplication_key = IF(%s IN ('SUCCEEDED', 'UNKNOWN'),
                                                deduplication_key, NULL)
                   WHERE id = %s""",
                (
                    status,
                    remote_file_id,
                    str(error_message or "")[:1000] or None,
                    status,
                    run_id,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def _response_error(provider, response):
        detail = None
        try:
            payload = response.json()
            if isinstance(payload, dict):
                detail = payload.get("description") or payload.get("error")
        except ValueError:
            pass
        suffix = f": {str(detail)[:300]}" if detail else ""
        return PublishingError(
            f"{provider} rejected the upload (HTTP {response.status_code}){suffix}"
        )

    @classmethod
    def _publish_modrinth(
        cls,
        target,
        token,
        build,
        archive,
        filename,
        version_number,
        release_type,
        changelog,
        http,
    ):
        loader = str(getattr(build, "modloader", "") or "minecraft").lower()
        if loader == "vanilla":
            loader = "minecraft"
        metadata = {
            "name": f"{target.modpack_name} {version_number}",
            "version_number": version_number,
            "changelog": changelog or None,
            "dependencies": [],
            "game_versions": [str(build.minecraft)],
            "version_type": release_type,
            "loaders": [loader],
            "featured": False,
            "status": "listed",
            "project_id": target.project_id,
            "file_parts": ["file"],
            "primary_file": "file",
            "environment": "client_and_server",
        }
        response = http.post(
            "https://api.modrinth.com/v2/version",
            headers={"Authorization": token, "User-Agent": USER_AGENT},
            files={
                "data": (
                    None,
                    json.dumps(metadata, separators=(",", ":")),
                    "application/json",
                ),
                "file": (
                    filename,
                    archive,
                    "application/x-modrinth-modpack+zip",
                ),
            },
            allow_redirects=False,
            timeout=_REQUEST_TIMEOUT,
        )
        try:
            if response.status_code not in {200, 201}:
                raise cls._response_error("Modrinth", response)
            try:
                return str(response.json()["id"])
            except (KeyError, TypeError, ValueError) as error:
                raise PublishingOutcomeUnknownError(
                    "Modrinth accepted the upload but returned an invalid "
                    "version response."
                ) from error
        finally:
            response.close()

    @classmethod
    def _publish_curseforge(
        cls,
        target,
        token,
        build,
        archive,
        filename,
        version_number,
        release_type,
        changelog,
        http,
    ):
        metadata = {
            "changelog": changelog or "Published from solder.py",
            "changelogType": "markdown",
            "displayName": f"{target.modpack_name} {version_number}",
            "gameVersionNames": [str(build.minecraft)],
            "releaseType": release_type,
            "isMarkedForManualRelease": False,
        }
        response = http.post(
            "https://minecraft.curseforge.com/api/projects/"
            f"{target.project_id}/upload-file",
            headers={"X-Api-Token": token, "User-Agent": USER_AGENT},
            files={
                "metadata": (
                    None,
                    json.dumps(metadata, separators=(",", ":")),
                    "application/json",
                ),
                "file": (filename, archive, "application/zip"),
            },
            allow_redirects=False,
            timeout=_REQUEST_TIMEOUT,
        )
        try:
            if response.status_code not in {200, 201}:
                # CurseForge API response values must remain request-scoped and
                # must not be copied into the persisted publication error log.
                raise PublishingError("CurseForge rejected the upload.")
            # CurseForge forbids saving or caching API response data. A
            # successful upload needs no remote identifier in solder.py.
            return None
        finally:
            response.close()

    @classmethod
    def publish(
        cls,
        target_id,
        modpack_id,
        build,
        archive,
        filename,
        release_type,
        changelog,
        user_id,
        *,
        expected_version=None,
        http=None,
    ):
        release_type = str(release_type or "release").strip().lower()
        if release_type not in RELEASE_TYPES:
            raise PublishingConfigurationError("Select a valid release type.")
        changelog = str(changelog or "").strip()
        if len(changelog) > 100000:
            raise PublishingConfigurationError(
                "The changelog cannot exceed 100,000 characters."
            )
        filename = str(filename or "").strip()
        if not filename or len(filename) > 255 or "/" in filename or "\\" in filename:
            raise PublishingConfigurationError("The archive filename is invalid.")
        archive.seek(0)
        hasher = hashlib.sha256()
        archive_size = 0
        while True:
            chunk = archive.read(1024 * 1024)
            if not chunk:
                break
            archive_size += len(chunk)
            hasher.update(chunk)
        if archive_size == 0:
            raise PublishingError("The generated publication archive is empty.")
        digest = hasher.hexdigest()
        if _SHA256_RE.fullmatch(digest) is None:
            raise PublishingError("The generated publication archive is invalid.")
        target, token = cls._target_for_publish(
            target_id, modpack_id, user_id
        )
        claim = cls._begin_run(
            target.id,
            build.id,
            digest,
            release_type,
            target.modpack_name,
            build.version,
            int(user_id),
            expected_version=expected_version,
        )
        if claim.patch_number > 0:
            stem, separator, extension = filename.rpartition(".")
            if not separator:
                stem, extension = filename, ""
            patch_suffix = f"-Patch-{claim.patch_number}"
            if patch_suffix not in stem:
                stem += patch_suffix
            filename = f"{stem}.{extension}" if extension else stem
        http = http or requests
        archive.seek(0)
        try:
            if target.provider == MODRINTH:
                remote_id = cls._publish_modrinth(
                    target,
                    token,
                    build,
                    archive,
                    filename,
                    claim.version_number,
                    release_type,
                    changelog,
                    http,
                )
            elif target.provider == CURSEFORGE:
                remote_id = cls._publish_curseforge(
                    target,
                    token,
                    build,
                    archive,
                    filename,
                    claim.version_number,
                    release_type,
                    changelog,
                    http,
                )
            else:
                raise PublishingConfigurationError(
                    "The publishing provider is not supported."
                )
        except PublishingOutcomeUnknownError as error:
            cls._finish_run(claim.run_id, "UNKNOWN", error_message=str(error))
            raise
        except PublishingError as error:
            cls._finish_run(claim.run_id, "FAILED", error_message=str(error))
            raise
        except requests.RequestException as error:
            message = (
                f"{target.provider_label} upload result is unknown because "
                "the request did not complete. Check the provider before "
                "allowing a retry."
            )
            cls._finish_run(claim.run_id, "UNKNOWN", error_message=message)
            raise PublishingOutcomeUnknownError(message) from error
        except Exception:
            cls._finish_run(
                claim.run_id,
                "UNKNOWN",
                error_message="Unexpected publishing result; check the provider.",
            )
            raise

        stored_remote_id = remote_id if target.provider == MODRINTH else None
        cls._finish_run(
            claim.run_id, "SUCCEEDED", remote_file_id=stored_remote_id
        )
        return PublicationResult(
            claim.run_id,
            target.provider,
            stored_remote_id,
            digest,
            claim.version_number,
            claim.patch_number,
        )
