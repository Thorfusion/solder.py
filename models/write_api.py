"""Transactional persistence used by the optional authenticated write API."""

from contextlib import contextmanager

from mysql.connector import IntegrityError, errorcode

from .compatibility import version_is_compatible
from .database import Database
from .modversion import Modversion


_WRITABLE_FIELDS = {
    "modpacks": {
        "name", "slug", "user_id", "recommended", "latest", "order",
        "hidden", "private", "pinned", "enable_optionals", "enable_server",
    },
    "builds": {
        "modpack_id", "version", "minecraft", "forge", "modloader",
        "is_published", "private", "min_java", "min_memory", "marked",
    },
    "mods": {
        "name", "pretty_name", "description", "author", "link", "notes",
        "side", "modtype", "integration_provider", "integration_project_id",
    },
    "modversions": {
        "mod_id", "version", "mcversion", "modloader", "md5", "jarmd5",
        "filesize", "integration_version_id",
    },
    "clients": {"name", "uuid"},
}


class WriteApiProblem(ValueError):
    def __init__(self, message, status=422):
        self.status = status
        super().__init__(message)


@contextmanager
def _transaction():
    conn = Database.get_connection()
    cur = conn.cursor(dictionary=True)
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


class WriteApiStore:
    @staticmethod
    def _one(query, parameters=()):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(query, parameters)
            return cur.fetchone()
        finally:
            cur.close()
            conn.close()

    @classmethod
    def get_modpack(cls, slug):
        return cls._one("SELECT * FROM modpacks WHERE slug = %s", (slug,))

    @classmethod
    def get_build(cls, modpack_id, version):
        return cls._one(
            "SELECT * FROM builds WHERE modpack_id = %s AND version = %s",
            (modpack_id, version),
        )

    @classmethod
    def get_mod(cls, slug):
        return cls._one("SELECT * FROM mods WHERE name = %s", (slug,))

    @classmethod
    def get_modversion(cls, mod_id, version):
        return cls._one(
            "SELECT * FROM modversions WHERE mod_id = %s AND version = %s",
            (mod_id, version),
        )

    @classmethod
    def get_client(cls, uuid):
        return cls._one("SELECT * FROM clients WHERE uuid = %s", (uuid,))

    @staticmethod
    def _insert(cur, table, values):
        columns = list(values)
        if table not in _WRITABLE_FIELDS or not set(columns).issubset(
            _WRITABLE_FIELDS[table]
        ):
            raise WriteApiProblem("Unsupported database write.", 500)
        placeholders = ", ".join(["%s"] * len(columns))
        quoted_columns = ", ".join(
            f"`{column}`" if column == "order" else column for column in columns
        )
        # Identifiers are allowlisted above and all values remain bound.
        cur.execute(
            f"INSERT INTO {table} ({quoted_columns}) VALUES ({placeholders})",  # nosec B608
            tuple(values[column] for column in columns),
        )
        return cur.lastrowid

    @staticmethod
    def _update(cur, table, row_id, values):
        if not values:
            return
        if table not in _WRITABLE_FIELDS or not set(values).issubset(
            _WRITABLE_FIELDS[table]
        ):
            raise WriteApiProblem("Unsupported database write.", 500)
        assignments = ", ".join(
            f"`{column}` = %s" if column == "order" else f"{column} = %s"
            for column in values
        )
        # Identifiers are allowlisted above and all values remain bound.
        cur.execute(
            f"UPDATE {table} SET {assignments} WHERE id = %s",  # nosec B608
            (*values.values(), row_id),
        )

    @staticmethod
    def _duplicate(error, message):
        if isinstance(error, IntegrityError) and error.errno == errorcode.ER_DUP_ENTRY:
            raise WriteApiProblem(message) from error
        raise error

    @staticmethod
    def _grant_modpack_access(cur, user_id, modpack_id):
        """Keep solder.py's relation and Technic's CSV permission in sync."""
        cur.execute(
            """INSERT INTO user_modpack (user_id, modpack_id)
               VALUES (%s, %s)""",
            (user_id, modpack_id),
        )
        cur.execute(
            """UPDATE user_permissions
               SET modpacks = CASE
                   WHEN FIND_IN_SET(%s, COALESCE(modpacks, '')) > 0 THEN modpacks
                   WHEN COALESCE(modpacks, '') = '' THEN CAST(%s AS CHAR)
                   ELSE CONCAT(modpacks, ',', %s)
               END
               WHERE user_id = %s""",
            (modpack_id, modpack_id, modpack_id, user_id),
        )

    @staticmethod
    def _revoke_modpack_access(cur, modpack_id):
        cur.execute(
            """UPDATE user_permissions
               SET modpacks = TRIM(BOTH ',' FROM REPLACE(
                   CONCAT(',', COALESCE(modpacks, ''), ','),
                   CONCAT(',', %s, ','),
                   ','
               ))""",
            (modpack_id,),
        )

    @classmethod
    def create_modpack(cls, values, user_id):
        try:
            with _transaction() as cur:
                modpack_id = cls._insert(cur, "modpacks", {**values, "user_id": user_id})
                cls._grant_modpack_access(cur, user_id, modpack_id)
                cur.execute("SELECT * FROM modpacks WHERE id = %s", (modpack_id,))
                return cur.fetchone()
        except IntegrityError as error:
            cls._duplicate(error, "A modpack with that name or slug already exists.")

    @classmethod
    def update_modpack(cls, modpack, values):
        try:
            with _transaction() as cur:
                cls._update(cur, "modpacks", modpack["id"], values)
                cur.execute("SELECT * FROM modpacks WHERE id = %s", (modpack["id"],))
                return cur.fetchone()
        except IntegrityError as error:
            cls._duplicate(error, "A modpack with that name or slug already exists.")

    @classmethod
    def clone_modpack(cls, source, values, user_id):
        try:
            with _transaction() as cur:
                new_values = {
                    "name": values["name"],
                    "slug": values["slug"],
                    "user_id": user_id,
                    "recommended": source.get("recommended"),
                    "latest": source.get("latest"),
                    "order": values.get("order", source.get("order", 0)),
                    "hidden": values.get("hidden", 0),
                    "private": values.get("private", source.get("private", 0)),
                    "pinned": values.get("pinned", 0),
                    "enable_optionals": values.get(
                        "enable_optionals", source.get("enable_optionals", 0)
                    ),
                    "enable_server": values.get(
                        "enable_server", source.get("enable_server", 0)
                    ),
                }
                modpack_id = cls._insert(cur, "modpacks", new_values)
                cls._grant_modpack_access(cur, user_id, modpack_id)
                cur.execute(
                    "SELECT * FROM builds WHERE modpack_id = %s ORDER BY id",
                    (source["id"],),
                )
                for build in cur.fetchall() or []:
                    old_build_id = build.pop("id")
                    build.pop("created_at", None)
                    build.pop("updated_at", None)
                    build["modpack_id"] = modpack_id
                    new_build_id = cls._insert(cur, "builds", build)
                    cur.execute(
                        """SELECT modversion_id, optional
                           FROM build_modversion WHERE build_id = %s
                           ORDER BY id""",
                        (old_build_id,),
                    )
                    memberships = cur.fetchall() or []
                    if memberships:
                        cur.executemany(
                            """INSERT INTO build_modversion
                                      (modversion_id, build_id, optional)
                               VALUES (%s, %s, %s)""",
                            [
                                (membership["modversion_id"], new_build_id,
                                 membership.get("optional", 0))
                                for membership in memberships
                            ],
                        )
                cur.execute("SELECT * FROM modpacks WHERE id = %s", (modpack_id,))
                return cur.fetchone()
        except IntegrityError as error:
            cls._duplicate(error, "A modpack with that name or slug already exists.")

    @staticmethod
    def delete_modpack(modpack_id):
        with _transaction() as cur:
            cur.execute("SELECT id FROM builds WHERE modpack_id = %s", (modpack_id,))
            build_ids = [row["id"] for row in (cur.fetchall() or [])]
            if build_ids:
                placeholders = ", ".join(["%s"] * len(build_ids))
                # Only the number of bound placeholders is dynamic.
                cur.execute(
                    f"DELETE FROM build_modversion WHERE build_id IN ({placeholders})",  # nosec B608
                    tuple(build_ids),
                )
            cur.execute("DELETE FROM builds WHERE modpack_id = %s", (modpack_id,))
            cur.execute("DELETE FROM client_modpack WHERE modpack_id = %s", (modpack_id,))
            cur.execute("DELETE FROM user_modpack WHERE modpack_id = %s", (modpack_id,))
            WriteApiStore._revoke_modpack_access(cur, modpack_id)
            cur.execute("DELETE FROM modpacks WHERE id = %s", (modpack_id,))

    @classmethod
    def create_build(cls, modpack_id, values, clone_source=None):
        with _transaction() as cur:
            cur.execute(
                """SELECT id FROM builds
                   WHERE modpack_id = %s AND version = %s FOR UPDATE""",
                (modpack_id, values["version"]),
            )
            if cur.fetchone():
                raise WriteApiProblem("Build version already exists for this modpack.")
            build_id = cls._insert(cur, "builds", {"modpack_id": modpack_id, **values})
            if clone_source:
                cur.execute(
                    """INSERT INTO build_modversion
                              (modversion_id, build_id, optional)
                       SELECT modversion_id, %s, optional
                       FROM build_modversion WHERE build_id = %s""",
                    (build_id, clone_source["id"]),
                )
            cur.execute("SELECT * FROM builds WHERE id = %s", (build_id,))
            return cur.fetchone()

    @classmethod
    def update_build(cls, build, values):
        with _transaction() as cur:
            if "version" in values and values["version"] != build["version"]:
                cur.execute(
                    """SELECT id FROM builds
                       WHERE modpack_id = %s AND version = %s AND id <> %s""",
                    (build["modpack_id"], values["version"], build["id"]),
                )
                if cur.fetchone():
                    raise WriteApiProblem(
                        "Build version already exists for this modpack."
                    )
            cls._update(cur, "builds", build["id"], values)
            cur.execute("SELECT * FROM builds WHERE id = %s", (build["id"],))
            return cur.fetchone()

    @staticmethod
    def delete_build(build_id):
        with _transaction() as cur:
            cur.execute("DELETE FROM build_modversion WHERE build_id = %s", (build_id,))
            cur.execute("DELETE FROM builds WHERE id = %s", (build_id,))

    @staticmethod
    def _compatible(build, modversion):
        return version_is_compatible(
            modversion.get("mcversion"),
            modversion.get("modloader"),
            build["minecraft"],
            build.get("modloader") or ("FORGE" if build.get("forge") else None),
        )

    @classmethod
    def add_build_mod(cls, build, mod, modversion, optional):
        if not cls._compatible(build, modversion):
            raise WriteApiProblem(
                "The selected mod version is not compatible with this build."
            )
        with _transaction() as cur:
            cur.execute(
                """SELECT build_modversion.id, modversions.mod_id
                   FROM build_modversion
                   INNER JOIN modversions
                       ON build_modversion.modversion_id = modversions.id
                   WHERE build_modversion.build_id = %s
                     AND modversions.mod_id = %s
                   LIMIT 1 FOR UPDATE""",
                (build["id"], mod["id"]),
            )
            existing = cur.fetchone()
            if existing:
                raise WriteApiProblem(
                    "Another version of this mod is already in the build. "
                    "Use PUT to update it."
                )
            cur.execute(
                """INSERT INTO build_modversion
                          (modversion_id, build_id, optional)
                   VALUES (%s, %s, %s)""",
                (modversion["id"], build["id"], int(optional)),
            )
            return Modversion._add_required_dependencies(
                cur,
                build["id"],
                build["minecraft"],
                mod["id"],
                build.get("modloader") or ("FORGE" if build.get("forge") else None),
            )

    @classmethod
    def update_build_mod(cls, build, mod, modversion, optional=None):
        if not cls._compatible(build, modversion):
            raise WriteApiProblem(
                "The selected mod version is not compatible with this build."
            )
        with _transaction() as cur:
            cur.execute(
                """SELECT build_modversion.id, build_modversion.optional
                   FROM build_modversion
                   INNER JOIN modversions
                       ON build_modversion.modversion_id = modversions.id
                   WHERE build_modversion.build_id = %s
                     AND modversions.mod_id = %s
                   LIMIT 1 FOR UPDATE""",
                (build["id"], mod["id"]),
            )
            membership = cur.fetchone()
            if membership is None:
                raise WriteApiProblem("Mod not in this build.", 404)
            cur.execute(
                """UPDATE build_modversion
                   SET modversion_id = %s, optional = %s
                   WHERE id = %s""",
                (
                    modversion["id"],
                    membership["optional"] if optional is None else int(optional),
                    membership["id"],
                ),
            )
            return Modversion._add_required_dependencies(
                cur,
                build["id"],
                build["minecraft"],
                mod["id"],
                build.get("modloader") or ("FORGE" if build.get("forge") else None),
            )

    @staticmethod
    def remove_build_mod(build_id, mod_id):
        with _transaction() as cur:
            cur.execute(
                """DELETE build_modversion FROM build_modversion
                   INNER JOIN modversions
                       ON build_modversion.modversion_id = modversions.id
                   WHERE build_modversion.build_id = %s
                     AND modversions.mod_id = %s""",
                (build_id, mod_id),
            )
            if cur.rowcount == 0:
                raise WriteApiProblem("Mod not in this build.", 404)

    @classmethod
    def create_mod(cls, values, dependency_identifiers=None):
        try:
            with _transaction() as cur:
                mod_id = cls._insert(cur, "mods", values)
                if dependency_identifiers is not None:
                    cls._sync_dependencies(cur, mod_id, dependency_identifiers)
                cur.execute("SELECT * FROM mods WHERE id = %s", (mod_id,))
                return cur.fetchone()
        except IntegrityError as error:
            cls._duplicate(error, "A mod with that slug already exists.")

    @classmethod
    def update_mod(cls, mod, values, dependency_identifiers=None):
        try:
            with _transaction() as cur:
                if "name" in values and values["name"] != mod["name"]:
                    cur.execute(
                        "SELECT 1 FROM modversions WHERE mod_id = %s LIMIT 1",
                        (mod["id"],),
                    )
                    if cur.fetchone():
                        raise WriteApiProblem(
                            "A mod slug cannot be changed after versions exist.", 409
                        )
                cls._update(cur, "mods", mod["id"], values)
                if dependency_identifiers is not None:
                    cls._sync_dependencies(cur, mod["id"], dependency_identifiers)
                cur.execute("SELECT * FROM mods WHERE id = %s", (mod["id"],))
                return cur.fetchone()
        except IntegrityError as error:
            cls._duplicate(error, "A mod with that slug already exists.")

    @staticmethod
    def delete_mod(mod_id):
        with _transaction() as cur:
            cur.execute("SELECT id FROM modversions WHERE mod_id = %s", (mod_id,))
            version_ids = [row["id"] for row in (cur.fetchall() or [])]
            if version_ids:
                placeholders = ", ".join(["%s"] * len(version_ids))
                # Only the number of bound placeholders is dynamic.
                cur.execute(
                    f"DELETE FROM build_modversion WHERE modversion_id IN ({placeholders})",  # nosec B608
                    tuple(version_ids),
                )
            cur.execute("DELETE FROM modversions WHERE mod_id = %s", (mod_id,))
            cur.execute(
                "DELETE FROM mod_dependencies WHERE mod_id = %s OR dependency_mod_id = %s",
                (mod_id, mod_id),
            )
            cur.execute(
                """DELETE maven_versions FROM maven_versions
                   INNER JOIN maven_artifacts
                       ON maven_versions.maven_artifact_id = maven_artifacts.id
                   WHERE maven_artifacts.mod_id = %s""",
                (mod_id,),
            )
            cur.execute(
                "DELETE FROM maven_artifacts WHERE mod_id = %s", (mod_id,)
            )
            cur.execute("DELETE FROM mods WHERE id = %s", (mod_id,))

    @staticmethod
    def _sync_dependencies(cur, mod_id, identifiers):
        identifiers = list(identifiers)
        if len(identifiers) != len({(type(value).__name__, str(value)) for value in identifiers}):
            raise WriteApiProblem("Dependencies must not contain duplicates.")

        dependency_ids = []
        for identifier in identifiers:
            if isinstance(identifier, int) and not isinstance(identifier, bool):
                cur.execute("SELECT id FROM mods WHERE id = %s", (identifier,))
            elif isinstance(identifier, str) and identifier:
                cur.execute("SELECT id FROM mods WHERE name = %s", (identifier,))
            else:
                raise WriteApiProblem(
                    "Each dependency must be a mod ID or mod slug."
                )
            row = cur.fetchone()
            if row is None:
                raise WriteApiProblem(f'Dependency "{identifier}" does not exist.')
            if row["id"] == mod_id:
                raise WriteApiProblem("A mod cannot depend on itself.")
            dependency_ids.append(row["id"])

        cur.execute("SELECT mod_id, dependency_mod_id FROM mod_dependencies")
        graph = {}
        for relationship in cur.fetchall() or []:
            graph.setdefault(relationship["mod_id"], set()).add(
                relationship["dependency_mod_id"]
            )
        graph[mod_id] = set(dependency_ids)
        pending = list(dependency_ids)
        visited = set()
        while pending:
            current = pending.pop()
            if current == mod_id:
                raise WriteApiProblem(
                    "The dependencies would create a dependency cycle."
                )
            if current in visited:
                continue
            visited.add(current)
            pending.extend(graph.get(current, ()))

        cur.execute("DELETE FROM mod_dependencies WHERE mod_id = %s", (mod_id,))
        if dependency_ids:
            cur.executemany(
                """INSERT INTO mod_dependencies (mod_id, dependency_mod_id)
                   VALUES (%s, %s)""",
                [(mod_id, dependency_id) for dependency_id in dependency_ids],
            )

    @classmethod
    def sync_dependencies(cls, mod_id, identifiers):
        with _transaction() as cur:
            cls._sync_dependencies(cur, mod_id, identifiers)

    @classmethod
    def create_modversion(cls, mod_id, values):
        try:
            with _transaction() as cur:
                cur.execute(
                    """SELECT id FROM modversions
                       WHERE mod_id = %s AND version = %s FOR UPDATE""",
                    (mod_id, values["version"]),
                )
                if cur.fetchone():
                    raise WriteApiProblem("Version already exists for this mod.")
                version_id = cls._insert(
                    cur, "modversions", {"mod_id": mod_id, **values}
                )
                Modversion.promote_parent_mod_for_jar_md5(
                    cur, mod_id, values.get("jarmd5")
                )
                cur.execute("SELECT * FROM modversions WHERE id = %s", (version_id,))
                return cur.fetchone()
        except IntegrityError as error:
            cls._duplicate(error, "Version already exists for this mod.")

    @classmethod
    def update_modversion(cls, modversion, values):
        with _transaction() as cur:
            compatibility = dict(modversion)
            compatibility.update(values)
            cur.execute(
                """SELECT builds.minecraft, builds.forge, builds.modloader
                   FROM build_modversion
                   INNER JOIN builds ON build_modversion.build_id = builds.id
                   WHERE build_modversion.modversion_id = %s""",
                (modversion["id"],),
            )
            for build in cur.fetchall() or []:
                if not cls._compatible(build, compatibility):
                    raise WriteApiProblem(
                        "The updated compatibility would exclude a build using this version.",
                        409,
                    )
            cls._update(cur, "modversions", modversion["id"], values)
            Modversion.promote_parent_mod_for_jar_md5(
                cur, modversion["mod_id"], values.get("jarmd5")
            )
            cur.execute(
                "SELECT * FROM modversions WHERE id = %s", (modversion["id"],)
            )
            return cur.fetchone()

    @staticmethod
    def delete_modversion(modversion_id):
        with _transaction() as cur:
            cur.execute(
                "SELECT COUNT(*) AS count FROM build_modversion WHERE modversion_id = %s",
                (modversion_id,),
            )
            count = cur.fetchone()["count"]
            if count:
                raise WriteApiProblem(
                    f"Mod version is in use by {count} build(s) and cannot be deleted.",
                    409,
                )
            cur.execute("DELETE FROM modversions WHERE id = %s", (modversion_id,))

    @classmethod
    def create_client(cls, values):
        try:
            with _transaction() as cur:
                client_id = cls._insert(cur, "clients", values)
                cur.execute("SELECT * FROM clients WHERE id = %s", (client_id,))
                return cur.fetchone()
        except IntegrityError as error:
            cls._duplicate(error, "A client with that name or UUID already exists.")

    @classmethod
    def update_client(cls, client, values, modpack_ids=None):
        try:
            with _transaction() as cur:
                cls._update(cur, "clients", client["id"], values)
                if modpack_ids is not None:
                    if modpack_ids:
                        placeholders = ", ".join(["%s"] * len(modpack_ids))
                        # Only the number of bound placeholders is dynamic.
                        cur.execute(
                            f"SELECT id FROM modpacks WHERE id IN ({placeholders})",  # nosec B608
                            tuple(modpack_ids),
                        )
                        existing = {row["id"] for row in (cur.fetchall() or [])}
                        if existing != set(modpack_ids):
                            raise WriteApiProblem(
                                "One or more selected modpacks do not exist."
                            )
                    cur.execute(
                        "DELETE FROM client_modpack WHERE client_id = %s",
                        (client["id"],),
                    )
                    if modpack_ids:
                        cur.executemany(
                            """INSERT INTO client_modpack (client_id, modpack_id)
                               VALUES (%s, %s)""",
                            [(client["id"], modpack_id) for modpack_id in modpack_ids],
                        )
                cur.execute("SELECT * FROM clients WHERE id = %s", (client["id"],))
                return cur.fetchone()
        except IntegrityError as error:
            cls._duplicate(error, "A client with that name already exists.")

    @staticmethod
    def delete_client(client_id):
        with _transaction() as cur:
            cur.execute("DELETE FROM client_modpack WHERE client_id = %s", (client_id,))
            cur.execute("DELETE FROM clients WHERE id = %s", (client_id,))
