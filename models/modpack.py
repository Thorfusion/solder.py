import datetime
from .database import Database
from .build import Build

class Modpack:
    def __init__(self, id, name, slug, recommended, latest, created_at, updated_at, order, hidden, private, pinned):
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

    @classmethod
    def new(cls, name, slug, hidden, private, user_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        now = datetime.datetime.now()
        cur.execute("INSERT INTO modpacks (name, slug, created_at, updated_at, hidden, private, user_id) VALUES (%s, %s, %s, %s, %s, %s, %s)", (name, slug, now, now, hidden, private, user_id))
        conn.commit()
        cur.execute("SELECT LAST_INSERT_ID() AS id")
        id = cur.fetchone()["id"]
        return None
        return cls(id, name, slug, now, now, hidden, private)
    
    @classmethod
    def update_checkbox(cls, id, value, column, table):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("UPDATE {} SET {} = %s WHERE id = %s".format(table, column), (value, id))
        conn.commit()
        return None
    
    @classmethod
    def get_by_id(cls, id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM modpacks WHERE id = %s", (id,))
        row = cur.fetchone()
        if row:
            return cls(row["id"], row["name"], row["slug"], row["recommended"], row["latest"], row["created_at"], row["updated_at"], row["order"], row["hidden"], row["private"], row["pinned"])
        return None
    
    @classmethod
    def get_by_pinned(cls):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM modpacks WHERE pinned = 1")
        rows = cur.fetchall()
        if rows:
            return [Modpack(row["id"], row["name"]) for row in rows]
        return None

    @staticmethod
    def get_by_cid(cid):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM modpacks WHERE hidden = 0 OR id IN (SELECT modpack_id FROM client_modpack cm JOIN clients c ON cm.client_id = c.id WHERE c.uuid = %s)", (cid,))
        rows = cur.fetchall()
        if rows:
            return [Modpack(row["id"], row["name"], row["slug"], row["recommended"], row["latest"], row["created_at"], row["updated_at"], row["order"], row["hidden"], row["private"], row["pinned"]) for row in rows]
        return None

    @classmethod
    def get_by_cid_slug(cls, cid, slug):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM modpacks WHERE slug = %s AND (hidden = 0 OR id IN (SELECT modpack_id FROM client_modpack cm JOIN clients c ON cm.client_id = c.id WHERE c.uuid = %s))", (slug, cid))
        row = cur.fetchone()
        if row:
            return cls(row["id"], row["name"], row["slug"], row["recommended"], row["latest"], row["created_at"], row["updated_at"], row["order"], row["hidden"], row["private"], row["pinned"])
        return None

    @staticmethod
    def get_all() -> list:
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM modpacks")
        rows = cur.fetchall()
        if rows:
            return [Modpack(row["id"], row["name"], row["slug"], row["recommended"], row["latest"], row["created_at"], row["updated_at"], row["order"], row["hidden"], row["private"], row["pinned"]) for row in rows]
        return []

    def get_builds(self):
        return Build.get_by_modpack(self)

    def get_build(self, version):
        return Build.get_by_modpack_version(self, version)

    def to_json(self):
        data ={
            "name": self.slug,
            "display_name": self.name,
            "recommended": self.recommended,
            "latest": self.latest,
        }

        if self.builds is not None:
            data["builds"] = [build.version for build in self.builds]
        return data