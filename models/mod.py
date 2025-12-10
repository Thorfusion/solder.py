import datetime

from flask import flash

from .database import Database
from .modversion import Modversion
import zipfile
import os


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

    @classmethod
    def get_by_name_api(cls, name):
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
        cur.execute("SELECT id, mod_id, version, mcversion, md5, filesize FROM modversions WHERE mod_id = %s ORDER BY id DESC", (self.id,))
        rows = cur.fetchall()
        if rows:
            return rows
        return []
    
    def get_versions_api(self) -> list:
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT version FROM modversions WHERE mod_id = %s ORDER BY id DESC", (self.id,))
        rows = cur.fetchall()
        if rows:
            return rows
        return []

    def get_version_api(self, version):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM modversions WHERE mod_id = %s AND version = %s", (self.id, version))
        row = cur.fetchone()
        if row:
            return Modversion(row["id"], row["mod_id"], row["version"], row["mcversion"], row["md5"], row["created_at"], row["updated_at"], row["filesize"])
        return None
    
    def extract_jar_from_zip(zip_paths):
        # Get folder where the zip file is located
        base_dir = os.path.dirname(zip_paths)

        with zipfile.ZipFile(zip_paths, 'r') as zip_ref:
            # Find jar file inside mods/ folder
            jar_files = [f for f in zip_ref.namelist() if f.startswith("mods/") and f.endswith(".jar")]

            if not jar_files:
                flash("failed to extract jarfile", "error")

            jar_inside_zip = jar_files[0]  # Take the first match
            jar_name = os.path.basename(jar_inside_zip)

            # Full path where the JAR will be extracted
            output_path = os.path.join(base_dir, jar_name)

            # Extract the jar file only
            with zip_ref.open(jar_inside_zip) as source, open(output_path, 'wb') as target:
                target.write(source.read())

            return output_path


    def to_json(self):
        return {
            "name": self.name,
            "pretty_name": self.pretty_name,
            "author": self.author,
            "description": self.description,
            "link": self.link,
            "side": self.side,
            "type": self.modtype,
        }
