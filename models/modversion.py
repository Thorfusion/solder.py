from collections import deque
import datetime
import hashlib
from pathlib import Path
import re
import threading
from urllib.parse import quote, urlsplit, urlunsplit

import requests

from .compatibility import normalize_modloader, version_is_compatible
from .database import Database


class MissingDependencyVersionError(ValueError):
    def __init__(self, dependency_name, minecraft, modloader=None):
        self.dependency_name = dependency_name
        self.minecraft = minecraft
        self.modloader = modloader
        loader_text = f" and modloader {modloader}" if modloader else ""
        super().__init__(
            f'{dependency_name} has no version compatible with Minecraft '
            f'{minecraft}{loader_text}.'
        )


class IncompatibleModVersionError(ValueError):
    """Raised when a version does not match the target build."""


class Modversion:
    JAR_MD5_PATTERN = re.compile(r"^[0-9A-Fa-f]{32}$")

    def __init__(self, id, mod_id, version, mcversion, md5, created_at, updated_at, filesize, optional=0, modloader=None, integration_version_id=None, jarmd5=None):
        self.id = id
        self.mod_id = mod_id
        self.version = version
        self.mcversion = mcversion
        self.md5 = md5
        self.created_at = created_at
        self.updated_at = updated_at
        self.filesize = filesize
        self.optional = optional
        self.modloader = normalize_modloader(modloader)
        self.integration_version_id = (
            str(integration_version_id) if integration_version_id else None
        )
        self.jarmd5 = jarmd5

    @classmethod
    def new(
        cls,
        mod_id,
        version,
        mcversion,
        md5,
        filesize,
        markedbuild,
        repository_base_url="0",
        jarmd5="0",
        modloader=None,
        integration_version_id=None,
        repository_mod_slug=None,
    ):
        if md5 == "0":
            cls.repository_file_source(
                repository_base_url, repository_mod_slug, version
            )
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        now = datetime.datetime.now()
        modloader = normalize_modloader(modloader)
        try:
            cur.execute(
                """INSERT INTO modversions
                          (mod_id, version, mcversion, modloader,
                           integration_version_id, md5, jarmd5,
                           created_at, updated_at, filesize)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (mod_id, version, mcversion, modloader, integration_version_id, md5, jarmd5, now, now, filesize),
            )
            id = cur.lastrowid
            cls.promote_parent_mod_for_jar_md5(cur, mod_id, jarmd5)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()
        if markedbuild == "1":
            try:
                Modversion.add_modversion_to_selected_build(
                    id, mod_id, "0", "1", "0"
                )
            except Exception:
                # Do not leave a database row for an upload that could not be
                # attached to its requested marked build.
                Modversion.delete_modversion(id)
                raise
        if md5 == "0":
            stored_version = Modversion.get_by_id(id)
            t = threading.Thread(
                target=stored_version.rehash,
                args=(repository_base_url, repository_mod_slug),
            )
            t.start()
        return cls(
            id,
            mod_id,
            version,
            mcversion,
            md5,
            now,
            now,
            filesize,
            modloader=modloader,
            integration_version_id=integration_version_id,
            jarmd5=jarmd5,
        )

    @classmethod
    def promote_parent_mod_for_jar_md5(cls, cur, mod_id, jarmd5):
        """Keep the parent package type consistent with raw-JAR detection."""
        if not cls.JAR_MD5_PATTERN.fullmatch(str(jarmd5 or "").strip()):
            return False
        cur.execute(
            "UPDATE mods SET modtype = 'MOD' WHERE id = %s",
            (mod_id,),
        )
        return True

    @staticmethod
    def add_modversion_to_selected_build(modver_id, mod_id, build_id, marked, optional):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            if marked == "1":
                cur.execute(
                    """SELECT id
                       FROM builds
                       WHERE marked = 1
                       ORDER BY id
                       LIMIT 1
                       FOR UPDATE"""
                )
                marked_build = cur.fetchone()
                if marked_build is None:
                    raise ValueError("No build is currently marked.")
                build_id = marked_build["id"]

            cur.execute(
                """SELECT modversions.mod_id, modversions.mcversion,
                          modversions.modloader, builds.minecraft,
                          builds.modloader AS build_modloader
                   FROM modversions
                   INNER JOIN builds ON builds.id = %s
                   WHERE modversions.id = %s
                   FOR UPDATE""",
                (build_id, modver_id),
            )
            selected = cur.fetchone()
            if selected is None:
                raise ValueError("The selected build or mod version no longer exists.")
            if int(mod_id) != selected["mod_id"]:
                raise ValueError("The selected version does not belong to that mod.")
            if not version_is_compatible(
                selected.get("mcversion"),
                selected.get("modloader"),
                selected["minecraft"],
                selected.get("build_modloader"),
            ):
                raise IncompatibleModVersionError(
                    "The selected version is not compatible with the build's "
                    "Minecraft version and modloader."
                )

            cur.execute(
                """SELECT build_modversion.id
                   FROM build_modversion
                   INNER JOIN modversions
                       ON build_modversion.modversion_id = modversions.id
                   WHERE build_modversion.build_id = %s
                     AND modversions.mod_id = %s
                   ORDER BY build_modversion.id
                   LIMIT 1""",
                (build_id, selected["mod_id"]),
            )
            existing = cur.fetchone()
            if existing is None:
                cur.execute(
                    """INSERT INTO build_modversion
                              (modversion_id, build_id, optional)
                       VALUES (%s, %s, %s)""",
                    (modver_id, build_id, optional),
                )
            else:
                cur.execute(
                    """UPDATE build_modversion
                       SET modversion_id = %s
                       WHERE id = %s""",
                    (modver_id, existing["id"]),
                )

            added_dependencies = Modversion._add_required_dependencies(
                cur,
                build_id,
                selected["minecraft"],
                selected["mod_id"],
                selected.get("build_modloader"),
            )
            conn.commit()
            return added_dependencies
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def _add_required_dependencies(
        cur, build_id, minecraft, root_mod_id, modloader=None
    ):
        cur.execute(
            """SELECT mod_dependencies.mod_id,
                      mod_dependencies.dependency_mod_id,
                      COALESCE(mods.pretty_name, mods.name) AS dependency_name
               FROM mod_dependencies
               LEFT JOIN mods
                   ON mod_dependencies.dependency_mod_id = mods.id
               ORDER BY mod_dependencies.mod_id, mod_dependencies.dependency_mod_id"""
        )
        dependencies_by_mod = {}
        dependency_names = {}
        for relationship in cur.fetchall() or []:
            dependency_mod_id = relationship["dependency_mod_id"]
            dependencies_by_mod.setdefault(relationship["mod_id"], []).append(
                dependency_mod_id
            )
            dependency_names[dependency_mod_id] = (
                relationship["dependency_name"] or f"Mod #{dependency_mod_id}"
            )

        cur.execute(
            """SELECT DISTINCT modversions.mod_id
               FROM build_modversion
               INNER JOIN modversions
                   ON build_modversion.modversion_id = modversions.id
               WHERE build_modversion.build_id = %s""",
            (build_id,),
        )
        present_mod_ids = {row["mod_id"] for row in (cur.fetchall() or [])}

        pending = deque(dependencies_by_mod.get(root_mod_id, ()))
        visited = {root_mod_id}
        added_dependencies = []
        while pending:
            dependency_mod_id = pending.popleft()
            if dependency_mod_id in visited:
                continue
            visited.add(dependency_mod_id)
            pending.extend(dependencies_by_mod.get(dependency_mod_id, ()))

            if dependency_mod_id in present_mod_ids:
                continue

            cur.execute(
                """SELECT id
                   FROM modversions
                   WHERE mod_id = %s
                     AND (mcversion = %s OR mcversion IS NULL)
                     AND (%s IS NULL OR modloader = %s OR modloader IS NULL)
                   ORDER BY CASE WHEN mcversion = %s THEN 0 ELSE 1 END,
                            CASE WHEN modloader = %s THEN 0 ELSE 1 END,
                            id DESC
                   LIMIT 1""",
                (
                    dependency_mod_id,
                    minecraft,
                    modloader,
                    modloader,
                    minecraft,
                    modloader,
                ),
            )
            dependency_version = cur.fetchone()
            if dependency_version is None:
                raise MissingDependencyVersionError(
                    dependency_names.get(
                        dependency_mod_id, f"Mod #{dependency_mod_id}"
                    ),
                    minecraft,
                    modloader,
                )

            cur.execute(
                """INSERT INTO build_modversion
                          (modversion_id, build_id, optional)
                   VALUES (%s, %s, 0)""",
                (dependency_version["id"], build_id),
            )
            present_mod_ids.add(dependency_mod_id)
            added_dependencies.append(dependency_names[dependency_mod_id])

        return added_dependencies

    @staticmethod
    def update_modversion_in_build(oldmodver_id, modver_id, build_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT replacement.mod_id, replacement.mcversion,
                          replacement.modloader,
                          current.mod_id AS current_mod_id,
                          builds.minecraft,
                          builds.modloader AS build_modloader
                   FROM modversions AS replacement
                   INNER JOIN modversions AS current ON current.id = %s
                   INNER JOIN builds ON builds.id = %s
                   WHERE replacement.id = %s""",
                (oldmodver_id, build_id, modver_id),
            )
            selected = cur.fetchone()
            if selected is None:
                raise ValueError("The selected build or mod version no longer exists.")
            if selected["mod_id"] != selected["current_mod_id"]:
                raise ValueError("The replacement version belongs to another mod.")
            if not version_is_compatible(
                selected.get("mcversion"),
                selected.get("modloader"),
                selected["minecraft"],
                selected.get("build_modloader"),
            ):
                raise IncompatibleModVersionError(
                    "The selected version is not compatible with the build's "
                    "Minecraft version and modloader."
                )
            cur.execute(
                """UPDATE build_modversion
                   SET modversion_id = %s
                   WHERE modversion_id = %s AND build_id = %s""",
                (modver_id, oldmodver_id, build_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def update_modversion_jarmd5(id, jarmd5):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                "UPDATE modversions SET jarmd5 = %s WHERE id = %s",
                (jarmd5, id),
            )
            if Modversion.JAR_MD5_PATTERN.fullmatch(str(jarmd5 or "").strip()):
                cur.execute(
                    """UPDATE mods
                       INNER JOIN modversions ON modversions.mod_id = mods.id
                       SET mods.modtype = 'MOD'
                       WHERE modversions.id = %s""",
                    (id,),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def delete_modversion(id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("DELETE FROM modversions WHERE id=%s", (id,))
        cur.execute("DELETE FROM build_modversion WHERE modversion_id = %s", (id,))
        conn.commit()
        return None

    @classmethod
    def get_by_id(cls, id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT * FROM modversions WHERE id = %s", (id,))
            row = cur.fetchone()
            if row:
                return cls(row["id"], row["mod_id"], row["version"], row["mcversion"], row["md5"], row["created_at"], row["updated_at"], row["filesize"], modloader=row.get("modloader"), integration_version_id=row.get("integration_version_id"), jarmd5=row.get("jarmd5"))
            return None
        finally:
            cur.close()
            conn.close()

    @classmethod
    def get_by_integration(cls, mod_id, integration_version_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT * FROM modversions
                   WHERE mod_id = %s AND integration_version_id = %s""",
                (mod_id, str(integration_version_id)),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return cls(
                row["id"], row["mod_id"], row["version"], row["mcversion"],
                row["md5"], row["created_at"], row["updated_at"],
                row["filesize"], modloader=row.get("modloader"),
                integration_version_id=row.get("integration_version_id"),
                jarmd5=row.get("jarmd5"),
            )
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def version_exists(mod_id, version):
        conn = Database.get_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT 1 FROM modversions WHERE mod_id = %s AND version = %s LIMIT 1",
                (mod_id, version),
            )
            return cur.fetchone() is not None
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def get_integration_version_ids(mod_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT integration_version_id FROM modversions
                   WHERE mod_id = %s AND integration_version_id IS NOT NULL""",
                (mod_id,),
            )
            return {
                str(row["integration_version_id"])
                for row in cur.fetchall()
            }
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def get_all():
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id, mod_id, version, mcversion, modloader FROM modversions")
        rows = cur.fetchall()
        if rows:
            return rows
        return []

    def get_builds_api(self, cid=None, api_key=False, modpack_ids=None):
        """List published builds containing this version that the caller can read."""
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            if api_key:
                cur.execute(
                    """SELECT DISTINCT
                              builds.id AS build_id,
                              builds.version AS build_version,
                              modpacks.id AS modpack_id,
                              modpacks.slug AS modpack_slug,
                              modpacks.name AS modpack_name,
                              build_modversion.optional
                       FROM build_modversion
                       INNER JOIN builds
                           ON build_modversion.build_id = builds.id
                       INNER JOIN modpacks
                           ON builds.modpack_id = modpacks.id
                       WHERE build_modversion.modversion_id = %s
                         AND builds.is_published = 1
                       ORDER BY builds.id ASC""",
                    (self.id,),
                )
            elif modpack_ids:
                placeholders = ", ".join(["%s"] * len(modpack_ids))
                # Only the number of bound placeholders is dynamic.
                cur.execute(
                    f"""SELECT DISTINCT
                              builds.id AS build_id,
                              builds.version AS build_version,
                              modpacks.id AS modpack_id,
                              modpacks.slug AS modpack_slug,
                              modpacks.name AS modpack_name,
                              build_modversion.optional
                       FROM build_modversion
                       INNER JOIN builds
                           ON build_modversion.build_id = builds.id
                       INNER JOIN modpacks
                           ON builds.modpack_id = modpacks.id
                       WHERE build_modversion.modversion_id = %s
                         AND builds.is_published = 1
                         AND (
                              (modpacks.private = 0 AND builds.private = 0)
                              OR modpacks.id IN ({placeholders})
                              OR EXISTS (
                                  SELECT 1
                                  FROM client_modpack cm
                                  INNER JOIN clients c ON cm.client_id = c.id
                                  WHERE cm.modpack_id = modpacks.id
                                    AND c.uuid = %s
                              )
                         )
                       ORDER BY builds.id ASC""",  # nosec B608
                    (self.id, *modpack_ids, cid),
                )
            else:
                cur.execute(
                    """SELECT DISTINCT
                              builds.id AS build_id,
                              builds.version AS build_version,
                              modpacks.id AS modpack_id,
                              modpacks.slug AS modpack_slug,
                              modpacks.name AS modpack_name,
                              build_modversion.optional
                       FROM build_modversion
                       INNER JOIN builds
                           ON build_modversion.build_id = builds.id
                       INNER JOIN modpacks
                           ON builds.modpack_id = modpacks.id
                       WHERE build_modversion.modversion_id = %s
                         AND builds.is_published = 1
                         AND (
                              (modpacks.private = 0 AND builds.private = 0)
                              OR EXISTS (
                                  SELECT 1
                                  FROM client_modpack cm
                                  INNER JOIN clients c ON cm.client_id = c.id
                                  WHERE cm.modpack_id = modpacks.id
                                    AND c.uuid = %s
                              )
                         )
                       ORDER BY builds.id ASC""",
                    (self.id, cid),
                )
            return [
                {
                    "id": row["build_id"],
                    "version": row["build_version"],
                    "optional": bool(row["optional"]),
                    "modpack": {
                        "id": row["modpack_id"],
                        "name": row["modpack_slug"],
                        "display_name": row["modpack_name"],
                    },
                }
                for row in cur.fetchall()
            ]
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def _repository_component(component, label):
        component = "" if component is None else str(component)
        if (
            component in {".", ".."}
            or re.fullmatch(r"[^/\\\x00-\x1f]+", component) is None
        ):
            raise ValueError(f"Invalid repository {label}.")
        return component

    @classmethod
    def repository_artifact_source(
        cls, repository_location, mod_slug, filename
    ):
        """Return an authorized HTTP URL or confined local repository path."""
        if not repository_location:
            raise ValueError("MD5_REPO_LOCATION is not configured.")

        mod_slug = cls._repository_component(mod_slug, "mod slug")
        filename = cls._repository_component(filename, "filename")
        location = str(repository_location)
        base = urlsplit(location)
        if base.scheme in {"http", "https"}:
            if (
                not base.hostname
                or base.username is not None
                or base.password is not None
                or base.query
                or base.fragment
            ):
                raise ValueError(
                    "MD5_REPO_LOCATION must be a plain HTTP(S) URL."
                )
            repository_path = base.path.rstrip("/")
            file_path = (
                f"{repository_path}/{quote(mod_slug, safe='-._~')}/"
                f"{quote(filename, safe='-._~')}"
            )
            return urlunsplit(
                (base.scheme, base.netloc, file_path, "", "")
            )

        if "://" in location:
            raise ValueError(
                "MD5_REPO_LOCATION must be an HTTP(S) URL or local path."
            )

        repository_root = Path(location).expanduser().resolve()
        source_path = (repository_root / mod_slug / filename).resolve()
        try:
            source_path.relative_to(repository_root)
        except ValueError as error:
            raise ValueError("Invalid local repository path.") from error
        return source_path

    @classmethod
    def repository_file_source(
        cls, repository_location, mod_slug, version
    ):
        mod_slug = cls._repository_component(mod_slug, "mod slug")
        version = cls._repository_component(version, "version")
        return cls.repository_artifact_source(
            repository_location,
            mod_slug,
            f"{mod_slug}-{version}.zip",
        )

    @staticmethod
    def get_file_size(repository_location, mod_slug, version):
        source = Modversion.repository_file_source(
            repository_location, mod_slug, version
        )
        if isinstance(source, Path):
            return source.stat().st_size

        response = requests.head(
            source,
            allow_redirects=False,
            timeout=(5, 30),
        )
        try:
            if 300 <= response.status_code < 400:
                raise requests.RequestException(
                    "Repository redirects are not allowed."
                )
            response.raise_for_status()
            try:
                file_size = int(response.headers.get("content-length", -1))
            except (TypeError, ValueError):
                return -1
            return file_size if file_size >= 0 else -1
        finally:
            response.close()

    def update_hash(self, md5, repository_location, mod_slug, file_size=None):
        conn = Database.get_connection()
        cur = conn.cursor()
        try:
            if file_size is None:
                file_size = Modversion.get_file_size(
                    repository_location, mod_slug, self.version
                )
            if file_size != -1:
                cur.execute(
                    "UPDATE modversions SET filesize = %s WHERE id = %s",
                    (file_size, self.id),
                )
            cur.execute(
                "UPDATE modversions SET md5 = %s WHERE id = %s",
                (md5, self.id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()
        self.md5 = md5
        self.updated_at = datetime.datetime.now()
        print(f"Updated hash for {self.mod_id} {self.version} to {md5}")
        return self

    def rehash(self, repository_location, mod_slug):
        source = Modversion.repository_file_source(
            repository_location, mod_slug, self.version
        )
        # Technic/Solder manifests require MD5 as a file checksum. It is not
        # used for passwords, signatures, or another security purpose.
        h = hashlib.md5(usedforsecurity=False)
        file_size = 0
        if isinstance(source, Path):
            with source.open("rb") as repository_file:
                while chunk := repository_file.read(8192):
                    h.update(chunk)
                    file_size += len(chunk)
        else:
            with requests.Session() as session:
                with session.get(
                    source,
                    stream=True,
                    allow_redirects=False,
                    timeout=(5, 60),
                ) as response:
                    if 300 <= response.status_code < 400:
                        raise requests.RequestException(
                            "Repository redirects are not allowed."
                        )
                    response.raise_for_status()
                    for chunk in response.iter_content(chunk_size=8192):
                        h.update(chunk)
                        file_size += len(chunk)
        self.update_hash(
            h.hexdigest(),
            repository_location,
            mod_slug,
            file_size=file_size,
        )

    def to_json(self):
        return {
            "mod_id": self.mod_id,
            "version": self.version,
            "md5": self.md5,
            "filesize": self.filesize,
        }
