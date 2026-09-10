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
        "AND TABLE_NAME IN ('sessions', 'user_modpack', 'mod_dependencies');",
    )
    if int(table_count) != 3:
        raise AssertionError(
            "Migration did not create sessions, user_modpack, and mod_dependencies"
        )

    expected_indexes = (
        ("build_modversion", "idx_build_modversion_build_version"),
        ("build_modversion", "idx_build_modversion_version_build"),
        ("builds", "idx_builds_modpack_version_access"),
        ("client_modpack", "idx_client_modpack_modpack_client"),
        ("client_modpack", "idx_client_modpack_client_modpack"),
        ("modversions", "idx_modversions_mod_mcversion"),
        ("modversions", "idx_modversions_mod_version"),
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


def exercise_synthetic_user_login(base_url: str, database_container: str) -> None:
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

    dependency_request = urllib.request.Request(
        f"{base_url}/modversion/3",
        data=urllib.parse.urlencode(
            {"dependency_mod_id": "2", "adddependency_submit": "1"}
        ).encode(),
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
        data=urllib.parse.urlencode(
            {
                "modversion": "3",
                "modnames": "3",
                "add_mod_submit": "1",
            }
        ).encode(),
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
           WHERE build_modversion.build_id = 1;""",
    )
    if selected_build_mods != "1:1:0,2:4:0,3:3:0":
        raise AssertionError(
            "Management did not add the parent and newest matching required "
            f"dependency to the build: {selected_build_mods}"
        )

    with opener.open(f"{base_url}/modpackbuild/1", timeout=5) as response:
        build_editor = response.read()
        if response.status != 200 or b"CI Example Mod" not in build_editor:
            raise AssertionError("The authenticated build editor did not render")

    duplicate_request = urllib.request.Request(
        f"{base_url}/newmod",
        data=urllib.parse.urlencode(
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
        ).encode(),
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

        seed_dependency_scenario(database_container)
        seed_api_access_scenario(database_container)

        docker(
            "run",
            "--detach",
            "--name",
            application_container,
            "--network",
            network,
            "--publish",
            "127.0.0.1:0:5000",
            *database_environment("mysql"),
            image,
            capture_output=True,
        )
        port_mapping = subprocess.check_output(
            ["docker", "port", application_container, "5000/tcp"], text=True
        ).strip()
        base_url = f"http://127.0.0.1:{port_mapping.rsplit(':', 1)[1]}"
        wait_for_application(application_container, f"{base_url}/api/")
        dependency_table_count = mysql(
            database_container,
            "SELECT COUNT(*) FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = '{DATABASE}' "
            "AND TABLE_NAME = 'mod_dependencies';",
        )
        if dependency_table_count != "1":
            raise AssertionError("Application startup did not create mod_dependencies")
        mysql(
            database_container,
            "INSERT INTO mod_dependencies (mod_id, dependency_mod_id) "
            "VALUES (21, 1);",
        )
        exercise_database_api(base_url)
        exercise_synthetic_user_login(base_url, database_container)
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
