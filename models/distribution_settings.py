from .database import Database


class DistributionSettingsError(RuntimeError):
    """Raised when public distribution settings cannot be saved."""


class DistributionSettings:
    """Database-backed switches shared by management and API-only processes."""

    MCIL = "mcil_enabled"
    SOLDERPY_LOADER = "solderpy_loader_enabled"
    PACKWIZ = "packwiz_enabled"
    FILEDIRECTOR = "filedirector_enabled"
    MRPACK = "mrpack_enabled"
    CURSEFORGE = "curseforge_export_enabled"
    PRISM = "prism_export_enabled"
    DEFAULTS = {
        MCIL: False,
        SOLDERPY_LOADER: False,
        PACKWIZ: False,
        FILEDIRECTOR: False,
        MRPACK: False,
        CURSEFORGE: False,
        PRISM: False,
    }

    @staticmethod
    def _enabled(value) -> bool:
        return str(value or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }

    @classmethod
    def get_all(cls) -> dict[str, bool]:
        settings = dict(cls.DEFAULTS)
        conn = Database.get_connection()
        if conn is None:
            return settings

        cursor = conn.cursor(dictionary=True)
        try:
            names = (
                cls.MCIL,
                cls.SOLDERPY_LOADER,
                cls.PACKWIZ,
                cls.FILEDIRECTOR,
                cls.MRPACK,
                cls.CURSEFORGE,
                cls.PRISM,
            )
            cursor.execute(
                "SELECT name, value FROM solder_settings "
                "WHERE name IN (%s, %s, %s, %s, %s, %s, %s)",
                names,
            )
            for row in cursor.fetchall():
                if row["name"] in settings:
                    settings[row["name"]] = cls._enabled(row["value"])
            return settings
        finally:
            cursor.close()
            conn.close()

    @classmethod
    def is_enabled(cls, name: str) -> bool:
        if name not in cls.DEFAULTS:
            raise ValueError("Unknown distribution setting")
        return cls.get_all()[name]

    @classmethod
    def update_exports(
        cls,
        packwiz: bool,
        filedirector: bool,
        mrpack: bool = False,
        curseforge: bool = False,
        mcil: bool = False,
        solderpy_loader: bool = False,
        prism: bool = False,
    ) -> None:
        conn = Database.get_connection()
        if conn is None:
            raise DistributionSettingsError(
                "Could not connect to the database to save export settings."
            )

        cursor = conn.cursor()
        try:
            for name, enabled in (
                (cls.MCIL, mcil),
                (cls.SOLDERPY_LOADER, solderpy_loader),
                (cls.PACKWIZ, packwiz),
                (cls.FILEDIRECTOR, filedirector),
                (cls.MRPACK, mrpack),
                (cls.CURSEFORGE, curseforge),
                (cls.PRISM, prism),
            ):
                value = "1" if enabled else "0"
                cursor.execute(
                    """INSERT INTO solder_settings (name, value)
                       VALUES (%s, %s)
                       ON DUPLICATE KEY UPDATE
                           value = %s,
                           updated_at = CURRENT_TIMESTAMP""",
                    (name, value, value),
                )
            conn.commit()
        except Exception as error:
            conn.rollback()
            raise DistributionSettingsError(
                "Could not save public distribution settings."
            ) from error
        finally:
            cursor.close()
            conn.close()
