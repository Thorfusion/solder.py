import re

from .database import Database


_PROVIDER = re.compile(r"^[A-Z][A-Z0-9_]{0,31}$")
_VERSION_ID = re.compile(r"^[A-Za-z0-9_-]{1,191}$")


class ModversionProviderIdError(ValueError):
    """Raised when a provider version identifier cannot be stored."""


class ModversionProviderId:
    CURSEFORGE = "CURSEFORGE"

    @staticmethod
    def _values(provider, project_id, version_id):
        provider = str(provider or "").strip().upper()
        project_id = str(project_id or "").strip()
        version_id = str(version_id or "").strip()
        if _PROVIDER.fullmatch(provider) is None:
            raise ModversionProviderIdError("The provider is invalid.")
        if _VERSION_ID.fullmatch(version_id) is None:
            raise ModversionProviderIdError(
                "The provider version ID is invalid."
            )
        if _VERSION_ID.fullmatch(project_id) is None:
            raise ModversionProviderIdError(
                "The provider project ID is invalid."
            )
        return provider, project_id, version_id

    @classmethod
    def get(cls, modversion_id, provider, project_id=None):
        provider = str(provider or "").strip().upper()
        if _PROVIDER.fullmatch(provider) is None:
            return None
        conn = Database.get_connection()
        if conn is None:
            return None
        cursor = conn.cursor(dictionary=True)
        try:
            query = (
                "SELECT version_id FROM modversion_provider_ids "
                "WHERE modversion_id = %s AND provider = %s"
            )
            parameters = [int(modversion_id), provider]
            if project_id is not None:
                query += " AND project_id = %s"
                parameters.append(str(project_id).strip())
            cursor.execute(query, tuple(parameters))
            row = cursor.fetchone()
            return str(row["version_id"]) if row else None
        finally:
            cursor.close()
            conn.close()

    @classmethod
    def get_modrinth_project_versions(
        cls, project_id, provider, provider_project_id
    ):
        provider = str(provider or "").strip().upper()
        project_id = str(project_id or "").strip()
        provider_project_id = str(provider_project_id or "").strip()
        if (
            _PROVIDER.fullmatch(provider) is None
            or not project_id
            or _VERSION_ID.fullmatch(provider_project_id) is None
        ):
            return []
        conn = Database.get_connection()
        if conn is None:
            return []
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                """SELECT modversions.id,
                          modversions.version,
                          modversions.mcversion,
                          modversions.modloader,
                          modversions.integration_version_id,
                          provider_ids.version_id AS provider_version_id,
                          (SELECT GROUP_CONCAT(
                                      compatibility.minecraft_version
                                      ORDER BY compatibility.minecraft_version
                                      SEPARATOR ',')
                             FROM modversion_minecraft_versions compatibility
                            WHERE compatibility.modversion_id = modversions.id)
                              AS minecraft_versions,
                          provider_ids.project_id AS provider_project_id
                     FROM mods
                     INNER JOIN modversions ON modversions.mod_id = mods.id
                     INNER JOIN modversion_provider_ids provider_ids
                        ON provider_ids.modversion_id = modversions.id
                        AND provider_ids.provider = %s
                        AND provider_ids.project_id = %s
                    WHERE mods.integration_provider = 'MODRINTH'
                      AND mods.integration_project_id = %s
                    ORDER BY modversions.id DESC""",
                (provider, provider_project_id, project_id),
            )
            return cursor.fetchall() or []
        finally:
            cursor.close()
            conn.close()

    @classmethod
    def save(cls, modversion_id, provider, project_id, version_id):
        provider, project_id, version_id = cls._values(
            provider, project_id, version_id
        )
        if provider == cls.CURSEFORGE:
            try:
                numeric_project_id = int(project_id)
                numeric_id = int(version_id)
            except (TypeError, ValueError) as error:
                raise ModversionProviderIdError(
                    "Enter a numeric CurseForge file ID."
                ) from error
            if numeric_project_id <= 0 or numeric_id <= 0:
                raise ModversionProviderIdError(
                    "Enter a numeric CurseForge file ID."
                )
            project_id = str(numeric_project_id)
            version_id = str(numeric_id)

        conn = Database.get_connection()
        if conn is None:
            raise ModversionProviderIdError(
                "Could not connect to the database."
            )
        cursor = conn.cursor()
        try:
            cursor.execute(
                """INSERT INTO modversion_provider_ids
                          (modversion_id, provider, project_id, version_id)
                   VALUES (%s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE
                       project_id = VALUES(project_id),
                       version_id = VALUES(version_id)""",
                (int(modversion_id), provider, project_id, version_id),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()
        return version_id

    @classmethod
    def delete(cls, modversion_id, provider):
        provider = str(provider or "").strip().upper()
        if _PROVIDER.fullmatch(provider) is None:
            raise ModversionProviderIdError("The provider is invalid.")
        conn = Database.get_connection()
        if conn is None:
            raise ModversionProviderIdError(
                "Could not connect to the database."
            )
        cursor = conn.cursor()
        try:
            cursor.execute(
                """DELETE FROM modversion_provider_ids
                    WHERE modversion_id = %s AND provider = %s""",
                (int(modversion_id), provider),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()
