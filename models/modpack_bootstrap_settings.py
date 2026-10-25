"""Per-modpack behavior for SolderPy Modpack Loader."""

from .database import Database


class ModpackBootstrapSettings:
    """Store opt-in behavior while keeping Technic's modpacks table intact."""

    @staticmethod
    def apply(
        cur,
        modpack_id,
        remove_unlisted_mod_files=False,
        always_hash_files=False,
    ):
        if not remove_unlisted_mod_files and not always_hash_files:
            cur.execute(
                "DELETE FROM modpack_bootstrap_settings WHERE modpack_id = %s",
                (modpack_id,),
            )
            return
        cur.execute(
            """INSERT INTO modpack_bootstrap_settings
                      (modpack_id, remove_unlisted_mod_files, always_hash_files)
               VALUES (%s, %s, %s)
               ON DUPLICATE KEY UPDATE
                   remove_unlisted_mod_files = VALUES(remove_unlisted_mod_files),
                   always_hash_files = VALUES(always_hash_files),
                   updated_at = CURRENT_TIMESTAMP""",
            (modpack_id, bool(remove_unlisted_mod_files), bool(always_hash_files)),
        )

    @classmethod
    def save(cls, modpack_id, remove_unlisted_mod_files):
        cls._save_option(
            modpack_id, "remove_unlisted_mod_files", remove_unlisted_mod_files
        )

    @classmethod
    def save_always_hash_files(cls, modpack_id, always_hash_files):
        cls._save_option(modpack_id, "always_hash_files", always_hash_files)

    @classmethod
    def _save_option(cls, modpack_id, column, enabled):
        if column == "remove_unlisted_mod_files":
            update_query = """INSERT INTO modpack_bootstrap_settings
                       (modpack_id, remove_unlisted_mod_files)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE
                    remove_unlisted_mod_files =
                        VALUES(remove_unlisted_mod_files),
                    updated_at = CURRENT_TIMESTAMP"""
        elif column == "always_hash_files":
            update_query = """INSERT INTO modpack_bootstrap_settings
                       (modpack_id, always_hash_files)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE
                    always_hash_files = VALUES(always_hash_files),
                    updated_at = CURRENT_TIMESTAMP"""
        else:
            raise ValueError("Unsupported modpack bootstrap setting")
        conn = Database.get_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                update_query,
                (modpack_id, bool(enabled)),
            )
            cur.execute(
                """DELETE FROM modpack_bootstrap_settings
                   WHERE modpack_id = %s
                     AND remove_unlisted_mod_files = 0
                     AND always_hash_files = 0""",
                (modpack_id,),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()
