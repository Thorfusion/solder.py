import datetime
import hashlib
import hmac
from pathlib import Path, PurePosixPath
import re

from mysql.connector import IntegrityError, errorcode
from werkzeug.utils import secure_filename

from .database import Database
from .modversion import Modversion
import zipfile


class DuplicateModError(ValueError):
    """Raised when a mod slug is already present in the database."""


class UploadVerificationError(ValueError):
    """Raised when an uploaded Solder package fails server verification."""


_MAX_UPLOAD_JAR_SIZE = 512 * 1024 * 1024
MOD_TYPES = frozenset({"MOD", "LAUNCHER", "RES", "CONFIG", "BOOTSTRAP", "NONE"})


def normalize_modtype(value):
    """Normalize the former product-specific MCIL role to BOOTSTRAP."""
    value = str(value or "MOD").strip().upper()
    if value == "MCIL":
        value = "BOOTSTRAP"
    if value not in MOD_TYPES:
        raise ValueError("Unknown mod type.")
    return value


class Mod:
    def __init__(
        self,
        id,
        name,
        description,
        author,
        link,
        created_at,
        updated_at,
        pretty_name,
        side,
        modtype,
        notes,
        integration_provider=None,
        integration_project_id=None,
        integration_label=None,
    ):
        self.id = id
        self.name = name
        self.description = description
        self.author = author
        self.link = link
        self.created_at = created_at
        self.updated_at = updated_at
        self.pretty_name = pretty_name
        self.side = side
        self.modtype = normalize_modtype(modtype)
        self.notes = notes
        self.integration_provider = (
            str(integration_provider).upper() if integration_provider else None
        )
        self.integration_project_id = (
            str(integration_project_id) if integration_project_id else None
        )
        self.integration_label = integration_label or (
            self.integration_provider.title()
            if self.integration_provider else None
        )

    @classmethod
    def new(
        cls,
        name,
        description,
        author,
        link,
        pretty_name,
        side,
        modtype,
        notes,
        integration_provider=None,
        integration_project_id=None,
    ):
        modtype = normalize_modtype(modtype)
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        now = datetime.datetime.now()
        try:
            cur.execute(
                """INSERT INTO mods
                          (name, description, author, link, created_at,
                           updated_at, pretty_name, side, modtype, notes,
                           integration_provider, integration_project_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    name,
                    description,
                    author,
                    link,
                    now,
                    now,
                    pretty_name,
                    side,
                    modtype,
                    notes,
                    integration_provider,
                    integration_project_id,
                ),
            )
            conn.commit()
            return cls(
                cur.lastrowid,
                name,
                description,
                author,
                link,
                now,
                now,
                pretty_name,
                side,
                modtype,
                notes,
                integration_provider,
                integration_project_id,
            )
        except IntegrityError as error:
            conn.rollback()
            if error.errno == errorcode.ER_DUP_ENTRY:
                raise DuplicateModError(name) from error
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def update(id, name, description, author, link, pretty_name, side, modtype, notes):
        modtype = normalize_modtype(modtype)
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        now = datetime.datetime.now()
        cur.execute("""UPDATE mods 
            SET name = %s, description = %s, author = %s, link = %s, updated_at = %s, pretty_name = %s, side = %s, modtype = %s, notes = %s
            WHERE id = %s;""", (name, description, author, link, now, pretty_name, side, modtype, notes, id))
        conn.commit()
        cur.execute("SELECT LAST_INSERT_ID() AS id")
        id = cur.fetchone()["id"]
        return None

    @staticmethod
    def delete_mod(id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM modversions WHERE mod_id = %s", (id,))
        modversions = cur.fetchall()
        if modversions:
            from .advanced_optional import AdvancedOptional

            AdvancedOptional.delete_modversion_memberships(
                cur, [mv["id"] for mv in modversions]
            )
            for mv in modversions:
                cur.execute("DELETE FROM build_modversion WHERE modversion_id = %s", (mv["id"],))
        cur.execute(
            "DELETE FROM mod_dependencies WHERE mod_id = %s OR dependency_mod_id = %s",
            (id, id),
        )
        cur.execute(
            """DELETE maven_versions FROM maven_versions
               INNER JOIN maven_artifacts
                   ON maven_versions.maven_artifact_id = maven_artifacts.id
               WHERE maven_artifacts.mod_id = %s""",
            (id,),
        )
        cur.execute("DELETE FROM maven_artifacts WHERE mod_id = %s", (id,))
        cur.execute(
            """DELETE modversion_download_overrides
               FROM modversion_download_overrides
               INNER JOIN modversions
                   ON modversions.id =
                      modversion_download_overrides.modversion_id
               WHERE modversions.mod_id = %s""",
            (id,),
        )
        cur.execute(
            """DELETE modversion_download_sources
               FROM modversion_download_sources
               INNER JOIN modversions
                   ON modversions.id =
                      modversion_download_sources.modversion_id
               WHERE modversions.mod_id = %s""",
            (id,),
        )
        cur.execute(
            """DELETE modversion_minecraft_versions
               FROM modversion_minecraft_versions
               INNER JOIN modversions
                   ON modversions.id =
                      modversion_minecraft_versions.modversion_id
               WHERE modversions.mod_id = %s""",
            (id,),
        )
        cur.execute("DELETE FROM modversions WHERE mod_id = %s", (id,))
        cur.execute("DELETE FROM mods WHERE id=%s", (id,))
        conn.commit()
        return None

    @classmethod
    def get_by_id(cls, id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT mods.*,
                          COALESCE(maven_repositories.name,
                                   mods.integration_provider)
                              AS integration_label
                   FROM mods
                   LEFT JOIN maven_artifacts
                       ON maven_artifacts.mod_id = mods.id
                   LEFT JOIN maven_repositories
                       ON maven_artifacts.repository_id = maven_repositories.id
                   WHERE mods.id = %s""",
                (id,),
            )
            row = cur.fetchone()
            if row:
                return cls(row["id"], row["name"], row["description"], row["author"], row["link"], row["created_at"], row["updated_at"], row["pretty_name"], row["side"], row["modtype"], row.get("notes", row.get("note")), row.get("integration_provider"), row.get("integration_project_id"), row.get("integration_label"))
            return None
        finally:
            cur.close()
            conn.close()

    @classmethod
    def get_by_name_api(cls, name):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT * FROM mods WHERE name = %s", (name,))
            row = cur.fetchone()
            if row:
                return cls(row["id"], row["name"], row["description"], row["author"], row["link"], row["created_at"], row["updated_at"], row["pretty_name"], row["side"], row["modtype"], row.get("notes", row.get("note")), row.get("integration_provider"), row.get("integration_project_id"))
            return None
        finally:
            cur.close()
            conn.close()

    @classmethod
    def get_all_api(cls):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT * FROM mods ORDER BY id ASC")
            return [
                cls(
                    row["id"],
                    row["name"],
                    row["description"],
                    row["author"],
                    row["link"],
                    row["created_at"],
                    row["updated_at"],
                    row["pretty_name"],
                    row["side"],
                    row["modtype"],
                    row.get("notes", row.get("note")),
                    row.get("integration_provider"),
                    row.get("integration_project_id"),
                )
                for row in cur.fetchall()
            ]
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def get_all():
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT mods.*,
                          COALESCE(maven_repositories.name,
                                   mods.integration_provider)
                              AS integration_label
                   FROM mods
                   LEFT JOIN maven_artifacts
                       ON maven_artifacts.mod_id = mods.id
                   LEFT JOIN maven_repositories
                       ON maven_artifacts.repository_id = maven_repositories.id
                   ORDER BY mods.id DESC"""
            )
            rows = cur.fetchall()
            if rows:
                return [Mod(row["id"], row["name"], row["description"], row["author"], row["link"], row["created_at"], row["updated_at"], row["pretty_name"], row["side"], row["modtype"], row.get("notes", row.get("note")), row.get("integration_provider"), row.get("integration_project_id"), row.get("integration_label")) for row in rows]
            return []
        finally:
            cur.close()
            conn.close()

    @classmethod
    def get_by_integration(cls, provider, project_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT * FROM mods
                   WHERE integration_provider = %s
                     AND integration_project_id = %s""",
                (str(provider).upper(), str(project_id)),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return cls(
                row["id"], row["name"], row["description"], row["author"],
                row["link"], row["created_at"], row["updated_at"],
                row["pretty_name"], row["side"], row["modtype"],
                row.get("notes", row.get("note")),
                row.get("integration_provider"),
                row.get("integration_project_id"),
            )
        finally:
            cur.close()
            conn.close()

    @classmethod
    def link_integration(cls, id, provider, project_id):
        """Attach provider metadata without replacing a mod or its versions."""
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        now = datetime.datetime.now()
        try:
            cur.execute(
                """UPDATE mods
                   SET integration_provider = %s,
                       integration_project_id = %s,
                       updated_at = %s
                   WHERE id = %s
                     AND integration_provider IS NULL
                     AND integration_project_id IS NULL""",
                (str(provider).upper(), str(project_id), now, id),
            )
            if cur.rowcount != 1:
                conn.rollback()
                return None
            conn.commit()
        except IntegrityError as error:
            conn.rollback()
            if error.errno == errorcode.ER_DUP_ENTRY:
                raise DuplicateModError(project_id) from error
            raise
        finally:
            cur.close()
            conn.close()
        return cls.get_by_id(id)

    @classmethod
    def unlink_integration(cls, id, provider):
        """Detach provider metadata while retaining local versions and builds."""
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        now = datetime.datetime.now()
        try:
            cur.execute(
                """UPDATE mods
                   SET integration_provider = NULL,
                       integration_project_id = NULL,
                       updated_at = %s
                   WHERE id = %s AND integration_provider = %s""",
                (now, id, str(provider).upper()),
            )
            changed = cur.rowcount == 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()
        return cls.get_by_id(id) if changed else None

    @staticmethod
    def get_integration_project_ids(provider):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT integration_project_id FROM mods
                   WHERE integration_provider = %s""",
                (str(provider).upper(),),
            )
            return {
                str(row["integration_project_id"])
                for row in cur.fetchall()
                if row["integration_project_id"] is not None
            }
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def get_all_pretty_names():
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id, pretty_name FROM mods ORDER BY name")
        rows = cur.fetchall()
        if rows:
            return rows
        return []

    def get_versions(self):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT id, mod_id, version, mcversion, modloader,
                          integration_version_id, md5, jarmd5, jarfilesize,
                          filesize, created_at, updated_at,
                          (SELECT GROUP_CONCAT(
                                      compatibility.minecraft_version
                                      ORDER BY compatibility.minecraft_version
                                      SEPARATOR ',')
                             FROM modversion_minecraft_versions compatibility
                            WHERE compatibility.modversion_id = modversions.id)
                              AS minecraft_versions_csv
                   FROM modversions
                   WHERE mod_id = %s
                   ORDER BY id DESC""",
                (self.id,),
            )
            return [
                Modversion.hydrate_minecraft_row(row)
                for row in (cur.fetchall() or [])
            ]
        finally:
            cur.close()
            conn.close()
    
    def get_versions_api(self) -> list:
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT version FROM modversions WHERE mod_id = %s ORDER BY id ASC", (self.id,))
            return cur.fetchall()
        finally:
            cur.close()
            conn.close()

    def get_version_api(self, version):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT modversions.*,
                          (SELECT GROUP_CONCAT(
                                      compatibility.minecraft_version
                                      ORDER BY compatibility.minecraft_version
                                      SEPARATOR ',')
                             FROM modversion_minecraft_versions compatibility
                            WHERE compatibility.modversion_id = modversions.id)
                              AS minecraft_versions_csv
                   FROM modversions
                   WHERE mod_id = %s AND version = %s""",
                (self.id, version),
            )
            row = cur.fetchone()
            if row:
                return Modversion(
                    row["id"], row["mod_id"], row["version"],
                    row["mcversion"], row["md5"], row["created_at"],
                    row["updated_at"], row["filesize"],
                    modloader=row.get("modloader"),
                    integration_version_id=row.get("integration_version_id"),
                    jarmd5=row.get("jarmd5"),
                    jarfilesize=row.get("jarfilesize"),
                    minecraft_versions=row.get("minecraft_versions_csv"),
                )
            return None
        finally:
            cur.close()
            conn.close()
    
    @staticmethod
    def file_md5(path):
        """Calculate the MD5 used by Solder manifests (not a security digest)."""
        digest = hashlib.md5(usedforsecurity=False)
        with open(path, "rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def verify_file_md5(path, expected_md5, label="uploaded file"):
        expected_md5 = str(expected_md5 or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{32}", expected_md5):
            raise UploadVerificationError(f"The client supplied an invalid MD5 for {label}.")
        actual_md5 = Mod.file_md5(path)
        if not hmac.compare_digest(actual_md5, expected_md5):
            raise UploadVerificationError(f"The MD5 verification failed for {label}.")
        return actual_md5

    @staticmethod
    def extract_jar_from_zip(
        zip_paths,
        output_name=None,
        expected_md5=None,
        require_only_jar=False,
        allow_any_jar=False,
    ):
        """Extract one package JAR and optionally verify its MD5.

        ``require_only_jar`` is used by the legacy package maintenance scan. In
        that mode directory entries are ignored, but the JAR must be the only
        file payload in the ZIP. Normal uploads deliberately retain the older
        behaviour which permits configuration files beside the mod JAR.
        ``allow_any_jar`` supports LAUNCHER packages, whose executable JAR is
        normally at the archive root or under ``bin/`` rather than ``mods/``.
        """
        base_dir = Path(zip_paths).parent
        try:
            with zipfile.ZipFile(zip_paths, "r") as zip_ref:
                jar_files = []
                payload_files = []
                for info in zip_ref.infolist():
                    if not info.is_dir():
                        payload_files.append(info)
                    normalized = info.filename.replace("\\", "/")
                    path = PurePosixPath(normalized)
                    if (
                        not info.is_dir()
                        and path.suffix.lower() == ".jar"
                        and ".." not in path.parts
                        and (
                            allow_any_jar
                            or (
                                len(path.parts) >= 2
                                and path.parts[0] == "mods"
                            )
                        )
                    ):
                        jar_files.append(info)

                if len(jar_files) != 1:
                    raise UploadVerificationError(
                        "A JAR upload must contain exactly one eligible JAR."
                    )
                if require_only_jar and payload_files != jar_files:
                    raise UploadVerificationError(
                        "The package contains files other than its single mods JAR."
                    )
                if jar_files[0].file_size > _MAX_UPLOAD_JAR_SIZE:
                    raise UploadVerificationError(
                        "The raw JAR exceeds the 512 MiB upload limit."
                    )

                jar_name = output_name or PurePosixPath(jar_files[0].filename).name
                safe_jar_name = secure_filename(jar_name)
                if (
                    not safe_jar_name
                    or safe_jar_name != jar_name
                    or not safe_jar_name.lower().endswith(".jar")
                ):
                    raise UploadVerificationError("The output JAR filename is invalid.")

                output_path = (base_dir / safe_jar_name).resolve()
                try:
                    output_path.relative_to(base_dir.resolve())
                except ValueError as error:
                    raise UploadVerificationError(
                        "The output JAR path is invalid."
                    ) from error
                try:
                    with zip_ref.open(jar_files[0], "r") as source, open(
                        output_path, "wb"
                    ) as target:
                        extracted_size = 0
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            extracted_size += len(chunk)
                            if extracted_size > _MAX_UPLOAD_JAR_SIZE:
                                raise UploadVerificationError(
                                    "The raw JAR exceeds the 512 MiB upload limit."
                                )
                            target.write(chunk)
                    if expected_md5 is not None:
                        Mod.verify_file_md5(output_path, expected_md5, "the raw JAR")
                except Exception:
                    output_path.unlink(missing_ok=True)
                    raise
                return safe_jar_name
        except UploadVerificationError:
            raise
        except (OSError, RuntimeError, zipfile.BadZipFile) as error:
            raise UploadVerificationError("The uploaded package is not a valid ZIP file.") from error


    def to_json(self):
        return {
            "name": self.name,
            "pretty_name": self.pretty_name,
            "author": self.author,
            "description": self.description,
            "link": self.link,
            "side": self.side,
            "type": self.modtype,
            "modtype": self.modtype,
        }
