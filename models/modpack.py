import datetime
from .database import Database

class Modpack:
    def __init__(self, id, name, slug, recommended, latest, url, created_at, updated_at, order, hidden, private):
        self.id = id
        self.name = name
        self.slug = slug
        self.recommended = recommended
        self.latest = latest
        self.url = url
        self.created_at = created_at
        self.updated_at = updated_at
        self.order = order
        self.hidden = hidden
        self.private = private

    @classmethod
    def new(cls, name, slug, recommended, latest, url, order, hidden, private):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        now = datetime.datetime.now()
        cur.execute("INSERT INTO modpacks (name, slug, recommended, latest, url, created_at, updated_at, order, hidden, private) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id", (name, slug, recommended, latest, url, now, now, order, hidden, private))
        id = cur.fetchone()["id"]
        return cls(id, name, slug, recommended, latest, url, now, now, order, hidden, private)

    @classmethod
    def get_by_id(cls, id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM modpacks WHERE id = %s", (id,))
        row = cur.fetchone()
        if row:
            return cls(row["id"], row["name"], row["slug"], row["recommended"], row["latest"], row["url"], row["created_at"], row["updated_at"], row["order"], row["hidden"], row["private"])
        return None