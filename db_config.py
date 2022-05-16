from datetime import datetime
from os import getenv

from dotenv import load_dotenv
from mysql import connector

load_dotenv(".env")


def message(message, e=""):
    print("-" * 70 + "\n" + str(message) + "\n" + str(e) + "+\n" + "-" * 70)


def init_db() -> None:
    """
    Initializes the database tables.
    """
    conn = connect()
    cur = conn.cursor()

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
                url VARCHAR(65535),
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
                link VARCHAR(65535)
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
        """CREATE TABLE IF NOT EXISTS build_modversions (
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
    conn.commit()


def connect():
    db_host = getenv("DB_HOST")
    db_port = getenv("DB_PORT")
    db_user = getenv("DB_USER")
    db_pass = getenv("DB_PASSWORD")
    db_name = getenv("DB_DATABASE")
    try:
        conn: connector.MySQLConnection = connector.connect(
            host=db_host,
            port=db_port,
            user=db_user,
            password=db_pass,
            database=db_name,
        )
    except Exception as e:
        print("-" * 70 + "\n Error connecting to database \n" + "-" * 70 + "\n" + str(e))
    return conn

def select_all_modpacks():
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM modpacks ORDER BY id ASC")
    conn.close()
    return cur.fetchall()


def select_all_mods():
    conn = connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM mods ORDER BY id ASC")
    mods = []
    for (
        id,
        name,
        description,
        author,
        link,
        created_at,
        created_at,
        pretty_name,
    ) in cur:
        mods.append(
            {
                "id": id,
                "name": name,
                "desc": description,
                "author": author,
                "link": link,
                "pretty_name": pretty_name,
            }
        )
    conn.close()
    return mods


def select_mod(id):
    conn = connect()
    cur = conn.cursor()
    sql = "SELECT * FROM mods WHERE id=?"
    try:
        cur.execute(sql, (id,))
        return cur.fetchall()[0][1]
    except Exception as e:
        message("Error whilst fetching mod info", e)


def add_modversion_db(mod_id, version, hash, filesize):
    conn = connect()
    cur = conn.cursor()
    sql = "INSERT INTO modversions(mod_id,version, md5,created_at,updated_at, filesize ) VALUES(?,?,?,?,?,?)"
    try:
        cur.execute(
            sql, (mod_id, version, hash, datetime.now(), datetime.now(), filesize)
        )
        conn.commit()
    except Exception as e:
        message("Error whilst adding a mod version to the database", e)
    conn.close()

def get_api_key(key: str) -> list[str]:
        conn = connect()
        cur = conn.cursor(dictionary=True)
        sql = "SELECT * FROM `keys` WHERE api_key = %s"
        try:
                cur.execute(sql, (key,))
                return cur.fetchone()
        except Exception as e:
                message("An error occurred whilst trying to fetch an API key", e)

def get_user_info(user_or_email: str) -> dict:
    conn = connect()
    cur = conn.cursor(dictionary=True)
    sql = "SELECT * FROM users WHERE username = %s OR email = %s"
    try:
        cur.execute(sql, (user_or_email, user_or_email))
        return cur.fetchone()
    except Exception as e:
        message("An error occurred whilst trying to fetch a user", e)
