"""Exercise the production image against current and migrated MySQL schemas."""

from __future__ import annotations

import http.cookiejar
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MYSQL_IMAGE = os.environ.get("MYSQL_TEST_IMAGE", "mysql:8.4")
DATABASE = "solder"
DATABASE_USER = "solder-ci"
DATABASE_PASSWORD = "solder-ci-password"
ROOT_PASSWORD = "solder-ci-root-password"


def run(*arguments: str, **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run([*arguments], check=True, text=True, **kwargs)


def docker(*arguments: str, **kwargs) -> subprocess.CompletedProcess[str]:
    return run("docker", *arguments, **kwargs)


def database_environment(
    host: str,
    *,
    api_only: bool = False,
    write_api: bool = False,
    user: str = DATABASE_USER,
    password: str = DATABASE_PASSWORD,
) -> list[str]:
    values = {
        "APP_PORT": "5000",
        "APP_URL": "https://solder.example.invalid/",
        "API_ONLY": str(api_only).lower(),
        "WRITE_API": str(write_api).lower(),
        "AWS_EC2_METADATA_DISABLED": "true",
        "CACHE_SIZE": "100",
        "CACHE_TTL": "300",
        "DB_DATABASE": DATABASE,
        "DB_HOST": host,
        "DB_PASSWORD": password,
        "DB_PORT": "3306",
        "DB_USER": user,
        "MD5_REPO_LOCATION": "https://example.invalid/mods/",
        "PUBLIC_REPO_LOCATION": "https://example.invalid/mods/",
        "R2_ACCESS_KEY": "smoke-test",
        "R2_REGION": "auto",
        "R2_SECRET_KEY": "smoke-test",
        "R2_URL": "https://example.invalid/mods/",
        # The smoke endpoint is deliberately plain HTTP on loopback. Production
        # keeps Secure cookies enabled behind its HTTPS reverse proxy.
        "SESSION_COOKIE_SECURE": "false",
    }
    arguments: list[str] = []
    for key, value in values.items():
        arguments.extend(("--env", f"{key}={value}"))
    return arguments


def container_running(container: str) -> bool:
    result = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Running}}", container],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def wait_for_mysql(container: str) -> None:
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        result = subprocess.run(
            [
                "docker",
                "exec",
                "--env",
                f"MYSQL_PWD={ROOT_PASSWORD}",
                container,
                "mysqladmin",
                "ping",
                "--host=127.0.0.1",
                "--user=root",
                "--silent",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            return
        if not container_running(container):
            raise RuntimeError("MySQL exited before becoming ready")
        time.sleep(2)
    raise TimeoutError("MySQL did not become ready within 120 seconds")


def mysql(container: str, sql: str) -> str:
    result = subprocess.run(
        [
            "docker",
            "exec",
            "--interactive",
            "--env",
            f"MYSQL_PWD={ROOT_PASSWORD}",
            container,
            "mysql",
            "--batch",
            "--skip-column-names",
            "--user=root",
            DATABASE,
        ],
        input=sql,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"MySQL command failed:\n{result.stderr.strip()}")
    return result.stdout.strip()


def migrate_technic_schema(image: str, network: str) -> None:
    migration_command = (
        "from models.database import Database; "
        "raise SystemExit(0 if Database.migratetechnic_tables() else 1)"
    )
    for _ in range(2):
        result = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                network,
                *database_environment("mysql"),
                image,
                "python",
                "-c",
                migration_command,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "Technic migration failed:\n"
                f"{result.stdout.strip()}\n{result.stderr.strip()}"
            )


def create_fresh_schema(image: str, network: str) -> None:
    command = (
        "from models.database import Database; "
        "raise SystemExit(0 if Database.create_tables() else 1)"
    )
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            network,
            *database_environment("mysql"),
            image,
            "python",
            "-c",
            command,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "Fresh database setup failed:\n"
            f"{result.stdout.strip()}\n{result.stderr.strip()}"
        )


def verify_read_only_api_startup(image: str, network: str, database_container: str) -> None:
    mysql(
        database_container,
        "CREATE USER 'solder-readonly'@'%' IDENTIFIED BY 'readonly-password'; "
        "GRANT SELECT ON solder.* TO 'solder-readonly'@'%';",
    )
    command = (
        "from models.common import DB_IS_UP; from app import app; "
        "routes = {rule.rule for rule in app.url_map.iter_rules()}; "
        "required = {'/api/', '/packwiz/<pack_slug>/<selector>/pack.toml', "
        "'/filedirector/<pack_slug>/<selector>/<bundle_name>.bundle.json'}; "
        "raise SystemExit(0 if DB_IS_UP == 1 and required <= routes "
        "and '/mainsettings' not in routes else 1)"
    )
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            network,
            *database_environment(
                "mysql",
                api_only=True,
                user="solder-readonly",
                password="readonly-password",
            ),
            image,
            "python",
            "-c",
            command,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            "API-only startup did not work with read-only database access:\n"
            f"{result.stdout.strip()}\n{result.stderr.strip()}"
        )


def verify_api_only_distribution_files(image: str, network: str) -> None:
    """Render both formats through an API-only app using SELECT permission."""
    command = (
        "from app import app; client = app.test_client(); "
        "pack = client.get('/packwiz/ci-hidden-pack/latest/pack.toml', "
        "follow_redirects=True); "
        "bundle = client.get('/filedirector/ci-hidden-pack/latest/"
        "mods.bundle.json'); "
        "version = client.get('/filedirector/ci-hidden-pack/latest/"
        "version.txt'); "
        "valid = (pack.status_code == 200 and b'packwiz:1.1.0' in pack.data "
        "and bundle.status_code == 200 and "
        "bundle.get_json().get('url') and version.data.strip() == b'1.0'); "
        "private = client.get('/filedirector/ci-private-pack/latest/"
        "version.txt'); valid = valid and private.status_code == 404; "
        "raise SystemExit(0 if valid else 1)"
    )
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            network,
            *database_environment(
                "mysql",
                api_only=True,
                user="solder-readonly",
                password="readonly-password",
            ),
            image,
            "python",
            "-c",
            command,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            "API-only mode did not serve enabled distribution files:\n"
            f"{result.stdout.strip()}\n{result.stderr.strip()}"
        )


def verify_performance_indexes(database_container: str) -> None:
    expected_indexes = (
        ("build_modversion", "build_id,modversion_id"),
        ("build_modversion", "modversion_id,build_id"),
        ("builds", "modpack_id,version,is_published,private"),
        ("builds", "modpack_id,id"),
        ("client_modpack", "modpack_id,client_id"),
        ("client_modpack", "client_id,modpack_id"),
        ("modversions", "mod_id,mcversion"),
        ("modversions", "mod_id,mcversion,modloader"),
        ("modversions", "mod_id,version"),
        ("modversions", "mod_id,id"),
        ("mods", "integration_provider,integration_project_id"),
        ("modversions", "mod_id,integration_version_id"),
        ("modversion_minecraft_versions", "minecraft_version,modversion_id"),
        ("maven_artifacts", "repository_id"),
        ("maven_versions", "maven_artifact_id,minecraft,modloader,enabled,available,metadata_order"),
        ("build_optional_groups", "build_id,sort_order"),
        ("build_optional_group_items", "group_id,sort_order"),
        ("build_optional_group_items", "build_modversion_id"),
        ("user_permissions", "user_id"),
        ("user_modpack", "user_id,modpack_id"),
        ("user_modpack", "modpack_id,user_id"),
        ("clients", "uuid"),
        ("keys", "api_key"),
        ("users", "username"),
        ("sessions", "user_id"),
        ("sessions", "expiry"),
        ("modpack_publication_targets", "provider_account_id"),
    )
    for table, columns in expected_indexes:
        index_count = mysql(
            database_container,
            "SELECT COUNT(*) FROM ("
            "SELECT INDEX_NAME, GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) columns "
            "FROM information_schema.STATISTICS "
            f"WHERE TABLE_SCHEMA = '{DATABASE}' AND TABLE_NAME = '{table}' "
            "GROUP BY INDEX_NAME"
            ") indexes_by_column "
            f"WHERE columns = '{columns}' OR columns LIKE '{columns},%';",
        )
        if int(index_count) < 1:
            raise AssertionError(f"No index on {table} covers the columns {columns}")


def verify_compatible_collations(database_container: str) -> None:
    incompatible = mysql(
        database_container,
        "SELECT COALESCE(GROUP_CONCAT(CONCAT(TABLE_NAME, ':', "
        "TABLE_COLLATION) ORDER BY TABLE_NAME SEPARATOR ','), '') "
        "FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA = '{DATABASE}' "
        "AND TABLE_TYPE = 'BASE TABLE' "
        # 1.7.4's standalone migration ledger is never joined to application
        # data and is intentionally outside Database.APPLICATION_TABLES.
        "AND TABLE_NAME <> 'migrations' "
        "AND TABLE_COLLATION <> 'utf8mb4_unicode_ci';",
    )
    if incompatible:
        raise AssertionError(
            f"Application tables use incompatible collations: {incompatible}"
        )


