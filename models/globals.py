import os
from dotenv import load_dotenv

## Solderpy version
solderpy_version = "1.4.0"

load_dotenv(".env")

## Enviroment variables
new_user = False
migratetechnic = False
debug = False

host = os.getenv("APP_URL")
port = os.getenv("APP_PORT")

new_user = os.getenv("NEW_USER")
migratetechnic = os.getenv("TECHNIC_MIGRATION")

debug = os.getenv("APP_DEBUG").lower() in ["true", "t", "1", "yes", "y"]

mirror_url = os.getenv("SOLDER_MIRROR_URL")
repo_url = os.getenv("SOLDER_REPO_LOCATION")

r2_url = os.getenv("R2_URL")
db_name = os.getenv("DB_DATABASE")

## S3 bucket variables
R2_ENDPOINT = os.getenv("R2_ENDPOINT")
R2_URL = os.getenv("R2_URL")
R2_REGION = os.getenv("R2_REGION")
R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY")
R2_SECRET_KEY = os.getenv("R2_SECRET_KEY")
R2_BUCKET = os.getenv("R2_BUCKET")
