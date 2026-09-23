"""Universal Maven repository catalog used by the management interface."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
import re
import unicodedata
from urllib.parse import quote, urljoin, urlparse

from defusedxml import ElementTree
import requests

from .compatibility import normalize_modloader
from .database import Database


MAVEN = "MAVEN"
MAVEN_VERSION_MODES = ("EMBEDDED", "FIXED", "MANUAL")
DEFAULT_VERSION_PATTERN = "{minecraft}-{version}"
REQUEST_TIMEOUT = (5, 30)
MAX_METADATA_SIZE = 2 * 1024 * 1024
MAX_MAVEN_VERSIONS = 10000
USER_AGENT = "solder.py/1.10.2 (+https://github.com/Thorfusion/solder.py)"
logger = logging.getLogger(__name__)

_GROUP_PATTERN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,190}")
_COORDINATE_PATTERN = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,190}")
_CLASSIFIER_PATTERN = re.compile(r"[A-Za-z0-9_.-]{0,128}")
_EXTENSION_PATTERN = re.compile(r"[A-Za-z0-9]{1,16}")
_VERSION_PATTERN = re.compile(r"[^/\\\x00-\x1f]{1,255}")
_CHECKSUM_LENGTHS = {"sha512": 128, "sha256": 64, "sha1": 40, "md5": 32}


class MavenError(ValueError):
    """Raised when Maven configuration or metadata is invalid."""


def normalize_base_url(value):
    value = str(value or "").strip()
    try:
        parsed = urlparse(value)
        parsed_port = parsed.port
    except ValueError as error:
        raise MavenError("Enter a valid HTTP or HTTPS Maven repository URL.") from error
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise MavenError("Enter a valid HTTP or HTTPS Maven repository URL.")
    if parsed_port is not None and not 1 <= parsed_port <= 65535:
        raise MavenError("Enter a valid HTTP or HTTPS Maven repository URL.")
    path = parsed.path.rstrip("/") + "/"
    return parsed._replace(path=path, params="", query="", fragment="").geturl()


def validate_coordinates(group_id, artifact_id, classifier="", extension="jar"):
    group_id = str(group_id or "").strip()
    artifact_id = str(artifact_id or "").strip()
    classifier = str(classifier or "").strip()
    extension = str(extension or "jar").strip().lower()
    if not _GROUP_PATTERN.fullmatch(group_id):
        raise MavenError("The Maven group ID is invalid.")
    if not _COORDINATE_PATTERN.fullmatch(artifact_id):
        raise MavenError("The Maven artifact ID is invalid.")
    if not _CLASSIFIER_PATTERN.fullmatch(classifier):
        raise MavenError("The Maven classifier is invalid.")
    if not _EXTENSION_PATTERN.fullmatch(extension):
        raise MavenError("The Maven extension is invalid.")
    return group_id, artifact_id, classifier, extension


def validate_version_rule(mode, pattern=None, fixed_minecraft=None):
    mode = str(mode or "").strip().upper()
    pattern = str(pattern or "").strip()
    fixed_minecraft = str(fixed_minecraft or "").strip()
    if mode not in MAVEN_VERSION_MODES:
        raise MavenError("Select a valid Minecraft version mapping mode.")
    if mode == "EMBEDDED":
        if (
            pattern.count("{minecraft}") != 1
            or pattern.count("{version}") != 1
            or len(pattern) > 255
        ):
            raise MavenError(
                "The embedded format must contain {minecraft} and {version} once."
            )
        remainder = pattern.replace("{minecraft}", "").replace("{version}", "")
        if "{" in remainder or "}" in remainder:
            raise MavenError("The embedded format contains an unknown placeholder.")
    if mode == "FIXED":
        if not fixed_minecraft or len(fixed_minecraft) > 255:
            raise MavenError("Select the fixed Minecraft version for this artifact.")
        pattern = "{version}"
    return mode, pattern or DEFAULT_VERSION_PATTERN, fixed_minecraft or None


def split_maven_version(upstream_version, mode, pattern, fixed_minecraft):
    """Return an explicit ``(minecraft, mod version)`` mapping or nulls."""
    upstream_version = str(upstream_version or "").strip()
    if not _VERSION_PATTERN.fullmatch(upstream_version):
        return None, None
    mode, pattern, fixed_minecraft = validate_version_rule(
        mode, pattern, fixed_minecraft
    )
    if mode == "FIXED":
        return fixed_minecraft, upstream_version
    if mode == "MANUAL":
        return None, None

    pieces = re.split(r"(\{minecraft\}|\{version\})", pattern)
    expression = []
    for piece in pieces:
        if piece == "{minecraft}":
            expression.append(r"(?P<minecraft>.+?)")
        elif piece == "{version}":
            expression.append(r"(?P<version>.+?)")
        else:
            expression.append(re.escape(piece))
    match = re.fullmatch("".join(expression), upstream_version)
    if not match:
        return None, None
    minecraft = match.group("minecraft").strip()
    version = match.group("version").strip()
    if not minecraft or not version:
        return None, None
    return minecraft, version


def maven_version_id(artifact_id, upstream_version):
    value = f"{int(artifact_id)}\0{upstream_version}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _slug_part(value):
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = value.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()


def maven_mod_slug(repository_name, mod_name):
    """Build the stable mod-and-repository slug used by Maven mods."""
    repository = _slug_part(repository_name)
    mod = _slug_part(mod_name)
    if not repository or not mod:
        raise MavenError("Repository and mod names must produce a usable slug.")
    value = f"{mod}-{repository}"
    if len(value) <= 255:
        return value
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"{value[:242].rstrip('-')}-{digest}"


def _path_part(value):
    return quote(str(value), safe="._-")


def artifact_directory_url(base_url, group_id, artifact_id):
    group_path = "/".join(_path_part(part) for part in group_id.split("."))
    return urljoin(base_url, f"{group_path}/{_path_part(artifact_id)}/")


def artifact_file_url(artifact, upstream_version, filename_version=None):
    directory = artifact_directory_url(
        artifact.repository_url, artifact.group_id, artifact.artifact_id
    )
    encoded_version = _path_part(upstream_version)
    encoded_filename_version = _path_part(filename_version or upstream_version)
    filename = f"{_path_part(artifact.artifact_id)}-{encoded_filename_version}"
    if artifact.classifier:
        filename += f"-{_path_part(artifact.classifier)}"
    filename += f".{artifact.extension}"
    return urljoin(directory, f"{encoded_version}/{filename}")


@dataclass(frozen=True)
class MavenRepository:
    id: int
    name: str
    base_url: str
    created_at: object = None
    updated_at: object = None

    @classmethod
    def from_row(cls, row):
        return cls(
            row["id"], row["name"], row["base_url"],
            row.get("created_at"), row.get("updated_at"),
        )

    @classmethod
    def new(cls, name, base_url):
        name = str(name or "").strip()
        if not name or len(name) > 255:
            raise MavenError("Repository name must contain 1 to 255 characters.")
        base_url = normalize_base_url(base_url)
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                "INSERT INTO maven_repositories (name, base_url) VALUES (%s, %s)",
                (name, base_url),
            )
            repository_id = cur.lastrowid
            conn.commit()
            return cls.get(repository_id)
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @classmethod
    def get(cls, repository_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                "SELECT * FROM maven_repositories WHERE id = %s",
                (repository_id,),
            )
            row = cur.fetchone()
            return cls.from_row(row) if row else None
        finally:
            cur.close()
            conn.close()

    @classmethod
    def get_all(cls):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT * FROM maven_repositories ORDER BY name, id")
            return [cls.from_row(row) for row in (cur.fetchall() or [])]
        finally:
            cur.close()
            conn.close()

    @classmethod
    def get_by_name(cls, name):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                "SELECT * FROM maven_repositories WHERE name = %s", (name,)
            )
            row = cur.fetchone()
            return cls.from_row(row) if row else None
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def delete(repository_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                "SELECT COUNT(*) AS count FROM maven_artifacts WHERE repository_id = %s",
                (repository_id,),
            )
            if cur.fetchone()["count"]:
                raise MavenError(
                    "A Maven repository in use by an artifact cannot be deleted."
                )
            cur.execute("DELETE FROM maven_repositories WHERE id = %s", (repository_id,))
            if cur.rowcount != 1:
                raise MavenError("The Maven repository no longer exists.")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()


@dataclass(frozen=True)
class MavenArtifact:
    id: int
    repository_id: int
    repository_name: str
    repository_url: str
    group_id: str
    artifact_id: str
    classifier: str
    extension: str
    version_mode: str
    version_pattern: str
    fixed_minecraft: str | None
    modloader: str | None
    slug: str
    title: str
    description: str
    author: str
    link: str
    side: str
    mod_id: int | None = None
    created_at: object = None
    updated_at: object = None
    solderpy_loader_direct: bool = False

    @classmethod
    def from_row(cls, row):
        return cls(
            row["id"], row["repository_id"], row["repository_name"],
            row["repository_url"], row["group_id"], row["artifact_id"],
            row.get("classifier") or "", row.get("extension") or "jar",
            row["version_mode"], row.get("version_pattern") or DEFAULT_VERSION_PATTERN,
            row.get("fixed_minecraft"), normalize_modloader(row.get("modloader")),
            row["slug"], row["title"], row.get("description") or "",
            row.get("author") or "", row.get("link") or "", row.get("side") or "BOTH",
            row.get("mod_id"), row.get("created_at"), row.get("updated_at"),
            bool(row.get("solderpy_loader_direct")),
        )

    @staticmethod
    def _select_sql():
        return """SELECT maven_artifacts.*,
                         maven_repositories.name AS repository_name,
                         maven_repositories.base_url AS repository_url
                  FROM maven_artifacts
                  INNER JOIN maven_repositories
                      ON maven_artifacts.repository_id = maven_repositories.id"""

    @classmethod
    def new(
        cls, repository_id, group_id, artifact_id, classifier, extension,
        version_mode, version_pattern, fixed_minecraft, modloader,
        slug, title, description, author, link, side,
    ):
        group_id, artifact_id, classifier, extension = validate_coordinates(
            group_id, artifact_id, classifier, extension
        )
        if extension != "jar":
            raise MavenError("Maven mod artifacts must use the JAR extension.")
        version_mode, version_pattern, fixed_minecraft = validate_version_rule(
            version_mode, version_pattern, fixed_minecraft
        )
        modloader = normalize_modloader(modloader)
        title = str(title or "").strip()
        description = str(description or "").strip()
        author = str(author or "").strip()
        link = str(link or "").strip()
        side = str(side or "BOTH").strip().upper()
        if not title or len(title) > 255:
            raise MavenError("Mod name must contain 1 to 255 characters.")
        if len(description) > 255 or len(author) > 255 or len(link) > 255:
            raise MavenError("Maven mod metadata is too long.")
        if side not in {"CLIENT", "SERVER", "BOTH"}:
            raise MavenError("Select a valid mod side.")
        repository = MavenRepository.get(repository_id)
        if repository is None:
            raise MavenError("The selected Maven repository does not exist.")
        slug = maven_mod_slug(repository.name, title)

        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """INSERT INTO maven_artifacts
                          (repository_id, group_id, artifact_id, classifier,
                           extension, version_mode, version_pattern,
                           fixed_minecraft, modloader, slug, title, description,
                           author, link, side)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                           %s, %s, %s, %s)""",
                (
                    repository_id, group_id, artifact_id, classifier, extension,
                    version_mode, version_pattern, fixed_minecraft, modloader,
                    slug, title, description, author, link, side,
                ),
            )
            artifact_db_id = cur.lastrowid
            conn.commit()
            return cls.get(artifact_db_id)
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @classmethod
    def get(cls, artifact_db_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(cls._select_sql() + " WHERE maven_artifacts.id = %s", (artifact_db_id,))
            row = cur.fetchone()
            return cls.from_row(row) if row else None
        finally:
            cur.close()
            conn.close()

    @classmethod
    def get_all(cls):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(cls._select_sql() + " ORDER BY maven_artifacts.title, maven_artifacts.id")
            return [cls.from_row(row) for row in (cur.fetchall() or [])]
        finally:
            cur.close()
            conn.close()

    @classmethod
    def get_by_mod_id(cls, mod_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                cls._select_sql() + " WHERE maven_artifacts.mod_id = %s",
                (mod_id,),
            )
            row = cur.fetchone()
            return cls.from_row(row) if row else None
        finally:
            cur.close()
            conn.close()

    @classmethod
    def get_by_coordinates(
        cls, repository_id, group_id, artifact_id, classifier="", extension="jar"
    ):
        group_id, artifact_id, classifier, extension = validate_coordinates(
            group_id, artifact_id, classifier, extension
        )
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                cls._select_sql()
                + """ WHERE maven_artifacts.repository_id = %s
                           AND maven_artifacts.group_id = %s
                           AND maven_artifacts.artifact_id = %s
                           AND maven_artifacts.classifier = %s
                           AND maven_artifacts.extension = %s""",
                (
                    repository_id, group_id, artifact_id, classifier, extension,
                ),
            )
            row = cur.fetchone()
            return cls.from_row(row) if row else None
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def attach_mod(artifact_db_id, mod_id):
        conn = Database.get_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                "UPDATE maven_artifacts SET mod_id = %s WHERE id = %s",
                (mod_id, artifact_db_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def delete_unlinked(artifact_db_id):
        conn = Database.get_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                """DELETE FROM maven_versions
                   WHERE maven_artifact_id = %s
                     AND EXISTS (
                         SELECT 1 FROM maven_artifacts
                         WHERE id = %s AND mod_id IS NULL
                     )""",
                (artifact_db_id, artifact_db_id),
            )
            cur.execute(
                "DELETE FROM maven_artifacts WHERE id = %s AND mod_id IS NULL",
                (artifact_db_id,),
            )
            conn.commit()
        finally:
            cur.close()
            conn.close()

    @classmethod
    def update_rule(
        cls, artifact_db_id, version_mode, version_pattern,
        fixed_minecraft, modloader,
    ):
        version_mode, version_pattern, fixed_minecraft = validate_version_rule(
            version_mode, version_pattern, fixed_minecraft
        )
        modloader = normalize_modloader(modloader)
        conn = Database.get_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                """UPDATE maven_artifacts
                   SET version_mode = %s, version_pattern = %s,
                       fixed_minecraft = %s, modloader = %s
                   WHERE id = %s""",
                (
                    version_mode, version_pattern, fixed_minecraft,
                    modloader, artifact_db_id,
                ),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()
        artifact = cls.get(artifact_db_id)
        if artifact is None:
            raise MavenError("The Maven artifact no longer exists.")
        MavenVersion.reapply_rule(artifact)
        return artifact

    @classmethod
    def update_solderpy_loader_direct(cls, artifact_db_id, enabled):
        artifact = cls.get(artifact_db_id)
        if artifact is None:
            raise MavenError("The Maven artifact no longer exists.")
        if enabled and urlparse(artifact.repository_url).scheme != "https":
            raise MavenError(
                "SolderPy Modpack Loader direct downloads require an HTTPS Maven repository."
            )
        conn = Database.get_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                """UPDATE maven_artifacts SET solderpy_loader_direct = %s
                   WHERE id = %s""",
                (int(bool(enabled)), artifact_db_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()
        return cls.get(artifact_db_id)

    @classmethod
    def solderpy_loader_downloads(cls, references, *, http=None):
        """Resolve enabled Maven sources with one catalog query per manifest."""
        requested = {
            (str(project_id), str(version_id))
            for project_id, version_id in references
            if str(project_id).isdigit() and str(version_id)
        }
        if not requested:
            return {}
        artifact_ids = sorted({int(project_id) for project_id, _ in requested})
        version_ids = sorted({version_id for _, version_id in requested})
        artifact_placeholders = ", ".join(["%s"] * len(artifact_ids))
        version_placeholders = ", ".join(["%s"] * len(version_ids))
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                f"""SELECT maven_artifacts.*,
                            maven_repositories.name AS repository_name,
                            maven_repositories.base_url AS repository_url,
                            maven_versions.maven_artifact_id
                                AS mapping_artifact_id,
                            maven_versions.integration_version_id,
                            maven_versions.upstream_version
                     FROM maven_artifacts
                     INNER JOIN maven_repositories
                         ON maven_artifacts.repository_id =
                            maven_repositories.id
                     INNER JOIN maven_versions
                         ON maven_versions.maven_artifact_id =
                            maven_artifacts.id
                     WHERE maven_artifacts.solderpy_loader_direct = 1
                       AND maven_artifacts.id IN ({artifact_placeholders})
                       AND maven_versions.integration_version_id
                           IN ({version_placeholders})""",  # nosec B608
                (*artifact_ids, *version_ids),
            )
            rows = cur.fetchall() or []
        finally:
            cur.close()
            conn.close()

        resolved = {}
        for row in rows:
            key = (
                str(row["mapping_artifact_id"]),
                str(row["integration_version_id"]),
            )
            if key not in requested:
                continue
            artifact = cls.from_row(row)
            repository = MavenRepository(
                artifact.repository_id,
                artifact.repository_name,
                artifact.repository_url,
            )
            upstream_version = str(row["upstream_version"])
            try:
                filename_version = MavenMetadataClient(
                    repository, http=http
                ).snapshot_value(artifact, upstream_version)
            except MavenError:
                logger.warning(
                    "Could not resolve Maven bootstrap source for artifact %s.",
                    artifact.id,
                    exc_info=True,
                )
                continue
            resolved[key] = artifact_file_url(
                artifact, upstream_version, filename_version
            )
        return resolved

    @property
    def coordinates(self):
        value = f"{self.group_id}:{self.artifact_id}"
        if self.classifier:
            value += f":{self.classifier}"
        return value

    @property
    def project_url(self):
        return self.link or artifact_directory_url(
            self.repository_url, self.group_id, self.artifact_id
        )


@dataclass(frozen=True)
class MavenVersion:
    id: int
    maven_artifact_id: int
    upstream_version: str
    integration_version_id: str
    minecraft: str | None
    mod_version: str | None
    modloader: str | None
    mapping_source: str
    enabled: bool
    available: bool
    metadata_order: int

    @classmethod
    def from_row(cls, row):
        return cls(
            row["id"], row["maven_artifact_id"], row["upstream_version"],
            row["integration_version_id"], row.get("minecraft"),
            row.get("mod_version"), normalize_modloader(row.get("modloader")),
            row.get("mapping_source") or "UNMAPPED", bool(row.get("enabled")),
            bool(row.get("available")), int(row.get("metadata_order") or 0),
        )

    @classmethod
    def get_all(cls, artifact_db_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT * FROM maven_versions
                   WHERE maven_artifact_id = %s
                   ORDER BY available DESC, metadata_order DESC, id DESC""",
                (artifact_db_id,),
            )
            return [cls.from_row(row) for row in (cur.fetchall() or [])]
        finally:
            cur.close()
            conn.close()

    @classmethod
    def get_compatible(cls, artifact_db_id, minecraft, modloader=None):
        modloader = normalize_modloader(modloader)
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT * FROM maven_versions
                   WHERE maven_artifact_id = %s
                     AND minecraft = %s
                     AND enabled = 1 AND available = 1
                     AND (%s IS NULL OR modloader = %s OR modloader IS NULL)
                   ORDER BY metadata_order DESC, id DESC""",
                (artifact_db_id, str(minecraft), modloader, modloader),
            )
            return [cls.from_row(row) for row in (cur.fetchall() or [])]
        finally:
            cur.close()
            conn.close()

    @classmethod
    def get_by_integration_id(cls, artifact_db_id, integration_version_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT * FROM maven_versions
                   WHERE maven_artifact_id = %s
                     AND integration_version_id = %s""",
                (artifact_db_id, str(integration_version_id)),
            )
            row = cur.fetchone()
            return cls.from_row(row) if row else None
        finally:
            cur.close()
            conn.close()

    @classmethod
    def get_by_id(cls, mapping_id, artifact_db_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT * FROM maven_versions
                   WHERE id = %s AND maven_artifact_id = %s""",
                (mapping_id, artifact_db_id),
            )
            row = cur.fetchone()
            return cls.from_row(row) if row else None
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def sync(artifact, upstream_versions):
        conn = Database.get_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                "UPDATE maven_versions SET available = 0 WHERE maven_artifact_id = %s",
                (artifact.id,),
            )
            rows = []
            for order, upstream_version in enumerate(upstream_versions):
                upstream_version = str(upstream_version or "").strip()
                if not _VERSION_PATTERN.fullmatch(upstream_version):
                    continue
                minecraft, mod_version = split_maven_version(
                    upstream_version, artifact.version_mode,
                    artifact.version_pattern, artifact.fixed_minecraft,
                )
                enabled = int(bool(minecraft and mod_version))
                source = "RULE" if enabled else "UNMAPPED"
                integration_id = maven_version_id(artifact.id, upstream_version)
                rows.append(
                    (
                        artifact.id, upstream_version, integration_id,
                        minecraft, mod_version, artifact.modloader, source,
                        enabled, order,
                    )
                )
            if rows:
                cur.executemany(
                    """INSERT INTO maven_versions
                              (maven_artifact_id, upstream_version,
                               integration_version_id, minecraft, mod_version,
                               modloader, mapping_source, enabled, available,
                               metadata_order)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 1, %s)
                       ON DUPLICATE KEY UPDATE
                           metadata_order = VALUES(metadata_order),
                           available = 1,
                           minecraft = IF(mapping_source = 'MANUAL', minecraft,
                                          VALUES(minecraft)),
                           mod_version = IF(mapping_source = 'MANUAL', mod_version,
                                           VALUES(mod_version)),
                           modloader = IF(mapping_source = 'MANUAL', modloader,
                                         VALUES(modloader)),
                           enabled = IF(mapping_source = 'MANUAL', enabled,
                                        VALUES(enabled)),
                           mapping_source = IF(mapping_source = 'MANUAL',
                                               mapping_source,
                                               VALUES(mapping_source))""",
                    rows,
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def reapply_rule(artifact):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT id, upstream_version FROM maven_versions
                   WHERE maven_artifact_id = %s AND mapping_source <> 'MANUAL'""",
                (artifact.id,),
            )
            for row in cur.fetchall() or []:
                minecraft, mod_version = split_maven_version(
                    row["upstream_version"], artifact.version_mode,
                    artifact.version_pattern, artifact.fixed_minecraft,
                )
                source = "RULE" if minecraft and mod_version else "UNMAPPED"
                cur.execute(
                    """UPDATE maven_versions
                       SET minecraft = %s, mod_version = %s, modloader = %s,
                           mapping_source = %s, enabled = %s
                       WHERE id = %s""",
                    (
                        minecraft, mod_version, artifact.modloader, source,
                        int(source == "RULE"), row["id"],
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
    def update_manual(mapping_id, artifact_db_id, minecraft, mod_version, modloader, enabled):
        minecraft = str(minecraft or "").strip()
        mod_version = str(mod_version or "").strip()
        modloader = normalize_modloader(modloader)
        enabled = bool(enabled)
        if enabled and (not minecraft or not mod_version):
            raise MavenError("Enabled versions need Minecraft and mod version values.")
        if len(minecraft) > 255 or len(mod_version) > 255:
            raise MavenError("The mapped version is too long.")
        conn = Database.get_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                """UPDATE maven_versions
                   SET minecraft = %s, mod_version = %s, modloader = %s,
                       mapping_source = 'MANUAL', enabled = %s
                   WHERE id = %s AND maven_artifact_id = %s""",
                (
                    minecraft or None, mod_version or None, modloader,
                    int(enabled), mapping_id, artifact_db_id,
                ),
            )
            if cur.rowcount != 1:
                raise MavenError("The selected Maven version no longer exists.")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()


class MavenMetadataClient:
    """Read standard Maven metadata and checksums from one configured origin."""

    def __init__(self, repository, http=None):
        self.repository = repository
        self.http = http or requests.Session()
        self._origin = self._url_origin(repository.base_url)

    @staticmethod
    def _url_origin(url):
        parsed = urlparse(url)
        return parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.port

    def _validate_url(self, url):
        if self._url_origin(url) != self._origin:
            raise MavenError("The Maven repository redirected to another host.")

    def _get_bytes(self, url, maximum, *, missing_ok=False):
        for _ in range(6):
            self._validate_url(url)
            response = None
            try:
                # Repository URLs are supplied by environment administrators;
                # coordinates are encoded and every redirect is revalidated
                # against this configured origin before another request.
                response = self.http.get(  # lgtm[py/partial-ssrf]
                    url,
                    headers={"User-Agent": USER_AGENT},
                    stream=True,
                    allow_redirects=False,
                    timeout=REQUEST_TIMEOUT,
                )
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("Location")
                    if not location:
                        raise MavenError("The Maven repository returned an invalid redirect.")
                    url = urljoin(url, location)
                    continue
                if missing_ok and response.status_code == 404:
                    return None
                response.raise_for_status()
                length = response.headers.get("Content-Length")
                if length and int(length) > maximum:
                    raise MavenError("The Maven metadata response is too large.")
                chunks = []
                downloaded = 0
                for chunk in response.iter_content(64 * 1024):
                    if not chunk:
                        continue
                    downloaded += len(chunk)
                    if downloaded > maximum:
                        raise MavenError("The Maven metadata response is too large.")
                    chunks.append(chunk)
                return b"".join(chunks)
            except MavenError:
                raise
            except (requests.RequestException, OSError, ValueError) as error:
                raise MavenError("The Maven repository could not be read.") from error
            finally:
                if response is not None:
                    response.close()
        raise MavenError("The Maven repository returned too many redirects.")

    def versions(self, group_id, artifact_id):
        group_id, artifact_id, _classifier, _extension = validate_coordinates(
            group_id, artifact_id
        )
        url = urljoin(
            artifact_directory_url(self.repository.base_url, group_id, artifact_id),
            "maven-metadata.xml",
        )
        content = self._get_bytes(url, MAX_METADATA_SIZE)
        if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
            raise MavenError("The Maven repository returned unsafe XML metadata.")
        try:
            root = ElementTree.fromstring(content)
        except ElementTree.ParseError as error:
            raise MavenError("The Maven repository returned invalid metadata XML.") from error
        versions = []
        seen = set()
        for element in root.findall("./versioning/versions/version"):
            value = str(element.text or "").strip()
            if _VERSION_PATTERN.fullmatch(value) and value not in seen:
                seen.add(value)
                versions.append(value)
                if len(versions) > MAX_MAVEN_VERSIONS:
                    raise MavenError(
                        "The Maven metadata contains more than 10,000 versions."
                    )
        if not versions:
            raise MavenError("No published versions were found in Maven metadata.")
        return versions

    def checksums(self, artifact_url):
        checksums = {}
        for algorithm in ("sha512", "sha256", "sha1", "md5"):
            content = self._get_bytes(
                f"{artifact_url}.{algorithm}", 1024, missing_ok=True
            )
            if content is None:
                continue
            values = content.decode("ascii", "ignore").strip().split()
            if not values:
                continue
            value = values[0].lower()
            if (
                len(value) == _CHECKSUM_LENGTHS[algorithm]
                and re.fullmatch(r"[0-9a-f]+", value)
            ):
                checksums[algorithm] = value
                break
        return checksums

    def snapshot_value(self, artifact, upstream_version):
        """Resolve the standard timestamped filename for a SNAPSHOT version."""
        if not str(upstream_version).upper().endswith("-SNAPSHOT"):
            return upstream_version
        directory = artifact_directory_url(
            self.repository.base_url, artifact.group_id, artifact.artifact_id
        )
        url = urljoin(directory, f"{_path_part(upstream_version)}/maven-metadata.xml")
        content = self._get_bytes(url, MAX_METADATA_SIZE)
        if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
            raise MavenError("The Maven repository returned unsafe XML metadata.")
        try:
            root = ElementTree.fromstring(content)
        except ElementTree.ParseError as error:
            raise MavenError("The Maven repository returned invalid snapshot metadata.") from error
        matches = []
        for item in root.findall("./versioning/snapshotVersions/snapshotVersion"):
            extension = str(item.findtext("extension") or "").strip()
            classifier = str(item.findtext("classifier") or "").strip()
            value = str(item.findtext("value") or "").strip()
            updated = str(item.findtext("updated") or "").strip()
            if (
                extension == artifact.extension
                and classifier == artifact.classifier
                and _VERSION_PATTERN.fullmatch(value)
            ):
                matches.append((updated, value))
        if not matches:
            timestamp = str(
                root.findtext("./versioning/snapshot/timestamp") or ""
            ).strip()
            build_number = str(
                root.findtext("./versioning/snapshot/buildNumber") or ""
            ).strip()
            if (
                re.fullmatch(r"\d{8}\.\d{6}", timestamp)
                and re.fullmatch(r"\d+", build_number)
            ):
                return (
                    f"{upstream_version[:-len('-SNAPSHOT')]}-"
                    f"{timestamp}-{build_number}"
                )
            raise MavenError(
                "The Maven snapshot metadata has no matching artifact file."
            )
        matches.sort(reverse=True)
        return matches[0][1]


class MavenCatalog:
    @staticmethod
    def refresh(artifact, *, http=None):
        repository = MavenRepository(
            artifact.repository_id,
            artifact.repository_name,
            artifact.repository_url,
        )
        versions = MavenMetadataClient(repository, http=http).versions(
            artifact.group_id, artifact.artifact_id
        )
        MavenVersion.sync(artifact, versions)
        return MavenVersion.get_all(artifact.id)
