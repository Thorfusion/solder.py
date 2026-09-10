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

tables = ("modpacks", "builds", "mods", "modversions", "build_modversions", "users", "user_permissions", "clients", "client_modpacks", "keys")


class Database:
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
        return 1

    @staticmethod
    def create_tables() -> bool:
        try:
            con = Database.get_connection()
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
                        is_published TINYINT(1) DEFAULT(0),
                        private TINYINT(1) DEFAULT(0),
                        min_java VARCHAR(255),
                        min_memory INT,
                        marked TINYINT(1) NOT NULL DEFAULT(0)
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
                        side enum('CLIENT', 'SERVER', 'BOTH') DEFAULT 'BOTH',
                        modtype enum('MOD', 'LAUNCHER', 'RES', 'CONFIG', 'MCIL', 'NONE') DEFAULT 'MOD',
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        note TEXT
                        )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS modversions (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        mod_id INT NOT NULL,
                        version VARCHAR(255) NOT NULL,
                        mcversion VARCHAR(255),
                        md5 VARCHAR(255) NOT NULL,
                        jarmd5 VARCHAR(255),
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        filesize INT,
                        INDEX idx_modversions_mod_mcversion (mod_id, mcversion)
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
                        INDEX idx_build_modversion_build_version (build_id, modversion_id)
                        )"""
            )
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
                        modpacks VARCHAR(255)
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
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                        )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS user_modpack (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        user_id INT NOT NULL,
                        modpack_id INT NOT NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                        )"""
            )
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
            con.close()
        except Exception as e:
            ErrorPrinter.message("Error creating tables", e)
            flash("Error creating tables", "error")

    @staticmethod
    def migratetechnic_tables() -> bool:
        timestamp_migrations = (
            """ALTER TABLE modpacks
               MODIFY created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
               MODIFY updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP""",
            """ALTER TABLE mods
               MODIFY created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
               MODIFY updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP""",
            """ALTER TABLE modversions
               MODIFY created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
               MODIFY updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP""",
            """ALTER TABLE build_modversion
               MODIFY created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
               MODIFY updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP""",
            """ALTER TABLE builds
               MODIFY created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
               MODIFY updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP""",
            """ALTER TABLE client_modpack
               MODIFY created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
               MODIFY updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP""",
            """ALTER TABLE clients
               MODIFY created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
               MODIFY updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP""",
            """ALTER TABLE `keys`
               MODIFY created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
               MODIFY updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP""",
            """ALTER TABLE user_permissions
               MODIFY created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
               MODIFY updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP""",
            """ALTER TABLE users
               MODIFY created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
               MODIFY updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP""",
        )
        column_migrations = (
            (
                "modpacks",
                "user_id",
                "ALTER TABLE modpacks ADD COLUMN user_id INT NOT NULL DEFAULT 1 AFTER slug",
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
                "mods",
                "note",
                "ALTER TABLE mods ADD COLUMN note VARCHAR(255) DEFAULT ''",
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
        )
        index_migrations = (
            (
                "build_modversion",
                "idx_build_modversion_build_version",
                "ALTER TABLE build_modversion "
                "ADD INDEX idx_build_modversion_build_version (build_id, modversion_id)",
            ),
            (
                "modversions",
                "idx_modversions_mod_mcversion",
                "ALTER TABLE modversions "
                "ADD INDEX idx_modversions_mod_mcversion (mod_id, mcversion)",
            ),
        )

        con = Database.get_connection()
        if con is None:
            return False

        try:
            cur = con.cursor()
            for query in timestamp_migrations:
                cur.execute(query)

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

            for table, index, query in index_migrations:
                cur.execute(
                    """SELECT 1
                       FROM information_schema.STATISTICS
                       WHERE TABLE_SCHEMA = %s
                         AND TABLE_NAME = %s
                         AND INDEX_NAME = %s
                       LIMIT 1""",
                    (db_name, table, index),
                )
                if cur.fetchone() is None:
                    cur.execute(query)

            cur.execute(
                """CREATE TABLE IF NOT EXISTS sessions (
                    token VARCHAR(80) NOT NULL PRIMARY KEY,
                    ip VARCHAR(255) NOT NULL,
                    expiry TIMESTAMP NOT NULL,
                    user_id INT NOT NULL
                )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS user_modpack (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        user_id INT NOT NULL,
                        modpack_id INT NOT NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                )"""
            )
            con.commit()
            print("technic database migrated!")
            return True
        except Exception as error:
            ErrorPrinter.message("Error migrating Technic Solder tables", error)
            if has_request_context():
                flash("Error migrating Technic Solder tables", "error")
            return False
        finally:
            con.close()

    @staticmethod
    def create_session_table() -> bool:
        try:
            con = Database.get_connection()
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
            con.close()
        except Exception:
            print.message("Error making session table", Exception)
            flash("Error making session table", "error")
