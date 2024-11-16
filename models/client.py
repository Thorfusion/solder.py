import datetime

from .database import Database
from .modpack import Modpack


class Client:
    def __init__(self, id, name, uuid, created_at, updated_at):
        self.id = id
        self.name = name
        self.uuid = uuid
        self.created_at = created_at
        self.updated_at = updated_at

    @classmethod
    def new(cls, name, uuid):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        now = datetime.datetime.now()
        cur.execute("INSERT INTO clients (name, uuid, created_at, updated_at) VALUES (%s, %s, %s, %s)", (name, uuid, now, now))
        conn.commit()
        cur.execute("SELECT LAST_INSERT_ID() AS id")
        id = cur.fetchone()["id"]
        return cls(id, name, uuid, now, now)

    @staticmethod
    def delete_client(id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("DELETE FROM clients WHERE id=%s", (id,))
        cur.execute("DELETE FROM client_modpack WHERE client_id = %s", (id,))
        conn.commit()
        return None

    @staticmethod
    def get_all_clients() -> list:
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM clients")
        return [Client(**row) for row in cur.fetchall()]
