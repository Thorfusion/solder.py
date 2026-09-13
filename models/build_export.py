"""Technic-compatible CSV exports for managed modpack builds."""

from dataclasses import dataclass
import csv
import io

from .database import Database


class BuildExportError(ValueError):
    """Raised when a requested build cannot be exported."""


@dataclass(frozen=True)
class BuildExportInfo:
    id: int
    modpack_slug: str
    version: str


class BuildCsvExport:
    HEADER = ("mod_name", "mod_slug", "version", "md5", "filesize")

    @classmethod
    def load(cls, build_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT builds.id, builds.version,
                          modpacks.slug AS modpack_slug
                   FROM builds
                   INNER JOIN modpacks ON builds.modpack_id = modpacks.id
                   WHERE builds.id = %s""",
                (build_id,),
            )
            build_row = cur.fetchone()
            if build_row is None:
                raise BuildExportError(f"Build {build_id} does not exist.")

            cur.execute(
                """SELECT COALESCE(NULLIF(mods.pretty_name, ''), mods.name)
                              AS mod_name,
                          mods.name AS mod_slug,
                          modversions.version,
                          modversions.md5,
                          modversions.filesize
                   FROM build_modversion
                   INNER JOIN modversions
                       ON build_modversion.modversion_id = modversions.id
                   INNER JOIN mods ON modversions.mod_id = mods.id
                   WHERE build_modversion.build_id = %s
                   ORDER BY LOWER(COALESCE(NULLIF(mods.pretty_name, ''),
                                           mods.name)),
                            LOWER(mods.name), modversions.id""",
                (build_id,),
            )
            rows = cur.fetchall() or []
        finally:
            cur.close()
            conn.close()

        return (
            BuildExportInfo(
                id=build_row["id"],
                modpack_slug=str(build_row["modpack_slug"]),
                version=str(build_row["version"]),
            ),
            rows,
        )

    @classmethod
    def render(cls, rows):
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(cls.HEADER)
        for row in rows:
            writer.writerow(row.get(column, "") for column in cls.HEADER)
        return io.BytesIO(output.getvalue().encode("utf-8"))
