"""Modrinth management-side mod integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import tempfile
from types import SimpleNamespace
import unicodedata
from urllib.parse import urljoin, urlparse
import zipfile

import requests
from werkzeug.utils import secure_filename

from .compatibility import (
    compatibility_values,
    minecraft_version_storage,
    normalize_minecraft_versions,
    normalize_modloader,
    normalize_modloaders,
)
from .database import Database
from .github_config import (
    GitHubClient,
    GitHubConfigError,
    GitHubConfigPack,
    github_repository_reference,
)
from .maven import (
    MAVEN,
    MavenArtifact,
    MavenCatalog,
    MavenError,
    MavenMetadataClient,
    MavenRepository,
    MavenVersion,
    artifact_file_url,
)
from .mod import DuplicateModError, Mod
from .mod_dependency import DependencyError, ModDependency
from .modversion import Modversion


logger = logging.getLogger(__name__)


MODRINTH = "MODRINTH"
GITHUB = "GITHUB"
SUPPORTED_PROVIDERS = (MODRINTH, MAVEN, GITHUB)
SUPPORTED_LOADERS = (
    "FORGE",
    "NEOFORGE",
    "FABRIC",
    "QUILT",
    "LITELOADER",
)
MAX_INTEGRATION_FILE_SIZE = 512 * 1024 * 1024
MAX_MODRINTH_DEPENDENCIES_PER_VERSION = 128
MAX_MODRINTH_DEPENDENCY_PROJECTS = 256
MAX_MODRINTH_DEPENDENCY_DEPTH = 32
REQUEST_TIMEOUT = (5, 30)
DOWNLOAD_TIMEOUT = (5, 120)
USER_AGENT = "solder.py/1.10.1 (+https://github.com/Thorfusion/solder.py)"


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
class ExternalDependency:
    project_id: str | None
    version_id: str | None = None


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
    dependencies: tuple[ExternalDependency, ...] = ()
    integration_label: str | None = None

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
            "integration_label": self.integration_label,
        }


@dataclass(frozen=True)
class MaterializedVersion:
    version: Modversion
    created: bool


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
    require_download_hash = True

    def __init__(self, http=None):
        self.http = http or requests.Session()

    def _headers(self):
        return {"Accept": "application/json", "User-Agent": USER_AGENT}

    def _request_json(self, path, *, params=None):
        try:
            response = self.http.get(
                f"{self.base_url}{path}",
                params=params,
                headers=self._headers(),
                allow_redirects=False,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as error:
            raise IntegrationError(
                f"{self.provider.title()} could not be reached."
            ) from error

        try:
            if 300 <= response.status_code < 400:
                raise IntegrationError(
                    f"{self.provider.title()} returned an unexpected redirect."
                )
            if response.status_code in {401, 403}:
                raise IntegrationError(
                    f"{self.provider.title()} rejected the request."
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

    def resolve_download_url(self, version):
        return version.download_url

    def list_all_versions(self, project_id):
        raise NotImplementedError

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
                "sha256": hashlib.sha256(),
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
            for algorithm in ("sha512", "sha256", "sha1", "md5"):
                expected = str(version.hashes.get(algorithm, "")).lower()
                if not expected:
                    continue
                verified = True
                if not hmac.compare_digest(digests[algorithm].hexdigest(), expected):
                    raise IntegrationError(
                        f"The downloaded file failed {algorithm.upper()} verification."
                    )
                break
            if not verified and self.require_download_hash:
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
    def _project(payload, author=None):
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
            author=str(
                author
                or payload.get("author")
                or ""
            ),
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
        project_id = self.project_reference(project_id)
        payload = self._request_json(f"/project/{project_id}")
        if payload.get("project_type") != "mod":
            raise IntegrationError("The selected Modrinth project is not a mod.")
        members = self._request_json(f"/project/{project_id}/members")
        members = sorted(
            (
                member for member in members
                if member.get("accepted") is not False
            ),
            key=lambda member: int(member.get("ordering") or 0),
        )
        names = []
        for member in members:
            user = member.get("user") or {}
            name = str(user.get("name") or user.get("username") or "").strip()
            if name and name not in names:
                names.append(name)
        if not names:
            raise IntegrationError(
                "Modrinth did not return any accepted project team members."
            )
        author = ", ".join(names)
        project = self._project(payload, author=author)
        if project.project_id != project_id and project.slug != project_id:
            raise IntegrationError("Modrinth returned a different project.")
        return project

    @staticmethod
    def project_reference(value):
        value = str(value or "").strip()
        if "://" in value:
            parsed = urlparse(value)
            if (
                parsed.scheme != "https"
                or (parsed.hostname or "").lower()
                not in {"modrinth.com", "www.modrinth.com"}
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
            ):
                raise IntegrationError("Enter a normal HTTPS Modrinth project URL.")
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) != 2 or parts[0] not in {"mod", "project"}:
                raise IntegrationError("Enter a Modrinth mod project URL.")
            value = parts[1]
        return external_id(value)

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
                ExternalDependency(
                    project_id=(
                        str(dependency.get("project_id"))
                        if dependency.get("project_id")
                        else None
                    ),
                    version_id=(
                        str(dependency.get("version_id"))
                        if dependency.get("version_id")
                        else None
                    ),
                )
                for dependency in payload.get("dependencies", [])
                if dependency.get("dependency_type") == "required"
                and (
                    dependency.get("project_id")
                    or dependency.get("version_id")
                )
            ),
        )

    @staticmethod
    def _published_at(version):
        """Return a stable UTC sort key for Modrinth's publication date."""
        if not version.date_published:
            return datetime.min.replace(tzinfo=timezone.utc)
        try:
            published = datetime.fromisoformat(
                str(version.date_published).replace("Z", "+00:00")
            )
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        return published.astimezone(timezone.utc)

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
        versions.sort(
            key=lambda version: (
                self._published_at(version),
                version.version_id,
            ),
            reverse=True,
        )
        return versions

    def list_all_versions(self, project_id):
        project_id = external_id(project_id)
        payload = self._request_json(
            f"/project/{project_id}/version",
            params={"include_changelog": "false"},
        )
        versions = []
        for item in payload:
            try:
                versions.append(self._version(item))
            except IntegrationError:
                continue
        versions.sort(
            key=lambda version: (
                self._published_at(version),
                version.version_id,
            ),
            reverse=True,
        )
        return versions

    def get_version_by_id(self, version_id, minecraft, modloader=None):
        """Resolve a version when Modrinth omits its dependency project ID."""
        version_id = external_id(version_id)
        version = self._version(self._request_json(f"/version/{version_id}"))
        if str(minecraft) not in version.game_versions:
            raise IntegrationError("The selected version does not support this Minecraft version.")
        normalized_loader = normalize_modloader(modloader)
        if normalized_loader and normalized_loader not in version.loaders:
            raise IntegrationError("The selected version does not support this modloader.")
        return version

    def get_version(self, project_id, version_id, minecraft, modloader=None):
        project_id = external_id(project_id)
        version = self.get_version_by_id(version_id, minecraft, modloader)
        if version.project_id != str(project_id):
            raise IntegrationError("The selected version belongs to another project.")
        return version

    def get_versions(self, project_versions, minecraft, modloader=None):
        """Resolve exact project/version pairs with Modrinth's batch endpoint."""
        requested = {}
        for project_id, version_id in project_versions:
            project_id = external_id(project_id)
            version_id = external_id(version_id)
            previous = requested.get(version_id)
            if previous is not None and previous != project_id:
                raise IntegrationError(
                    "One Modrinth version was linked to multiple projects."
                )
            requested[version_id] = project_id

        resolved = {}
        version_ids = list(requested)
        for offset in range(0, len(version_ids), 100):
            chunk = version_ids[offset : offset + 100]
            payload = self._request_json(
                "/versions", params={"ids": json.dumps(chunk)}
            )
            if not isinstance(payload, list):
                raise IntegrationError(
                    "Modrinth returned an invalid version list."
                )
            for item in payload:
                version = self._version(item)
                expected_project = requested.get(version.version_id)
                if expected_project is None:
                    raise IntegrationError(
                        "Modrinth returned an unrequested version."
                    )
                if version.project_id != expected_project:
                    raise IntegrationError(
                        "A selected Modrinth version belongs to another project."
                    )
                resolved[version.version_id] = version

        if set(resolved) != set(requested):
            raise IntegrationError(
                "One or more selected Modrinth versions are no longer available."
            )

        normalized_loader = normalize_modloader(modloader)
        for version in resolved.values():
            if str(minecraft) not in version.game_versions:
                raise IntegrationError(
                    "A selected Modrinth version does not support this Minecraft version."
                )
            if normalized_loader and normalized_loader not in version.loaders:
                raise IntegrationError(
                    "A selected Modrinth version does not support this modloader."
                )
        return resolved


