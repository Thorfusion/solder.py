"""Create Modrinth and CurseForge packs backed by Solder artifacts."""

from contextlib import contextmanager
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import PurePosixPath
import re
import shutil
import tempfile
from urllib.parse import quote, urlencode, urlsplit
import zipfile

import requests

from .compatibility import normalize_modloader, version_is_compatible
from .distribution import DistributionExport, FileDirectorExport, PackwizExport
from .integration import (
    IntegrationError,
    ModrinthProvider,
    REQUEST_TIMEOUT,
    USER_AGENT,
)
from .mcinstance import MCInstanceExport, MCInstanceExportError


_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA512_RE = re.compile(r"^[0-9a-f]{128}$")
_JAVA_VERSION_RE = re.compile(
    r"^(?:1\.(?P<legacy>\d+)|(?P<modern>\d+))(?:[._+u-].*)?$",
    re.IGNORECASE,
)
_SPOOL_MEMORY_LIMIT = 8 * 1024 * 1024
_MAX_DOWNLOADER_SIZE = 64 * 1024 * 1024
_IGNORED_PACKAGE_TYPES = {"BOOTSTRAP", "MCIL", "LAUNCHER"}
_SOURCE_MODES = {"solder", "hybrid"}
_DELIVERY_MODES = {"bundled", "hosted"}
_MRPACK_LOADERS = {
    "FABRIC": "fabric-loader",
    "FORGE": "forge",
    "NEOFORGE": "neoforge",
    "QUILT": "quilt-loader",
}
_PRISM_LOADERS = {
    "FABRIC": "net.fabricmc.fabric-loader",
    "FORGE": "net.minecraftforge",
    "LITELOADER": "com.mumfrey.liteloader",
    "NEOFORGE": "net.neoforged",
    "QUILT": "org.quiltmc.quilt-loader",
}


class PlatformExportError(ValueError):
    """Raised when a build cannot be exported for a launcher platform."""


@dataclass(frozen=True)
class NativeModrinthFile:
    project_id: str
    version_id: str
    filename: str
    download_url: str
    sha1: str
    sha512: str
    size: int


@dataclass(frozen=True)
class DownloaderRelease:
    selector: str
    version: str
    default: bool
    modrinth: NativeModrinthFile | None = None
    curseforge_file_id: int | None = None
    modrinth_dependencies: tuple[NativeModrinthFile, ...] = ()
    curseforge_dependencies: tuple["CurseForgeFile", ...] = ()


@dataclass(frozen=True)
class DownloaderSpec:
    key: str
    label: str
    setting_key: str
    modrinth_project_id: str | None
    curseforge_project_id: int | None
    supports_remote_config: bool
    supported_loaders: tuple[str, ...] = ("FORGE",)
    modrinth_required_projects: tuple[str, ...] = ()
    curseforge_required_projects: tuple[int, ...] = ()
    releases: tuple[DownloaderRelease, ...] = ()

    def release(self, version=None):
        if version:
            return next(
                (
                    release
                    for release in self.releases
                    if release.selector.lower() == str(version).lower()
                ),
                None,
            )
        return next(
            (release for release in self.releases if release.default),
            self.releases[0] if self.releases else None,
        )


@dataclass(frozen=True)
class SelectedDownloader:
    downloader: DownloaderSpec
    release: DownloaderRelease

    @property
    def key(self):
        return self.downloader.key

    @property
    def label(self):
        return self.downloader.label

    @property
    def supports_remote_config(self):
        return self.downloader.supports_remote_config

    @property
    def modrinth(self):
        return self.release.modrinth

    @property
    def curseforge_project_id(self):
        return self.downloader.curseforge_project_id

    @property
    def curseforge_file_id(self):
        return self.release.curseforge_file_id

    @property
    def modrinth_dependencies(self):
        return self.release.modrinth_dependencies

    @property
    def curseforge_dependencies(self):
        return self.release.curseforge_dependencies


@dataclass(frozen=True)
class ExportPackagePlan:
    packages: tuple
    downloader_packages: tuple
    native_entries: tuple
    native_files: dict


@dataclass(frozen=True)
class CurseForgeFile:
    project_id: int
    file_id: int
    display_name: str
    filename: str
    game_versions: tuple[str, ...]
    published: str


class CurseForgeDownloaderAPI:
    """Resolve downloader files through CurseForge's authenticated API."""

    base_url = "https://api.curseforge.com/v1"

    def __init__(self, api_key, http=None):
        self.api_key = str(api_key or "").strip()
        self.http = http or requests.Session()
        if not self.api_key:
            raise PlatformExportError(
                "CURSEFORGE_API_KEY is required for CurseForge exports."
            )
        if any(ord(character) < 32 for character in self.api_key):
            raise PlatformExportError("CURSEFORGE_API_KEY is invalid.")

    def _request_json(self, path, *, params=None):
        try:
            response = self.http.get(
                f"{self.base_url}{path}",
                params=params,
                headers={
                    "Accept": "application/json",
                    "User-Agent": USER_AGENT,
                    "x-api-key": self.api_key,
                },
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
            )
        except requests.RequestException as error:
            raise PlatformExportError(
                "CurseForge could not be reached."
            ) from error

        try:
            if response.status_code in {401, 403}:
                raise PlatformExportError(
                    "CurseForge rejected CURSEFORGE_API_KEY."
                )
            if 300 <= response.status_code < 400:
                raise PlatformExportError(
                    "CurseForge returned an unexpected redirect."
                )
            response.raise_for_status()
            payload = response.json()
        except PlatformExportError:
            raise
        except (requests.RequestException, ValueError) as error:
            raise PlatformExportError(
                "CurseForge returned an invalid response."
            ) from error
        finally:
            response.close()
        if not isinstance(payload, dict):
            raise PlatformExportError("CurseForge returned an invalid response.")
        return payload

    @staticmethod
    def _file(payload, project_id, minecraft, modloader=None):
        try:
            file_id = int(payload.get("id"))
            returned_project = int(payload.get("modId"))
        except (TypeError, ValueError) as error:
            raise PlatformExportError(
                "CurseForge returned invalid downloader metadata."
            ) from error
        filename = str(payload.get("fileName") or "").strip()
        game_versions = tuple(
            str(value) for value in payload.get("gameVersions") or ()
        )
        normalized_loader = normalize_modloader(modloader)
        normalized_game_versions = {
            value.replace(" ", "").upper() for value in game_versions
        }
        loader_supported = (
            not normalized_loader
            or normalized_loader == "VANILLA"
            or normalized_loader in normalized_game_versions
        )
        if (
            file_id <= 0
            or returned_project != int(project_id)
            or payload.get("isAvailable") is False
            or not filename.lower().endswith(".jar")
            or str(minecraft) not in game_versions
            or not loader_supported
        ):
            raise PlatformExportError(
                "The selected CurseForge file does not support this build."
            )
        return CurseForgeFile(
            project_id=returned_project,
            file_id=file_id,
            display_name=str(payload.get("displayName") or filename),
            filename=filename,
            game_versions=game_versions,
            published=str(payload.get("fileDate") or ""),
        )

    def list_files(self, project_id, minecraft, modloader=None):
        files = []
        index = 0
        while index < 10000:
            payload = self._request_json(
                f"/mods/{int(project_id)}/files",
                params={
                    "gameVersion": str(minecraft),
                    "index": index,
                    "pageSize": 50,
                },
            )
            data = payload.get("data")
            pagination = payload.get("pagination") or {}
            if not isinstance(data, list) or not isinstance(pagination, dict):
                raise PlatformExportError(
                    "CurseForge returned an invalid downloader list."
                )
            for item in data:
                try:
                    files.append(
                        self._file(item, project_id, minecraft, modloader)
                    )
                except PlatformExportError:
                    continue
            try:
                result_count = int(
                    pagination.get("resultCount") or len(data)
                )
                total_count = int(pagination.get("totalCount") or len(data))
            except (TypeError, ValueError) as error:
                raise PlatformExportError(
                    "CurseForge returned invalid pagination metadata."
                ) from error
            if result_count < 0 or total_count < 0:
                raise PlatformExportError(
                    "CurseForge returned invalid pagination metadata."
                )
            if not data or index + result_count >= total_count:
                break
            index += max(result_count, len(data))
        files.sort(key=lambda item: (item.published, item.file_id), reverse=True)
        return tuple(files)

    def get_file(self, project_id, file_id, minecraft, modloader=None):
        try:
            file_id = int(file_id)
        except (TypeError, ValueError) as error:
            raise PlatformExportError(
                "The CurseForge downloader file identifier is invalid."
            ) from error
        payload = self._request_json(
            f"/mods/{int(project_id)}/files/{file_id}"
        )
        return self._file(
            payload.get("data") or {}, project_id, minecraft, modloader
        )


