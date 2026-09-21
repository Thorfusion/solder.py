"""GitHub repository support for versioned CONFIG packages.

The database only stores GitHub's stable numeric repository id in the existing
integration columns.  Repository archives are converted to ordinary Solder
ZIPs when a tag or manually selected ref is materialized.
"""

from __future__ import annotations

from configparser import ConfigParser
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
from urllib.parse import quote, urljoin, urlparse
import zipfile

import requests


GITHUB_API = "https://api.github.com"
REQUEST_TIMEOUT = (5, 30)
DOWNLOAD_TIMEOUT = (5, 120)
MAX_ARCHIVE_SIZE = 512 * 1024 * 1024
MAX_EXPANDED_SIZE = 1024 * 1024 * 1024
MAX_ARCHIVE_FILES = 50000
MAX_SUBMODULES = 50
MAX_SUBMODULE_DEPTH = 3
MAX_IGNORE_SIZE = 256 * 1024
MAX_IGNORE_RULES = 2000
USER_AGENT = "solder.py/1.10.1 (+https://github.com/Thorfusion/solder.py)"

_REPOSITORY_RE = re.compile(
    r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/(?P<repo>[A-Za-z0-9_.-]{1,100})"
)
_SHA_RE = re.compile(r"[0-9a-fA-F]{40}")
_ALLOWED_ARCHIVE_HOSTS = {"api.github.com", "codeload.github.com"}
_EXCLUDED_ROOT_NAMES = {
    ".gitattributes",
    ".gitignore",
    ".gitmodules",
    "code_of_conduct.md",
    "contributing.md",
    "license",
    "license.md",
    "readme",
    "readme.md",
}
_EXCLUDED_ROOT_PREFIXES = ("changelog", "license", "readme")


class GitHubConfigError(ValueError):
    """Raised when a GitHub config source cannot be imported safely."""


@dataclass(frozen=True)
class GitHubRepository:
    repository_id: str
    full_name: str
    name: str
    owner: str
    description: str
    html_url: str
    default_branch: str
    archived: bool
    private: bool
    license: str | None


@dataclass(frozen=True)
class GitHubReference:
    name: str
    sha: str
    tree_sha: str
    committed_at: str | None = None


def github_repository_reference(value):
    """Accept an owner/repository pair or a normal GitHub repository URL."""
    value = str(value or "").strip()
    if not value:
        raise GitHubConfigError("Enter a GitHub repository URL or owner/repository.")
    if "://" in value:
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() not in {"github.com", "www.github.com"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise GitHubConfigError("Only plain HTTPS GitHub repository URLs are supported.")
        value = parsed.path.strip("/")
    value = value.removesuffix(".git").strip("/")
    if not _REPOSITORY_RE.fullmatch(value):
        raise GitHubConfigError("The GitHub repository must use owner/repository format.")
    return value


def _safe_repository_from_git_url(value):
    value = str(value or "").strip()
    if value.startswith("git@github.com:"):
        value = value[len("git@github.com:") :]
    return github_repository_reference(value)


def _natural_key(value):
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", str(value))
        if part
    )


