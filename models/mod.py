import datetime

from .database import Database
from .modversion import Modversion


class Mod:
    def __init__(self, id, name, description, author, link, created_at, updated_at, pretty_name, side, modtype, note):
        self.id = id
        self.name = name
        self.description = description
        self.author = author
        self.link = link
        self.created_at = created_at
        self.updated_at = updated_at
        self.pretty_name = pretty_name
        self.side = side
        self.modtype = modtype
        self.note = note

    @classmethod
    def new(cls, name, description, author, link, pretty_name, side, modtype, note):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        now = datetime.datetime.now()
        cur.execute("INSERT INTO mods (name, description, author, link, created_at, updated_at, pretty_name, side, modtype, note) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (name, description, author, link, now, now, pretty_name, side, modtype, note))
        conn.commit()
        cur.execute("SELECT LAST_INSERT_ID() AS id")
        id = cur.fetchone()["id"]
        return cls(id, name, description, author, link, now, now, pretty_name, side, modtype, note)

    @staticmethod
    def update(id, name, description, author, link, pretty_name, side, modtype, note):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        now = datetime.datetime.now()
        cur.execute("""UPDATE mods 
            SET name = %s, description = %s, author = %s, link = %s, updated_at = %s, pretty_name = %s, side = %s, modtype = %s, note = %s 
            WHERE id = %s;""", (name, description, author, link, now, pretty_name, side, modtype, note, id))
        conn.commit()
        cur.execute("SELECT LAST_INSERT_ID() AS id")
        id = cur.fetchone()["id"]
        return None

    @staticmethod
    def delete_mod(id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM modversions WHERE mod_id = %s", (id,))
        modversions = cur.fetchall()
        if modversions:
            for mv in modversions:
                cur.execute("DELETE FROM build_modversion WHERE modversion_id = %s", (mv["id"],))
        cur.execute("DELETE FROM modversions WHERE mod_id = %s", (id,))
        cur.execute("DELETE FROM mods WHERE id=%s", (id,))
        conn.commit()
        return None

    @classmethod
    def get_by_id(cls, id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM mods WHERE id = %s", (id,))
        row = cur.fetchone()
        if row:
            return cls(row["id"], row["name"], row["description"], row["author"], row["link"], row["created_at"], row["updated_at"], row["pretty_name"], row["side"], row["modtype"], row["note"])
        return None

    @staticmethod
    def get_multi_by_id(ids: tuple):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute(f"SELECT * FROM mods WHERE id IN ({','.join(['%s'] * len(ids))})", ids)
        rows = cur.fetchall()
        if rows:
            return [Mod(row["id"], row["name"], row["description"], row["author"], row["link"], row["created_at"], row["updated_at"], row["pretty_name"], row["side"], row["modtype"], row["note"]) for row in rows]
        return None

    @classmethod
    def get_by_name(cls, name):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM mods WHERE name = %s", (name,))
        row = cur.fetchone()
        if row:
            return cls(row["id"], row["name"], row["description"], row["author"], row["link"], row["created_at"], row["updated_at"], row["pretty_name"], row["side"], row["modtype"], row["note"])
        return None

    @staticmethod
    def get_all():
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM mods ORDER BY id DESC")
        rows = cur.fetchall()
        if rows:
            return [Mod(row["id"], row["name"], row["description"], row["author"], row["link"], row["created_at"], row["updated_at"], row["pretty_name"], row["side"], row["modtype"], row["note"]) for row in rows]
        return []

    @staticmethod
    def get_all_pretty_names():
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id, pretty_name FROM mods ORDER BY name")
        rows = cur.fetchall()
        if rows:
            return rows
        return []

    def get_versions(self):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM modversions WHERE mod_id = %s ORDER BY id DESC", (self.id,))
        rows = cur.fetchall()
        if rows:
            return [Modversion(row["id"], row["mod_id"], row["version"], row["mcversion"], row["md5"], row["created_at"], row["updated_at"], row["filesize"]) for row in rows]
        return []

    def get_versions_by_id(self, id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM modversions WHERE mod_id = %s", (id,))
        row = cur.fetchone()
        if row:
            return Modversion(row["id"], row["mod_id"], row["version"], row["md5"], row["created_at"], row["updated_at"], row["filesize"])
        return None

    def get_version(self, version):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM modversions WHERE mod_id = %s AND version = %s", (self.id, version))
        row = cur.fetchone()
        if row:
            return Modversion(row["id"], row["mod_id"], row["version"], row["mcversion"], row["md5"], row["created_at"], row["updated_at"], row["filesize"])
        return None

    def to_json(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "author": self.author,
            "link": self.link,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "pretty_name": self.pretty_name,
            "side": self.side,
            "type": self.modtype,
            "note": self.note
        }
