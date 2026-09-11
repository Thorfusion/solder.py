"""Build MCInstanceLoader archives from Solder builds."""

from dataclasses import dataclass
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
from urllib.parse import quote
import zipfile

import requests

from .compatibility import normalize_modloader, version_is_compatible
from .database import Database
from .mod import Mod, UploadVerificationError
from .modversion import Modversion


_MD5_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_MAX_PACKAGE_SIZE = 512 * 1024 * 1024
_MAX_EXPANDED_SIZE = 1024 * 1024 * 1024


class MCInstanceExportError(ValueError):
    """Raised when a build cannot be represented safely as an MCInstance."""


class MCInstanceBuildNotFound(MCInstanceExportError):
    """Raised when an export was requested for a missing build."""


class MCInstanceJarError(MCInstanceExportError):
    """Raised when a legacy Solder package cannot provide an MCIL JAR."""


@dataclass(frozen=True)
class MCInstanceBuild:
    id: int
    version: str
    minecraft: str
    forge: str | None
    modpack_id: int
    modpack_name: str
    modpack_slug: str
    modloader: str | None = None


@dataclass(frozen=True)
class MCInstancePackage:
    mod_id: int
    name: str
    pretty_name: str
    description: str
    version: str
    md5: str
    jarmd5: str | None
    side: str
    modtype: str
    optional: bool
    modloader: str | None = None
    minecraft: str | None = None


class MCInstanceJar:
    """Create the raw JAR artifact needed by MCIL from a legacy Solder ZIP."""

    @staticmethod
    def is_ready(jarmd5):
        return bool(_MD5_RE.fullmatch(str(jarmd5 or "").strip()))

    @classmethod
    def create(
        cls,
        mod,
        version,
        repository_url,
        local_repo_root="./mods/",
        r2_client=None,
        r2_bucket=None,
    ):
        if mod is None or version is None or str(version.mod_id) != str(mod.id):
            raise MCInstanceJarError("The selected mod version no longer exists.")
        if str(mod.modtype or "").upper() != "MOD":
            raise MCInstanceJarError("Only MOD packages can be converted to MCIL JARs.")
        if cls.is_ready(version.jarmd5):
            return str(version.jarmd5).strip().lower()

        MCInstanceExport._validate_artifact_component(mod.name, "mod slug")
        MCInstanceExport._validate_artifact_component(version.version, "mod version")
        expected_zip_md5 = str(version.md5 or "").strip().lower()
        if not _MD5_RE.fullmatch(expected_zip_md5):
            raise MCInstanceJarError(
                "This version needs a valid ZIP MD5 before its MCIL JAR can be created. "
                "Rehash the version first."
            )

        root = Path(local_repo_root).resolve()
        destination_folder = (root / mod.name).resolve()
        try:
            destination_folder.relative_to(root)
        except ValueError as error:
            raise MCInstanceJarError("The mod has an invalid repository path.") from error
        destination_folder.mkdir(parents=True, exist_ok=True)

        zip_filename = f"{mod.name}-{version.version}.zip"
        jar_filename = f"{mod.name}-{version.version}.jar"
        source_zip = destination_folder / zip_filename
        final_jar = destination_folder / jar_filename

        try:
            with tempfile.TemporaryDirectory(
                prefix=".solder-mcil-", dir=destination_folder
            ) as staging_directory:
                staged_zip = Path(staging_directory, zip_filename)
                if source_zip.is_file():
                    if source_zip.stat().st_size > _MAX_PACKAGE_SIZE:
                        raise MCInstanceJarError(
                            "The stored ZIP exceeds the 512 MiB conversion limit."
                        )
                    shutil.copyfile(source_zip, staged_zip)
                else:
                    cls._download_package(
                        repository_url, mod.name, zip_filename, staged_zip
                    )

                try:
                    Mod.verify_file_md5(staged_zip, expected_zip_md5, "the stored ZIP")
                    Mod.extract_jar_from_zip(
                        staged_zip,
                        output_name=jar_filename,
                    )
                except UploadVerificationError as error:
                    raise MCInstanceJarError(str(error)) from error

                staged_jar = Path(staging_directory, jar_filename)
                jar_md5 = Mod.file_md5(staged_jar)
                os.replace(staged_jar, final_jar)

            if r2_client is not None and r2_bucket:
                r2_client.upload_file(
                    str(final_jar),
                    r2_bucket,
                    f"mods/{mod.name}/{jar_filename}",
                    ExtraArgs={"ContentType": "application/jar"},
                )

            Modversion.update_modversion_jarmd5(version.id, jar_md5)
            return jar_md5
        except MCInstanceJarError:
            raise
        except OSError as error:
            raise MCInstanceJarError("The MCIL JAR could not be stored.") from error

    @staticmethod
    def _download_package(repository_url, mod_name, filename, destination):
        if not repository_url:
            raise MCInstanceJarError(
                "The ZIP is not stored locally and MD5_REPO_LOCATION is not configured."
            )
        url = MCInstanceExport._artifact_url(repository_url, mod_name, filename)
        try:
            with requests.get(url, stream=True, timeout=(5, 60)) as response:
                response.raise_for_status()
                content_length = int(response.headers.get("content-length", 0))
                if content_length > _MAX_PACKAGE_SIZE:
                    raise MCInstanceJarError(
                        "The stored ZIP exceeds the 512 MiB conversion limit."
                    )
                downloaded = 0
                with open(destination, "wb") as output:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        downloaded += len(chunk)
                        if downloaded > _MAX_PACKAGE_SIZE:
                            raise MCInstanceJarError(
                                "The stored ZIP exceeds the 512 MiB conversion limit."
                            )
                        output.write(chunk)
        except MCInstanceJarError:
            Path(destination).unlink(missing_ok=True)
            raise
        except (OSError, requests.RequestException, ValueError) as error:
            Path(destination).unlink(missing_ok=True)
            raise MCInstanceJarError(
                f'Unable to download the stored package "{filename}".'
            ) from error