DOWNLOADERS = (
    DownloaderSpec(
        key="solderpyloader",
        label="SolderPy Loader",
        setting_key="solderpy_loader_enabled",
        modrinth_project_id="5LpwENAj",
        curseforge_project_id=1702825,
        supports_remote_config=True,
        supported_loaders=("FORGE", "NEOFORGE", "FABRIC", "QUILT"),
        modrinth_required_projects=("zCFNaupz",),
        curseforge_required_projects=(1491728,),
    ),
    DownloaderSpec(
        key="mcil",
        label="MCInstance Loader",
        setting_key="mcil_enabled",
        modrinth_project_id="cUtsYbG5",
        curseforge_project_id=576287,
        supports_remote_config=False,
    ),
    DownloaderSpec(
        key="filedirector",
        label="FileDirector",
        setting_key="filedirector_enabled",
        modrinth_project_id="4dRu1OUz",
        curseforge_project_id=650242,
        supports_remote_config=True,
    ),
    DownloaderSpec(
        key="modpackdirector",
        label="Modpack Director",
        setting_key="modpack_director_enabled",
        modrinth_project_id=None,
        curseforge_project_id=969109,
        supports_remote_config=True,
        supported_loaders=("FORGE", "NEOFORGE"),
    ),
)


class PlatformPackExport:
    """Render launcher archives without treating downloaders as Solder mods."""

    @staticmethod
    @contextmanager
    def _zip_archive():
        """Yield a ZIP target and leave its rewound backing file open."""
        archive = tempfile.SpooledTemporaryFile(
            max_size=_SPOOL_MEMORY_LIMIT, mode="w+b"
        )
        try:
            with zipfile.ZipFile(
                archive, "w", compression=zipfile.ZIP_DEFLATED
            ) as target:
                yield archive, target
        except Exception:
            archive.close()
            raise
        archive.seek(0)

    @staticmethod
    def downloader_specs() -> tuple[DownloaderSpec, ...]:
        return DOWNLOADERS

    @staticmethod
    def downloader_key(value):
        return str(value or "").strip().lower().partition(":")[0]

    @classmethod
    def downloader_spec(cls, value):
        key = cls.downloader_key(value)
        return next((item for item in DOWNLOADERS if item.key == key), None)

    @classmethod
    def _modrinth_release(
        cls, version, *, default=False, dependencies=()
    ):
        native_file = NativeModrinthFile(
            project_id=version.project_id,
            version_id=version.version_id,
            filename=version.filename,
            download_url=version.download_url,
            sha1=version.hashes.get("sha1"),
            sha512=version.hashes.get("sha512"),
            size=version.size,
        )
        cls._validate_native_file(native_file)
        return DownloaderRelease(
            selector=version.version_id,
            version=version.version_number,
            default=default,
            modrinth=native_file,
            modrinth_dependencies=tuple(dependencies),
        )

    @staticmethod
    def _curseforge_release(file, *, default=False, dependencies=()):
        return DownloaderRelease(
            selector=str(file.file_id),
            version=file.display_name,
            default=default,
            curseforge_file_id=file.file_id,
            curseforge_dependencies=tuple(dependencies),
        )

    @classmethod
    def _modrinth_downloader_dependencies(cls, spec, build, provider):
        dependencies = []
        for project_id in spec.modrinth_required_projects:
            try:
                versions = provider.list_versions(
                    project_id, build.minecraft, build.modloader
                )
            except IntegrationError as error:
                raise PlatformExportError(
                    f"{spec.label}'s required Modrinth dependency could not be loaded."
                ) from error
            if not versions:
                raise PlatformExportError(
                    f"{spec.label} has no compatible required Modrinth dependency."
                )
            dependencies.append(cls._modrinth_release(versions[0]).modrinth)
        return tuple(dependencies)

    @classmethod
    def _curseforge_downloader_dependencies(cls, spec, build, provider):
        dependencies = []
        for project_id in spec.curseforge_required_projects:
            files = provider.list_files(
                project_id, build.minecraft, build.modloader
            )
            if not files:
                raise PlatformExportError(
                    f"{spec.label} has no compatible required CurseForge dependency."
                )
            dependencies.append(files[0])
        return tuple(dependencies)

    @classmethod
    def available_downloaders(
        cls,
        build,
        platform,
        *,
        specs=None,
        http=None,
        curseforge_api_key=None,
    ):
        """Resolve compatible releases from the selected launcher's API."""
        platform = str(platform or "").strip().lower()
        if platform not in {"modrinth", "curseforge"}:
            raise PlatformExportError("Unknown downloader platform.")
        build_loader = normalize_modloader(build.modloader)

        available = []
        failures = []
        provider = None
        selected_specs = DOWNLOADERS if specs is None else tuple(specs)
        for spec in selected_specs:
            if build_loader not in spec.supported_loaders:
                continue
            try:
                if platform == "modrinth":
                    if not spec.modrinth_project_id:
                        continue
                    if provider is None:
                        provider = ModrinthProvider(http=http)
                    versions = provider.list_versions(
                        spec.modrinth_project_id,
                        build.minecraft,
                        build.modloader,
                    )
                    dependencies = (
                        cls._modrinth_downloader_dependencies(
                            spec, build, provider
                        )
                        if versions
                        else ()
                    )
                    releases = tuple(
                        cls._modrinth_release(
                            version,
                            default=index == 0,
                            dependencies=dependencies,
                        )
                        for index, version in enumerate(versions)
                    )
                else:
                    if not spec.curseforge_project_id:
                        continue
                    if provider is None:
                        provider = CurseForgeDownloaderAPI(
                            curseforge_api_key, http=http
                        )
                    files = provider.list_files(
                        spec.curseforge_project_id,
                        build.minecraft,
                        build.modloader,
                    )
                    dependencies = (
                        cls._curseforge_downloader_dependencies(
                            spec, build, provider
                        )
                        if files
                        else ()
                    )
                    releases = tuple(
                        cls._curseforge_release(
                            file,
                            default=index == 0,
                            dependencies=dependencies,
                        )
                        for index, file in enumerate(files)
                    )
            except (IntegrationError, PlatformExportError) as error:
                # One unpublished or temporarily unavailable downloader must
                # not hide compatible releases from the other families.
                failures.append((spec, error))
                continue
            if releases:
                available.append(replace(spec, releases=releases))
        if not available and failures:
            failed_spec, failure = failures[0]
            if len(failures) == 1:
                message = (
                    f"{failed_spec.label} downloader versions could not be "
                    f"loaded from {platform.title()}."
                )
            else:
                message = (
                    f"{platform.title()} downloader versions could not be loaded."
                )
            raise PlatformExportError(message) from failure
        return tuple(available)

    @staticmethod
    def override_modloader_version(build, value):
        """Apply a request-only loader-version override to an export build."""
        if value is None:
            return build
        version = str(value).strip()
        if len(version) > 255 or any(ord(character) < 32 for character in version):
            raise PlatformExportError("The modloader version is invalid.")
        if hasattr(build, "forge"):
            return replace(build, forge=version or None)
        if hasattr(build, "modloader_version"):
            return replace(build, modloader_version=version or None)
        raise PlatformExportError("This export cannot override the modloader version.")

    @staticmethod
    def source_mode(value, default="solder"):
        mode = str(value or default).strip().lower()
        if mode not in _SOURCE_MODES:
            raise PlatformExportError("Unknown download source mode.")
        return mode

    @staticmethod
    def delivery_mode(value, default="bundled"):
        mode = str(value or default).strip().lower()
        if mode not in _DELIVERY_MODES:
            raise PlatformExportError("Unknown config delivery mode.")
        return mode

    @classmethod
    def config_delivery(cls, downloader, value):
        default = "hosted" if downloader.supports_remote_config else "bundled"
        mode = cls.delivery_mode(value, default=default)
        if mode == "hosted" and not downloader.supports_remote_config:
            return "bundled"
        return mode

    @staticmethod
    def hosted_selector(build, value):
        selector = str(value or "build").strip()
        if selector == "build":
            return str(build.version)
        if selector not in {"latest", "recommended"}:
            raise PlatformExportError("Unknown hosted build selector.")
        return selector

    @classmethod
    def resolve_downloader(
        cls,
        key,
        build,
        platform,
        *,
        required=True,
        http=None,
        curseforge_api_key=None,
    ):
        selection = str(key or "").strip()
        if selection.lower() in {"", "none"}:
            if required:
                raise PlatformExportError(
                    "Select a compatible mod downloader for this export."
                )
            return None
        downloader_key, separator, requested_version = selection.partition(":")
        downloader = cls.downloader_spec(downloader_key)
        if downloader is None:
            raise PlatformExportError("Unknown mod downloader or version.")
        if normalize_modloader(build.modloader) not in downloader.supported_loaders:
            raise PlatformExportError(
                "That mod downloader version does not support this build."
            )
        if separator:
            if platform == "modrinth":
                if not downloader.modrinth_project_id:
                    raise PlatformExportError(
                        f"{downloader.label} is not distributed on Modrinth."
                    )
                try:
                    provider = ModrinthProvider(http=http)
                    version = provider.get_version(
                        downloader.modrinth_project_id,
                        requested_version,
                        build.minecraft,
                        build.modloader,
                    )
                except IntegrationError as error:
                    raise PlatformExportError(
                        "The selected Modrinth downloader is unavailable."
                    ) from error
                dependencies = cls._modrinth_downloader_dependencies(
                    downloader, build, provider
                )
                release = cls._modrinth_release(
                    version, default=True, dependencies=dependencies
                )
            elif platform == "curseforge":
                if not downloader.curseforge_project_id:
                    raise PlatformExportError(
                        f"{downloader.label} is not distributed on CurseForge."
                    )
                provider = CurseForgeDownloaderAPI(
                    curseforge_api_key, http=http
                )
                file = provider.get_file(
                    downloader.curseforge_project_id,
                    requested_version,
                    build.minecraft,
                    build.modloader,
                )
                dependencies = cls._curseforge_downloader_dependencies(
                    downloader, build, provider
                )
                release = cls._curseforge_release(
                    file, default=True, dependencies=dependencies
                )
            else:
                raise PlatformExportError("Unknown downloader platform.")
        else:
            available = cls.available_downloaders(
                build,
                platform,
                specs=(downloader,),
                http=http,
                curseforge_api_key=curseforge_api_key,
            )
            release = available[0].release() if available else None
        if release is None:
            raise PlatformExportError(
                "That mod downloader version does not support this build."
            )
        return SelectedDownloader(downloader, release)

    @staticmethod
    def _actual_packages(packages):
        return [
            package
            for package in packages
            if str(package.modtype or "MOD").upper()
            not in _IGNORED_PACKAGE_TYPES
        ]

    @staticmethod
    def _optional_state(package):
        return int(
            getattr(
                package,
                "optional_state",
                int(bool(getattr(package, "optional", False))),
            )
        )

    @classmethod
    def _basic_packages(cls, packages):
        return tuple(
            package for package in packages if cls._optional_state(package) != 2
        )

    @staticmethod
    def _safe_filename(value):
        value = str(value or "")
        path = PurePosixPath(value.replace("\\", "/"))
        if (
            not value
            or len(value) > 255
            or path.name != value
            or value in {".", ".."}
            or any(ord(character) < 32 for character in value)
        ):
            raise PlatformExportError(
                "Modrinth returned an unsafe download filename."
            )
        return value

    @staticmethod
    def _validate_native_file(file):
        filename = PlatformPackExport._safe_filename(file.filename)
        parsed = urlsplit(str(file.download_url or ""))
        hostname = (parsed.hostname or "").lower()
        sha1 = str(file.sha1 or "").lower()
        sha512 = str(file.sha512 or "").lower()
        try:
            file_size = int(file.size or 0)
        except (TypeError, ValueError) as error:
            raise PlatformExportError(
                "Modrinth returned incomplete or untrusted file metadata."
            ) from error
        if (
            parsed.scheme != "https"
            or hostname != "cdn.modrinth.com"
            or parsed.username is not None
            or parsed.password is not None
            or not _SHA1_RE.fullmatch(sha1)
            or not _SHA512_RE.fullmatch(sha512)
            or file_size <= 0
        ):
            raise PlatformExportError(
                "Modrinth returned incomplete or untrusted file metadata."
            )
        return filename, sha1, sha512

    @staticmethod
    def _environment(package=None):
        side = str(getattr(package, "side", "BOTH") or "BOTH").upper()
        selected = (
            "optional"
            if bool(getattr(package, "optional", False))
            else "required"
        )
        if side == "CLIENT":
            return {"client": selected, "server": "unsupported"}
        if side == "SERVER":
            return {"client": "unsupported", "server": selected}
        if side != "BOTH":
            raise PlatformExportError(f'Unsupported package side "{side}".')
        return {"client": selected, "server": selected}

    @classmethod
    def _mrpack_file_entry(cls, file, package=None):
        filename, sha1, sha512 = cls._validate_native_file(file)
        return {
            "path": f"mods/{filename}",
            "hashes": {"sha1": sha1, "sha512": sha512},
            "env": cls._environment(package),
            "downloads": [file.download_url],
            "fileSize": int(file.size),
        }

    @staticmethod
    def _loader_version(build):
        version = str(build.forge or "").strip()
        prefix = f"{build.minecraft}-"
        if version.startswith(prefix):
            version = version[len(prefix) :]
        if not version:
            raise PlatformExportError(
                "Set the build's modloader version before exporting it."
            )
        return version

    @classmethod
    def _mrpack_dependencies(cls, build):
        dependencies = {"minecraft": str(build.minecraft)}
        loader = normalize_modloader(build.modloader)
        if loader in {None, "VANILLA"}:
            return dependencies
        dependency_name = _MRPACK_LOADERS.get(loader)
        if dependency_name is None:
            raise PlatformExportError(
                "This build's modloader is not supported by MRPack."
            )
        dependencies[dependency_name] = cls._loader_version(build)
        return dependencies

    @staticmethod
    def _remote_config(
        build,
        application_url,
        bundle_name,
        selector="build",
        source_mode="solder",
        route_prefix="filedirector",
    ):
        base = DistributionExport.application_base(application_url)
        DistributionExport._validate_slug(build.modpack_slug, "modpack slug")
        selector = PlatformPackExport.hosted_selector(build, selector)
        DistributionExport._validate_component(selector, "build selector")
        url = (
            f"{base}/{route_prefix}/"
            f"{quote(build.modpack_slug, safe='-._~')}/"
            f"{quote(selector, safe='-._~')}/"
            f"{bundle_name}.bundle.json"
        )
        if source_mode == "hybrid" and bundle_name != "modrinth-fallback":
            url += "?" + urlencode({"source": "hybrid"})
        return FileDirectorExport.remote(url)

    @staticmethod
    def _modpack_director_metadata(
        build,
        application_url,
        selector="build",
    ):
        """Create Modpack Director's optional pack identity/update config."""
        DistributionExport._validate_slug(build.modpack_slug, "modpack slug")
        metadata = {
            "packName": str(build.modpack_name),
            "localVersion": str(build.version),
            "refuseLaunch": False,
            "requiresRestart": False,
        }
        if application_url:
            base = DistributionExport.application_base(application_url)
            selector = PlatformPackExport.hosted_selector(build, selector)
            DistributionExport._validate_component(selector, "build selector")
            metadata["remoteVersion"] = (
                f"{base}/modpackdirector/"
                f"{quote(build.modpack_slug, safe='-._~')}/"
                f"{quote(selector, safe='-._~')}/version.txt"
            )
        return (
            json.dumps(metadata, indent=2)
            + "\n"
        ).encode("utf-8")

    @staticmethod
    def solderpy_loader_config(
        build, application_url, selector="build", *, modpack_slug=None
    ):
        """Create the launch-time bootstrap API pointer for SolderPy Loader."""
        if not build.is_published or build.private:
            raise PlatformExportError(
                "SolderPy Loader requires a published, non-private build."
            )
        base = DistributionExport.application_base(application_url)
        slug = modpack_slug or getattr(build, "modpack_slug", None)
        DistributionExport._validate_slug(slug, "modpack slug")
        selector = PlatformPackExport.hosted_selector(build, selector)
        DistributionExport._validate_component(selector, "build selector")
        config = {
            "enabled": True,
            "api": f"{base}/api/",
            "modpack": str(slug),
            "build": selector,
            "target": "auto",
        }
        return (json.dumps(config, indent=2) + "\n").encode("utf-8")

    @staticmethod
    def relauncher_java_config(build):
        """Create Relauncher's supported Java-major selection policy."""
        minimum = str(getattr(build, "min_java", None) or "").strip()
        if not minimum:
            return None

        match = _JAVA_VERSION_RE.fullmatch(minimum)
        if match is None:
            raise PlatformExportError(
                "Minimum Java Version must start with a Java version such as "
                "1.8.0_422, 8u422, 17, or 21.0.2."
            )
        major = int(match.group("legacy") or match.group("modern"))
        if major < 8:
            raise PlatformExportError(
                "SolderPy Loader and Relauncher require Java 8 or newer."
            )

        return (
            "# Generated by solder.py from this build's Minimum Java Version.\n"
            "# Relauncher 1.1.x selects Java by compatible major version.\n"
            "# enabled controls extra JVM arguments; Java selection stays active.\n"
            "enabled = false\n"
            f"java.versions = {major}\n"
        ).encode("utf-8")

    @classmethod
    def render_solderpy_loader_archive(
        cls, selected, config, *, relauncher_config=None, http=None
    ):
        """Bundle one verified SolderPy Loader release and its runtime."""
        if getattr(selected, "key", None) != "solderpyloader":
            raise PlatformExportError("Select a SolderPy Loader release.")
        native_files = (
            selected.modrinth,
            *selected.modrinth_dependencies,
        )
        if native_files[0] is None:
            raise PlatformExportError(
                "The selected SolderPy Loader release has no Modrinth file."
            )

        with cls._zip_archive() as (archive, target):
            for index, native_file in enumerate(native_files):
                destination = (
                    "mods/!solderpy-loader.jar"
                    if index == 0
                    else (
                        "mods/!relauncher.jar"
                        if index == 1
                        else f"mods/!solderpy-loader-dependency-{index}.jar"
                    )
                )
                cls._write_native_file(
                    target, native_file, destination, http=http
                )
            target.writestr("config/solderpy-loader.json", config)
            if relauncher_config is not None:
                target.writestr(
                    "config/relauncher/config.cfg", relauncher_config
                )
        return archive

    @classmethod
    def render_solderpy_loader(
        cls,
        build,
        application_url,
        *,
        selector="build",
    ):
        """Create the configuration archive for an installed Loader."""
        config = cls.solderpy_loader_config(
            build, application_url, selector
        )
        with cls._zip_archive() as (archive, target):
            target.writestr("config/solderpy-loader.json", config)
            relauncher_config = cls.relauncher_java_config(build)
            if relauncher_config is not None:
                target.writestr(
                    "config/relauncher/config.cfg", relauncher_config
                )
        return archive

    @staticmethod
    def _copy_archive(source, target, destination):
        source.seek(0)
        with target.open(destination, "w") as output:
            shutil.copyfileobj(source, output, 1024 * 1024)

    @classmethod
    def _write_native_file(cls, target, file, destination, *, http=None):
        """Download one validated Modrinth file directly into an export ZIP."""
        _filename, expected_sha1, expected_sha512 = cls._validate_native_file(
            file
        )
        expected_size = int(file.size)
        if expected_size > _MAX_DOWNLOADER_SIZE:
            raise PlatformExportError(
                "The selected mod downloader exceeds the export size limit."
            )

        response = None
        try:
            response = (http or requests).get(
                file.download_url,
                stream=True,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
            )
            if 300 <= response.status_code < 400:
                raise PlatformExportError(
                    "The mod downloader returned an unexpected redirect."
                )
            response.raise_for_status()
            content_length = int(response.headers.get("content-length", 0))
            if content_length and content_length != expected_size:
                raise PlatformExportError(
                    "The mod downloader size does not match Modrinth metadata."
                )

            sha1 = hashlib.sha1(usedforsecurity=False)
            sha512 = hashlib.sha512()
            downloaded = 0
            with target.open(destination, "w") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    downloaded += len(chunk)
                    if (
                        downloaded > expected_size
                        or downloaded > _MAX_DOWNLOADER_SIZE
                    ):
                        raise PlatformExportError(
                            "The mod downloader exceeded its declared size."
                        )
                    sha1.update(chunk)
                    sha512.update(chunk)
                    output.write(chunk)

            if (
                downloaded != expected_size
                or sha1.hexdigest() != expected_sha1
                or sha512.hexdigest() != expected_sha512
            ):
                raise PlatformExportError(
                    "The mod downloader does not match Modrinth metadata."
                )
        except PlatformExportError:
            raise
        except (
            OSError,
            requests.RequestException,
            TypeError,
            ValueError,
        ) as error:
            raise PlatformExportError(
                "The selected mod downloader could not be downloaded."
            ) from error
        finally:
            if response is not None:
                response.close()

    @classmethod
    def _write_downloader_config(
        cls,
        target,
        spec,
        build,
        packages,
        public_repo_url,
        local_repo_root,
        application_url,
        bundle_name,
        *,
        source_mode="solder",
        delivery="bundled",
        selector="build",
        native_files=None,
        optional_groups=(),
        archive_root="overrides",
    ):
        source_mode = cls.source_mode(source_mode)
        delivery = cls.config_delivery(spec, delivery)
        native_files = native_files or {}
        archive_root = str(archive_root or "").strip("/")

        def archive_path(relative):
            return (
                f"{archive_root}/{relative}"
                if archive_root
                else relative
            )

        if spec.key == "solderpyloader":
            if source_mode != "solder":
                raise PlatformExportError(
                    "SolderPy Loader requires the Solder-only download source."
                )
            target.writestr(
                archive_path("config/solderpy-loader.json"),
                cls.solderpy_loader_config(
                    build, application_url, selector
                ),
            )
            relauncher_config = cls.relauncher_java_config(build)
            if relauncher_config is not None:
                target.writestr(
                    archive_path("config/relauncher/config.cfg"),
                    relauncher_config,
                )
            return

        if spec.key == "mcil":
            try:
                archive = MCInstanceExport.render(
                    build,
                    packages,
                    public_repo_url,
                    local_repo_root,
                    include_modloader=False,
                    native_files=native_files,
                    optional_groups=optional_groups,
                )
            except MCInstanceExportError as error:
                raise PlatformExportError(str(error)) from error
            try:
                cls._copy_archive(
                    archive,
                    target,
                    archive_path(
                        "config/mcinstanceloader/pack.mcinstance"
                    ),
                )
            finally:
                archive.close()
            return

        if spec.key == "modpackdirector":
            target.writestr(
                archive_path("config/mod-director/modpack.json"),
                cls._modpack_director_metadata(
                    build, application_url, selector
                ),
            )

        if delivery == "hosted":
            if not build.is_published or build.private:
                raise PlatformExportError(
                    "Hosted FileDirector configs require a published, non-private build."
                )
            DistributionExport.repository_base(public_repo_url)
            hosted_bundle_name = (
                "mods"
                if source_mode == "solder" and bundle_name == "modrinth-fallback"
                else bundle_name
            )
            target.writestr(
                archive_path("config/mod-director/solder.remote.json"),
                cls._remote_config(
                    build,
                    application_url,
                    hosted_bundle_name,
                    selector,
                    source_mode,
                    route_prefix=(
                        "modpackdirector"
                        if spec.key == "modpackdirector"
                        else "filedirector"
                    ),
                ),
            )
            return

        target.writestr(
            archive_path("config/mod-director/solder.bundle.json"),
            FileDirectorExport.bundle(
                packages,
                public_repo_url,
                native_files=native_files,
                optional_groups=optional_groups,
            ),
        )

    @classmethod
    def _package_plan(
        cls,
        build,
        packages,
        source_mode,
        *,
        launcher_handles_modrinth=False,
        excluded_modrinth_projects=(),
        http=None,
    ):
        """Apply the common package filters used by launcher exports."""
        excluded_modrinth_projects = {
            str(project_id) for project_id in excluded_modrinth_projects
        }
        packages = tuple(
            package
            for package in cls._actual_packages(packages)
            if not (
                str(package.integration_provider or "").upper() == "MODRINTH"
                and str(package.integration_project_id or "")
                in excluded_modrinth_projects
            )
        )
        native_packages = tuple(
            package for package in packages if package.is_native_modrinth
        )
        native_files = (
            cls.native_modrinth_files(build, native_packages, http=http)
            if source_mode == "hybrid" and native_packages
            else {}
        )

        native_entries = []
        handled_ids = set()
        if launcher_handles_modrinth:
            paths = set()
            for package in native_packages:
                if cls._optional_state(package) == 2:
                    continue
                native_file = native_files.get(package.integration_version_id)
                if native_file is None:
                    continue
                entry = cls._mrpack_file_entry(native_file, package)
                if entry["path"] in paths:
                    raise PlatformExportError(
                        "Multiple Modrinth files use the same destination path."
                    )
                paths.add(entry["path"])
                handled_ids.add(id(package))
                native_entries.append(entry)

        return ExportPackagePlan(
            packages=packages,
            downloader_packages=tuple(
                package for package in packages if id(package) not in handled_ids
            ),
            native_entries=tuple(native_entries),
            native_files=native_files,
        )

    @staticmethod
    def _active_export_overrides(overrides, source_mode):
        if source_mode != "solder":
            return tuple(overrides)
        return tuple(
            override
            for override in overrides
            if bool(getattr(override, "override_solder_only", True))
        )

    @classmethod
    def _modrinth_override_files(cls, build, overrides, *, http=None):
        """Resolve the newest compatible Modrinth file for every override."""
        if not overrides:
            return ()
        provider = ModrinthProvider(http=http)
        resolved = []
        for override in overrides:
            try:
                versions = provider.list_versions(
                    override.modrinth_project_id,
                    build.minecraft,
                    build.modloader,
                )
            except IntegrationError as error:
                raise PlatformExportError(
                    f'{override.name} could not be resolved on Modrinth.'
                ) from error
            if not versions:
                raise PlatformExportError(
                    f'{override.name} has no compatible Modrinth version.'
                )
            resolved.append(
                (override, cls._modrinth_release(versions[0]).modrinth)
            )
        return tuple(resolved)

    @classmethod
    def _curseforge_override_files(
        cls,
        build,
        overrides,
        *,
        http=None,
        curseforge_api_key=None,
    ):
        """Resolve the newest compatible CurseForge file for every override."""
        if not overrides:
            return ()
        provider = CurseForgeDownloaderAPI(curseforge_api_key, http=http)
        resolved = []
        for override in overrides:
            files = provider.list_files(
                override.curseforge_project_id,
                build.minecraft,
                build.modloader,
            )
            if not files:
                raise PlatformExportError(
                    f'{override.name} has no compatible CurseForge file.'
                )
            resolved.append((override, files[0]))
        return tuple(resolved)

    @classmethod
    def native_modrinth_files(cls, build, packages, *, http=None):
        """Resolve and validate exact Modrinth files for hybrid exports."""
        native_packages = [
            package for package in packages if package.is_native_modrinth
        ]
        if not native_packages:
            return {}

        provider = ModrinthProvider(http=http)
        try:
            versions = provider.get_versions(
                [
                    (
                        package.integration_project_id,
                        package.integration_version_id,
                    )
                    for package in native_packages
                ],
                build.minecraft,
                build.modloader,
            )
        except IntegrationError as error:
            raise PlatformExportError(
                "The selected Modrinth files could not be verified."
            ) from error

        native_files = {}
        for package in native_packages:
            version = versions[package.integration_version_id]
            native_file = NativeModrinthFile(
                project_id=version.project_id,
                version_id=version.version_id,
                filename=version.filename,
                download_url=version.download_url,
                sha1=version.hashes.get("sha1"),
                sha512=version.hashes.get("sha512"),
                size=version.size,
            )
            cls._validate_native_file(native_file)
            if package.integration_version_id in native_files:
                raise PlatformExportError(
                    "A Modrinth version is assigned to more than one package."
                )
            native_files[package.integration_version_id] = native_file
        return native_files

    @classmethod
    def _prism_components(cls, build):
        components = [
            {
                "uid": "net.minecraft",
                "version": str(build.minecraft),
                "important": True,
            }
        ]
        loader = normalize_modloader(build.modloader)
        if loader in {None, "VANILLA"}:
            return components
        uid = _PRISM_LOADERS.get(loader)
        if uid is None:
            raise PlatformExportError(
                "This build's modloader is not supported by Prism Launcher."
            )
        version = str(build.forge or "").strip()
        if loader != "LITELOADER":
            minecraft_prefix = f"{build.minecraft}-"
            if version.startswith(minecraft_prefix):
                version = version[len(minecraft_prefix) :]
        if not version:
            raise PlatformExportError(
                "Set the build's modloader version before exporting it."
            )
        components.append({"uid": uid, "version": version})
        return components

    @staticmethod
    def _prism_instance_config(build):
        name = "".join(
            character if ord(character) >= 32 else " "
            for character in str(build.modpack_name or "")
        ).strip()
        return (
            "ConfigVersion=1.2\n"
            "InstanceType=OneSix\n"
            f"name={name}\n"
        )

    @classmethod
    def _prism_packages(cls, build, packages, optional_groups):
        """Resolve one concrete client instance from the saved optional defaults."""
        grouped_items = {
            item.build_modversion_id: item
            for group in optional_groups or ()
            for item in group.items
        }
        selected = []
        for package in cls._actual_packages(packages):
            side = MCInstanceExport._side(package.side)
            if side == "SERVER":
                continue
            if not version_is_compatible(
                getattr(package, "minecraft", None),
                getattr(package, "modloader", None),
                build.minecraft,
                build.modloader,
            ):
                raise PlatformExportError(
                    f'Package "{package.pretty_name}" is not compatible '
                    "with the build's modloader."
                )
            grouped = grouped_items.get(
                getattr(package, "membership_id", None)
            )
            if grouped is not None:
                if grouped.selected_by_default:
                    selected.append(package)
            elif cls._optional_state(package) == 0:
                selected.append(package)
        return tuple(selected)

    @classmethod
    def render_prism(
        cls,
        build,
        packages,
        public_repo_url,
        local_repo_root,
        *,
        downloader="none",
        application_url=None,
        http=None,
        source_mode="hybrid",
        delivery=None,
        selector="build",
        optional_groups=(),
    ):
        """Create a Prism instance using a downloader or bundled files."""
        manifest = {
            "formatVersion": 1,
            "components": cls._prism_components(build),
        }
        downloader_selection = str(downloader or "").strip().lower()
        self_contained = downloader_selection == "none"
        if not downloader_selection:
            raise PlatformExportError(
                "Select a compatible mod downloader or Self-contained."
            )

        selected_packages = ()
        plan = None
        spec = None
        if self_contained:
            selected_packages = cls._prism_packages(
                build, packages, optional_groups
            )
        else:
            source_mode = cls.source_mode(source_mode, default="hybrid")
            plan = cls._package_plan(
                build,
                packages,
                source_mode,
                http=http,
            )
            spec = cls.resolve_downloader(
                downloader,
                build,
                "modrinth",
                required=True,
                http=http,
            )
            delivery = cls.config_delivery(spec, delivery)

        written_paths = {"instance.cfg", "mmc-pack.json", ".minecraft/"}
        try:
            with cls._zip_archive() as (archive, target):
                target.writestr(
                    "mmc-pack.json",
                    (json.dumps(manifest, indent=2) + "\n").encode("utf-8"),
                )
                target.writestr(
                    "instance.cfg",
                    cls._prism_instance_config(build).encode("utf-8"),
                )
                target.writestr(".minecraft/", b"")
                if self_contained:
                    for package in selected_packages:
                        MCInstanceExport._copy_package(
                            target,
                            package,
                            local_repo_root,
                            public_repo_url,
                            written_paths,
                            destination_prefixes={
                                "BOTH": ".minecraft",
                                "CLIENT": ".minecraft",
                                "SERVER": None,
                            },
                        )
                else:
                    for downloader_file in (
                        spec.modrinth,
                        *spec.modrinth_dependencies,
                    ):
                        downloader_filename, _sha1, _sha512 = (
                            cls._validate_native_file(downloader_file)
                        )
                        destination = (
                            f".minecraft/mods/{downloader_filename}"
                        )
                        if destination in written_paths:
                            raise PlatformExportError(
                                "A downloader dependency has a conflicting filename."
                            )
                        written_paths.add(destination)
                        cls._write_native_file(
                            target,
                            downloader_file,
                            destination,
                            http=http,
                        )
                    cls._write_downloader_config(
                        target,
                        spec,
                        build,
                        plan.downloader_packages,
                        public_repo_url,
                        local_repo_root,
                        application_url,
                        "mods",
                        source_mode=source_mode,
                        delivery=delivery,
                        selector=selector,
                        native_files=plan.native_files,
                        optional_groups=optional_groups,
                        archive_root=".minecraft",
                    )
        except MCInstanceExportError as error:
            raise PlatformExportError(str(error)) from error
        return archive

    @classmethod
    def render_packwiz(
        cls,
        build,
        packages,
        public_repo_url,
        *,
        source_mode="solder",
        http=None,
    ):
        """Create a self-contained ZIP of Packwiz metadata."""
        source_mode = cls.source_mode(source_mode)
        basic_packages = cls._basic_packages(packages)
        plan = cls._package_plan(
            build,
            basic_packages,
            source_mode,
            http=http,
        )
        index, _excluded = PackwizExport.index_toml(
            plan.packages,
            public_repo_url,
            native_files=plan.native_files,
        )
        pack = PackwizExport.pack_toml(build, index)
        with cls._zip_archive() as (archive, target):
            target.writestr("pack.toml", pack)
            target.writestr("index.toml", index)
            written_slugs = set()
            for package in plan.packages:
                if not package.jar_ready or package.mod_slug in written_slugs:
                    continue
                written_slugs.add(package.mod_slug)
                target.writestr(
                    f"mods/{package.mod_slug}.pw.toml",
                    PackwizExport.mod_toml(
                        package,
                        public_repo_url,
                        plan.native_files.get(package.integration_version_id),
                    ),
                )
        return archive

    @classmethod
    def render_filedirector(
        cls,
        build,
        packages,
        public_repo_url,
        *,
        source_mode="solder",
        http=None,
        optional_groups=(),
    ):
        """Create a ZIP containing a local FileDirector bundle config."""
        source_mode = cls.source_mode(source_mode)
        plan = cls._package_plan(
            build,
            packages,
            source_mode,
            http=http,
        )
        bundle = FileDirectorExport.bundle(
            plan.packages,
            public_repo_url,
            native_files=plan.native_files,
            optional_groups=optional_groups,
        )
        with cls._zip_archive() as (archive, target):
            target.writestr(
                "config/mod-director/solder.bundle.json",
                bundle,
            )
        return archive

    @classmethod
    def render_modpack_director(
        cls,
        build,
        packages,
        public_repo_url,
        application_url,
        *,
        source_mode="solder",
        delivery="bundled",
        selector="build",
        http=None,
        optional_groups=(),
    ):
        """Create a Modpack Director config archive backed by Solder."""
        source_mode = cls.source_mode(source_mode)
        delivery = cls.delivery_mode(delivery)
        if delivery == "hosted" and (
            not build.is_published or build.private
        ):
            raise PlatformExportError(
                "Hosted Modpack Director configs require a published, "
                "non-private build."
            )
        plan = (
            cls._package_plan(
                build,
                packages,
                source_mode,
                http=http,
            )
            if delivery == "bundled"
            else None
        )
        with cls._zip_archive() as (archive, target):
            target.writestr(
                "config/mod-director/modpack.json",
                cls._modpack_director_metadata(
                    build, application_url, selector
                ),
            )
            if delivery == "hosted":
                target.writestr(
                    "config/mod-director/solder.remote.json",
                    cls._remote_config(
                        build,
                        application_url,
                        "mods",
                        selector,
                        source_mode,
                        route_prefix="modpackdirector",
                    ),
                )
            else:
                target.writestr(
                    "config/mod-director/solder.bundle.json",
                    FileDirectorExport.bundle(
                        plan.packages,
                        public_repo_url,
                        native_files=plan.native_files,
                        optional_groups=optional_groups,
                    ),
                )
        return archive

    @classmethod
    def render_mrpack(
        cls,
        build,
        packages,
        downloader,
        public_repo_url,
        local_repo_root,
        application_url,
        *,
        http=None,
        source_mode="hybrid",
        delivery=None,
        selector="build",
        export_overrides=(),
        optional_groups=(),
    ):
        source_mode = cls.source_mode(source_mode, default="hybrid")
        export_overrides = cls._active_export_overrides(
            export_overrides, source_mode
        )
        override_files = cls._modrinth_override_files(
            build, tuple(export_overrides), http=http
        )
        plan = cls._package_plan(
            build,
            packages,
            source_mode,
            launcher_handles_modrinth=True,
            excluded_modrinth_projects=(
                override.modrinth_project_id
                for override, _native_file in override_files
            ),
            http=http,
        )
        files = list(plan.native_entries)
        existing_paths = {entry["path"] for entry in files}
        for override, native_file in override_files:
            entry = cls._mrpack_file_entry(native_file, override)
            if entry["path"] in existing_paths:
                raise PlatformExportError(
                    f'{override.name} conflicts with another Modrinth file.'
                )
            existing_paths.add(entry["path"])
            files.append(entry)
        spec = cls.resolve_downloader(
            downloader,
            build,
            "modrinth",
            required=bool(plan.downloader_packages),
            http=http,
        )
        if spec is not None:
            delivery = cls.config_delivery(spec, delivery)
            downloader_entry = cls._mrpack_file_entry(spec.modrinth)
            if downloader_entry["path"] in existing_paths:
                raise PlatformExportError(
                    "The downloader conflicts with another Modrinth file."
                )
            existing_paths.add(downloader_entry["path"])
            files.append(downloader_entry)
            for dependency in spec.modrinth_dependencies:
                dependency_entry = cls._mrpack_file_entry(dependency)
                if dependency_entry["path"] in existing_paths:
                    raise PlatformExportError(
                        "A downloader dependency conflicts with another Modrinth file."
                    )
                existing_paths.add(dependency_entry["path"])
                files.append(dependency_entry)

        index = {
            "formatVersion": 1,
            "game": "minecraft",
            "versionId": str(build.version),
            "name": str(build.modpack_name),
            "summary": f"Exported from {build.modpack_name} by solder.py",
            "files": files,
            "dependencies": cls._mrpack_dependencies(build),
        }
        with cls._zip_archive() as (archive, target):
            target.writestr(
                "modrinth.index.json",
                (json.dumps(index, indent=2) + "\n").encode("utf-8"),
            )
            if spec is not None:
                cls._write_downloader_config(
                    target,
                    spec,
                    build,
                    plan.downloader_packages,
                    public_repo_url,
                    local_repo_root,
                    application_url,
                    "modrinth-fallback",
                    source_mode=source_mode,
                    delivery=delivery,
                    selector=selector,
                    native_files=plan.native_files,
                    optional_groups=optional_groups,
                )
        return archive

    @classmethod
    def render_curseforge(
        cls,
        build,
        packages,
        downloader,
        public_repo_url,
        local_repo_root,
        application_url,
        *,
        http=None,
        curseforge_api_key=None,
        source_mode="hybrid",
        delivery=None,
        selector="build",
        export_overrides=(),
        optional_groups=(),
    ):
        source_mode = cls.source_mode(source_mode, default="hybrid")
        export_overrides = cls._active_export_overrides(
            export_overrides, source_mode
        )
        override_files = cls._curseforge_override_files(
            build,
            export_overrides,
            http=http,
            curseforge_api_key=curseforge_api_key,
        )
        spec = cls.resolve_downloader(
            downloader,
            build,
            "curseforge",
            required=True,
            http=http,
            curseforge_api_key=curseforge_api_key,
        )
        delivery = cls.config_delivery(spec, delivery)
        plan = cls._package_plan(
            build,
            packages,
            source_mode,
            excluded_modrinth_projects=(
                override.modrinth_project_id
                for override in export_overrides
            ),
            http=http,
        )
        loader = normalize_modloader(build.modloader)
        if loader not in spec.downloader.supported_loaders:
            raise PlatformExportError(
                f"{spec.label} does not support this build's modloader."
            )
        curseforge_loader = {
            "FORGE": "forge",
            "NEOFORGE": "neoforge",
        }.get(loader)
        if curseforge_loader is None:
            raise PlatformExportError(
                "This build's modloader is not supported by CurseForge export."
            )

        manifest_files = [
            {
                "projectID": spec.curseforge_project_id,
                "fileID": spec.curseforge_file_id,
                "required": True,
            }
        ]
        selected_projects = {spec.curseforge_project_id: spec.curseforge_file_id}
        for dependency in spec.curseforge_dependencies:
            previous_file = selected_projects.get(dependency.project_id)
            if previous_file is not None:
                if previous_file == dependency.file_id:
                    continue
                raise PlatformExportError(
                    "A required downloader dependency conflicts with the "
                    "selected downloader."
                )
            selected_projects[dependency.project_id] = dependency.file_id
            manifest_files.append(
                {
                    "projectID": dependency.project_id,
                    "fileID": dependency.file_id,
                    "required": True,
                }
            )
        for override, file in override_files:
            previous_file = selected_projects.get(file.project_id)
            if previous_file is not None:
                if previous_file == file.file_id:
                    continue
                raise PlatformExportError(
                    f'{override.name} conflicts with the selected downloader.'
                )
            selected_projects[file.project_id] = file.file_id
            manifest_files.append(
                {
                    "projectID": file.project_id,
                    "fileID": file.file_id,
                    "required": True,
                }
            )

        manifest = {
            "minecraft": {
                "version": str(build.minecraft),
                "modLoaders": [
                    {
                        "id": f"{curseforge_loader}-{cls._loader_version(build)}",
                        "primary": True,
                    }
                ],
            },
            "manifestType": "minecraftModpack",
            "manifestVersion": 1,
            "name": str(build.modpack_name),
            "version": str(build.version),
            "author": "solder.py",
            "files": manifest_files,
            "overrides": "overrides",
        }
        with cls._zip_archive() as (archive, target):
            target.writestr(
                "manifest.json",
                (json.dumps(manifest, indent=2) + "\n").encode("utf-8"),
            )
            target.writestr("overrides/", b"")
            cls._write_downloader_config(
                target,
                spec,
                build,
                plan.downloader_packages,
                public_repo_url,
                local_repo_root,
                application_url,
                "mods",
                source_mode=source_mode,
                delivery=delivery,
                selector=selector,
                native_files=plan.native_files,
                optional_groups=optional_groups,
            )
        return archive
