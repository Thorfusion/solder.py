"""Public Packwiz and FileDirector views of published Solder builds."""

from dataclasses import dataclass
import hashlib
import json
import re
from urllib.parse import quote, urlsplit

from .database import Database
from .modpack import Modpack


_MD5_RE = re.compile(r"^[0-9a-fA-F]{32}$")
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,254}$")
_PACKWIZ_LOADERS = {
    "FABRIC": "fabric",
    "FORGE": "forge",
    "LITELOADER": "liteloader",
    "NEOFORGE": "neoforge",
    "QUILT": "quilt",
}


class DistributionExportError(ValueError):
    """Raised when a published build cannot be represented safely."""


class DistributionBuildNotFound(DistributionExportError):
    """Raised when a public, published build does not exist."""


@dataclass(frozen=True)
class DistributionBuild:
    id: int
    pack_name: str
    pack_slug: str
    version: str
    minecraft: str
    modloader: str | None
    modloader_version: str | None
    requested_selector: str

    @property
    def is_channel(self) -> bool:
        return self.requested_selector in {"latest", "recommended"}


@dataclass(frozen=True)
class DistributionPackage:
    id: int
    mod_slug: str
    pretty_name: str
    description: str
    version: str
    zip_md5: str
    jar_md5: str | None
    side: str
    modtype: str
    optional: bool

    @property
    def jar_ready(self) -> bool:
        return (
            self.modtype == "MOD"
            and _MD5_RE.fullmatch(str(self.jar_md5 or "").strip()) is not None
        )

    @property
    def jar_filename(self) -> str:
        return f"{self.mod_slug}-{self.version}.jar"

    @property
    def zip_filename(self) -> str:
        return f"{self.mod_slug}-{self.version}.zip"


