from os import getenv
import os

from dotenv import load_dotenv
from flask import flash, has_request_context
from mysql import connector

from .errorPrinter import ErrorPrinter

load_dotenv(".env")
db_host = getenv("DB_HOST")
db_port = getenv("DB_PORT")
db_user = getenv("DB_USER")
db_pass = getenv("DB_PASSWORD")
db_name = getenv("DB_DATABASE")

# Unit tests import the application without a MySQL server. Never let this
# historical test shortcut disable schema repair in a deployed container.
DISABLE_is_setup = (
    os.getenv("SOLDERPY_TESTING") == "1"
    and os.getenv("DISABLE_is_setup", "").lower() in ["true", "t", "1", "yes", "y"]
)

CORE_TABLES = {
    "modpacks",
    "builds",
    "mods",
    "modversions",
    "build_modversion",
    "users",
    "user_permissions",
    "clients",
    "client_modpack",
    "keys",
}


class Database:
    TABLE_OPTIONS = (
        " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci"
    )
    APPLICATION_TABLES = CORE_TABLES | {
        "sessions",
        "login_attempts",
        "solder_settings",
        "platform_export_overrides",
        "build_optional_groups",
        "build_optional_group_items",
        "technic_solderpy_loader_builds",
        "personal_access_tokens",
        "mod_dependencies",
        "modversion_download_overrides",
        "modversion_download_sources",
        "modversion_provider_ids",
        "modversion_minecraft_versions",
        "mod_bootstrap_settings",
        "publishing_provider_accounts",
        "modpack_publication_targets",
        "modpack_publication_runs",
        "user_modpack",
        "maven_repositories",
        "maven_artifacts",
        "maven_versions",
    }

    SESSION_TABLE_SQL = """CREATE TABLE IF NOT EXISTS sessions (
        token VARCHAR(80) NOT NULL PRIMARY KEY,
        ip VARCHAR(255) NOT NULL,
        expiry TIMESTAMP NOT NULL,
        user_id INT NOT NULL,
        INDEX idx_sessions_user (user_id),
        INDEX idx_sessions_expiry (expiry)
    )""" + TABLE_OPTIONS

    LOGIN_ATTEMPTS_TABLE_SQL = """CREATE TABLE IF NOT EXISTS login_attempts (
        attempt_key CHAR(64) NOT NULL PRIMARY KEY,
        attempts INT UNSIGNED NOT NULL DEFAULT 0,
        first_attempt TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        blocked_until TIMESTAMP NULL,
        last_attempt TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_login_attempts_cleanup (last_attempt),
        INDEX idx_login_attempts_blocked (blocked_until)
    )""" + TABLE_OPTIONS

    SOLDER_SETTINGS_TABLE_SQL = """CREATE TABLE IF NOT EXISTS solder_settings (
        name VARCHAR(64) NOT NULL PRIMARY KEY,
        value VARCHAR(255) NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )""" + TABLE_OPTIONS

    PLATFORM_EXPORT_OVERRIDES_TABLE_SQL = """CREATE TABLE IF NOT EXISTS platform_export_overrides (
        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(255) NOT NULL,
        modrinth_project_id VARCHAR(64) NOT NULL,
        curseforge_project_id INT UNSIGNED NOT NULL,
        side ENUM('CLIENT', 'SERVER', 'BOTH') NOT NULL DEFAULT 'BOTH',
        enabled TINYINT(1) NOT NULL DEFAULT 0,
        override_solder_only TINYINT(1) NOT NULL DEFAULT 0,
        built_in TINYINT(1) NOT NULL DEFAULT 0,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_platform_export_overrides_modrinth (modrinth_project_id),
        UNIQUE KEY uq_platform_export_overrides_curseforge (curseforge_project_id)
    )""" + TABLE_OPTIONS

    PLATFORM_EXPORT_OVERRIDES_DEFAULT_SQL = """INSERT IGNORE INTO platform_export_overrides
        (name, modrinth_project_id, curseforge_project_id, side, enabled,
         override_solder_only, built_in)
        VALUES ('TX Loader', 'eh8us8FY', 706505, 'CLIENT', 0, 0, 1)"""

    ADVANCED_OPTIONAL_TABLES_SQL = (
        """CREATE TABLE IF NOT EXISTS build_optional_groups (
            id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            build_id INT NOT NULL,
            name VARCHAR(255) NOT NULL,
            description VARCHAR(1000) NOT NULL DEFAULT '',
            selection_type TINYINT NOT NULL DEFAULT 0,
            sort_order INT NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_build_optional_group_name (build_id, name),
            INDEX idx_build_optional_groups_build (build_id, sort_order)
        )""" + TABLE_OPTIONS,
        """CREATE TABLE IF NOT EXISTS build_optional_group_items (
            id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            group_id INT NULL,
            build_modversion_id INT NOT NULL,
            selected_by_default TINYINT(1) NOT NULL DEFAULT 0,
            sort_order INT NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_build_optional_group_membership (build_modversion_id),
            INDEX idx_build_optional_group_items_group (group_id, sort_order)
        )""" + TABLE_OPTIONS,
    )

    TECHNIC_SOLDERPY_LOADER_TABLE_SQL = """CREATE TABLE IF NOT EXISTS technic_solderpy_loader_builds (
        build_id INT NOT NULL PRIMARY KEY,
        version_id VARCHAR(64) NOT NULL,
        version VARCHAR(255) NOT NULL,
        delivery_mode VARCHAR(16) NOT NULL DEFAULT 'LOADER',
        bootstrap_path VARCHAR(512) NOT NULL,
        bootstrap_md5 CHAR(32) NOT NULL,
        bootstrap_filesize BIGINT UNSIGNED NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )""" + TABLE_OPTIONS

    PERSONAL_ACCESS_TOKENS_TABLE_SQL = """CREATE TABLE IF NOT EXISTS personal_access_tokens (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        tokenable_type VARCHAR(255) NOT NULL,
        tokenable_id BIGINT UNSIGNED NOT NULL,
        name TEXT NOT NULL,
        token VARCHAR(64) NOT NULL UNIQUE,
        abilities TEXT,
        last_used_at TIMESTAMP NULL,
        expires_at TIMESTAMP NULL,
        created_at TIMESTAMP NULL,
        updated_at TIMESTAMP NULL,
        INDEX personal_access_tokens_tokenable_type_tokenable_id_index
            (tokenable_type, tokenable_id),
        INDEX personal_access_tokens_expires_at_index (expires_at)
    )""" + TABLE_OPTIONS

    MOD_DEPENDENCIES_TABLE_SQL = """CREATE TABLE IF NOT EXISTS mod_dependencies (
        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        mod_id INT NOT NULL,
        dependency_mod_id INT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_mod_dependencies_pair (mod_id, dependency_mod_id),
        INDEX idx_mod_dependencies_dependency (dependency_mod_id)
    )""" + TABLE_OPTIONS

    MODVERSION_DOWNLOAD_OVERRIDES_TABLE_SQL = """CREATE TABLE IF NOT EXISTS modversion_download_overrides (
        modversion_id INT NOT NULL PRIMARY KEY,
        jar_url VARCHAR(2048) NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )""" + TABLE_OPTIONS

    MODVERSION_DOWNLOAD_SOURCES_TABLE_SQL = """CREATE TABLE IF NOT EXISTS modversion_download_sources (
        modversion_id INT NOT NULL,
        provider VARCHAR(16) NOT NULL,
        url VARCHAR(2048) NOT NULL,
        filename VARCHAR(255) NOT NULL,
        md5 CHAR(32),
        sha1 CHAR(40),
        sha512 CHAR(128),
        filesize BIGINT UNSIGNED,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (modversion_id, provider),
        INDEX idx_modversion_download_sources_provider (provider, modversion_id)
    )""" + TABLE_OPTIONS

    MODVERSION_PROVIDER_IDS_TABLE_SQL = """CREATE TABLE IF NOT EXISTS modversion_provider_ids (
        modversion_id INT NOT NULL,
        provider VARCHAR(32) NOT NULL,
        project_id VARCHAR(191) NOT NULL,
        version_id VARCHAR(191) NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (modversion_id, provider),
        INDEX idx_modversion_provider_ids_lookup
            (provider, project_id, version_id)
    )""" + TABLE_OPTIONS

    MODVERSION_MINECRAFT_VERSIONS_TABLE_SQL = """CREATE TABLE IF NOT EXISTS modversion_minecraft_versions (
        modversion_id INT NOT NULL,
        minecraft_version VARCHAR(64) NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (modversion_id, minecraft_version),
        INDEX idx_modversion_minecraft_compatibility
            (minecraft_version, modversion_id)
    )""" + TABLE_OPTIONS

    MOD_BOOTSTRAP_SETTINGS_TABLE_SQL = """CREATE TABLE IF NOT EXISTS mod_bootstrap_settings (
        mod_id INT NOT NULL PRIMARY KEY,
        enforce TINYINT(1) NOT NULL DEFAULT 1,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )""" + TABLE_OPTIONS

    PUBLISHING_TABLES_SQL = (
        """CREATE TABLE IF NOT EXISTS publishing_provider_accounts (
            id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            provider VARCHAR(32) NOT NULL,
            name VARCHAR(255) NOT NULL,
            token TEXT NOT NULL,
            token_hint VARCHAR(16) NOT NULL DEFAULT '',
            configuration TEXT,
            enabled TINYINT(1) NOT NULL DEFAULT 1,
            user_id INT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_publishing_provider_account
                (user_id, provider, name),
            INDEX idx_publishing_provider_user (user_id, enabled),
            INDEX idx_publishing_provider_enabled (provider, enabled)
        )""" + TABLE_OPTIONS,
        """CREATE TABLE IF NOT EXISTS modpack_publication_targets (
            id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
            modpack_id INT NOT NULL,
            provider_account_id INT NOT NULL,
            project_id VARCHAR(191) NOT NULL,
            configuration TEXT,
            enabled TINYINT(1) NOT NULL DEFAULT 1,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_modpack_publication_account
                (modpack_id, provider_account_id),
            INDEX idx_modpack_publication_enabled
                (modpack_id, enabled),
            INDEX idx_modpack_publication_provider_account
                (provider_account_id)
        )""" + TABLE_OPTIONS,
        """CREATE TABLE IF NOT EXISTS modpack_publication_runs (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            target_id INT NOT NULL,
            build_id INT NOT NULL,
            artifact_sha256 CHAR(64) NOT NULL,
            deduplication_key CHAR(64),
            release_type VARCHAR(16) NOT NULL,
            display_name VARCHAR(255) NOT NULL,
            status VARCHAR(16) NOT NULL,
            remote_file_id VARCHAR(191),
            error_message VARCHAR(1000),
            created_by_user_id INT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            INDEX idx_modpack_publication_run_build
                (target_id, build_id, status),
            UNIQUE KEY uq_modpack_publication_run_deduplication
                (target_id, deduplication_key)
        )""" + TABLE_OPTIONS,
    )

    USER_MODPACK_TABLE_SQL = """CREATE TABLE IF NOT EXISTS user_modpack (
        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        user_id INT NOT NULL,
        modpack_id INT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        INDEX idx_user_modpack_user_pack (user_id, modpack_id),
        INDEX idx_user_modpack_pack_user (modpack_id, user_id)
    )""" + TABLE_OPTIONS

    MAVEN_REPOSITORIES_TABLE_SQL = """CREATE TABLE IF NOT EXISTS maven_repositories (
        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(255) NOT NULL UNIQUE,
        base_url VARCHAR(2048) NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    )""" + TABLE_OPTIONS

    MAVEN_ARTIFACTS_TABLE_SQL = """CREATE TABLE IF NOT EXISTS maven_artifacts (
        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        repository_id INT NOT NULL,
        group_id VARCHAR(191) NOT NULL,
        artifact_id VARCHAR(191) NOT NULL,
        classifier VARCHAR(128) NOT NULL DEFAULT '',
        extension VARCHAR(16) NOT NULL DEFAULT 'jar',
        version_mode VARCHAR(16) NOT NULL DEFAULT 'MANUAL',
        version_pattern VARCHAR(255),
        fixed_minecraft VARCHAR(255),
        modloader VARCHAR(32),
        slug VARCHAR(255) NOT NULL,
        title VARCHAR(255) NOT NULL,
        description VARCHAR(255) NOT NULL DEFAULT '',
        author VARCHAR(255),
        link VARCHAR(255),
        side ENUM('CLIENT', 'SERVER', 'BOTH') NOT NULL DEFAULT 'BOTH',
        solderpy_loader_direct TINYINT(1) NOT NULL DEFAULT 0,
        mod_id INT,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_maven_artifact_coordinates
            (repository_id, group_id, artifact_id, classifier, extension),
        UNIQUE KEY uq_maven_artifact_mod (mod_id),
        INDEX idx_maven_artifacts_repository (repository_id)
    )""" + TABLE_OPTIONS

    MAVEN_VERSIONS_TABLE_SQL = """CREATE TABLE IF NOT EXISTS maven_versions (
        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
        maven_artifact_id INT NOT NULL,
        upstream_version VARCHAR(255) NOT NULL,
        integration_version_id CHAR(64) NOT NULL,
        minecraft VARCHAR(255),
        mod_version VARCHAR(255),
        modloader VARCHAR(32),
        mapping_source VARCHAR(16) NOT NULL DEFAULT 'UNMAPPED',
        enabled TINYINT(1) NOT NULL DEFAULT 0,
        available TINYINT(1) NOT NULL DEFAULT 1,
        metadata_order INT NOT NULL DEFAULT 0,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_maven_version_source
            (maven_artifact_id, upstream_version),
        UNIQUE KEY uq_maven_version_integration
            (maven_artifact_id, integration_version_id),
        INDEX idx_maven_version_compatibility
            (maven_artifact_id, minecraft, modloader, enabled, available,
             metadata_order)
    )""" + TABLE_OPTIONS

    MAVEN_TABLES_SQL = (
        MAVEN_REPOSITORIES_TABLE_SQL,
        MAVEN_ARTIFACTS_TABLE_SQL,
        MAVEN_VERSIONS_TABLE_SQL,
    )

    # One current-state table definition per additive feature. Fresh installs,
    # Technic imports and existing solder.py databases all use this same list.
    ADDITIVE_TABLES_SQL = (
        MOD_DEPENDENCIES_TABLE_SQL,
        MODVERSION_DOWNLOAD_OVERRIDES_TABLE_SQL,
        MODVERSION_DOWNLOAD_SOURCES_TABLE_SQL,
        MODVERSION_PROVIDER_IDS_TABLE_SQL,
        MODVERSION_MINECRAFT_VERSIONS_TABLE_SQL,
        MOD_BOOTSTRAP_SETTINGS_TABLE_SQL,
        *PUBLISHING_TABLES_SQL,
        PERSONAL_ACCESS_TOKENS_TABLE_SQL,
        USER_MODPACK_TABLE_SQL,
        SOLDER_SETTINGS_TABLE_SQL,
        SESSION_TABLE_SQL,
        LOGIN_ATTEMPTS_TABLE_SQL,
        PLATFORM_EXPORT_OVERRIDES_TABLE_SQL,
        *ADVANCED_OPTIONAL_TABLES_SQL,
        TECHNIC_SOLDERPY_LOADER_TABLE_SQL,
        *MAVEN_TABLES_SQL,
    )

    @staticmethod
    def create_additive_tables(cur) -> None:
        for query in Database.ADDITIVE_TABLES_SQL:
            cur.execute(query)

    @staticmethod
    def verify_application_tables(cur) -> None:
        cur.execute("SHOW TABLES")
        present = {row[0] for row in cur.fetchall()}
        missing = Database.APPLICATION_TABLES - present
        if missing:
            raise RuntimeError(
                "Database schema is missing required tables: "
                + ", ".join(sorted(missing))
            )

    MODLOADER_COLUMN_MIGRATIONS = (
        (
            "builds",
            "modloader",
            "ALTER TABLE builds ADD COLUMN modloader VARCHAR(32) NULL AFTER forge",
        ),
        (
            "modversions",
            "modloader",
            "ALTER TABLE modversions ADD COLUMN modloader VARCHAR(255) NULL AFTER mcversion",
        ),
    )

    JAVA_RUNTIME_COLUMN_MIGRATIONS = (
        (
            "builds",
            "java_runtime",
            "ALTER TABLE builds ADD COLUMN java_runtime VARCHAR(255) NULL AFTER min_java",
        ),
    )

    USER_COLUMN_MIGRATIONS = (
        (
            "users",
            "night_mode",
            "ALTER TABLE users ADD COLUMN night_mode TINYINT(1) NOT NULL "
            "DEFAULT 0 AFTER remember_token",
        ),
    )

    JAR_COLUMN_MIGRATIONS = (
        (
            "modversions",
            "jarmd5",
            "ALTER TABLE modversions ADD COLUMN jarmd5 VARCHAR(255) AFTER md5",
        ),
        (
            "modversions",
            "jarfilesize",
            "ALTER TABLE modversions ADD COLUMN jarfilesize BIGINT UNSIGNED NULL AFTER jarmd5",
        ),
    )

    MAVEN_COLUMN_MIGRATIONS = (
        (
            "maven_artifacts",
            "solderpy_loader_direct",
            "ALTER TABLE maven_artifacts ADD COLUMN solderpy_loader_direct "
            "TINYINT(1) NOT NULL DEFAULT 0 AFTER side",
        ),
    )

    TECHNIC_SOLDERPY_LOADER_COLUMN_MIGRATIONS = (
        (
            "technic_solderpy_loader_builds",
            "delivery_mode",
            "ALTER TABLE technic_solderpy_loader_builds "
            "ADD COLUMN delivery_mode VARCHAR(16) NOT NULL DEFAULT 'LOADER' "
            "AFTER version",
        ),
    )

    NOTES_COLUMN_MIGRATIONS = (
        (
            "mods",
            "notes",
            "ALTER TABLE mods ADD COLUMN notes TEXT NULL AFTER link",
        ),
    )

    INTEGRATION_COLUMN_MIGRATIONS = (
        (
            "mods",
            "integration_provider",
            "ALTER TABLE mods ADD COLUMN integration_provider VARCHAR(16) NULL AFTER notes",
        ),
        (
            "mods",
            "integration_project_id",
            "ALTER TABLE mods ADD COLUMN integration_project_id VARCHAR(64) NULL AFTER integration_provider",
        ),
        (
            "modversions",
            "integration_version_id",
            "ALTER TABLE modversions ADD COLUMN integration_version_id VARCHAR(64) NULL AFTER modloader",
        ),
    )

    CURRENT_COLUMN_REPAIRS = (
        *JAR_COLUMN_MIGRATIONS,
        *MODLOADER_COLUMN_MIGRATIONS,
        *JAVA_RUNTIME_COLUMN_MIGRATIONS,
        *USER_COLUMN_MIGRATIONS,
        *NOTES_COLUMN_MIGRATIONS,
        *INTEGRATION_COLUMN_MIGRATIONS,
        *MAVEN_COLUMN_MIGRATIONS,
        *TECHNIC_SOLDERPY_LOADER_COLUMN_MIGRATIONS,
        (
            "modpacks",
            "optional_mode",
            "ALTER TABLE modpacks ADD COLUMN optional_mode TINYINT NOT NULL DEFAULT 0",
        ),
        (
            "platform_export_overrides",
            "override_solder_only",
            "ALTER TABLE platform_export_overrides ADD COLUMN override_solder_only "
            "TINYINT(1) NOT NULL DEFAULT 0 AFTER enabled",
        ),
    )

    API_INDEX_MIGRATIONS = (
        (
            "build_modversion",
            ("build_id", "modversion_id"),
            "ALTER TABLE build_modversion "
            "ADD INDEX idx_build_modversion_build_version (build_id, modversion_id)",
        ),
        (
            "build_modversion",
            ("modversion_id", "build_id"),
            "ALTER TABLE build_modversion "
            "ADD INDEX idx_build_modversion_version_build (modversion_id, build_id)",
        ),
        (
            "builds",
            ("modpack_id", "version", "is_published", "private"),
            "ALTER TABLE builds "
            "ADD INDEX idx_builds_modpack_version_access "
            "(modpack_id, version, is_published, private)",
        ),
        (
            "builds",
            ("modpack_id", "id"),
            "ALTER TABLE builds "
            "ADD INDEX idx_builds_modpack_id (modpack_id, id)",
        ),
        (
            "client_modpack",
            ("modpack_id", "client_id"),
            "ALTER TABLE client_modpack "
            "ADD INDEX idx_client_modpack_modpack_client (modpack_id, client_id)",
        ),
        (
            "client_modpack",
            ("client_id", "modpack_id"),
            "ALTER TABLE client_modpack "
            "ADD INDEX idx_client_modpack_client_modpack (client_id, modpack_id)",
        ),
        (
            "modversions",
            ("mod_id", "mcversion", "modloader"),
            "ALTER TABLE modversions "
            "ADD INDEX idx_modversions_mod_compatibility "
            "(mod_id, mcversion, modloader)",
        ),
        (
            "modversions",
            ("mod_id", "version"),
            "ALTER TABLE modversions "
            "ADD INDEX idx_modversions_mod_version (mod_id, version)",
        ),
        (
            "modversions",
            ("mod_id", "id"),
            "ALTER TABLE modversions "
            "ADD INDEX idx_modversions_mod_id (mod_id, id)",
        ),
        (
            "mods",
            ("integration_provider", "integration_project_id"),
            "ALTER TABLE mods ADD UNIQUE INDEX uq_mods_integration_project "
            "(integration_provider, integration_project_id)",
        ),
        (
            "modversions",
            ("mod_id", "integration_version_id"),
            "ALTER TABLE modversions ADD UNIQUE INDEX uq_modversions_integration_version "
            "(mod_id, integration_version_id)",
        ),
        (
            "user_permissions",
            ("user_id",),
            "ALTER TABLE user_permissions "
            "ADD INDEX idx_user_permissions_user (user_id)",
        ),
        (
            "user_modpack",
            ("user_id", "modpack_id"),
            "ALTER TABLE user_modpack "
            "ADD INDEX idx_user_modpack_user_pack (user_id, modpack_id)",
        ),
        (
            "user_modpack",
            ("modpack_id", "user_id"),
            "ALTER TABLE user_modpack "
            "ADD INDEX idx_user_modpack_pack_user (modpack_id, user_id)",
        ),
        (
            "clients",
            ("uuid",),
            "ALTER TABLE clients ADD INDEX idx_clients_uuid (uuid)",
        ),
        (
            "keys",
            ("api_key",),
            "ALTER TABLE `keys` ADD INDEX idx_keys_api_key (api_key)",
        ),
        (
            "users",
            ("username",),
            "ALTER TABLE users ADD INDEX idx_users_username (username)",
        ),
        (
            "sessions",
            ("user_id",),
            "ALTER TABLE sessions ADD INDEX idx_sessions_user (user_id)",
        ),
        (
            "sessions",
            ("expiry",),
            "ALTER TABLE sessions ADD INDEX idx_sessions_expiry (expiry)",
        ),
        (
            "modpack_publication_targets",
            ("provider_account_id",),
            "ALTER TABLE modpack_publication_targets "
            "ADD INDEX idx_modpack_publication_provider_account "
            "(provider_account_id)",
        ),
    )

    @staticmethod
    def index_covers_columns(cur, table: str, columns: tuple[str, ...]) -> bool:
        """Return whether an index starts with the requested columns."""
        cur.execute(
            """SELECT INDEX_NAME, COLUMN_NAME
               FROM information_schema.STATISTICS
               WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
               ORDER BY INDEX_NAME, SEQ_IN_INDEX""",
            (db_name, table),
        )
        indexes = {}
        for index, column in cur.fetchall():
            indexes.setdefault(index, []).append(column)
        return any(
            tuple(index_columns[:len(columns)]) == columns
            for index_columns in indexes.values()
        )

    @staticmethod
    def column_exists(cur, table: str, column: str) -> bool:
        cur.execute(
            """SELECT 1
               FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA = %s
                 AND TABLE_NAME = %s
                 AND COLUMN_NAME = %s""",
            (db_name, table, column),
        )
        return cur.fetchone() is not None

    @staticmethod
    def repair_columns(cur, definitions) -> None:
        for table, column, query in definitions:
            if not Database.column_exists(cur, table, column):
                cur.execute(query)

    @staticmethod
    def repair_indexes(cur) -> None:
        for table, columns, query in Database.API_INDEX_MIGRATIONS:
            if not Database.index_covers_columns(cur, table, columns):
                cur.execute(query)

    @staticmethod
    def allow_ungrouped_advanced_optionals(cur) -> None:
        """Let the advanced work list exist before a group is selected."""
        cur.execute(
            """SELECT IS_NULLABLE
               FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA = %s
                 AND TABLE_NAME = 'build_optional_group_items'
                 AND COLUMN_NAME = 'group_id'""",
            (db_name,),
        )
        row = cur.fetchone()
        if row and row[0] == "NO":
            cur.execute(
                "ALTER TABLE build_optional_group_items "
                "MODIFY group_id INT NULL"
            )

    @staticmethod
    def migrate_legacy_mod_notes(cur) -> None:
        """Move solder.py 1.7.4 mod notes to Technic's plural column."""
        if Database.column_exists(cur, "mods", "note"):
            cur.execute(
                """UPDATE mods
                   SET notes = note
                   WHERE notes IS NULL AND note IS NOT NULL"""
            )
            cur.execute("ALTER TABLE mods DROP COLUMN note")

    @staticmethod
    def migrate_jar_hash_mod_types(cur) -> None:
        """Promote mods with a stored raw-JAR hash to the MOD package type."""
        cur.execute(
            """UPDATE mods
               INNER JOIN modversions ON modversions.mod_id = mods.id
               SET mods.modtype = 'MOD'
               WHERE CHAR_LENGTH(TRIM(modversions.jarmd5)) = 32
                 AND TRIM(modversions.jarmd5) NOT REGEXP '[^0-9A-Fa-f]'
                 AND (
                     mods.modtype IS NULL
                     OR mods.modtype NOT IN ('MOD', 'BOOTSTRAP', 'LAUNCHER')
                 )"""
        )

    @staticmethod
    def migrate_bootstrap_modtype(cur) -> None:
        """Rename the former MCIL-specific package role without losing rows."""
        if not Database.column_exists(cur, "mods", "modtype"):
            return
        cur.execute(
            """SELECT COLUMN_TYPE
               FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA = %s
                 AND TABLE_NAME = 'mods'
                 AND COLUMN_NAME = 'modtype'""",
            (db_name,),
        )
        row = cur.fetchone()
        column_type = str(row[0] if row else "").upper()
        had_mcil = "'MCIL'" in column_type
        had_bootstrap = "'BOOTSTRAP'" in column_type
        if not had_bootstrap:
            cur.execute(
                "ALTER TABLE mods MODIFY modtype "
                "ENUM('MOD','LAUNCHER','RES','CONFIG','MCIL','BOOTSTRAP','NONE') "
                "DEFAULT 'MOD'"
            )
        if had_mcil:
            cur.execute(
                "UPDATE mods SET modtype = 'BOOTSTRAP' WHERE modtype = 'MCIL'"
            )
        if had_mcil or not had_bootstrap:
            cur.execute(
                "ALTER TABLE mods MODIFY modtype "
                "ENUM('MOD','LAUNCHER','RES','CONFIG','BOOTSTRAP','NONE') "
                "DEFAULT 'MOD'"
            )

    @staticmethod
    def expand_modversion_compatibility(cur) -> None:
        """Allow canonical CSV sets in modversion compatibility columns."""
        cur.execute(
            """SELECT CHARACTER_MAXIMUM_LENGTH
               FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA = %s
                 AND TABLE_NAME = 'modversions'
                 AND COLUMN_NAME = 'modloader'""",
            (db_name,),
        )
        row = cur.fetchone()
        if row and int(row[0] or 0) < 255:
            cur.execute(
                "ALTER TABLE modversions MODIFY modloader VARCHAR(255) NULL"
            )

    @staticmethod
    def migrate_technic_modpack_permissions(cur) -> None:
        """Preserve Technic's CSV modpack grants in solder.py's relation."""
        cur.execute(
            """INSERT INTO user_modpack (user_id, modpack_id)
               SELECT user_permissions.user_id, modpacks.id
               FROM user_permissions
               INNER JOIN modpacks
                   ON FIND_IN_SET(
                       CAST(modpacks.id AS BINARY),
                       CAST(
                           REPLACE(
                               COALESCE(user_permissions.modpacks, ''),
                               ' ',
                               ''
                           ) AS BINARY
                       )
                   ) > 0
               LEFT JOIN user_modpack
                   ON user_modpack.user_id = user_permissions.user_id
                  AND user_modpack.modpack_id = modpacks.id
               WHERE user_modpack.id IS NULL"""
        )
        cur.execute(
            """UPDATE modpacks
               INNER JOIN (
                   SELECT modpack_id, MIN(user_id) AS user_id
                   FROM user_modpack
                   GROUP BY modpack_id
               ) AS assignments ON assignments.modpack_id = modpacks.id
               SET modpacks.user_id = assignments.user_id
               WHERE modpacks.user_id IS NULL"""
        )
        cur.execute(
            """UPDATE modpacks
               SET user_id = (SELECT MIN(id) FROM users)
               WHERE user_id IS NULL"""
        )

    @staticmethod
    def normalize_legacy_timestamps(cur) -> None:
        """Replace only obsolete zero-date defaults left by older databases.

        Current Technic Solder timestamps are nullable, so valid NULL values are
        deliberately preserved. Older zero-date definitions are restored to the
        automatic solder.py timestamp defaults before MySQL 8 adds indexes.
        """
        cur.execute(
            """SELECT TABLE_NAME, COLUMN_NAME, COLUMN_DEFAULT
               FROM information_schema.COLUMNS
               WHERE TABLE_SCHEMA = %s
                 AND COLUMN_NAME IN ('created_at', 'updated_at')""",
            (db_name,),
        )
        legacy_columns = {}
        for table, column, default in cur.fetchall():
            if table not in CORE_TABLES or not str(default).startswith("0000-00-00"):
                continue
            legacy_columns.setdefault(table, []).append(column)

        for table, columns in legacy_columns.items():
            definitions = ", ".join(
                f"MODIFY `{column}` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
                + (" ON UPDATE CURRENT_TIMESTAMP" if column == "updated_at" else "")
                for column in columns
            )
            cur.execute(f"ALTER TABLE `{table}` {definitions}")

    @staticmethod
    def normalize_table_collations(cur) -> None:
        """Keep extension tables comparable with Technic Solder text columns."""
        table_names = tuple(sorted(Database.APPLICATION_TABLES))
        placeholders = ", ".join(["%s"] * len(table_names))
        cur.execute(
            f"""SELECT TABLE_NAME, TABLE_COLLATION
                FROM information_schema.TABLES
                WHERE TABLE_SCHEMA = %s
                  AND TABLE_NAME IN ({placeholders})""",  # nosec B608
            (db_name, *table_names),
        )
        for table, collation in cur.fetchall():
            if str(collation or "").casefold() == "utf8mb4_unicode_ci":
                continue
            # The table name came from the fixed APPLICATION_TABLES allowlist.
            cur.execute(
                f"ALTER TABLE `{table}` CONVERT TO CHARACTER SET utf8mb4 "
                "COLLATE utf8mb4_unicode_ci"  # nosec B608
            )

    @staticmethod
    def get_connection() -> connector.connection:
        try:
            conn: connector.MySQLConnection = connector.connect(
                host=db_host,
                port=db_port,
                user=db_user,
                password=db_pass,
                database=db_name,
            )
        except Exception as e:
            ErrorPrinter.message("Error connecting to database", e)
            # Connections are also checked while the application is importing,
            # before Flask has established a request context.
            if has_request_context():
                flash("Error connecting to database", "error")
            return None
        return conn

    @staticmethod
    def is_setup() -> int:
        if DISABLE_is_setup == True:
            return 1
        conn = Database.get_connection()
        if conn is None:
            return 2
        try:
            cur = conn.cursor()
            cur.execute("SHOW TABLES")
            tables_found = cur.fetchall()
        except Exception as e:
            ErrorPrinter.message("An error occurred whilst trying to check database setup", e)
            if has_request_context():
                flash("An error occurred whilst trying to check database setup", "error")
            return 2
        finally:
            conn.close()
        if not tables_found:
            return 0
        if not CORE_TABLES.issubset({table[0] for table in tables_found}):
            return 0
        return 1

    @staticmethod
    def create_tables() -> bool:
        con = Database.get_connection()
        if con is None:
            return False
        cur = None
        try:
            cur = con.cursor()
            cur.execute(
                """CREATE TABLE IF NOT EXISTS modpacks (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        name VARCHAR(255) NOT NULL UNIQUE,
                        slug VARCHAR(255) NOT NULL UNIQUE,
                        user_id INT NOT NULL,
                        recommended VARCHAR(255),
                        latest VARCHAR(255),
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        `order` INT DEFAULT(0),
                        hidden TINYINT(1) DEFAULT(1),
                        private TINYINT(1) DEFAULT(0),
                        pinned TINYINT(1) NOT NULL DEFAULT(0),
                        enable_optionals BOOLEAN DEFAULT(0),
                        enable_server BOOLEAN DEFAULT(0),
                        optional_mode TINYINT NOT NULL DEFAULT(0)
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                          COLLATE=utf8mb4_unicode_ci"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS builds (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        modpack_id INT NOT NULL,
                        version VARCHAR(255) NOT NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        minecraft VARCHAR(255) NOT NULL DEFAULT(''),
                        forge VARCHAR(255),
                        modloader VARCHAR(32),
                        is_published TINYINT(1) DEFAULT(0),
                        private TINYINT(1) DEFAULT(0),
                        min_java VARCHAR(255),
                        java_runtime VARCHAR(255),
                        min_memory INT,
                        marked TINYINT(1) NOT NULL DEFAULT(0),
                        INDEX idx_builds_modpack_version_access
                            (modpack_id, version, is_published, private),
                        INDEX idx_builds_modpack_id (modpack_id, id)
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                          COLLATE=utf8mb4_unicode_ci"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS mods (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        name VARCHAR(255) NOT NULL UNIQUE,
                        pretty_name VARCHAR(255) DEFAULT(''),
                        description VARCHAR(255) DEFAULT(''),
                        author VARCHAR(255),
                        link VARCHAR(255),
                        notes TEXT,
                        integration_provider VARCHAR(16),
                        integration_project_id VARCHAR(64),
                        side enum('CLIENT', 'SERVER', 'BOTH') DEFAULT 'BOTH',
                        modtype enum('MOD', 'LAUNCHER', 'RES', 'CONFIG', 'BOOTSTRAP', 'NONE') DEFAULT 'MOD',
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        UNIQUE INDEX uq_mods_integration_project
                            (integration_provider, integration_project_id)
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                          COLLATE=utf8mb4_unicode_ci"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS modversions (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        mod_id INT NOT NULL,
                        version VARCHAR(255) NOT NULL,
                        mcversion VARCHAR(255),
                        modloader VARCHAR(255),
                        integration_version_id VARCHAR(64),
                        md5 VARCHAR(255) NOT NULL,
                        jarmd5 VARCHAR(255),
                        jarfilesize BIGINT UNSIGNED,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        filesize INT,
                        INDEX idx_modversions_mod_compatibility
                            (mod_id, mcversion, modloader),
                        INDEX idx_modversions_mod_version (mod_id, version),
                        INDEX idx_modversions_mod_id (mod_id, id),
                        UNIQUE INDEX uq_modversions_integration_version
                            (mod_id, integration_version_id)
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                          COLLATE=utf8mb4_unicode_ci"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS build_modversion (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        modversion_id INT NOT NULL,
                        build_id INT NOT NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        optional TINYINT(1) NOT NULL DEFAULT(0),
                        INDEX idx_build_modversion_build_version (build_id, modversion_id),
                        INDEX idx_build_modversion_version_build (modversion_id, build_id)
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                          COLLATE=utf8mb4_unicode_ci"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS users (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        username VARCHAR(255) NOT NULL,
                        email VARCHAR(255) NOT NULL,
                        password VARCHAR(255) NOT NULL,
                        created_ip VARCHAR(255) NOT NULL,
                        last_ip VARCHAR(255),
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        remember_token VARCHAR(255) DEFAULT(''),
                        night_mode TINYINT(1) NOT NULL DEFAULT 0,
                        updated_by_ip VARCHAR(255),
                        created_by_user_id INT DEFAULT(1),
                        updated_by_user_id INT,
                        INDEX idx_users_username (username)
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                          COLLATE=utf8mb4_unicode_ci"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS user_permissions (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        user_id INT NOT NULL,
                        solder_full BOOLEAN DEFAULT(0),
                        solder_users BOOLEAN DEFAULT(0),
                        solder_keys BOOLEAN DEFAULT(0),
                        solder_clients BOOLEAN DEFAULT(0),
                        solder_env BOOLEAN DEFAULT(0),
                        mods_create BOOLEAN DEFAULT(0),
                        mods_manage BOOLEAN DEFAULT(0),
                        mods_delete BOOLEAN DEFAULT(0),
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        modpacks_create BOOLEAN DEFAULT(0),
                        modpacks_manage BOOLEAN DEFAULT(0),
                        modpacks_delete BOOLEAN DEFAULT(0),
                        modpacks VARCHAR(255),
                        INDEX user_permissions_user_id_index (user_id)
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                          COLLATE=utf8mb4_unicode_ci"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS clients (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        name VARCHAR(255) NOT NULL UNIQUE,
                        uuid VARCHAR(255) NOT NULL UNIQUE,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                          COLLATE=utf8mb4_unicode_ci"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS client_modpack (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        client_id INT NOT NULL,
                        modpack_id INT NOT NULL,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        INDEX idx_client_modpack_modpack_client (modpack_id, client_id),
                        INDEX idx_client_modpack_client_modpack (client_id, modpack_id)
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                          COLLATE=utf8mb4_unicode_ci"""
            )
            cur.execute(
                """CREATE TABLE IF NOT EXISTS `keys` (
                        id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
                        name VARCHAR(255) NOT NULL UNIQUE,
                        api_key VARCHAR(255) NOT NULL UNIQUE,
                        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                          COLLATE=utf8mb4_unicode_ci"""
            )
            Database.create_additive_tables(cur)
            cur.execute(Database.PLATFORM_EXPORT_OVERRIDES_DEFAULT_SQL)
            Database.allow_ungrouped_advanced_optionals(cur)
            Database.normalize_table_collations(cur)
            Database.verify_application_tables(cur)
            con.commit()
            return True
        except Exception as error:
            ErrorPrinter.message("Error creating tables", error)
            if has_request_context():
                flash("Error creating tables", "error")
            return False
        finally:
            if cur is not None:
                cur.close()
            con.close()

    @staticmethod
    def migratetechnic_tables() -> bool:
        column_migrations = (
            (
                "modpacks",
                "user_id",
                "ALTER TABLE modpacks ADD COLUMN user_id INT NULL AFTER slug",
            ),
            (
                "modpacks",
                "pinned",
                "ALTER TABLE modpacks ADD COLUMN pinned TINYINT(1) NOT NULL DEFAULT 0",
            ),
            (
                "modpacks",
                "enable_optionals",
                "ALTER TABLE modpacks ADD COLUMN enable_optionals BOOLEAN DEFAULT 0",
            ),
            (
                "modpacks",
                "enable_server",
                "ALTER TABLE modpacks ADD COLUMN enable_server BOOLEAN DEFAULT 0",
            ),
            (
                "mods",
                "side",
                "ALTER TABLE mods ADD COLUMN side ENUM('CLIENT', 'SERVER', 'BOTH') DEFAULT 'BOTH'",
            ),
            (
                "mods",
                "modtype",
                "ALTER TABLE mods ADD COLUMN modtype ENUM('MOD', 'LAUNCHER', 'RES', 'CONFIG', 'BOOTSTRAP', 'NONE') DEFAULT 'MOD'",
            ),
            (
                "builds",
                "minecraft",
                "ALTER TABLE builds ADD COLUMN minecraft VARCHAR(255) NOT NULL DEFAULT ''",
            ),
            (
                "builds",
                "forge",
                "ALTER TABLE builds ADD COLUMN forge VARCHAR(255)",
            ),
            (
                "builds",
                "marked",
                "ALTER TABLE builds ADD COLUMN marked TINYINT(1) NOT NULL DEFAULT 0",
            ),
            (
                "build_modversion",
                "optional",
                "ALTER TABLE build_modversion ADD COLUMN optional TINYINT(1) NOT NULL DEFAULT 0",
            ),
            (
                "modversions",
                "mcversion",
                "ALTER TABLE modversions ADD COLUMN mcversion VARCHAR(255) AFTER version",
            ),
            (
                "user_permissions",
                "solder_env",
                "ALTER TABLE user_permissions ADD COLUMN solder_env BOOLEAN DEFAULT 0",
            ),
            *Database.CURRENT_COLUMN_REPAIRS,
        )
        con = Database.get_connection()
        if con is None:
            return False

        cur = None
        try:
            cur = con.cursor()
            # Repair all additive tables before checking columns that may
            # belong to those tables.
            Database.create_additive_tables(cur)
            Database.normalize_legacy_timestamps(cur)
            Database.repair_columns(cur, column_migrations)

            Database.migrate_legacy_mod_notes(cur)
            Database.migrate_bootstrap_modtype(cur)
            Database.expand_modversion_compatibility(cur)
            Database.migrate_jar_hash_mod_types(cur)

            # Technic stores modpack permissions as CSV; copy them into the
            # current table after its definition has been repaired above.
            Database.migrate_technic_modpack_permissions(cur)
            cur.execute("ALTER TABLE modpacks MODIFY user_id INT NOT NULL")
            cur.execute(
                "UPDATE builds SET modloader = 'FORGE' "
                "WHERE modloader IS NULL AND forge IS NOT NULL"
            )

            cur.execute(Database.PLATFORM_EXPORT_OVERRIDES_DEFAULT_SQL)
            Database.allow_ungrouped_advanced_optionals(cur)

            # Run index migrations after every additive table has been
            # created. A stock Technic database does not contain sessions or
            # the publishing tables yet.
            Database.repair_indexes(cur)
            Database.normalize_table_collations(cur)
            Database.verify_application_tables(cur)
            con.commit()
            print("technic database migrated!")
            return True
        except Exception as error:
            ErrorPrinter.message("Error migrating Technic Solder tables", error)
            if has_request_context():
                flash("Error migrating Technic Solder tables", "error")
            return False
        finally:
            if cur is not None:
                cur.close()
            con.close()

    @staticmethod
    def repair_schema() -> bool:
        """Repair an existing database against the current schema, without version steps."""
        if DISABLE_is_setup:
            return True

        con = Database.get_connection()
        if con is None:
            return False

        cur = None
        try:
            cur = con.cursor()
            Database.normalize_legacy_timestamps(cur)
            Database.create_additive_tables(cur)
            Database.allow_ungrouped_advanced_optionals(cur)

            Database.repair_columns(cur, Database.CURRENT_COLUMN_REPAIRS)

            # Existing installations can already have this table with an
            # older shape. Seed built-in rows only after its additive column
            # migrations have completed.
            cur.execute(Database.PLATFORM_EXPORT_OVERRIDES_DEFAULT_SQL)

            Database.migrate_legacy_mod_notes(cur)
            Database.migrate_bootstrap_modtype(cur)
            Database.expand_modversion_compatibility(cur)
            Database.migrate_jar_hash_mod_types(cur)

            cur.execute(
                "UPDATE builds SET modloader = 'FORGE' "
                "WHERE modloader IS NULL AND forge IS NOT NULL"
            )

            Database.repair_indexes(cur)
            Database.normalize_table_collations(cur)
            Database.verify_application_tables(cur)
            con.commit()
            return True
        except Exception as error:
            ErrorPrinter.message("Error updating solder.py database schema", error)
            return False
        finally:
            if cur is not None:
                cur.close()
            con.close()

    ensure_runtime_schema = repair_schema

    @staticmethod
    def create_session_table() -> bool:
        con = Database.get_connection()
        if con is None:
            return False
        cur = None
        try:
            cur = con.cursor()
            cur.execute(Database.SESSION_TABLE_SQL)
            con.commit()
            return True
        except Exception as error:
            ErrorPrinter.message("Error making session table", error)
            if has_request_context():
                flash("Error making session table", "error")
            return False
        finally:
            if cur is not None:
                cur.close()
            con.close()
