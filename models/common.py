import os
from dotenv import load_dotenv

from models.database import Database

## Solderpy version
solderpy_version = "1.5.0"

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

mirror_url = os.getenv("SOLDER_MIRROR_URL")
repo_url = os.getenv("SOLDER_REPO_LOCATION")

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

class common:

    @staticmethod
    def update_checkbox(where_id, value, column, table):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("UPDATE {} SET {} = %s WHERE id = %s".format(table, column), (value, where_id))
        conn.commit()

