"""Technic-compatible personal access tokens for the optional write API."""

from dataclasses import dataclass
import hashlib
import hmac
import secrets

from .database import Database


TOKENABLE_TYPE = r"App\Models\User"


@dataclass(frozen=True)
class ApiPrincipal:
    token_id: int
    user_id: int
    permissions: dict
    assigned_modpacks: frozenset = frozenset()

    def allows(self, permission):
        return bool(
            self.permissions.get("solder_full")
            or self.permissions.get(permission)
        )

    def can_access_modpack(self, modpack_id):
        if self.permissions.get("solder_full"):
            return True
        technic_modpacks = {
            value.strip()
            for value in str(self.permissions.get("modpacks") or "").split(",")
            if value.strip()
        }
        if str(modpack_id) in technic_modpacks:
            return True
        return int(modpack_id) in self.assigned_modpacks

    @property
    def accessible_modpack_ids(self):
        values = {
            int(value.strip())
            for value in str(self.permissions.get("modpacks") or "").split(",")
            if value.strip().isdigit()
        }
        values.update(self.assigned_modpacks)
        return tuple(sorted(values))

    @property
    def cache_identity(self):
        """Represent every permission that can change a cached read response."""
        return (
            self.token_id,
            bool(self.permissions.get("solder_full")),
            self.accessible_modpack_ids,
        )


class ApiToken:
    @staticmethod
    def _digest(secret):
        return hashlib.sha256(secret.encode("utf-8")).hexdigest()

    @classmethod
    def authenticate(cls, authorization, *, touch=True):
        if not authorization:
            return None
        scheme, separator, plaintext = authorization.partition(" ")
        if not separator or scheme.casefold() != "bearer":
            return None
        plaintext = plaintext.strip()
        try:
            token_id, secret = plaintext.split("|", 1)
            token_id = int(token_id)
        except (TypeError, ValueError):
            return None
        if token_id < 1 or not secret:
            return None

        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT personal_access_tokens.id AS token_id,
                          personal_access_tokens.token,
                          personal_access_tokens.tokenable_id AS user_id,
                          user_permissions.*
                   FROM personal_access_tokens
                   INNER JOIN user_permissions
                       ON personal_access_tokens.tokenable_id = user_permissions.user_id
                   INNER JOIN users
                       ON personal_access_tokens.tokenable_id = users.id
                   WHERE personal_access_tokens.id = %s
                     AND personal_access_tokens.tokenable_type = %s
                     AND (personal_access_tokens.expires_at IS NULL
                          OR personal_access_tokens.expires_at > UTC_TIMESTAMP())""",
                (token_id, TOKENABLE_TYPE),
            )
            row = cur.fetchone()
            if row is None or not hmac.compare_digest(
                str(row["token"]), cls._digest(secret)
            ):
                return None
            assigned_modpacks = frozenset()
            if not row.get("solder_full"):
                cur.execute(
                    """SELECT modpack_id FROM user_modpack
                       WHERE user_id = %s""",
                    (row["user_id"],),
                )
                assigned_modpacks = frozenset(
                    int(item["modpack_id"]) for item in (cur.fetchall() or [])
                )
            if touch:
                cur.execute(
                    """UPDATE personal_access_tokens
                       SET last_used_at = UTC_TIMESTAMP(), updated_at = UTC_TIMESTAMP()
                       WHERE id = %s""",
                    (token_id,),
                )
                conn.commit()
            permissions = {
                key: row.get(key, 0)
                for key in (
                    "solder_full",
                    "solder_users",
                    "solder_keys",
                    "solder_clients",
                    "solder_env",
                    "mods_create",
                    "mods_manage",
                    "mods_delete",
                    "modpacks_create",
                    "modpacks_manage",
                    "modpacks_delete",
                    "modpacks",
                )
            }
            return ApiPrincipal(
                token_id, row["user_id"], permissions, assigned_modpacks
            )
        finally:
            cur.close()
            conn.close()

    @classmethod
    def create(cls, user_id, name):
        name = str(name or "").strip()
        if not name or len(name) > 255:
            raise ValueError("Token name must contain between 1 and 255 characters.")
        secret = secrets.token_hex(20)
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """INSERT INTO personal_access_tokens
                          (tokenable_type, tokenable_id, name, token, abilities,
                           created_at, updated_at)
                   VALUES (%s, %s, %s, %s, '[\"*\"]', UTC_TIMESTAMP(), UTC_TIMESTAMP())""",
                (TOKENABLE_TYPE, user_id, name, cls._digest(secret)),
            )
            token_id = cur.lastrowid
            conn.commit()
            return {
                "id": token_id,
                "name": name,
                "plaintext": f"{token_id}|{secret}",
                "created_at": cls.get(token_id, user_id)["created_at"],
            }
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def get(token_id, user_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT id, name, last_used_at, created_at
                   FROM personal_access_tokens
                   WHERE id = %s AND tokenable_id = %s
                     AND tokenable_type = %s""",
                (token_id, user_id, TOKENABLE_TYPE),
            )
            return cur.fetchone()
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def get_all(user_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT id, name, last_used_at, created_at
                   FROM personal_access_tokens
                   WHERE tokenable_id = %s AND tokenable_type = %s
                   ORDER BY id""",
                (user_id, TOKENABLE_TYPE),
            )
            return cur.fetchall() or []
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def delete(token_id, user_id):
        conn = Database.get_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                """DELETE FROM personal_access_tokens
                   WHERE id = %s AND tokenable_id = %s
                     AND tokenable_type = %s""",
                (token_id, user_id, TOKENABLE_TYPE),
            )
            deleted = cur.rowcount > 0
            conn.commit()
            return deleted
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()
