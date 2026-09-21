"""Database-backed generation used to invalidate API caches across workers."""

from .database import Database


class CacheRevision:
    NAME = "api_cache_revision"

    @classmethod
    def current(cls):
        conn = Database.get_connection()
        if conn is None:
            return 0
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                "SELECT value FROM solder_settings WHERE name = %s",
                (cls.NAME,),
            )
            row = cur.fetchone()
            try:
                return int(row["value"]) if row else 0
            except (TypeError, ValueError):
                return 0
        finally:
            cur.close()
            conn.close()

    @classmethod
    def bump(cls):
        conn = Database.get_connection()
        if conn is None:
            return
        cur = conn.cursor()
        try:
            cur.execute(
                """INSERT INTO solder_settings (name, value)
                   VALUES (%s, '1')
                   ON DUPLICATE KEY UPDATE
                       value = CAST(value AS UNSIGNED) + 1""",
                (cls.NAME,),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()
