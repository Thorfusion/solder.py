"""Per-mod bootstrap behavior stored outside Technic's core tables."""

from .database import Database


class ModBootstrapSettings:
    """Store exceptions to the default complete package replacement behavior."""

    @staticmethod
    def apply(cur, mod_id, replace_on_launch_and_update):
        if replace_on_launch_and_update:
            cur.execute(
                "DELETE FROM mod_bootstrap_settings WHERE mod_id = %s",
                (mod_id,),
            )
            return
        cur.execute(
            """INSERT INTO mod_bootstrap_settings
                      (mod_id, replace_on_launch_and_update)
               VALUES (%s, 0)
               ON DUPLICATE KEY UPDATE
                   replace_on_launch_and_update = 0,
                   updated_at = CURRENT_TIMESTAMP""",
            (mod_id,),
        )

    @staticmethod
    def get(mod_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT replace_on_launch_and_update
                   FROM mod_bootstrap_settings
                   WHERE mod_id = %s""",
                (mod_id,),
            )
            row = cur.fetchone()
            return bool(
                row["replace_on_launch_and_update"] if row else True
            )
        finally:
            cur.close()
            conn.close()
