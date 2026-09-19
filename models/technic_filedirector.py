"""FileDirector bootstrap packages exposed through the Technic read API."""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import quote
import zipfile

from .compatibility import normalize_modloader
from .database import Database
from .distribution import DistributionExport, FileDirectorExport
from .integration import IntegrationError, ModrinthProvider


FILEDIRECTOR_MODRINTH_PROJECT = "4dRu1OUz"
_EXTERNAL_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class TechnicFileDirectorError(ValueError):
    """Raised when a Technic FileDirector bootstrap cannot be configured."""


@dataclass(frozen=True)
class TechnicFileDirector:
    build_id: int
    version_id: str
    version: str
    bootstrap_path: str
    bootstrap_md5: str
    bootstrap_filesize: int
    config_path: str
    config_md5: str
    config_filesize: int

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
    def _temporary_path(directory, suffix):
        descriptor, name = tempfile.mkstemp(
            prefix=".solderpy-filedirector-", suffix=suffix, dir=directory
        )
        os.close(descriptor)
        return Path(name)

    @staticmethod
    def _write_bytes(archive, name, content):
        info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        archive.writestr(info, content)

    @classmethod
    def _write_archives(cls, directory, version, remote_url):
        downloaded_jar = cls._temporary_path(directory, ".jar")
        bootstrap_temp = cls._temporary_path(directory, ".zip")
        config_temp = cls._temporary_path(directory, ".zip")
        provider = ModrinthProvider()
        try:
            provider.download(version, downloaded_jar)
            # A fixed extracted name updates cleanly on launchers predating
            # Technic's extractedFiles.json orphan cleanup.
            os.utime(downloaded_jar, (315532800, 315532800))
            with zipfile.ZipFile(
                bootstrap_temp, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                archive.write(
                    downloaded_jar,
                    "mods/!solderpy-filedirector.jar",
                )
            with zipfile.ZipFile(
                config_temp, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                cls._write_bytes(
                    archive,
                    "config/mod-director/solderpy.remote.json",
                    FileDirectorExport.remote(remote_url),
                )
            return bootstrap_temp, config_temp
        except IntegrationError as error:
            bootstrap_temp.unlink(missing_ok=True)
            config_temp.unlink(missing_ok=True)
            raise TechnicFileDirectorError(str(error)) from error
        except Exception:
            bootstrap_temp.unlink(missing_ok=True)
            config_temp.unlink(missing_ok=True)
            raise
        finally:
            downloaded_jar.unlink(missing_ok=True)

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
        version,
        repository_root,
        repository_url,
        application_url,
        *,
        r2_client=None,
        r2_bucket=None,
    ):
        """Materialize and remember the two virtual Solder packages."""
        if normalize_modloader(getattr(build, "modloader", None)) != "FORGE":
            raise TechnicFileDirectorError(
                "Technic FileDirector delivery currently requires Forge."
            )
        if int(getattr(modpack, "optional_mode", 0) or 0) != 1:
            raise TechnicFileDirectorError(
                "Enable advanced optionals before enabling FileDirector for Technic."
            )
        if (
            not bool(getattr(build, "is_published", False))
            or bool(getattr(build, "private", False))
            or bool(getattr(modpack, "hidden", False))
            or bool(getattr(modpack, "private", False))
        ):
            raise TechnicFileDirectorError(
                "Technic FileDirector requires a published, public build and modpack."
            )
        version_id = str(getattr(version, "version_id", "") or "")
        version_name = str(getattr(version, "version_number", "") or "")
        if (
            getattr(version, "project_id", None) != FILEDIRECTOR_MODRINTH_PROJECT
            or _EXTERNAL_ID.fullmatch(version_id) is None
            or not version_name
            or len(version_name) > 255
        ):
            raise TechnicFileDirectorError(
                "Modrinth returned an invalid FileDirector version."
            )

        DistributionExport.repository_base(repository_url)
        application_base = DistributionExport.application_base(application_url)
        slug = str(getattr(modpack, "slug", "") or "")
        build_version = str(getattr(build, "version", "") or "")
        DistributionExport._validate_slug(slug, "modpack slug")
        DistributionExport._validate_component(build_version, "build version")
        remote_url = (
            f"{application_base}/filedirector/"
            f"{quote(slug, safe='-._~')}/"
            f"{quote(build_version, safe='-._~')}/technic.bundle.json"
        )

        root = Path(repository_root).resolve()
        directory = (root / "_solderpy" / "filedirector" / str(build.id)).resolve()
        if root not in directory.parents:
            raise TechnicFileDirectorError("The repository path is invalid.")
        directory.mkdir(parents=True, exist_ok=True)

        bootstrap_temp = config_temp = None
        try:
            bootstrap_temp, config_temp = cls._write_archives(
                directory, version, remote_url
            )
            bootstrap_md5, bootstrap_size = cls._checksum(bootstrap_temp)
            config_md5, config_size = cls._checksum(config_temp)
            bootstrap_relative = (
                f"_solderpy/filedirector/{build.id}/"
                f"bootstrap-{bootstrap_md5}.zip"
            )
            config_relative = (
                f"_solderpy/filedirector/{build.id}/config-{config_md5}.zip"
            )
            bootstrap_path = root / Path(bootstrap_relative)
            config_path = root / Path(config_relative)
            os.replace(bootstrap_temp, bootstrap_path)
            bootstrap_temp = None
            os.replace(config_temp, config_path)
            config_temp = None

            if r2_client is not None and r2_bucket:
                r2_client.upload_file(
                    str(bootstrap_path),
                    r2_bucket,
                    bootstrap_relative,
                    ExtraArgs={"ContentType": "application/zip"},
                )
                r2_client.upload_file(
                    str(config_path),
                    r2_bucket,
                    config_relative,
                    ExtraArgs={"ContentType": "application/zip"},
                )

            conn = Database.get_connection()
            if conn is None:
                raise TechnicFileDirectorError(
                    "Could not save the Technic FileDirector configuration."
                )
            cursor = conn.cursor()
            try:
                cursor.execute(
                    """INSERT INTO technic_filedirector_builds
                       (build_id, version_id, version,
                        bootstrap_path, bootstrap_md5, bootstrap_filesize,
                        config_path, config_md5, config_filesize)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON DUPLICATE KEY UPDATE
                           version_id = VALUES(version_id),
                           version = VALUES(version),
                           bootstrap_path = VALUES(bootstrap_path),
                           bootstrap_md5 = VALUES(bootstrap_md5),
                           bootstrap_filesize = VALUES(bootstrap_filesize),
                           config_path = VALUES(config_path),
                           config_md5 = VALUES(config_md5),
                           config_filesize = VALUES(config_filesize)""",
                    (
                        build.id,
                        version_id,
                        version_name,
                        bootstrap_relative,
                        bootstrap_md5,
                        bootstrap_size,
                        config_relative,
                        config_md5,
                        config_size,
                    ),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                cursor.close()
                conn.close()
        finally:
            if bootstrap_temp is not None:
                bootstrap_temp.unlink(missing_ok=True)
            if config_temp is not None:
                config_temp.unlink(missing_ok=True)

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
            "config_path",
            "config_md5",
            "config_filesize",
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
                "SELECT * FROM technic_filedirector_builds WHERE build_id = %s",
                (build_id,),
            )
            return cls._from_row(cursor.fetchone())
        finally:
            cursor.close()
            conn.close()

    @classmethod
    def get_active(cls, build_id):
        """Return configuration only when public advanced delivery is usable."""
        conn = Database.get_connection()
        if conn is None:
            return None
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                """SELECT technic_filedirector_builds.*
                   FROM technic_filedirector_builds
                   INNER JOIN builds
                       ON builds.id = technic_filedirector_builds.build_id
                   INNER JOIN modpacks ON modpacks.id = builds.modpack_id
                   WHERE technic_filedirector_builds.build_id = %s
                     AND modpacks.optional_mode = 1
                     AND builds.is_published = 1
                     AND builds.private = 0
                     AND modpacks.hidden = 0
                     AND modpacks.private = 0
                     AND EXISTS (
                         SELECT 1 FROM solder_settings
                         WHERE name = 'filedirector_enabled'
                           AND LOWER(value) IN ('1', 'true', 'yes', 'on')
                     )
                     AND EXISTS (
                         SELECT 1
                         FROM build_optional_groups
                         INNER JOIN build_optional_group_items
                           ON build_optional_group_items.group_id =
                              build_optional_groups.id
                         WHERE build_optional_groups.build_id =
                               technic_filedirector_builds.build_id
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
            raise TechnicFileDirectorError(
                "Could not disable the Technic FileDirector configuration."
            )
        cursor = conn.cursor()
        try:
            cursor.execute(
                "DELETE FROM technic_filedirector_builds WHERE build_id = %s",
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
            "DELETE FROM technic_filedirector_builds WHERE build_id = %s",
            (build_id,),
        )

    def manifest_entries(self, repository_url, *, expanded=False, extended=False):
        entries = [
            {
                "id": -(int(self.build_id) * 2),
                "name": "solderpy-filedirector",
                "version": self.version,
                "md5": self.bootstrap_md5,
                "filesize": int(self.bootstrap_filesize),
                "url": self._artifact_url(repository_url, self.bootstrap_path),
            },
            {
                "id": -(int(self.build_id) * 2 + 1),
                "name": "solderpy-filedirector-config",
                "version": f"{self.build_id}-{self.config_md5[:12]}",
                "md5": self.config_md5,
                "filesize": int(self.config_filesize),
                "url": self._artifact_url(repository_url, self.config_path),
            },
        ]
        if expanded:
            entries[0].update(
                {
                    "pretty_name": "FileDirector",
                    "author": "FileDirector",
                    "description": "Advanced optional download bootstrap.",
                    "link": "https://github.com/TerraFirmaCraft-The-Final-Frontier/FileDirector",
                }
            )
            entries[1].update(
                {
                    "pretty_name": "FileDirector configuration",
                    "author": "solder.py",
                    "description": "Versioned advanced optional configuration.",
                    "link": None,
                }
            )
        if expanded or extended:
            for entry, modtype in zip(entries, ("MOD", "CONFIG")):
                entry.update(
                    {
                        "side": "CLIENT",
                        "type": modtype,
                        "modtype": modtype,
                        "modloader": None,
                        "optional": False,
                        "dependencies": [],
                    }
                )
        return entries
