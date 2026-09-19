"""SolderPy Loader bootstrap package exposed through the Technic read API."""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shutil
import tempfile
from urllib.parse import quote

from .database import Database
from .distribution import DistributionExport
from .platform_export import PlatformExportError, PlatformPackExport


class TechnicSolderPyLoaderError(ValueError):
    """Raised when Technic bootstrap delivery cannot be configured."""


@dataclass(frozen=True)
class TechnicSolderPyLoader:
    build_id: int
    version_id: str
    version: str
    bootstrap_path: str
    bootstrap_md5: str
    bootstrap_filesize: int

    @staticmethod
    def _checksum(path):
        digest = hashlib.md5(usedforsecurity=False)
        size = 0
        with Path(path).open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                size += len(chunk)
                digest.update(chunk)
        return digest.hexdigest(), size

    @staticmethod
    def _temporary_path(directory):
        descriptor, name = tempfile.mkstemp(
            prefix=".solderpy-loader-", suffix=".zip", dir=directory
        )
        os.close(descriptor)
        return Path(name)

    @classmethod
    def _write_archive(
        cls,
        directory,
        selected,
        config,
        *,
        relauncher_config=None,
        http=None,
    ):
        archive_path = cls._temporary_path(directory)
        try:
            rendered = PlatformPackExport.render_solderpy_loader_archive(
                selected,
                config,
                relauncher_config=relauncher_config,
                http=http,
            )
            with rendered, archive_path.open("wb") as destination:
                shutil.copyfileobj(
                    rendered, destination, length=1024 * 1024
                )
            return archive_path
        except Exception:
            archive_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _artifact_url(repository_url, relative_path):
        base = DistributionExport.repository_base(repository_url)
        return base + "/" + "/".join(
            quote(component, safe="-._~")
            for component in Path(relative_path).as_posix().split("/")
        )

    @classmethod
    def configure(
        cls,
        build,
        modpack,
        selected,
        repository_root,
        repository_url,
        application_url,
        *,
        r2_client=None,
        r2_bucket=None,
        http=None,
    ):
        """Materialize and remember one self-contained Technic bootstrap ZIP."""
        if getattr(selected, "key", None) != "solderpyloader":
            raise TechnicSolderPyLoaderError(
                "Select a SolderPy Loader release for Technic delivery."
            )
        if (
            not bool(getattr(build, "is_published", False))
            or bool(getattr(build, "private", False))
            or bool(getattr(modpack, "hidden", False))
            or bool(getattr(modpack, "private", False))
        ):
            raise TechnicSolderPyLoaderError(
                "SolderPy Loader requires a published, public build and modpack."
            )

        version_id = str(getattr(selected.release, "selector", "") or "")
        version_name = str(getattr(selected.release, "version", "") or "")
        if not version_id or len(version_id) > 64 or not version_name:
            raise TechnicSolderPyLoaderError(
                "Modrinth returned an invalid SolderPy Loader version."
            )
        if len(version_name) > 255:
            raise TechnicSolderPyLoaderError(
                "The SolderPy Loader version name is too long."
            )

        DistributionExport.repository_base(repository_url)
        config = PlatformPackExport.solderpy_loader_config(
            build,
            application_url,
            selector="build",
            modpack_slug=getattr(modpack, "slug", None),
        )
        relauncher_config = PlatformPackExport.relauncher_java_config(build)

        root = Path(repository_root).resolve()
        directory = (
            root / "_solderpy" / "solderpy-loader" / str(build.id)
        ).resolve()
        if root not in directory.parents:
            raise TechnicSolderPyLoaderError("The repository path is invalid.")
        directory.mkdir(parents=True, exist_ok=True)

        temporary = None
        try:
            temporary = cls._write_archive(
                directory,
                selected,
                config,
                relauncher_config=relauncher_config,
                http=http,
            )
            bootstrap_md5, bootstrap_size = cls._checksum(temporary)
            relative = (
                f"_solderpy/solderpy-loader/{build.id}/"
                f"bootstrap-{bootstrap_md5}.zip"
            )
            destination = root / Path(relative)
            os.replace(temporary, destination)
            temporary = None

            if r2_client is not None and r2_bucket:
                r2_client.upload_file(
                    str(destination),
                    r2_bucket,
                    relative,
                    ExtraArgs={"ContentType": "application/zip"},
                )

            conn = Database.get_connection()
            if conn is None:
                raise TechnicSolderPyLoaderError(
                    "Could not save the Technic SolderPy Loader configuration."
                )
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """INSERT INTO technic_solderpy_loader_builds
                       (build_id, version_id, version, bootstrap_path,
                        bootstrap_md5, bootstrap_filesize)
                       VALUES (%s, %s, %s, %s, %s, %s)
                       ON DUPLICATE KEY UPDATE
                           version_id = VALUES(version_id),
                           version = VALUES(version),
                           bootstrap_path = VALUES(bootstrap_path),
                           bootstrap_md5 = VALUES(bootstrap_md5),
                           bootstrap_filesize = VALUES(bootstrap_filesize)""",
                    (
                        build.id,
                        version_id,
                        version_name,
                        relative,
                        bootstrap_md5,
                        bootstrap_size,
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()
                conn.close()
        except PlatformExportError as error:
            raise TechnicSolderPyLoaderError(str(error)) from error
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

        return cls.get(build.id)

    @classmethod
    def _from_row(cls, row):
        if not row:
            return None
        allowed = {
            "build_id",
            "version_id",
            "version",
            "bootstrap_path",
            "bootstrap_md5",
            "bootstrap_filesize",
        }
        return cls(**{name: row[name] for name in allowed})

    @classmethod
    def get(cls, build_id):
        conn = Database.get_connection()
        if conn is None:
            return None
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                "SELECT * FROM technic_solderpy_loader_builds WHERE build_id = %s",
                (build_id,),
            )
            return cls._from_row(cursor.fetchone())
        finally:
            cursor.close()
            conn.close()

    @classmethod
    def get_active(cls, build_id):
        """Return configuration only while public loader delivery is enabled."""
        conn = Database.get_connection()
        if conn is None:
            return None
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                """SELECT technic_solderpy_loader_builds.*
                   FROM technic_solderpy_loader_builds
                   INNER JOIN builds
                       ON builds.id = technic_solderpy_loader_builds.build_id
                   INNER JOIN modpacks ON modpacks.id = builds.modpack_id
                   WHERE technic_solderpy_loader_builds.build_id = %s
                     AND builds.is_published = 1
                     AND builds.private = 0
                     AND modpacks.hidden = 0
                     AND modpacks.private = 0
                     AND EXISTS (
                         SELECT 1 FROM solder_settings
                         WHERE name = 'solderpy_loader_enabled'
                           AND LOWER(value) IN ('1', 'true', 'yes', 'on')
                     )""",
                (build_id,),
            )
            return cls._from_row(cursor.fetchone())
        finally:
            cursor.close()
            conn.close()

    @classmethod
    def disable(cls, build_id):
        conn = Database.get_connection()
        if conn is None:
            raise TechnicSolderPyLoaderError(
                "Could not disable Technic SolderPy Loader delivery."
            )
        cursor = conn.cursor()
        try:
            cursor.execute(
                "DELETE FROM technic_solderpy_loader_builds WHERE build_id = %s",
                (build_id,),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def delete_build(cursor, build_id):
        cursor.execute(
            "DELETE FROM technic_solderpy_loader_builds WHERE build_id = %s",
            (build_id,),
        )

    def manifest_entries(self, repository_url, *, expanded=False, extended=False):
        entry = {
            "id": -int(self.build_id),
            "name": "solderpy-loader-bootstrap",
            "version": self.version,
            "md5": self.bootstrap_md5,
            "filesize": int(self.bootstrap_filesize),
            "url": self._artifact_url(repository_url, self.bootstrap_path),
        }
        if expanded:
            entry.update(
                {
                    "pretty_name": "SolderPy Loader bootstrap",
                    "author": "Thorfusion",
                    "description": (
                        "Installs SolderPy Loader, Relauncher, and this build's "
                        "bootstrap API configuration."
                    ),
                    "link": "https://github.com/Thorfusion/solderpy_loader",
                }
            )
        if expanded or extended:
            entry.update(
                {
                    "side": "BOTH",
                    "type": "BOOTSTRAP",
                    "modtype": "BOOTSTRAP",
                    "modloader": None,
                    "optional": False,
                    "dependencies": [],
                }
            )
        return [entry]
