import os
from dotenv import load_dotenv
from cachetools import FIFOCache, LRUCache, LFUCache, RRCache

from models.database import Database

## Solderpy version
solderpy_version = "1.5.2"

load_dotenv(".env")

## Enviroment variables
new_user = False
migratetechnic = False
api_only = False
management_only = False
debug = False

host = os.getenv("APP_URL")
port = os.getenv("APP_PORT")

if os.getenv("NEW_USER"):
    new_user = os.getenv("NEW_USER").lower() in ["true", "t", "1", "yes", "y"]
if os.getenv("TECHNIC_MIGRATION"):
    migratetechnic = os.getenv("TECHNIC_MIGRATION").lower() in ["true", "t", "1", "yes", "y"]

if os.getenv("API_ONLY"):
    api_only = os.getenv("API_ONLY").lower() in ["true", "t", "1", "yes", "y"]
if os.getenv("MANAGEMENT_ONLY"):
    management_only = os.getenv("MANAGEMENT_ONLY").lower() in ["true", "t", "1", "yes", "y"]

if os.getenv("APP_DEBUG"):
    debug = os.getenv("APP_DEBUG").lower() in ["true", "t", "1", "yes", "y"]

public_repo_url = os.getenv("PUBLIC_REPO_LOCATION")
md5_repo_url = os.getenv("MD5_REPO_LOCATION")
solder_url = os.getenv("PUBLIC_URL_LOCATION")

r2_url = os.getenv("R2_URL")
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

cache_algorithm = os.getenv("CACHE_ALGORITHM").upper()
if cache_algorithm not in ("FIFO", "LRU", "LFU", "RR"):
    print("Invalid cache algorithm, using LRU as default")
    cache_algorithm = "LRU"

cache_type = None
if cache_algorithm == "FIFO":
    cache_type = FIFOCache
elif cache_algorithm == "LRU":
    cache_type = LRUCache
elif cache_algorithm == "LFU":
    cache_type = LFUCache
elif cache_algorithm == "RR":
    cache_type = RRCache

cache_size = int(os.getenv("CACHE_SIZE"))

class common:

    @staticmethod
    def update_checkbox(where_id, value, column, table):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("UPDATE {} SET {} = %s WHERE id = %s".format(table, column), (value, where_id))
        conn.commit()
