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
            errorPrinter.message("Error connecting to database", e)
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
                        minecraft VARCHAR(65535) NOT NULL DEFAULT(''),
                        forge VARCHAR(65535),
                        recommended VARCHAR(65535),
                        latest VARCHAR(65535),
                        `order` INT DEFAULT(0),
                        hidden BOOLEAN DEFAULT(1),
                        private BOOLEAN DEFAULT(0)
                        )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS builds (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        modpack_id INT NOT NULL,
                        version VARCHAR(65535) NOT NULL,
                        is_published BOOLEAN DEFAULT(0),
                        private BOOLEAN DEFAULT(0),
                        min_java VARCHAR(65535),
                        min_memory INT
                        )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS mods (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        name VARCHAR(255) NOT NULL UNIQUE,
                        pretty_name VARCHAR(65535) DEFAULT(''),
                        description VARCHAR(65535),
                        author VARCHAR(65535),
                        link VARCHAR(65535),
                        side enum('CLIENT', 'SERVER', 'BOTH'),
                        note VARCHAR(65535)
                        )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS modversions (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        mod_id INT NOT NULL,
                        version VARCHAR(65535) NOT NULL,
                        md5 VARCHAR(65535) NOT NULL,
                        filesize INT
                        )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS build_modversion (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        modversion_id INT NOT NULL,
                        buildversion_id INT NOT NULL
                        )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS users (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        username VARCHAR(65535) NOT NULL,
                        email VARCHAR(65535) NOT NULL,
                        password VARCHAR(65535) NOT NULL,
                        created_ip VARCHAR(65535) NOT NULL,
                        last_ip VARCHAR(65535),
                        remember_token VARCHAR(65535) DEFAULT(''),
                        updated_by_ip VARCHAR(65535),
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
                        modpacks_create BOOLEAN DEFAULT(0),
                        modpacks_manage BOOLEAN DEFAULT(0),
                        modpacks_delete BOOLEAN DEFAULT(0),
                        modpacks VARCHAR(65535)
                        )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS clients (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        name VARCHAR(255) NOT NULL UNIQUE,
                        uuid VARCHAR(255) NOT NULL UNIQUE
                        )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS client_modpack (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        client_id INT NOT NULL,
                        modpack_id INT NOT NULL
                        )"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS `keys` (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        name VARCHAR(255) NOT NULL UNIQUE,
                        api_key VARCHAR(255) NOT NULL UNIQUE
                        )"""
            )
            con.commit()
            con.close()
        except Exception:
            errorPrinter.message("Error creating tables", e)