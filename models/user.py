import datetime

from flask import flash
from models.common import DB_IS_UP, new_user

from .database import Database
from .modpack import Modpack
from .passhasher import Passhasher


class User:
    def __init__(self, id, username, email, hash, created_ip, last_ip, created_at, updated_at, updated_by_ip, created_by_user_id, updated_by_user_id):
        self.id = id
        self.username = username
        self.email = email
        self.password = Passhasher(hash, username)
        self.created_ip = created_ip
        self.last_ip = last_ip
        self.created_at = created_at
        self.updated_at = updated_at
        self.updated_by_ip = updated_by_ip
        self.created_by_user_id = created_by_user_id
        self.updated_by_user_id = updated_by_user_id

    @classmethod
    def new(cls, username, email, hash1, ip, creator_id, setup=False):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            now = datetime.datetime.now()
            password = Passhasher.hasher(hash1, username)
            add_user = ("INSERT INTO users (username, email, password, created_ip, last_ip, created_at, updated_at, updated_by_ip, created_by_user_id, updated_by_user_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)")
            data_user = (username, email, password, ip, ip, now, now, ip, creator_id, creator_id)
            cur.execute(add_user, data_user)
            cur.execute("SELECT LAST_INSERT_ID() AS id")
            id = cur.fetchone()["id"]
            if new_user is True or setup == True and DB_IS_UP == 0:
                cur.execute("INSERT INTO user_permissions (user_id, solder_full, solder_users, solder_keys, solder_clients, solder_env, mods_create, mods_manage, mods_delete, modpacks_create, modpacks_manage, modpacks_delete) VALUES (%s, 1, 1, 1, 1, 1, 1, 1, 1 ,1 ,1 ,1)", (id,))
            else:
                cur.execute("INSERT INTO user_permissions (user_id) VALUES (%s)", (id,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()
        return cls(id, username, email, password, ip, ip, now, now, ip, creator_id, creator_id)

    @staticmethod
    def change(userid, hash1, ip, creator_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            now = datetime.datetime.now()
            cur.execute("SELECT username FROM users WHERE id = %s", (userid,))
            username = cur.fetchone()["username"]
            password = Passhasher.hasher(hash1, username)
            cur.execute("UPDATE users SET password = %s, updated_by_ip = %s, updated_by_user_id = %s, updated_at = %s WHERE id = %s", (password, ip, creator_id, now, userid))
            conn.commit()
        finally:
            cur.close()
            conn.close()
        return None

    @staticmethod
    def delete(id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("DELETE FROM users WHERE id=%s", (id,))
            cur.execute("DELETE FROM user_permissions WHERE user_id=%s", (id,))
            cur.execute(
                """DELETE FROM personal_access_tokens
                   WHERE tokenable_id = %s AND tokenable_type = %s""",
                (id, r"App\Models\User"),
            )
            conn.commit()
        finally:
            cur.close()
            conn.close()
        return None

    @classmethod
    def get_by_username(cls, username):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT * FROM users WHERE username = %s", (username,))
            row = cur.fetchone()
            if row:
                return cls(row["id"], row["username"], row["email"], row["password"], row["created_ip"], row["last_ip"], row["created_at"], row["updated_at"], row["updated_by_ip"], row["created_by_user_id"], row["updated_by_user_id"])
            return None
        finally:
            cur.close()
            conn.close()
    
    @staticmethod
    def get_userid(username):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT id FROM users WHERE username = %s", (username,))
            row = cur.fetchone()
            if row is not None:
                return row["id"]
            flash("failed to fetch user_id from users", "error")
            return None
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def _permissions_for_token(token):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT user_permissions.*
                   FROM sessions
                   INNER JOIN user_permissions
                       ON user_permissions.user_id = sessions.user_id
                   WHERE sessions.token = %s AND sessions.expiry > NOW()""",
                (token,),
            )
            return cur.fetchone()
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def get_permission_token(token: str, db_column):
        row = User._permissions_for_token(token)
        if row is None or db_column not in row:
            flash("unable to check your permission", "error")
            return 0
        allowed = 1 if row["solder_full"] == 1 else row[db_column]
        if allowed == 0:
            flash("Permission Denied", "error")
        return allowed
        
    @staticmethod
    def get_fulluser(token: str):
        row = User._permissions_for_token(token)
        if row is None:
            flash("unable to fetch user_id for permission check", "error")
            return 0
        if row["solder_full"] == 1 or row["solder_users"] == 1:
            return 0
        return row["user_id"]

    @staticmethod
    def get_all_users() -> list:
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT * FROM users")
            return [User(row["id"], row["username"], row["email"], row["password"], row["created_ip"], row["last_ip"], row["created_at"], row["updated_at"], row["updated_by_ip"], row["created_by_user_id"], row["updated_by_user_id"]) for row in cur.fetchall()]
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def any_user_exists() -> bool:
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT * FROM users")
            check = cur.fetchone()
        except Exception:
            flash("failed to check for existing users", "error")
            return True
        finally:
            cur.close()
            conn.close()
        return check is not None

    def verify_password(self, password):
        return self.password.verify(password)
