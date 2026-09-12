from os import getenv
import os

from dotenv import load_dotenv
from flask import flash, has_request_context
from mysql import connector

from .errorPrinter import ErrorPrinter

load_dotenv(".env")
db_host = getenv("DB_HOST")
db_port = getenv("DB_PORT")
db_user = getenv("DB_USER")
db_pass = getenv("DB_PASSWORD")
db_name = getenv("DB_DATABASE")

DISABLE_is_setup = False

if os.getenv("DISABLE_is_setup"):
    DISABLE_is_setup = os.getenv("DISABLE_is_setup").lower() in ["true", "t", "1", "yes", "y"]

CORE_TABLES = {
    "modpacks",
    "builds",
    "mods",
    "modversions",
    "build_modversion",
    "users",
    "user_permissions",
    "clients",
    "client_modpack",
    "keys",
}


class Database:
    PERSONAL_ACCESS_TOKENS_TABLE_SQL = """CREATE TABLE IF NOT EXISTS personal_access_tokens (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        tokenable_type VARCHAR(255) NOT NULL,
        tokenable_id BIGINT UNSIGNED NOT NULL,
        name TEXT NOT NULL,
        token VARCHAR(64) NOT NULL UNIQUE,
        abilities TEXT,
        last_used_at TIMESTAMP NULL,
        expires_at TIMESTAMP NULL,
        created_at TIMESTAMP NULL,
        updated_at TIMESTAMP NULL,
        INDEX personal_access_tokens_tokenable_type_tokenable_id_index
            (tokenable_type, tokenable_id),
        INDEX personal_access_tokens_expires_at_index (expires_at)
    )"""

    MOD_DEPENDENCIES_TABLE_SQL = """CREATE TABLE IF NOT EXISTS mod_dependencies (
        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        mod_id INT NOT NULL,
        dependency_mod_id INT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_mod_dependencies_pair (mod_id, dependency_mod_id),
        INDEX idx_mod_dependencies_dependency (dependency_mod_id)
    )"""

    USER_MODPACK_TABLE_SQL = """CREATE TABLE IF NOT EXISTS user_modpack (
        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        modpack_id INT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX idx_user_modpack_user_pack (user_id, modpack_id),
        INDEX idx_user_modpack_pack_user (modpack_id, user_id)
    )"""

    MODLOADER_COLUMN_MIGRATIONS = (
        (
            "builds",
            "modloader",
            "ALTER TABLE builds ADD COLUMN modloader VARCHAR(32) NULL AFTER forge",
        ),
        (
            "modversions",
            "modloader",
            "ALTER TABLE modversions ADD COLUMN modloader VARCHAR(32) NULL AFTER mcversion",
        ),
    )

    NOTES_COLUMN_MIGRATIONS = (
        (
            "mods",
            "notes",
            "ALTER TABLE mods ADD COLUMN notes TEXT NULL AFTER link",
        ),
    )

    INTEGRATION_COLUMN_MIGRATIONS = (
        (
            "mods",
            "integration_provider",
            "ALTER TABLE mods ADD COLUMN integration_provider VARCHAR(16) NULL AFTER notes",
        ),
        (
            "mods",
            "integration_project_id",
            "ALTER TABLE mods ADD COLUMN integration_project_id VARCHAR(64) NULL AFTER integration_provider",
        ),
        (
            "modversions",
            "integration_version_id",
            "ALTER TABLE modversions ADD COLUMN integration_version_id VARCHAR(64) NULL AFTER modloader",
        ),
    )

    API_INDEX_MIGRATIONS = (
        (
            "build_modversion",
            ("build_id", "modversion_id"),
            "ALTER TABLE build_modversion "
            "ADD INDEX idx_build_modversion_build_version (build_id, modversion_id)",
        ),
        (
            "build_modversion",
            ("modversion_id", "build_id"),
            "ALTER TABLE build_modversion "
            "ADD INDEX idx_build_modversion_version_build (modversion_id, build_id)",
        ),
        (
            "builds",
            ("modpack_id", "version", "is_published", "private"),
            "ALTER TABLE builds "
            "ADD INDEX idx_builds_modpack_version_access "
            "(modpack_id, version, is_published, private)",
        ),
        (
            "client_modpack",
            ("modpack_id", "client_id"),
            "ALTER TABLE client_modpack "
            "ADD INDEX idx_client_modpack_modpack_client (modpack_id, client_id)",
        ),
        (
            "client_modpack",
            ("client_id", "modpack_id"),
            "ALTER TABLE client_modpack "
            "ADD INDEX idx_client_modpack_client_modpack (client_id, modpack_id)",
        ),
        (
            "modversions",
            ("mod_id", "mcversion", "modloader"),
            "ALTER TABLE modversions "
            "ADD INDEX idx_modversions_mod_compatibility "
            "(mod_id, mcversion, modloader)",
        ),
        (
            "modversions",
            ("mod_id", "version"),
            "ALTER TABLE modversions "
            "ADD INDEX idx_modversions_mod_version (mod_id, version)",
        ),
        (
            "mods",
            ("integration_provider", "integration_project_id"),
            "ALTER TABLE mods ADD UNIQUE INDEX uq_mods_integration_project "
            "(integration_provider, integration_project_id)",
        ),
        (
            "modversions",
            ("mod_id", "integration_version_id"),
            "ALTER TABLE modversions ADD UNIQUE INDEX uq_modversions_integration_version "
            "(mod_id, integration_version_id)",
        ),
        (
            "user_permissions",
            ("user_id",),
            "ALTER TABLE user_permissions "
            "ADD INDEX idx_user_permissions_user (user_id)",
        ),
        (
            "user_modpack",
            ("user_id", "modpack_id"),
            "ALTER TABLE user_modpack "
            "ADD INDEX idx_user_modpack_user_pack (user_id, modpack_id)",
        ),
        (
            "user_modpack",
            ("modpack_id", "user_id"),
            "ALTER TABLE user_modpack "
            "ADD INDEX idx_user_modpack_pack_user (modpack_id, user_id)",
        ),
        (
            "clients",
            ("uuid",),
            "ALTER TABLE clients ADD INDEX idx_clients_uuid (uuid)",
        ),
        (
            "keys",
            ("api_key",),
            "ALTER TABLE `keys` ADD INDEX idx_keys_api_key (api_key)",
        ),
    )

    @staticmethod
    def index_covers_columns(cur, table: str, columns: tuple[str, ...]) -> bool:
        """Return whether an index starts with the requested columns."""
        cur.execute(
            """SELECT INDEX_NAME, COLUMN_NAME
               FROM information_schema.STATISTICS
               WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
               ORDER BY INDEX_NAME, SEQ_IN_INDEX""",
            (db_name, table),
        )
        indexes = {}
        for index, column in cur.fetchall():
            indexes.setdefault(index, []).append(column)
        return any(
            tuple(index_columns[:len(columns)]) == columns
            for index_columns in indexes.values()
        )

    @staticmethod
    def column_exists(cur, table: str, column: str) -> bool:
        cur.execute(
            """SELECT 1
               FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA = %s
                 AND TABLE_NAME = %s
                 AND COLUMN_NAME = %s""",
            (db_name, table, column),
        )
        return cur.fetchone() is not None

    @staticmethod
    def migrate_legacy_mod_notes(cur) -> None:
        """Move solder.py 1.7.4 mod notes to Technic's plural column."""
        if Database.column_exists(cur, "mods", "note"):
            cur.execute(
                """UPDATE mods
                   SET notes = note
                   WHERE notes IS NULL AND note IS NOT NULL"""
            )
            cur.execute("ALTER TABLE mods DROP COLUMN note")

    @staticmethod
    def migrate_jar_hash_mod_types(cur) -> None:
        """Promote mods with a stored raw-JAR hash to the MOD package type."""
        cur.execute(
            """UPDATE mods
               INNER JOIN modversions ON modversions.mod_id = mods.id
               SET mods.modtype = 'MOD'
               WHERE CHAR_LENGTH(TRIM(modversions.jarmd5)) = 32
                 AND TRIM(modversions.jarmd5) NOT REGEXP '[^0-9A-Fa-f]'
                 AND (mods.modtype IS NULL OR mods.modtype <> 'MOD')"""
        )

    @staticmethod
    def normalize_legacy_timestamps(cur) -> None:
        """Replace only obsolete zero-date defaults left by older databases.

        Current Technic Solder timestamps are nullable, so valid NULL values are
        deliberately preserved. Older zero-date definitions are restored to the
        automatic solder.py timestamp defaults before MySQL 8 adds indexes.
        """
        cur.execute(
            """SELECT TABLE_NAME, COLUMN_NAME, COLUMN_DEFAULT
               FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA = %s
                 AND COLUMN_NAME IN ('created_at', 'updated_at')""",
            (db_name,),
        )
        legacy_columns = {}
        for table, column, default in cur.fetchall():
            if table not in CORE_TABLES or not str(default).startswith("0000-00-00"):
                continue
            legacy_columns.setdefault(table, []).append(column)

        for table, columns in legacy_columns.items():
            definitions = ", ".join(
                f"MODIFY `{column}` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
                + (" ON UPDATE CURRENT_TIMESTAMP" if column == "updated_at" else "")
                for column in columns
            )
            cur.execute(f"ALTER TABLE `{table}` {definitions}")

    @staticmethod
    def get_connection() -> connector.connection:
        try:
            conn: connector.MySQLConnection = connector.connect(
                host=db_host,
                port=db_port,
                user=db_user,
                password=db_pass,
                database=db_name,
            )
        except Exception as e:
            ErrorPrinter.message("Error connecting to database", e)
            # Connections are also checked while the application is importing,
            # before Flask has established a request context.
            if has_request_context():
                flash("Error connecting to database", "error")
            return None
        return conn

    @staticmethod
    def is_setup() -> int:
        if DISABLE_is_setup == True:
            return 1
        conn = Database.get_connection()
        if conn is None:
            return 2
        try:
            cur = conn.cursor()
            cur.execute("SHOW TABLES")
            tables_found = cur.fetchall()
        except Exception as e:
            ErrorPrinter.message("An error occurred whilst trying to check database setup", e)
            if has_request_context():
                flash("An error occurred whilst trying to check database setup", "error")
            return 2
        finally:
            conn.close()
        if not tables_found:
            return 0
        if not CORE_TABLES.issubset({table[0] for table in tables_found}):
            return 0
        return 1

    @staticmethod
    def create_tables() -> bool:
        con = Database.get_connection()
        if con is None:
            return False
        cur = None
        try:
            cur = con.cursor()
            cur.execute(
                """CREATE TABLE IF NOT EXISTS modpacks (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        name VARCHAR(255) NOT NULL UNIQUE,
                        slug VARCHAR(255) NOT NULL UNIQUE,
                        user_id INT NOT NULL,
                        recommended VARCHAR(255),
                        latest VARCHAR(255),
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        `order` INT DEFAULT(0),
                        hidden TINYINT(1) DEFAULT(1),
                        private TINYINT(1) DEFAULT(0),
                        pinned TINYINT(1) NOT NULL DEFAULT(0),
                        enable_optionals BOOLEAN DEFAULT(0),
                        enable_server BOOLEAN DEFAULT(0)
                        )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS builds (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        modpack_id INT NOT NULL,
                        version VARCHAR(255) NOT NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        minecraft VARCHAR(255) NOT NULL DEFAULT(''),
                        forge VARCHAR(255),
                        modloader VARCHAR(32),
                        is_published TINYINT(1) DEFAULT(0),
                        private TINYINT(1) DEFAULT(0),
                        min_java VARCHAR(255),
                        min_memory INT,
                        marked TINYINT(1) NOT NULL DEFAULT(0),
                        INDEX idx_builds_modpack_version_access
                            (modpack_id, version, is_published, private)
                        )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS mods (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        name VARCHAR(255) NOT NULL UNIQUE,
                        pretty_name VARCHAR(255) DEFAULT(''),
                        description VARCHAR(255) DEFAULT(''),
                        author VARCHAR(255),
                        link VARCHAR(255),
                        notes TEXT,
                        integration_provider VARCHAR(16),
                        integration_project_id VARCHAR(64),
                        side enum('CLIENT', 'SERVER', 'BOTH') DEFAULT 'BOTH',
                        modtype enum('MOD', 'LAUNCHER', 'RES', 'CONFIG', 'MCIL', 'NONE') DEFAULT 'MOD',
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        UNIQUE INDEX uq_mods_integration_project
                            (integration_provider, integration_project_id)
                        )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS modversions (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        mod_id INT NOT NULL,
                        version VARCHAR(255) NOT NULL,
                        mcversion VARCHAR(255),
                        modloader VARCHAR(32),
                        integration_version_id VARCHAR(64),
                        md5 VARCHAR(255) NOT NULL,
                        jarmd5 VARCHAR(255),
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        filesize INT,
                        INDEX idx_modversions_mod_compatibility
                            (mod_id, mcversion, modloader),
                        INDEX idx_modversions_mod_version (mod_id, version),
                        UNIQUE INDEX uq_modversions_integration_version
                            (mod_id, integration_version_id)
                        )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS build_modversion (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        modversion_id INT NOT NULL,
                        build_id INT NOT NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        optional TINYINT(1) NOT NULL DEFAULT(0),
                        INDEX idx_build_modversion_build_version (build_id, modversion_id),
                        INDEX idx_build_modversion_version_build (modversion_id, build_id)
                        )"""
            )
            cur.execute(Database.MOD_DEPENDENCIES_TABLE_SQL)
            cur.execute(Database.PERSONAL_ACCESS_TOKENS_TABLE_SQL)
            cur.execute(
                """CREATE TABLE IF NOT EXISTS users (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        username VARCHAR(255) NOT NULL,
                        email VARCHAR(255) NOT NULL,
                        password VARCHAR(255) NOT NULL,
                        created_ip VARCHAR(255) NOT NULL,
                        last_ip VARCHAR(255),
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        remember_token VARCHAR(255) DEFAULT(''),
                        updated_by_ip VARCHAR(255),
                        created_by_user_id INT DEFAULT(1),
                        updated_by_user_id INT
                        )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS user_permissions (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        user_id INT NOT NULL,
                        solder_full BOOLEAN DEFAULT(0),
                        solder_users BOOLEAN DEFAULT(0),
                        solder_keys BOOLEAN DEFAULT(0),
                        solder_clients BOOLEAN DEFAULT(0),
                        solder_env BOOLEAN DEFAULT(0),
                        mods_create BOOLEAN DEFAULT(0),
                        mods_manage BOOLEAN DEFAULT(0),
                        mods_delete BOOLEAN DEFAULT(0),
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        modpacks_create BOOLEAN DEFAULT(0),
                        modpacks_manage BOOLEAN DEFAULT(0),
                        modpacks_delete BOOLEAN DEFAULT(0),
                        modpacks VARCHAR(255),
                        INDEX user_permissions_user_id_index (user_id)
                        )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS clients (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        name VARCHAR(255) NOT NULL UNIQUE,
                        uuid VARCHAR(255) NOT NULL UNIQUE,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                        )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS client_modpack (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        client_id INT NOT NULL,
                        modpack_id INT NOT NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        INDEX idx_client_modpack_modpack_client (modpack_id, client_id),
                        INDEX idx_client_modpack_client_modpack (client_id, modpack_id)
                        )"""
            )
            cur.execute(Database.USER_MODPACK_TABLE_SQL)
            cur.execute(
                """CREATE TABLE IF NOT EXISTS `keys` (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        name VARCHAR(255) NOT NULL UNIQUE,
                        api_key VARCHAR(255) NOT NULL UNIQUE,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                        )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS sessions (
                    token VARCHAR(80) NOT NULL PRIMARY KEY,
                    ip VARCHAR(255) NOT NULL,
                    expiry TIMESTAMP NOT NULL,
                    user_id INT NOT NULL
                )"""
            )
            con.commit()
            return True
        except Exception as error:
            ErrorPrinter.message("Error creating tables", error)
            if has_request_context():
                flash("Error creating tables", "error")
            return False
        finally:
            if cur is not None:
                cur.close()
            con.close()

    @staticmethod
    def migratetechnic_tables() -> bool:
        column_migrations = (
            (
                "modpacks",
                "user_id",
                "ALTER TABLE modpacks ADD COLUMN user_id INT NULL AFTER slug",
            ),
            (
                "modpacks",
                "pinned",
                "ALTER TABLE modpacks ADD COLUMN pinned TINYINT(1) NOT NULL DEFAULT 0",
            ),
            (
                "modpacks",
                "enable_optionals",
                "ALTER TABLE modpacks ADD COLUMN enable_optionals BOOLEAN DEFAULT 0",
            ),
            (
                "modpacks",
                "enable_server",
                "ALTER TABLE modpacks ADD COLUMN enable_server BOOLEAN DEFAULT 0",
            ),
            (
                "mods",
                "side",
                "ALTER TABLE mods ADD COLUMN side ENUM('CLIENT', 'SERVER', 'BOTH') DEFAULT 'BOTH'",
            ),
            (
                "mods",
                "modtype",
                "ALTER TABLE mods ADD COLUMN modtype ENUM('MOD', 'LAUNCHER', 'RES', 'CONFIG', 'MCIL', 'NONE') DEFAULT 'MOD'",
            ),
            (
                "builds",
                "minecraft",
                "ALTER TABLE builds ADD COLUMN minecraft VARCHAR(255) NOT NULL DEFAULT ''",
            ),
            (
                "builds",
                "forge",
                "ALTER TABLE builds ADD COLUMN forge VARCHAR(255)",
            ),
            (
                "builds",
                "marked",
                "ALTER TABLE builds ADD COLUMN marked TINYINT(1) NOT NULL DEFAULT 0",
            ),
            (
                "build_modversion",
                "optional",
                "ALTER TABLE build_modversion ADD COLUMN optional TINYINT(1) NOT NULL DEFAULT 0",
            ),
            (
                "modversions",
                "jarmd5",
                "ALTER TABLE modversions ADD COLUMN jarmd5 VARCHAR(255) AFTER md5",
            ),
            (
                "modversions",
                "mcversion",
                "ALTER TABLE modversions ADD COLUMN mcversion VARCHAR(255) AFTER version",
            ),
            (
                "user_permissions",
                "solder_env",
                "ALTER TABLE user_permissions ADD COLUMN solder_env BOOLEAN DEFAULT 0",
            ),
            *Database.MODLOADER_COLUMN_MIGRATIONS,
            *Database.NOTES_COLUMN_MIGRATIONS,
            *Database.INTEGRATION_COLUMN_MIGRATIONS,
        )
        con = Database.get_connection()
        if con is None:
            return False

        cur = None
        try:
            cur = con.cursor()
            Database.normalize_legacy_timestamps(cur)
            for table, column, query in column_migrations:
                cur.execute(
                    """SELECT 1
                       FROM information_schema.COLUMNS
                       WHERE TABLE_SCHEMA = %s
                         AND TABLE_NAME = %s
                         AND COLUMN_NAME = %s""",
                    (db_name, table, column),
                )
                if cur.fetchone() is None:
                    cur.execute(query)

            Database.migrate_legacy_mod_notes(cur)
            Database.migrate_jar_hash_mod_types(cur)

            cur.execute("UPDATE modpacks SET user_id = 1 WHERE user_id IS NULL")
            cur.execute("ALTER TABLE modpacks MODIFY user_id INT NOT NULL")
            cur.execute(
                "UPDATE builds SET modloader = 'FORGE' "
                "WHERE modloader IS NULL AND forge IS NOT NULL"
            )

            for table, columns, query in Database.API_INDEX_MIGRATIONS:
                if not Database.index_covers_columns(cur, table, columns):
                    cur.execute(query)

            cur.execute(Database.MOD_DEPENDENCIES_TABLE_SQL)
            cur.execute(Database.PERSONAL_ACCESS_TOKENS_TABLE_SQL)

            cur.execute(
                """CREATE TABLE IF NOT EXISTS sessions (
                    token VARCHAR(80) NOT NULL PRIMARY KEY,
                    ip VARCHAR(255) NOT NULL,
                    expiry TIMESTAMP NOT NULL,
                    user_id INT NOT NULL
                )"""
            )
            cur.execute(Database.USER_MODPACK_TABLE_SQL)
            con.commit()
            print("technic database migrated!")
            return True
        except Exception as error:
            ErrorPrinter.message("Error migrating Technic Solder tables", error)
            if has_request_context():
                flash("Error migrating Technic Solder tables", "error")
            return False
        finally:
            if cur is not None:
                cur.close()
            con.close()

    @staticmethod
    def ensure_runtime_schema() -> bool:
        """Create additive tables needed when an existing installation upgrades."""
        if DISABLE_is_setup:
            return True

        con = Database.get_connection()
        if con is None:
            return False

        cur = None
        try:
            cur = con.cursor()
            Database.normalize_legacy_timestamps(cur)
            cur.execute(Database.MOD_DEPENDENCIES_TABLE_SQL)
            cur.execute(Database.PERSONAL_ACCESS_TOKENS_TABLE_SQL)
            cur.execute(Database.USER_MODPACK_TABLE_SQL)

            for table, column, query in (
                *Database.MODLOADER_COLUMN_MIGRATIONS,
                *Database.NOTES_COLUMN_MIGRATIONS,
                *Database.INTEGRATION_COLUMN_MIGRATIONS,
            ):
                cur.execute(
                    """SELECT 1
                       FROM information_schema.COLUMNS
                       WHERE TABLE_SCHEMA = %s
                         AND TABLE_NAME = %s
                         AND COLUMN_NAME = %s""",
                    (db_name, table, column),
                )
                if cur.fetchone() is None:
                    cur.execute(query)

            Database.migrate_legacy_mod_notes(cur)
            Database.migrate_jar_hash_mod_types(cur)

            cur.execute(
                "UPDATE builds SET modloader = 'FORGE' "
                "WHERE modloader IS NULL AND forge IS NOT NULL"
            )

            for table, columns, query in Database.API_INDEX_MIGRATIONS:
                cur.execute(
                    """SELECT 1
                       FROM information_schema.TABLES
                       WHERE TABLE_SCHEMA = %s
                         AND TABLE_NAME = %s
                       LIMIT 1""",
                    (db_name, table),
                )
                if cur.fetchone() is None:
                    continue

                if not Database.index_covers_columns(cur, table, columns):
                    cur.execute(query)
            con.commit()
            return True
        except Exception as error:
            ErrorPrinter.message("Error updating solder.py database schema", error)
            return False
        finally:
            if cur is not None:
                cur.close()
            con.close()

    @staticmethod
    def create_session_table() -> bool:
        con = Database.get_connection()
        if con is None:
            return False
        cur = None
        try:
            cur = con.cursor()
            cur.execute(
                """CREATE TABLE IF NOT EXISTS sessions (
                    token VARCHAR(80) NOT NULL PRIMARY KEY,
                    ip VARCHAR(255) NOT NULL,
                    expiry TIMESTAMP NOT NULL,
                    user_id INT NOT NULL
                )"""
            )
            con.commit()
            return True
        except Exception as error:
            ErrorPrinter.message("Error making session table", error)
            if has_request_context():
                flash("Error making session table", "error")
            return False
        finally:
            if cur is not None:
                cur.close()
            con.close()
