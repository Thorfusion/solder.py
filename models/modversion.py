import datetime
from .database import Database

class Modversion:
    def __init__(self, id, mod_id, version, md5, created_at, updated_at, filesize, optional=False):
        self.id = id
        self.mod_id = mod_id
        self.version = version
        self.md5 = md5
        self.created_at = created_at
        self.updated_at = updated_at
        self.filesize = filesize
        self.optional = optional

    @classmethod
    def new(cls, mod_id, version, md5, filesize):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        now = datetime.datetime.now()
        cur.execute("INSERT INTO modversions (mod_id, version, md5, created_at, updated_at, filesize) VALUES (%s, %s, %s, %s, %s, %s)", (mod_id, version, md5, now, now, filesize))
        conn.commit()
        cur.execute("SELECT LAST_INSERT_ID() AS id")
        id = cur.fetchone()["id"]
        return cls(id, mod_id, version, md5, now, now, filesize)

    @classmethod
    def get_by_id(cls, id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM modversions WHERE id = %s", (id,))
        row = cur.fetchone()
        if row:
            return cls(row["id"], row["mod_id"], row["version"], row["md5"], row["created_at"], row["updated_at"], row["filesize"])
        return None

    def to_json(self):
        return {
            "id": self.id,
            "mod_id": self.mod_id,
            "version": self.version,
            "md5": self.md5,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "filesize": self.filesize
        }