class GitHubClient:
    def __init__(self, http=None, token=None):
        self.http = http or requests.Session()
        self.token = token if token is not None else os.getenv("GITHUB_TOKEN")

    def _headers(self, *, json_response=True):
        headers = {"User-Agent": USER_AGENT}
        if json_response:
            headers["Accept"] = "application/vnd.github+json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _request_json(self, path, *, params=None):
        try:
            response = self.http.get(
                f"{GITHUB_API}{path}",
                params=params,
                headers=self._headers(),
                allow_redirects=False,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as error:
            raise GitHubConfigError("GitHub could not be reached.") from error
        try:
            if 300 <= response.status_code < 400:
                raise GitHubConfigError("GitHub returned an unexpected redirect.")
            if response.status_code == 404:
                raise GitHubConfigError("The GitHub repository or ref was not found.")
            if response.status_code in {401, 403}:
                raise GitHubConfigError(
                    "GitHub rejected the request or its API rate limit was reached."
                )
            response.raise_for_status()
            return response.json()
        except GitHubConfigError:
            raise
        except (requests.RequestException, ValueError) as error:
            raise GitHubConfigError("GitHub returned an invalid response.") from error
        finally:
            response.close()

    @staticmethod
    def _repository(payload):
        owner = payload.get("owner") or {}
        license_data = payload.get("license") or {}
        repository_id = str(payload.get("id") or "")
        full_name = str(payload.get("full_name") or "")
        if not repository_id.isdigit() or not _REPOSITORY_RE.fullmatch(full_name):
            raise GitHubConfigError("GitHub returned invalid repository metadata.")
        return GitHubRepository(
            repository_id=repository_id,
            full_name=full_name,
            name=str(payload.get("name") or full_name.split("/", 1)[1]),
            owner=str(owner.get("login") or full_name.split("/", 1)[0]),
            description=str(payload.get("description") or ""),
            html_url=str(payload.get("html_url") or f"https://github.com/{full_name}"),
            default_branch=str(payload.get("default_branch") or "main"),
            archived=bool(payload.get("archived")),
            private=bool(payload.get("private")),
            license=(str(license_data.get("spdx_id")) if license_data.get("spdx_id") else None),
        )

    def repository(self, reference):
        reference = str(reference or "").strip()
        if reference.isdigit():
            payload = self._request_json(f"/repositories/{reference}")
        else:
            full_name = github_repository_reference(reference)
            owner, repository = full_name.split("/", 1)
            payload = self._request_json(
                f"/repos/{quote(owner, safe='')}/{quote(repository, safe='')}"
            )
        result = self._repository(payload)
        if result.private:
            raise GitHubConfigError(
                "Private GitHub config repositories are not supported for public Solder packages."
            )
        return result

    def resolve_ref(self, repository, ref):
        ref = str(ref or "").strip()
        if not ref or len(ref) > 255 or any(ord(character) < 32 for character in ref):
            raise GitHubConfigError("Enter a valid GitHub branch, tag, or commit.")
        payload = self._request_json(
            f"/repos/{repository.full_name}/commits/{quote(ref, safe='')}"
        )
        sha = str(payload.get("sha") or "")
        commit = payload.get("commit") or {}
        tree_sha = str((commit.get("tree") or {}).get("sha") or "")
        if not _SHA_RE.fullmatch(sha) or not _SHA_RE.fullmatch(tree_sha):
            raise GitHubConfigError("GitHub returned invalid commit metadata.")
        committer = commit.get("committer") or {}
        return GitHubReference(ref, sha.lower(), tree_sha.lower(), committer.get("date"))

    def tags(self, repository, limit=1000):
        tags = []
        for page in range(1, 11):
            payload = self._request_json(
                f"/repos/{repository.full_name}/tags",
                params={"per_page": 100, "page": page},
            )
            if not isinstance(payload, list):
                raise GitHubConfigError("GitHub returned an invalid tag list.")
            for item in payload:
                name = str(item.get("name") or "")
                sha = str((item.get("commit") or {}).get("sha") or "")
                if name and len(name) <= 255 and _SHA_RE.fullmatch(sha):
                    tags.append((name, sha.lower()))
                    if len(tags) >= limit:
                        break
            if len(tags) >= limit or len(payload) < 100:
                break
        tags.sort(key=lambda item: (_natural_key(item[0]), item[1]), reverse=True)
        return tags

    def tag_reference(self, repository, name, sha):
        resolved = self.resolve_ref(repository, sha)
        return GitHubReference(name, resolved.sha, resolved.tree_sha, resolved.committed_at)

    def gitlinks(self, repository, tree_sha):
        payload = self._request_json(
            f"/repos/{repository.full_name}/git/trees/{tree_sha}",
            params={"recursive": "1"},
        )
        if payload.get("truncated"):
            raise GitHubConfigError("The GitHub repository tree is too large to resolve safely.")
        links = []
        for item in payload.get("tree", []):
            if item.get("mode") != "160000" or item.get("type") != "commit":
                continue
            path = _safe_relative_path(item.get("path"))
            sha = str(item.get("sha") or "")
            if not _SHA_RE.fullmatch(sha):
                raise GitHubConfigError("GitHub returned an invalid submodule commit.")
            links.append((path.as_posix(), sha.lower()))
        if len(links) > MAX_SUBMODULES:
            raise GitHubConfigError("The repository contains too many submodules.")
        return links

    def download_archive(self, repository, sha, destination):
        if not _SHA_RE.fullmatch(str(sha or "")):
            raise GitHubConfigError("The GitHub archive commit is invalid.")
        url = f"{GITHUB_API}/repos/{repository.full_name}/zipball/{sha}"
        response = None
        for _ in range(6):
            parsed = urlparse(url)
            if parsed.scheme != "https" or (parsed.hostname or "").lower() not in _ALLOWED_ARCHIVE_HOSTS:
                raise GitHubConfigError("GitHub returned an untrusted archive URL.")
            try:
                response = self.http.get(
                    url,
                    headers=self._headers(json_response=False),
                    stream=True,
                    allow_redirects=False,
                    timeout=DOWNLOAD_TIMEOUT,
                )
            except requests.RequestException as error:
                raise GitHubConfigError("The GitHub repository archive download failed.") from error
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("Location")
                response.close()
                if not location:
                    raise GitHubConfigError("GitHub returned an invalid archive redirect.")
                url = urljoin(url, location)
                continue
            break
        else:
            raise GitHubConfigError("GitHub returned too many archive redirects.")

        try:
            response.raise_for_status()
            length = response.headers.get("Content-Length")
            if length and int(length) > MAX_ARCHIVE_SIZE:
                raise GitHubConfigError("The GitHub repository archive exceeds 512 MiB.")
            downloaded = 0
            with Path(destination).open("wb") as archive:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    downloaded += len(chunk)
                    if downloaded > MAX_ARCHIVE_SIZE:
                        raise GitHubConfigError("The GitHub repository archive exceeds 512 MiB.")
                    archive.write(chunk)
            if downloaded == 0:
                raise GitHubConfigError("GitHub returned an empty repository archive.")
        except GitHubConfigError:
            Path(destination).unlink(missing_ok=True)
            raise
        except (OSError, requests.RequestException, ValueError) as error:
            Path(destination).unlink(missing_ok=True)
            raise GitHubConfigError("The GitHub repository archive download failed.") from error
        finally:
            response.close()


def _safe_relative_path(value):
    normalized = str(value or "").replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or normalized.startswith("/")
        or re.match(r"^[A-Za-z]:", normalized)
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(":" in part for part in path.parts)
    ):
        raise GitHubConfigError("The GitHub repository contains an unsafe path.")
    return path


