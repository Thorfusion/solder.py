from dataclasses import dataclass
import io
import json
import re

from .database import Database


_PROJECT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SIDES = {"CLIENT", "SERVER", "BOTH"}
SYNC_MANIFEST_FORMAT = "solder.py-modrinth-curseforge-sync"
SYNC_MANIFEST_VERSION = 1
MAX_SYNC_MANIFEST_SIZE = 256 * 1024
MAX_SYNC_MAPPINGS = 500


class PlatformExportOverrideError(ValueError):
    """Raised when a native platform export override cannot be saved."""


@dataclass(frozen=True)
class PlatformExportOverride:
    id: int
    name: str
    modrinth_project_id: str
    curseforge_project_id: int
    side: str
    enabled: bool
    built_in: bool
    override_solder_only: bool = False
    created_at: object = None
    updated_at: object = None

    @classmethod
    def from_row(cls, row):
        return cls(
            id=int(row["id"]),
            name=str(row["name"]),
            modrinth_project_id=str(row["modrinth_project_id"]),
            curseforge_project_id=int(row["curseforge_project_id"]),
            side=str(row.get("side") or "BOTH").upper(),
            enabled=bool(row.get("enabled")),
            override_solder_only=bool(row.get("override_solder_only")),
            built_in=bool(row.get("built_in")),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
        )

    @staticmethod
    def _values(name, modrinth_project_id, curseforge_project_id, side):
        name = str(name or "").strip()
        project_id = str(modrinth_project_id or "").strip()
        side = str(side or "BOTH").strip().upper()
        try:
            curseforge_project_id = int(curseforge_project_id)
        except (TypeError, ValueError) as error:
            raise PlatformExportOverrideError(
                "Enter a numeric CurseForge project ID."
            ) from error
        if not name or len(name) > 255:
            raise PlatformExportOverrideError(
                "The override name must contain 1 to 255 characters."
            )
        if not _PROJECT_ID.fullmatch(project_id):
            raise PlatformExportOverrideError(
                "The Modrinth project ID is invalid."
            )
        if curseforge_project_id <= 0:
            raise PlatformExportOverrideError(
                "Enter a numeric CurseForge project ID."
            )
        if side not in _SIDES:
            raise PlatformExportOverrideError("Select a valid mod side.")
        return name, project_id, curseforge_project_id, side

    @classmethod
    def get_all(cls, *, enabled=None):
        conn = Database.get_connection()
        if conn is None:
            return []
        cursor = conn.cursor(dictionary=True)
        try:
            query = "SELECT * FROM platform_export_overrides"
            parameters = ()
            if enabled is not None:
                query += " WHERE enabled = %s"
                parameters = (1 if enabled else 0,)
            query += " ORDER BY built_in DESC, name, id"
            if parameters:
                cursor.execute(query, parameters)
            else:
                cursor.execute(query)
            return [cls.from_row(row) for row in (cursor.fetchall() or [])]
        finally:
            cursor.close()
            conn.close()

    @classmethod
    def get_enabled(cls):
        return cls.get_all(enabled=True)

    @classmethod
    def render_manifest(cls):
        mappings = cls.get_all()
        if len(mappings) > MAX_SYNC_MAPPINGS:
            raise PlatformExportOverrideError(
                f"The sync list contains more than {MAX_SYNC_MAPPINGS} mappings."
            )
        payload = {
            "format": SYNC_MANIFEST_FORMAT,
            "version": SYNC_MANIFEST_VERSION,
            "mappings": [
                {
                    "name": mapping.name,
                    "modrinth_project_id": mapping.modrinth_project_id,
                    "curseforge_project_id": mapping.curseforge_project_id,
                    "side": mapping.side,
                    "enabled": mapping.enabled,
                    "override_solder_only": mapping.override_solder_only,
                    "built_in": mapping.built_in,
                }
                for mapping in mappings
            ],
        }
        content = (
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        if len(content) > MAX_SYNC_MANIFEST_SIZE:
            raise PlatformExportOverrideError(
                "The sync list exceeds 256 KiB."
            )
        return io.BytesIO(content)

    @classmethod
    def _parse_manifest(cls, uploaded_file):
        if uploaded_file is None:
            raise PlatformExportOverrideError(
                "Select a Modrinth-CurseForge sync JSON file."
            )
        content = uploaded_file.read(MAX_SYNC_MANIFEST_SIZE + 1)
        if len(content) > MAX_SYNC_MANIFEST_SIZE:
            raise PlatformExportOverrideError(
                "The sync list exceeds 256 KiB."
            )
        try:
            payload = json.loads(content.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise PlatformExportOverrideError(
                "The sync list must be valid UTF-8 JSON."
            ) from error
        if not isinstance(payload, dict):
            raise PlatformExportOverrideError(
                "The sync list root must be an object."
            )
        if payload.get("format") != SYNC_MANIFEST_FORMAT:
            raise PlatformExportOverrideError(
                f'The sync list format must be "{SYNC_MANIFEST_FORMAT}".'
            )
        if payload.get("version") != SYNC_MANIFEST_VERSION:
            raise PlatformExportOverrideError(
                f"Only sync list version {SYNC_MANIFEST_VERSION} is supported."
            )
        entries = payload.get("mappings")
        if not isinstance(entries, list):
            raise PlatformExportOverrideError(
                '"mappings" must be a list.'
            )
        if len(entries) > MAX_SYNC_MAPPINGS:
            raise PlatformExportOverrideError(
                f"A sync list may contain at most {MAX_SYNC_MAPPINGS} mappings."
            )

        parsed = []
        modrinth_ids = set()
        curseforge_ids = set()
        for index, entry in enumerate(entries, start=1):
            if not isinstance(entry, dict):
                raise PlatformExportOverrideError(
                    f"Mapping {index} must be an object."
                )
            try:
                values = cls._values(
                    entry.get("name"),
                    entry.get("modrinth_project_id"),
                    entry.get("curseforge_project_id"),
                    entry.get("side"),
                )
            except PlatformExportOverrideError as error:
                raise PlatformExportOverrideError(
                    f"Mapping {index}: {error}"
                ) from error
            enabled = entry.get("enabled", False)
            override_solder_only = entry.get("override_solder_only", False)
            if not isinstance(enabled, bool) or not isinstance(
                override_solder_only, bool
            ):
                raise PlatformExportOverrideError(
                    f"Mapping {index}: enabled and override_solder_only must be true or false."
                )
            if values[1] in modrinth_ids or values[2] in curseforge_ids:
                raise PlatformExportOverrideError(
                    f"Mapping {index}: provider project IDs must be unique in the list."
                )
            modrinth_ids.add(values[1])
            curseforge_ids.add(values[2])
            parsed.append((*values, enabled, override_solder_only))
        return parsed

    @classmethod
    def import_manifest(cls, uploaded_file):
        mappings = cls._parse_manifest(uploaded_file)
        conn = Database.get_connection()
        if conn is None:
            raise PlatformExportOverrideError(
                "Could not connect to the database."
            )
        cursor = conn.cursor(dictionary=True)
        created = 0
        updated = 0
        try:
            for (
                name,
                modrinth_project_id,
                curseforge_project_id,
                side,
                enabled,
                override_solder_only,
            ) in mappings:
                cursor.execute(
                    "SELECT id FROM platform_export_overrides "
                    "WHERE modrinth_project_id = %s "
                    "OR curseforge_project_id = %s FOR UPDATE",
                    (modrinth_project_id, curseforge_project_id),
                )
                existing = cursor.fetchall() or []
                if len(existing) > 1:
                    raise PlatformExportOverrideError(
                        f'"{name}" conflicts with two existing mappings.'
                    )
                values = (
                    name,
                    modrinth_project_id,
                    curseforge_project_id,
                    side,
                    int(enabled),
                    int(override_solder_only),
                )
                if existing:
                    cursor.execute(
                        """UPDATE platform_export_overrides
                           SET name = %s, modrinth_project_id = %s,
                               curseforge_project_id = %s, side = %s,
                               enabled = %s, override_solder_only = %s
                           WHERE id = %s""",
                        (*values, existing[0]["id"]),
                    )
                    updated += 1
                else:
                    cursor.execute(
                        """INSERT INTO platform_export_overrides
                                  (name, modrinth_project_id,
                                   curseforge_project_id, side, enabled,
                                   override_solder_only, built_in)
                           VALUES (%s, %s, %s, %s, %s, %s, 0)""",
                        values,
                    )
                    created += 1
            conn.commit()
            return created, updated
        except PlatformExportOverrideError:
            conn.rollback()
            raise
        except Exception as error:
            conn.rollback()
            raise PlatformExportOverrideError(
                "The Modrinth-CurseForge sync list could not be imported."
            ) from error
        finally:
            cursor.close()
            conn.close()

    @classmethod
    def create(
        cls,
        name,
        modrinth_project_id,
        curseforge_project_id,
        side="BOTH",
        override_solder_only=False,
    ):
        values = cls._values(
            name, modrinth_project_id, curseforge_project_id, side
        )
        conn = Database.get_connection()
        if conn is None:
            raise PlatformExportOverrideError(
                "Could not connect to the database."
            )
        cursor = conn.cursor()
        try:
            cursor.execute(
                """INSERT INTO platform_export_overrides
                   (name, modrinth_project_id, curseforge_project_id, side,
                    override_solder_only)
                   VALUES (%s, %s, %s, %s, %s)""",
                (*values, 1 if override_solder_only else 0),
            )
            conn.commit()
        except Exception as error:
            conn.rollback()
            raise PlatformExportOverrideError(
                "That Modrinth or CurseForge project is already mapped."
            ) from error
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def set_enabled(override_id, enabled):
        conn = Database.get_connection()
        if conn is None:
            raise PlatformExportOverrideError(
                "Could not connect to the database."
            )
        cursor = conn.cursor()
        try:
            cursor.execute(
                "UPDATE platform_export_overrides SET enabled = %s WHERE id = %s",
                (1 if enabled else 0, int(override_id)),
            )
            if cursor.rowcount != 1:
                raise PlatformExportOverrideError(
                    "The export override no longer exists."
                )
            conn.commit()
        except PlatformExportOverrideError:
            conn.rollback()
            raise
        except Exception as error:
            conn.rollback()
            raise PlatformExportOverrideError(
                "Could not update the export override."
            ) from error
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def set_override_solder_only(override_id, enabled):
        conn = Database.get_connection()
        if conn is None:
            raise PlatformExportOverrideError(
                "Could not connect to the database."
            )
        cursor = conn.cursor()
        try:
            cursor.execute(
                "UPDATE platform_export_overrides "
                "SET override_solder_only = %s WHERE id = %s",
                (1 if enabled else 0, int(override_id)),
            )
            if cursor.rowcount != 1:
                raise PlatformExportOverrideError(
                    "The export override no longer exists."
                )
            conn.commit()
        except PlatformExportOverrideError:
            conn.rollback()
            raise
        except Exception as error:
            conn.rollback()
            raise PlatformExportOverrideError(
                "Could not update the export override."
            ) from error
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def delete(override_id):
        conn = Database.get_connection()
        if conn is None:
            raise PlatformExportOverrideError(
                "Could not connect to the database."
            )
        cursor = conn.cursor()
        try:
            cursor.execute(
                "DELETE FROM platform_export_overrides "
                "WHERE id = %s AND built_in = 0",
                (int(override_id),),
            )
            if cursor.rowcount != 1:
                raise PlatformExportOverrideError(
                    "Built-in sync mappings cannot be deleted."
                )
            conn.commit()
        except PlatformExportOverrideError:
            conn.rollback()
            raise
        except Exception as error:
            conn.rollback()
            raise PlatformExportOverrideError(
                "Could not delete the export override."
            ) from error
        finally:
            cursor.close()
            conn.close()