def verify_technic_migration(database_container: str) -> None:
    expected_columns = (
        ("build_modversion", "optional"),
        ("builds", "marked"),
        ("builds", "modloader"),
        ("builds", "java_runtime"),
        ("mods", "modtype"),
        ("mods", "notes"),
        ("mods", "side"),
        ("mods", "integration_provider"),
        ("mods", "integration_project_id"),
        ("modpacks", "enable_optionals"),
        ("modpacks", "enable_server"),
        ("modpacks", "optional_mode"),
        ("modpacks", "pinned"),
        ("modpacks", "user_id"),
        ("modpacks", "url"),
        ("modpacks", "icon_md5"),
        ("modpacks", "logo_md5"),
        ("modpacks", "background_md5"),
        ("modpacks", "icon"),
        ("modpacks", "logo"),
        ("modpacks", "background"),
        ("modpacks", "icon_url"),
        ("modpacks", "logo_url"),
        ("modpacks", "background_url"),
        ("modversions", "jarmd5"),
        ("modversions", "jarfilesize"),
        ("modversions", "mcversion"),
        ("modversions", "modloader"),
        ("modversions", "integration_version_id"),
        ("maven_artifacts", "solderpy_loader_direct"),
        ("platform_export_overrides", "override_solder_only"),
        ("technic_solderpy_loader_builds", "delivery_mode"),
        ("user_permissions", "solder_env"),
        ("users", "night_mode"),
        ("users", "two_factor_confirmed_at"),
        ("users", "two_factor_recovery_codes"),
        ("users", "two_factor_secret"),
    )
    conditions = " OR ".join(
        f"(TABLE_NAME = '{table}' AND COLUMN_NAME = '{column}')"
        for table, column in expected_columns
    )
    column_count = mysql(
        database_container,
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA = '{DATABASE}' AND ({conditions});",
    )
    if int(column_count) != len(expected_columns):
        raise AssertionError(
            f"Migration created {column_count}/{len(expected_columns)} expected columns"
        )

    legacy_note_count = mysql(
        database_container,
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA = '{DATABASE}' AND TABLE_NAME = 'mods' "
        "AND COLUMN_NAME = 'note';",
    )
    if legacy_note_count != "0":
        raise AssertionError("Technic migration added solder.py's legacy note column")

    table_count = mysql(
        database_container,
        "SELECT COUNT(*) FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA = '{DATABASE}' "
        "AND TABLE_NAME IN ('sessions', 'login_attempts', 'user_modpack', 'mod_dependencies', "
        "'solder_settings', 'platform_export_overrides', "
        "'build_optional_groups', 'build_optional_group_items', "
        "'technic_solderpy_loader_builds', "
        "'modversion_download_overrides', "
        "'modversion_download_sources', "
        "'modversion_minecraft_versions', "
        "'publishing_provider_accounts', "
        "'modpack_publication_targets', 'modpack_publication_runs', "
        "'personal_access_tokens', 'password_reset_tokens', "
        "'maven_repositories', 'maven_artifacts', 'maven_versions');",
    )
    if int(table_count) != 20:
        raise AssertionError(
            "Migration did not preserve the current Technic tables and create "
            "the solder.py tables"
        )

    nullable_timestamps = mysql(
        database_container,
        "SELECT COUNT(*) FROM modpacks "
        "WHERE id = 1 AND created_at IS NULL AND updated_at IS NULL;",
    )
    if nullable_timestamps != "1":
        raise AssertionError("Migration changed valid nullable Technic timestamps")

    modpack_state = mysql(
        database_container,
        "SELECT user_id, url FROM modpacks WHERE id = 1;",
    )
    if modpack_state != "7\thttps://example.invalid/pack":
        raise AssertionError(f"Migration did not preserve Technic data: {modpack_state}")

    modpack_grant = mysql(
        database_container,
        "SELECT COUNT(*) FROM user_modpack WHERE user_id = 7 AND modpack_id = 1;",
    )
    if modpack_grant != "1":
        raise AssertionError("Migration did not preserve Technic modpack access")

    notes_state = mysql(
        database_container,
        "SELECT notes FROM mods WHERE id = 1;",
    )
    if notes_state != "Technic private mod note":
        raise AssertionError(f"Migration did not preserve Technic notes: {notes_state}")

    user_id_definition = mysql(
        database_container,
        "SELECT CONCAT(IS_NULLABLE, ':', IF(COLUMN_DEFAULT IS NULL, 'NULL', "
        "COLUMN_DEFAULT)) FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA = '{DATABASE}' AND TABLE_NAME = 'modpacks' "
        "AND COLUMN_NAME = 'user_id';",
    )
    if user_id_definition != "NO:NULL":
        raise AssertionError(f"Unexpected user_id definition: {user_id_definition}")

    verify_performance_indexes(database_container)


