"""Validated batch imports for Modrinth and standard Maven repositories."""

from dataclasses import dataclass, field
import json
from mysql.connector import IntegrityError

from .compatibility import InvalidModloaderError, normalize_modloader
from .integration import IntegrationError, MAVEN, MODRINTH, ModIntegration
from .maven import (
    DEFAULT_VERSION_PATTERN,
    MavenArtifact,
    MavenCatalog,
    MavenError,
    MavenRepository,
    normalize_base_url,
    validate_coordinates,
    validate_version_rule,
)


MANIFEST_FORMAT = "solder.py-integration-manifest"
MANIFEST_VERSION = 1
MAX_MANIFEST_SIZE = 512 * 1024
MAX_MANIFEST_MODS = 250
_SIDES = {"BOTH", "CLIENT", "SERVER"}


class IntegrationManifestError(ValueError):
    """Raised when a manifest is malformed or cannot be safely imported."""


@dataclass(frozen=True)
class ManifestMod:
    provider: str
    project_id: str | None = None
    repository_name: str | None = None
    repository_url: str | None = None
    group_id: str | None = None
    artifact_id: str | None = None
    classifier: str = ""
    extension: str = "jar"
    version_mode: str = "MANUAL"
    version_pattern: str = DEFAULT_VERSION_PATTERN
    fixed_minecraft: str | None = None
    modloader: str | None = None
    name: str | None = None
    description: str | None = None
    author: str | None = None
    link: str | None = None
    side: str | None = None

    @property
    def display_name(self):
        return self.name or self.artifact_id or self.project_id or self.provider

    @property
    def metadata(self):
        return {
            key: value
            for key, value in {
                "name": self.name,
                "description": self.description,
                "author": self.author,
                "link": self.link,
                "side": self.side,
            }.items()
            if value is not None
        }


@dataclass
class ManifestImportResult:
    created: int = 0
    existing: int = 0
    errors: list[str] = field(default_factory=list)


def _text(data, field_name, *, required=False, maximum=255):
    value = data.get(field_name)
    if value is None:
        if required:
            raise IntegrationManifestError(f'"{field_name}" is required.')
        return None
    if not isinstance(value, str):
        raise IntegrationManifestError(f'"{field_name}" must be text.')
    value = value.strip()
    if required and not value:
        raise IntegrationManifestError(f'"{field_name}" is required.')
    if len(value) > maximum:
        raise IntegrationManifestError(
            f'"{field_name}" may not exceed {maximum} characters.'
        )
    return value or None


def _metadata(data):
    side = _text(data, "side", maximum=16)
    if side:
        side = side.upper()
        if side not in _SIDES:
            raise IntegrationManifestError(
                '"side" must be BOTH, CLIENT, or SERVER.'
            )
    link = _text(data, "link")
    if link:
        try:
            link = normalize_base_url(link).rstrip("/")
        except MavenError as error:
            raise IntegrationManifestError(
                '"link" must be an HTTP or HTTPS URL without credentials.'
            ) from error
    return {
        "name": _text(data, "name"),
        "description": _text(data, "description"),
        "author": _text(data, "author"),
        "link": link,
        "side": side,
    }


