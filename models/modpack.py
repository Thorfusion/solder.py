import datetime

from .build import Build
from .database import Database


class Modpack:
    def __init__(
        self,
        id,
        name,
        slug,
        recommended,
        latest,
        created_at,
        updated_at,
        order,
        hidden,
        private,
        pinned,
        enable_optionals=0,
        enable_server=0,
        optional_mode=0,
    ):
        self.id = id
        self.name = name
        self.slug = slug
        self.recommended = recommended
        self.latest = latest
        self.created_at = created_at
        self.updated_at = updated_at
        self.order = order
        self.hidden = hidden
        self.private = private
        self.pinned = pinned
        self.enable_optionals = enable_optionals
        self.enable_server = enable_server
        self.optional_mode = int(optional_mode or 0)

    @classmethod
    def _from_row(cls, row):
        """Create a modpack from either a Technic Solder or solder.py row."""
        return cls(
            row["id"],
            row["name"],
            row["slug"],
            row["recommended"],
            row["latest"],
            row["created_at"],
            row["updated_at"],
            row["order"],
            row["hidden"],
            row["private"],
            row.get("pinned", 0),
            row.get("enable_optionals", 0),
            row.get("enable_server", 0),
            row.get("optional_mode", 0),
        )

    @staticmethod
    def new(name, slug, hidden, private, user_id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        now = datetime.datetime.now()
        try:
            cur.execute("INSERT INTO modpacks (name, slug, created_at, updated_at, hidden, private, user_id) VALUES (%s, %s, %s, %s, %s, %s, %s)", (name, slug, now, now, hidden, private, user_id))
            modpack_id = cur.lastrowid
            cur.execute(
                "INSERT INTO user_modpack (user_id, modpack_id, created_at, updated_at) VALUES (%s, %s, %s, %s)",
                (user_id, modpack_id, now, now),
            )
            cur.execute(
                """UPDATE user_permissions
                   SET modpacks = CASE
                       WHEN FIND_IN_SET(%s, COALESCE(modpacks, '')) > 0
                           THEN modpacks
                       WHEN COALESCE(modpacks, '') = '' THEN CAST(%s AS CHAR)
                       ELSE CONCAT(modpacks, ',', %s)
                   END
                   WHERE user_id = %s""",
                (modpack_id, modpack_id, modpack_id, user_id),
            )
            conn.commit()
            return modpack_id
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def delete_related_rows(cursor, modpack_id):
        """Delete pack-owned mappings using the caller's transaction."""
        cursor.execute(
            """DELETE modpack_publication_runs
               FROM modpack_publication_runs
               INNER JOIN modpack_publication_targets
                   ON modpack_publication_targets.id =
                      modpack_publication_runs.target_id
               WHERE modpack_publication_targets.modpack_id = %s""",
            (modpack_id,),
        )
        cursor.execute(
            "DELETE FROM modpack_publication_targets WHERE modpack_id = %s",
            (modpack_id,),
        )
        cursor.execute(
            "DELETE FROM client_modpack WHERE modpack_id = %s", (modpack_id,)
        )
        cursor.execute(
            "DELETE FROM user_modpack WHERE modpack_id = %s", (modpack_id,)
        )
        cursor.execute(
            """UPDATE user_permissions
               SET modpacks = TRIM(BOTH ',' FROM REPLACE(
                   CONCAT(',', COALESCE(modpacks, ''), ','),
                   CONCAT(',', %s, ','),
                   ','
               ))""",
            (modpack_id,),
        )

    @staticmethod
    def delete_modpack(id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT id FROM builds WHERE modpack_id = %s", (id,))
            for build in cur.fetchall() or []:
                Build.delete_related_rows(cur, build["id"])
            Modpack.delete_related_rows(cur, id)
            cur.execute("DELETE FROM builds WHERE modpack_id = %s", (id,))
            cur.execute("DELETE FROM modpacks WHERE id=%s", (id,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @classmethod
    def get_by_id(cls, id):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM modpacks WHERE id = %s", (id,))
        row = cur.fetchone()
        if row:
            return cls._from_row(row)
        return None

    @staticmethod
    def get_by_pinned(user_id=None):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            if user_id is None:
                cur.execute("SELECT name, id FROM modpacks WHERE pinned = 1")
            else:
                cur.execute(
                    """SELECT DISTINCT modpacks.name, modpacks.id
                       FROM modpacks
                       INNER JOIN user_permissions
                           ON user_permissions.user_id = %s
                       LEFT JOIN user_modpack
                           ON user_modpack.user_id = %s
                          AND user_modpack.modpack_id = modpacks.id
                       WHERE modpacks.pinned = 1
                         AND (user_permissions.solder_full = 1
                              OR user_modpack.modpack_id IS NOT NULL)
                       ORDER BY modpacks.name, modpacks.id""",
                    (int(user_id), int(user_id)),
                )
            return cur.fetchall() or []
        finally:
            cur.close()
            conn.close()

    @classmethod
    def get_by_cid_api(cls, cid):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT *
                   FROM modpacks
                   WHERE (hidden = 0 AND private = 0)
                      OR EXISTS (
                           SELECT 1
                           FROM client_modpack cm
                           INNER JOIN clients c ON cm.client_id = c.id
                           WHERE cm.modpack_id = modpacks.id
                             AND c.uuid = %s
                      )
                   ORDER BY id ASC""",
                (cid,),
            )
            return [cls._from_row(row) for row in cur.fetchall()]
        finally:
            cur.close()
            conn.close()
    
    @classmethod
    def get_all_api(cls):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT * FROM modpacks ORDER BY id ASC")
            return [cls._from_row(row) for row in cur.fetchall()]
        finally:
            cur.close()
            conn.close()

    @classmethod
    def get_by_cid_slug_api(cls, cid, slug):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            # Hidden packs are unlisted, not private. They remain directly
            # addressable by slug, matching the Technic Launcher contract.
            cur.execute(
                """SELECT *
                   FROM modpacks
                   WHERE slug = %s
                     AND (
                          private = 0
                          OR EXISTS (
                              SELECT 1
                              FROM client_modpack cm
                              INNER JOIN clients c ON cm.client_id = c.id
                              WHERE cm.modpack_id = modpacks.id
                                AND c.uuid = %s
                          )
                     )""",
                (slug, cid),
            )
            row = cur.fetchone()
            return cls._from_row(row) if row else None
        finally:
            cur.close()
            conn.close()
    
    @classmethod
    def get_all_by_slug_api(cls, slug):
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute("SELECT * FROM modpacks WHERE slug = %s", (slug,))
            row = cur.fetchone()
            return cls._from_row(row) if row else None
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def get_all() -> list:
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM modpacks")
        rows = cur.fetchall()
        if rows:
            return rows
        return []

    @staticmethod
    def get_all_for_user(user_id) -> list:
        """Return modpacks assigned to a user, or every pack for full admins."""
        conn = Database.get_connection()
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                """SELECT DISTINCT modpacks.*
                   FROM modpacks
                   INNER JOIN user_permissions
                       ON user_permissions.user_id = %s
                   LEFT JOIN user_modpack
                       ON user_modpack.user_id = %s
                      AND user_modpack.modpack_id = modpacks.id
                   WHERE user_permissions.solder_full = 1
                      OR user_modpack.modpack_id IS NOT NULL
                   ORDER BY modpacks.name, modpacks.id""",
                (int(user_id), int(user_id)),
            )
            return cur.fetchall() or []
        finally:
            cur.close()
            conn.close()

    def get_builds(self):
        return Build.get_by_modpack(self)
    
    def get_builds_cid_api(self, cid):
        return self.get_builds_api(cid=cid)
    
    def get_builds_api(self, cid=None, api_key=False):
        return Build.get_by_modpack_api(self, cid=cid, api_key=api_key)

    def get_build_api(self, version, cid=None, api_key=False):
        return Build.get_by_modpack_version_api(
            self, version, cid=cid, api_key=api_key
        )
    
    @staticmethod
    def to_modpack_json(cid, slug):
        modpackd = Modpack.get_by_cid_slug_api(cid, slug)
        if modpackd:
            modpackd.builds = modpackd.get_builds_api(cid=cid)
            return modpackd.to_json()
        
    @staticmethod
    def to_modpack_json_all(slug):
        modpackd = Modpack.get_all_by_slug_api(slug)
        if modpackd:
            modpackd.builds = modpackd.get_builds_api(api_key=True)
            return modpackd.to_json()
        

    def to_json(self):
        data = {
            "id": self.id,
            "name": self.slug,
            "display_name": self.name,
            "recommended": self.recommended,
            "latest": self.latest,
            "capabilities": {
                "advanced_optionals": self.optional_mode == 1,
                "bootstrap_manifest": True,
                "optional": bool(self.enable_optionals),
                "server": bool(self.enable_server),
            },
        }

        if self.builds is not None:
            buildversions = []
            for build in self.builds:
                buildversions.append(build.version)
                if self.enable_optionals:
                    buildversions.append(build.version + "-optional")
                if self.enable_server:
                    buildversions.append(build.version + "-server")
            data["builds"] = buildversions
        return data
