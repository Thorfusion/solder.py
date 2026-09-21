from collections import deque
import datetime
import hashlib
import hmac
import http.client
import ipaddress
from pathlib import Path
import re
import socket
import threading
from urllib.parse import quote, urlsplit, urlunsplit

import requests

from .compatibility import (
    MULTI_MINECRAFT_VERSION,
    minecraft_version_storage,
    normalize_minecraft_versions,
    normalize_modloaders,
    primary_modloader,
    resolved_minecraft_versions,
    version_is_compatible,
)
from .database import Database


class MissingDependencyVersionError(ValueError):
    def __init__(self, dependency_name, minecraft, modloader=None):
        self.dependency_name = dependency_name
        self.minecraft = minecraft
        self.modloader = modloader
        loader_text = f" and modloader {modloader}" if modloader else ""
        super().__init__(
            f'{dependency_name} has no version compatible with Minecraft '
            f'{minecraft}{loader_text}.'
        )


class IncompatibleModVersionError(ValueError):
    """Raised when a version does not match the target build."""


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection whose TCP destination has already been validated."""

    def __init__(
        self,
        hostname,
        port,
        address,
        *,
        connect_timeout=5,
        read_timeout=60,
    ):
        super().__init__(hostname, port=port, timeout=read_timeout)
        self._verified_address = address
        self._connect_timeout = connect_timeout

    def connect(self):
        raw_socket = socket.create_connection(
            (self._verified_address, self.port),
            timeout=self._connect_timeout,
            source_address=self.source_address,
        )
        try:
            # ``self.host`` remains the original hostname, so certificate
            # validation and SNI are not weakened by connecting to a pinned IP.
            self.sock = self._context.wrap_socket(
                raw_socket,
                server_hostname=self.host,
            )
            self.sock.settimeout(self.timeout)
        except Exception:
            raw_socket.close()
            raise


class Modversion:
    JAR_MD5_PATTERN = re.compile(r"^[0-9A-Fa-f]{32}$")
    DOWNLOAD_SOURCE_PROVIDER_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,15}$")
    DOWNLOAD_SOURCE_HASH_PATTERNS = {
        "md5": re.compile(r"^[0-9a-f]{32}$"),
        "sha1": re.compile(r"^[0-9a-f]{40}$"),
        "sha512": re.compile(r"^[0-9a-f]{128}$"),
    }
    MAX_JAR_DOWNLOAD_SIZE = 512 * 1024 * 1024

    def __init__(
        self,
        id,
        mod_id,
        version,
        mcversion,
        md5,
        created_at,
        updated_at,
        filesize,
        optional=0,
        modloader=None,
        integration_version_id=None,
        jarmd5=None,
        jarfilesize=None,
        jar_url_override=None,
        minecraft_versions=None,
    ):
        self.id = id
        self.mod_id = mod_id
        self.version = version
        self.mcversion = (
            MULTI_MINECRAFT_VERSION
            if str(mcversion or "").upper() == MULTI_MINECRAFT_VERSION
            else normalize_minecraft_versions(mcversion)
        )
        self.minecraft_versions = resolved_minecraft_versions(
            self.mcversion, minecraft_versions
        )
        self.mcversion_display = ",".join(self.minecraft_versions) or None
        self.md5 = md5
        self.created_at = created_at
        self.updated_at = updated_at
        self.filesize = filesize
        self.optional = optional
        self.modloader = normalize_modloaders(modloader)
        self.integration_version_id = (
            str(integration_version_id) if integration_version_id else None
        )
        self.jarmd5 = jarmd5
        self.jarfilesize = jarfilesize
        self.jar_url_override = jar_url_override

    @classmethod
    def new(
        cls,
        mod_id,
        version,
        mcversion,
        md5,
        filesize,
        markedbuild,
        repository_base_url="0",
        jarmd5="0",
        modloader=None,
        integration_version_id=None,
        repository_mod_slug=None,
        jarfilesize=None,
        jar_url_override=None,
        download_source=None,
    ):
        if md5 == "0":
            cls.repository_file_source(
                repository_base_url, repository_mod_slug, version
            )
        stored_mcversion, minecraft_versions = minecraft_version_storage(
            mcversion
        )
        modloader = normalize_modloaders(modloader)
        jar_url_override = cls.normalize_jar_url_override(jar_url_override)
        download_source = cls.normalize_download_source(download_source)
        if jar_url_override and not cls.JAR_MD5_PATTERN.fullmatch(
            str(jarmd5 or "").strip()
        ):
            raise ValueError(
                "A JAR override requires a verified JAR MD5."
            )
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        now = datetime.datetime.now()
        try:
            cur.execute(
                """INSERT INTO modversions
                          (mod_id, version, mcversion, modloader,
                           integration_version_id, md5, jarmd5, jarfilesize,
                           created_at, updated_at, filesize)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                           %s)""",
                (
                    mod_id,
                    version,
                    stored_mcversion,
                    modloader,
                    integration_version_id,
                    md5,
                    jarmd5,
                    jarfilesize,
                    now,
                    now,
                    filesize,
                ),
            )
            id = cur.lastrowid
            cls._store_minecraft_versions(
                cur, id, minecraft_versions, replace=False
            )
            if jar_url_override is not None:
                cls._store_jar_url_override(cur, id, jar_url_override)
            if download_source is not None:
                cls._store_download_source(cur, id, download_source)
            cls.promote_parent_mod_for_jar_md5(cur, mod_id, jarmd5)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()
        if str(markedbuild or "0") != "0":
            try:
                Modversion.add_modversion_to_selected_build(
                    id, mod_id, markedbuild, "0", "0"
                )
            except Exception:
                # Do not leave a database row for an upload that could not be
                # attached to its requested marked build.
                Modversion.delete_modversion(id)
                raise
        if md5 == "0":
            stored_version = Modversion.get_by_id(id)
            t = threading.Thread(
                target=Modversion._rehash_and_invalidate,
                args=(stored_version, repository_base_url, repository_mod_slug),
            )
            t.start()
        return cls(
            id,
            mod_id,
            version,
            stored_mcversion,
            md5,
            now,
            now,
            filesize,
            modloader=modloader,
            integration_version_id=integration_version_id,
            jarmd5=jarmd5,
            jarfilesize=jarfilesize,
            jar_url_override=jar_url_override,
            minecraft_versions=minecraft_versions,
        )

    @staticmethod
    def _store_minecraft_versions(
        cur, modversion_id, versions, *, replace=True
    ):
        """Replace normalized multi-version compatibility inside a transaction."""
        if replace:
            cur.execute(
                "DELETE FROM modversion_minecraft_versions "
                "WHERE modversion_id = %s",
                (modversion_id,),
            )
        if len(versions) > 1:
            cur.executemany(
                """INSERT INTO modversion_minecraft_versions
                          (modversion_id, minecraft_version)
                   VALUES (%s, %s)""",
                [(modversion_id, version) for version in versions],
            )

    @staticmethod
    def minecraft_values_from_row(row):
        """Resolve compatibility values on dictionary rows from either layout."""
        if not row:
            return ()
        related = row.get("minecraft_versions_csv")
        return resolved_minecraft_versions(row.get("mcversion"), related)

    @classmethod
    def hydrate_minecraft_row(cls, row):
        if row is None:
            return None
        versions = cls.minecraft_values_from_row(row)
        row["minecraft_versions"] = list(versions)
        row["mcversion_display"] = ",".join(versions) or None
        return row

    @classmethod
    def promote_parent_mod_for_jar_md5(cls, cur, mod_id, jarmd5):
        """Keep the parent package type consistent with raw-JAR detection."""
        if not cls.JAR_MD5_PATTERN.fullmatch(str(jarmd5 or "").strip()):
            return False
        cur.execute(
            """UPDATE mods SET modtype = 'MOD'
               WHERE id = %s
                 AND (modtype IS NULL OR modtype NOT IN ('MOD', 'BOOTSTRAP', 'LAUNCHER'))""",
            (mod_id,),
        )
        return True

    @staticmethod
    def sync_launcher_build_metadata(
        cur, build_id, modtype, version, modloader=None
    ):
        """Copy a legacy launcher package's version onto its target build."""
        if str(modtype or "").strip().upper() != "LAUNCHER":
            return False

        # Launcher versions describe one build loader, even though ordinary
        # package versions may target several loaders.
        modloader = primary_modloader(modloader)
        if modloader:
            cur.execute(
                """UPDATE builds
                   SET forge = %s, modloader = %s
                   WHERE id = %s""",
                (version, modloader, build_id),
            )
        else:
            # Old Technic launcher packages do not have loader metadata. Keep
            # the build's existing loader choice, but still synchronize the
            # legacy `forge` field used as the modloader version by the API.
            cur.execute(
                """UPDATE builds
                   SET forge = %s
                   WHERE id = %s""",
                (version, build_id),
            )
        return True

    @staticmethod
    def add_modversion_to_selected_build(modver_id, mod_id, build_id, marked, optional):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            if marked == "1":
                cur.execute(
                    """SELECT id
                       FROM builds
                       WHERE marked = 1
                       ORDER BY id
                       LIMIT 1
                       FOR UPDATE"""
                )
                marked_build = cur.fetchone()
                if marked_build is None:
                    raise ValueError("No build is currently marked.")
                build_id = marked_build["id"]

            cur.execute(
                """SELECT modversions.mod_id, modversions.version,
                          modversions.mcversion, modversions.modloader,
                          (SELECT GROUP_CONCAT(
                                      compatibility.minecraft_version
                                      ORDER BY compatibility.minecraft_version
                                      SEPARATOR ',')
                             FROM modversion_minecraft_versions compatibility
                            WHERE compatibility.modversion_id = modversions.id)
                              AS minecraft_versions_csv,
                          mods.modtype, builds.minecraft,
                          builds.modloader AS build_modloader
                   FROM modversions
                   INNER JOIN mods ON mods.id = modversions.mod_id
                   INNER JOIN builds ON builds.id = %s
                   WHERE modversions.id = %s
                   FOR UPDATE""",
                (build_id, modver_id),
            )
            selected = cur.fetchone()
            if selected is None:
                raise ValueError("The selected build or mod version no longer exists.")
            if int(mod_id) != selected["mod_id"]:
                raise ValueError("The selected version does not belong to that mod.")
            if not version_is_compatible(
                selected.get("mcversion"),
                selected.get("modloader"),
                selected["minecraft"],
                selected.get("build_modloader"),
                selected.get("minecraft_versions_csv"),
            ):
                raise IncompatibleModVersionError(
                    "The selected version is not compatible with the build's "
                    "Minecraft version and modloader."
                )

            cur.execute(
                """SELECT build_modversion.id
                   FROM build_modversion
                   INNER JOIN modversions
                       ON build_modversion.modversion_id = modversions.id
                   WHERE build_modversion.build_id = %s
                     AND modversions.mod_id = %s
                   ORDER BY build_modversion.id
                   LIMIT 1""",
                (build_id, selected["mod_id"]),
            )
            existing = cur.fetchone()
            if existing is None:
                cur.execute(
                    """INSERT INTO build_modversion
                              (modversion_id, build_id, optional)
                       VALUES (%s, %s, %s)""",
                    (modver_id, build_id, optional),
                )
            else:
                cur.execute(
                    """UPDATE build_modversion
                       SET modversion_id = %s
                       WHERE id = %s""",
                    (modver_id, existing["id"]),
                )

            Modversion.sync_launcher_build_metadata(
                cur,
                build_id,
                selected.get("modtype"),
                selected.get("version"),
                selected.get("modloader"),
            )
            dependency_modloader = selected.get("build_modloader")
            if (
                str(selected.get("modtype") or "").upper() == "LAUNCHER"
                and selected.get("modloader")
            ):
                dependency_modloader = primary_modloader(
                    selected["modloader"]
                )

            added_dependencies = Modversion._add_required_dependencies(
                cur,
                build_id,
                selected["minecraft"],
                selected["mod_id"],
                dependency_modloader,
            )
            conn.commit()
            return added_dependencies
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def _add_required_dependencies(
        cur, build_id, minecraft, root_mod_id, modloader=None
    ):
        cur.execute(
            """SELECT mod_dependencies.mod_id,
                      mod_dependencies.dependency_mod_id,
                      COALESCE(mods.pretty_name, mods.name) AS dependency_name
               FROM mod_dependencies
               LEFT JOIN mods
                   ON mod_dependencies.dependency_mod_id = mods.id
               ORDER BY mod_dependencies.mod_id, mod_dependencies.dependency_mod_id"""
        )
        dependencies_by_mod = {}
        dependency_names = {}
        for relationship in cur.fetchall() or []:
            dependency_mod_id = relationship["dependency_mod_id"]
            dependencies_by_mod.setdefault(relationship["mod_id"], []).append(
                dependency_mod_id
            )
            dependency_names[dependency_mod_id] = (
                relationship["dependency_name"] or f"Mod #{dependency_mod_id}"
            )

        cur.execute(
            """SELECT DISTINCT modversions.mod_id
               FROM build_modversion
               INNER JOIN modversions
                   ON build_modversion.modversion_id = modversions.id
               WHERE build_modversion.build_id = %s""",
            (build_id,),
        )
        present_mod_ids = {row["mod_id"] for row in (cur.fetchall() or [])}

        pending = deque(dependencies_by_mod.get(root_mod_id, ()))
        visited = {root_mod_id}
        added_dependencies = []
        while pending:
            dependency_mod_id = pending.popleft()
            if dependency_mod_id in visited:
                continue
            visited.add(dependency_mod_id)
            pending.extend(dependencies_by_mod.get(dependency_mod_id, ()))

            if dependency_mod_id in present_mod_ids:
                continue

            cur.execute(
                """SELECT id
                   FROM modversions
                   WHERE mod_id = %s
                      AND (
                          mcversion IS NULL
                          OR mcversion = %s
                          OR FIND_IN_SET(%s, mcversion) > 0
                          OR (
                              mcversion = 'MULTI'
                              AND EXISTS (
                                  SELECT 1
                                  FROM modversion_minecraft_versions compatibility
                                  WHERE compatibility.modversion_id = modversions.id
                                    AND compatibility.minecraft_version = %s
                              )
                          )
                      )
                      AND (%s IS NULL OR FIND_IN_SET(%s, modloader) > 0
                           OR modloader IS NULL)
                    ORDER BY CASE WHEN mcversion = %s
                                            OR FIND_IN_SET(%s, mcversion) > 0
                                            OR EXISTS (
                                                SELECT 1
                                                FROM modversion_minecraft_versions compatibility
                                                WHERE compatibility.modversion_id = modversions.id
                                                  AND compatibility.minecraft_version = %s
                                            )
                                  THEN 0 ELSE 1 END,
                             CASE WHEN FIND_IN_SET(%s, modloader) > 0
                                  THEN 0 ELSE 1 END,
                            id DESC
                   LIMIT 1""",
                (
                    dependency_mod_id,
                    minecraft,
                    minecraft,
                    minecraft,
                    modloader,
                    modloader,
                    minecraft,
                    minecraft,
                    minecraft,
                    modloader,
                ),
            )
            dependency_version = cur.fetchone()
            if dependency_version is None:
                raise MissingDependencyVersionError(
                    dependency_names.get(
                        dependency_mod_id, f"Mod #{dependency_mod_id}"
                    ),
                    minecraft,
                    modloader,
                )

            cur.execute(
                """INSERT INTO build_modversion
                          (modversion_id, build_id, optional)
                   VALUES (%s, %s, 0)""",
                (dependency_version["id"], build_id),
            )
            present_mod_ids.add(dependency_mod_id)
            added_dependencies.append(dependency_names[dependency_mod_id])

        return added_dependencies

    @staticmethod
    def update_modversion_in_build(oldmodver_id, modver_id, build_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT replacement.mod_id, replacement.version,
                          replacement.mcversion, replacement.modloader,
                          (SELECT GROUP_CONCAT(
                                      compatibility.minecraft_version
                                      ORDER BY compatibility.minecraft_version
                                      SEPARATOR ',')
                             FROM modversion_minecraft_versions compatibility
                            WHERE compatibility.modversion_id = replacement.id)
                              AS minecraft_versions_csv,
                          mods.modtype,
                          current.mod_id AS current_mod_id,
                          builds.minecraft,
                          builds.modloader AS build_modloader
                   FROM modversions AS replacement
                   INNER JOIN modversions AS current ON current.id = %s
                   INNER JOIN mods ON mods.id = replacement.mod_id
                   INNER JOIN builds ON builds.id = %s
                   WHERE replacement.id = %s""",
                (oldmodver_id, build_id, modver_id),
            )
            selected = cur.fetchone()
            if selected is None:
                raise ValueError("The selected build or mod version no longer exists.")
            if selected["mod_id"] != selected["current_mod_id"]:
                raise ValueError("The replacement version belongs to another mod.")
            if not version_is_compatible(
                selected.get("mcversion"),
                selected.get("modloader"),
                selected["minecraft"],
                selected.get("build_modloader"),
                selected.get("minecraft_versions_csv"),
            ):
                raise IncompatibleModVersionError(
                    "The selected version is not compatible with the build's "
                    "Minecraft version and modloader."
                )
            cur.execute(
                """UPDATE build_modversion
                   SET modversion_id = %s
                   WHERE modversion_id = %s AND build_id = %s""",
                (modver_id, oldmodver_id, build_id),
            )
            Modversion.sync_launcher_build_metadata(
                cur,
                build_id,
                selected.get("modtype"),
                selected.get("version"),
                selected.get("modloader"),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def update_modversion_jarmd5(id, jarmd5, jarfilesize=None):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """UPDATE modversions
                   SET jarmd5 = %s, jarfilesize = %s
                   WHERE id = %s""",
                (jarmd5, jarfilesize, id),
            )
            if Modversion.JAR_MD5_PATTERN.fullmatch(str(jarmd5 or "").strip()):
                cur.execute(
                    """UPDATE mods
                       INNER JOIN modversions ON modversions.mod_id = mods.id
                       SET mods.modtype = 'MOD'
                       WHERE modversions.id = %s
                         AND (mods.modtype IS NULL
                              OR mods.modtype NOT IN
                                 ('MOD', 'BOOTSTRAP', 'LAUNCHER'))""",
                    (id,),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def normalize_jar_url_override(value):
        value = str(value or "").strip()
        if not value:
            return None
        if len(value) > 2048:
            raise ValueError("The JAR override URL is too long.")
        try:
            parsed = urlsplit(value)
            parsed_port = parsed.port
        except ValueError as error:
            raise ValueError("Enter a valid HTTPS JAR override URL.") from error
        if (
            parsed.scheme.lower() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or "\\" in value
            or any(ord(character) <= 32 for character in value)
            or (parsed_port is not None and not 1 <= parsed_port <= 65535)
        ):
            raise ValueError("Enter a valid HTTPS JAR override URL.")
        try:
            literal_address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            literal_address = None
        if literal_address is not None and not literal_address.is_global:
            raise ValueError("The JAR override URL must use a public host.")
        return value

    @staticmethod
    def _require_public_url_destination(value, *, resolver=None):
        """Resolve an override host and return only verified public addresses."""
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port or 443
        resolver = resolver or socket.getaddrinfo
        try:
            addresses = {
                entry[4][0].split("%", 1)[0]
                for entry in resolver(
                    hostname,
                    port,
                    type=socket.SOCK_STREAM,
                )
            }
        except OSError as error:
            raise ValueError(
                "The JAR override host could not be resolved."
            ) from error
        if not addresses:
            raise ValueError("The JAR override host could not be resolved.")
        try:
            public = all(
                ipaddress.ip_address(address).is_global for address in addresses
            )
        except ValueError as error:
            raise ValueError(
                "The JAR override host returned an invalid address."
            ) from error
        if not public:
            raise ValueError("The JAR override URL must use a public host.")
        return tuple(
            sorted(
                addresses,
                key=lambda address: (
                    ipaddress.ip_address(address).version,
                    ipaddress.ip_address(address).packed,
                ),
            )
        )

    @staticmethod
    def _open_verified_https_response(value, addresses):
        """Open an HTTPS response without resolving the hostname a second time."""
        parsed = urlsplit(value)
        hostname = parsed.hostname.encode("idna").decode("ascii")
        port = parsed.port or 443
        request_target = quote(
            parsed.path or "/",
            safe="/%:@!$&'()*+,;=-._~",
        )
        if parsed.query:
            request_target += "?" + quote(
                parsed.query,
                safe="=&?/:;+,%@!$'()*-._~",
            )

        try:
            host_is_ipv6 = ipaddress.ip_address(hostname).version == 6
        except ValueError:
            host_is_ipv6 = False
        host_header = f"[{hostname}]" if host_is_ipv6 else hostname
        if port != 443:
            host_header = f"{host_header}:{port}"

        last_error = None
        for address in addresses:
            # The complete DNS result was rejected unless every address was
            # public; this connection receives one of those literal IPs.
            # codeql[py/full-ssrf]
            connection = _PinnedHTTPSConnection(
                hostname,
                port,
                address,
                connect_timeout=5,
                read_timeout=60,
            )
            try:
                # The destination is a validated public IP. Only the escaped
                # origin-form path remains user-selectable here.
                # codeql[py/partial-ssrf]
                connection.request(
                    "GET",
                    request_target,
                    headers={
                        "Host": host_header,
                        "Accept-Encoding": "identity",
                        "User-Agent": "solder.py jar verifier",
                    },
                )
                return connection, connection.getresponse()
            except (OSError, http.client.HTTPException) as error:
                last_error = error
                connection.close()
        raise ValueError("The override JAR could not be downloaded.") from last_error

    @classmethod
    def verify_jar_url_override(
        cls, value, expected_md5, *, resolver=None
    ):
        """Download an override once and verify its Solder JAR checksum."""
        value = cls.normalize_jar_url_override(value)
        expected_md5 = str(expected_md5 or "").strip().lower()
        if value is None:
            raise ValueError("The JAR override URL is required.")
        if cls.JAR_MD5_PATTERN.fullmatch(expected_md5) is None:
            raise ValueError("A JAR override requires a verified JAR MD5.")
        addresses = cls._require_public_url_destination(value, resolver=resolver)

        connection = None
        response = None
        try:
            connection, response = cls._open_verified_https_response(
                value,
                addresses,
            )
            if 300 <= response.status < 400:
                raise ValueError("The override JAR URL must not redirect.")
            if response.status >= 400:
                raise ValueError("The override JAR could not be downloaded.")

            content_length = response.getheader("content-length")
            if content_length not in {None, ""}:
                try:
                    content_length = int(content_length)
                except (TypeError, ValueError) as error:
                    raise ValueError(
                        "The override JAR returned an invalid file size."
                    ) from error
                if content_length < 0:
                    raise ValueError(
                        "The override JAR returned an invalid file size."
                    )
                if content_length > cls.MAX_JAR_DOWNLOAD_SIZE:
                    raise ValueError(
                        "The override JAR exceeds the 512 MiB verification limit."
                    )

            digest = hashlib.md5(usedforsecurity=False)
            filesize = 0
            try:
                while chunk := response.read(1024 * 1024):
                    if not chunk:
                        continue
                    filesize += len(chunk)
                    if filesize > cls.MAX_JAR_DOWNLOAD_SIZE:
                        raise ValueError(
                            "The override JAR exceeds the 512 MiB verification limit."
                        )
                    digest.update(chunk)
            except (OSError, http.client.HTTPException) as error:
                raise ValueError(
                    "The override JAR could not be downloaded."
                ) from error

            if not hmac.compare_digest(digest.hexdigest(), expected_md5):
                raise ValueError(
                    "The override JAR MD5 does not match the stored JAR MD5."
                )
            return filesize
        finally:
            if response is not None:
                response.close()
            if connection is not None:
                connection.close()

    @staticmethod
    def _store_jar_url_override(cur, modversion_id, value):
        if value is None:
            cur.execute(
                "DELETE FROM modversion_download_overrides "
                "WHERE modversion_id = %s",
                (modversion_id,),
            )
            return
        cur.execute(
            """INSERT INTO modversion_download_overrides
                      (modversion_id, jar_url)
               VALUES (%s, %s)
               ON DUPLICATE KEY UPDATE
                   jar_url = VALUES(jar_url),
                   updated_at = CURRENT_TIMESTAMP""",
            (modversion_id, value),
        )

    @classmethod
    def normalize_download_source(cls, source):
        """Validate persistent metadata for an integration-hosted JAR."""
        if source is None:
            return None
        if not isinstance(source, dict):
            raise ValueError("The download source is invalid.")

        provider = str(source.get("provider") or "").strip().upper()
        if not cls.DOWNLOAD_SOURCE_PROVIDER_PATTERN.fullmatch(provider):
            raise ValueError("The download source provider is invalid.")
        url = cls.normalize_jar_url_override(source.get("url"))
        if url is None:
            raise ValueError("The download source URL is required.")
        filename = str(source.get("filename") or "").strip()
        if (
            not filename
            or len(filename) > 255
            or filename in {".", ".."}
            or "/" in filename
            or "\\" in filename
            or not filename.casefold().endswith(".jar")
        ):
            raise ValueError("The download source filename is invalid.")

        normalized = {
            "provider": provider,
            "url": url,
            "filename": filename,
        }
        for algorithm, pattern in cls.DOWNLOAD_SOURCE_HASH_PATTERNS.items():
            value = str(source.get(algorithm) or "").strip().lower()
            if value and pattern.fullmatch(value) is None:
                raise ValueError(
                    f"The download source {algorithm.upper()} is invalid."
                )
            normalized[algorithm] = value or None

        filesize = source.get("filesize")
        if filesize is None or filesize == "":
            normalized["filesize"] = None
        else:
            try:
                filesize = int(filesize)
            except (TypeError, ValueError) as error:
                raise ValueError(
                    "The download source file size is invalid."
                ) from error
            if filesize < 0 or filesize > 18446744073709551615:
                raise ValueError("The download source file size is invalid.")
            normalized["filesize"] = filesize
        return normalized

    @staticmethod
    def _store_download_source(cur, modversion_id, source):
        cur.execute(
            """INSERT INTO modversion_download_sources
                      (modversion_id, provider, url, filename, md5, sha1,
                       sha512, filesize)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE
                   url = VALUES(url), filename = VALUES(filename),
                   md5 = VALUES(md5), sha1 = VALUES(sha1),
                   sha512 = VALUES(sha512), filesize = VALUES(filesize),
                   updated_at = CURRENT_TIMESTAMP""",
            (
                modversion_id,
                source["provider"],
                source["url"],
                source["filename"],
                source["md5"],
                source["sha1"],
                source["sha512"],
                source["filesize"],
            ),
        )

    @classmethod
    def store_download_source(cls, modversion_id, source):
        source = cls.normalize_download_source(source)
        if source is None:
            raise ValueError("The download source is required.")
        conn = Database.get_connection()
        cur = conn.cursor()
        try:
            cls._store_download_source(cur, modversion_id, source)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @classmethod
    def update_jar_url_override(cls, id, mod_id, value):
        value = cls.normalize_jar_url_override(value)
        expected_md5 = None
        verified_filesize = None
        if value is not None:
            lookup = Database.get_connection()
            lookup_cur = lookup.cursor(dictionary=True)
            try:
                lookup_cur.execute(
                    """SELECT jarmd5 FROM modversions
                       WHERE id = %s AND mod_id = %s""",
                    (id, mod_id),
                )
                row = lookup_cur.fetchone()
                if row is None:
                    raise ValueError(
                        "The selected mod version no longer exists."
                    )
                expected_md5 = (
                    row.get("jarmd5") if isinstance(row, dict) else row[0]
                )
            finally:
                lookup_cur.close()
                lookup.close()
            verified_filesize = cls.verify_jar_url_override(
                value, expected_md5
            )

        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT jarmd5 FROM modversions
                   WHERE id = %s AND mod_id = %s FOR UPDATE""",
                (id, mod_id),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError(
                    "The selected mod version no longer exists."
                )
            jarmd5 = row.get("jarmd5") if isinstance(row, dict) else row[0]
            if value:
                current_md5 = str(jarmd5 or "").strip().lower()
                if (
                    cls.JAR_MD5_PATTERN.fullmatch(current_md5) is None
                    or not hmac.compare_digest(
                        current_md5, str(expected_md5).strip().lower()
                    )
                ):
                    raise ValueError(
                        "The stored JAR MD5 changed during override verification."
                    )
                cur.execute(
                    """UPDATE modversions SET jarfilesize = %s
                       WHERE id = %s AND mod_id = %s""",
                    (verified_filesize, id, mod_id),
                )
            cls._store_jar_url_override(cur, id, value)
            conn.commit()
            return value
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def delete_modversion(id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        from .advanced_optional import AdvancedOptional

        AdvancedOptional.delete_modversion_memberships(cur, [id])
        cur.execute(
            "DELETE FROM modversion_download_overrides WHERE modversion_id = %s",
            (id,),
        )
        cur.execute(
            "DELETE FROM modversion_download_sources WHERE modversion_id = %s",
            (id,),
        )
        cur.execute(
            "DELETE FROM modversion_minecraft_versions WHERE modversion_id = %s",
            (id,),
        )
        cur.execute("DELETE FROM modversions WHERE id=%s", (id,))
        cur.execute("DELETE FROM build_modversion WHERE modversion_id = %s", (id,))
        conn.commit()
        return None

    @classmethod
    def get_by_id(cls, id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT modversions.*,
                          modversion_download_overrides.jar_url
                              AS jar_url_override,
                          (SELECT GROUP_CONCAT(
                                      compatibility.minecraft_version
                                      ORDER BY compatibility.minecraft_version
                                      SEPARATOR ',')
                             FROM modversion_minecraft_versions compatibility
                            WHERE compatibility.modversion_id = modversions.id)
                              AS minecraft_versions_csv
                   FROM modversions
                   LEFT JOIN modversion_download_overrides
                       ON modversion_download_overrides.modversion_id =
                          modversions.id
                   WHERE modversions.id = %s""",
                (id,),
            )
            row = cur.fetchone()
            if row:
                return cls(
                    row["id"], row["mod_id"], row["version"],
                    row["mcversion"], row["md5"], row["created_at"],
                    row["updated_at"], row["filesize"],
                    modloader=row.get("modloader"),
                    integration_version_id=row.get("integration_version_id"),
                    jarmd5=row.get("jarmd5"),
                    jarfilesize=row.get("jarfilesize"),
                    jar_url_override=row.get("jar_url_override"),
                    minecraft_versions=row.get("minecraft_versions_csv"),
                )
            return None
        finally:
            cur.close()
            conn.close()

    @classmethod
    def get_by_integration(cls, mod_id, integration_version_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT modversions.*,
                          (SELECT GROUP_CONCAT(
                                      compatibility.minecraft_version
                                      ORDER BY compatibility.minecraft_version
                                      SEPARATOR ',')
                             FROM modversion_minecraft_versions compatibility
                            WHERE compatibility.modversion_id = modversions.id)
                              AS minecraft_versions_csv
                   FROM modversions
                   WHERE mod_id = %s AND integration_version_id = %s""",
                (mod_id, str(integration_version_id)),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return cls(
                row["id"], row["mod_id"], row["version"], row["mcversion"],
                row["md5"], row["created_at"], row["updated_at"],
                row["filesize"], modloader=row.get("modloader"),
                integration_version_id=row.get("integration_version_id"),
                jarmd5=row.get("jarmd5"),
                jarfilesize=row.get("jarfilesize"),
                jar_url_override=row.get("jar_url_override"),
                minecraft_versions=row.get("minecraft_versions_csv"),
            )
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def version_exists(mod_id, version):
        conn = Database.get_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT 1 FROM modversions WHERE mod_id = %s AND version = %s LIMIT 1",
                (mod_id, version),
            )
            return cur.fetchone() is not None
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def get_integration_version_ids(mod_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT integration_version_id FROM modversions
                   WHERE mod_id = %s AND integration_version_id IS NOT NULL""",
                (mod_id,),
            )
            return {
                str(row["integration_version_id"])
                for row in cur.fetchall()
            }
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def get_all():
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """SELECT id, mod_id, version, mcversion, modloader,
                      (SELECT GROUP_CONCAT(
                                  compatibility.minecraft_version
                                  ORDER BY compatibility.minecraft_version
                                  SEPARATOR ',')
                         FROM modversion_minecraft_versions compatibility
                        WHERE compatibility.modversion_id = modversions.id)
                          AS minecraft_versions_csv
               FROM modversions"""
        )
        rows = cur.fetchall()
        if rows:
            return [Modversion.hydrate_minecraft_row(row) for row in rows]
        return []

    def get_builds_api(self, cid=None, api_key=False, modpack_ids=None):
        """List published builds containing this version that the caller can read."""
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            if api_key:
                cur.execute(
                    """SELECT DISTINCT
                              builds.id AS build_id,
                              builds.version AS build_version,
                              modpacks.id AS modpack_id,
                              modpacks.slug AS modpack_slug,
                              modpacks.name AS modpack_name,
                              build_modversion.optional
                       FROM build_modversion
                       INNER JOIN builds
                           ON build_modversion.build_id = builds.id
                       INNER JOIN modpacks
                           ON builds.modpack_id = modpacks.id
                       WHERE build_modversion.modversion_id = %s
                         AND build_modversion.optional IN (0, 1)
                         AND builds.is_published = 1
                       ORDER BY builds.id ASC""",
                    (self.id,),
                )
            elif modpack_ids:
                placeholders = ", ".join(["%s"] * len(modpack_ids))
                # Only the number of bound placeholders is dynamic.
                cur.execute(
                    f"""SELECT DISTINCT
                              builds.id AS build_id,
                              builds.version AS build_version,
                              modpacks.id AS modpack_id,
                              modpacks.slug AS modpack_slug,
                              modpacks.name AS modpack_name,
                              build_modversion.optional
                       FROM build_modversion
                       INNER JOIN builds
                           ON build_modversion.build_id = builds.id
                       INNER JOIN modpacks
                           ON builds.modpack_id = modpacks.id
                       WHERE build_modversion.modversion_id = %s
                         AND build_modversion.optional IN (0, 1)
                         AND builds.is_published = 1
                         AND (
                              (modpacks.private = 0 AND builds.private = 0)
                              OR modpacks.id IN ({placeholders})
                              OR EXISTS (
                                  SELECT 1
                                  FROM client_modpack cm
                                  INNER JOIN clients c ON cm.client_id = c.id
                                  WHERE cm.modpack_id = modpacks.id
                                    AND c.uuid = %s
                              )
                         )
                       ORDER BY builds.id ASC""",  # nosec B608
                    (self.id, *modpack_ids, cid),
                )
            else:
                cur.execute(
                    """SELECT DISTINCT
                              builds.id AS build_id,
                              builds.version AS build_version,
                              modpacks.id AS modpack_id,
                              modpacks.slug AS modpack_slug,
                              modpacks.name AS modpack_name,
                              build_modversion.optional
                       FROM build_modversion
                       INNER JOIN builds
                           ON build_modversion.build_id = builds.id
                       INNER JOIN modpacks
                           ON builds.modpack_id = modpacks.id
                       WHERE build_modversion.modversion_id = %s
                         AND build_modversion.optional IN (0, 1)
                         AND builds.is_published = 1
                         AND (
                              (modpacks.private = 0 AND builds.private = 0)
                              OR EXISTS (
                                  SELECT 1
                                  FROM client_modpack cm
                                  INNER JOIN clients c ON cm.client_id = c.id
                                  WHERE cm.modpack_id = modpacks.id
                                    AND c.uuid = %s
                              )
                         )
                       ORDER BY builds.id ASC""",
                    (self.id, cid),
                )
            return [
                {
                    "id": row["build_id"],
                    "version": row["build_version"],
                    "optional": int(row["optional"] or 0) == 1,
                    "modpack": {
                        "id": row["modpack_id"],
                        "name": row["modpack_slug"],
                        "display_name": row["modpack_name"],
                    },
                }
                for row in cur.fetchall()
            ]
        finally:
            cur.close()
            conn.close()

    def get_management_builds(self):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT build_modversion.id AS membership_id,
                          build_modversion.optional,
                          builds.id AS build_id,
                          builds.version AS build_version,
                          builds.is_published,
                          modpacks.id AS modpack_id,
                          modpacks.name AS modpack_name,
                          modpacks.slug AS modpack_slug
                   FROM build_modversion
                   INNER JOIN builds
                       ON build_modversion.build_id = builds.id
                   INNER JOIN modpacks
                       ON builds.modpack_id = modpacks.id
                   WHERE build_modversion.modversion_id = %s
                   ORDER BY modpacks.name, builds.id""",
                (self.id,),
            )
            return cur.fetchall() or []
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def _repository_component(component, label):
        component = "" if component is None else str(component)
        if (
            component in {".", ".."}
            or re.fullmatch(r"[^/\\\x00-\x1f]+", component) is None
        ):
            raise ValueError(f"Invalid repository {label}.")
        return component

    @classmethod
    def repository_artifact_source(
        cls, repository_location, mod_slug, filename
    ):
        """Return an authorized HTTP URL or confined local repository path."""
        if not repository_location:
            raise ValueError("MD5_REPO_LOCATION is not configured.")

        mod_slug = cls._repository_component(mod_slug, "mod slug")
        filename = cls._repository_component(filename, "filename")
        location = str(repository_location)
        base = urlsplit(location)
        if base.scheme in {"http", "https"}:
            if (
                not base.hostname
                or base.username is not None
                or base.password is not None
                or base.query
                or base.fragment
            ):
                raise ValueError(
                    "MD5_REPO_LOCATION must be a plain HTTP(S) URL."
                )
            repository_path = base.path.rstrip("/")
            file_path = (
                f"{repository_path}/{quote(mod_slug, safe='-._~')}/"
                f"{quote(filename, safe='-._~')}"
            )
            return urlunsplit(
                (base.scheme, base.netloc, file_path, "", "")
            )

        if "://" in location:
            raise ValueError(
                "MD5_REPO_LOCATION must be an HTTP(S) URL or local path."
            )

        repository_root = Path(location).expanduser().resolve()
        source_path = (repository_root / mod_slug / filename).resolve()
        try:
            source_path.relative_to(repository_root)
        except ValueError as error:
            raise ValueError("Invalid local repository path.") from error
        return source_path

    @classmethod
    def repository_file_source(
        cls, repository_location, mod_slug, version
    ):
        mod_slug = cls._repository_component(mod_slug, "mod slug")
        version = cls._repository_component(version, "version")
        return cls.repository_artifact_source(
            repository_location,
            mod_slug,
            f"{mod_slug}-{version}.zip",
        )

    @staticmethod
    def get_file_size(repository_location, mod_slug, version):
        source = Modversion.repository_file_source(
            repository_location, mod_slug, version
        )
        if isinstance(source, Path):
            return source.stat().st_size

        response = requests.head(
            source,
            allow_redirects=False,
            timeout=(5, 30),
        )
        try:
            if 300 <= response.status_code < 400:
                raise requests.RequestException(
                    "Repository redirects are not allowed."
                )
            response.raise_for_status()
            try:
                file_size = int(response.headers.get("content-length", -1))
            except (TypeError, ValueError):
                return -1
            return file_size if file_size >= 0 else -1
        finally:
            response.close()

    def update_hash(self, md5, repository_location, mod_slug, file_size=None):
        conn = Database.get_connection()
        cur = conn.cursor()
        try:
            if file_size is None:
                file_size = Modversion.get_file_size(
                    repository_location, mod_slug, self.version
                )
            if file_size == -1:
                cur.execute(
                    "UPDATE modversions SET md5 = %s WHERE id = %s",
                    (md5, self.id),
                )
            else:
                cur.execute(
                    """UPDATE modversions
                       SET md5 = %s, filesize = %s
                       WHERE id = %s""",
                    (md5, file_size, self.id),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()
        self.md5 = md5
        if file_size != -1:
            self.filesize = file_size
        self.updated_at = datetime.datetime.now()
        print(f"Updated hash for {self.mod_id} {self.version} to {md5}")
        return self

    @staticmethod
    def _rehash_and_invalidate(version, repository_location, mod_slug):
        version.rehash(repository_location, mod_slug)
        from .cache_revision import CacheRevision

        CacheRevision.bump()

    def rehash(self, repository_location, mod_slug):
        source = Modversion.repository_file_source(
            repository_location, mod_slug, self.version
        )
        # Technic/Solder manifests require MD5 as a file checksum. It is not
        # used for passwords, signatures, or another security purpose.
        h = hashlib.md5(usedforsecurity=False)
        file_size = 0
        if isinstance(source, Path):
            with source.open("rb") as repository_file:
                while chunk := repository_file.read(8192):
                    h.update(chunk)
                    file_size += len(chunk)
        else:
            with requests.Session() as session:
                with session.get(
                    source,
                    stream=True,
                    allow_redirects=False,
                    timeout=(5, 60),
                ) as response:
                    if 300 <= response.status_code < 400:
                        raise requests.RequestException(
                            "Repository redirects are not allowed."
                        )
                    response.raise_for_status()
                    for chunk in response.iter_content(chunk_size=8192):
                        h.update(chunk)
                        file_size += len(chunk)
        return self.update_hash(
            h.hexdigest(),
            repository_location,
            mod_slug,
            file_size=file_size,
        )

    def to_json(self):
        return {
            "mod_id": self.mod_id,
            "version": self.version,
            "md5": self.md5,
            "filesize": self.filesize,
        }
