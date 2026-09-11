import datetime
import re

from flask import flash

from .compatibility import normalize_modloader
from .database import Database
from .modversion import Modversion


class Build:
    def __init__(self, id, modpack_id, version, created_at, updated_at, minecraft, forge, is_published, private, min_java, min_memory, marked, count=None, modloader=None):
        self.id = id
        self.modpack_id = modpack_id
        self.version = version
        self.created_at = created_at
        self.updated_at = updated_at
        self.minecraft = minecraft
        self.forge = forge
        self.is_published = is_published
        self.private = private
        self.min_java = min_java
        self.min_memory = min_memory
        self.marked = marked
        self.count = count
        self.modloader = normalize_modloader(modloader)
        if self.modloader is None and forge:
            self.modloader = "FORGE"

    @classmethod
    def new(cls, modpack_id, version, minecraft, is_published, private, min_java, min_memory, clone_id, forge=None, modloader=None):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        now = datetime.datetime.now()
        modloader = normalize_modloader(modloader)
        if modloader is None and forge:
            modloader = "FORGE"
        cur.execute("INSERT INTO builds (modpack_id, version, created_at, updated_at, minecraft, forge, modloader, is_published, private, min_java, min_memory) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (modpack_id, version, now, now, minecraft, forge, modloader, is_published, private, min_java, min_memory))
        conn.commit()
        cur.execute("SELECT LAST_INSERT_ID() AS id")
        id = cur.fetchone()["id"]
        if clone_id != "":
            cur.execute("SELECT * FROM build_modversion WHERE build_id = %s", (clone_id,))
            modversions = cur.fetchall()
            if modversions:
                for mv in modversions:
                    cur.execute("INSERT INTO build_modversion (modversion_id, build_id, optional) VALUES (%s, %s, %s)", (mv["modversion_id"], id, mv["optional"]))
            conn.commit()
        cls(id, modpack_id, version, now, now, minecraft, forge, is_published, private, min_java, min_memory, "0", modloader=modloader)

    @staticmethod
    def delete_build(id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("DELETE FROM build_modversion WHERE build_id = %s", (id,))
        cur.execute("DELETE FROM builds WHERE id=%s", (id,))
        conn.commit()
        return None

    @staticmethod
    def update(id, version, minecraft, is_published, private, min_java, min_memory, forge=None, modloader=None):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        now = datetime.datetime.now()
        modloader = normalize_modloader(modloader)
        if modloader is None and forge:
            modloader = "FORGE"
        cur.execute("""UPDATE builds 
            SET version = %s, minecraft = %s, forge = %s, modloader = %s, is_published = %s, private = %s, min_java = %s, min_memory = %s
            WHERE id = %s;""", (version, minecraft, forge, modloader, is_published, private, min_java, min_memory, id))
        conn.commit()
        return None

    @staticmethod
    def update_checkbox_marked(id, value):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("UPDATE builds SET marked = '0'")
        cur.execute("UPDATE builds SET marked = %s WHERE id = %s", (value, id))
        conn.commit()
        return None

    @staticmethod
    def get_modpackname_by_id(id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT modpack_id FROM builds WHERE id = %s", (id,))
        modpack_id = cur.fetchone()["modpack_id"]
        cur.execute("SELECT name FROM modpacks WHERE id = %s", (modpack_id,))
        name = cur.fetchone()["name"]
        if name is None:
            flash("unable to get modpackname by id", "error")
            return None
        return (name)
    
    @classmethod
    def get_modpackid_by_id(cls, id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT modpack_id FROM builds WHERE id = %s", (id,))
            row = cur.fetchone()
            return row["modpack_id"] if row else 0
        finally:
            cur.close()
            conn.close()

    @classmethod
    def get_by_id(cls, id):
        conn = Database.get_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM builds WHERE id = %s", (id,))
            build = cursor.fetchone()
            if build is None:
                return None
            return cls(**build)
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_modpack(modpack):
        conn = Database.get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """SELECT builds.*, modcount.count
                FROM builds
                LEFT JOIN (SELECT build_id, COUNT(*) AS count FROM build_modversion GROUP BY build_id) modcount ON builds.id = modcount.build_id
                WHERE modpack_id = %s
                ORDER BY builds.id DESC
            """, (modpack.id,))
        builds = cursor.fetchall()
        if builds:
            return [Build(**build) for build in builds]
        return []
    
    @staticmethod
    def get_by_modpack_api(modpack, cid=None, api_key=False):
        conn = Database.get_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            if api_key:
                cursor.execute(
                    """SELECT builds.*
                       FROM builds
                       WHERE builds.modpack_id = %s
                         AND builds.is_published = 1
                       ORDER BY builds.id ASC""",
                    (modpack.id,),
                )
            else:
                cursor.execute(
                    """SELECT builds.*
                       FROM builds
                       INNER JOIN modpacks ON builds.modpack_id = modpacks.id
                       WHERE builds.modpack_id = %s
                         AND builds.is_published = 1
                         AND (
                              (modpacks.private = 0 AND builds.private = 0)
                              OR EXISTS (
                                  SELECT 1
                                  FROM client_modpack cm
                                  INNER JOIN clients c ON cm.client_id = c.id
                                  WHERE cm.modpack_id = builds.modpack_id
                                    AND c.uuid = %s
                              )
                         )
                       ORDER BY builds.id ASC""",
                    (modpack.id, cid),
                )
            return [Build(**build) for build in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_modpack_cid(modpack, cid):
        return Build.get_by_modpack_api(modpack, cid=cid)

    @classmethod
    def get_by_modpack_version_api(cls, modpack, version, cid=None, api_key=False):
        """Return a published build only when the API caller may access it."""
        conn = Database.get_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            if api_key:
                cursor.execute(
                    """SELECT builds.*
                       FROM builds
                       WHERE builds.modpack_id = %s
                         AND builds.version = %s
                         AND builds.is_published = 1""",
                    (modpack.id, version),
                )
            else:
                cursor.execute(
                    """SELECT builds.*
                       FROM builds
                       INNER JOIN modpacks ON builds.modpack_id = modpacks.id
                       WHERE builds.modpack_id = %s
                         AND builds.version = %s
                         AND builds.is_published = 1
                         AND (
                              (modpacks.private = 0 AND builds.private = 0)
                              OR EXISTS (
                                  SELECT 1
                                  FROM client_modpack cm
                                  INNER JOIN clients c ON cm.client_id = c.id
                                  WHERE cm.modpack_id = builds.modpack_id
                                    AND c.uuid = %s
                              )
                         )""",
                    (modpack.id, version, cid),
                )
            build = cursor.fetchone()
            return cls(**build) if build else None
        finally:
            cursor.close()
            conn.close()

    @classmethod
    def get_by_modpack_version(cls, modpack, version):
        conn = Database.get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM builds WHERE modpack_id = %s AND version = %s", (modpack.id, version))
        build = cursor.fetchone()
        if build is None:
            flash("unable to get modpack by version", "error")
            return None
        return cls(**build)
    
    @staticmethod
    def get_marked_build():
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id FROM builds WHERE marked = 1")
        try: 
            build_id = cur.fetchone()["id"]
            return (build_id)
        except:
            return 0

    def get_modversions_api(
        self, tag: str = "", target=None, include_optional=None
    ):
        if target is None:
            target = "server" if tag == "server" else "client"
        if include_optional is None:
            include_optional = tag == "optional"
        if target not in {"client", "server"}:
            raise ValueError("target must be client or server")

        conn = Database.get_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            if target == "server":
                cursor.execute(
                    """SELECT modversions.id, modversions.mod_id,
                              modversions.version, modversions.mcversion,
                              modversions.modloader,
                              modversions.md5, modversions.created_at,
                              modversions.updated_at, modversions.filesize,
                              mods.name AS modname, mods.pretty_name,
                              mods.author, mods.link, mods.description,
                              mods.side, mods.modtype,
                              build_modversion.optional
                       FROM modversions
                       INNER JOIN build_modversion
                           ON modversions.id = build_modversion.modversion_id
                       INNER JOIN mods ON modversions.mod_id = mods.id
                       WHERE build_modversion.build_id = %s
                         AND (%s = 1 OR build_modversion.optional = 0)
                         AND mods.side IN ('SERVER', 'BOTH')""",
                    (self.id, int(include_optional)),
                )
            else:
                cursor.execute(
                    """SELECT modversions.id, modversions.mod_id,
                              modversions.version, modversions.mcversion,
                              modversions.modloader,
                              modversions.md5, modversions.created_at,
                              modversions.updated_at, modversions.filesize,
                              mods.name AS modname, mods.pretty_name,
                              mods.author, mods.link, mods.description,
                              mods.side, mods.modtype,
                              build_modversion.optional
                       FROM modversions
                       INNER JOIN build_modversion
                           ON modversions.id = build_modversion.modversion_id
                       INNER JOIN mods ON modversions.mod_id = mods.id
                       WHERE build_modversion.build_id = %s
                         AND (%s = 1 OR build_modversion.optional = 0)
                         AND mods.side IN ('CLIENT', 'BOTH')""",
                    (self.id, int(include_optional)),
                )
            modversions = cursor.fetchall()
            versions = []
            for mv in modversions:
                v = Modversion(mv["id"], mv["mod_id"], mv["version"], mv["mcversion"], mv["md5"], mv["created_at"], mv["updated_at"], mv["filesize"], mv["optional"], mv.get("modloader"))
                v.modname = mv["modname"]
                v.pretty_name = mv["pretty_name"]
                v.author = mv["author"]
                v.link = mv["link"]
                v.description = mv["description"]
                v.side = mv.get("side", "BOTH")
                v.modtype = mv.get("modtype", "MOD")
                versions.append(v)

            def natural_name_key(modversion):
                parts = re.split(r"(\d+)", modversion.modname.casefold())
                return tuple(
                    int(part) if part.isdigit() else part for part in parts
                ), modversion.id

            return sorted(versions, key=natural_name_key)
        finally:
            cursor.close()
            conn.close()
