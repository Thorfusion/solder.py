"""Per-modpack behavior for SolderPy Modpack Loader."""

from .database import Database


class ModpackBootstrapSettings:
    """Store opt-in behavior while keeping Technic's modpacks table intact."""

    @staticmethod
    def apply(cur, modpack_id, remove_unlisted_mod_files):
        if not remove_unlisted_mod_files:
            cur.execute(
                "DELETE FROM modpack_bootstrap_settings WHERE modpack_id = %s",
                (modpack_id,),
            )
            return
        cur.execute(
            """INSERT INTO modpack_bootstrap_settings
                      (modpack_id, remove_unlisted_mod_files)
               VALUES (%s, 1)
               ON DUPLICATE KEY UPDATE
                   remove_unlisted_mod_files = 1,
                   updated_at = CURRENT_TIMESTAMP""",
            (modpack_id,),
        )

    @classmethod
    def save(cls, modpack_id, remove_unlisted_mod_files):
        conn = Database.get_connection()
        cur = conn.cursor()
        try:
            cls.apply(cur, modpack_id, remove_unlisted_mod_files)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()
