import datetime
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
    def new(cls, username, email, hash1, ip, creator_id):
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
        return cls(id, username, email, password, ip, ip, now, now, ip, creator_id, creator_id)
    
    @classmethod
    def change(cls, userid, hash1, ip, creator_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        now = datetime.datetime.now()
        cur.execute("SELECT username FROM users WHERE id = %s", (userid,))
        username = cur.fetchone()["username"]
        password = Passhasher.hasher(hash1, username)
        cur.execute("UPDATE users SET password = %s, updated_by_ip = %s, updated_by_user_id = %s, updated_at = %s WHERE id = %s", (password, ip, creator_id, now, userid))
        conn.commit()
        return None

    @classmethod
    def delete(cls, id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("DELETE FROM users WHERE id=%s", (id,))
        conn.commit()
        return None

    @classmethod
    def get_by_id(cls, id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM users WHERE id = %s", (id,))
        row = cur.fetchone()
        if row:
            return cls(row["id"], row["username"], row["email"], row["password"], row["created_ip"], row["last_ip"], row["created_at"], row["updated_at"], row["updated_by_ip"], row["created_by_user_id"], row["updated_by_user_id"])
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
    def get_all_users() -> list:
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM users")
        return [User(row["id"], row["username"], row["email"], row["password"], row["created_ip"], row["last_ip"], row["created_at"], row["updated_at"], row["updated_by_ip"], row["created_by_user_id"], row["updated_by_user_id"]) for row in cur.fetchall()]

    @staticmethod
    def any_user_exists() -> bool:
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM users")
        return cur.fetchone() is not None

    def get_allowed_packs(self):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT id FROM modpacks JOIN user_permissions ON modpacks.id = user_permissions.modpacks WHERE user_permissions.user_id = %s", (self.id,))
        return Modpack.from_ids(cur.fetchall())

    def verify_password(self, password):
        return self.password.verify(password)