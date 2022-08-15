from ast import keyword
from database import Database
import errorPrinter
from datetime import datetime
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
        conn = Database.get_connection()
        cur = conn.cursor()
        sql = "INSERT INTO `keys` (name, api_key, created_at, updated_at) VALUES (%s, %s, %s, %s) RETURNING id"
        date = datetime.now()
        try:
            cur.execute(sql, (name, key, date, date))
            id = cur.fetchone()[0]
            conn.close()
            return cls(id, name, key, date, date)
        except Exception as e:
            errorPrinter.print_error("An error occurred trying to insert a new key", e)
            return None

    @staticmethod
    def verify_key(key: str) -> bool:
        conn = Database.get_connection()
        cur = conn.cursor()
        sql = "SELECT * FROM `keys` WHERE api_key = %s"
        try:
            cur.execute(sql, (key,))
            return True if cur.fetchone() is not None else False
        except Exception as e:
            errorPrinter.message("An error occurred whilst trying to fetch an API key", e)
        conn.close()

    @classmethod
    def get_key_by_id(cls, id: int) -> Key:
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        sql = "SELECT * FROM `keys` WHERE id = %s"
        try:
            cur.execute(sql, (id,))
            key = cur.fetchone()
            conn.close()
            return cls(key['id'], key['name'], key['api_key'], key['created_at'], key['updated_at'])
        except Exception as e:
            errorPrinter.message("An error occurred whilst trying to fetch an API key", e)
        conn.close()

    @classmethod
    def get_key(cls, key: str) -> Key:
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        sql = "SELECT * FROM `keys` WHERE api_key = %s"
        try:
            cur.execute(sql, (key,))
            key = cur.fetchone()
            conn.close()
            return cls(key['id'], key['name'], key['api_key'], key['created_at'], key['updated_at'])
        except Exception as e:
            errorPrinter.message("An error occurred whilst trying to fetch an API key", e)
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
            errorPrinter.message("An error occurred whilst trying to fetch all API keys", e)
        conn.close()

    @staticmethod
    def add_key(key: Key) -> None:
        conn = Database.get_connection()
        cur = conn.cursor()
        sql = "INSERT INTO `keys` (id, name, api_key, created_at, updated_at) VALUES (%s, %s, %s, %s, %s)"
        try:
            cur.execute(sql, (key.id, key.name, key.key, key.created_at, key.updated_at))
        except Exception as e:
            errorPrinter.message("An error occurred whilst trying to fetch an API key", e)
        conn.close()
