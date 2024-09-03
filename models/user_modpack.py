import datetime

from flask import flash

from .database import Database


class User_modpack:
    def __init__(self, id, user_id, modpack_id, created_at, updated_at):
        self.id = id
        self.user_id = user_id
        self.modpack_id = modpack_id
        self.created_at = created_at
        self.updated_at = updated_at

    @classmethod
    def new(cls, user_id, modpack_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        now = datetime.datetime.now()
        cur.execute("INSERT INTO user_modpack (user_id, modpack_id, created_at, updated_at) VALUES (%s, %s, %s, %s)", (user_id, modpack_id, now, now))
        conn.commit()
        cur.execute("SELECT LAST_INSERT_ID() AS id")
        id = cur.fetchone()["id"]
        return cls(id, user_id, modpack_id, now, now)

    @staticmethod
    def delete_user_modpack(id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("DELETE FROM user_modpack WHERE id=%s", (id,))
        conn.commit()
        return None

    @staticmethod
    def get_all_user_modpacks(id) -> list:
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """SELECT user_modpack.id, user_modpack.user_id, user_modpack.modpack_id, user_modpack.created_at, user_modpack.updated_at, users.username AS user_name, modpacks.name AS modpack_name
                FROM user_modpack
                INNER JOIN users ON user_modpack.user_id = users.id
                INNER JOIN modpacks ON user_modpack.modpack_id = modpacks.id
                WHERE user_modpack.user_id = %s
            """, (id,))
        rows = cur.fetchall()
        if rows:
            return rows
        return []
    
    @staticmethod
    def get_user_modpackpermission(token: str, modpack_id) -> list:
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT user_id FROM sessions WHERE token = %s", (token,))
        try: 
            user_id = cur.fetchone()["user_id"]
            conn.commit()
        except:
            flash("unable to fetch user_id for permission check", "error")
            return False
        cur.execute("SELECT solder_full FROM user_permissions WHERE user_id = %s", (user_id,))
        try: 
            row = cur.fetchone()["solder_full"]
            conn.commit()
            if row == 1:
                return True
        except:
            flash("unable to check your admin permission", "error")
        cur.execute("SELECT modpack_id FROM user_modpack WHERE user_id = %s AND modpack_id = %s", (user_id, modpack_id))
        try: 
            rows = cur.fetchone()["modpack_id"]
            conn.commit()
            if rows == modpack_id:
                return True
        except:
            flash("Permission denied to this modpack", "error")
            return False
    
    @staticmethod
    def get_user_permission(id) -> list:
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM user_permissions WHERE user_id = %s", (id,))
        rows = cur.fetchone()
        if rows:
            return rows
        return []
    
    @staticmethod
    def update_userpermissions(id, solder_full, solder_users, solder_keys, solder_clients, solder_env, mods_create, mods_manage, mods_delete, modpacks_create, modpacks_manage, modpacks_delete):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        now = datetime.datetime.now()
        cur.execute("""UPDATE user_permissions 
            SET solder_full = %s, solder_users = %s, solder_keys = %s, solder_clients = %s, solder_env = %s, mods_create = %s, mods_manage = %s, mods_delete = %s, modpacks_create = %s, modpacks_manage = %s, modpacks_delete = %s
            WHERE user_id = %s;""", (solder_full, solder_users, solder_keys, solder_clients, solder_env, mods_create, mods_manage, mods_delete, modpacks_create, modpacks_manage, modpacks_delete, id))
        conn.commit()
        return None