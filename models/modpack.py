import datetime

from .build import Build
from .database import Database


class Modpack:
    def __init__(self, id, name, slug, recommended, latest, created_at, updated_at, order, hidden, private, pinned, enable_optionals=0, enable_server=0):
        self.id = id
        self.name = name
        self.slug = slug
        self.recommended = recommended
        self.latest = latest
        self.created_at = created_at
        self.updated_at = updated_at
        self.order = order
        self.hidden = hidden
        self.private = private
        self.pinned = pinned
        self.enable_optionals = enable_optionals
        self.enable_server = enable_server

    @staticmethod
    def new(name, slug, hidden, private, user_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        now = datetime.datetime.now()
        cur.execute("INSERT INTO modpacks (name, slug, created_at, updated_at, hidden, private, user_id) VALUES (%s, %s, %s, %s, %s, %s, %s)", (name, slug, now, now, hidden, private, user_id))
        conn.commit()
        cur.execute("SELECT LAST_INSERT_ID() AS id")

    @staticmethod
    def delete_modpack(id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM builds WHERE modpack_id = %s", (id,))
        modversions = cur.fetchall()
        if modversions:
            for mv in modversions:
                cur.execute("DELETE FROM build_modversion WHERE build_id = %s", (mv["id"],))
        cur.execute("DELETE FROM builds WHERE modpack_id = %s", (id,))
        cur.execute("DELETE FROM modpacks WHERE id=%s", (id,))
        conn.commit()

    @classmethod
    def get_by_id(cls, id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM modpacks WHERE id = %s", (id,))
        row = cur.fetchone()
        if row:
            return cls(row["id"], row["name"], row["slug"], row["recommended"], row["latest"], row["created_at"], row["updated_at"], row["order"], row["hidden"], row["private"], row["pinned"])
        return None

    @staticmethod
    def get_by_pinned():
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT name, id FROM modpacks WHERE pinned = 1")
        rows = cur.fetchall()
        if rows:
            return rows
        return []

    @staticmethod
    def get_by_cid_api(cid):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM modpacks WHERE hidden = 0 AND (private = 0 OR id IN (SELECT modpack_id FROM client_modpack cm JOIN clients c ON cm.client_id = c.id WHERE c.uuid = %s))", (cid,))
        rows = cur.fetchall()
        if rows:
            return [Modpack(row["id"], row["name"], row["slug"], row["recommended"], row["latest"], row["created_at"], row["updated_at"], row["order"], row["hidden"], row["private"], row["pinned"]) for row in rows]
        return None

    @classmethod
    def get_by_cid_slug_api(cls, cid, slug):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM modpacks WHERE slug = %s AND (hidden = 0 OR id IN (SELECT modpack_id FROM client_modpack cm JOIN clients c ON cm.client_id = c.id WHERE c.uuid = %s))", (slug, cid))
        row = cur.fetchone()
        if row:
            return cls(row["id"], row["name"], row["slug"], row["recommended"], row["latest"], row["created_at"], row["updated_at"], row["order"], row["hidden"], row["private"], row["pinned"], row["enable_optionals"], row["enable_server"])
        return None

    @staticmethod
    def get_all() -> list:
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM modpacks")
        rows = cur.fetchall()
        if rows:
            return rows
        return []

    def get_builds(self):
        return Build.get_by_modpack(self)
    
    def get_builds_cid(self, cid):
        return Build.get_by_modpack_cid(self, cid)

    def get_build(self, version):
        return Build.get_by_modpack_version(self, version)

    def to_json(self):
        data = {
            "name": self.slug,
            "display_name": self.name,
            "recommended": self.recommended,
            "latest": self.latest,
        }

        if self.builds is not None:
            buildversions = []
            for build in self.builds:
                buildversions.append(build.version)
                if self.enable_optionals:
                    buildversions.append(build.version + "-optional")
                if self.enable_server:
                    buildversions.append(build.version + "-server")
            data["builds"] = buildversions
        return data
