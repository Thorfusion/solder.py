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
        try:
            now = datetime.datetime.now()
            cur.execute("INSERT INTO user_modpack (user_id, modpack_id, created_at, updated_at) VALUES (%s, %s, %s, %s)", (user_id, modpack_id, now, now))
            cur.execute("SELECT LAST_INSERT_ID() AS id")
            id = cur.fetchone()["id"]
            conn.commit()
        finally:
            cur.close()
            conn.close()
        return cls(id, user_id, modpack_id, now, now)

    @staticmethod
    def delete_user_modpack(id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("DELETE FROM user_modpack WHERE id=%s", (id,))
            conn.commit()
        finally:
            cur.close()
            conn.close()
        return None

    @staticmethod
    def get_all_user_modpacks(id) -> list:
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT user_modpack.id, user_modpack.user_id, user_modpack.modpack_id, user_modpack.created_at, user_modpack.updated_at, users.username AS user_name, modpacks.name AS modpack_name
                    FROM user_modpack
                    INNER JOIN users ON user_modpack.user_id = users.id
                    INNER JOIN modpacks ON user_modpack.modpack_id = modpacks.id
                    WHERE user_modpack.user_id = %s
                """, (id,))
            return cur.fetchall() or []
        finally:
            cur.close()
            conn.close()
    
    @staticmethod
    def get_user_modpackpermission(token: str, modpack_id) -> list:
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT user_permissions.solder_full,
                          user_modpack.modpack_id
                   FROM sessions
                   INNER JOIN user_permissions
                       ON user_permissions.user_id = sessions.user_id
                   LEFT JOIN user_modpack
                       ON user_modpack.user_id = sessions.user_id
                      AND user_modpack.modpack_id = %s
                   WHERE sessions.token = %s AND sessions.expiry > NOW()""",
                (modpack_id, token),
            )
            row = cur.fetchone()
            allowed = bool(
                row
                and (
                    row["solder_full"] == 1
                    or row["modpack_id"] == modpack_id
                )
            )
            if not allowed:
                flash("Permission denied to this modpack", "error")
            return allowed
        finally:
            cur.close()
            conn.close()
    
    @staticmethod
    def get_user_permission(id) -> list:
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT * FROM user_permissions WHERE user_id = %s", (id,))
            return cur.fetchone() or []
        finally:
            cur.close()
            conn.close()
    
    @staticmethod
    def update_userpermissions(id, solder_full, solder_users, solder_keys, solder_clients, solder_env, mods_create, mods_manage, mods_delete, modpacks_create, modpacks_manage, modpacks_delete):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("""UPDATE user_permissions
                SET solder_full = %s, solder_users = %s, solder_keys = %s, solder_clients = %s, solder_env = %s, mods_create = %s, mods_manage = %s, mods_delete = %s, modpacks_create = %s, modpacks_manage = %s, modpacks_delete = %s
                WHERE user_id = %s;""", (solder_full, solder_users, solder_keys, solder_clients, solder_env, mods_create, mods_manage, mods_delete, modpacks_create, modpacks_manage, modpacks_delete, id))
            conn.commit()
        finally:
            cur.close()
            conn.close()
        return None
