from dataclasses import dataclass

from flask import flash

from .build import Build
from .database import Database


@dataclass
class BuildEditorData:
    packbuild: Build
    packbuildname: str
    listmod: list[dict]
    listmodversions: list[dict]
    buildlist: list[dict]


class Build_modversion:
    def __init__(self, id, modversion_id, build_id, created_at, updated_at, optional):
        self.id = id
        self.modversion_id = modversion_id
        self.build_id = build_id
        self.created_at = created_at
        self.updated_at = updated_at
        self.optional = optional

    @staticmethod
    def delete_build_modversion(id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("DELETE FROM build_modversion WHERE id = %s", (id,))
        conn.commit()
        return None

    @staticmethod
    def update_optional(modversion_id, optional, build_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("""UPDATE build_modversion 
            SET optional = %s 
            WHERE modversion_id = %s AND build_id = %s;""", (optional, modversion_id, build_id))
        conn.commit()
        return None

    @staticmethod
    def get_modpack_build(id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """SELECT build_modversion.id, build_modversion.optional, modversions.version, modversions.id AS modverid, mods.name, mods.pretty_name, mods.id AS modid
                FROM build_modversion
                INNER JOIN modversions ON build_modversion.modversion_id = modversions.id
                INNER JOIN mods ON modversions.mod_id = mods.id
                WHERE build_id = %s
                ORDER BY mods.name
            """, (id,))
        rows = cur.fetchall()
        if rows:
            return rows
        return []

    @staticmethod
    def get_build_editor_data(id):
        """Load and organize all data needed by the build editor in linear time."""
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT builds.id, builds.modpack_id, builds.version,
                          builds.created_at, builds.updated_at, builds.minecraft,
                          builds.forge, builds.modloader,
                          builds.is_published, builds.private,
                          builds.min_java, builds.java_runtime,
                          builds.min_memory, builds.marked,
                          modpacks.name AS modpack_name
                   FROM builds
                   INNER JOIN modpacks ON builds.modpack_id = modpacks.id
                   WHERE builds.id = %s""",
                (id,),
            )
            build_row = cur.fetchone()
            if build_row is None:
                return None

            build_row = dict(build_row)
            packbuildname = build_row.pop("modpack_name")
            packbuild = Build(**build_row)

            cur.execute(
                """SELECT build_modversion.id, build_modversion.optional,
                          modversions.version, modversions.id AS modverid,
                          mods.name, mods.pretty_name, mods.id AS modid,
                          mods.integration_provider,
                          COALESCE(maven_repositories.name,
                                   mods.integration_provider)
                              AS integration_label
                   FROM build_modversion
                   INNER JOIN modversions
                       ON build_modversion.modversion_id = modversions.id
                   INNER JOIN mods ON modversions.mod_id = mods.id
                   LEFT JOIN maven_artifacts
                       ON maven_artifacts.mod_id = mods.id
                   LEFT JOIN maven_repositories
                       ON maven_artifacts.repository_id = maven_repositories.id
                   WHERE build_modversion.build_id = %s
                   ORDER BY mods.name""",
                (id,),
            )
            build_rows = cur.fetchall() or []

            cur.execute(
                """SELECT mods.id, mods.name, mods.pretty_name,
                          mods.integration_provider,
                          COALESCE(maven_repositories.name,
                                   mods.integration_provider)
                              AS integration_label
                   FROM mods
                   LEFT JOIN maven_artifacts
                       ON maven_artifacts.mod_id = mods.id
                   LEFT JOIN maven_repositories
                       ON maven_artifacts.repository_id = maven_repositories.id
                   ORDER BY mods.name"""
            )
            mod_rows = cur.fetchall() or []

            cur.execute(
                """SELECT modversions.id, modversions.mod_id,
                          modversions.version, modversions.mcversion,
                          modversions.modloader,
                          COALESCE(maven_repositories.name,
                                   mods.integration_provider)
                              AS integration_label
                   FROM modversions
                   INNER JOIN mods ON modversions.mod_id = mods.id
                   LEFT JOIN maven_artifacts
                       ON maven_artifacts.mod_id = mods.id
                   LEFT JOIN maven_repositories
                       ON maven_artifacts.repository_id = maven_repositories.id
                   WHERE (modversions.mcversion = %s
                          OR modversions.mcversion IS NULL)
                     AND (%s IS NULL
                          OR modversions.modloader = %s
                          OR modversions.modloader IS NULL)
                   ORDER BY modversions.mod_id, modversions.id DESC""",
                (
                    packbuild.minecraft,
                    packbuild.modloader,
                    packbuild.modloader,
                ),
            )
            version_rows = cur.fetchall() or []
        finally:
            cur.close()
            conn.close()

        assigned_mod_ids = {row["modid"] for row in build_rows}
        versions_by_mod = {}
        available_versions = []
        for version in version_rows:
            versions_by_mod.setdefault(version["mod_id"], []).append(version)
            if version["mod_id"] not in assigned_mod_ids:
                available_versions.append(version)

        buildlist = []
        for build_row in build_rows:
            build_entry = dict(build_row)
            build_entry["versions"] = versions_by_mod.get(build_entry["modid"], [])
            buildlist.append(build_entry)

        available_mods = [
            mod for mod in mod_rows if mod["id"] not in assigned_mod_ids
        ]

        return BuildEditorData(
            packbuild=packbuild,
            packbuildname=packbuildname,
            listmod=available_mods,
            listmodversions=available_versions,
            buildlist=buildlist,
        )

    @staticmethod
    def get_integrated_mods(build_id):
        """Return integration-managed mods currently assigned to a build."""
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT DISTINCT mods.id, mods.name, mods.pretty_name
                   FROM build_modversion
                   INNER JOIN modversions
                       ON build_modversion.modversion_id = modversions.id
                   INNER JOIN mods ON modversions.mod_id = mods.id
                   WHERE build_modversion.build_id = %s
                     AND mods.integration_provider IS NOT NULL
                     AND mods.integration_project_id IS NOT NULL
                   ORDER BY mods.name""",
                (build_id,),
            )
            return cur.fetchall() or []
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def update_all_compatible(build_id, preferred_versions=None):
        """Move each build entry to its newest compatible stored version."""
        preferred_versions = preferred_versions or {}
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT build_modversion.id AS membership_id,
                          build_modversion.modversion_id AS current_version_id,
                          current.mod_id,
                          candidate.id AS replacement_version_id
                   FROM build_modversion
                   INNER JOIN modversions AS current
                       ON build_modversion.modversion_id = current.id
                   INNER JOIN builds ON build_modversion.build_id = builds.id
                   INNER JOIN modversions AS candidate
                       ON candidate.mod_id = current.mod_id
                      AND (candidate.mcversion = builds.minecraft
                           OR candidate.mcversion IS NULL)
                      AND (builds.modloader IS NULL
                           OR candidate.modloader = builds.modloader
                           OR candidate.modloader IS NULL)
                   WHERE build_modversion.build_id = %s
                   ORDER BY build_modversion.id, candidate.id DESC
                   FOR UPDATE""",
                (build_id,),
            )
            replacements = {}
            current_versions = {}
            for row in cur.fetchall() or []:
                membership_id = row["membership_id"]
                current_versions[membership_id] = row["current_version_id"]
                preferred_id = preferred_versions.get(row["mod_id"])
                if preferred_id is not None:
                    if row["replacement_version_id"] == preferred_id:
                        replacements[membership_id] = preferred_id
                    else:
                        replacements.setdefault(
                            membership_id, row["replacement_version_id"]
                        )
                else:
                    replacements.setdefault(
                        membership_id, row["replacement_version_id"]
                    )

            updates = [
                (replacement_id, membership_id)
                for membership_id, replacement_id in replacements.items()
                if replacement_id != current_versions[membership_id]
            ]
            if updates:
                cur.executemany(
                    """UPDATE build_modversion
                       SET modversion_id = %s
                       WHERE id = %s""",
                    updates,
                )
            conn.commit()
            return len(updates)
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def get_changelog(previd, id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """SELECT coalesce(build.name1, build.name2) name, build.oldversion, 
                    case
                            when build.name1 is null then 'added'
                            when build.name2 is null then 'removed'
                        when build.name1 is not null AND build.name2 IS NOT NULL THEN 'changed to'
                    end status, build.newversion
                FROM 
                (
                SELECT *
                FROM
                (
                SELECT modversions.version AS oldversion, mods.name AS name1, modversions.id AS modverid
                        FROM build_modversion
                                INNER JOIN modversions ON build_modversion.modversion_id = modversions.id
                                INNER JOIN mods ON modversions.mod_id = mods.id
                                WHERE build_id = %s
                ) AS build1
                LEFT OUTER JOIN 
                (
                SELECT modversions.version AS newversion, mods.name AS name2, modversions.id AS modverid2
                                FROM build_modversion
                                INNER JOIN modversions ON build_modversion.modversion_id = modversions.id
                                INNER JOIN mods ON modversions.mod_id = mods.id
                                WHERE build_id = %s
                ) AS build2 ON build1.name1 = build2.name2 WHERE NOT build1.modverid <=> build2.modverid2

                UNION

                SELECT *
                FROM
                (
                SELECT modversions.version AS oldversion, mods.name AS name1, modversions.id AS modverid
                        FROM build_modversion
                                INNER JOIN modversions ON build_modversion.modversion_id = modversions.id
                                INNER JOIN mods ON modversions.mod_id = mods.id
                                WHERE build_id = %s
                ) AS build1
                RIGHT OUTER JOIN
                (
                SELECT modversions.version AS newversion, mods.name as name2, modversions.id AS modverid2
                                FROM build_modversion
                                INNER JOIN modversions ON build_modversion.modversion_id = modversions.id
                                INNER JOIN mods ON modversions.mod_id = mods.id
                                WHERE build_id = %s
                ) AS build2 ON build1.name1 = build2.name2 WHERE NOT build1.modverid <=> build2.modverid2
                ) AS build ORDER BY status, name
            """, (previd, id, previd, id,))
        rows = cur.fetchall()
        if rows:
            return rows
        flash("Failed to make changelog", "error")
        return []
