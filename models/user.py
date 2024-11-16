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
        now = datetime.datetime.now()
        password = Passhasher.hasher(hash1, username)
        add_user = ("INSERT INTO users (username, email, password, created_ip, last_ip, created_at, updated_at, updated_by_ip, created_by_user_id, updated_by_user_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)")
        data_user = (username, email, password, ip, ip, now, now, ip, creator_id, creator_id)
        cur.execute(add_user, data_user)
        conn.commit()
        cur.execute("SELECT LAST_INSERT_ID() AS id")
        id = cur.fetchone()["id"]
        if new_user is True or setup == True and DB_IS_UP == 0:
            cur.execute("INSERT INTO user_permissions (user_id, solder_full, solder_users, solder_keys, solder_clients, solder_env, mods_create, mods_manage, mods_delete, modpacks_create, modpacks_manage, modpacks_delete) VALUES (%s, 1, 1, 1, 1, 1, 1, 1, 1 ,1 ,1 ,1)", (id,))
        else:
            cur.execute("INSERT INTO user_permissions (user_id) VALUES (%s)", (id,))
        conn.commit()
        return cls(id, username, email, password, ip, ip, now, now, ip, creator_id, creator_id)

    @staticmethod
    def change(userid, hash1, ip, creator_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        now = datetime.datetime.now()
        cur.execute("SELECT username FROM users WHERE id = %s", (userid,))
        username = cur.fetchone()["username"]
        password = Passhasher.hasher(hash1, username)
        cur.execute("UPDATE users SET password = %s, updated_by_ip = %s, updated_by_user_id = %s, updated_at = %s WHERE id = %s", (password, ip, creator_id, now, userid))
        conn.commit()
        return None

    @staticmethod
    def delete(id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("DELETE FROM users WHERE id=%s", (id,))
        cur.execute("DELETE FROM user_permissions WHERE user_id=%s", (id,))
        conn.commit()
        return None

    @classmethod
    def get_by_username(cls, username):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM users WHERE username = %s", (username,))
        row = cur.fetchone()
        if row:
            return cls(row["id"], row["username"], row["email"], row["password"], row["created_ip"], row["last_ip"], row["created_at"], row["updated_at"], row["updated_by_ip"], row["created_by_user_id"], row["updated_by_user_id"])
        return None
    
    @staticmethod
    def get_userid(username):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id FROM users WHERE username = %s", (username,))
        try: 
            user_id = cur.fetchone()["id"]
            return (user_id)
        except:
            flash("failed to fetch user_id from users", "error")
            return None
        
    @staticmethod
    def get_permission_token(token: str, db_column):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT user_id FROM sessions WHERE token = %s", (token,))
        try: 
            user_id = cur.fetchone()["user_id"]
            conn.commit()
        except:
            flash("unable to fetch user_id for permission check", "error")
            return 0
        cur.execute("SELECT * FROM user_permissions WHERE user_id = %s", (user_id,))
        try: 
            row = cur.fetchone()
            allowed = row[db_column]
            if row["solder_full"] == 1:
                allowed = 1
            conn.commit()
            if allowed == 0:
                flash("Permission Denied", "error")
            return (allowed)
        except:
            flash("unable to check your permission", "error")
            return 0
        
    @staticmethod
    def get_fulluser(token: str):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT user_id FROM sessions WHERE token = %s", (token,))
        try: 
            user_id = cur.fetchone()["user_id"]
            conn.commit()
        except:
            flash("unable to fetch user_id for permission check", "error")
            return 0
        cur.execute("SELECT * FROM user_permissions WHERE user_id = %s", (user_id,))
        try: 
            row = cur.fetchone()
            if row["solder_full"] == 1:
                return 0
            if row["solder_user"] == 1:
                return 0
            return user_id
        except:
            return user_id

    @staticmethod
    def get_all_users() -> list:
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM users")
        return [User(row["id"], row["username"], row["email"], row["password"], row["created_ip"], row["last_ip"], row["created_at"], row["updated_at"], row["updated_by_ip"], row["created_by_user_id"], row["updated_by_user_id"]) for row in cur.fetchall()]

    @staticmethod
    def any_user_exists() -> bool:
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT * FROM users")
            check = cur.fetchone()
        except:
            flash("failed to check for existing users", "error")
            return True
        if check == None:
            return False
        return True

    def verify_password(self, password):
        return self.password.verify(password)
