import datetime
from .database import Database
import requests
import hashlib

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
    def new(cls, mod_id, version, mcversion, md5, filesize, markedbuild):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        now = datetime.datetime.now()
        cur.execute("INSERT INTO modversions (mod_id, version, mcversion, md5, created_at, updated_at, filesize) VALUES (%s, %s, %s, %s, %s, %s, %s)", (mod_id, version, mcversion, md5, now, now, filesize))
        conn.commit()
        cur.execute("SELECT LAST_INSERT_ID() AS id")
        id = cur.fetchone()["id"]
        if markedbuild is "1":
            cur.execute("SELECT id FROM builds WHERE marked = 1")
            marked_build_id = cur.fetchone()["id"]
            # when new modversion is added to build, old modversion gets deleted, quite tricky as both values are unique each time and you need to get all modversion and delete them on said build.
            cur.execute("SELECT * FROM modversions WHERE mod_id = %s", (mod_id,))
            modversions = cur.fetchall()
            if modversions:
                for mv in modversions:
                    cur.execute("DELETE FROM build_modversion WHERE modversion_id = %s AND build_id = %s", (mv["id"], marked_build_id))
            cur.execute("INSERT INTO build_modversion (modversion_id, build_id) VALUES (%s, %s)", (id, marked_build_id))
            conn.commit()
        return cls(id, mod_id, version, mcversion, md5, now, now, filesize)
    
    @classmethod
    def add_modversion_to_selected_build(cls, modver_id, mod_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id FROM builds WHERE marked = 1")
        marked_build_id = cur.fetchone()["id"]
        # when new modversion is added to build, old modversion gets deleted, quite tricky as both values are unique each time and you need to get all modversion and delete them on said build.
        cur.execute("DELETE FROM build_modversion WHERE modversion_id = %s AND build_id = %s", (mod_id, marked_build_id))
        cur.execute("INSERT INTO build_modversion (modversion_id, build_id) VALUES (%s, %s)", (modver_id, marked_build_id))
        conn.commit()
        return None

    @classmethod
    def delete_modversion(cls, id):
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
        cur.execute("SELECT * FROM modversions")
        rows = cur.fetchall()
        if rows:
            return [Modversion(row["id"], row["mod_id"], row["version"], row["mcversion"], row["md5"], row["created_at"], row["updated_at"], row["filesize"]) for row in rows]
        return []

    def update_hash(self, md5):
        conn = Database.get_connection()
        cur = conn.cursor()
        cur.execute("UPDATE modversions SET md5 = %s WHERE id = %s", (md5, self.id))
        conn.commit()
        self.md5 = md5
        self.updated_at = datetime.datetime.now()
        print(f"Updated hash for {self.mod_id} {self.version} to {md5}")
        return self

    def rehash(self, rehash_url):
        with requests.Session() as s:
            h = hashlib.md5()
            resp = s.get(rehash_url, stream=True)
            for chunk in resp.iter_content(chunk_size=8192):
                h.update(chunk)
            self.update_hash(h.hexdigest())

    def to_json(self):
        return {
            "id": self.id,
            "mod_id": self.mod_id,
            "version": self.version,
            "md5": self.md5,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "filesize": self.filesize,
        }