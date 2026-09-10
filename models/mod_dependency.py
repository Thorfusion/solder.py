from mysql.connector import IntegrityError, errorcode

from .database import Database


class DependencyError(ValueError):
    """Base error for invalid mod dependency relationships."""


class CircularDependencyError(DependencyError):
    pass


class DuplicateDependencyError(DependencyError):
    pass


class UnknownDependencyModError(DependencyError):
    pass


class ModDependency:
    @staticmethod
    def get_management_data(mod_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT mod_dependencies.id,
                          mods.id AS dependency_mod_id,
                          mods.name,
                          mods.pretty_name
                   FROM mod_dependencies
                   INNER JOIN mods
                       ON mod_dependencies.dependency_mod_id = mods.id
                   WHERE mod_dependencies.mod_id = %s
                   ORDER BY mods.pretty_name, mods.name""",
                (mod_id,),
            )
            dependencies = cur.fetchall() or []

            cur.execute(
                """SELECT mods.id, mods.name, mods.pretty_name
                   FROM mods
                   WHERE mods.id <> %s
                     AND NOT EXISTS (
                         SELECT 1
                         FROM mod_dependencies
                         WHERE mod_dependencies.mod_id = %s
                           AND mod_dependencies.dependency_mod_id = mods.id
                     )
                   ORDER BY mods.pretty_name, mods.name""",
                (mod_id, mod_id),
            )
            available_mods = cur.fetchall() or []
            return dependencies, available_mods
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def add(mod_id, dependency_mod_id):
        try:
            mod_id = int(mod_id)
            dependency_mod_id = int(dependency_mod_id)
        except (TypeError, ValueError) as error:
            raise UnknownDependencyModError(
                "The selected dependency is invalid."
            ) from error
        if mod_id == dependency_mod_id:
            raise CircularDependencyError("A mod cannot depend on itself.")

        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                "SELECT id FROM mods WHERE id IN (%s, %s)",
                (mod_id, dependency_mod_id),
            )
            existing_mods = {row["id"] for row in (cur.fetchall() or [])}
            if existing_mods != {mod_id, dependency_mod_id}:
                raise UnknownDependencyModError(
                    "The selected dependency no longer exists."
                )

            cur.execute("SELECT mod_id, dependency_mod_id FROM mod_dependencies")
            relationships = cur.fetchall() or []
            dependencies_by_mod = {}
            for relationship in relationships:
                dependencies_by_mod.setdefault(relationship["mod_id"], set()).add(
                    relationship["dependency_mod_id"]
                )

            if dependency_mod_id in dependencies_by_mod.get(mod_id, set()):
                raise DuplicateDependencyError(
                    "That required dependency is already configured."
                )

            pending = [dependency_mod_id]
            visited = set()
            while pending:
                current = pending.pop()
                if current == mod_id:
                    raise CircularDependencyError(
                        "That relationship would create a dependency cycle."
                    )
                if current in visited:
                    continue
                visited.add(current)
                pending.extend(dependencies_by_mod.get(current, ()))

            cur.execute(
                """INSERT INTO mod_dependencies (mod_id, dependency_mod_id)
                   VALUES (%s, %s)""",
                (mod_id, dependency_mod_id),
            )
            conn.commit()
        except DependencyError:
            conn.rollback()
            raise
        except IntegrityError as error:
            conn.rollback()
            if error.errno == errorcode.ER_DUP_ENTRY:
                raise DuplicateDependencyError(
                    "That required dependency is already configured."
                ) from error
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def delete(dependency_id, mod_id):
        conn = Database.get_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                """DELETE FROM mod_dependencies
                   WHERE id = %s AND mod_id = %s""",
                (dependency_id, mod_id),
            )
            deleted = cur.rowcount > 0
            conn.commit()
            return deleted
        finally:
            cur.close()
            conn.close()
