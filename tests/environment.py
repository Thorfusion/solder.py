"""Stable, non-production settings used while importing the application."""

import os


TEST_ENVIRONMENT = {
    "APP_DEBUG": "false",
    "APP_HOST": "127.0.0.1",
    "APP_PORT": "5000",
    "APP_URL": "https://solder.example.test/",
    "API_ONLY": "false",
    "CACHE_SIZE": "100",
    "CACHE_TTL": "300",
    "DB_DATABASE": "solder_test",
    "DB_HOST": "127.0.0.1",
    "DB_PASSWORD": "test",
    "DB_PORT": "3306",
    "DB_USER": "test",
    "DISABLE_is_setup": "true",
    "MANAGEMENT_ONLY": "false",
    "WRITE_API": "false",
    "MD5_REPO_LOCATION": "https://cdn.example.test/mods/",
    "NEW_USER": "false",
    "PROXY_IP": "",
    "PUBLIC_REPO_LOCATION": "https://cdn.example.test/mods/",
    "R2_ACCESS_KEY": "test",
    "R2_BUCKET": "test",
    "R2_ENDPOINT": "https://r2.example.test",
    "R2_REGION": "auto",
    "R2_SECRET_KEY": "test",
    "R2_URL": "https://cdn.example.test/mods/",
    "TECHNIC_MIGRATION": "false",
}


def configure_test_environment() -> None:
    """Override local settings before a solder.py module is imported."""
    os.environ.update(TEST_ENVIRONMENT)
