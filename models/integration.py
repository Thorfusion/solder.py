"""CurseForge and Modrinth management-side mod integration."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata
from urllib.parse import urljoin, urlparse
import zipfile

import requests
from werkzeug.utils import secure_filename

from .compatibility import normalize_modloader
from .database import Database
from .mod import DuplicateModError, Mod
from .modversion import Modversion


MODRINTH = "MODRINTH"
CURSEFORGE = "CURSEFORGE"
SUPPORTED_PROVIDERS = (MODRINTH, CURSEFORGE)
SUPPORTED_LOADERS = (
    "FORGE",
    "NEOFORGE",
    "FABRIC",
    "QUILT",
    "LITELOADER",
)
MAX_INTEGRATION_FILE_SIZE = 512 * 1024 * 1024
REQUEST_TIMEOUT = (5, 30)
DOWNLOAD_TIMEOUT = (5, 120)
USER_AGENT = "solder.py/1.7.4 (+https://github.com/Thorfusion/solder.py)"


class IntegrationError(ValueError):
    """Raised when a provider operation cannot safely be completed."""


@dataclass(frozen=True)
class ExternalProject:
    provider: str
    project_id: str
    slug: str
    title: str
    description: str
    author: str
    link: str
    icon_url: str | None = None
    license: str | None = None
    side: str = "BOTH"
    distribution_allowed: bool = True
    available: bool = True


@dataclass(frozen=True)
class ExternalVersion:
    provider: str
    project_id: str
    version_id: str
    name: str
    version_number: str
    game_versions: tuple[str, ...]
    loaders: tuple[str, ...]
    release_type: str
    date_published: str | None
    filename: str
    download_url: str | None
    hashes: dict[str, str]
    size: int
    dependencies: tuple[str, ...] = ()

    def loader_for_build(self, build_loader):
        normalized_build = normalize_modloader(build_loader)
        normalized_loaders = {
            loader.upper() for loader in self.loaders if loader
        }
        if normalized_build:
            return normalized_build if normalized_build in normalized_loaders else None
        return next(
            (loader for loader in SUPPORTED_LOADERS if loader in normalized_loaders),
            None,
        )

    def management_json(self):
        return {
            "id": self.version_id,
            "name": self.name,
            "version": self.version_number,
            "minecraft": list(self.game_versions),
            "loaders": list(self.loaders),
            "release_type": self.release_type,
            "published": self.date_published,
            "filename": self.filename,
            "size": self.size,
        }


@dataclass(frozen=True)
class MaterializedVersion:
    version: Modversion
    created: bool


class IntegrationCredential:
    """Store a provider API key for the management user who supplied it."""

    @staticmethod
    def get(user_id, provider):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT api_key FROM integration_credentials
                   WHERE user_id = %s AND provider = %s""",
                (user_id, normalize_provider(provider)),
            )
            row = cur.fetchone()
            return row["api_key"] if row else None
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def set(user_id, provider, api_key):
        provider = normalize_provider(provider)
        api_key = str(api_key or "").strip()
        if provider != CURSEFORGE:
            raise IntegrationError("This provider does not require an API key.")
        if not api_key or len(api_key) > 512:
            raise IntegrationError("Enter a valid CurseForge API key.")

        conn = Database.get_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                """INSERT INTO integration_credentials (user_id, provider, api_key)
                   VALUES (%s, %s, %s)
                   ON DUPLICATE KEY UPDATE api_key = VALUES(api_key)""",
                (user_id, provider, api_key),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def delete(user_id, provider):
        conn = Database.get_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                "DELETE FROM integration_credentials WHERE user_id = %s AND provider = %s",
                (user_id, normalize_provider(provider)),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()


def normalize_provider(provider):
    provider = str(provider or "").strip().upper()
    if provider not in SUPPORTED_PROVIDERS:
        raise IntegrationError("Unknown mod integration provider.")
    return provider


def external_id(value):
    value = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
        raise IntegrationError("The provider returned an invalid identifier.")
    return value


def _side_from_support(client_side, server_side):
    client_side = str(client_side or "unknown").lower()
    server_side = str(server_side or "unknown").lower()
    client_supported = client_side not in {"unsupported", "server_only"}
    server_supported = server_side not in {"unsupported", "client_only"}
    if client_supported and not server_supported:
        return "CLIENT"
    if server_supported and not client_supported:
        return "SERVER"
    return "BOTH"