class DistributionExport:
    """Load only builds visible through Solder's unauthenticated read API."""

    @classmethod
    def load_build(cls, pack_slug: str, selector: str) -> DistributionBuild:
        cls._validate_slug(pack_slug, "modpack slug")
        cls._validate_component(selector, "build selector")

        modpack = Modpack.get_by_cid_slug_api(None, pack_slug)
        if modpack is None:
            raise DistributionBuildNotFound("The requested modpack was not found.")

        version = selector
        if selector == "latest":
            version = modpack.latest
        elif selector == "recommended":
            version = modpack.recommended
        if not version:
            raise DistributionBuildNotFound(
                f'The modpack does not have a "{selector}" build.'
            )

        cls._validate_component(version, "build version")
        build = modpack.get_build_api(version, cid=None)
        if build is None:
            raise DistributionBuildNotFound(
                "The requested public, published build was not found."
            )

        return DistributionBuild(
            id=build.id,
            pack_name=modpack.name,
            pack_slug=modpack.slug,
            version=build.version,
            minecraft=build.minecraft,
            modloader=build.modloader,
            modloader_version=build.forge,
            requested_selector=selector,
        )

    @classmethod
    def load_packages(
        cls, build_id: int, optional: bool | None = None
    ) -> list[DistributionPackage]:
        rows = cls._package_rows(build_id)
        packages = [cls._package_from_row(row) for row in rows]
        if optional is None:
            return packages
        return [package for package in packages if package.optional is optional]

    @classmethod
    def load_package(
        cls, build_id: int, mod_slug: str
    ) -> DistributionPackage:
        cls._validate_slug(mod_slug, "mod slug")
        rows = cls._package_rows(build_id, mod_slug)
        if not rows:
            raise DistributionBuildNotFound(
                "The requested mod is not in this build."
            )
        if len(rows) != 1:
            raise DistributionExportError(
                "The build contains more than one version of this mod."
            )
        return cls._package_from_row(rows[0])

    @staticmethod
    def _package_rows(build_id: int, mod_slug: str | None = None) -> list[dict]:
        conn = Database.get_connection()
        if conn is None:
            raise DistributionExportError("The database is unavailable.")
        cursor = conn.cursor(dictionary=True)
        try:
            query = """SELECT modversions.id,
                              modversions.version,
                              modversions.md5,
                              modversions.jarmd5,
                              mods.name AS mod_slug,
                              mods.pretty_name,
                              mods.description,
                              mods.side,
                              mods.modtype,
                              build_modversion.optional
                       FROM build_modversion
                       INNER JOIN modversions
                           ON modversions.id = build_modversion.modversion_id
                       INNER JOIN mods ON mods.id = modversions.mod_id
                       WHERE build_modversion.build_id = %s"""
            parameters = [build_id]
            if mod_slug is not None:
                query += " AND mods.name = %s"
                parameters.append(mod_slug)
            query += " ORDER BY mods.name ASC, modversions.id ASC"
            cursor.execute(query, tuple(parameters))
            return cursor.fetchall()
        finally:
            cursor.close()
            conn.close()

    @classmethod
    def _package_from_row(cls, row: dict) -> DistributionPackage:
        cls._validate_slug(row["mod_slug"], "mod slug")
        cls._validate_component(row["version"], "mod version")
        return DistributionPackage(
            id=row["id"],
            mod_slug=row["mod_slug"],
            pretty_name=row.get("pretty_name") or row["mod_slug"],
            description=row.get("description") or "",
            version=row["version"],
            zip_md5=str(row.get("md5") or "").strip().lower(),
            jar_md5=(str(row.get("jarmd5") or "").strip().lower() or None),
            side=str(row.get("side") or "BOTH").upper(),
            modtype=str(row.get("modtype") or "MOD").upper(),
            optional=bool(row.get("optional")),
        )

    @staticmethod
    def _validate_component(value, label: str) -> None:
        value = str(value or "")
        if (
            not value
            or value in {".", ".."}
            or len(value) > 255
            or "/" in value
            or "\\" in value
            or any(ord(character) < 32 for character in value)
        ):
            raise DistributionExportError(f"The {label} is not safe to export.")

    @staticmethod
    def _validate_slug(value, label: str) -> None:
        if _SLUG_RE.fullmatch(str(value or "")) is None:
            raise DistributionExportError(f"The {label} is not safe to export.")

    @staticmethod
    def repository_base(public_repo_url: str | None) -> str:
        value = str(public_repo_url or "").strip()
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise DistributionExportError(
                "PUBLIC_REPO_LOCATION must be a public HTTP or HTTPS URL."
            )
        return value.rstrip("/")

    @classmethod
    def application_base(cls, value: str | None) -> str:
        try:
            return cls.repository_base(value)
        except DistributionExportError as error:
            raise DistributionExportError(
                "APP_URL must be configured as the public solder.py HTTP or "
                "HTTPS URL before exporting a remote FileDirector config."
            ) from error

    @classmethod
    def artifact_url(
        cls, public_repo_url: str | None, package: DistributionPackage,
        filename: str,
    ) -> str:
        base = cls.repository_base(public_repo_url)
        return (
            f"{base}/{quote(package.mod_slug, safe='-._~')}/"
            f"{quote(filename, safe='-._~')}"
        )


