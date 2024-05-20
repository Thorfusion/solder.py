import datetime
from .database import Database
from .mod import Mod
from .modversion import Modversion

class Build:
    def __init__(self, id, modpack_id, version, created_at, updated_at, minecraft, forge, is_published, private, min_java, min_memory, marked):
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

    @classmethod
    def delete_build(cls, id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("DELETE FROM build_modversion WHERE build_id = %s", (id,))
        cur.execute("DELETE FROM builds WHERE id=%s", (id,))
        conn.commit()
        return None

    @classmethod
    def update(cls, id, version, minecraft, is_published, private, min_java, min_memory):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        now = datetime.datetime.now()
        cur.execute("""UPDATE builds 
            SET version = %s, minecraft = %s, is_published = %s, private = %s, min_java = %s, min_memory = %s
            WHERE id = %s;""", ( version, minecraft, is_published, private, min_java, min_memory, id))
        conn.commit()
        return None

    @classmethod
    def update_checkbox(cls, id, value, column, table):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("UPDATE {} SET {} = %s WHERE id = %s".format(table, column), (value, id))
        conn.commit()
        return None
    
    @classmethod
    def update_checkbox_marked(cls, id, value):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("UPDATE builds SET marked = '0'")
        cur.execute("UPDATE builds SET marked = %s WHERE id = %s", (value, id))
        conn.commit()
        return None

    @classmethod
    def get_modpackname_by_id(cls, id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT modpack_id FROM builds WHERE id = %s", (id,))
        modpack_id = cur.fetchone()["modpack_id"]
        cur.execute("SELECT name FROM modpacks WHERE id = %s", (modpack_id,))
        name = cur.fetchone()["name"]
        if name is None:
            return None
        return (name)

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
        return []

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
        cursor.execute("SELECT modversions.id, modversions.mod_id, modversions.version, modversions.mcversion, modversions.md5, modversions.created_at, modversions.updated_at, modversions.filesize, mods.name AS modname, build_modversion.optional FROM modversions INNER JOIN build_modversion ON modversions.id = build_modversion.modversion_id JOIN mods ON modversions.mod_id = mods.id WHERE build_modversion.build_id = %s", (self.id,))
        modversions = cursor.fetchall()
        if modversions:
            versions = []
            for mv in modversions:
                v = Modversion(mv["id"], mv["mod_id"], mv["version"], mv["mcversion"], mv["md5"], mv["created_at"], mv["updated_at"], mv["filesize"], mv["optional"])
                v.modname = mv["modname"]
                versions.append(v)
            return versions
        return None