from flask import flash
from .database import Database


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
        flash("Unable to get modpack build", "error")
        return []

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