def verify_fresh_schema(database_container: str) -> None:
    unwanted_columns = (
        ("modpacks", "url"),
        ("modpacks", "icon_md5"),
        ("modpacks", "logo_md5"),
        ("modpacks", "background_md5"),
        ("modpacks", "icon"),
        ("modpacks", "logo"),
        ("modpacks", "background"),
        ("modpacks", "icon_url"),
        ("modpacks", "logo_url"),
        ("modpacks", "background_url"),
        ("mods", "note"),
        ("users", "two_factor_secret"),
        ("users", "two_factor_recovery_codes"),
        ("users", "two_factor_confirmed_at"),
    )
    conditions = " OR ".join(
        f"(TABLE_NAME = '{table}' AND COLUMN_NAME = '{column}')"
        for table, column in unwanted_columns
    )
    unwanted_count = mysql(
        database_container,
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA = '{DATABASE}' AND ({conditions});",
    )
    if unwanted_count != "0":
        raise AssertionError(f"Fresh schema added {unwanted_count} unused columns")

    notes_column_count = mysql(
        database_container,
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA = '{DATABASE}' AND "
        "TABLE_NAME = 'mods' AND COLUMN_NAME = 'notes';",
    )
    if notes_column_count != "1":
        raise AssertionError("Fresh schema did not create Technic-compatible notes")

    java_runtime_column_count = mysql(
        database_container,
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA = '{DATABASE}' AND "
        "TABLE_NAME = 'builds' AND COLUMN_NAME = 'java_runtime';",
    )
    if java_runtime_column_count != "1":
        raise AssertionError("Fresh schema did not create java_runtime")

    night_mode_column_count = mysql(
        database_container,
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA = '{DATABASE}' AND "
        "TABLE_NAME = 'users' AND COLUMN_NAME = 'night_mode';",
    )
    if night_mode_column_count != "1":
        raise AssertionError("Fresh schema did not create user night mode")

    integration_schema_count = mysql(
        database_container,
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA = '{DATABASE}' AND ("
        "(TABLE_NAME = 'mods' AND COLUMN_NAME IN "
        "('integration_provider', 'integration_project_id')) OR "
        "(TABLE_NAME = 'modversions' AND COLUMN_NAME = "
        "'integration_version_id'));",
    )
    if integration_schema_count != "3":
        raise AssertionError("Fresh schema did not create integration columns")

    direct_source_column_count = mysql(
        database_container,
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA = '{DATABASE}' AND ("
        "(TABLE_NAME = 'modversion_download_overrides' AND "
        "COLUMN_NAME = 'jar_url') OR "
        "(TABLE_NAME = 'modversion_download_sources' AND COLUMN_NAME IN "
        "('provider', 'url', 'filename', 'md5', 'sha1', 'sha512', "
        "'filesize')) OR "
        "(TABLE_NAME = 'maven_artifacts' AND COLUMN_NAME = "
        "'solderpy_loader_direct'));",
    )
    if direct_source_column_count != "9":
        raise AssertionError("Fresh schema did not create direct JAR source columns")

    integration_table_count = mysql(
        database_container,
        "SELECT COUNT(*) FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA = '{DATABASE}' "
        "AND TABLE_NAME = 'integration_credentials';",
    )
    if integration_table_count != "0":
        raise AssertionError("Fresh schema still creates CurseForge credentials")

    unwanted_table_count = mysql(
        database_container,
        "SELECT COUNT(*) FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA = '{DATABASE}' "
        "AND TABLE_NAME = 'password_reset_tokens';",
    )
    if unwanted_table_count != "0":
        raise AssertionError("Fresh schema added the unused password-reset table")

    token_table_count = mysql(
        database_container,
        "SELECT COUNT(*) FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA = '{DATABASE}' "
        "AND TABLE_NAME = 'personal_access_tokens';",
    )
    if token_table_count != "1":
        raise AssertionError("Fresh schema did not create write API tokens")

    settings_table_count = mysql(
        database_container,
        "SELECT COUNT(*) FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA = '{DATABASE}' "
        "AND TABLE_NAME = 'solder_settings';",
    )
    if settings_table_count != "1":
        raise AssertionError("Fresh schema did not create distribution settings")

    advanced_optional_table_count = mysql(
        database_container,
        "SELECT COUNT(*) FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA = '{DATABASE}' AND TABLE_NAME IN "
        "('build_optional_groups', 'build_optional_group_items');",
    )
    if advanced_optional_table_count != "2":
        raise AssertionError("Fresh schema did not create advanced optionals")

    advanced_listing_nullable = mysql(
        database_container,
        "SELECT IS_NULLABLE FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA = '{DATABASE}' "
        "AND TABLE_NAME = 'build_optional_group_items' "
        "AND COLUMN_NAME = 'group_id';",
    )
    if advanced_listing_nullable != "YES":
        raise AssertionError(
            "Advanced optional work-list entries still require a group"
        )

    technic_solderpy_loader_table_count = mysql(
        database_container,
        "SELECT COUNT(*) FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA = '{DATABASE}' "
        "AND TABLE_NAME = 'technic_solderpy_loader_builds';",
    )
    if technic_solderpy_loader_table_count != "1":
        raise AssertionError(
            "Fresh schema did not create Technic SolderPy Loader settings"
        )
    technic_delivery_column_count = mysql(
        database_container,
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA = '{DATABASE}' "
        "AND TABLE_NAME = 'technic_solderpy_loader_builds' "
        "AND COLUMN_NAME = 'delivery_mode';",
    )
    if technic_delivery_column_count != "1":
        raise AssertionError("Fresh schema omitted the Technic delivery mode")

    advanced_optional_column_count = mysql(
        database_container,
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA = '{DATABASE}' AND ("
        "(TABLE_NAME = 'modpacks' AND COLUMN_NAME = 'optional_mode') OR "
        "(TABLE_NAME = 'platform_export_overrides' AND "
        "COLUMN_NAME = 'override_solder_only'));",
    )
    if advanced_optional_column_count != "2":
        raise AssertionError("Fresh schema omitted advanced export columns")

    override_default = mysql(
        database_container,
        "SELECT CONCAT(modrinth_project_id, ':', curseforge_project_id, ':', "
        "side, ':', enabled, ':', override_solder_only, ':', built_in) "
        "FROM platform_export_overrides "
        "WHERE name = 'TX Loader';",
    )
    if override_default != "eh8us8FY:706505:CLIENT:0:0:1":
        raise AssertionError(
            f"Fresh schema has an invalid TX Loader override: {override_default}"
        )

    maven_table_count = mysql(
        database_container,
        "SELECT COUNT(*) FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA = '{DATABASE}' "
        "AND TABLE_NAME IN "
        "('maven_repositories', 'maven_artifacts', 'maven_versions');",
    )
    if maven_table_count != "3":
        raise AssertionError("Fresh schema did not create the Maven catalog")

    user_id_definition = mysql(
        database_container,
        "SELECT CONCAT(IS_NULLABLE, ':', IF(COLUMN_DEFAULT IS NULL, 'NULL', "
        "COLUMN_DEFAULT)) FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA = '{DATABASE}' AND TABLE_NAME = 'modpacks' "
        "AND COLUMN_NAME = 'user_id';",
    )
    if user_id_definition != "NO:NULL":
        raise AssertionError(f"Unexpected fresh user_id definition: {user_id_definition}")

    automatic_timestamps = mysql(
        database_container,
        "SELECT COUNT(*) FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA = '{DATABASE}' "
        "AND TABLE_NAME IN ('modpacks', 'builds', 'mods', 'modversions', "
        "'build_modversion', 'users', 'user_permissions', 'clients', "
        "'client_modpack', 'keys') "
        "AND COLUMN_NAME IN ('created_at', 'updated_at') "
        "AND IS_NULLABLE = 'NO' "
        "AND UPPER(COLUMN_DEFAULT) LIKE 'CURRENT_TIMESTAMP%';",
    )
    if automatic_timestamps != "20":
        raise AssertionError(
            f"Fresh schema has {automatic_timestamps}/20 automatic timestamps"
        )

    verify_performance_indexes(database_container)


def seed_fresh_database(database_container: str) -> None:
    mysql(
        database_container,
        """INSERT INTO modpacks
               (id, name, slug, user_id, recommended, latest, `order`, hidden,
                private, pinned, enable_optionals, enable_server)
           VALUES
               (1, 'CI Example Pack', 'ci-example-pack', 1, '1.0', '1.0',
                0, 0, 0, 0, 0, 0);
           INSERT INTO users
               (id, username, email, password, created_ip, last_ip,
                created_at, updated_at, remember_token, updated_by_ip,
                created_by_user_id, updated_by_user_id)
           VALUES
               (1, 'ci-user', 'ci-user@example.invalid',
                '59e423d8ee3b20da4266e8d80366b6b610975cfd405a75bfb05cf0b1850247fdf767cd96a6eb70ef18c496d83fdb21e6b1df0c1b846540a76de31baee15eaebc',
                '127.0.0.1', '127.0.0.1', '2024-01-01 00:00:00',
                '2024-01-01 00:00:00', '', '127.0.0.1', 1, 1);
           INSERT INTO user_permissions
               (id, user_id, solder_full, solder_users, mods_create,
                mods_manage, mods_delete, modpacks, created_at, updated_at,
                solder_keys, solder_clients, modpacks_create, modpacks_manage,
                modpacks_delete)
           VALUES
               (1, 1, 1, 1, 1, 1, 1, '1', '2024-01-01 00:00:00',
                '2024-01-01 00:00:00', 1, 1, 1, 1, 1);
           INSERT INTO `keys` (id, name, api_key, created_at, updated_at)
           VALUES
               (1, 'CI key', 'ci-api-key-not-a-secret',
                '2024-01-01 00:00:00', '2024-01-01 00:00:00');
           INSERT INTO clients (id, name, uuid, created_at, updated_at)
           VALUES
               (1, 'CI client', 'ci-client-id-not-a-secret',
                '2024-01-01 00:00:00', '2024-01-01 00:00:00');
           INSERT INTO builds
               (id, modpack_id, version, created_at, updated_at, minecraft,
                forge, is_published, private, min_java, min_memory)
           VALUES
               (1, 1, '1.0', '2024-01-01 00:00:00',
                '2024-01-01 00:00:00', '1.21.1', NULL, 1, 0, '21', 4096);
           INSERT INTO client_modpack
               (id, client_id, modpack_id, created_at, updated_at)
           VALUES
               (1, 1, 1, '2024-01-01 00:00:00', '2024-01-01 00:00:00');
           INSERT INTO mods
               (id, name, description, author, link, created_at, updated_at,
                pretty_name, side, modtype)
           VALUES
               (1, 'ci-example-mod', 'Synthetic integration-test mod', 'CI',
                'https://example.invalid/mod', '2024-01-01 00:00:00',
                '2024-01-01 00:00:00', 'CI Example Mod', 'BOTH', 'MOD');
           INSERT INTO modversions
               (id, mod_id, version, mcversion, md5, created_at, updated_at,
                filesize)
           VALUES
               (1, 1, '1.0', '1.21.1',
                '00000000000000000000000000000000', '2024-01-01 00:00:00',
                '2024-01-01 00:00:00', 1024);
           INSERT INTO build_modversion
               (id, modversion_id, build_id, created_at, updated_at, optional)
           VALUES
               (1, 1, 1, '2024-01-01 00:00:00',
                '2024-01-01 00:00:00', 0);
           INSERT INTO sessions (token, ip, expiry, user_id)
           VALUES
               ('ci-session-token-not-a-secret', '127.0.0.1',
                '2030-01-01 00:00:00', 1);
           INSERT INTO user_modpack
               (id, user_id, modpack_id, created_at, updated_at)
           VALUES
               (1, 1, 1, '2024-01-01 00:00:00',
                '2024-01-01 00:00:00');""",
    )


def seed_dependency_scenario(database_container: str) -> None:
    mysql(
        database_container,
        """INSERT INTO mods
               (id, name, description, author, link, created_at, updated_at,
                pretty_name, side, modtype)
           VALUES
               (2, 'ci-required-library', 'Synthetic required dependency', 'CI',
                'https://example.invalid/required', '2024-01-01 00:00:00',
                '2024-01-01 00:00:00', 'CI Required Library', 'BOTH', 'MOD'),
               (3, 'ci-parent-mod', 'Synthetic mod with a dependency', 'CI',
                'https://example.invalid/parent', '2024-01-01 00:00:00',
                '2024-01-01 00:00:00', 'CI Parent Mod', 'BOTH', 'MOD');
           INSERT INTO modversions
               (id, mod_id, version, mcversion, md5, created_at, updated_at, filesize)
           VALUES
               (2, 2, '1.21.1-1.0', '1.21.1',
                '11111111111111111111111111111111', '2024-01-01 00:00:00',
                '2024-01-01 00:00:00', 1024),
               (3, 3, '1.21.1-1.0', '1.21.1',
                '22222222222222222222222222222222', '2024-01-01 00:00:00',
                '2024-01-01 00:00:00', 1024),
               (4, 2, '1.21.1-2.0', '1.21.1',
                '33333333333333333333333333333333', '2024-01-02 00:00:00',
                '2024-01-02 00:00:00', 2048),
               (5, 2, 'universal-3.0', NULL,
                '44444444444444444444444444444444', '2024-01-03 00:00:00',
                '2024-01-03 00:00:00', 4096);""",
    )


def seed_api_access_scenario(database_container: str) -> None:
    mysql(
        database_container,
        """INSERT INTO modpacks
               (id, name, slug, user_id, recommended, latest, `order`, hidden,
                private, pinned, enable_optionals, enable_server)
           VALUES
               (20, 'CI Hidden Pack', 'ci-hidden-pack', 1, '1.0', '1.0',
                20, 1, 0, 0, 0, 0),
               (21, 'CI Private Pack', 'ci-private-pack', 1, '1.0', '1.0',
                21, 0, 1, 0, 0, 0),
               (22, 'CI API Variants', 'ci-api-variants', 1,
                '1.20.1-beta-2', '1.20.1-beta-2', 22, 0, 0, 0, 1, 1);

           INSERT INTO builds
               (id, modpack_id, version, minecraft, forge, is_published,
                private, min_java, min_memory, marked)
           VALUES
               (20, 20, '1.0', '1.21.1', NULL, 1, 0, '21', 2048, 0),
               (21, 21, '1.0', '1.21.1', NULL, 1, 0, '21', 2048, 0),
               (22, 22, '1.20.1-beta-2', '1.20.1', '47.3.0', 1, 0,
                '17', 4096, 0),
               (23, 22, 'private', '1.20.1', NULL, 1, 1, '17', 4096, 0),
               (24, 22, 'unpublished', '1.20.1', NULL, 0, 0,
                '17', 4096, 0),
               (25, 22, 'previous', '1.20.1', '47.3.0', 1, 0,
                '17', 4096, 0);

           INSERT INTO client_modpack
               (id, client_id, modpack_id, created_at, updated_at)
           VALUES
               (21, 1, 21, '2024-01-01 00:00:00', '2024-01-01 00:00:00'),
               (22, 1, 22, '2024-01-01 00:00:00', '2024-01-01 00:00:00');

           INSERT INTO mods
               (id, name, description, author, link, pretty_name, side, modtype)
           VALUES
               (20, 'ci-client-only', 'Synthetic client package', 'CI',
                'https://example.invalid/client', 'CI Client Only',
                'CLIENT', 'MOD'),
               (21, 'ci-server-only', 'Synthetic server package', 'CI',
                'https://example.invalid/server', 'CI Server Only',
                'SERVER', 'MOD'),
               (22, 'ci-optional-both', 'Synthetic optional package', 'CI',
                'https://example.invalid/optional', 'CI Optional Both',
                'BOTH', 'MOD'),
               (23, 'ci-optional-server', 'Synthetic optional server package',
                'CI', 'https://example.invalid/optional-server',
                'CI Optional Server', 'SERVER', 'MOD'),
               (24, 'ci-removed-server', 'Synthetic removed server package',
                'CI', 'https://example.invalid/removed', 'CI Removed Server',
                'SERVER', 'MOD');

           INSERT INTO modversions
               (id, mod_id, version, mcversion, md5, filesize)
           VALUES
               (20, 20, '1.0', '1.20.1',
                '20202020202020202020202020202020', 2020),
               (21, 21, '1.0', '1.20.1',
                '21212121212121212121212121212121', 2121),
               (22, 22, '1.0', '1.20.1',
                '22222222222222222222222222222222', 2222),
               (23, 23, '1.0', '1.20.1',
                '23232323232323232323232323232323', 2323),
               (24, 1, '0.9', '1.20.1',
                '24242424242424242424242424242424', 2424),
               (25, 24, '1.0', '1.20.1',
                '25252525252525252525252525252525', 2525);

           INSERT INTO build_modversion
               (id, modversion_id, build_id, optional)
           VALUES
               (20, 1, 20, 0),
               (21, 1, 21, 0),
               (22, 1, 22, 0),
               (23, 1, 23, 0),
               (24, 1, 24, 0),
               (30, 20, 22, 0),
               (31, 21, 22, 0),
               (32, 22, 22, 1),
               (33, 23, 22, 1),
               (34, 24, 25, 0),
               (35, 25, 25, 0);""",
    )


def seed_mcinstance_scenario(database_container: str) -> None:
    modtype_definition = mysql(
        database_container,
        "SELECT COLUMN_TYPE FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA = '{DATABASE}' AND TABLE_NAME = 'mods' "
        "AND COLUMN_NAME = 'modtype';",
    ).upper()
    bootstrap_modtype = (
        "BOOTSTRAP" if "'BOOTSTRAP'" in modtype_definition else "MCIL"
    )
    mysql(
        database_container,
        f"""INSERT INTO mods
               (id, name, description, author, link, pretty_name, side, modtype)
           VALUES
               (26, 'ci-mcil-loader', 'Synthetic MCInstanceLoader package',
                'CI', 'https://example.invalid/bootstrap', 'CI Bootstrap Loader',
                'BOTH', '{bootstrap_modtype}'),
               (27, 'ci-mcil-optional', 'Synthetic optional MCIL export mod',
                'CI', 'https://example.invalid/mcil-optional',
                'CI MCIL Optional', 'CLIENT', 'MOD'),
               (28, 'ci-forge-launcher', 'Synthetic modpack.jar package',
                'CI', 'https://example.invalid/forge', 'CI Forge Launcher',
                'BOTH', 'LAUNCHER');
           INSERT INTO modversions
               (id, mod_id, version, mcversion, md5, jarmd5, filesize)
           VALUES
               (26, 26, '1.7.10-2.7', '1.7.10',
                '26262626262626262626262626262626',
                '26262626262626262626262626262626', 2626),
               (27, 27, '1.7.10-1.0', '1.7.10',
                '27272727272727272727272727272727',
                '27272727272727272727272727272727', 2727),
               (28, 28, '1.7.10-10.13.4.1614', '1.7.10',
                '28282828282828282828282828282828', '0', 2828);
           INSERT INTO build_modversion
               (id, modversion_id, build_id, optional)
           VALUES
               (36, 26, 20, 0),
               (37, 27, 20, 1);
           UPDATE modversions
           SET jarmd5 = '11111111111111111111111111111111', mcversion = NULL
           WHERE id = 1;
           UPDATE builds
           SET minecraft = '1.7.10', forge = '10.13.4.1614'
           WHERE id = 20;
           INSERT INTO user_modpack
               (id, user_id, modpack_id, created_at, updated_at)
           VALUES
               (20, 1, 20, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);""",
    )


def request_json_with_status(endpoint: str) -> tuple[int, dict]:
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
        raise ValueError(f"Refusing non-loopback test endpoint: {endpoint}")
    try:
        with urllib.request.urlopen(endpoint, timeout=5) as response:  # nosec B310
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def request_json(endpoint: str) -> dict:
    status, payload = request_json_with_status(endpoint)
    if status != 200:
        raise AssertionError(f"{endpoint} returned HTTP {status}: {payload}")
    return payload


def write_request_json(
    endpoint: str,
    token: str,
    *,
    method: str = "POST",
    payload: dict | None = None,
) -> tuple[int, dict]:
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
        raise ValueError(f"Refusing non-loopback test endpoint: {endpoint}")
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=data,
        method=method,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # nosec B310
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


def seed_write_api_token(database_container: str) -> str:
    secret = "ci-write-token-not-a-secret"
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    tokenable_type = r"App\Models\User".encode("utf-8").hex()
    mysql(
        database_container,
        "DELETE FROM personal_access_tokens WHERE name = 'CI write test'; "
        "INSERT INTO personal_access_tokens "
        "(tokenable_type, tokenable_id, name, token, abilities, created_at, updated_at) "
        f"VALUES (UNHEX('{tokenable_type}'), 1, 'CI write test', '{digest}', "
        "'[\"*\"]', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);",
    )
    token_id = mysql(
        database_container,
        "SELECT id FROM personal_access_tokens WHERE name = 'CI write test';",
    )
    return f"{token_id}|{secret}"


def start_maven_fixture(image: str, network: str, container: str) -> None:
    version = "1.21.1-2.0"
    relative = Path("example", "group", "ci-maven-mod")
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        artifact_root = root / relative
        version_root = artifact_root / version
        version_root.mkdir(parents=True)
        metadata = (
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
            "<metadata><groupId>example.group</groupId>"
            "<artifactId>ci-maven-mod</artifactId><versioning><versions>"
            f"<version>{version}</version>"
            "</versions></versioning></metadata>\n"
        )
        (artifact_root / "maven-metadata.xml").write_text(
            metadata, encoding="utf-8"
        )
        jar_data = io.BytesIO()
        with zipfile.ZipFile(jar_data, "w") as archive:
            archive.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
        jar_name = f"ci-maven-mod-{version}.jar"
        jar_path = version_root / jar_name
        jar_path.write_bytes(jar_data.getvalue())
        (version_root / f"{jar_name}.sha256").write_text(
            hashlib.sha256(jar_data.getvalue()).hexdigest(), encoding="ascii"
        )

        docker(
            "create",
            "--name",
            container,
            "--network",
            network,
            "--network-alias",
            "maven",
            image,
            "python",
            "-m",
            "http.server",
            "8000",
            "--directory",
            "/repo",
            capture_output=True,
        )
        docker("cp", f"{root}{os.sep}.", f"{container}:/repo")
    docker("start", container, capture_output=True)


def exercise_maven_write_api(
    base_url: str,
    database_container: str,
    application_container: str,
    token: str,
) -> None:
    status, repository = write_request_json(
        f"{base_url}/api/integration/maven/repository",
        token,
        payload={"name": "CI Maven", "base_url": "http://maven:8000/"},
    )
    if status != 201:
        raise AssertionError(
            f"Write API did not create a Maven repository: {status} {repository}"
        )

    status, artifact = write_request_json(
        f"{base_url}/api/integration/maven/artifact",
        token,
        payload={
            "repository_id": repository["repository"]["id"],
            "group_id": "example.group",
            "artifact_id": "ci-maven-mod",
            "slug": "ci-maven-mod",
            "title": "CI Maven Mod",
            "author": "CI",
            "side": "BOTH",
            "version_mode": "EMBEDDED",
            "version_pattern": "{minecraft}-{version}",
            "redistribution_confirmed": True,
        },
    )
    if status != 201 or len(artifact.get("versions", [])) != 1:
        raise AssertionError(
            f"Write API did not import Maven metadata: {status} {artifact}"
        )
    artifact_id = artifact["artifact"]["id"]
    mod_slug = artifact["artifact"]["slug"]
    if mod_slug != "ci-maven-mod-ci-maven":
        raise AssertionError(f"Unexpected Maven mod/repository slug: {mod_slug}")
    mapping_id = artifact["versions"][0]["id"]
    integration_id = artifact["versions"][0]["integration_version_id"]
    status, mapped = write_request_json(
        f"{base_url}/api/integration/maven/artifact/{artifact_id}/"
        f"version/{mapping_id}",
        token,
        method="PUT",
        payload={
            "minecraft": "1.21.1",
            "mod_version": "2.0-ci",
            "modloader": None,
            "enabled": True,
        },
    )
    if status != 200 or mapped["version"]["mapping_source"] != "MANUAL":
        raise AssertionError(
            f"Write API did not save a Maven mapping: {status} {mapped}"
        )
    refresh_status, refreshed = write_request_json(
        f"{base_url}/api/integration/maven/artifact/{artifact_id}/refresh",
        token,
    )
    if refresh_status != 200:
        raise AssertionError(
            f"Write API did not refresh Maven metadata: "
            f"{refresh_status} {refreshed}"
        )
    status, catalog = write_request_json(
        f"{base_url}/api/integration/maven/artifact/{artifact_id}",
        token,
        method="GET",
    )
    refreshed_mapping = catalog.get("versions", [{}])[0]
    if (
        status != 200
        or refreshed_mapping.get("mapping_source") != "MANUAL"
        or refreshed_mapping.get("mod_version") != "2.0-ci"
    ):
        raise AssertionError(
            f"Maven refresh did not preserve a manual mapping: {status} {catalog}"
        )

    status, versions = write_request_json(
        f"{base_url}/api/modpack/ci-example-pack/1.0/mod/"
        f"{mod_slug}/integration-versions",
        token,
        method="GET",
    )
    if status != 200 or versions["versions"][0]["id"] != integration_id:
        raise AssertionError(
            f"Maven version was not available to the build: {status} {versions}"
        )

    status, membership = write_request_json(
        f"{base_url}/api/modpack/ci-example-pack/1.0/mod",
        token,
        payload={
            "mod_slug": mod_slug,
            "integration_version_id": integration_id,
        },
    )
    if status != 201:
        raise AssertionError(
            f"Maven version was not materialized into the build: "
            f"{status} {membership}"
        )
    stored = mysql(
        database_container,
        "SELECT mods.integration_provider, modversions.version, "
        "modversions.mcversion, LENGTH(modversions.md5), "
        "LENGTH(modversions.jarmd5) FROM mods "
        "INNER JOIN modversions ON modversions.mod_id = mods.id "
        f"WHERE mods.name = '{mod_slug}';",
    )
    if stored != "MAVEN\t1.21.1-2.0-ci\t1.21.1\t32\t32":
        raise AssertionError(f"Unexpected stored Maven version: {stored}")
    for filename in (
        f"/app/mods/{mod_slug}/{mod_slug}-1.21.1-2.0-ci.jar",
        f"/app/mods/{mod_slug}/{mod_slug}-1.21.1-2.0-ci.zip",
    ):
        result = subprocess.run(
            ["docker", "exec", application_container, "test", "-f", filename],
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(f"Maven materialization did not create {filename}")


def exercise_write_api(base_url: str, database_container: str, token: str) -> None:
    capabilities = request_json(f"{base_url}/api/").get("capabilities", {})
    if capabilities.get("write_api") is not True:
        raise AssertionError("WRITE_API did not register in the production image")

    status, _ = write_request_json(
        f"{base_url}/api/mod",
        "1|invalid",
        payload={"name": "unauthorized", "pretty_name": "Unauthorized"},
    )
    if status != 401:
        raise AssertionError(f"Invalid write token returned HTTP {status}")

    status, created_mod = write_request_json(
        f"{base_url}/api/mod",
        token,
        payload={
            "name": "ci-write-mod",
            "pretty_name": "CI Write Mod",
            "author": "CI",
            "side": "SERVER",
            "modtype": "CONFIG",
            "notes": "private write API note",
            "dependencies": [1],
        },
    )
    if status != 201 or created_mod.get("side") != "SERVER":
        raise AssertionError(f"Write API did not create a mod: {status} {created_mod}")

    status, created_version = write_request_json(
        f"{base_url}/api/mod/ci-write-mod/version",
        token,
        payload={
            "version": "1.21.1-1.0",
            "minecraft": "ignored",
            "mcversion": "1.21.1",
            "modloader": None,
            "md5": "abababababababababababababababab",
            "filesize": 2048,
        },
    )
    if status != 201 or created_version.get("mcversion") != "1.21.1":
        raise AssertionError(
            f"Write API did not create a mod version: {status} {created_version}"
        )

    status, membership = write_request_json(
        f"{base_url}/api/modpack/ci-example-pack/1.0/mod",
        token,
        payload={
            "mod_slug": "ci-write-mod",
            "mod_version": "1.21.1-1.0",
            "optional": True,
        },
    )
    if status != 201:
        raise AssertionError(
            f"Write API did not add a mod to a build: {status} {membership}"
        )
    optional = mysql(
        database_container,
        "SELECT build_modversion.optional FROM build_modversion "
        "INNER JOIN modversions ON build_modversion.modversion_id = modversions.id "
        "INNER JOIN mods ON modversions.mod_id = mods.id "
        "WHERE build_modversion.build_id = 1 AND mods.name = 'ci-write-mod';",
    )
    if optional != "1":
        raise AssertionError("Write API did not preserve optional build status")

    status, created_pack = write_request_json(
        f"{base_url}/api/modpack",
        token,
        payload={
            "name": "CI Write Pack",
            "slug": "ci-write-pack",
            "private": True,
            "enable_optionals": True,
            "enable_server": True,
        },
    )
    if status != 201 or created_pack.get("enable_server") is not True:
        raise AssertionError(
            f"Write API did not create a modpack: {status} {created_pack}"
        )
    status, created_build = write_request_json(
        f"{base_url}/api/modpack/ci-write-pack/build",
        token,
        payload={
            "version": "1.0",
            "minecraft": "1.21.1",
            "modloader": "FABRIC",
            "min_java": "21.0.2",
            "java_runtime": "java-runtime-delta",
            "is_published": True,
        },
    )
    if (
        status != 201
        or created_build.get("modloader") != "FABRIC"
        or created_build.get("min_java") != "21.0.2"
        or created_build.get("java_runtime") != "java-runtime-delta"
    ):
        raise AssertionError(
            f"Write API did not create a build: {status} {created_build}"
        )

    anonymous_status, _ = request_json_with_status(
        f"{base_url}/api/modpack/ci-write-pack/1.0"
    )
    if anonymous_status != 404:
        raise AssertionError(
            f"Private write-API modpack was public: HTTP {anonymous_status}"
        )
    status, private_build = write_request_json(
        f"{base_url}/api/modpack/ci-write-pack/1.0",
        token,
        method="GET",
    )
    if status != 200 or private_build.get("minecraft") != "1.21.1":
        raise AssertionError(
            "Bearer token could not read its assigned private build: "
            f"{status} {private_build}"
        )
    permission_scope = mysql(
        database_container,
        "SELECT modpacks FROM user_permissions WHERE user_id = 1;",
    ).split(",")
    if str(created_pack["id"]) not in permission_scope:
        raise AssertionError("Write API did not update Technic modpack permissions")

    status, created_client = write_request_json(
        f"{base_url}/api/client",
        token,
        payload={"name": "CI Write Client", "uuid": "ci-write-client"},
    )
    if status != 201:
        raise AssertionError(
            f"Write API did not create a client: {status} {created_client}"
        )
    status, updated_client = write_request_json(
        f"{base_url}/api/client/ci-write-client",
        token,
        method="PUT",
        payload={"modpacks": [created_pack["id"]]},
    )
    if status != 200:
        raise AssertionError(
            f"Write API did not update a client: {status} {updated_client}"
        )

    status, tokens = write_request_json(
        f"{base_url}/api/token", token, method="GET"
    )
    if status != 200 or len(tokens.get("tokens", [])) != 1:
        raise AssertionError(f"Write API did not list user tokens: {status} {tokens}")

    status, new_token = write_request_json(
        f"{base_url}/api/token",
        token,
        payload={"name": "CI child token"},
    )
    if status != 201 or "plaintext" not in new_token.get("token", {}):
        raise AssertionError(f"Write API did not create a token: {status} {new_token}")
    child_id = new_token["token"]["id"]
    status, revoked = write_request_json(
        f"{base_url}/api/token/{child_id}", token, method="DELETE"
    )
    if status != 200:
        raise AssertionError(f"Write API did not revoke a token: {status} {revoked}")


def wait_for_application(container: str, endpoint: str) -> None:
    deadline = time.monotonic() + 60
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            payload = request_json(endpoint)
            if payload.get("api") == "solder.py":
                return
            last_error = AssertionError(f"Unexpected API response: {payload}")
        except (OSError, ValueError, urllib.error.URLError) as error:
            last_error = error
        if not container_running(container):
            raise RuntimeError(f"solder.py exited before becoming ready: {last_error}")
        time.sleep(1)
    raise TimeoutError(f"solder.py did not become ready: {last_error}")


def exercise_database_api(base_url: str) -> None:
    key = request_json(f"{base_url}/api/verify/ci-api-key-not-a-secret")
    if key.get("valid") != "Key validated." or key.get("name") != "CI key":
        raise AssertionError(f"API key fixture was not readable: {key}")

    client = urllib.parse.quote("ci-client-id-not-a-secret")
    modpacks = request_json(f"{base_url}/api/modpack?cid={client}")
    expected_client_packs = {
        "ci-example-pack": "CI Example Pack",
        "ci-private-pack": "CI Private Pack",
        "ci-api-variants": "CI API Variants",
    }
    if modpacks.get("modpacks") != expected_client_packs:
        raise AssertionError(f"Modpack fixture was not readable: {modpacks}")

    public_modpacks = request_json(f"{base_url}/api/modpack")
    if public_modpacks.get("modpacks") != {
        "ci-example-pack": "CI Example Pack",
        "ci-api-variants": "CI API Variants",
    }:
        raise AssertionError(f"Public modpack visibility was incorrect: {public_modpacks}")

    modpack = request_json(f"{base_url}/api/modpack/ci-example-pack?cid={client}")
    if modpack.get("builds") != ["1.0"]:
        raise AssertionError(f"Build fixture was not readable: {modpack}")

    manifest = request_json(
        f"{base_url}/api/modpack/ci-example-pack/1.0?cid={client}"
    )
    if manifest.get("minecraft") != "1.21.1":
        raise AssertionError(f"Build metadata was not readable: {manifest}")
    if [mod.get("name") for mod in manifest.get("mods", [])] != ["ci-example-mod"]:
        raise AssertionError(f"Mod-version fixture was not readable: {manifest}")

    hidden = request_json(f"{base_url}/api/modpack/ci-hidden-pack")
    if hidden.get("name") != "ci-hidden-pack":
        raise AssertionError(f"Hidden public pack was not reachable by slug: {hidden}")

    private_url = f"{base_url}/api/modpack/ci-private-pack"
    private_status, _ = request_json_with_status(private_url)
    if private_status != 404:
        raise AssertionError("Private modpack was exposed without credentials")
    private_pack = request_json(f"{private_url}?cid={client}")
    if private_pack.get("name") != "ci-private-pack":
        raise AssertionError(f"CID did not grant private-pack access: {private_pack}")
    private_by_key = request_json(
        f"{private_url}?k=ci-api-key-not-a-secret"
    )
    if private_by_key.get("name") != "ci-private-pack":
        raise AssertionError(f"API key did not grant private-pack access: {private_by_key}")

    variant_url = f"{base_url}/api/modpack/ci-api-variants"
    private_build_status, _ = request_json_with_status(
        f"{variant_url}/private"
    )
    if private_build_status != 404:
        raise AssertionError("Private build was exposed without credentials")
    request_json(f"{variant_url}/private?cid={client}")
    request_json(f"{variant_url}/private?k=ci-api-key-not-a-secret")

    unpublished_status, _ = request_json_with_status(
        f"{variant_url}/unpublished?k=ci-api-key-not-a-secret"
    )
    if unpublished_status != 404:
        raise AssertionError("Unpublished build was exposed through an API key")

    hyphenated = request_json(f"{variant_url}/1.20.1-beta-2")
    if hyphenated.get("id") != 22 or hyphenated.get("forge") != "47.3.0":
        raise AssertionError(f"Hyphenated build was parsed incorrectly: {hyphenated}")
    example_mod = next(
        (
            mod
            for mod in hyphenated.get("mods", [])
            if mod.get("name") == "ci-example-mod"
        ),
        {},
    )
    if example_mod.get("filesize") != 1024:
        raise AssertionError(f"Build manifest parity fields are missing: {hyphenated}")

    optional = request_json(f"{variant_url}/1.20.1-beta-2-optional")
    if optional.get("id") != 22:
        raise AssertionError(f"Optional build suffix was parsed incorrectly: {optional}")
    server = request_json(f"{variant_url}/1.20.1-beta-2-server")
    if server.get("id") != 22:
        raise AssertionError(f"Server build suffix was parsed incorrectly: {server}")

    server_query = request_json(
        f"{variant_url}/recommended?target=server&optional=true"
    )
    if server_query.get("version") != "1.20.1-beta-2":
        raise AssertionError(f"Recommended channel was not resolved: {server_query}")
    if server_query.get("target") != "server" or not server_query.get("optional"):
        raise AssertionError(f"Server manifest metadata was missing: {server_query}")
    if len(server_query.get("manifest_hash", "")) != 64:
        raise AssertionError(f"Server manifest hash was missing: {server_query}")
    expected_server_mods = {
        "ci-example-mod": ("BOTH", False),
        "ci-optional-both": ("BOTH", True),
        "ci-optional-server": ("SERVER", True),
        "ci-server-only": ("SERVER", False),
    }
    actual_server_mods = {
        mod["name"]: (mod.get("side"), mod.get("optional"))
        for mod in server_query.get("mods", [])
    }
    if actual_server_mods != expected_server_mods:
        raise AssertionError(
            f"Server/optional filtering was incorrect: {server_query}"
        )
    server_only = next(
        mod
        for mod in server_query["mods"]
        if mod["name"] == "ci-server-only"
    )
    if server_only.get("modtype") != "MOD":
        raise AssertionError(f"Mod type was missing from manifest: {server_only}")
    if [dependency["name"] for dependency in server_only.get("dependencies", [])] != [
        "ci-example-mod"
    ]:
        raise AssertionError(f"Dependencies were missing from manifest: {server_only}")

    comparison = request_json(
        f"{variant_url}/latest?target=server&from=previous"
    ).get("changes", {})
    if [mod["name"] for mod in comparison.get("added", [])] != [
        "ci-server-only"
    ]:
        raise AssertionError(f"Server additions were incorrect: {comparison}")
    if [
        change["to"]["name"] for change in comparison.get("updated", [])
    ] != ["ci-example-mod"]:
        raise AssertionError(f"Server updates were incorrect: {comparison}")
    if [mod["name"] for mod in comparison.get("removed", [])] != [
        "ci-removed-server"
    ]:
        raise AssertionError(f"Server removals were incorrect: {comparison}")

    mods = request_json(f"{base_url}/api/mod")
    if mods.get("mods", {}).get("ci-example-mod") != "CI Example Mod":
        raise AssertionError(f"Mod catalogue was not readable: {mods}")

    private_note_mod = request_json(f"{base_url}/api/mod/ci-example-mod")
    if "notes" in private_note_mod:
        raise AssertionError("Private management notes were exposed by the read API")

    server_mod = request_json(f"{base_url}/api/mod/ci-server-only")
    if server_mod.get("side") != "SERVER" or server_mod.get("modtype") != "MOD":
        raise AssertionError(f"Mod extension metadata was missing: {server_mod}")
    if [dependency["id"] for dependency in server_mod.get("dependencies", [])] != [1]:
        raise AssertionError(f"Mod dependencies were missing: {server_mod}")

    optional_server_version = request_json(
        f"{base_url}/api/mod/ci-optional-server/1.0"
    )
    optional_memberships = optional_server_version.get("builds", [])
    if len(optional_memberships) != 1 or not optional_memberships[0].get("optional"):
        raise AssertionError(
            "Build-specific optional status was missing: "
            f"{optional_server_version}"
        )

    mod_version = request_json(f"{base_url}/api/mod/ci-example-mod/1.0")
    expected_url = "https://example.invalid/mods/ci-example-mod/ci-example-mod-1.0.zip"
    if mod_version.get("url") != expected_url:
        raise AssertionError(f"Mod-version URL was incorrect: {mod_version}")
    public_build_ids = [build["id"] for build in mod_version.get("builds", [])]
    if public_build_ids != [1, 20, 22]:
        raise AssertionError(
            f"Mod-version public build memberships were incorrect: {mod_version}"
        )

    mod_version_with_cid = request_json(
        f"{base_url}/api/mod/ci-example-mod/1.0?cid={client}"
    )
    client_build_ids = [
        build["id"] for build in mod_version_with_cid.get("builds", [])
    ]
    if client_build_ids != [1, 20, 21, 22, 23]:
        raise AssertionError(
            "Mod-version CID build memberships were incorrect: "
            f"{mod_version_with_cid}"
        )


def stage_legacy_mcil_package(
    application_container: str, database_container: str
) -> str:
    jar_data = b"legacy Technic Solder JAR"
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("mods/upstream-name.jar", jar_data)
        archive.writestr("config/legacy.cfg", b"enabled=true")
    package_data = package.getvalue()
    package_md5 = hashlib.md5(
        package_data, usedforsecurity=False
    ).hexdigest()
    jar_md5 = hashlib.md5(jar_data, usedforsecurity=False).hexdigest()

    destination = "/app/mods/ci-parent-mod"
    docker("exec", application_container, "mkdir", "-p", destination)
    with tempfile.TemporaryDirectory() as directory:
        package_path = Path(directory, "ci-parent-mod-1.21.1-1.0.zip")
        package_path.write_bytes(package_data)
        docker(
            "cp",
            str(package_path),
            f"{application_container}:{destination}/{package_path.name}",
        )
    mysql(
        database_container,
        "UPDATE modversions "
        f"SET md5 = '{package_md5}', jarmd5 = NULL, filesize = {len(package_data)} "
        "WHERE id = 3;",
    )
    return jar_md5


def exercise_synthetic_user_login(
    base_url: str,
    database_container: str,
    application_container: str,
    expected_legacy_jar_md5: str,
) -> None:
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, file_pointer, code, message, headers, url):
            return None

    cookies = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cookies), NoRedirect
    )
    with opener.open(f"{base_url}/login", timeout=5) as response:
        login_page = response.read()
    token_match = re.search(
        rb'name="_csrf_token" value="([^"]+)"', login_page
    )
    if token_match is None:
        raise AssertionError("The login form did not include a CSRF token")
    csrf_token = token_match.group(1).decode("ascii")

    def form_data(values: dict[str, str]) -> bytes:
        submitted = dict(values)
        submitted["_csrf_token"] = csrf_token
        return urllib.parse.urlencode(submitted).encode()

    request = urllib.request.Request(
        f"{base_url}/login",
        data=form_data(
            {
                "username": "ci-user",
                "password": "ci-password",
            }
        ),
        method="POST",
    )
    try:
        opener.open(request, timeout=5)  # nosec B310
    except urllib.error.HTTPError as error:
        if error.code != 302 or error.headers.get("Location") != "/":
            raise AssertionError(
                f"Synthetic user login returned an unexpected response: {error}"
            ) from error
    else:
        raise AssertionError("Synthetic user login did not redirect after success")

    upgraded_password = mysql(
        database_container,
        "SELECT password LIKE '$argon2id$%' FROM users WHERE username = 'ci-user';",
    )
    if upgraded_password != "1":
        raise AssertionError(
            "A successful legacy login did not upgrade the password to Argon2id"
        )

    with opener.open(f"{base_url}/", timeout=5) as response:
        dashboard_page = response.read()
        if response.status != 200:
            raise AssertionError("The management dashboard did not load")
        for expected_text in (
            b"Needs attention",
            b"Recent changes",
            b"Repository health",
        ):
            if expected_text not in dashboard_page:
                raise AssertionError(
                    f"The management dashboard is missing {expected_text!r}"
                )

    with opener.open(f"{base_url}/integrations", timeout=5) as response:
        integrations_page = response.read()
        if (
            response.status != 200
            or b"Export configured integrations" not in integrations_page
        ):
            raise AssertionError(
                "The integration manifest export action was not shown"
            )

    with opener.open(f"{base_url}/mainsettings", timeout=5) as response:
        settings_page = response.read()
        if response.status != 200:
            raise AssertionError("The environment settings page did not load")
        distribution_position = settings_page.find(
            b'id="distribution_settings"'
        )
        hashing_position = settings_page.find(b'id="manual_md5_hashing"')
        environment_position = settings_page.find(b"DEBUG ENV")
        if (
            distribution_position < 0
            or hashing_position < 0
            or environment_position < 0
            or hashing_position >= environment_position
        ):
            raise AssertionError(
                "Manual MD5 hashing was not above the environment list"
            )

    with opener.open(
        f"{base_url}/integrations/manifest/export", timeout=5
    ) as response:
        if response.status != 200:
            raise AssertionError(
                "The integration manifest export did not return HTTP 200"
            )
        disposition = response.headers.get("Content-Disposition", "")
        if "solder.py-integration-manifest.json" not in disposition:
            raise AssertionError(
                f"The integration export filename was incorrect: {disposition}"
            )
        integration_manifest = json.loads(response.read())
    if (
        integration_manifest.get("format") != "solder.py-integration-manifest"
        or integration_manifest.get("version") != 1
    ):
        raise AssertionError(
            f"The integration export header was invalid: {integration_manifest}"
        )
    maven_entries = [
        entry
        for entry in integration_manifest.get("mods", [])
        if entry.get("provider") == "maven"
    ]
    if (
        len(maven_entries) != 1
        or maven_entries[0].get("artifact_id") != "ci-maven-mod"
        or maven_entries[0].get("repository", {}).get("name") != "CI Maven"
    ):
        raise AssertionError(
            f"The Maven integration was not portable: {integration_manifest}"
        )

    with opener.open(f"{base_url}/modversion/3/manage/3", timeout=5) as response:
        version_page = response.read()
        if (
            response.status != 200
            or b">Create JAR</button>" not in version_page
            or b">Verify ZIP</button>" not in version_page
        ):
            raise AssertionError("The artifact verification actions were not shown")

    mcil_jar_request = urllib.request.Request(
        f"{base_url}/modversion/3/manage/3",
        data=form_data({"jar_action_submit": "1"}),
        method="POST",
    )
    try:
        opener.open(mcil_jar_request, timeout=10)
    except urllib.error.HTTPError as error:
        if (
            error.code != 302
            or error.headers.get("Location") != "/modversion/3/manage/3"
        ):
            raise AssertionError(
                f"Creating a legacy MCIL JAR returned an unexpected response: {error}"
            ) from error
    else:
        raise AssertionError("Creating a legacy MCIL JAR did not redirect")

    stored_jar_md5 = mysql(
        database_container,
        "SELECT jarmd5 FROM modversions WHERE id = 3;",
    )
    if stored_jar_md5 != expected_legacy_jar_md5:
        raise AssertionError(
            f"The converted MCIL JAR MD5 was not stored: {stored_jar_md5}"
        )
    stored_jar_size = mysql(
        database_container,
        "SELECT jarfilesize FROM modversions WHERE id = 3;",
    )
    if int(stored_jar_size) != len(b"legacy Technic Solder JAR"):
        raise AssertionError(
            f"The converted MCIL JAR size was not stored: {stored_jar_size}"
        )
    artifact_hash = subprocess.check_output(
        [
            "docker",
            "exec",
            application_container,
            "md5sum",
            "/app/mods/ci-parent-mod/ci-parent-mod-1.21.1-1.0.jar",
        ],
        text=True,
    ).split()[0]
    if artifact_hash != expected_legacy_jar_md5:
        raise AssertionError(f"The converted MCIL JAR was incorrect: {artifact_hash}")

    with opener.open(f"{base_url}/modversion/3/manage/3", timeout=5) as response:
        if b">Verify JAR</button>" not in response.read():
            raise AssertionError("The converted version did not show the JAR verification action")

    dependency_request = urllib.request.Request(
        f"{base_url}/modversion/3",
        data=form_data(
            {"dependency_mod_id": "2", "adddependency_submit": "1"}
        ),
        method="POST",
    )
    try:
        opener.open(dependency_request, timeout=5)
    except urllib.error.HTTPError as error:
        if error.code != 302 or error.headers.get("Location") != "/modversion/3":
            raise AssertionError(
                f"Adding a required dependency returned an unexpected response: {error}"
            ) from error
    else:
        raise AssertionError("Adding a required dependency did not redirect")

    with opener.open(f"{base_url}/modversion/3", timeout=5) as response:
        dependency_page = response.read()
        if response.status != 200 or b"CI Required Library" not in dependency_page:
            raise AssertionError("The configured dependency was not shown on the mod page")

    add_parent_request = urllib.request.Request(
        f"{base_url}/modpackbuild/1",
        data=form_data(
            {
                "modversion": "3",
                "modnames": "3",
                "add_mod_submit": "1",
            }
        ),
        method="POST",
    )
    try:
        opener.open(add_parent_request, timeout=5)
    except urllib.error.HTTPError as error:
        if error.code != 302 or error.headers.get("Location") != "/modpackbuild/1":
            raise AssertionError(
                f"Adding a mod with dependencies returned an unexpected response: {error}"
            ) from error
    else:
        raise AssertionError("Adding a mod with dependencies did not redirect")

    selected_build_mods = mysql(
        database_container,
        """SELECT GROUP_CONCAT(
                      CONCAT(modversions.mod_id, ':', modversions.id, ':',
                             build_modversion.optional)
                      ORDER BY modversions.mod_id SEPARATOR ',')
           FROM build_modversion
           INNER JOIN modversions
               ON build_modversion.modversion_id = modversions.id
           WHERE build_modversion.build_id = 1
             AND modversions.mod_id IN (1, 2, 3);""",
    )
    if selected_build_mods != "1:1:0,2:4:0,3:3:0":
        raise AssertionError(
            "Management did not add the parent and newest matching required "
            f"dependency to the build: {selected_build_mods}"
        )

    update_all_request = urllib.request.Request(
        f"{base_url}/modpackbuild/1",
        data=form_data(
            {"update_all_mods_submit": "1"}
        ),
        method="POST",
    )
    try:
        opener.open(update_all_request, timeout=5)
    except urllib.error.HTTPError as error:
        if error.code != 302 or error.headers.get("Location") != "/modpackbuild/1":
            raise AssertionError(
                f"Updating all build mods returned an unexpected response: {error}"
            ) from error
    else:
        raise AssertionError("Updating all build mods did not redirect")

    updated_dependency = mysql(
        database_container,
        """SELECT build_modversion.modversion_id
           FROM build_modversion
           INNER JOIN modversions
               ON build_modversion.modversion_id = modversions.id
           WHERE build_modversion.build_id = 1
             AND modversions.mod_id = 2;""",
    )
    if updated_dependency != "5":
        raise AssertionError(
            "Update all did not select the newest compatible dependency "
            f"version: {updated_dependency}"
        )

    with opener.open(f"{base_url}/modpackbuild/1", timeout=5) as response:
        build_editor = response.read()
        if (
            response.status != 200
            or b"CI Example Mod" not in build_editor
            or b">Update all mods</button>" not in build_editor
            or b">Export</a>" not in build_editor
            or b"export=1" not in build_editor
            or b'id="export_modpack_modal"' in build_editor
            or b'id="build_version_fields"' not in build_editor
            or b'name="java_runtime"' not in build_editor
            or b">Advanced</span>" in build_editor
        ):
            raise AssertionError("The authenticated build editor did not render")

    with opener.open(f"{base_url}/modpackbuild/1?export=1", timeout=5) as response:
        export_editor = response.read()
        if (
            response.status != 200
            or b'id="export_modpack_modal"' not in export_editor
            or b">Export CSV</button>" not in export_editor
        ):
            raise AssertionError("The build export window did not render")

    build_settings_request = urllib.request.Request(
        f"{base_url}/modpackbuild/1",
        data=form_data(
            {
                "form-submit": "1",
                "version": "1.0",
                "mcversion": "1.21.1",
                "min_java": "1.8.0_51",
                "java_runtime": "java-runtime-delta",
                "memory": "4096",
                "forge": "",
                "modloader": "",
                "publish": "1",
            }
        ),
        method="POST",
    )
    try:
        opener.open(build_settings_request, timeout=5)
    except urllib.error.HTTPError as error:
        if error.code != 302 or error.headers.get("Location") != "/modpackbuild/1":
            raise AssertionError(
                "Updating the minimum Java version returned an unexpected "
                f"response: {error}"
            ) from error
    else:
        raise AssertionError("Updating the minimum Java version did not redirect")

    stored_java = mysql(
        database_container,
        "SELECT min_java, java_runtime FROM builds WHERE id = 1;",
    )
    if stored_java != "1.8.0_51\tjava-runtime-delta":
        raise AssertionError(
            "The Java requirement/runtime override was not stored: "
            f"{stored_java}"
        )
    client = urllib.parse.quote("ci-client-id-not-a-secret")
    java_manifest = request_json(
        f"{base_url}/api/modpack/ci-example-pack/1.0?cid={client}"
    )
    if java_manifest.get("java") != "1.8.0_51":
        raise AssertionError(
            f"The API changed the minimum Java version: {java_manifest}"
        )
    if java_manifest.get("java_runtime") != "java-runtime-delta":
        raise AssertionError(
            f"The API omitted the Mojang Java runtime: {java_manifest}"
        )

    add_launcher_request = urllib.request.Request(
        f"{base_url}/modpackbuild/20",
        data=form_data(
            {
                "modversion": "28",
                "modnames": "28",
                "add_mod_submit": "1",
            }
        ),
        method="POST",
    )
    try:
        opener.open(add_launcher_request, timeout=5)
    except urllib.error.HTTPError as error:
        if error.code != 302 or error.headers.get("Location") != "/modpackbuild/20":
            raise AssertionError(
                f"Adding a launcher package returned an unexpected response: {error}"
            ) from error
    else:
        raise AssertionError("Adding a launcher package did not redirect")

    stored_modloader = mysql(
        database_container,
        "SELECT forge, modloader FROM builds WHERE id = 20;",
    )
    if stored_modloader != "1.7.10-10.13.4.1614\tFORGE":
        raise AssertionError(
            "The launcher package version was not synchronized to the build: "
            f"{stored_modloader}"
        )

    with opener.open(f"{base_url}/modpackbuild/20/mcinstance", timeout=5) as response:
        if response.status != 200:
            raise AssertionError("The MCInstance export did not return HTTP 200")
        disposition = response.headers.get("Content-Disposition", "")
        if "ci-hidden-pack-1.0.mcinstance" not in disposition:
            raise AssertionError(
                f"The MCInstance export filename was incorrect: {disposition}"
            )
        exported = io.BytesIO(response.read())

    with zipfile.ZipFile(exported) as archive:
        resources = archive.read("resources.packconfig").decode("utf-8")
        optionals = archive.read("optionals.packconfig").decode("utf-8")
        metadata = archive.read("metadata.packconfig").decode("utf-8")
        if "ci-mcil-loader" in resources or "solder-mod-26" in resources:
            raise AssertionError("The MCInstanceLoader package exported itself")
        if "[ci-mcil-optional]" not in resources or "optional = true" not in resources:
            raise AssertionError("The optional MCInstance resource was not exported")
        if "option1.resources = ci-mcil-optional" not in optionals:
            raise AssertionError("The MCInstance optional menu was not exported")
        if "name = CI Hidden Pack" not in metadata:
            raise AssertionError("The MCInstance metadata did not identify the pack")

    duplicate_request = urllib.request.Request(
        f"{base_url}/newmod",
        data=form_data(
            {
                "pretty_name": "Duplicate CI Mod",
                "name": "ci-example-mod",
                "author": "CI",
                "description": "Duplicate handling test",
                "link": "https://example.invalid/duplicate-mod",
                "flexRadioDefault": "BOTH",
                "type": "MOD",
                "internal_note": "Synthetic duplicate",
            }
        ),
        method="POST",
    )
    try:
        opener.open(duplicate_request, timeout=5)
    except urllib.error.HTTPError as error:
        duplicate_response = error.read()
        if error.code != 409 or b"already exists" not in duplicate_response:
            raise AssertionError(
                "Duplicate mod submission did not return a useful conflict error"
            ) from error
    else:
        raise AssertionError("Duplicate mod submission unexpectedly succeeded")


def test_fixture(image: str, fixture: Path | None, migrate: bool) -> None:
    suffix = uuid.uuid4().hex
    network = f"solderpy-db-test-{suffix}"
    database_container = f"solderpy-mysql-{suffix}"
    application_container = f"solderpy-app-{suffix}"
    maven_container = f"solderpy-maven-{suffix}"
    failed = True

    docker("network", "create", network, capture_output=True)
    try:
        docker(
            "run",
            "--detach",
            "--name",
            database_container,
            "--network",
            network,
            "--network-alias",
            "mysql",
            "--env",
            f"MYSQL_DATABASE={DATABASE}",
            "--env",
            f"MYSQL_USER={DATABASE_USER}",
            "--env",
            f"MYSQL_PASSWORD={DATABASE_PASSWORD}",
            "--env",
            f"MYSQL_ROOT_PASSWORD={ROOT_PASSWORD}",
            MYSQL_IMAGE,
            capture_output=True,
        )
        wait_for_mysql(database_container)
        if fixture is None:
            create_fresh_schema(image, network)
            verify_fresh_schema(database_container)
            seed_fresh_database(database_container)
        else:
            mysql(database_container, fixture.read_text(encoding="utf-8"))

        if migrate:
            migrate_technic_schema(image, network)
            verify_technic_migration(database_container)

        verify_read_only_api_startup(image, network, database_container)

        seed_dependency_scenario(database_container)
        seed_api_access_scenario(database_container)
        seed_mcinstance_scenario(database_container)
        start_maven_fixture(image, network, maven_container)

        docker(
            "run",
            "--detach",
            "--name",
            application_container,
            "--network",
            network,
            "--publish",
            "127.0.0.1:0:5000",
            *database_environment("mysql", write_api=True),
            image,
            capture_output=True,
        )
        port_mapping = subprocess.check_output(
            ["docker", "port", application_container, "5000/tcp"], text=True
        ).strip()
        base_url = f"http://127.0.0.1:{port_mapping.rsplit(':', 1)[1]}"
        wait_for_application(application_container, f"{base_url}/api/")
        verify_compatible_collations(database_container)
        migrated_bootstrap_type = mysql(
            database_container,
            "SELECT modtype FROM mods WHERE id = 26;",
        )
        if migrated_bootstrap_type != "BOOTSTRAP":
            raise AssertionError(
                "Application startup did not migrate the MCIL package type "
                "to BOOTSTRAP"
            )
        write_token = seed_write_api_token(database_container)
        exercise_write_api(base_url, database_container, write_token)
        if fixture is not None and fixture.name == "solderpy.sql":
            legacy_notes = mysql(
                database_container,
                "SELECT notes FROM mods WHERE id = 1;",
            )
            if legacy_notes != "Legacy solder.py private mod note":
                raise AssertionError(
                    f"Legacy solder.py note was not preserved: {legacy_notes}"
                )
            legacy_note_column = mysql(
                database_container,
                "SELECT COUNT(*) FROM information_schema.COLUMNS "
                f"WHERE TABLE_SCHEMA = '{DATABASE}' AND TABLE_NAME = 'mods' "
                "AND COLUMN_NAME = 'note';",
            )
            if legacy_note_column != "0":
                raise AssertionError("Legacy mods.note column was not removed")
            migrated_mod_type = mysql(
                database_container,
                "SELECT modtype FROM mods WHERE id = 1;",
            )
            if migrated_mod_type != "MOD":
                raise AssertionError(
                    "A legacy mod with a raw-JAR hash was not promoted to MOD"
                )
        mysql(
            database_container,
            "UPDATE modversions SET modloader = 'FORGE' WHERE id IN (26, 27, 28);",
        )
        dependency_table_count = mysql(
            database_container,
            "SELECT COUNT(*) FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = '{DATABASE}' "
            "AND TABLE_NAME = 'mod_dependencies';",
        )
        if dependency_table_count != "1":
            raise AssertionError("Application startup did not create mod_dependencies")
        settings_table_count = mysql(
            database_container,
            "SELECT COUNT(*) FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = '{DATABASE}' "
            "AND TABLE_NAME = 'solder_settings';",
        )
        if settings_table_count != "1":
            raise AssertionError(
                "Application startup did not create distribution settings"
            )
        advanced_optional_table_count = mysql(
            database_container,
            "SELECT COUNT(*) FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = '{DATABASE}' AND TABLE_NAME IN "
            "('build_optional_groups', 'build_optional_group_items');",
        )
        if advanced_optional_table_count != "2":
            raise AssertionError(
                "Application startup did not create advanced optionals"
            )
        technic_solderpy_loader_table_count = mysql(
            database_container,
            "SELECT COUNT(*) FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = '{DATABASE}' "
            "AND TABLE_NAME = 'technic_solderpy_loader_builds';",
        )
        if technic_solderpy_loader_table_count != "1":
            raise AssertionError(
                "Application startup did not create Technic SolderPy Loader settings"
            )
        technic_delivery_column_count = mysql(
            database_container,
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            f"WHERE TABLE_SCHEMA = '{DATABASE}' "
            "AND TABLE_NAME = 'technic_solderpy_loader_builds' "
            "AND COLUMN_NAME = 'delivery_mode';",
        )
        if technic_delivery_column_count != "1":
            raise AssertionError(
                "Application startup omitted the Technic delivery mode"
            )
        override_default = mysql(
            database_container,
            "SELECT CONCAT(modrinth_project_id, ':', curseforge_project_id, ':', "
            "side, ':', enabled, ':', override_solder_only, ':', built_in) "
            "FROM platform_export_overrides WHERE name = 'TX Loader';",
        )
        if override_default != "eh8us8FY:706505:CLIENT:0:0:1":
            raise AssertionError(
                "Application startup did not create the disabled TX Loader override"
            )
        maven_table_count = mysql(
            database_container,
            "SELECT COUNT(*) FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = '{DATABASE}' "
            "AND TABLE_NAME IN "
            "('maven_repositories', 'maven_artifacts', 'maven_versions');",
        )
        if maven_table_count != "3":
            raise AssertionError("Application startup did not create the Maven catalog")
        direct_source_column_count = mysql(
            database_container,
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            f"WHERE TABLE_SCHEMA = '{DATABASE}' AND ("
            "(TABLE_NAME = 'modversion_download_overrides' AND "
            "COLUMN_NAME = 'jar_url') OR "
            "(TABLE_NAME = 'modversion_download_sources' AND COLUMN_NAME IN "
            "('provider', 'url', 'filename', 'md5', 'sha1', 'sha512', "
            "'filesize')) OR "
            "(TABLE_NAME = 'maven_artifacts' AND COLUMN_NAME = "
            "'solderpy_loader_direct'));",
        )
        if direct_source_column_count != "9":
            raise AssertionError(
                "Application startup did not create direct JAR source columns"
            )
        integration_schema_count = mysql(
            database_container,
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            f"WHERE TABLE_SCHEMA = '{DATABASE}' AND ("
            "(TABLE_NAME = 'mods' AND COLUMN_NAME IN "
            "('integration_provider', 'integration_project_id')) OR "
            "(TABLE_NAME = 'modversions' AND COLUMN_NAME = "
            "'integration_version_id'));",
        )
        if integration_schema_count != "3":
            raise AssertionError(
                "Application startup did not create the integration columns"
            )
        integration_table_count = mysql(
            database_container,
            "SELECT COUNT(*) FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = '{DATABASE}' "
            "AND TABLE_NAME = 'integration_credentials';",
        )
        if integration_table_count != "0":
            raise AssertionError(
                "Application startup still creates CurseForge credentials"
            )
        mysql(
            database_container,
            "INSERT INTO mod_dependencies (mod_id, dependency_mod_id) "
            "VALUES (21, 1);",
        )
        mysql(
            database_container,
            "INSERT INTO solder_settings (name, value) VALUES "
            "('mcil_enabled', '1'), ('packwiz_enabled', '1'), "
            "('filedirector_enabled', '1') "
            "ON DUPLICATE KEY UPDATE value = '1';",
        )
        verify_api_only_distribution_files(image, network)
        exercise_database_api(base_url)
        legacy_jar_md5 = stage_legacy_mcil_package(
            application_container, database_container
        )
        exercise_maven_write_api(
            base_url, database_container, application_container, write_token
        )
        exercise_synthetic_user_login(
            base_url,
            database_container,
            application_container,
            legacy_jar_md5,
        )
        failed = False
        fixture_name = fixture.name if fixture is not None else "fresh database"
        print(f"Database image test passed: {fixture_name}")
    finally:
        if failed:
            for container in (
                application_container,
                maven_container,
                database_container,
            ):
                if subprocess.run(
                    ["docker", "inspect", container],
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                ).returncode != 0:
                    continue
                logs = subprocess.run(
                    ["docker", "logs", container],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if logs.stdout:
                    print(logs.stdout)
                if logs.stderr:
                    print(logs.stderr, file=sys.stderr)
        for container in (application_container, maven_container, database_container):
            subprocess.run(
                ["docker", "rm", "--force", container],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        subprocess.run(
            ["docker", "network", "rm", network],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def main(image: str, selection: str = "all") -> None:
    if selection in ("all", "technic"):
        test_fixture(
            image,
            ROOT / "tests" / "fixtures" / "technic_solder.sql",
            migrate=True,
        )
    if selection in ("all", "solderpy"):
        test_fixture(
            image,
            ROOT / "tests" / "fixtures" / "solderpy.sql",
            migrate=False,
        )
    if selection in ("all", "fresh"):
        test_fixture(image, None, migrate=False)


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit(
            f"Usage: {sys.argv[0]} IMAGE [all|technic|solderpy|fresh]"
        )
    selected_fixture = sys.argv[2] if len(sys.argv) == 3 else "all"
    if selected_fixture not in ("all", "technic", "solderpy", "fresh"):
        raise SystemExit(f"Unknown fixture selection: {selected_fixture}")
    main(sys.argv[1], selected_fixture)
