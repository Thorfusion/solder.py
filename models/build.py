import datetime
from .database import Database
from .mod import Mod
from .modversion import Modversion

class Build:
    def __init__(self, id, modpack_id, version, created_at, updated_at, minecraft, forge, is_published, private, min_java, min_memory):
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

    @classmethod
    def new(cls, modpack_id, version, minecraft, forge, is_published, private, min_java, min_memory):
        conn = Database.get_connection()
        cursor = conn.cursor(dictionary=True)
        now = datetime.datetime.now()
        cursor.execute("INSERT INTO builds (modpack_id, version, created_at, updated_at, minecraft, forge, is_published, private, min_java, min_memory) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id", (modpack_id, version, now, now, minecraft, forge, is_published, private, min_java, min_memory))
        id = cursor.fetchone()["id"]
        cls(id, modpack_id, version, now, now, minecraft, forge, is_published, private, min_java, min_memory)

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
        cursor.execute("SELECT * FROM builds WHERE modpack_id = %s", (modpack.id,))
        builds = cursor.fetchall()
        if builds:
            return [Build(**build) for build in builds]
        return None

    @classmethod
    def get_by_modpack_version(cls, modpack, version):
        conn = Database.get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM builds WHERE modpack_id = %s AND version = %s", (modpack.id, version))
        build = cursor.fetchone()
        if build is None:
            return None
        return cls(**build)

    def get_modversions_minimal(self):
        conn = Database.get_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT modversions.id, modversions.mod_id, modversions.version, modversions.md5, modversions.created_at, modversions.updated_at, modversions.filesize, mods.name AS modname FROM modversions INNER JOIN build_modversion ON modversions.id = build_modversion.modversion_id JOIN mods ON modversions.mod_id = mods.id WHERE build_modversion.build_id = %s", (self.id,))
        modversions = cursor.fetchall()
        if modversions:
            versions = []
            for mv in modversions:
                v = Modversion(mv["id"], mv["mod_id"], mv["version"], mv["md5"], mv["created_at"], mv["updated_at"], mv["filesize"])
                v.modname = mv["modname"]
                versions.append(v)
            return versions
        return None