class PackwizExport:
    """Render the standard Packwiz pack format from raw-JAR-ready mods."""

    @staticmethod
    def _toml_string(value) -> str:
        return json.dumps(str(value or ""), ensure_ascii=False)

    @classmethod
    def mod_toml(
        cls, package: DistributionPackage, public_repo_url: str | None
    ) -> bytes:
        if not package.jar_ready:
            raise DistributionExportError(
                "This package does not have a raw JAR for Packwiz."
            )
        side = package.side.lower()
        if side not in {"client", "server", "both"}:
            side = "both"
        url = DistributionExport.artifact_url(
            public_repo_url, package, package.jar_filename
        )
        lines = [
            f"name = {cls._toml_string(package.pretty_name)}",
            f"filename = {cls._toml_string(package.jar_filename)}",
            f"side = {cls._toml_string(side)}",
            "",
            "[download]",
            'hash-format = "md5"',
            f"hash = {cls._toml_string(package.jar_md5)}",
            f"url = {cls._toml_string(url)}",
        ]
        if package.optional:
            lines.extend(
                [
                    "",
                    "[option]",
                    "optional = true",
                    "default = false",
                ]
            )
            if package.description:
                lines.append(
                    f"description = {cls._toml_string(package.description)}"
                )
        return ("\n".join(lines) + "\n").encode("utf-8")

    @classmethod
    def index_toml(
        cls,
        packages: list[DistributionPackage],
        public_repo_url: str | None,
    ) -> tuple[bytes, int]:
        lines = ['hash-format = "sha256"']
        excluded = 0
        seen_slugs = set()
        for package in packages:
            if not package.jar_ready:
                excluded += 1
                continue
            if package.mod_slug in seen_slugs:
                raise DistributionExportError(
                    "The build contains more than one version of a mod."
                )
            seen_slugs.add(package.mod_slug)
            metadata = cls.mod_toml(package, public_repo_url)
            lines.extend(
                [
                    "",
                    "[[files]]",
                    f'file = {cls._toml_string(f"mods/{package.mod_slug}.pw.toml")}',
                    f'hash = "{hashlib.sha256(metadata).hexdigest()}"',
                    "metafile = true",
                ]
            )
        return ("\n".join(lines) + "\n").encode("utf-8"), excluded

    @classmethod
    def pack_toml(cls, build: DistributionBuild, index: bytes) -> bytes:
        lines = [
            f"name = {cls._toml_string(build.pack_name)}",
            f"version = {cls._toml_string(build.version)}",
            'pack-format = "packwiz:1.1.0"',
            "",
            "[index]",
            'file = "index.toml"',
            'hash-format = "sha256"',
            f'hash = "{hashlib.sha256(index).hexdigest()}"',
            "",
            "[versions]",
            f"minecraft = {cls._toml_string(build.minecraft)}",
        ]
        if build.modloader:
            loader_key = _PACKWIZ_LOADERS.get(str(build.modloader).upper())
            if loader_key is None:
                raise DistributionExportError(
                    "This build's modloader is not supported by Packwiz."
                )
            if not build.modloader_version:
                raise DistributionExportError(
                    "Set the build's modloader version before exporting it "
                    "to Packwiz."
                )
            lines.append(
                f"{loader_key} = {cls._toml_string(build.modloader_version)}"
            )
        return ("\n".join(lines) + "\n").encode("utf-8")


class FileDirectorExport:
    """Render FileDirector URL bundles and remote config pointers."""

    EXCLUDED_TYPES = {"MCIL", "LAUNCHER"}

    @classmethod
    def bundle(
        cls,
        packages: list[DistributionPackage],
        public_repo_url: str | None,
    ) -> bytes:
        entries = []
        for package in packages:
            if package.modtype in cls.EXCLUDED_TYPES:
                continue
            if package.jar_ready:
                filename = package.jar_filename
                entry = {
                    "url": DistributionExport.artifact_url(
                        public_repo_url, package, filename
                    ),
                    "fileName": filename,
                    "metadata": {
                        "hash": {"MD5": package.jar_md5},
                    },
                }
            else:
                filename = package.zip_filename
                entry = {
                    "url": DistributionExport.artifact_url(
                        public_repo_url, package, filename
                    ),
                    "fileName": filename,
                    "folder": ".",
                    "installationPolicy": {
                        "extract": True,
                        "deleteAfterExtract": True,
                    },
                }
            if package.side in {"CLIENT", "SERVER"}:
                entry.setdefault("metadata", {})["side"] = package.side
            if package.optional:
                policy = entry.setdefault("installationPolicy", {})
                policy.update(
                    {
                        "optionalKey": package.mod_slug,
                        "selectedByDefault": False,
                        "name": package.pretty_name,
                        "description": package.description,
                    }
                )
            entries.append(entry)
        return (json.dumps({"url": entries}, indent=2) + "\n").encode("utf-8")

    @staticmethod
    def remote(url: str) -> bytes:
        return (json.dumps({"url": url}, indent=2) + "\n").encode("utf-8")
