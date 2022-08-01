from datetime import datetime
from os import getenv
from dotenv import load_dotenv
from mysql import connector
import typing

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
    conn.commit()
    conn.close()


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

def select_all_modpacks() -> list:
    conn = connect()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM modpacks WHERE hidden = 0 ORDER BY id ASC")
    ret = cur.fetchall()
    conn.close()
    return ret

def select_all_modpacks_cid(cid: str) -> list:
    conn = connect()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM modpacks WHERE hidden = 0")
    packs = cur.fetchall()
    toremove = []
    for pack in packs:
        if pack["private"]:
            cur.execute("SELECT * FROM client_modpack WHERE client_id IN (SELECT id FROM clients WHERE uuid = %s) AND modpack_id = %s", (cid, pack["id"]))
            if not cur.fetchone():
                toremove.append(pack)
    for pack in toremove:
        packs.remove(pack)
    conn.close()
    return packs

def select_modpack(slug: str) -> typing.Union[dict, None]:
    conn = connect()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM modpacks WHERE slug = %s", (slug,))
    ret = cur.fetchone()
    conn.close()
    return ret

def select_modpack_id(id: int) -> typing.Union[dict, None]:
    conn = connect()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM modpacks WHERE id = %s", (id,))
    ret = cur.fetchone()
    conn.close()
    return ret

def select_modpack_cid(slug: str, cid: str) -> typing.Union[dict, None]:
    conn = connect()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM modpacks WHERE slug = %s", (slug,))
    pack = cur.fetchone()
    if pack is not None and pack["private"]:
        if cid is None:
            return None
        cur.execute("SELECT * FROM client_modpack WHERE client_id IN (SELECT client_id FROM clients WHERE uuid = %s) AND modpack_id = %s", (cid, pack["id"]))
        if cur.fetchone() is None:
            return None
        else:
            return pack
    conn.close()
    return pack

def select_all_mods():
    conn = connect()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM mods ORDER BY id ASC")
    ret = cur.fetchall()
    conn.close()
    return ret

def select_mod_versions_from_build(build: int) -> list:
    conn = connect()
    cur = conn.cursor(dictionary=True)
    sql = "SELECT * FROM modversions AS v JOIN build_modversion AS bv ON bv.modversion_id = v.id JOIN mods AS m ON v.mod_id = m.id WHERE bv.build_id = %s"
    try:
        cur.execute(sql, (build,))
    except Exception as e:
        message("Error whilst fetching mods for modpack", e)
    ret = cur.fetchall()
    conn.close()
    return ret

def select_mod(id):
    conn = connect()
    cur = conn.cursor(dictionary=True)
    sql = "SELECT * FROM mods WHERE id=%s"
    try:
        cur.execute(sql, (id,))
    except Exception as e:
        message("Error whilst fetching mod info", e)
    ret = cur.fetchone()
    conn.close()
    return ret

def select_mod_name(name: str) -> typing.Union[dict, None]:
    conn = connect()
    cur = conn.cursor(dictionary=True)
    sql = "SELECT * FROM mods WHERE name=%s"
    try:
        cur.execute(sql, (name,))
    except Exception as e:
        message("Error whilst fetching mod info", e)
    ret = cur.fetchone()
    conn.close()
    return ret

def select_mod_versions(id: int) -> list:
    conn = connect()
    cur = conn.cursor(dictionary=True)
    sql = "SELECT * FROM modversions WHERE mod_id=%s"
    try:
        cur.execute(sql, (id,))
        return cur.fetchall()
    except Exception as e:
        message("Error whilst fetching mod versions", e)
    conn.close()

def select_mod_version(mod: str, version: str) -> typing.Union[dict, None]:
    conn = connect()
    cur = conn.cursor(dictionary=True)
    sql = "SELECT * FROM modversions WHERE mod_id=%s AND version=%s"
    try:
        cur.execute(sql, (mod, version))
        return cur.fetchone()
    except Exception as e:
        message("Error whilst fetching mod version", e)
    conn.close()

def select_builds(modpack_id: int) -> dict:
    conn = connect()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM builds WHERE modpack_id = %s AND is_published = 1", (modpack_id,))
    except Exception as e:
        message("Error whilst fetching builds", e)
    ret = cur.fetchall()
    conn.close()
    return ret

def select_modpack_build(modpack: str, build: str) -> dict:
    conn = connect()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM builds WHERE modpack_id = %s AND version = %s AND is_published = 1", (modpack, build))
    ret = cur.fetchone()
    conn.close()
    return ret

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
    conn.close()

def get_user_info(user_or_email: str) -> dict:
    conn = connect()
    cur = conn.cursor(dictionary=True)
    sql = "SELECT * FROM users WHERE username = %s OR email = %s"
    try:
        cur.execute(sql, (user_or_email, user_or_email))
        return cur.fetchone()
    except Exception as e:
        message("An error occurred whilst trying to fetch a user", e)
    conn.close()

def add_mod(name: str, pretty_name: str, author: str, description: str, link: str, side: str, note: str) -> bool:
    conn = connect()
    cur = conn.cursor()
    sql = "SELECT * FROM mods WHERE NAME = %s"
    try:
        cur.execute(sql, name)
        if cur.fetchone() is not None:
            return False
    except Exception as e:
        message("An error occurred whilst fetching mod info", e)
        return False
    sql = "INSERT INTO mods(name, pretty_name, author, description, link, side, note, created_at, updated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)"
    try:
        cur.execute(
            sql, (name, pretty_name, author, description, link, side, note, datetime.now(), datetime.now())
        )
        conn.commit()
    except Exception as e:
        message("Error whilst adding a mod to the database", e)
        return False
    conn.close()
    return True
