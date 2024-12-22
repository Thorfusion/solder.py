import datetime

from flask import flash

from .database import Database
from .modversion import Modversion


class Build:
    def __init__(self, id, modpack_id, version, created_at, updated_at, minecraft, forge, is_published, private, min_java, min_memory, marked, count=None):
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

    @classmethod
    def new(cls, modpack_id, version, minecraft, is_published, private, min_java, min_memory, clone_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        now = datetime.datetime.now()
        cur.execute("INSERT INTO builds (modpack_id, version, created_at, updated_at, minecraft, is_published, private, min_java, min_memory) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)", (modpack_id, version, now, now, minecraft, is_published, private, min_java, min_memory))
        conn.commit()
        cur.execute("SELECT LAST_INSERT_ID() AS id")
        id = cur.fetchone()["id"]
        if clone_id != "":
            cur.execute("SELECT * FROM build_modversion WHERE build_id = %s", (clone_id,))
            modversions = cur.fetchall()
            if modversions:
                for mv in modversions:
                    cur.execute("INSERT INTO build_modversion (modversion_id, build_id) VALUES (%s, %s)", (mv["modversion_id"], id))
            conn.commit()
        cls(id, modpack_id, version, now, now, minecraft, "0", is_published, private, min_java, min_memory, "0")

    @staticmethod
    def delete_build(id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("DELETE FROM build_modversion WHERE build_id = %s", (id,))
        cur.execute("DELETE FROM builds WHERE id=%s", (id,))
        conn.commit()
        return None

    @staticmethod
    def update(id, version, minecraft, is_published, private, min_java, min_memory):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        now = datetime.datetime.now()
        cur.execute("""UPDATE builds 
            SET version = %s, minecraft = %s, is_published = %s, private = %s, min_java = %s, min_memory = %s
            WHERE id = %s;""", (version, minecraft, is_published, private, min_java, min_memory, id))
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
        cur.execute("SELECT modpack_id FROM builds WHERE id = %s", (id,))
        try: 
            row = cur.fetchone()["modpack_id"]
            conn.commit()
            return (row)
        except:
            flash("unable to get modpackid by id", "error")
            return 0

    @classmethod
    def get_by_id(cls, id):
        conn = Database.get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM builds WHERE id = %s", (id,))
        build = cursor.fetchone()
        if build is None:
            return None
        return cls(**build)

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
    def get_by_modpack_api(modpack):
        conn = Database.get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """SELECT builds.*
                FROM builds
                WHERE modpack_id = %s
                ORDER BY builds.id ASC
            """, (modpack.id,))
        builds = cursor.fetchall()
        if builds:
            return [Build(**build) for build in builds]
        return []

    @staticmethod
    def get_by_modpack_cid(modpack, cid):
        conn = Database.get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """SELECT builds.*
                FROM builds
                WHERE modpack_id = %s
                AND is_published = 1 AND (private = 0 OR modpack_id IN (SELECT modpack_id FROM client_modpack cm JOIN clients c ON cm.client_id = c.id WHERE c.uuid = %s))
                ORDER BY builds.id ASC
            """, (modpack.id, cid))
        builds = cursor.fetchall()
        if builds:
            return [Build(**build) for build in builds]
        return []

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

    def get_modversions_api(self, tag: str):
        conn = Database.get_connection()
        cursor = conn.cursor(dictionary=True)
        if tag == "optional":
            cursor.execute(
                """SELECT modversions.id, modversions.mod_id, modversions.version, modversions.mcversion, modversions.md5, modversions.created_at, modversions.updated_at, modversions.filesize, mods.name AS modname, mods.pretty_name, mods.author, mods.link, mods.description, build_modversion.optional 
                FROM modversions
                INNER JOIN build_modversion ON modversions.id = build_modversion.modversion_id JOIN mods ON modversions.mod_id = mods.id 
                WHERE build_modversion.build_id = %s AND mods.side IN ('CLIENT','BOTH')
                """, (self.id,))
        elif tag == "server":
            cursor.execute(
                """SELECT modversions.id, modversions.mod_id, modversions.version, modversions.mcversion, modversions.md5, modversions.created_at, modversions.updated_at, modversions.filesize, mods.name AS modname, mods.pretty_name, mods.author, mods.link, mods.description, build_modversion.optional 
                FROM modversions
                INNER JOIN build_modversion ON modversions.id = build_modversion.modversion_id JOIN mods ON modversions.mod_id = mods.id 
                WHERE build_modversion.build_id = %s AND build_modversion.optional = 0 AND mods.side IN ('SERVER','BOTH')
                """, (self.id,))
        else:
            cursor.execute(
                """SELECT modversions.id, modversions.mod_id, modversions.version, modversions.mcversion, modversions.md5, modversions.created_at, modversions.updated_at, modversions.filesize, mods.name AS modname, mods.pretty_name, mods.author, mods.link, mods.description, build_modversion.optional 
                FROM modversions
                INNER JOIN build_modversion ON modversions.id = build_modversion.modversion_id JOIN mods ON modversions.mod_id = mods.id 
                WHERE build_modversion.build_id = %s AND build_modversion.optional = 0 AND mods.side IN ('CLIENT','BOTH')
                """, (self.id,))
        modversions = cursor.fetchall()
        if modversions:
            versions = []
            for mv in modversions:
                v = Modversion(mv["id"], mv["mod_id"], mv["version"], mv["mcversion"], mv["md5"], mv["created_at"], mv["updated_at"], mv["filesize"], mv["optional"])
                v.modname = mv["modname"]
                v.pretty_name = mv["pretty_name"]
                v.author = mv["author"]
                v.link = mv["link"]
                v.description = mv["description"]
                versions.append(v)
            return versions
        return None
