"""Per-mod bootstrap behavior stored outside Technic's core tables."""

from .database import Database


class ModBootstrapSettings:
    """Store exceptions to the default package enforcement behavior."""

    @staticmethod
    def apply(cur, mod_id, enforce):
        if enforce:
            cur.execute(
                "DELETE FROM mod_bootstrap_settings WHERE mod_id = %s",
                (mod_id,),
            )
            return
        cur.execute(
            """INSERT INTO mod_bootstrap_settings
                      (mod_id, enforce)
               VALUES (%s, 0)
               ON DUPLICATE KEY UPDATE
                   enforce = 0,
                   updated_at = CURRENT_TIMESTAMP""",
            (mod_id,),
        )

    @staticmethod
    def get(mod_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT enforce
                   FROM mod_bootstrap_settings
                   WHERE mod_id = %s""",
                (mod_id,),
            )
            row = cur.fetchone()
            return bool(
                row["enforce"] if row else True
            )
        finally:
            cur.close()
            conn.close()