class GitHubProvider(ExternalProvider):
    """Expose GitHub repository tags as materializable CONFIG versions."""

    provider = GITHUB
    require_download_hash = False

    def __init__(self, http=None):
        # GitHubClient owns all HTTP validation, redirects, and rate-limit
        # errors. Keep ExternalProvider's session convention for tests.
        super().__init__(http=http)
        self.github = GitHubClient(http=self.http)

    def _download_host_allowed(self, hostname):
        return False

    @staticmethod
    def _project(repository):
        return ExternalProject(
            provider=GITHUB,
            project_id=repository.repository_id,
            slug=repository.name,
            title=repository.name,
            description=repository.description,
            author=repository.owner,
            link=repository.html_url,
            license=repository.license,
            side="BOTH",
            distribution_allowed=not repository.private,
            available=True,
        )

    def resolve_project(self, reference):
        try:
            return self._project(self.github.repository(reference))
        except GitHubConfigError as error:
            raise IntegrationError(str(error)) from error

    def get_project(self, project_id):
        project_id = external_id(project_id)
        if not project_id.isdigit():
            raise IntegrationError("GitHub returned an invalid repository identifier.")
        project = self.resolve_project(project_id)
        if project.project_id != project_id:
            raise IntegrationError("GitHub returned a different repository.")
        return project

    @staticmethod
    def _tag_version_id(repository_id, tag):
        return hashlib.sha256(
            f"github-tag\0{repository_id}\0{tag}".encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _manual_version_id(repository_id, sha, version):
        return hashlib.sha256(
            f"github-manual\0{repository_id}\0{sha}\0{version}".encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _external(repository, name, sha, version_id, minecraft, modloader=None):
        loader = normalize_modloader(modloader)
        minecraft = str(minecraft or "").strip()
        return ExternalVersion(
            provider=GITHUB,
            project_id=repository.repository_id,
            version_id=version_id,
            name=name,
            version_number=name,
            game_versions=(minecraft,) if minecraft else (),
            loaders=(loader,) if loader else (),
            release_type="release",
            date_published=None,
            filename=f"{repository.name}-{name}.zip",
            download_url=None,
            hashes={"git": sha},
            size=0,
            integration_label=repository.full_name,
        )

    def list_versions(self, project_id, minecraft, modloader=None):
        try:
            repository = self.github.repository(external_id(project_id))
            return [
                self._external(
                    repository,
                    tag,
                    sha,
                    self._tag_version_id(repository.repository_id, tag),
                    minecraft,
                    modloader,
                )
                for tag, sha in self.github.tags(repository)
            ]
        except GitHubConfigError as error:
            raise IntegrationError(str(error)) from error

    def list_all_versions(self, project_id):
        return self.list_versions(project_id, "")

    def get_version(self, project_id, version_id, minecraft, modloader=None):
        version_id = external_id(version_id)
        for version in self.list_versions(project_id, minecraft, modloader):
            if hmac.compare_digest(version.version_id, version_id):
                return version
        raise IntegrationError("The selected GitHub tag is no longer available.")

    def resolve_ref(self, project_id, ref, version, minecraft, modloader=None):
        version = str(version or "").strip()
        if not version or len(version) > 255:
            raise IntegrationError("Enter a version for this GitHub config package.")
        try:
            repository = self.github.repository(external_id(project_id))
            reference = self.github.resolve_ref(repository, ref)
        except GitHubConfigError as error:
            raise IntegrationError(str(error)) from error
        return self._external(
            repository,
            version,
            reference.sha,
            self._manual_version_id(
                repository.repository_id, reference.sha, version
            ),
            minecraft,
            modloader,
        )

    def build_config_package(self, version, destination):
        sha = str(version.hashes.get("git") or "")
        try:
            repository = self.github.repository(version.project_id)
            reference = self.github.resolve_ref(repository, sha)
            GitHubConfigPack(self.github).build(repository, reference, destination)
        except GitHubConfigError as error:
            raise IntegrationError(str(error)) from error


class MavenProvider(ExternalProvider):
    """Expose a configured standard Maven artifact as an integration provider."""

    provider = MAVEN
    require_download_hash = False

    def __init__(self, user_id=None, http=None):
        super().__init__(http=http)
        self.user_id = user_id
        self._download_origin = None

    @staticmethod
    def _artifact(project_id):
        project_id = external_id(project_id)
        if not project_id.isdigit():
            raise IntegrationError("The Maven artifact identifier is invalid.")
        artifact = MavenArtifact.get(int(project_id))
        if artifact is None:
            raise IntegrationError("The configured Maven artifact no longer exists.")
        return artifact

    @staticmethod
    def _repository(artifact):
        return MavenRepository(
            artifact.repository_id,
            artifact.repository_name,
            artifact.repository_url,
        )

    def get_project(self, project_id):
        artifact = self._artifact(project_id)
        return ExternalProject(
            provider=MAVEN,
            project_id=str(artifact.id),
            slug=artifact.slug,
            title=artifact.title,
            description=artifact.description,
            author=artifact.author,
            link=artifact.project_url,
            side=artifact.side,
        )

    @staticmethod
    def _external_version(artifact, mapping, hashes=None, download_url=None):
        download_url = download_url or artifact_file_url(
            artifact, mapping.upstream_version
        )
        filename = urlparse(download_url).path.rsplit("/", 1)[-1]
        loaders = (mapping.modloader,) if mapping.modloader else ()
        return ExternalVersion(
            provider=MAVEN,
            project_id=str(artifact.id),
            version_id=mapping.integration_version_id,
            name=mapping.upstream_version,
            version_number=mapping.mod_version,
            game_versions=(mapping.minecraft,),
            loaders=loaders,
            release_type="release",
            date_published=None,
            filename=filename,
            download_url=download_url,
            hashes=hashes or {},
            size=0,
            integration_label=artifact.repository_name,
        )

    def list_versions(self, project_id, minecraft, modloader=None):
        artifact = self._artifact(project_id)
        try:
            MavenCatalog.refresh(artifact, http=self.http)
        except MavenError as error:
            raise IntegrationError(str(error)) from error
        mappings = MavenVersion.get_compatible(
            artifact.id, minecraft, modloader
        )
        return [self._external_version(artifact, mapping) for mapping in mappings]

    def list_all_versions(self, project_id):
        artifact = self._artifact(project_id)
        try:
            MavenCatalog.refresh(artifact, http=self.http)
        except MavenError as error:
            raise IntegrationError(str(error)) from error
        mappings = (
            mapping
            for mapping in MavenVersion.get_all(artifact.id)
            if mapping.enabled
            and mapping.available
            and mapping.minecraft
            and mapping.mod_version
        )
        return [self._external_version(artifact, mapping) for mapping in mappings]

    def get_version(self, project_id, version_id, minecraft, modloader=None):
        artifact = self._artifact(project_id)
        version_id = external_id(version_id)
        mapping = MavenVersion.get_by_integration_id(artifact.id, version_id)
        if mapping is None or not mapping.enabled or not mapping.available:
            raise IntegrationError("The selected Maven version is not available.")
        if mapping.minecraft != str(minecraft):
            raise IntegrationError(
                "The selected Maven version does not support this Minecraft version."
            )
        normalized_loader = normalize_modloader(modloader)
        if mapping.modloader and normalized_loader != mapping.modloader:
            raise IntegrationError(
                "The selected Maven version does not support this modloader."
            )
        metadata = MavenMetadataClient(
            self._repository(artifact), http=self.http
        )
        try:
            filename_version = metadata.snapshot_value(
                artifact, mapping.upstream_version
            )
            download_url = artifact_file_url(
                artifact, mapping.upstream_version, filename_version
            )
            checksums = metadata.checksums(download_url)
        except MavenError as error:
            raise IntegrationError(str(error)) from error
        self._download_origin = MavenMetadataClient._url_origin(download_url)
        return self._external_version(
            artifact, mapping, checksums, download_url=download_url
        )

    def _download_host_allowed(self, hostname):
        return bool(
            self._download_origin
            and hostname.lower() == self._download_origin[1]
        )

    def _validate_download_url(self, url):
        if (
            not self._download_origin
            or MavenMetadataClient._url_origin(url) != self._download_origin
        ):
            raise IntegrationError("Maven returned an untrusted download URL.")


def provider_for_user(provider, user_id, *, http=None):
    provider = normalize_provider(provider)
    if provider == MAVEN:
        return MavenProvider(user_id=user_id, http=http)
    if provider == GITHUB:
        return GitHubProvider(http=http)
    return ModrinthProvider(http=http)


class ModIntegration:
    @staticmethod
    def _provider_download_source(external, *, md5=None, filesize=None):
        """Return verified immutable provider metadata stored for bootstrap."""
        provider = normalize_provider(external.provider)
        download_url = str(external.download_url or "")
        if (
            provider not in {MODRINTH, MAVEN}
            or urlparse(download_url).scheme.lower() != "https"
        ):
            # Private Maven origins may intentionally use HTTP. They can be
            # verified and packaged by management, but are not suitable as a
            # public bootstrap source; use the Solder-hosted JAR instead.
            return None
        return {
            "provider": provider,
            "url": download_url,
            "filename": external.filename,
            "md5": md5 or external.hashes.get("md5"),
            "sha1": external.hashes.get("sha1"),
            "sha512": external.hashes.get("sha512"),
            "filesize": filesize if filesize is not None else external.size,
        }

    # Kept as an internal compatibility alias for callers from older plugins.
    _modrinth_download_source = _provider_download_source

    @staticmethod
    def _slug(value):
        value = unicodedata.normalize("NFKD", str(value or ""))
        value = value.encode("ascii", "ignore").decode("ascii")
        value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
        if not value:
            raise IntegrationError("The provider project has no usable mod slug.")
        return value[:255]

    @classmethod
    def import_project(
        cls,
        provider_name,
        project_id,
        user_id,
        *,
        http=None,
        metadata=None,
        _change_context=None,
    ):
        provider_name = normalize_provider(provider_name)
        if provider_name == GITHUB:
            raise IntegrationError(
                "GitHub repositories must be linked from an existing CONFIG entry."
            )
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

        metadata = metadata or {}
        title = str(metadata.get("name") or project.title)[:255]
        description = str(
            metadata.get("description") or project.description
        )[:255]
        author = str(metadata.get("author") or project.author)[:255]
        link = str(metadata.get("link") or project.link)[:255]
        side = str(metadata.get("side") or project.side).upper()
        slug = cls._slug(project.slug or title)

        # A manual Solder mod may predate its Modrinth integration. Link the
        # existing row instead of forcing an administrator to delete it (and
        # all of its locally hosted versions) before importing the project.
        if project.provider == MODRINTH:
            existing_slug = Mod.get_by_name_api(slug)
            if existing_slug is not None:
                if (
                    existing_slug.integration_provider
                    or existing_slug.integration_project_id
                ):
                    raise IntegrationError(
                        f'The local mod slug "{slug}" is already managed by '
                        "another integration."
                    )
                try:
                    linked = Mod.link_integration(
                        existing_slug.id,
                        project.provider,
                        project.project_id,
                    )
                except DuplicateModError as error:
                    mapped = Mod.get_by_integration(
                        project.provider, project.project_id
                    )
                    if mapped:
                        return mapped, False
                    raise IntegrationError(
                        "This Modrinth project is already linked to another mod."
                    ) from error
                if linked is not None:
                    if _change_context is not None:
                        _change_context.setdefault("linked_mods", []).append(
                            (linked.id, project.provider, project.project_id)
                        )
                    return linked, False

                refreshed = Mod.get_by_name_api(slug)
                if (
                    refreshed is not None
                    and refreshed.integration_provider == project.provider
                    and refreshed.integration_project_id == project.project_id
                ):
                    if _change_context is not None:
                        _change_context.setdefault("linked_mods", []).append(
                            (
                                refreshed.id,
                                project.provider,
                                project.project_id,
                            )
                        )
                    return refreshed, False
                raise IntegrationError(
                    f'The local mod slug "{slug}" could not be linked safely.'
                )

        try:
            mod = Mod.new(
                slug,
                description,
                author,
                link,
                title,
                side,
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
        if _change_context is not None:
            _change_context.setdefault("created_mods", set()).add(mod.id)
        return mod, True

    @classmethod
    def ensure_bootstrap_projects(
        cls, project_ids, user_id, *, http=None
    ):
        """Ensure required Modrinth bootstrap projects exist in the library."""
        project_ids = tuple(
            dict.fromkeys(external_id(value) for value in project_ids if value)
        )
        change_context = {
            "externals": {},
            "mods": {},
            "processed": set(),
            "created_mods": set(),
            "linked_mods": [],
            "created_versions": set(),
            "dependency_edges": [],
            "artifacts": [],
        }
        imported = []
        created = 0
        try:
            for project_id in project_ids:
                mod = Mod.get_by_integration(MODRINTH, project_id)
                if mod is None:
                    mod, was_created = cls.import_project(
                        MODRINTH,
                        project_id,
                        user_id,
                        http=http,
                        _change_context=change_context,
                    )
                    created += int(was_created)
                imported.append(mod)
            Mod.set_modtypes(
                (mod.id for mod in imported), "BOOTSTRAP"
            )
        except Exception as error:
            if any(
                change_context.get(key)
                for key in ("created_mods", "linked_mods")
            ):
                cls._rollback_modrinth_materialization(change_context)
            if isinstance(error, IntegrationError):
                raise
            raise IntegrationError(
                "Downloader bootstrap projects could not be imported."
            ) from error
        return tuple(imported), created

    @classmethod
    def link_existing(cls, mod, provider_name, reference, user_id, *, http=None):
        """Link a manually selected upstream project without changing local data."""
        if mod is None:
            raise IntegrationError("The selected mod no longer exists.")
        if mod.integration_provider or mod.integration_project_id:
            raise IntegrationError("This mod is already managed by an integration.")

        provider_name = normalize_provider(provider_name)
        if provider_name == MAVEN:
            raise IntegrationError("Maven artifacts are configured from the Maven page.")
        if provider_name == GITHUB:
            if str(mod.modtype or "").upper() != "CONFIG":
                raise IntegrationError(
                    "GitHub config repositories can only be linked to CONFIG mods."
                )
            provider = GitHubProvider(http=http)
            project = provider.resolve_project(reference)
        else:
            if str(mod.modtype or "").upper() != "MOD":
                raise IntegrationError(
                    "Modrinth projects can only be linked to MOD entries."
                )
            provider = ModrinthProvider(http=http)
            project = provider.get_project(
                ModrinthProvider.project_reference(reference)
            )

        if not project.available or not project.distribution_allowed:
            raise IntegrationError("That upstream project cannot be used by Solder.")
        existing = Mod.get_by_integration(project.provider, project.project_id)
        if existing is not None and int(existing.id) != int(mod.id):
            raise IntegrationError(
                "That upstream project is already linked to another mod."
            )
        try:
            linked = Mod.link_integration(
                mod.id, project.provider, project.project_id
            )
        except DuplicateModError as error:
            raise IntegrationError(
                "That upstream project is already linked to another mod."
            ) from error
        if linked is None:
            raise IntegrationError("The mod could not be linked safely.")
        return linked, project

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
    def list_unimported_versions(mod, user_id, *, http=None):
        if not mod.integration_provider or not mod.integration_project_id:
            return []
        provider = provider_for_user(mod.integration_provider, user_id, http=http)
        imported = Modversion.get_integration_version_ids(mod.id)
        return [
            version
            for version in provider.list_all_versions(
                mod.integration_project_id
            )
            if version.version_id not in imported
        ]

    @classmethod
    def materialize_for_management(
        cls,
        mod,
        integration_version_id,
        minecraft,
        modloader,
        user_id,
        upload_folder,
        *,
        r2_client=None,
        r2_bucket=None,
        http=None,
    ):
        try:
            minecraft = normalize_minecraft_versions(minecraft)
            minecraft_version_storage(minecraft)
            modloader = normalize_modloaders(modloader)
        except ValueError as error:
            raise IntegrationError(str(error)) from error
        if not minecraft:
            raise IntegrationError(
                "Select at least one Minecraft version for the upstream version."
            )
        primary_minecraft = minecraft.split(",", 1)[0]
        primary_modloader = (
            modloader.split(",", 1)[0] if modloader else None
        )
        return cls.materialize(
            mod,
            SimpleNamespace(
                minecraft=primary_minecraft,
                modloader=primary_modloader,
            ),
            integration_version_id,
            user_id,
            upload_folder,
            r2_client=r2_client,
            r2_bucket=r2_bucket,
            http=http,
            _stored_minecraft=minecraft,
            _stored_modloader=modloader,
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
    def _materialize_github_config(
        cls,
        mod,
        build,
        external,
        provider,
        upload_folder,
        *,
        r2_client=None,
        r2_bucket=None,
    ):
        if str(mod.modtype or "").upper() != "CONFIG":
            raise IntegrationError(
                "GitHub repository versions can only be imported for CONFIG mods."
            )
        selected_loader = external.loader_for_build(build.modloader)
        if build.modloader and external.loaders and selected_loader is None:
            raise IntegrationError(
                "The selected config version does not support this modloader."
            )
        version_name = cls._local_version(mod, external, build.minecraft)
        destination_folder = Path(upload_folder, mod.name)
        destination_folder.mkdir(parents=True, exist_ok=True)
        zip_filename = f"{mod.name}-{version_name}.zip"
        final_zip = destination_folder / zip_filename
        uploaded_key = None
        try:
            with tempfile.TemporaryDirectory(
                prefix=".solder-github-", dir=destination_folder
            ) as staging_directory:
                staged_zip = Path(staging_directory, zip_filename)
                provider.build_config_package(external, staged_zip)
                package_md5 = Mod.file_md5(staged_zip)
                package_size = staged_zip.stat().st_size
                os.replace(staged_zip, final_zip)

            if r2_client is not None and r2_bucket:
                uploaded_key = f"mods/{mod.name}/{zip_filename}"
                r2_client.upload_file(
                    str(final_zip),
                    r2_bucket,
                    uploaded_key,
                    ExtraArgs={"ContentType": "application/zip"},
                )

            version = Modversion.new(
                mod.id,
                version_name,
                build.minecraft,
                package_md5,
                package_size,
                "0",
                "0",
                "0",
                modloader=selected_loader,
                integration_version_id=external.version_id,
            )
            return MaterializedVersion(version, True)
        except Exception:
            raced = Modversion.get_by_integration(mod.id, external.version_id)
            if raced:
                return MaterializedVersion(raced, False)
            final_zip.unlink(missing_ok=True)
            if uploaded_key and r2_client is not None and r2_bucket:
                try:
                    r2_client.delete_object(Bucket=r2_bucket, Key=uploaded_key)
                except Exception:
                    logger.warning(
                        "Unable to remove an incomplete R2 integration upload.",
                        exc_info=True,
                    )
            raise

    @classmethod
    def materialize_github_ref(
        cls,
        mod,
        minecraft,
        modloader,
        ref,
        version,
        user_id,
        upload_folder,
        *,
        r2_client=None,
        r2_bucket=None,
        http=None,
    ):
        if mod is None or mod.integration_provider != GITHUB:
            raise IntegrationError("This CONFIG mod is not linked to GitHub.")
        if not mod.name or secure_filename(mod.name) != mod.name:
            raise IntegrationError(
                "The managed mod slug contains unsafe filename characters."
            )
        minecraft = str(minecraft or "").strip()
        if not minecraft or len(minecraft) > 255:
            raise IntegrationError("Enter the Minecraft version for this config package.")
        try:
            minecraft_version_storage(minecraft)
        except ValueError as error:
            raise IntegrationError(str(error)) from error
        provider = provider_for_user(GITHUB, user_id, http=http)
        external = provider.resolve_ref(
            mod.integration_project_id,
            ref,
            version,
            minecraft,
            modloader,
        )
        existing = Modversion.get_by_integration(mod.id, external.version_id)
        if existing:
            if mod.integration_provider == MODRINTH:
                provider._validate_download_url(external.download_url)
                Modversion.store_download_source(
                    existing.id,
                    cls._modrinth_download_source(
                        external,
                        md5=existing.jarmd5,
                        filesize=existing.jarfilesize,
                    ),
                )
            return MaterializedVersion(existing, False)
        build = type(
            "GitHubConfigBuild",
            (),
            {"minecraft": minecraft, "modloader": normalize_modloader(modloader)},
        )()
        return cls._materialize_github_config(
            mod,
            build,
            external,
            provider,
            upload_folder,
            r2_client=r2_client,
            r2_bucket=r2_bucket,
        )

    @classmethod
    def materialize_latest_github_tag(
        cls,
        mod,
        minecraft,
        modloader,
        user_id,
        upload_folder,
        *,
        r2_client=None,
        r2_bucket=None,
        http=None,
    ):
        if mod is None or mod.integration_provider != GITHUB:
            raise IntegrationError("This CONFIG mod is not linked to GitHub.")
        minecraft = str(minecraft or "").strip()
        if not minecraft or len(minecraft) > 255:
            raise IntegrationError("Enter the Minecraft version for this config package.")
        try:
            minecraft_version_storage(minecraft)
        except ValueError as error:
            raise IntegrationError(str(error)) from error
        provider = provider_for_user(GITHUB, user_id, http=http)
        versions = provider.list_versions(
            mod.integration_project_id, minecraft, modloader
        )
        if not versions:
            raise IntegrationError("This GitHub repository has no tags to import.")
        external = versions[0]
        existing = Modversion.get_by_integration(mod.id, external.version_id)
        if existing:
            return MaterializedVersion(existing, False)
        build = type(
            "GitHubConfigBuild",
            (),
            {"minecraft": minecraft, "modloader": normalize_modloader(modloader)},
        )()
        return cls._materialize_github_config(
            mod,
            build,
            external,
            provider,
            upload_folder,
            r2_client=r2_client,
            r2_bucket=r2_bucket,
        )

    @classmethod
    def _materialize_modrinth_dependencies(
        cls,
        mod,
        build,
        external,
        provider,
        user_id,
        upload_folder,
        *,
        r2_client=None,
        r2_bucket=None,
        dependency_path=(),
        dependency_context=None,
        stored_minecraft=None,
        stored_modloader=None,
    ):
        """Import, materialize and declare required Modrinth dependencies."""
        if len(dependency_path) > MAX_MODRINTH_DEPENDENCY_DEPTH:
            raise IntegrationError(
                "The Modrinth required dependency chain is too deep."
            )
        if len(external.dependencies) > MAX_MODRINTH_DEPENDENCIES_PER_VERSION:
            raise IntegrationError(
                "A Modrinth version declared too many required dependencies."
            )
        if dependency_context is None:
            dependency_context = {
                "externals": {},
                "mods": {},
                "processed": set(),
            }
        dependencies = {}
        for dependency in external.dependencies:
            version_id = (
                external_id(dependency.version_id)
                if dependency.version_id
                else None
            )
            resolved_version = None
            if dependency.project_id:
                project_id = external_id(dependency.project_id)
            elif version_id:
                resolved_version = provider.get_version_by_id(
                    version_id,
                    str(build.minecraft),
                    normalize_modloader(build.modloader)
                    or external.loader_for_build(None),
                )
                project_id = external_id(resolved_version.project_id)
            else:
                continue
            previous = dependencies.get(project_id)
            if (
                project_id in dependencies
                and previous[0] != version_id
            ):
                raise IntegrationError(
                    "Modrinth returned conflicting required versions for "
                    f'project "{project_id}".'
                )
            dependencies[project_id] = (version_id, resolved_version)

        if not dependencies:
            return

        selected_loader = normalize_modloader(build.modloader)
        if selected_loader is None:
            selected_loader = external.loader_for_build(None)
        dependency_build = SimpleNamespace(
            minecraft=str(build.minecraft),
            modloader=selected_loader,
        )
        imported_dependencies = []
        for project_id, dependency_data in dependencies.items():
            version_id, resolved_version = dependency_data
            if project_id in dependency_path:
                chain = " -> ".join((*dependency_path, project_id))
                raise IntegrationError(
                    f"Modrinth returned a circular required dependency: {chain}."
                )

            dependency_mod = dependency_context["mods"].get(project_id)
            if dependency_mod is None:
                if (
                    len(dependency_context["mods"])
                    >= MAX_MODRINTH_DEPENDENCY_PROJECTS
                ):
                    raise IntegrationError(
                        "The Modrinth dependency graph contains too many projects."
                    )
                dependency_mod, _created = cls.import_project(
                    MODRINTH,
                    project_id,
                    user_id,
                    http=provider.http,
                    _change_context=dependency_context,
                )
                dependency_context["mods"][project_id] = dependency_mod

            dependency_version = dependency_context["externals"].get(
                project_id
            )
            if (
                dependency_version is not None
                and version_id
                and dependency_version.version_id != version_id
            ):
                raise IntegrationError(
                    "Modrinth required two different versions of "
                    f'project "{project_id}" in the same dependency graph.'
                )
            if dependency_version is None and resolved_version is not None:
                dependency_version = resolved_version
            if dependency_version is None:
                if version_id:
                    dependency_version = provider.get_version(
                        project_id,
                        version_id,
                        dependency_build.minecraft,
                        dependency_build.modloader,
                    )
                else:
                    compatible_versions = provider.list_versions(
                        project_id,
                        dependency_build.minecraft,
                        dependency_build.modloader,
                    )
                    if not compatible_versions:
                        raise IntegrationError(
                            f'Required dependency "{dependency_mod.pretty_name}" '
                            "has no compatible Modrinth version for this build."
                        )
                    dependency_version = compatible_versions[0]
            dependency_context["externals"][project_id] = dependency_version

            dependency_key = (project_id, dependency_version.version_id)
            if dependency_key not in dependency_context["processed"]:
                cls.materialize(
                    dependency_mod,
                    dependency_build,
                    dependency_version.version_id,
                    user_id,
                    upload_folder,
                    r2_client=r2_client,
                    r2_bucket=r2_bucket,
                    http=provider.http,
                    _provider=provider,
                    _external=dependency_version,
                    _dependency_path=dependency_path,
                    _dependency_context=dependency_context,
                    _stored_minecraft=stored_minecraft,
                    _stored_modloader=stored_modloader,
                )
                dependency_context["processed"].add(dependency_key)
            imported_dependencies.append(dependency_mod)

        for dependency_mod in imported_dependencies:
            try:
                created = ModDependency.ensure(mod.id, dependency_mod.id)
                if created:
                    dependency_context.setdefault("dependency_edges", []).append(
                        (mod.id, dependency_mod.id)
                    )
            except DependencyError as error:
                raise IntegrationError(
                    f'Could not declare "{dependency_mod.pretty_name}" as a '
                    "required dependency."
                ) from error

    @staticmethod
    def _rollback_modrinth_materialization(
        context, *, r2_client=None, r2_bucket=None
    ):
        """Compensate committed graph nodes when a top-level import fails."""
        version_ids = tuple(sorted(context.get("created_versions", ())))
        mod_ids = tuple(sorted(context.get("created_mods", ())))
        connection = Database.get_connection()
        if connection is None:
            logger.error(
                "Unable to roll back a failed Modrinth dependency import: "
                "the database is unavailable."
            )
            return
        cursor = connection.cursor()
        try:
            for mod_id, dependency_mod_id in reversed(
                context.get("dependency_edges", ())
            ):
                cursor.execute(
                    """DELETE FROM mod_dependencies
                       WHERE mod_id = %s AND dependency_mod_id = %s""",
                    (mod_id, dependency_mod_id),
                )
            if version_ids:
                placeholders = ", ".join(["%s"] * len(version_ids))
                cursor.execute(
                    f"""DELETE build_optional_group_items
                        FROM build_optional_group_items
                        INNER JOIN build_modversion
                            ON build_modversion.id =
                               build_optional_group_items.build_modversion_id
                        WHERE build_modversion.modversion_id
                              IN ({placeholders})""",  # nosec B608
                    version_ids,
                )
                cursor.execute(
                    f"DELETE FROM build_modversion WHERE modversion_id "
                    f"IN ({placeholders})",  # nosec B608
                    version_ids,
                )
                for table in (
                    "modversion_download_overrides",
                    "modversion_download_sources",
                    "modversion_provider_ids",
                    "modversion_minecraft_versions",
                ):
                    cursor.execute(
                        f"DELETE FROM {table} WHERE modversion_id "
                        f"IN ({placeholders})",  # nosec B608
                        version_ids,
                    )
                cursor.execute(
                    f"DELETE FROM modversions WHERE id "
                    f"IN ({placeholders})",  # nosec B608
                    version_ids,
                )
            if mod_ids:
                placeholders = ", ".join(["%s"] * len(mod_ids))
                cursor.execute(
                    f"""DELETE FROM mods
                        WHERE id IN ({placeholders})
                          AND NOT EXISTS (
                              SELECT 1 FROM modversions
                              WHERE modversions.mod_id = mods.id
                          )
                          AND NOT EXISTS (
                              SELECT 1 FROM mod_dependencies
                              WHERE mod_dependencies.mod_id = mods.id
                                 OR mod_dependencies.dependency_mod_id = mods.id
                          )""",  # nosec B608
                    mod_ids,
                )
            for mod_id, provider, project_id in reversed(
                context.get("linked_mods", ())
            ):
                cursor.execute(
                    """UPDATE mods
                       SET integration_provider = NULL,
                           integration_project_id = NULL
                        WHERE id = %s
                          AND integration_provider = %s
                          AND integration_project_id = %s
                          AND NOT EXISTS (
                              SELECT 1 FROM modversions
                              WHERE modversions.mod_id = mods.id
                                AND modversions.integration_version_id IS NOT NULL
                          )""",
                    (mod_id, provider, project_id),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            logger.error(
                "Unable to roll back a failed Modrinth dependency import.",
                exc_info=True,
            )
            return
        finally:
            cursor.close()
            connection.close()

        for artifact in reversed(context.get("artifacts", ())):
            for path in artifact.get("paths", ()):
                try:
                    Path(path).unlink(missing_ok=True)
                except OSError:
                    logger.warning(
                        "Unable to remove a rolled-back integration artifact.",
                        exc_info=True,
                    )
            if r2_client is not None and r2_bucket:
                for key in artifact.get("keys", ()):
                    try:
                        r2_client.delete_object(Bucket=r2_bucket, Key=key)
                    except Exception:
                        logger.warning(
                            "Unable to remove a rolled-back R2 integration artifact.",
                            exc_info=True,
                        )

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
        _provider=None,
        _external=None,
        _dependency_path=(),
        _dependency_context=None,
        _stored_minecraft=None,
        _stored_modloader=None,
    ):
        """Materialize one graph and remove newly created rows on failure."""
        root_graph = (
            _dependency_context is None
            and str(getattr(mod, "integration_provider", "") or "").upper()
            == MODRINTH
        )
        if root_graph:
            _dependency_context = {
                "externals": {},
                "mods": {},
                "processed": set(),
                "created_mods": set(),
                "linked_mods": [],
                "created_versions": set(),
                "dependency_edges": [],
                "artifacts": [],
            }
        try:
            return cls._materialize(
                mod,
                build,
                integration_version_id,
                user_id,
                upload_folder,
                r2_client=r2_client,
                r2_bucket=r2_bucket,
                http=http,
                _provider=_provider,
                _external=_external,
                _dependency_path=_dependency_path,
                _dependency_context=_dependency_context,
                _stored_minecraft=_stored_minecraft,
                _stored_modloader=_stored_modloader,
            )
        except Exception:
            changed_graph = root_graph and any(
                _dependency_context.get(key)
                for key in (
                    "created_mods",
                    "linked_mods",
                    "created_versions",
                    "dependency_edges",
                    "artifacts",
                )
            )
            if changed_graph:
                cls._rollback_modrinth_materialization(
                    _dependency_context,
                    r2_client=r2_client,
                    r2_bucket=r2_bucket,
                )
            raise

    @classmethod
    def _materialize(
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
        _provider=None,
        _external=None,
        _dependency_path=(),
        _dependency_context=None,
        _stored_minecraft=None,
        _stored_modloader=None,
    ):
        if not mod.integration_provider or not mod.integration_project_id:
            raise IntegrationError("This mod is not managed by an integration.")
        if not mod.name or secure_filename(mod.name) != mod.name:
            raise IntegrationError(
                "The managed mod slug contains unsafe filename characters."
            )

        existing = Modversion.get_by_integration(mod.id, integration_version_id)
        if existing and _external is None:
            return MaterializedVersion(existing, False)

        provider = _provider or provider_for_user(
            mod.integration_provider, user_id, http=http
        )
        external = _external
        if external is None:
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
        if (
            normalize_provider(external.provider) != mod.integration_provider
            or external_id(external.project_id)
            != external_id(mod.integration_project_id)
            or external_id(external.version_id)
            != external_id(integration_version_id)
        ):
            raise IntegrationError(
                "The resolved provider version does not belong to this mod."
            )
        if mod.integration_provider == GITHUB:
            return cls._materialize_github_config(
                mod,
                build,
                external,
                provider,
                upload_folder,
                r2_client=r2_client,
                r2_bucket=r2_bucket,
            )
        selected_loader = external.loader_for_build(build.modloader)
        if build.modloader and external.loaders and selected_loader is None:
            raise IntegrationError("The selected version does not support this modloader.")
        stored_minecraft = _stored_minecraft or build.minecraft
        stored_modloader = _stored_modloader or selected_loader
        if external.game_versions:
            unsupported_minecraft = set(
                compatibility_values(stored_minecraft)
            ).difference(external.game_versions)
            if unsupported_minecraft:
                raise IntegrationError(
                    "The selected upstream version does not support every "
                    "selected Minecraft version."
                )
        if external.loaders:
            unsupported_loaders = {
                loader
                for loader in compatibility_values(
                    stored_modloader, modloaders=True
                )
                if external.loader_for_build(loader) is None
            }
            if unsupported_loaders:
                raise IntegrationError(
                    "The selected upstream version does not support every "
                    "selected modloader."
                )

        if mod.integration_provider == MODRINTH:
            project_id = external_id(mod.integration_project_id)
            if project_id in _dependency_path:
                chain = " -> ".join((*_dependency_path, project_id))
                raise IntegrationError(
                    f"Modrinth returned a circular required dependency: {chain}."
                )
            dependency_path = (*_dependency_path, project_id)
            if _dependency_context is None:
                _dependency_context = {
                    "externals": {},
                    "mods": {},
                    "processed": set(),
                }
            previous_external = _dependency_context["externals"].get(project_id)
            if (
                previous_external is not None
                and previous_external.version_id != external.version_id
            ):
                raise IntegrationError(
                    "Modrinth required two different versions of "
                    f'project "{project_id}" in the same dependency graph.'
                )
            _dependency_context["externals"][project_id] = external
            _dependency_context["mods"][project_id] = mod
            cls._materialize_modrinth_dependencies(
                mod,
                build,
                external,
                provider,
                user_id,
                upload_folder,
                r2_client=r2_client,
                r2_bucket=r2_bucket,
                dependency_path=dependency_path,
                dependency_context=_dependency_context,
                stored_minecraft=stored_minecraft,
                stored_modloader=stored_modloader,
            )

        if existing:
            return MaterializedVersion(existing, False)

        version_minecraft, _minecraft_versions = minecraft_version_storage(
            stored_minecraft
        )
        version_name = cls._local_version(
            mod, external, version_minecraft or build.minecraft
        )
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
                jar_filesize = staged_jar.stat().st_size
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
                stored_minecraft,
                package_md5,
                package_size,
                "0",
                "0",
                jar_md5,
                modloader=stored_modloader,
                integration_version_id=external.version_id,
                jarfilesize=jar_filesize,
                download_source=cls._provider_download_source(
                    external, md5=jar_md5, filesize=jar_filesize
                ),
            )
            if _dependency_context is not None:
                _dependency_context.setdefault("created_versions", set()).add(
                    version.id
                )
                _dependency_context.setdefault("artifacts", []).append(
                    {
                        "version_id": version.id,
                        "paths": (final_jar, final_zip),
                        "keys": tuple(uploaded_keys),
                    }
                )
            return MaterializedVersion(version, True)
        except Exception:
            raced = Modversion.get_by_integration(mod.id, integration_version_id)
            if raced:
                if mod.integration_provider in {MODRINTH, MAVEN}:
                    provider._validate_download_url(external.download_url)
                    source = cls._provider_download_source(
                        external,
                        md5=raced.jarmd5,
                        filesize=raced.jarfilesize,
                    )
                    if source is not None:
                        Modversion.store_download_source(raced.id, source)
                return MaterializedVersion(raced, False)
            final_zip.unlink(missing_ok=True)
            final_jar.unlink(missing_ok=True)
            if r2_client is not None and r2_bucket:
                for key in uploaded_keys:
                    try:
                        r2_client.delete_object(Bucket=r2_bucket, Key=key)
                    except Exception:
                        logger.warning(
                            "Unable to remove an incomplete R2 integration upload.",
                            exc_info=True,
                        )
            raise