class MCInstanceExport:
    """Read a Solder build and render the MCInstanceLoader file format."""

    @classmethod
    def load(cls, build_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT builds.id AS build_id,
                          builds.version AS build_version,
                          builds.minecraft,
                          builds.forge,
                          builds.modloader AS build_modloader,
                          modpacks.id AS modpack_id,
                          modpacks.name AS modpack_name,
                          modpacks.slug AS modpack_slug,
                          mods.id AS mod_id,
                          mods.name AS mod_name,
                          mods.pretty_name,
                          mods.description,
                          mods.side,
                          mods.modtype,
                          modversions.version AS mod_version,
                          modversions.mcversion AS mod_minecraft,
                          modversions.md5,
                          modversions.jarmd5,
                          modversions.modloader,
                          build_modversion.optional
                   FROM builds
                   INNER JOIN modpacks ON builds.modpack_id = modpacks.id
                   LEFT JOIN build_modversion
                       ON build_modversion.build_id = builds.id
                   LEFT JOIN modversions
                       ON build_modversion.modversion_id = modversions.id
                   LEFT JOIN mods ON modversions.mod_id = mods.id
                   WHERE builds.id = %s
                   ORDER BY mods.name ASC, modversions.id ASC""",
                (build_id,),
            )
            rows = cur.fetchall()
        finally:
            cur.close()
            conn.close()

        if not rows:
            raise MCInstanceBuildNotFound(f"Build {build_id} does not exist.")

        first = rows[0]
        build = MCInstanceBuild(
            id=first["build_id"],
            version=str(first["build_version"]),
            minecraft=str(first["minecraft"]),
            forge=first["forge"],
            modpack_id=first["modpack_id"],
            modpack_name=first["modpack_name"],
            modpack_slug=first["modpack_slug"],
            modloader=first.get("build_modloader") or (
                "FORGE" if first["forge"] else None
            ),
        )
        packages = [
            MCInstancePackage(
                mod_id=row["mod_id"],
                name=row["mod_name"],
                pretty_name=row["pretty_name"] or row["mod_name"],
                description=row["description"] or "",
                version=str(row["mod_version"]),
                md5=row["md5"],
                jarmd5=row["jarmd5"],
                side=row["side"] or "BOTH",
                modtype=row["modtype"] or "MOD",
                optional=bool(row["optional"]),
                modloader=row.get("modloader"),
                minecraft=row.get("mod_minecraft"),
            )
            for row in rows
            if row["mod_id"] is not None
        ]
        return build, packages

    @classmethod
    def create(cls, build_id, public_repo_url, local_repo_root="./mods/"):
        build, packages = cls.load(build_id)
        return cls.render(build, packages, public_repo_url, local_repo_root)

    @classmethod
    def render(cls, build, packages, public_repo_url, local_repo_root="./mods/"):
        if not public_repo_url:
            raise MCInstanceExportError(
                "PUBLIC_REPO_LOCATION must be configured before exporting MCInstance files."
            )

        archive_buffer = io.BytesIO()
        resources = []
        optionals = []
        written_paths = {
            "metadata.packconfig",
            "resources.packconfig",
            "optionals.packconfig",
            "overrides/",
            "client-overrides/",
            "server-overrides/",
        }

        with zipfile.ZipFile(
            archive_buffer, "w", compression=zipfile.ZIP_DEFLATED
        ) as target:
            target.writestr("metadata.packconfig", cls._metadata(build))
            for directory in (
                "overrides/",
                "client-overrides/",
                "server-overrides/",
            ):
                target.writestr(directory, b"")

            for package in packages:
                modtype = package.modtype.upper()
                if modtype in {"MCIL", "LAUNCHER"}:
                    continue

                if not version_is_compatible(
                    package.minecraft,
                    package.modloader,
                    build.minecraft,
                    build.modloader,
                ):
                    raise MCInstanceExportError(
                        f'Package "{package.pretty_name}" is not compatible '
                        "with the build's modloader."
                    )

                if package.optional and cls._side(package.side) == "SERVER":
                    raise MCInstanceExportError(
                        f'Optional package "{package.pretty_name}" is server-only. '
                        "MCInstanceLoader 2.7 only presents optional choices on clients."
                    )

                raw_hash = cls._normal_hash(package.jarmd5)
                if modtype == "MOD" and raw_hash:
                    resource_name = f"{package.name}"
                    resources.append(
                        cls._resource(
                            package,
                            resource_name,
                            raw_hash,
                            public_repo_url,
                        )
                    )
                    if package.optional:
                        optionals.append((package, resource_name))
                    continue

                if package.optional:
                    raise MCInstanceExportError(
                        f'Optional package "{package.pretty_name}" needs a verified raw JAR. '
                        "MCInstanceLoader cannot toggle the contents of a bundled Solder ZIP."
                    )

                cls._copy_package(
                    target,
                    package,
                    local_repo_root,
                    public_repo_url,
                    written_paths,
                )

            target.writestr("resources.packconfig", "\n".join(resources))
            target.writestr("optionals.packconfig", cls._optionals(optionals))

        archive_buffer.seek(0)
        return archive_buffer

    @staticmethod
    def _clean(value):
        # MCIL treats # as a comment marker and has no escaping for line breaks.
        return " ".join(str(value or "").replace("#", "-").splitlines()).strip()

    @classmethod
    def _metadata(cls, build):
        modloader = normalize_modloader(build.modloader)
        if modloader is None:
            modloader = "FORGE"
        return "\n".join(
            (
                "[file]",
                "formatVersion = 1",
                "",
                "[modloader]",
                f"type = {modloader.lower()}",
                f"version = {cls._clean(build.forge)}",
                f"minecraftVersion = {cls._clean(build.minecraft)}",
                "",
                "[pack]",
                f"name = {cls._clean(build.modpack_name)}",
                "author = solder.py",
                f"description = Exported from {cls._clean(build.modpack_name)}",
                f"version = {cls._clean(build.version)}",
                "",
            )
        )

    @classmethod
    def _resource(cls, package, resource_name, raw_hash, public_repo_url):
        cls._validate_artifact_component(package.name, "mod slug")
        cls._validate_artifact_component(package.version, "mod version")
        jar_name = f"{package.name}-{package.version}.jar"
        url = cls._artifact_url(public_repo_url, package.name, jar_name)
        side = cls._side(package.side).lower()
        lines = [
            f"[{resource_name}]",
            "type = url",
            f"destination = mods/{jar_name}",
            f"url = {url}",
            f"side = {side}",
            f"optional = {'true' if package.optional else 'false'}",
            f"MD5 = {raw_hash}",
            "",
        ]
        return "\n".join(lines)

    @classmethod
    def _optionals(cls, optionals):
        if not optionals:
            return ""

        lines = [
            "[solder-optionals]",
            "title = Optional mods",
            "minchoices = 0",
            f"maxchoices = {len(optionals)}",
        ]
        for index, (package, resource_name) in enumerate(optionals, 1):
            lines.extend(
                (
                    f"option{index}.name = {cls._clean(package.pretty_name)}",
                    f"option{index}.description = {cls._clean(package.description)}",
                    "option%d.default = false" % index,
                    f"option{index}.resources = {resource_name}",
                )
            )
        lines.append("")
        return "\n".join(lines)

    @classmethod
    def _copy_package(
        cls,
        target,
        package,
        local_repo_root,
        public_repo_url,
        written_paths,
    ):
        cls._validate_artifact_component(package.name, "mod slug")
        cls._validate_artifact_component(package.version, "mod version")
        filename = f"{package.name}-{package.version}.zip"
        package_bytes = cls._package_bytes(
            local_repo_root, public_repo_url, package.name, filename
        )
        expected_hash = cls._normal_hash(package.md5)
        if expected_hash:
            actual_hash = hashlib.md5(package_bytes, usedforsecurity=False).hexdigest()
            if actual_hash != expected_hash:
                raise MCInstanceExportError(
                    f'The stored ZIP for "{package.pretty_name}" does not match its MD5.'
                )

        prefix = {
            "BOTH": "overrides",
            "CLIENT": "client-overrides",
            "SERVER": "server-overrides",
        }[cls._side(package.side)]

        try:
            source = zipfile.ZipFile(io.BytesIO(package_bytes), "r")
        except zipfile.BadZipFile as error:
            raise MCInstanceExportError(
                f'The stored package for "{package.pretty_name}" is not a valid ZIP.'
            ) from error

        expanded_size = 0
        try:
            with source:
                for info in source.infolist():
                    if info.is_dir():
                        continue
                    expanded_size += info.file_size
                    if expanded_size > _MAX_EXPANDED_SIZE:
                        raise MCInstanceExportError(
                            f'The stored package for "{package.pretty_name}" expands beyond the export limit.'
                        )

                    relative = cls._safe_archive_path(
                        info.filename, package.pretty_name
                    )
                    mode = (info.external_attr >> 16) & 0o170000
                    if mode == stat.S_IFLNK:
                        raise MCInstanceExportError(
                            f'The stored package for "{package.pretty_name}" contains a symbolic link.'
                        )
                    destination = f"{prefix}/{relative.as_posix()}"
                    if destination in written_paths:
                        raise MCInstanceExportError(
                            f'Multiple packages export the same path: "{destination}".'
                        )
                    written_paths.add(destination)
                    with source.open(info, "r") as source_file, target.open(
                        destination, "w"
                    ) as destination_file:
                        shutil.copyfileobj(
                            source_file, destination_file, 1024 * 1024
                        )
        except MCInstanceExportError:
            raise
        except (OSError, RuntimeError, zipfile.BadZipFile) as error:
            raise MCInstanceExportError(
                f'The stored package for "{package.pretty_name}" could not be unpacked.'
            ) from error

    @staticmethod
    def _safe_archive_path(name, package_name):
        normalized = str(name).replace("\\", "/")
        path = PurePosixPath(normalized)
        if (
            not normalized
            or normalized.startswith("/")
            or re.match(r"^[A-Za-z]:", normalized)
            or any(part in {"", ".", ".."} for part in path.parts)
            or any(":" in part for part in path.parts)
        ):
            raise MCInstanceExportError(
                f'The stored package for "{package_name}" contains an unsafe path.'
            )
        return path

    @staticmethod
    def _validate_artifact_component(value, label):
        value = str(value)
        if not value or value in {".", ".."} or "/" in value or "\\" in value:
            raise MCInstanceExportError(f"Invalid {label} in the selected build.")

    @staticmethod
    def _normal_hash(value):
        value = str(value or "").strip()
        if value in {"", "0"}:
            return None
        if not _MD5_RE.fullmatch(value):
            raise MCInstanceExportError("A package in the build has an invalid MD5 value.")
        return value.lower()

    @staticmethod
    def _side(value):
        value = str(value or "BOTH").upper()
        if value not in {"BOTH", "CLIENT", "SERVER"}:
            raise MCInstanceExportError(f'Unsupported package side "{value}".')
        return value

    @staticmethod
    def _artifact_url(base_url, mod_name, filename):
        return (
            str(base_url).rstrip("/")
            + "/"
            + quote(mod_name, safe="")
            + "/"
            + quote(filename, safe="")
        )

    @classmethod
    def _package_bytes(cls, local_repo_root, public_repo_url, mod_name, filename):
        root = Path(local_repo_root).resolve()
        local_path = (root / mod_name / filename).resolve()
        try:
            local_path.relative_to(root)
        except ValueError as error:
            raise MCInstanceExportError("Invalid local package path.") from error

        if local_path.is_file():
            if local_path.stat().st_size > _MAX_PACKAGE_SIZE:
                raise MCInstanceExportError("A package exceeds the MCInstance export limit.")
            return local_path.read_bytes()

        url = cls._artifact_url(public_repo_url, mod_name, filename)
        try:
            with requests.get(url, stream=True, timeout=(5, 60)) as response:
                response.raise_for_status()
                content_length = int(response.headers.get("content-length", 0))
                if content_length > _MAX_PACKAGE_SIZE:
                    raise MCInstanceExportError(
                        "A package exceeds the MCInstance export limit."
                    )
                result = io.BytesIO()
                size = 0
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > _MAX_PACKAGE_SIZE:
                        raise MCInstanceExportError(
                            "A package exceeds the MCInstance export limit."
                        )
                    result.write(chunk)
                return result.getvalue()
        except MCInstanceExportError:
            raise
        except (requests.RequestException, ValueError) as error:
            raise MCInstanceExportError(
                f'Unable to read the stored package "{filename}".'
            ) from error
