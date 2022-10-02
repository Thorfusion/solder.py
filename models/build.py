import datetime
from .database import Database

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