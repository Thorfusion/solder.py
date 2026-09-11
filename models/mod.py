import datetime
import hashlib
import hmac
from pathlib import Path, PurePosixPath
import re

from mysql.connector import IntegrityError, errorcode

from .database import Database
from .modversion import Modversion
import zipfile


class DuplicateModError(ValueError):
    """Raised when a mod slug is already present in the database."""


class UploadVerificationError(ValueError):
    """Raised when an uploaded Solder package fails server verification."""


_MAX_UPLOAD_JAR_SIZE = 512 * 1024 * 1024


class Mod:
    def __init__(self, id, name, description, author, link, created_at, updated_at, pretty_name, side, modtype, notes):
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
        self.notes = notes

    @classmethod
    def new(cls, name, description, author, link, pretty_name, side, modtype, notes):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        now = datetime.datetime.now()
        try:
            cur.execute("INSERT INTO mods (name, description, author, link, created_at, updated_at, pretty_name, side, modtype, notes) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", (name, description, author, link, now, now, pretty_name, side, modtype, notes))
            conn.commit()
            return cls(cur.lastrowid, name, description, author, link, now, now, pretty_name, side, modtype, notes)
        except IntegrityError as error:
            conn.rollback()
            if error.errno == errorcode.ER_DUP_ENTRY:
                raise DuplicateModError(name) from error
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def update(id, name, description, author, link, pretty_name, side, modtype, notes):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        now = datetime.datetime.now()
        cur.execute("""UPDATE mods 
            SET name = %s, description = %s, author = %s, link = %s, updated_at = %s, pretty_name = %s, side = %s, modtype = %s, notes = %s
            WHERE id = %s;""", (name, description, author, link, now, pretty_name, side, modtype, notes, id))
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
        cur.execute(
            "DELETE FROM mod_dependencies WHERE mod_id = %s OR dependency_mod_id = %s",
            (id, id),
        )
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
            return cls(row["id"], row["name"], row["description"], row["author"], row["link"], row["created_at"], row["updated_at"], row["pretty_name"], row["side"], row["modtype"], row.get("notes", row.get("note")))
        return None

    @classmethod
    def get_by_name_api(cls, name):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT * FROM mods WHERE name = %s", (name,))
            row = cur.fetchone()
            if row:
                return cls(row["id"], row["name"], row["description"], row["author"], row["link"], row["created_at"], row["updated_at"], row["pretty_name"], row["side"], row["modtype"], row.get("notes", row.get("note")))
            return None
        finally:
            cur.close()
            conn.close()

    @classmethod
    def get_all_api(cls):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT * FROM mods ORDER BY id ASC")
            return [
                cls(
                    row["id"],
                    row["name"],
                    row["description"],
                    row["author"],
                    row["link"],
                    row["created_at"],
                    row["updated_at"],
                    row["pretty_name"],
                    row["side"],
                    row["modtype"],
                    row.get("notes", row.get("note")),
                )
                for row in cur.fetchall()
            ]
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def get_all():
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM mods ORDER BY id DESC")
        rows = cur.fetchall()
        if rows:
            return [Mod(row["id"], row["name"], row["description"], row["author"], row["link"], row["created_at"], row["updated_at"], row["pretty_name"], row["side"], row["modtype"], row.get("notes", row.get("note"))) for row in rows]
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
        cur.execute("SELECT id, mod_id, version, mcversion, modloader, md5, filesize FROM modversions WHERE mod_id = %s ORDER BY id DESC", (self.id,))
        rows = cur.fetchall()
        if rows:
            return rows
        return []
    
    def get_versions_api(self) -> list:
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT version FROM modversions WHERE mod_id = %s ORDER BY id ASC", (self.id,))
            return cur.fetchall()
        finally:
            cur.close()
            conn.close()

    def get_version_api(self, version):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT * FROM modversions WHERE mod_id = %s AND version = %s", (self.id, version))
            row = cur.fetchone()
            if row:
                return Modversion(row["id"], row["mod_id"], row["version"], row["mcversion"], row["md5"], row["created_at"], row["updated_at"], row["filesize"], modloader=row.get("modloader"))
            return None
        finally:
            cur.close()
            conn.close()
    
    @staticmethod
    def file_md5(path):
        """Calculate the MD5 used by Solder manifests (not a security digest)."""
        digest = hashlib.md5(usedforsecurity=False)
        with open(path, "rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def verify_file_md5(path, expected_md5, label="uploaded file"):
        expected_md5 = str(expected_md5 or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{32}", expected_md5):
            raise UploadVerificationError(f"The client supplied an invalid MD5 for {label}.")
        actual_md5 = Mod.file_md5(path)
        if not hmac.compare_digest(actual_md5, expected_md5):
            raise UploadVerificationError(f"The MD5 verification failed for {label}.")
        return actual_md5

    @staticmethod
    def extract_jar_from_zip(zip_paths, output_name=None, expected_md5=None):
        """Extract the single mods/*.jar entry and optionally verify its MD5."""
        base_dir = Path(zip_paths).parent
        try:
            with zipfile.ZipFile(zip_paths, "r") as zip_ref:
                jar_files = []
                for info in zip_ref.infolist():
                    normalized = info.filename.replace("\\", "/")
                    path = PurePosixPath(normalized)
                    if (
                        not info.is_dir()
                        and len(path.parts) >= 2
                        and path.parts[0] == "mods"
                        and path.suffix.lower() == ".jar"
                        and ".." not in path.parts
                    ):
                        jar_files.append(info)

                if len(jar_files) != 1:
                    raise UploadVerificationError(
                        "A JAR upload must contain exactly one JAR inside the mods folder."
                    )
                if jar_files[0].file_size > _MAX_UPLOAD_JAR_SIZE:
                    raise UploadVerificationError(
                        "The raw JAR exceeds the 512 MiB upload limit."
                    )

                jar_name = output_name or PurePosixPath(jar_files[0].filename).name
                if Path(jar_name).name != jar_name or not jar_name.lower().endswith(".jar"):
                    raise UploadVerificationError("The output JAR filename is invalid.")

                output_path = base_dir / jar_name
                try:
                    with zip_ref.open(jar_files[0], "r") as source, open(
                        output_path, "wb"
                    ) as target:
                        extracted_size = 0
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            extracted_size += len(chunk)
                            if extracted_size > _MAX_UPLOAD_JAR_SIZE:
                                raise UploadVerificationError(
                                    "The raw JAR exceeds the 512 MiB upload limit."
                                )
                            target.write(chunk)
                    if expected_md5 is not None:
                        Mod.verify_file_md5(output_path, expected_md5, "the raw JAR")
                except Exception:
                    output_path.unlink(missing_ok=True)
                    raise
                return jar_name
        except UploadVerificationError:
            raise
        except (OSError, RuntimeError, zipfile.BadZipFile) as error:
            raise UploadVerificationError("The uploaded package is not a valid ZIP file.") from error


    def to_json(self):
        return {
            "name": self.name,
            "pretty_name": self.pretty_name,
            "author": self.author,
            "description": self.description,
            "link": self.link,
            "side": self.side,
            "type": self.modtype,
            "modtype": self.modtype,
        }
