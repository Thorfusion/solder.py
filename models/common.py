import os
from dotenv import load_dotenv

from models.database import Database

## Solderpy version
solderpy_version = "1.10.0"

load_dotenv(".env")

## Enviroment variables
new_user = False
migratetechnic = False
api_only = False
management_only = False
write_api = False
legacy_modversion_adding = False
debug = False
reverse_proxy = False

host = os.getenv("APP_HOST")
port = os.getenv("APP_PORT")
app_url = os.getenv("APP_URL")
curseforge_api_key = os.getenv("CURSEFORGE_API_KEY")

if os.getenv("PROXY_IP"):
    reverse_proxy = True

if os.getenv("NEW_USER"):
    new_user = os.getenv("NEW_USER").lower() in ["true", "t", "1", "yes", "y"]
if os.getenv("TECHNIC_MIGRATION"):
    migratetechnic = os.getenv("TECHNIC_MIGRATION").lower() in ["true", "t", "1", "yes", "y"]

if os.getenv("API_ONLY"):
    api_only = os.getenv("API_ONLY").lower() in ["true", "t", "1", "yes", "y"]
if os.getenv("MANAGEMENT_ONLY"):
    management_only = os.getenv("MANAGEMENT_ONLY").lower() in ["true", "t", "1", "yes", "y"]
if os.getenv("WRITE_API") or os.getenv("WRITABLE_API"):
    write_api = (os.getenv("WRITE_API") or os.getenv("WRITABLE_API")).lower() in ["true", "t", "1", "yes", "y"]
if os.getenv("ENABLE_LEGACY_MODVERSION_ADDING"):
    legacy_modversion_adding = os.getenv(
        "ENABLE_LEGACY_MODVERSION_ADDING"
    ).lower() in ["true", "t", "1", "yes", "y"]

if os.getenv("APP_DEBUG"):
    debug = os.getenv("APP_DEBUG").lower() in ["true", "t", "1", "yes", "y"]

if not os.getenv("PUBLIC_REPO_LOCATION"):
    print("PUBLIC_REPO_LOCATION not set")
if not os.getenv("MD5_REPO_LOCATION"):
    print("MD5_REPO_LOCATION not set")
public_repo_url = os.getenv("PUBLIC_REPO_LOCATION")
md5_repo_url = os.getenv("MD5_REPO_LOCATION")

r2_url = os.getenv("R2_URL")
if not os.getenv("DB_DATABASE"):
    print("DB_DATABASE not set")
db_name = os.getenv("DB_DATABASE")

UPLOAD_FOLDER = "./mods/"

## S3 bucket variables
R2_ENDPOINT = os.getenv("R2_ENDPOINT")
R2_URL = os.getenv("R2_URL")
R2_REGION = os.getenv("R2_REGION")
R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY")
R2_SECRET_KEY = os.getenv("R2_SECRET_KEY")
R2_BUCKET = os.getenv("R2_BUCKET")

DB_IS_UP = Database.is_setup()
if DB_IS_UP == 1 and (not api_only or write_api):
    if migratetechnic:
        schema_ready = Database.migratetechnic_tables()
    else:
        schema_ready = Database.ensure_runtime_schema()
    if not schema_ready:
        DB_IS_UP = 2

if (os.getenv("CACHE_SIZE")):
    cache_size = int(os.getenv("CACHE_SIZE"))
else: 
    print("No cache size specified, using default")
    cache_size = int(100)

if (os.getenv("CACHE_TTL")):
    cache_ttl = int(os.getenv("CACHE_TTL"))
else:
    print("No cache ttl specified, using default")
    cache_ttl = 300

class common:

    _UPDATE_QUERIES = {
        ("builds", "is_published"): "UPDATE builds SET is_published = %s WHERE id = %s",
        ("builds", "private"): "UPDATE builds SET private = %s WHERE id = %s",
        ("modpacks", "enable_optionals"): "UPDATE modpacks SET enable_optionals = %s WHERE id = %s",
        ("modpacks", "enable_server"): "UPDATE modpacks SET enable_server = %s WHERE id = %s",
        ("modpacks", "hidden"): "UPDATE modpacks SET hidden = %s WHERE id = %s",
        ("modpacks", "latest"): "UPDATE modpacks SET latest = %s WHERE id = %s",
        ("modpacks", "pinned"): "UPDATE modpacks SET pinned = %s WHERE id = %s",
        ("modpacks", "private"): "UPDATE modpacks SET private = %s WHERE id = %s",
        ("modpacks", "recommended"): "UPDATE modpacks SET recommended = %s WHERE id = %s",
    }

    @staticmethod
    def update_checkbox(where_id, value, column, table):
        try:
            query = common._UPDATE_QUERIES[(table, column)]
        except KeyError as error:
            raise ValueError(f"Unsupported update target: {table}.{column}") from error

        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute(query, (value, where_id))
        conn.commit()
