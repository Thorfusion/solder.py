"""Exercise the production image against current and migrated MySQL schemas."""

from __future__ import annotations

import http.cookiejar
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
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


def database_environment(host: str) -> list[str]:
    values = {
        "APP_PORT": "5000",
        "API_ONLY": "false",
        "AWS_EC2_METADATA_DISABLED": "true",
        "CACHE_SIZE": "100",
        "CACHE_TTL": "300",
        "DB_DATABASE": DATABASE,
        "DB_HOST": host,
        "DB_PASSWORD": DATABASE_PASSWORD,
        "DB_PORT": "3306",
        "DB_USER": DATABASE_USER,
        "MD5_REPO_LOCATION": "https://example.invalid/mods/",
        "PUBLIC_REPO_LOCATION": "https://example.invalid/mods/",
        "R2_ACCESS_KEY": "smoke-test",
        "R2_REGION": "auto",
        "R2_SECRET_KEY": "smoke-test",
        "R2_URL": "https://example.invalid/mods/",
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


def verify_migrated_schema(database_container: str) -> None:
    expected_columns = (
        ("build_modversion", "optional"),
        ("builds", "marked"),
        ("mods", "modtype"),
        ("mods", "note"),
        ("mods", "side"),
        ("modpacks", "enable_optionals"),
        ("modpacks", "enable_server"),
        ("modpacks", "pinned"),
        ("modpacks", "user_id"),
        ("modversions", "jarmd5"),
        ("modversions", "mcversion"),
        ("user_permissions", "solder_env"),
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

    table_count = mysql(
        database_container,
        "SELECT COUNT(*) FROM information_schema.TABLES "
        f"WHERE TABLE_SCHEMA = '{DATABASE}' "
        "AND TABLE_NAME IN ('sessions', 'user_modpack');",
    )
    if int(table_count) != 2:
        raise AssertionError("Migration did not create sessions and user_modpack")

    expected_indexes = (
        ("build_modversion", "idx_build_modversion_build_version"),
        ("modversions", "idx_modversions_mod_mcversion"),
    )
    index_conditions = " OR ".join(
        f"(TABLE_NAME = '{table}' AND INDEX_NAME = '{index}')"
        for table, index in expected_indexes
    )
    index_count = mysql(
        database_container,
        "SELECT COUNT(DISTINCT TABLE_NAME, INDEX_NAME) "
        "FROM information_schema.STATISTICS "
        f"WHERE TABLE_SCHEMA = '{DATABASE}' AND ({index_conditions});",
    )
    if int(index_count) != len(expected_indexes):
        raise AssertionError(
            f"Migration created {index_count}/{len(expected_indexes)} performance indexes"
        )


def request_json(endpoint: str) -> dict:
    parsed = urllib.parse.urlsplit(endpoint)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
        raise ValueError(f"Refusing non-loopback test endpoint: {endpoint}")
    with urllib.request.urlopen(endpoint, timeout=5) as response:  # nosec B310
        if response.status != 200:
            raise AssertionError(f"{endpoint} returned HTTP {response.status}")
        return json.load(response)


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
    if modpacks.get("modpacks") != {"ci-example-pack": "CI Example Pack"}:
        raise AssertionError(f"Modpack fixture was not readable: {modpacks}")

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


def exercise_synthetic_user_login(base_url: str) -> None:
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, request, file_pointer, code, message, headers, url):
            return None

    request = urllib.request.Request(
        f"{base_url}/login",
        data=urllib.parse.urlencode(
            {"username": "ci-user", "password": "ci-password"}
        ).encode(),
        method="POST",
    )
    cookies = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(cookies), NoRedirect
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

    with opener.open(f"{base_url}/modpackbuild/1", timeout=5) as response:
        build_editor = response.read()
        if response.status != 200 or b"CI Example Mod" not in build_editor:
            raise AssertionError("The authenticated build editor did not render")


def test_fixture(image: str, fixture: Path, migrate: bool) -> None:
    suffix = uuid.uuid4().hex
    network = f"solderpy-db-test-{suffix}"
    database_container = f"solderpy-mysql-{suffix}"
    application_container = f"solderpy-app-{suffix}"
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
        mysql(database_container, fixture.read_text(encoding="utf-8"))

        if migrate:
            migrate_technic_schema(image, network)
            verify_migrated_schema(database_container)

        docker(
            "run",
            "--detach",
            "--name",
            application_container,
            "--network",
            network,
            "--publish",
            "127.0.0.1::5000",
            *database_environment("mysql"),
            image,
            capture_output=True,
        )
        port_mapping = subprocess.check_output(
            ["docker", "port", application_container, "5000/tcp"], text=True
        ).strip()
        base_url = f"http://127.0.0.1:{port_mapping.rsplit(':', 1)[1]}"
        wait_for_application(application_container, f"{base_url}/api/")
        exercise_database_api(base_url)
        exercise_synthetic_user_login(base_url)
        failed = False
        print(f"Database image test passed: {fixture.name}")
    finally:
        if failed:
            for container in (application_container, database_container):
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
        for container in (application_container, database_container):
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


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        raise SystemExit(f"Usage: {sys.argv[0]} IMAGE [all|technic|solderpy]")
    selected_fixture = sys.argv[2] if len(sys.argv) == 3 else "all"
    if selected_fixture not in ("all", "technic", "solderpy"):
        raise SystemExit(f"Unknown fixture selection: {selected_fixture}")
    main(sys.argv[1], selected_fixture)
