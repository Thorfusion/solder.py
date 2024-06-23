import datetime

from .database import Database
from .errorPrinter import ErrorPrinter


class Key:
    def __init__(self, id, name, key, created_at, updated_at):
        self.id = id
        self.name = name
        self.key = key
        self.created_at = created_at
        self.updated_at = updated_at

    @classmethod
    def insert_new(cls, name, key):
        key_exists = cls.get_key(key)
        if key_exists:
            return key_exists
        new_key = cls(None, name, key, datetime.now(), datetime.now())
        cls.add_key(new_key)
        return new_key

    @classmethod
    def get_key_by_id(cls, id: int):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        sql = "SELECT * FROM `keys` WHERE id = %s"
        try:
            cur.execute(sql, (id,))
            key = cur.fetchone()
            conn.close()
            return cls(key['id'], key['name'], key['api_key'], key['created_at'], key['updated_at'])
        except Exception as e:
            ErrorPrinter.message("An error occurred whilst trying to fetch an API key", e)
        conn.close()

    @classmethod
    def get_key(cls, key: str):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        sql = "SELECT * FROM `keys` WHERE api_key = %s"
        try:
            cur.execute(sql, (key,))
            key = cur.fetchone()
            conn.close()
            return cls(key['id'], key['name'], key['api_key'], key['created_at'], key['updated_at'])
        except Exception as e:
            ErrorPrinter.message("An error occurred whilst trying to fetch an API key", e)
        conn.close()

    @staticmethod
    def verify_key(key: str) -> bool:
        conn = Database.get_connection()
        cur = conn.cursor()
        sql = "SELECT * FROM `keys` WHERE api_key = %s"
        try:
            cur.execute(sql, (key,))
            return True if cur.fetchone() is not None else False
        except Exception as e:
            ErrorPrinter.message("An error occurred whilst trying to fetch an API key", e)
        conn.close()

    @staticmethod
    def get_all_keys() -> list:
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        sql = "SELECT * FROM `keys`"
        try:
            cur.execute(sql)
            keydata = cur.fetchall()
            keys = [Key(row['id'], row['name'], row['api_key'], row['created_at'], row['updated_at']) for row in keydata]
            return keys
        except Exception as e:
            ErrorPrinter.message("An error occurred whilst trying to fetch all API keys", e)
        conn.close()

    def add_key() -> None:
        conn = Database.get_connection()
        cur = conn.cursor()
        sql = "INSERT INTO `keys` (id, name, api_key, created_at, updated_at) VALUES (%s, %s, %s, %s, %s)"
        try:
            cur.execute(sql, (self.id, self.name, self.key, self.created_at, self.updated_at))
        except Exception as e:
            ErrorPrinter.message("An error occurred whilst trying to fetch an API key", e)
        conn.close()

    @classmethod
    def delete_key(cls, id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("DELETE FROM `keys` WHERE id=%s", (id,))
        conn.commit()
        return None

    @classmethod
    def new_key(cls, name, key):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        now = datetime.datetime.now()
        add_key = ("INSERT INTO `keys` (name, api_key, created_at, updated_at) VALUES (%s, %s, %s, %s)")
        data_key = (name, key, now, now)
        cur.execute(add_key, data_key)
        conn.commit()
        cur.execute("SELECT LAST_INSERT_ID() AS id")
        id = cur.fetchone()["id"]
        return cls(id, name, key, now, now)
