import datetime
import hashlib
import threading

import requests

from .database import Database


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
    def new(cls, mod_id, version, mcversion, md5, filesize, markedbuild, url="0", jarmd5="0"):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        now = datetime.datetime.now()
        cur.execute("INSERT INTO modversions (mod_id, version, mcversion, md5, created_at, updated_at, filesize) VALUES (%s, %s, %s, %s, %s, %s, %s)", (mod_id, version, mcversion, md5, now, now, filesize))
        conn.commit()
        cur.execute("SELECT LAST_INSERT_ID() AS id")
        id = cur.fetchone()["id"]
        conn.commit()
        if markedbuild == "1":
            Modversion.add_modversion_to_selected_build(id, mod_id, "0", "1", "0")
        if md5 == "0":
            version = Modversion.get_by_id(id)
            t = threading.Thread(target=version.rehash, args=(url,))
            t.start()
        if jarmd5 != "0":
            Modversion.update_modversion_jarmd5(id, jarmd5)
        return cls(id, mod_id, version, mcversion, md5, now, now, filesize)

    @staticmethod
    def add_modversion_to_selected_build(modver_id, mod_id, build_id, marked, optional):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        if marked == "1":
            cur.execute("SELECT id FROM builds WHERE marked = 1")
            build_id = cur.fetchone()["id"]
            conn.commit()
        # when new modversion is added to build, old modversion gets deleted, quite tricky as both values are unique each time and you need to get all modversion and delete them on said build.
        cur.execute(
            """SELECT build_modversion.id 
                FROM build_modversion 
                INNER JOIN modversions ON build_modversion.modversion_id = modversions.id 
                WHERE build_id = %s AND modversions.mod_id = %s
            """, (build_id, mod_id))
        try:
            build_modid = cur.fetchone()["id"]
        except:
            build_modid = None
        if build_modid is not None:
            cur.execute("UPDATE build_modversion SET modversion_id = %s WHERE id = %s", (modver_id, build_modid))
            conn.commit()
            return None
        cur.execute("SELECT * FROM modversions WHERE mod_id = %s", (mod_id,))
        modversions = cur.fetchall()
        if modversions:
            for mv in modversions:
                cur.execute("DELETE FROM build_modversion WHERE modversion_id = %s AND build_id = %s", (mv["id"], build_id))
        cur.execute("INSERT INTO build_modversion (modversion_id, build_id, optional) VALUES (%s, %s, %s)", (modver_id, build_id, optional))
        conn.commit()
        return None

    @staticmethod
    def update_modversion_in_build(oldmodver_id, modver_id, build_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("UPDATE build_modversion SET modversion_id = %s WHERE modversion_id = %s AND build_id = %s", (modver_id, oldmodver_id, build_id))
        conn.commit()
        return None

    @staticmethod
    def update_modversion_jarmd5(id, jarmd5):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("UPDATE modversions SET jarmd5 = %s WHERE id = %s", (jarmd5, id))
        conn.commit()
        return None

    @staticmethod
    def delete_modversion(id):
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
        cur.execute("SELECT id, mod_id, version, mcversion FROM modversions")
        rows = cur.fetchall()
        if rows:
            return rows
        return []

    def get_file_size(url):
        response = requests.head(url)  # Only get headers, not content
        file_size = int(response.headers.get('content-length', -1))  # Get file size from headers

        return file_size
        # https://www.classace.io/answers/56cb76718f9932eba6153a625885309b

    def update_hash(self, md5, filesize_url):
        conn = Database.get_connection()
        cur = conn.cursor()
        file_size = Modversion.get_file_size(filesize_url)
        if file_size != -1:
            cur.execute("UPDATE modversions SET filesize = %s WHERE id = %s", (file_size, self.id))
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
            self.update_hash(h.hexdigest(), rehash_url)

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
