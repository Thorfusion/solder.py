import datetime

from .database import Database


class Client_modpack:
    def __init__(self, id, client_id, modpack_id, created_at, updated_at, client_name, modpack_name):
        self.id = id
        self.client_id = client_id
        self.modpack_id = modpack_id
        self.created_at = created_at
        self.updated_at = updated_at
        self.client_name = client_name
        self.modpack_name = modpack_name

    @classmethod
    def new(cls, client_id, modpack_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        now = datetime.datetime.now()
        cur.execute("INSERT INTO client_modpack (client_id, modpack_id, created_at, updated_at) VALUES (%s, %s, %s, %s)", (client_id, modpack_id, now, now))
        conn.commit()
        cur.execute("SELECT LAST_INSERT_ID() AS id")
        id = cur.fetchone()["id"]
        return cls(id, client_id, modpack_id, now, now, "", "")

    @staticmethod
    def delete_client_modpack(id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("DELETE FROM client_modpack WHERE id=%s", (id,))
        conn.commit()
        return None

    @staticmethod
    def get_all_client_modpacks(id) -> list:
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """SELECT client_modpack.id, client_modpack.client_id, client_modpack.modpack_id, client_modpack.created_at, client_modpack.updated_at, client_modpack.id, clients.name AS client_name, modpacks.name AS modpack_name
                FROM client_modpack
                INNER JOIN clients ON client_modpack.client_id = clients.id
                INNER JOIN modpacks ON client_modpack.modpack_id = modpacks.id
                WHERE client_id = %s
            """, (id,))
        return [Client_modpack(**row) for row in cur.fetchall()]
