import datetime
from .database import Database
from .modpack import Modpack

class Client_modpack:
    def __init__(self, id, client_id, modpack_id, created_at, updated_at):
        self.id = id
        self.client_id = client_id
        self.modpack_id = modpack_id
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
    
    @classmethod
    def delete_client_modpack(cls, id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("DELETE FROM client_modpack WHERE id=%s", (id,))
        conn.commit()
        return None

    @staticmethod
    def get_all_client_modpacks(id) -> list:
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM client_modpack WHERE client_id = %s", (id,))
        return [Client_modpack(**row) for row in cur.fetchall()]

