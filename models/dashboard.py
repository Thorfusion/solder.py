"""Read-only management dashboard queries."""

from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from .database import Database


class Dashboard:
    """Build the small operational overview shown on the management home page."""

    @staticmethod
    def _pack_scope(full_access):
        if full_access:
            return "", ()
        return (
            """AND EXISTS (
                   SELECT 1 FROM user_modpack dashboard_access
                   WHERE dashboard_access.user_id = %s
                     AND dashboard_access.modpack_id = modpacks.id
               )""",
            None,
        )

    @staticmethod
    def _activity_sort_value(activity):
        value = activity.get("updated_at")
        return value if isinstance(value, datetime) else datetime.min

    @staticmethod
    def repository_health(public_location, md5_location, r2_bucket=None):
        """Describe repository configuration without making network requests."""
        public_url = urlparse(str(public_location or "").strip())
        public_ready = (
            public_url.scheme.lower() in {"http", "https"}
            and bool(public_url.netloc)
        )

        md5_value = str(md5_location or "").strip()
        md5_url = urlparse(md5_value)
        md5_path = Path(md5_value).expanduser()
        if not md5_value:
            md5_ready = False
            md5_detail = "Not configured"
        elif md5_url.scheme.lower() in {"http", "https"} and md5_url.netloc:
            md5_ready = True
            md5_detail = "Remote source configured"
        elif md5_path.is_absolute():
            md5_ready = md5_path.is_dir()
            md5_detail = (
                "Local path accessible" if md5_ready else "Local path unavailable"
            )
        elif md5_url.scheme:
            md5_ready = False
            md5_detail = "Unsupported location"
        else:
            md5_ready = False
            md5_detail = "Local path must be absolute"

        return [
            {
                "name": "Public repository",
                "ready": public_ready,
                "detail": "Configured" if public_ready else "Not configured",
            },
            {
                "name": "Integrity source",
                "ready": md5_ready,
                "detail": md5_detail,
            },
            {
                "name": "Object storage",
                "ready": True,
                "detail": "Enabled" if r2_bucket else "Using local repository",
            },
            {"name": "Database", "ready": True, "detail": "Connected"},
        ]

    @classmethod
    def load(cls, user_id):
        data = {
            "counts": {},
            "attention": [],
            "marked_build": None,
            "recent": [],
        }
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT solder_full, mods_manage, modpacks_manage
                   FROM user_permissions WHERE user_id = %s""",
                (user_id,),
            )
            permissions = cur.fetchone() or {}
            full_access = bool(permissions.get("solder_full"))
            can_manage_mods = full_access or bool(permissions.get("mods_manage"))
            can_manage_packs = full_access or bool(
                permissions.get("modpacks_manage")
            )

            recent = []
            if can_manage_packs:
                scope, scope_params = cls._pack_scope(full_access)
                params = () if scope_params == () else (user_id,)

                cur.execute(
                    f"""SELECT COUNT(DISTINCT modpacks.id) AS modpacks,
                               COUNT(DISTINCT builds.id) AS builds,
                               COUNT(DISTINCT CASE WHEN builds.is_published = 0
                                                  THEN builds.id END)
                                   AS unpublished_builds
                        FROM modpacks
                        LEFT JOIN builds ON builds.modpack_id = modpacks.id
                        WHERE 1 = 1 {scope}""",
                    params,
                )
                data["counts"].update(cur.fetchone() or {})

                cur.execute(
                    f"""SELECT modpacks.id, modpacks.name,
                               modpacks.recommended, modpacks.latest,
                               CASE
                                   WHEN NULLIF(TRIM(modpacks.recommended), '')
                                        IS NULL THEN 'not set'
                                   WHEN recommended.version IS NULL
                                        THEN 'does not exist'
                                   WHEN recommended.is_published = 0
                                        THEN 'is unpublished'
                               END AS recommended_problem,
                               CASE
                                   WHEN NULLIF(TRIM(modpacks.latest), '')
                                        IS NULL THEN 'not set'
                                   WHEN latest.version IS NULL
                                        THEN 'does not exist'
                                   WHEN latest.is_published = 0
                                        THEN 'is unpublished'
                               END AS latest_problem
                        FROM modpacks
                        LEFT JOIN (
                            SELECT modpack_id, version,
                                   MAX(is_published) AS is_published
                            FROM builds GROUP BY modpack_id, version
                        ) recommended
                            ON recommended.modpack_id = modpacks.id
                           AND recommended.version = modpacks.recommended
                        LEFT JOIN (
                            SELECT modpack_id, version,
                                   MAX(is_published) AS is_published
                            FROM builds GROUP BY modpack_id, version
                        ) latest
                            ON latest.modpack_id = modpacks.id
                           AND latest.version = modpacks.latest
                        WHERE 1 = 1 {scope}
                          AND (
                              NULLIF(TRIM(modpacks.recommended), '') IS NULL
                              OR recommended.version IS NULL
                              OR recommended.is_published = 0
                              OR NULLIF(TRIM(modpacks.latest), '') IS NULL
                              OR latest.version IS NULL
                              OR latest.is_published = 0
                          )
                        ORDER BY modpacks.updated_at DESC, modpacks.id DESC
                        LIMIT 6""",
                    params,
                )
                for row in cur.fetchall() or []:
                    problems = []
                    if row["recommended_problem"]:
                        problems.append(
                            f"Recommended build {row['recommended_problem']}"
                        )
                    if row["latest_problem"]:
                        problems.append(f"Latest build {row['latest_problem']}")
                    data["attention"].append(
                        {
                            "title": row["name"],
                            "detail": "; ".join(problems),
                            "target": "modpack",
                            "target_id": row["id"],
                        }
                    )

                cur.execute(
                    f"""SELECT builds.id, builds.version, builds.minecraft,
                               builds.modloader, builds.forge,
                               builds.is_published, modpacks.name AS modpack_name,
                               COUNT(build_modversion.id) AS mod_count
                        FROM builds
                        INNER JOIN modpacks ON builds.modpack_id = modpacks.id
                        LEFT JOIN build_modversion
                            ON build_modversion.build_id = builds.id
                        WHERE builds.marked = 1 {scope}
                        GROUP BY builds.id, builds.version, builds.minecraft,
                                 builds.modloader, builds.forge,
                                 builds.is_published, modpacks.name
                        ORDER BY builds.id DESC LIMIT 1""",
                    params,
                )
                data["marked_build"] = cur.fetchone()

                cur.execute(
                    f"""SELECT COUNT(DISTINCT builds.id) AS item_count,
                               MIN(builds.id) AS target_id
                        FROM build_modversion
                        INNER JOIN modversions current_version
                            ON build_modversion.modversion_id = current_version.id
                        INNER JOIN builds
                            ON build_modversion.build_id = builds.id
                        INNER JOIN modpacks
                            ON builds.modpack_id = modpacks.id
                        WHERE 1 = 1 {scope}
                          AND EXISTS (
                              SELECT 1 FROM modversions candidate
                              WHERE candidate.mod_id = current_version.mod_id
                                AND candidate.id > current_version.id
                                AND (candidate.mcversion = builds.minecraft
                                     OR candidate.mcversion IS NULL)
                                AND (builds.modloader IS NULL
                                     OR candidate.modloader = builds.modloader
                                     OR candidate.modloader IS NULL)
                          )""",
                    params,
                )
                update_count = cur.fetchone() or {}
                if update_count.get("item_count"):
                    data["attention"].append(
                        {
                            "title": "Stored updates available",
                            "detail": (
                                f"{update_count['item_count']} build(s) contain "
                                "older compatible mod versions"
                            ),
                            "target": "build",
                            "target_id": update_count["target_id"],
                        }
                    )

                cur.execute(
                    f"""SELECT COUNT(DISTINCT builds.id) AS item_count,
                               MIN(builds.id) AS target_id
                        FROM build_modversion
                        INNER JOIN modversions
                            ON build_modversion.modversion_id = modversions.id
                        INNER JOIN builds
                            ON build_modversion.build_id = builds.id
                        INNER JOIN modpacks
                            ON builds.modpack_id = modpacks.id
                        WHERE 1 = 1 {scope}
                          AND (
                              (modversions.mcversion IS NOT NULL
                               AND modversions.mcversion <> builds.minecraft)
                              OR (builds.modloader IS NOT NULL
                                  AND modversions.modloader IS NOT NULL
                                  AND modversions.modloader <> builds.modloader)
                          )""",
                    params,
                )
                incompatible = cur.fetchone() or {}
                if incompatible.get("item_count"):
                    data["attention"].append(
                        {
                            "title": "Incompatible build entries",
                            "detail": (
                                f"{incompatible['item_count']} build(s) contain "
                                "a Minecraft or modloader mismatch"
                            ),
                            "target": "build",
                            "target_id": incompatible["target_id"],
                        }
                    )

                cur.execute(
                    f"""SELECT COUNT(DISTINCT builds.id) AS item_count,
                               MIN(builds.id) AS target_id
                        FROM build_modversion parent_membership
                        INNER JOIN modversions parent_version
                            ON parent_membership.modversion_id = parent_version.id
                        INNER JOIN mod_dependencies
                            ON parent_version.mod_id = mod_dependencies.mod_id
                        INNER JOIN builds
                            ON parent_membership.build_id = builds.id
                        INNER JOIN modpacks
                            ON builds.modpack_id = modpacks.id
                        WHERE 1 = 1 {scope}
                          AND NOT EXISTS (
                              SELECT 1
                              FROM build_modversion dependency_membership
                              INNER JOIN modversions dependency_version
                                  ON dependency_membership.modversion_id =
                                     dependency_version.id
                              WHERE dependency_membership.build_id = builds.id
                                AND dependency_version.mod_id =
                                    mod_dependencies.dependency_mod_id
                          )""",
                    params,
                )
                missing_dependencies = cur.fetchone() or {}
                if missing_dependencies.get("item_count"):
                    data["attention"].append(
                        {
                            "title": "Missing required dependencies",
                            "detail": (
                                f"{missing_dependencies['item_count']} build(s) "
                                "need a declared dependency"
                            ),
                            "target": "build",
                            "target_id": missing_dependencies["target_id"],
                        }
                    )

                cur.execute(
                    f"""SELECT modpacks.id AS item_id,
                               'modpack' AS item_type,
                               modpacks.name AS title,
                               modpacks.slug AS detail,
                               COALESCE(modpacks.updated_at,
                                        modpacks.created_at) AS updated_at
                        FROM modpacks
                        WHERE 1 = 1 {scope}
                        ORDER BY updated_at DESC, modpacks.id DESC LIMIT 8""",
                    params,
                )
                recent.extend(cur.fetchall() or [])

                cur.execute(
                    f"""SELECT builds.id AS item_id,
                               'build' AS item_type,
                               CONCAT(modpacks.name, ' - ', builds.version)
                                   AS title,
                               CONCAT('Minecraft ', builds.minecraft,
                                      CASE
                                          WHEN builds.modloader IS NOT NULL
                                          THEN CONCAT(' / ', builds.modloader)
                                          ELSE ''
                                      END) AS detail,
                               COALESCE(builds.updated_at,
                                        builds.created_at) AS updated_at
                        FROM builds
                        INNER JOIN modpacks ON builds.modpack_id = modpacks.id
                        WHERE 1 = 1 {scope}
                        ORDER BY updated_at DESC, builds.id DESC LIMIT 8""",
                    params,
                )
                recent.extend(cur.fetchall() or [])

            if can_manage_mods:
                cur.execute(
                    """SELECT COUNT(*) AS mods,
                              (SELECT COUNT(*) FROM modversions) AS modversions
                       FROM mods"""
                )
                data["counts"].update(cur.fetchone() or {})

                cur.execute(
                    """SELECT COUNT(*) AS item_count
                       FROM modversions
                       WHERE md5 IS NULL
                          OR md5 NOT REGEXP '^[0-9A-Fa-f]{32}$'
                          OR filesize IS NULL OR filesize <= 0"""
                )
                integrity = cur.fetchone() or {}
                if integrity.get("item_count"):
                    data["attention"].append(
                        {
                            "title": "Incomplete integrity data",
                            "detail": (
                                f"{integrity['item_count']} mod version(s) have "
                                "a missing hash or file size"
                            ),
                        }
                    )

                cur.execute(
                    """SELECT COUNT(*) AS item_count
                       FROM modversions
                       INNER JOIN mods ON modversions.mod_id = mods.id
                       WHERE mods.modtype = 'MCIL'
                         AND (modversions.jarmd5 IS NULL
                              OR modversions.jarmd5 NOT REGEXP
                                  '^[0-9A-Fa-f]{32}$')"""
                )
                mcil = cur.fetchone() or {}
                if mcil.get("item_count"):
                    data["attention"].append(
                        {
                            "title": "MCIL packages need JAR data",
                            "detail": (
                                f"{mcil['item_count']} MCIL mod version(s) are "
                                "not ready for direct JAR installation"
                            ),
                        }
                    )

                cur.execute(
                    """SELECT modversions.mod_id AS item_id,
                              'modversion' AS item_type,
                              CONCAT(mods.pretty_name, ' - ',
                                     modversions.version) AS title,
                              CONCAT(
                                  COALESCE(modversions.mcversion, 'All Minecraft'),
                                  CASE
                                      WHEN modversions.modloader IS NOT NULL
                                      THEN CONCAT(' / ', modversions.modloader)
                                      ELSE ''
                                  END
                              ) AS detail,
                              COALESCE(modversions.updated_at,
                                       modversions.created_at) AS updated_at
                       FROM modversions
                       INNER JOIN mods ON modversions.mod_id = mods.id
                       ORDER BY updated_at DESC, modversions.id DESC LIMIT 8"""
                )
                recent.extend(cur.fetchall() or [])

            data["recent"] = sorted(
                recent, key=cls._activity_sort_value, reverse=True
            )[:8]
            return data
        finally:
            cur.close()
            conn.close()