def _package_path_allowed(path):
    parts = path.parts
    if not parts:
        return False
    first = parts[0].casefold()
    if first in {".git", ".github", "docs", "documentation"}:
        return False
    if first.startswith("."):
        return False
    if len(parts) == 1:
        name = parts[0].casefold()
        if name in _EXCLUDED_ROOT_NAMES or name.endswith(".md"):
            return False
        if any(name.startswith(prefix) for prefix in _EXCLUDED_ROOT_PREFIXES):
            return False
    return True


def _ignore_glob_expression(pattern):
    """Translate the useful gitignore glob subset without matching across `/`."""
    expression = []
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if character == "\\" and index + 1 < len(pattern):
            index += 1
            expression.append(re.escape(pattern[index]))
        elif character == "*":
            if index + 1 < len(pattern) and pattern[index + 1] == "*":
                while index + 1 < len(pattern) and pattern[index + 1] == "*":
                    index += 1
                if index + 1 < len(pattern) and pattern[index + 1] == "/":
                    index += 1
                    expression.append("(?:[^/]+/)*")
                else:
                    expression.append(".*")
            else:
                expression.append("[^/]*")
        elif character == "?":
            expression.append("[^/]")
        else:
            expression.append(re.escape(character))
        index += 1
    return "".join(expression)


@dataclass(frozen=True)
class _IgnoreRule:
    expression: re.Pattern
    negated: bool
    match_segments: bool

    def matches(self, path):
        if self.match_segments:
            return any(self.expression.fullmatch(part) for part in path.split("/"))
        return self.expression.fullmatch(path) is not None


