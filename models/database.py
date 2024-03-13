from os import getenv
from dotenv import load_dotenv
from mysql import connector

from .errorPrinter import ErrorPrinter

load_dotenv(".env")
db_host = getenv("DB_HOST")
db_port = getenv("DB_PORT")
db_user = getenv("DB_USER")
db_pass = getenv("DB_PASSWORD")
db_name = getenv("DB_DATABASE")

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
            print("Error connecting to database", e)
        return conn

    @staticmethod
    def is_setup() -> bool:
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        sql = "SELECT table_name FROM information_schema.tables"
        try:
            cur.execute(sql)
            curr_tables = [table["table_name"] for table in cur.fetchall()]
            for table in tables:
                if table not in curr_tables:
                    return False
                return True
        except Exception as e:
            errorPrinter.message("An error occurred whilst trying to check database setup", e)
        conn.close()

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
                        minecraft VARCHAR(255) NOT NULL DEFAULT(''),
                        forge VARCHAR(255),
                        recommended VARCHAR(255),
                        latest VARCHAR(255),
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        `order` INT DEFAULT(0),
                        hidden TINYINT(1) DEFAULT(1),
                        private TINYINT(1) DEFAULT(0)
                        pinned TINYINT(1) NOT NULL DEFAULT(0)
                        )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS builds (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        modpack_id INT NOT NULL,
                        version VARCHAR(255) NOT NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
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
                        description VARCHAR(255),
                        author VARCHAR(255),
                        link VARCHAR(255),
                        side enum('CLIENT', 'SERVER', 'BOTH'),
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
                        mcversion VARCHAR(255) NOT NULL,
                        md5 VARCHAR(255) NOT NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        filesize INT
                        )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS build_modversion (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        modversion_id INT NOT NULL,
                        build_id INT NOT NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        optional TINYINT(1) NOT NULL DEFAULT(0)
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
                    ip INT NOT NULL,
                    expiry TIMESTAMP NOT NULL
                )"""
            )
            con.commit()
            con.close()
        except Exception as e:
            ErrorPrinter.message("Error creating tables", e)

    @staticmethod
    def migratetechnic_tables() -> bool:
        try:
            con = Database.get_connection()
            cur = con.cursor()
            cur.execute(
                """ALTER TABLE modpacks
                    MODIFY created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    MODIFY updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP;
                """
            )
            cur.execute(
                """ALTER TABLE mods
                    MODIFY created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    MODIFY updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP;
                """
            )
            cur.execute(
                """ALTER TABLE modversions
                    MODIFY created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    MODIFY updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP;
                """
            )
            cur.execute(
                """ALTER TABLE build_modversion
                    MODIFY created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    MODIFY updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP;
                """
            )
            cur.execute(
                """ALTER TABLE builds
                    MODIFY created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    MODIFY updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP;
                """
            )
            cur.execute(
                """ALTER TABLE clients
                    MODIFY created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    MODIFY updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP;
                """
            )
            cur.execute(
                """ALTER TABLE `keys`
                    MODIFY created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    MODIFY updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP;
                """
            )
            cur.execute(
                """ALTER TABLE builds
                    MODIFY created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    MODIFY updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP;
                """
            )
            cur.execute(
                """ALTER TABLE user_permissions
                    MODIFY created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    MODIFY updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP;
                """
            )
            cur.execute(
                """ALTER TABLE users
                    MODIFY created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    MODIFY updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP;
                """
            )
            con.commit()
            cur.execute(
                """ALTER TABLE modpacks
                    ADD COLUMN user_id INT NOT NULL AFTER slug,
                    ADD COLUMN minecraft VARCHAR(255) NOT NULL DEFAULT(''),
                    ADD COLUMN forge VARCHAR(255),
                    ADD COLUMN pinned TINYINT(1) NOT NULL DEFAULT(0);
                """
            )
            cur.execute(
                """ALTER TABLE mods
                    ADD COLUMN side enum('CLIENT', 'SERVER', 'BOTH') AFTER link,
                    ADD COLUMN note VARCHAR(255),
                """
            )
            cur.execute(
                """ALTER TABLE builds
                    ADD COLUMN marked TINYINT(1) NOT NULL DEFAULT(0);
                """
            )
            cur.execute(
                """ALTER TABLE build_modversion
                    ADD COLUMN optional TINYINT(1) NOT NULL DEFAULT(0);
                """
            )
            cur.execute(
                """ALTER TABLE modversions
                    ADD COLUMN mcversion VARCHAR(255) NOT NULL DEFAULT(0) AFTER version;
                """
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS sessions (
                    token VARCHAR(80) NOT NULL PRIMARY KEY,
                    ip INT NOT NULL,
                    expiry TIMESTAMP NOT NULL
                )"""
            )
            con.commit()
            con.close()
        except Exception:
            print.message("Error migration technic tables", Exception)