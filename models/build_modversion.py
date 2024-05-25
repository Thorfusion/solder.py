import datetime
from .database import Database
from .mod import Mod
from .modversion import Modversion

class Build_modversion:
    def __init__(self, id, modversion_id, build_id, created_at, updated_at, optional):
        self.id = id
        self.modversion_id = modversion_id
        self.build_id = build_id
        self.created_at = created_at
        self.updated_at = updated_at
        self.optional = optional
    
    @classmethod
    def delete_build_modversion(cls, id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("DELETE FROM build_modversion WHERE id = %s", (id,))
        conn.commit()
        return None
    
    @classmethod
    def update_optional(cls, modversion_id, optional, build_id):
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
            """
        , (id,))
        rows = cur.fetchall()
        if rows:
            return rows
        return []