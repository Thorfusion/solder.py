from collections import deque
import datetime
import hashlib
import threading

import requests

from .database import Database


class MissingDependencyVersionError(ValueError):
    def __init__(self, dependency_name, minecraft):
        self.dependency_name = dependency_name
        self.minecraft = minecraft
        super().__init__(
            f'{dependency_name} has no version compatible with Minecraft {minecraft}.'
        )


class Modversion:
    def __init__(self, id, mod_id, version, mcversion, md5, created_at, updated_at, filesize, optional=0):
        self.id = id
        self.mod_id = mod_id
        self.version = version
        self.mcversion = mcversion
        self.md5 = md5
        self.created_at = created_at
        self.updated_at = updated_at
        self.filesize = filesize
        self.optional = optional

    @classmethod
    def new(cls, mod_id, version, mcversion, md5, filesize, markedbuild, url="0", jarmd5="0"):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        now = datetime.datetime.now()
        cur.execute("INSERT INTO modversions (mod_id, version, mcversion, md5, created_at, updated_at, filesize) VALUES (%s, %s, %s, %s, %s, %s, %s)", (mod_id, version, mcversion, md5, now, now, filesize))
        conn.commit()
        cur.execute("SELECT LAST_INSERT_ID() AS id")
        id = cur.fetchone()["id"]
        conn.commit()
        if markedbuild == "1":
            Modversion.add_modversion_to_selected_build(id, mod_id, "0", "1", "0")
        if md5 == "0":
            version = Modversion.get_by_id(id)
            t = threading.Thread(target=version.rehash, args=(url,))
            t.start()
        if jarmd5 != "0":
            Modversion.update_modversion_jarmd5(id, jarmd5)
        return cls(id, mod_id, version, mcversion, md5, now, now, filesize)

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
                """SELECT modversions.mod_id, builds.minecraft
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
    def _add_required_dependencies(cur, build_id, minecraft, root_mod_id):
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
                   ORDER BY CASE WHEN mcversion = %s THEN 0 ELSE 1 END, id DESC
                   LIMIT 1""",
                (dependency_mod_id, minecraft, minecraft),
            )
            dependency_version = cur.fetchone()
            if dependency_version is None:
                raise MissingDependencyVersionError(
                    dependency_names.get(
                        dependency_mod_id, f"Mod #{dependency_mod_id}"
                    ),
                    minecraft,
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
        cur.execute("UPDATE build_modversion SET modversion_id = %s WHERE modversion_id = %s AND build_id = %s", (modver_id, oldmodver_id, build_id))
        conn.commit()
        return None

    @staticmethod
    def update_modversion_jarmd5(id, jarmd5):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("UPDATE modversions SET jarmd5 = %s WHERE id = %s", (jarmd5, id))
        conn.commit()
        return None

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
        cur.execute("SELECT * FROM modversions WHERE id = %s", (id,))
        row = cur.fetchone()
        if row:
            return cls(row["id"], row["mod_id"], row["version"], row["mcversion"], row["md5"], row["created_at"], row["updated_at"], row["filesize"])
        return None

    @staticmethod
    def get_all():
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id, mod_id, version, mcversion FROM modversions")
        rows = cur.fetchall()
        if rows:
            return rows
        return []

    def get_builds_api(self, cid=None, api_key=False):
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

    def get_file_size(url):
        response = requests.head(url)  # Only get headers, not content
        file_size = int(response.headers.get('content-length', -1))  # Get file size from headers

        return file_size
        # https://www.classace.io/answers/56cb76718f9932eba6153a625885309b

    def update_hash(self, md5, filesize_url):
        conn = Database.get_connection()
        cur = conn.cursor()
        file_size = Modversion.get_file_size(filesize_url)
        if file_size != -1:
            cur.execute("UPDATE modversions SET filesize = %s WHERE id = %s", (file_size, self.id))
        cur.execute("UPDATE modversions SET md5 = %s WHERE id = %s", (md5, self.id))
        conn.commit()
        self.md5 = md5
        self.updated_at = datetime.datetime.now()
        print(f"Updated hash for {self.mod_id} {self.version} to {md5}")
        return self

    def rehash(self, rehash_url):
        with requests.Session() as s:
            # Technic/Solder manifests require MD5 as a file checksum. It is not used for passwords, signatures, or another security purpose.
            h = hashlib.md5(usedforsecurity=False)
            resp = s.get(rehash_url, stream=True)
            for chunk in resp.iter_content(chunk_size=8192):
                h.update(chunk)
            self.update_hash(h.hexdigest(), rehash_url)

    def to_json(self):
        return {
            "mod_id": self.mod_id,
            "version": self.version,
            "md5": self.md5,
            "filesize": self.filesize,
        }
