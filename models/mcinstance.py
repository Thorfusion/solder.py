"""Build MCInstanceLoader archives from Solder builds."""

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
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
_SPOOL_MEMORY_LIMIT = 8 * 1024 * 1024


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
    is_published: bool = False
    private: bool = False

    @property
    def pack_name(self):
        return self.modpack_name

    @property
    def pack_slug(self):
        return self.modpack_slug

    @property
    def modloader_version(self):
        return self.forge


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
    optional_state: int = 0
    membership_id: int | None = None
    modloader: str | None = None
    minecraft: str | None = None
    integration_provider: str | None = None
    integration_project_id: str | None = None
    integration_version_id: str | None = None

    @property
    def mod_slug(self):
        return self.name

    @property
    def zip_md5(self):
        return self.md5

    @property
    def jar_md5(self):
        return self.jarmd5

    @property
    def jar_ready(self):
        return bool(
            str(self.modtype or "").upper() == "MOD"
            and _MD5_RE.fullmatch(str(self.jarmd5 or "").strip())
        )

    @property
    def jar_filename(self):
        return f"{self.name}-{self.version}.jar"

    @property
    def zip_filename(self):
        return f"{self.name}-{self.version}.zip"

    @property
    def is_native_modrinth(self) -> bool:
        return bool(
            str(self.modtype or "").upper() == "MOD"
            and str(self.integration_provider or "").upper() == "MODRINTH"
            and self.integration_project_id
            and self.integration_version_id
        )


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
    def _download_package(repository_location, mod_name, filename, destination):
        if not repository_location:
            raise MCInstanceJarError(
                "The ZIP is not stored locally and MD5_REPO_LOCATION is not configured."
            )
        try:
            source = Modversion.repository_artifact_source(
                repository_location, mod_name, filename
            )
            if isinstance(source, Path):
                if source.stat().st_size > _MAX_PACKAGE_SIZE:
                    raise MCInstanceJarError(
                        "The stored ZIP exceeds the 512 MiB conversion limit."
                    )
                shutil.copyfile(source, destination)
                return

            with requests.get(
                source,
                stream=True,
                allow_redirects=False,
                timeout=(5, 60),
            ) as response:
                if 300 <= response.status_code < 400:
                    raise requests.RequestException(
                        "Repository redirects are not allowed."
                    )
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
                          builds.is_published,
                          builds.private,
                          modpacks.id AS modpack_id,
                          modpacks.name AS modpack_name,
                          modpacks.slug AS modpack_slug,
                          mods.id AS mod_id,
                          mods.name AS mod_name,
                          mods.pretty_name,
                          mods.description,
                          mods.side,
                          mods.modtype,
                          mods.integration_provider,
                          mods.integration_project_id,
                          build_modversion.id AS membership_id,
                          modversions.version AS mod_version,
                          modversions.mcversion AS mod_minecraft,
                          modversions.md5,
                          modversions.jarmd5,
                           modversions.modloader,
                           modversions.integration_version_id,
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
            is_published=bool(first.get("is_published")),
            private=bool(first.get("private")),
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
                optional=int(row["optional"] or 0) == 1,
                optional_state=int(row["optional"] or 0),
                membership_id=row.get("membership_id"),
                modloader=row.get("modloader"),
                minecraft=row.get("mod_minecraft"),
                integration_provider=row.get("integration_provider"),
                integration_project_id=row.get("integration_project_id"),
                integration_version_id=row.get("integration_version_id"),
            )
            for row in rows
            if row["mod_id"] is not None
        ]
        return build, packages

    @classmethod
    def create(cls, build_id, public_repo_url, local_repo_root="./mods/"):
        build, packages = cls.load(build_id)
        from .advanced_optional import AdvancedOptional

        return cls.render(
            build,
            packages,
            public_repo_url,
            local_repo_root,
            optional_groups=AdvancedOptional.get_active_groups(build_id),
        )

    @classmethod
    def render(
        cls,
        build,
        packages,
        public_repo_url,
        local_repo_root="./mods/",
        *,
        include_modloader=True,
        native_files=None,
        optional_groups=(),
    ):
        if not public_repo_url:
            raise MCInstanceExportError(
                "PUBLIC_REPO_LOCATION must be configured before exporting MCInstance files."
            )

        native_files = native_files or {}
        grouped_items = {}
        for group in optional_groups or ():
            for item in group.items:
                grouped_items[item.build_modversion_id] = (group, item)
        archive_buffer = tempfile.SpooledTemporaryFile(
            max_size=_SPOOL_MEMORY_LIMIT,
            mode="w+b",
        )
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

        try:
            with zipfile.ZipFile(
                archive_buffer, "w", compression=zipfile.ZIP_DEFLATED
            ) as target:
                target.writestr(
                    "metadata.packconfig",
                    cls._metadata(build, include_modloader=include_modloader),
                )
                for directory in (
                    "overrides/",
                    "client-overrides/",
                    "server-overrides/",
                ):
                    target.writestr(directory, b"")

                for package in packages:
                    grouped = grouped_items.get(
                        getattr(package, "membership_id", None)
                    )
                    optional_choice = package.optional or grouped is not None
                    if (
                        int(getattr(package, "optional_state", int(package.optional)))
                        == 2
                        and grouped is None
                    ):
                        continue
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

                    if optional_choice and cls._side(package.side) == "SERVER":
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
                                native_files.get(package.integration_version_id),
                                optional=optional_choice,
                            )
                        )
                        if optional_choice:
                            optionals.append((package, resource_name, grouped))
                        continue

                    if optional_choice:
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
                target.writestr(
                    "optionals.packconfig",
                    cls._optionals(optionals, optional_groups),
                )
        except Exception:
            archive_buffer.close()
            raise

        archive_buffer.seek(0)
        return archive_buffer

    @staticmethod
    def _clean(value):
        # MCIL treats # as a comment marker and has no escaping for line breaks.
        return " ".join(str(value or "").replace("#", "-").splitlines()).strip()

    @classmethod
    def _metadata(cls, build, include_modloader=True):
        modloader = normalize_modloader(build.modloader)
        if modloader is None:
            modloader = "FORGE"
        lines = ["[file]", "formatVersion = 1", ""]
        if include_modloader:
            lines.extend(
                (
                    "[modloader]",
                    f"type = {modloader.lower()}",
                    f"version = {cls._clean(build.forge)}",
                    f"minecraftVersion = {cls._clean(build.minecraft)}",
                    "",
                )
            )
        lines.extend(
            (
                "[pack]",
                f"name = {cls._clean(build.modpack_name)}",
                "author = solder.py",
                f"description = Exported from {cls._clean(build.modpack_name)}",
                f"version = {cls._clean(build.version)}",
                "",
            )
        )
        return "\n".join(lines)

    @classmethod
    def _resource(
        cls,
        package,
        resource_name,
        raw_hash,
        public_repo_url,
        native_file=None,
        optional=None,
    ):
        cls._validate_artifact_component(package.name, "mod slug")
        cls._validate_artifact_component(package.version, "mod version")
        jar_name = f"{package.name}-{package.version}.jar"
        url = (
            native_file.download_url
            if native_file is not None
            else cls._artifact_url(public_repo_url, package.name, jar_name)
        )
        side = cls._side(package.side).lower()
        lines = [
            f"[{resource_name}]",
            "type = url",
            f"destination = mods/{jar_name}",
            f"url = {url}",
            f"side = {side}",
            "optional = "
            + (
                "true"
                if (package.optional if optional is None else optional)
                else "false"
            ),
            f"MD5 = {raw_hash}",
            "",
        ]
        return "\n".join(lines)

    @classmethod
    def _optionals(cls, optionals, optional_groups=()):
        if not optionals:
            return ""

        from .advanced_optional import SINGLE

        rendered_by_group = {}
        ungrouped = []
        for package, resource_name, grouped in optionals:
            if grouped is None:
                ungrouped.append((package, resource_name, False))
            else:
                group, item = grouped
                rendered_by_group.setdefault(group.id, []).append(
                    (
                        package,
                        resource_name,
                        item.selected_by_default,
                        item.sort_order,
                        item.id,
                    )
                )

        lines = []
        for group in optional_groups or ():
            choices = rendered_by_group.get(group.id, [])
            if not choices:
                continue
            choices.sort(key=lambda choice: (choice[3], choice[4]))
            if group.selection_type == SINGLE:
                if sum(1 for choice in choices if choice[2]) != 1:
                    raise MCInstanceExportError(
                        f'Optional group "{group.name}" must have exactly one default.'
                    )
                minimum = maximum = 1
            else:
                minimum, maximum = 0, len(choices)
            lines.extend(
                (
                    f"[solder-optionals-{group.id}]",
                    f"title = {cls._clean(group.name)}",
                    f"minchoices = {minimum}",
                    f"maxchoices = {maximum}",
                )
            )
            for index, choice in enumerate(choices, 1):
                package, resource_name, default = choice[:3]
                lines.extend(
                    (
                        f"option{index}.name = {cls._clean(package.pretty_name)}",
                        f"option{index}.description = {cls._clean(package.description)}",
                        f"option{index}.default = {'true' if default else 'false'}",
                        f"option{index}.resources = {resource_name}",
                    )
                )
            lines.append("")

        if ungrouped:
            lines.extend(
                (
                    "[solder-optionals]",
                    "title = Optional mods",
                    "minchoices = 0",
                    f"maxchoices = {len(ungrouped)}",
                )
            )
            for index, (package, resource_name, default) in enumerate(
                ungrouped, 1
            ):
                lines.extend(
                    (
                        f"option{index}.name = {cls._clean(package.pretty_name)}",
                        f"option{index}.description = {cls._clean(package.description)}",
                        f"option{index}.default = {'true' if default else 'false'}",
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
        destination_prefixes=None,
    ):
        cls._validate_artifact_component(package.name, "mod slug")
        cls._validate_artifact_component(package.version, "mod version")
        filename = f"{package.name}-{package.version}.zip"
        with cls._package_file(
            local_repo_root, public_repo_url, package.name, filename
        ) as package_file:
            expected_hash = cls._normal_hash(package.md5)
            if expected_hash:
                actual_hash = cls._file_md5(package_file)
                if actual_hash != expected_hash:
                    raise MCInstanceExportError(
                        f'The stored ZIP for "{package.pretty_name}" does not match its MD5.'
                    )

            prefixes = destination_prefixes or {
                "BOTH": "overrides",
                "CLIENT": "client-overrides",
                "SERVER": "server-overrides",
            }
            prefix = prefixes.get(cls._side(package.side))
            if prefix is None:
                return

            try:
                source = zipfile.ZipFile(package_file, "r")
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
                        destination = (
                            f"{prefix}/{relative.as_posix()}"
                            if prefix
                            else relative.as_posix()
                        )
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
    @contextmanager
    def _package_file(cls, local_repo_root, public_repo_url, mod_name, filename):
        root = Path(local_repo_root).resolve()
        local_path = (root / mod_name / filename).resolve()
        try:
            local_path.relative_to(root)
        except ValueError as error:
            raise MCInstanceExportError("Invalid local package path.") from error

        if local_path.is_file():
            if local_path.stat().st_size > _MAX_PACKAGE_SIZE:
                raise MCInstanceExportError("A package exceeds the MCInstance export limit.")
            try:
                with local_path.open("rb") as source:
                    yield source
            except OSError as error:
                raise MCInstanceExportError(
                    f'Unable to read the stored package "{filename}".'
                ) from error
            return

        url = cls._artifact_url(public_repo_url, mod_name, filename)
        result = tempfile.SpooledTemporaryFile(
            max_size=_SPOOL_MEMORY_LIMIT,
            mode="w+b",
        )
        try:
            with requests.get(
                url,
                stream=True,
                allow_redirects=False,
                timeout=(5, 60),
            ) as response:
                if 300 <= response.status_code < 400:
                    raise MCInstanceExportError(
                        "The package repository returned an unexpected redirect."
                    )
                response.raise_for_status()
                content_length = int(response.headers.get("content-length", 0))
                if content_length > _MAX_PACKAGE_SIZE:
                    raise MCInstanceExportError(
                        "A package exceeds the MCInstance export limit."
                    )
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
            result.seek(0)
            yield result
        except MCInstanceExportError:
            raise
        except (OSError, requests.RequestException, ValueError) as error:
            raise MCInstanceExportError(
                f'Unable to read the stored package "{filename}".'
            ) from error
        finally:
            result.close()

    @staticmethod
    def _file_md5(source):
        digest = hashlib.md5(usedforsecurity=False)
        source.seek(0)
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
        source.seek(0)
        return digest.hexdigest()