class ExternalProvider:
    provider = ""
    base_url = ""

    def __init__(self, api_key=None, http=None):
        self.api_key = api_key
        self.http = http or requests.Session()

    def _headers(self):
        return {"Accept": "application/json", "User-Agent": USER_AGENT}

    def _request_json(self, path, *, params=None):
        try:
            response = self.http.get(
                f"{self.base_url}{path}",
                params=params,
                headers=self._headers(),
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as error:
            raise IntegrationError(
                f"{self.provider.title()} could not be reached."
            ) from error

        try:
            if response.status_code in {401, 403}:
                raise IntegrationError(
                    f"{self.provider.title()} rejected the configured API key."
                )
            response.raise_for_status()
            payload = response.json()
        except IntegrationError:
            raise
        except (requests.RequestException, ValueError) as error:
            raise IntegrationError(
                f"{self.provider.title()} returned an invalid response."
            ) from error
        finally:
            response.close()
        return payload

    def validate_credentials(self):
        return True

    def resolve_download_url(self, version):
        return version.download_url

    def _download_host_allowed(self, hostname):
        raise NotImplementedError

    def _validate_download_url(self, url):
        parsed = urlparse(str(url or ""))
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not hostname or not self._download_host_allowed(hostname):
            raise IntegrationError(
                f"{self.provider.title()} returned an untrusted download URL."
            )

    def download(self, version, destination):
        url = self.resolve_download_url(version)
        if not url:
            raise IntegrationError("The provider did not supply a download URL.")

        response = None
        for _ in range(6):
            self._validate_download_url(url)
            try:
                response = self.http.get(
                    url,
                    headers={"User-Agent": USER_AGENT},
                    stream=True,
                    allow_redirects=False,
                    timeout=DOWNLOAD_TIMEOUT,
                )
            except requests.RequestException as error:
                raise IntegrationError("The provider file download failed.") from error

            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise IntegrationError("The provider returned an invalid redirect.")
                url = urljoin(url, location)
                continue
            break
        else:
            raise IntegrationError("The provider returned too many download redirects.")

        try:
            response.raise_for_status()
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > MAX_INTEGRATION_FILE_SIZE:
                raise IntegrationError("The provider file exceeds the 512 MiB limit.")

            digests = {
                "sha512": hashlib.sha512(),
                # SHA-1 and MD5 are compatibility checks against provider
                # metadata. SHA-512 is preferred whenever it is available.
                "sha1": hashlib.sha1(usedforsecurity=False),
                "md5": hashlib.md5(usedforsecurity=False),
            }
            downloaded = 0
            with open(destination, "wb") as target:
                for chunk in response.iter_content(1024 * 1024):
                    if not chunk:
                        continue
                    downloaded += len(chunk)
                    if downloaded > MAX_INTEGRATION_FILE_SIZE:
                        raise IntegrationError(
                            "The provider file exceeds the 512 MiB limit."
                        )
                    for digest in digests.values():
                        digest.update(chunk)
                    target.write(chunk)

            if version.size and downloaded != version.size:
                raise IntegrationError("The provider download size did not match its metadata.")

            verified = False
            for algorithm in ("sha512", "sha1", "md5"):
                expected = str(version.hashes.get(algorithm, "")).lower()
                if not expected:
                    continue
                verified = True
                if not hmac.compare_digest(digests[algorithm].hexdigest(), expected):
                    raise IntegrationError(
                        f"The downloaded file failed {algorithm.upper()} verification."
                    )
                break
            if not verified:
                raise IntegrationError("The provider supplied no supported file hash.")

            try:
                with zipfile.ZipFile(destination, "r") as jar:
                    entries = jar.infolist()
                    if not entries or len(entries) > 100000:
                        raise IntegrationError("The downloaded JAR has an invalid file table.")
            except zipfile.BadZipFile as error:
                raise IntegrationError("The downloaded file is not a valid JAR.") from error

            return downloaded, digests["md5"].hexdigest()
        except (OSError, requests.RequestException, ValueError) as error:
            if isinstance(error, IntegrationError):
                raise
            raise IntegrationError("The provider file download failed.") from error
        finally:
            response.close()


class ModrinthProvider(ExternalProvider):
    provider = MODRINTH
    base_url = "https://api.modrinth.com/v2"

    def _download_host_allowed(self, hostname):
        return hostname == "cdn.modrinth.com" or hostname.endswith(".modrinth.com")

    @staticmethod
    def _project(payload):
        environment = payload.get("environment") or []
        if environment:
            client_side = "unsupported" if all(
                value in {"server_only", "dedicated_server_only"}
                for value in environment
            ) else "required"
            server_side = "unsupported" if all(
                value in {"client_only", "singleplayer_only"}
                for value in environment
            ) else "required"
        else:
            client_side = payload.get("client_side")
            server_side = payload.get("server_side")
        license_data = payload.get("license")
        if isinstance(license_data, dict):
            license_name = license_data.get("id") or license_data.get("name")
        else:
            license_name = license_data
        return ExternalProject(
            provider=MODRINTH,
            project_id=str(payload.get("project_id") or payload.get("id") or ""),
            slug=str(payload.get("slug") or payload.get("id") or ""),
            title=str(payload.get("title") or payload.get("name") or "Unnamed project"),
            description=str(payload.get("description") or ""),
            author=str(payload.get("author") or payload.get("organization") or "Modrinth"),
            link=f"https://modrinth.com/mod/{payload.get('slug') or payload.get('id')}",
            icon_url=payload.get("icon_url"),
            license=str(license_name) if license_name else None,
            side=_side_from_support(client_side, server_side),
            available=str(payload.get("status", "approved")) not in {"rejected", "withheld"},
            distribution_allowed=True,
            )

    def search(self, query, limit=30):
        payload = self._request_json(
            "/search",
            params={
                "query": str(query or "").strip(),
                "facets": json.dumps([["project_type:mod"]]),
                "limit": min(max(int(limit), 1), 100),
            },
        )
        return [self._project(item) for item in payload.get("hits", [])]

    def get_project(self, project_id):
        project_id = external_id(project_id)
        payload = self._request_json(f"/project/{project_id}")
        if payload.get("project_type") != "mod":
            raise IntegrationError("The selected Modrinth project is not a mod.")
        project = self._project(payload)
        if project.project_id != project_id:
            raise IntegrationError("Modrinth returned a different project.")
        return project

    @staticmethod
    def _version(payload):
        files = [
            file_data for file_data in payload.get("files", [])
            if str(file_data.get("filename", "")).lower().endswith(".jar")
            and file_data.get("file_type") not in {"sources-jar", "dev-jar", "javadoc-jar"}
        ]
        if not files:
            raise IntegrationError("This Modrinth version has no installable JAR.")
        file_data = next(
            (candidate for candidate in files if candidate.get("primary")),
            files[0],
        )
        return ExternalVersion(
            provider=MODRINTH,
            project_id=str(payload.get("project_id") or ""),
            version_id=str(payload.get("id") or ""),
            name=str(payload.get("name") or payload.get("version_number") or "Unnamed version"),
            version_number=str(payload.get("version_number") or payload.get("id") or "version"),
            game_versions=tuple(str(value) for value in payload.get("game_versions", [])),
            loaders=tuple(str(value).upper() for value in payload.get("loaders", [])),
            release_type=str(payload.get("version_type") or "release"),
            date_published=payload.get("date_published"),
            filename=str(file_data.get("filename") or "mod.jar"),
            download_url=file_data.get("url"),
            hashes={
                str(key).lower(): str(value).lower()
                for key, value in (file_data.get("hashes") or {}).items()
            },
            size=int(file_data.get("size") or 0),
            dependencies=tuple(
                str(dependency.get("project_id"))
                for dependency in payload.get("dependencies", [])
                if dependency.get("dependency_type") == "required"
                and dependency.get("project_id")
            ),
        )

    def list_versions(self, project_id, minecraft, modloader=None):
        project_id = external_id(project_id)
        params = {
            "game_versions": json.dumps([str(minecraft)]),
            "include_changelog": "false",
        }
        normalized_loader = normalize_modloader(modloader)
        if normalized_loader:
            params["loaders"] = json.dumps([normalized_loader.lower()])
        payload = self._request_json(f"/project/{project_id}/version", params=params)
        versions = []
        for item in payload:
            try:
                version = self._version(item)
            except IntegrationError:
                continue
            if str(minecraft) not in version.game_versions:
                continue
            if normalized_loader and normalized_loader not in version.loaders:
                continue
            versions.append(version)
        return versions

    def get_version(self, project_id, version_id, minecraft, modloader=None):
        project_id = external_id(project_id)
        version_id = external_id(version_id)
        version = self._version(self._request_json(f"/version/{version_id}"))
        if version.project_id != str(project_id):
            raise IntegrationError("The selected version belongs to another project.")
        if str(minecraft) not in version.game_versions:
            raise IntegrationError("The selected version does not support this Minecraft version.")
        normalized_loader = normalize_modloader(modloader)
        if normalized_loader and normalized_loader not in version.loaders:
            raise IntegrationError("The selected version does not support this modloader.")
        return version


class CurseForgeProvider(ExternalProvider):
    provider = CURSEFORGE
    base_url = "https://api.curseforge.com/v1"
    minecraft_game_id = 432
    minecraft_mod_class_id = 6
    loader_types = {
        "FORGE": 1,
        "LITELOADER": 3,
        "FABRIC": 4,
        "QUILT": 5,
        "NEOFORGE": 6,
    }

    def __init__(self, api_key=None, http=None):
        if not str(api_key or "").strip():
            raise IntegrationError("Add your CurseForge API key before using CurseForge.")
        super().__init__(str(api_key).strip(), http=http)

    def _headers(self):
        headers = super()._headers()
        headers["x-api-key"] = self.api_key
        return headers

    def _download_host_allowed(self, hostname):
        return hostname == "forgecdn.net" or hostname.endswith(".forgecdn.net")

    def validate_credentials(self):
        payload = self._request_json(f"/games/{self.minecraft_game_id}")
        return int(payload.get("data", {}).get("id", 0)) == self.minecraft_game_id

    @staticmethod
    def _project(payload):
        authors = payload.get("authors") or []
        logo = payload.get("logo") or {}
        slug = str(payload.get("slug") or payload.get("id") or "")
        return ExternalProject(
            provider=CURSEFORGE,
            project_id=str(payload.get("id") or ""),
            slug=slug,
            title=str(payload.get("name") or "Unnamed project"),
            description=str(payload.get("summary") or ""),
            author=", ".join(
                str(author.get("name")) for author in authors if author.get("name")
            ) or "CurseForge",
            link=f"https://www.curseforge.com/minecraft/mc-mods/{slug}",
            icon_url=logo.get("thumbnailUrl") or logo.get("url"),
            license=None,
            side="BOTH",
            distribution_allowed=payload.get("allowModDistribution") is True,
            available=payload.get("isAvailable") is True,
        )

    def search(self, query, limit=30):
        payload = self._request_json(
            "/mods/search",
            params={
                "gameId": self.minecraft_game_id,
                "classId": self.minecraft_mod_class_id,
                "searchFilter": str(query or "").strip(),
                "pageSize": min(max(int(limit), 1), 50),
                "sortField": 2,
                "sortOrder": "desc",
            },
        )
        return [self._project(item) for item in payload.get("data", [])]

    def get_project(self, project_id):
        project_id = external_id(project_id)
        payload = self._request_json(f"/mods/{project_id}").get("data") or {}
        if int(payload.get("gameId", 0)) != self.minecraft_game_id:
            raise IntegrationError("The selected CurseForge project is not for Minecraft.")
        if int(payload.get("classId") or 0) != self.minecraft_mod_class_id:
            raise IntegrationError("The selected CurseForge project is not a mod.")
        project = self._project(payload)
        if project.project_id != project_id:
            raise IntegrationError("CurseForge returned a different project.")
        return project

    @classmethod
    def _version(cls, payload):
        hashes = {}
        for item in payload.get("hashes", []):
            algorithm = {1: "sha1", 2: "md5"}.get(item.get("algo"))
            if algorithm and item.get("value"):
                hashes[algorithm] = str(item["value"]).lower()
        game_versions = tuple(str(value) for value in payload.get("gameVersions", []))
        loaders = tuple(
            loader for loader in SUPPORTED_LOADERS
            if loader.lower() in {value.lower() for value in game_versions}
        )
        release_type = {1: "release", 2: "beta", 3: "alpha"}.get(
            payload.get("releaseType"), "release"
        )
        return ExternalVersion(
            provider=CURSEFORGE,
            project_id=str(payload.get("modId") or ""),
            version_id=str(payload.get("id") or ""),
            name=str(payload.get("displayName") or payload.get("fileName") or "Unnamed version"),
            version_number=str(payload.get("displayName") or payload.get("id") or "version"),
            game_versions=game_versions,
            loaders=loaders,
            release_type=release_type,
            date_published=payload.get("fileDate"),
            filename=str(payload.get("fileName") or "mod.jar"),
            download_url=payload.get("downloadUrl"),
            hashes=hashes,
            size=int(payload.get("fileLength") or payload.get("fileSizeOnDisk") or 0),
            dependencies=tuple(
                str(dependency.get("modId"))
                for dependency in payload.get("dependencies", [])
                if dependency.get("relationType") == 3 and dependency.get("modId")
            ),
        )

    def list_versions(self, project_id, minecraft, modloader=None):
        project_id = external_id(project_id)
        params = {"gameVersion": str(minecraft), "pageSize": 50}
        normalized_loader = normalize_modloader(modloader)
        if normalized_loader:
            loader_type = self.loader_types.get(normalized_loader)
            if loader_type is None:
                return []
            params["modLoaderType"] = loader_type
        payload = self._request_json(f"/mods/{project_id}/files", params=params)
        versions = []
        for item in payload.get("data", []):
            if item.get("isAvailable") is False:
                continue
            version = self._version(item)
            if str(minecraft) not in version.game_versions:
                continue
            if normalized_loader and normalized_loader not in version.loaders:
                continue
            if not version.filename.lower().endswith(".jar"):
                continue
            versions.append(version)
        return versions

    def get_version(self, project_id, version_id, minecraft, modloader=None):
        project_id = external_id(project_id)
        version_id = external_id(version_id)
        payload = self._request_json(
            f"/mods/{project_id}/files/{version_id}"
        ).get("data") or {}
        version = self._version(payload)
        if version.project_id != str(project_id):
            raise IntegrationError("The selected version belongs to another project.")
        if str(minecraft) not in version.game_versions:
            raise IntegrationError("The selected version does not support this Minecraft version.")
        normalized_loader = normalize_modloader(modloader)
        if normalized_loader and normalized_loader not in version.loaders:
            raise IntegrationError("The selected version does not support this modloader.")
        if not version.filename.lower().endswith(".jar"):
            raise IntegrationError("The selected CurseForge file is not a JAR.")
        return version

    def resolve_download_url(self, version):
        if version.download_url:
            return version.download_url
        payload = self._request_json(
            f"/mods/{version.project_id}/files/{version.version_id}/download-url"
        )
        return payload.get("data")


def provider_for_user(provider, user_id, *, http=None):
    provider = normalize_provider(provider)
    if provider == MODRINTH:
        return ModrinthProvider(http=http)
    return CurseForgeProvider(
        IntegrationCredential.get(user_id, CURSEFORGE),
        http=http,
    )


class ModIntegration:
    @staticmethod
    def _slug(value):
        value = unicodedata.normalize("NFKD", str(value or ""))
        value = value.encode("ascii", "ignore").decode("ascii")
        value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
        if not value:
            raise IntegrationError("The provider project has no usable mod slug.")
        return value[:255]

    @classmethod
    def import_project(cls, provider_name, project_id, user_id, *, http=None):
        provider = provider_for_user(provider_name, user_id, http=http)
        project = provider.get_project(external_id(project_id))
        if not project.available:
            raise IntegrationError("This provider project is not currently available.")
        if not project.distribution_allowed:
            raise IntegrationError(
                "This project does not allow third-party distribution."
            )

        existing = Mod.get_by_integration(project.provider, project.project_id)
        if existing:
            return existing, False

        slug = cls._slug(project.slug or project.title)
        try:
            mod = Mod.new(
                slug,
                project.description[:255],
                project.author[:255],
                project.link[:255],
                project.title[:255],
                project.side,
                "MOD",
                f"Managed by {project.provider.title()} project {project.project_id}",
                integration_provider=project.provider,
                integration_project_id=project.project_id,
            )
        except DuplicateModError as error:
            existing = Mod.get_by_integration(
                project.provider, project.project_id
            )
            if existing:
                return existing, False
            raise IntegrationError(
                f'The local mod slug "{slug}" is already in use.'
            ) from error
        return mod, True

    @staticmethod
    def list_versions(mod, build, user_id, *, http=None):
        if not mod.integration_provider or not mod.integration_project_id:
            return []
        provider = provider_for_user(mod.integration_provider, user_id, http=http)
        project = provider.get_project(mod.integration_project_id)
        if not project.available or not project.distribution_allowed:
            raise IntegrationError(
                "This provider no longer permits this project to be imported."
            )
        return provider.list_versions(
            mod.integration_project_id,
            build.minecraft,
            build.modloader,
        )

    @staticmethod
    def _local_version(mod, external_version, minecraft):
        raw = f"{minecraft}-{external_version.version_number}"
        version = secure_filename(raw).replace("_", "-").strip("-.")
        if not version:
            version = f"external-{secure_filename(external_version.version_id)}"
        # Keep both <slug>-<version>.jar and .zip within the common 255-byte
        # filesystem component limit. Imported slugs and versions are ASCII.
        maximum_length = 250 - len(mod.name)
        if maximum_length < 14:
            raise IntegrationError(
                "The managed mod slug is too long to create repository files."
            )
        version = version[:maximum_length]
        if Modversion.version_exists(mod.id, version):
            suffix = hashlib.sha256(
                external_version.version_id.encode("utf-8")
            ).hexdigest()[:12]
            version = f"{version[:maximum_length - 13]}-{suffix}"
        return version

    @classmethod
    def materialize(
        cls,
        mod,
        build,
        integration_version_id,
        user_id,
        upload_folder,
        *,
        r2_client=None,
        r2_bucket=None,
        http=None,
    ):
        if not mod.integration_provider or not mod.integration_project_id:
            raise IntegrationError("This mod is not managed by an integration.")
        if not mod.name or secure_filename(mod.name) != mod.name:
            raise IntegrationError(
                "The managed mod slug contains unsafe filename characters."
            )

        existing = Modversion.get_by_integration(mod.id, integration_version_id)
        if existing:
            return MaterializedVersion(existing, False)

        provider = provider_for_user(mod.integration_provider, user_id, http=http)
        project = provider.get_project(mod.integration_project_id)
        if not project.available or not project.distribution_allowed:
            raise IntegrationError(
                "This provider no longer permits this project to be imported."
            )
        external = provider.get_version(
            mod.integration_project_id,
            str(integration_version_id),
            build.minecraft,
            build.modloader,
        )
        selected_loader = external.loader_for_build(build.modloader)
        if build.modloader and selected_loader is None:
            raise IntegrationError("The selected version does not support this modloader.")

        version_name = cls._local_version(mod, external, build.minecraft)
        destination_folder = Path(upload_folder, mod.name)
        destination_folder.mkdir(parents=True, exist_ok=True)
        jar_filename = f"{mod.name}-{version_name}.jar"
        zip_filename = f"{mod.name}-{version_name}.zip"
        final_jar = destination_folder / jar_filename
        final_zip = destination_folder / zip_filename

        uploaded_keys = []
        try:
            with tempfile.TemporaryDirectory(
                prefix=".solder-integration-", dir=destination_folder
            ) as staging_directory:
                staged_jar = Path(staging_directory, jar_filename)
                staged_zip = Path(staging_directory, zip_filename)
                _downloaded_size, jar_md5 = provider.download(
                    external, staged_jar
                )
                with zipfile.ZipFile(
                    staged_zip, "w", compression=zipfile.ZIP_STORED
                ) as package:
                    package.write(staged_jar, f"mods/{jar_filename}")
                package_md5 = Mod.file_md5(staged_zip)
                package_size = staged_zip.stat().st_size

                os.replace(staged_jar, final_jar)
                os.replace(staged_zip, final_zip)

            if r2_client is not None and r2_bucket:
                for filename, content_type, path in (
                    (zip_filename, "application/zip", final_zip),
                    (jar_filename, "application/java-archive", final_jar),
                ):
                    key = f"mods/{mod.name}/{filename}"
                    r2_client.upload_file(
                        str(path),
                        r2_bucket,
                        key,
                        ExtraArgs={"ContentType": content_type},
                    )
                    uploaded_keys.append(key)

            version = Modversion.new(
                mod.id,
                version_name,
                build.minecraft,
                package_md5,
                package_size,
                "0",
                "0",
                jar_md5,
                modloader=selected_loader,
                integration_version_id=external.version_id,
            )
            return MaterializedVersion(version, True)
        except Exception:
            raced = Modversion.get_by_integration(mod.id, integration_version_id)
            if raced:
                return MaterializedVersion(raced, False)
            final_zip.unlink(missing_ok=True)
            final_jar.unlink(missing_ok=True)
            if r2_client is not None and r2_bucket:
                for key in uploaded_keys:
                    try:
                        r2_client.delete_object(Bucket=r2_bucket, Key=key)
                    except Exception:
                        pass
            raise
