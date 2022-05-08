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
                name TEXT NOT NULL UNIQUE,
                slug TEXT NOT NULL UNIQUE,
                user_id INT NOT NULL,
                minecraft TEXT NOT NULL DEFAULT '',
                forge TEXT,
                recommended TEXT,
                latest TEXT
                url TEXT,
                icon BOOLEAN DEFAULT 0,
                logo BOOLEAN DEFAULT 0,
                background BOOLEAN DEFAULT 0,
                icon_url TEXT,
                logo_url TEXT,
                background_url TEXT,
                icon_md5 TEXT,
                logo_md5 TEXT,
                background_md5 TEXT,
                order INT DEFAULT 0,
                hidden BOOLEAN DEFAULT 1,
                private BOOLEAN DEFAULT 0
                )"""
    )
    cur.execute(
        """CREATE TABLE IF NOT EXISTS builds (
                id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                modpack_id INT NOT NULL,
                version TEXT NOT NULL,
                is_published BOOLEAN DEFAULT 0,
                private BOOLEAN DEFAULT 0,
                min_java TEXT,
                min_memory INT
                )"""
    )
    cur.execute(
        """CREATE TABLE IF NOT EXISTS mods (
                id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                pretty_name TEXT DEFAULT '',
                description TEXT,
                author TEXT,
                link TEXT
                )"""
    )
    cur.execute(
        """CREATE TABLE IF NOT EXISTS modversions (
                id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                mod_id INT NOT NULL,
                version TEXT NOT NULL,
                md5 TEXT NOT NULL,
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
                username TEXT NOT NULL,
                email TEXT NOT NULL,
                password TEXT NOT NULL,
                created_ip TEXT NOT NULL,
                last_ip TEXT,
                remember_token TEXT DEFAULT '',
                updated_by_ip TEXT,
                created_by_user_id INT DEFAULT 1;
                updated_by_user_id INT
                )"""
    )
    cur.execute(
        """CREATE TABLE IF NOT EXISTS user_permissions (
                id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                solder_full BOOLEAN DEFAULT 0,
                solder_users BOOLEAN DEFAULT 0,
                solder_keys BOOLEAN DEFAULT 0,
                solder_clients BOOLEAN DEFAULT 0,
                mods_create BOOLEAN DEFAULT 0,
                mods_manage BOOLEAN DEFAULT 0,
                mods_delete BOOLEAN DEFAULT 0,
                modpacks_create BOOLEAN DEFAULT 0,
                modpacks_manage BOOLEAN DEFAULT 0,
                modpacks_delete BOOLEAN DEFAULT 0,
                modpacks TEXT
                )"""
    )
    cur.execute(
        """CREATE TABLE IF NOT EXISTS clients (
                id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                uuid TEXT NOT NULL UNIQUE,
                )"""
    )
    cur.execute(
        """CREATE TABLE IF NOT EXISTS client_modpack (
                id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                client_id INT NOT NULL,
                modpack_id INT NOT NULL,
                )"""
    )
    cur.execute(
        """CREATE TABLE IF NOT EXISTS keys (
                id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                api_key TEXT NOT NULL UNIQUE,
                )"""
    )


def connect():
    try:
        db_host = getenv("HOST")
        db_port = getenv("PORT")
        db_user = getenv("USER")
        db_pass = getenv("PASSWORD")
        db_name = getenv("DATABASE")
        conn: connector.MySQLConnection = connector.connect(
            host=db_host,
            port=db_port,
            user=db_user,
            passwd=db_pass,
            database=db_name,
        )
    except connector.Error as e:
        print("-" * 70 + "\n Error connecting to database \n" + "-" * 70 + str(e))
    return conn


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
        created_at,
    ) in cur:
        mods.append(
            {
                "id": id,
                "name": name,
                "desc": description,
                "author": author,
                "link": link,
            }
        )
    conn.close()
    return mods


def select_mod(id):
    conn = connect()
    cur = conn.cursor()
    sql = "SELECT * FROM mods WHERE id=?"
    try:
        cur.execute(sql, ([id]))
        return cur.fetchall()[0][1]
    except Exception as e:
        message("feil ved å hente enkel mod", e)


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
        message("en feil oppstod ved å legge inn ny modverisjon", e)
    conn.close()

def get_api_key(key: str) -> list[str]:
        conn = connect()
        cur = conn.cursor()
        sql = "SELECT * FROM keys WHERE api_key=?"
        try:
                cur.execute(sql, (key))
                return cur.fetchall()
        except Exception as e:
                message("An error occurred whilst trying to fetch an API key", e)