class IntegrationManifest:
    def __init__(self, mods):
        self.mods = tuple(mods)

    @property
    def includes_maven(self):
        return any(mod.provider == MAVEN for mod in self.mods)

    @classmethod
    def parse(cls, uploaded_file):
        if uploaded_file is None:
            raise IntegrationManifestError("Select a JSON manifest to import.")
        content = uploaded_file.read(MAX_MANIFEST_SIZE + 1)
        if len(content) > MAX_MANIFEST_SIZE:
            raise IntegrationManifestError("The manifest exceeds 512 KiB.")
        try:
            payload = json.loads(content.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise IntegrationManifestError(
                "The manifest must be valid UTF-8 JSON."
            ) from error
        if not isinstance(payload, dict):
            raise IntegrationManifestError("The manifest root must be an object.")
        if payload.get("format") != MANIFEST_FORMAT:
            raise IntegrationManifestError(
                f'The manifest format must be "{MANIFEST_FORMAT}".'
            )
        if payload.get("version") != MANIFEST_VERSION:
            raise IntegrationManifestError(
                f"Only manifest version {MANIFEST_VERSION} is supported."
            )
        entries = payload.get("mods")
        if not isinstance(entries, list) or not entries:
            raise IntegrationManifestError('"mods" must be a non-empty list.')
        if len(entries) > MAX_MANIFEST_MODS:
            raise IntegrationManifestError(
                f"A manifest may contain at most {MAX_MANIFEST_MODS} mods."
            )

        parsed = []
        for index, entry in enumerate(entries, start=1):
            try:
                parsed.append(cls._entry(entry))
            except (IntegrationManifestError, MavenError, InvalidModloaderError) as error:
                raise IntegrationManifestError(
                    f"Mod {index}: {error}"
                ) from error
        return cls(parsed)

    @staticmethod
    def _entry(entry):
        if not isinstance(entry, dict):
            raise IntegrationManifestError("Each mod must be an object.")
        provider = _text(entry, "provider", required=True, maximum=16).upper()
        metadata = _metadata(entry)
        if provider == MODRINTH:
            project_id = _text(
                entry, "project_id", required=True, maximum=64
            )
            # Uses the same identifier rules as interactive Modrinth imports.
            from .integration import external_id

            project_id = external_id(project_id)
            return ManifestMod(
                provider=provider, project_id=project_id, **metadata
            )
        if provider != MAVEN:
            raise IntegrationManifestError(
                '"provider" must be "modrinth" or "maven".'
            )

        repository = entry.get("repository")
        if not isinstance(repository, dict):
            raise IntegrationManifestError(
                'Maven entries require a "repository" object.'
            )
        repository_name = _text(
            repository, "name", required=True, maximum=255
        )
        repository_url = normalize_base_url(
            _text(repository, "url", required=True, maximum=2048)
        )
        group_id, artifact_id, classifier, extension = validate_coordinates(
            _text(entry, "group_id", required=True),
            _text(entry, "artifact_id", required=True),
            _text(entry, "classifier", maximum=128) or "",
            _text(entry, "extension", maximum=16) or "jar",
        )
        if extension != "jar":
            raise IntegrationManifestError(
                "Maven mod artifacts must use the JAR extension."
            )
        mapping = entry.get("minecraft", {})
        if not isinstance(mapping, dict):
            raise IntegrationManifestError('"minecraft" must be an object.')
        version_mode, version_pattern, fixed_minecraft = validate_version_rule(
            _text(mapping, "mode", maximum=16) or "MANUAL",
            _text(mapping, "pattern") or DEFAULT_VERSION_PATTERN,
            _text(mapping, "version"),
        )
        return ManifestMod(
            provider=provider,
            repository_name=repository_name,
            repository_url=repository_url,
            group_id=group_id,
            artifact_id=artifact_id,
            classifier=classifier,
            extension=extension,
            version_mode=version_mode,
            version_pattern=version_pattern,
            fixed_minecraft=fixed_minecraft,
            modloader=normalize_modloader(_text(entry, "modloader", maximum=32)),
            **metadata,
        )

    def import_all(self, user_id):
        result = ManifestImportResult()
        for entry in self.mods:
            try:
                created = self._import_entry(entry, user_id)
            except (
                IntegrationError,
                MavenError,
                InvalidModloaderError,
                IntegrityError,
                ValueError,
            ) as error:
                result.errors.append(f"{entry.display_name}: {error}")
            else:
                if created:
                    result.created += 1
                else:
                    result.existing += 1
        return result

    @staticmethod
    def _import_entry(entry, user_id):
        if entry.provider == MODRINTH:
            _mod, created = ModIntegration.import_project(
                MODRINTH,
                entry.project_id,
                user_id,
                metadata=entry.metadata,
            )
            return created

        repository = MavenRepository.get_by_name(entry.repository_name)
        if repository:
            if repository.base_url != entry.repository_url:
                raise IntegrationManifestError(
                    "A repository with this name already uses another URL."
                )
        else:
            repository = MavenRepository.new(
                entry.repository_name, entry.repository_url
            )

        artifact = MavenArtifact.get_by_coordinates(
            repository.id,
            entry.group_id,
            entry.artifact_id,
            entry.classifier,
            entry.extension,
        )
        if artifact and artifact.mod_id:
            return False

        created_artifact = artifact is None
        try:
            if artifact is None:
                artifact = MavenArtifact.new(
                    repository.id,
                    entry.group_id,
                    entry.artifact_id,
                    entry.classifier,
                    entry.extension,
                    entry.version_mode,
                    entry.version_pattern,
                    entry.fixed_minecraft,
                    entry.modloader,
                    None,
                    entry.name or entry.artifact_id,
                    entry.description or "",
                    entry.author or "",
                    entry.link or "",
                    entry.side or "BOTH",
                )
                MavenCatalog.refresh(artifact)
            mod, created = ModIntegration.import_project(
                MAVEN, str(artifact.id), user_id
            )
            MavenArtifact.attach_mod(artifact.id, mod.id)
            return created
        except Exception:
            if created_artifact and artifact is not None:
                MavenArtifact.delete_unlinked(artifact.id)
            raise