class SolderPyIgnore:
    """Small, deterministic gitignore-style matcher for repository packages."""

    def __init__(self, content):
        rules = []
        for raw_line in str(content or "").splitlines():
            line = raw_line.rstrip()
            if not line:
                continue
            if len(line) > 2048 or "\x00" in line:
                raise GitHubConfigError("The repository .solderpyignore is invalid.")
            if line.startswith("\\#"):
                line = line[1:]
            elif line.startswith("#"):
                continue

            negated = False
            if line.startswith("\\!"):
                line = line[1:]
            elif line.startswith("!"):
                negated = True
                line = line[1:]

            anchored = line.startswith("/")
            if anchored:
                line = line[1:]
            line = line.rstrip("/")
            if not line:
                continue
            if any(part in {".", ".."} for part in line.split("/")):
                raise GitHubConfigError(
                    "The repository .solderpyignore contains an unsafe pattern."
                )

            match_segments = not anchored and "/" not in line
            expression = _ignore_glob_expression(line)
            # A matching directory also excludes everything below it. This
            # keeps directory rules useful while scanning a flat ZIP listing.
            if not match_segments:
                expression = f"(?:{expression})(?:/.*)?"
            rules.append(
                _IgnoreRule(re.compile(expression), negated, match_segments)
            )
            if len(rules) > MAX_IGNORE_RULES:
                raise GitHubConfigError(
                    "The repository .solderpyignore contains too many rules."
                )
        self.rules = tuple(rules)

    def ignores(self, path):
        ignored = False
        for rule in self.rules:
            if rule.matches(path):
                ignored = not rule.negated
        return ignored


