"""Validated batch imports for Modrinth and standard Maven repositories."""

from dataclasses import dataclass, field
import io
import json
from mysql.connector import IntegrityError

from .compatibility import InvalidModloaderError, normalize_modloader
from .database import Database
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

    def as_dict(self):
        entry = {"provider": self.provider.lower()}
        if self.provider == MODRINTH:
            entry["project_id"] = self.project_id
        else:
            entry.update(
                {
                    "repository": {
                        "name": self.repository_name,
                        "url": self.repository_url,
                    },
                    "group_id": self.group_id,
                    "artifact_id": self.artifact_id,
                    "extension": self.extension,
                }
            )
            if self.classifier:
                entry["classifier"] = self.classifier
            if self.modloader:
                entry["modloader"] = self.modloader

            minecraft = {"mode": self.version_mode}
            if self.version_mode == "EMBEDDED":
                minecraft["pattern"] = self.version_pattern
            elif self.version_mode == "FIXED":
                minecraft["version"] = self.fixed_minecraft
            entry["minecraft"] = minecraft

        entry.update(self.metadata)
        return entry


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
    def from_database(cls):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT mods.id, mods.name, mods.pretty_name,
                          mods.description, mods.author, mods.link, mods.side,
                          mods.integration_provider,
                          mods.integration_project_id,
                          maven_artifacts.id AS maven_artifact_id,
                          maven_artifacts.group_id,
                          maven_artifacts.artifact_id,
                          maven_artifacts.classifier,
                          maven_artifacts.extension,
                          maven_artifacts.version_mode,
                          maven_artifacts.version_pattern,
                          maven_artifacts.fixed_minecraft,
                          maven_artifacts.modloader,
                          maven_repositories.name AS repository_name,
                          maven_repositories.base_url AS repository_url
                   FROM mods
                   LEFT JOIN maven_artifacts
                       ON maven_artifacts.mod_id = mods.id
                   LEFT JOIN maven_repositories
                       ON maven_repositories.id = maven_artifacts.repository_id
                   WHERE mods.integration_provider IN (%s, %s)
                   ORDER BY LOWER(COALESCE(NULLIF(mods.pretty_name, ''),
                                           mods.name)),
                            LOWER(mods.name), mods.id""",
                (MODRINTH, MAVEN),
            )
            rows = cur.fetchall() or []
        finally:
            cur.close()
            conn.close()

        if not rows:
            raise IntegrationManifestError(
                "No Modrinth or Maven mods are configured."
            )
        if len(rows) > MAX_MANIFEST_MODS:
            raise IntegrationManifestError(
                f"The export contains more than {MAX_MANIFEST_MODS} mods."
            )

        mods = []
        for row in rows:
            provider = str(row["integration_provider"]).upper()
            metadata = {
                "name": cls._export_text(
                    row.get("pretty_name") or row.get("name")
                ),
                "description": cls._export_text(row.get("description")),
                "author": cls._export_text(row.get("author")),
                "link": cls._export_text(row.get("link")),
                "side": cls._export_text(row.get("side")) or "BOTH",
            }
            if provider == MODRINTH:
                project_id = cls._export_text(row.get("integration_project_id"))
                if not project_id:
                    raise IntegrationManifestError(
                        f'Modrinth mod "{metadata["name"]}" has no project ID.'
                    )
                mods.append(
                    ManifestMod(
                        provider=MODRINTH,
                        project_id=project_id,
                        **metadata,
                    )
                )
                continue

            required_maven_fields = (
                "maven_artifact_id",
                "repository_name",
                "repository_url",
                "group_id",
                "artifact_id",
            )
            if any(not row.get(field) for field in required_maven_fields):
                raise IntegrationManifestError(
                    f'Maven mod "{metadata["name"]}" has no attached artifact.'
                )
            mods.append(
                ManifestMod(
                    provider=MAVEN,
                    repository_name=str(row["repository_name"]),
                    repository_url=str(row["repository_url"]),
                    group_id=str(row["group_id"]),
                    artifact_id=str(row["artifact_id"]),
                    classifier=str(row.get("classifier") or ""),
                    extension=str(row.get("extension") or "jar"),
                    version_mode=str(row.get("version_mode") or "MANUAL"),
                    version_pattern=str(
                        row.get("version_pattern") or DEFAULT_VERSION_PATTERN
                    ),
                    fixed_minecraft=cls._export_text(
                        row.get("fixed_minecraft")
                    ),
                    modloader=cls._export_text(row.get("modloader")),
                    **metadata,
                )
            )
        return cls(mods)

    @staticmethod
    def _export_text(value):
        if value is None:
            return None
        return str(value).strip() or None

    def payload(self):
        return {
            "format": MANIFEST_FORMAT,
            "version": MANIFEST_VERSION,
            "mods": [mod.as_dict() for mod in self.mods],
        }

    def render(self):
        content = (
            json.dumps(self.payload(), ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        if len(content) > MAX_MANIFEST_SIZE:
            raise IntegrationManifestError("The exported manifest exceeds 512 KiB.")
        # Keep export and import as one contract. This is local validation only;
        # parsing a manifest does not make provider requests.
        type(self).parse(io.BytesIO(content))
        return io.BytesIO(content)

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