class GitHubConfigPack:
    """Convert a repository snapshot into a deterministic instance-root ZIP."""

    def __init__(self, client):
        self.client = client
        self._expanded_size = 0
        self._file_count = 0
        self._submodule_count = 0
        self._ignore_scopes = []

    @staticmethod
    def _root_metadata(archive_path, expected_sha):
        try:
            archive = zipfile.ZipFile(archive_path, "r")
        except (OSError, zipfile.BadZipFile) as error:
            raise GitHubConfigError("GitHub returned an invalid repository ZIP.") from error
        with archive:
            roots = {
                PurePosixPath(info.filename.replace("\\", "/")).parts[0]
                for info in archive.infolist()
                if info.filename and PurePosixPath(info.filename.replace("\\", "/")).parts
            }
            if len(roots) != 1:
                raise GitHubConfigError("The GitHub repository ZIP has an invalid root.")
            root = next(iter(roots))
            # GitHub zipball roots end in the resolved commit's abbreviated
            # object id. Bind the archive response to the API-resolved commit
            # before accepting any of its contents.
            expected_prefix = str(expected_sha or "").lower()[:7]
            if not expected_prefix or not root.lower().endswith(expected_prefix):
                raise GitHubConfigError(
                    "The GitHub repository ZIP does not match the resolved commit."
                )
            def control_file(name, maximum):
                matches = [
                    info for info in archive.infolist()
                    if info.filename.replace("\\", "/") == f"{root}/{name}"
                ]
                if not matches:
                    return None
                if len(matches) != 1 or matches[0].file_size > maximum:
                    raise GitHubConfigError(
                        f"The repository {name} file is invalid or too large."
                    )
                mode = (matches[0].external_attr >> 16) & 0o170000
                if mode == stat.S_IFLNK:
                    raise GitHubConfigError(
                        f"The repository {name} file cannot be a symbolic link."
                    )
                try:
                    return archive.read(matches[0]).decode("utf-8")
                except (UnicodeDecodeError, RuntimeError, zipfile.BadZipFile) as error:
                    raise GitHubConfigError(
                        f"The repository {name} file must be valid UTF-8."
                    ) from error

            return (
                root,
                control_file(".gitmodules", 1024 * 1024),
                control_file(".solderpyignore", MAX_IGNORE_SIZE),
            )

    def _is_ignored(self, target):
        path = target.as_posix()
        for prefix, matcher in self._ignore_scopes:
            if prefix:
                if not path.startswith(f"{prefix}/"):
                    continue
                relative = path[len(prefix) + 1:]
            else:
                relative = path
            if matcher.ignores(relative):
                return True
        return False

    def _extract(self, archive_path, staging, prefix="", expected_sha=None):
        root, modules, ignore_content = self._root_metadata(
            archive_path, expected_sha
        )
        if ignore_content is not None:
            self._ignore_scopes.append((prefix, SolderPyIgnore(ignore_content)))
        try:
            source = zipfile.ZipFile(archive_path, "r")
        except (OSError, zipfile.BadZipFile) as error:
            raise GitHubConfigError("GitHub returned an invalid repository ZIP.") from error
        with source:
            for info in source.infolist():
                normalized = info.filename.replace("\\", "/")
                path = PurePosixPath(normalized)
                if not path.parts or path.parts[0] != root or len(path.parts) == 1:
                    continue
                relative = _safe_relative_path(PurePosixPath(*path.parts[1:]).as_posix())
                if relative.as_posix() == ".gitmodules" or info.is_dir():
                    continue
                mode = (info.external_attr >> 16) & 0o170000
                if mode == stat.S_IFLNK:
                    raise GitHubConfigError("The GitHub repository contains a symbolic link.")
                target = _safe_relative_path(
                    f"{prefix}/{relative.as_posix()}" if prefix else relative.as_posix()
                )
                if not _package_path_allowed(target) or self._is_ignored(target):
                    continue
                self._file_count += 1
                self._expanded_size += info.file_size
                if self._file_count > MAX_ARCHIVE_FILES:
                    raise GitHubConfigError("The config package contains too many files.")
                if self._expanded_size > MAX_EXPANDED_SIZE:
                    raise GitHubConfigError("The config package expands beyond 1 GiB.")
                destination = Path(staging, *target.parts)
                if destination.exists():
                    raise GitHubConfigError(
                        f'The config package contains the path "{target.as_posix()}" more than once.'
                    )
                destination.parent.mkdir(parents=True, exist_ok=True)
                try:
                    with source.open(info, "r") as incoming, destination.open("wb") as outgoing:
                        shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
                except (OSError, RuntimeError, zipfile.BadZipFile) as error:
                    raise GitHubConfigError("The GitHub repository ZIP could not be unpacked.") from error
        return modules

    @staticmethod
    def _submodule_urls(modules):
        if not modules:
            return {}
        parser = ConfigParser(interpolation=None)
        try:
            parser.read_string(modules)
        except Exception as error:
            raise GitHubConfigError("The repository has an invalid .gitmodules file.") from error
        result = {}
        for section in parser.sections():
            path = parser.get(section, "path", fallback="").strip()
            url = parser.get(section, "url", fallback="").strip()
            if not path or not url:
                continue
            safe_path = _safe_relative_path(path).as_posix()
            result[safe_path] = _safe_repository_from_git_url(url)
        return result

    def _add_repository(self, repository, reference, staging, prefix, depth, temporary):
        if depth:
            self._submodule_count += 1
            if self._submodule_count > MAX_SUBMODULES:
                raise GitHubConfigError(
                    "The config package contains too many submodules."
                )
        archive_path = Path(temporary, f"archive-{hashlib.sha256((repository.full_name + reference.sha + prefix).encode()).hexdigest()}.zip")
        self.client.download_archive(repository, reference.sha, archive_path)
        modules = self._extract(
            archive_path, staging, prefix, expected_sha=reference.sha
        )
        if not modules or depth >= MAX_SUBMODULE_DEPTH:
            return
        module_urls = self._submodule_urls(modules)
        if not module_urls:
            return
        for path, sha in self.client.gitlinks(repository, reference.tree_sha):
            submodule_name = module_urls.get(path)
            if not submodule_name:
                continue
            submodule = self.client.repository(submodule_name)
            sub_reference = self.client.resolve_ref(submodule, sha)
            sub_prefix = f"{prefix}/{path}" if prefix else path
            self._add_repository(
                submodule,
                sub_reference,
                staging,
                sub_prefix,
                depth + 1,
                temporary,
            )

    @staticmethod
    def _write_deterministic(staging, destination):
        files = sorted(path for path in Path(staging).rglob("*") if path.is_file())
        if not files:
            raise GitHubConfigError(
                "The GitHub repository contains no files for a config package."
            )
        try:
            with zipfile.ZipFile(
                destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
            ) as target:
                for path in files:
                    relative = path.relative_to(staging).as_posix()
                    info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o100644 << 16
                    with path.open("rb") as incoming, target.open(info, "w") as outgoing:
                        shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
        except (OSError, RuntimeError, zipfile.BadZipFile) as error:
            Path(destination).unlink(missing_ok=True)
            raise GitHubConfigError("The Solder config package could not be created.") from error

    def build(self, repository, reference, destination):
        # A pack builder may be reused by synchronization jobs. Limits and
        # ignore scopes apply to one output, not the lifetime of the object.
        self._expanded_size = 0
        self._file_count = 0
        self._submodule_count = 0
        self._ignore_scopes = []
        with tempfile.TemporaryDirectory(prefix="solder-github-config-") as temporary:
            staging = Path(temporary, "package")
            staging.mkdir()
            self._add_repository(
                repository,
                reference,
                staging,
                "",
                0,
                temporary,
            )
            self._write_deterministic(staging, destination)
        return Path(destination)
