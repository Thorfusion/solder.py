"""Advanced optional groups layered on top of build memberships."""

from dataclasses import dataclass, field

from .database import Database


REQUIRED = 0
OPTIONAL = 1
EXCLUDED = 2
VALID_STATES = {REQUIRED, OPTIONAL, EXCLUDED}

MULTIPLE = 0
SINGLE = 1
VALID_SELECTION_TYPES = {MULTIPLE, SINGLE}

BASIC_MODE = 0
ADVANCED_MODE = 1


class AdvancedOptionalError(ValueError):
    """Raised when an advanced optional configuration is invalid."""


@dataclass(frozen=True)
class AdvancedOptionalItem:
    id: int
    group_id: int
    build_modversion_id: int
    selected_by_default: bool
    sort_order: int
    optional_state: int
    modversion_id: int | None = None
    mod_slug: str | None = None
    pretty_name: str | None = None
    version: str | None = None


@dataclass
class AdvancedOptionalGroup:
    id: int
    build_id: int
    name: str
    description: str
    selection_type: int
    sort_order: int
    items: list[AdvancedOptionalItem] = field(default_factory=list)

    @property
    def is_single(self):
        return self.selection_type == SINGLE


class AdvancedOptional:
    """Manage named FileDirector/MCIL choices for a build."""

    @staticmethod
    def _integer(value, label):
        try:
            return int(value)
        except (TypeError, ValueError) as error:
            raise AdvancedOptionalError(f"Select a valid {label}.") from error

    @staticmethod
    def normalize_group(cursor, group_id):
        """Keep an exact-one group with one default after membership changes."""
        if not group_id:
            return
        cursor.execute(
            "SELECT selection_type FROM build_optional_groups WHERE id = %s",
            (group_id,),
        )
        group = cursor.fetchone()
        if not group or int(group["selection_type"]) != SINGLE:
            return
        cursor.execute(
            """SELECT id, selected_by_default
               FROM build_optional_group_items
               WHERE group_id = %s
               ORDER BY selected_by_default DESC, sort_order, id""",
            (group_id,),
        )
        items = cursor.fetchall() or []
        if not items:
            return
        selected_id = items[0]["id"]
        cursor.execute(
            """UPDATE build_optional_group_items
               SET selected_by_default = (id = %s)
               WHERE group_id = %s""",
            (selected_id, group_id),
        )

    @classmethod
    def set_modpack_mode(cls, modpack_id, mode):
        modpack_id = cls._integer(modpack_id, "modpack")
        mode = cls._integer(mode, "optional mode")
        if mode not in {BASIC_MODE, ADVANCED_MODE}:
            raise AdvancedOptionalError("Select a valid optional mode.")

        conn = Database.get_connection()
        if conn is None:
            raise AdvancedOptionalError("Could not connect to the database.")
        cursor = conn.cursor(dictionary=True)
        try:
            if mode == BASIC_MODE:
                cursor.execute(
                    """SELECT COUNT(*) AS item_count
                       FROM build_modversion
                       INNER JOIN builds ON builds.id = build_modversion.build_id
                       WHERE builds.modpack_id = %s
                         AND build_modversion.optional = %s""",
                    (modpack_id, EXCLUDED),
                )
                if int((cursor.fetchone() or {}).get("item_count") or 0):
                    raise AdvancedOptionalError(
                        "Change excluded advanced choices to required or optional "
                        "before returning to basic mode."
                    )
            cursor.execute(
                "UPDATE modpacks SET optional_mode = %s WHERE id = %s",
                (mode, modpack_id),
            )
            if cursor.rowcount != 1:
                raise AdvancedOptionalError("The modpack no longer exists.")
            conn.commit()
        except AdvancedOptionalError:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()

    @classmethod
    def create_group(
        cls, build_id, name, description="", selection_type=MULTIPLE, sort_order=0
    ):
        build_id = cls._integer(build_id, "build")
        name = " ".join(str(name or "").split()).strip()
        description = " ".join(str(description or "").splitlines()).strip()
        selection_type = cls._integer(selection_type, "selection type")
        sort_order = cls._integer(sort_order or 0, "group order")
        if not name or len(name) > 255:
            raise AdvancedOptionalError(
                "The group name must contain 1 to 255 characters."
            )
        if len(description) > 1000:
            raise AdvancedOptionalError(
                "The group description cannot exceed 1000 characters."
            )
        if selection_type not in VALID_SELECTION_TYPES:
            raise AdvancedOptionalError("Select a valid group type.")
        if selection_type == SINGLE and name == "$":
            raise AdvancedOptionalError(
                'The name "$" is reserved for independent FileDirector choices.'
            )

        conn = Database.get_connection()
        if conn is None:
            raise AdvancedOptionalError("Could not connect to the database.")
        cursor = conn.cursor()
        try:
            cursor.execute(
                """INSERT INTO build_optional_groups
                   (build_id, name, description, selection_type, sort_order)
                   SELECT builds.id, %s, %s, %s, %s
                   FROM builds
                   WHERE builds.id = %s""",
                (name, description, selection_type, sort_order, build_id),
            )
            if cursor.rowcount != 1:
                raise AdvancedOptionalError("The build no longer exists.")
            conn.commit()
        except AdvancedOptionalError:
            conn.rollback()
            raise
        except Exception as error:
            conn.rollback()
            raise AdvancedOptionalError(
                "A group with that name already exists in this build."
            ) from error
        finally:
            cursor.close()
            conn.close()

    @classmethod
    def delete_group(cls, build_id, group_id):
        build_id = cls._integer(build_id, "build")
        group_id = cls._integer(group_id, "group")
        conn = Database.get_connection()
        if conn is None:
            raise AdvancedOptionalError("Could not connect to the database.")
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                """SELECT build_optional_group_items.build_modversion_id
                   FROM build_optional_group_items
                   INNER JOIN build_optional_groups
                       ON build_optional_groups.id =
                          build_optional_group_items.group_id
                   WHERE build_optional_groups.id = %s
                     AND build_optional_groups.build_id = %s""",
                (group_id, build_id),
            )
            membership_ids = [
                row["build_modversion_id"] for row in (cursor.fetchall() or [])
            ]
            if membership_ids:
                # Only the number of bound placeholders is dynamic.
                placeholders = ", ".join(["%s"] * len(membership_ids))
                cursor.execute(
                    f"""UPDATE build_modversion
                        SET optional = %s
                        WHERE optional = %s AND id IN ({placeholders})""",  # nosec B608
                    (OPTIONAL, EXCLUDED, *membership_ids),
                )
            cursor.execute(
                """UPDATE build_optional_group_items
                   SET group_id = NULL,
                       selected_by_default = 0,
                       sort_order = 0
                   WHERE group_id = %s""",
                (group_id,),
            )
            cursor.execute(
                "DELETE FROM build_optional_groups WHERE id = %s AND build_id = %s",
                (group_id, build_id),
            )
            if cursor.rowcount != 1:
                raise AdvancedOptionalError("The optional group no longer exists.")
            conn.commit()
        except AdvancedOptionalError:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()

    @classmethod
    def save_membership(
        cls,
        build_id,
        build_modversion_id,
        group_id,
        optional_state,
        selected_by_default=False,
        sort_order=0,
    ):
        build_id = cls._integer(build_id, "build")
        membership_id = cls._integer(build_modversion_id, "build mod")
        optional_state = cls._integer(optional_state, "Technic/basic state")
        sort_order = cls._integer(sort_order or 0, "choice order")
        if optional_state not in VALID_STATES:
            raise AdvancedOptionalError("Select a valid Technic/basic state.")
        group_id = str(group_id or "").strip()
        if not group_id:
            if optional_state == EXCLUDED:
                raise AdvancedOptionalError(
                    "Excluded entries must belong to an advanced optional group."
                )
            return cls.remove_membership(
                build_id, membership_id, optional_state=optional_state
            )
        group_id = cls._integer(group_id, "optional group")

        conn = Database.get_connection()
        if conn is None:
            raise AdvancedOptionalError("Could not connect to the database.")
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                "SELECT id, selection_type FROM build_optional_groups "
                "WHERE id = %s AND build_id = %s",
                (group_id, build_id),
            )
            group = cursor.fetchone()
            cursor.execute(
                """SELECT build_modversion.id, mods.modtype
                   FROM build_modversion
                   INNER JOIN modversions
                     ON modversions.id = build_modversion.modversion_id
                   INNER JOIN mods ON mods.id = modversions.mod_id
                   WHERE build_modversion.id = %s
                     AND build_modversion.build_id = %s""",
                (membership_id, build_id),
            )
            membership = cursor.fetchone()
            if group is None or membership is None:
                raise AdvancedOptionalError(
                    "The selected group or build mod no longer exists."
                )
            if str(membership.get("modtype") or "").upper() in {
                "LAUNCHER",
                "MCIL",
            }:
                raise AdvancedOptionalError(
                    "Modloader and downloader packages cannot be assigned "
                    "to an advanced optional group."
                )

            cursor.execute(
                "SELECT group_id FROM build_optional_group_items "
                "WHERE build_modversion_id = %s",
                (membership_id,),
            )
            existing_item = cursor.fetchone()
            previous_group_id = (
                existing_item["group_id"] if existing_item else None
            )
            selected = bool(selected_by_default)
            if int(group["selection_type"]) == SINGLE and selected:
                cursor.execute(
                    "UPDATE build_optional_group_items "
                    "SET selected_by_default = 0 WHERE group_id = %s",
                    (group_id,),
                )

            cursor.execute(
                """INSERT INTO build_optional_group_items
                   (group_id, build_modversion_id, selected_by_default, sort_order)
                   VALUES (%s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE
                       group_id = VALUES(group_id),
                       selected_by_default = VALUES(selected_by_default),
                       sort_order = VALUES(sort_order)""",
                (group_id, membership_id, 1 if selected else 0, sort_order),
            )
            cursor.execute(
                "UPDATE build_modversion SET optional = %s "
                "WHERE id = %s AND build_id = %s",
                (optional_state, membership_id, build_id),
            )
            cls.normalize_group(cursor, group_id)
            if previous_group_id != group_id:
                cls.normalize_group(cursor, previous_group_id)
            conn.commit()
        except AdvancedOptionalError:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()

    @classmethod
    def remove_membership(cls, build_id, build_modversion_id, optional_state=REQUIRED):
        build_id = cls._integer(build_id, "build")
        membership_id = cls._integer(build_modversion_id, "build mod")
        optional_state = cls._integer(optional_state, "Technic/basic state")
        if optional_state not in {REQUIRED, OPTIONAL}:
            raise AdvancedOptionalError(
                "An ungrouped build mod must be required or optional."
            )
        conn = Database.get_connection()
        if conn is None:
            raise AdvancedOptionalError("Could not connect to the database.")
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                "SELECT group_id FROM build_optional_group_items "
                "WHERE build_modversion_id = %s",
                (membership_id,),
            )
            item = cursor.fetchone()
            cursor.execute(
                """INSERT INTO build_optional_group_items
                   (group_id, build_modversion_id,
                    selected_by_default, sort_order)
                   VALUES (NULL, %s, 0, 0)
                   ON DUPLICATE KEY UPDATE
                       group_id = NULL,
                       selected_by_default = 0,
                       sort_order = 0""",
                (membership_id,),
            )
            cursor.execute(
                "UPDATE build_modversion SET optional = %s "
                "WHERE id = %s AND build_id = %s",
                (optional_state, membership_id, build_id),
            )
            if cursor.rowcount != 1:
                raise AdvancedOptionalError("The build mod no longer exists.")
            cls.normalize_group(cursor, item["group_id"] if item else None)
            conn.commit()
        except AdvancedOptionalError:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()

    @classmethod
    def set_listing(cls, build_id, build_modversion_id, listed):
        """Add or remove a build mod from the advanced-optionals work list.

        An ungrouped row in build_optional_group_items is the work-list marker,
        keeping this concern independent from the Technic/basic optional state.
        """
        build_id = cls._integer(build_id, "build")
        membership_id = cls._integer(build_modversion_id, "build mod")
        listed = str(listed or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        conn = Database.get_connection()
        if conn is None:
            raise AdvancedOptionalError("Could not connect to the database.")
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                """SELECT build_modversion.id,
                          build_modversion.optional,
                          build_optional_group_items.group_id,
                          mods.modtype
                   FROM build_modversion
                   INNER JOIN modversions
                     ON modversions.id = build_modversion.modversion_id
                   INNER JOIN mods ON mods.id = modversions.mod_id
                   LEFT JOIN build_optional_group_items
                     ON build_optional_group_items.build_modversion_id =
                        build_modversion.id
                   WHERE build_modversion.id = %s
                     AND build_modversion.build_id = %s""",
                (membership_id, build_id),
            )
            membership = cursor.fetchone()
            if membership is None:
                raise AdvancedOptionalError("The build mod no longer exists.")
            if listed and str(membership.get("modtype") or "").upper() in {
                "LAUNCHER",
                "MCIL",
            }:
                raise AdvancedOptionalError(
                    "Modloader and downloader packages cannot be listed as "
                    "advanced optionals."
                )

            if listed:
                cursor.execute(
                    """INSERT IGNORE INTO build_optional_group_items
                       (group_id, build_modversion_id,
                        selected_by_default, sort_order)
                       VALUES (NULL, %s, 0, 0)""",
                    (membership_id,),
                )
            else:
                cursor.execute(
                    "DELETE FROM build_optional_group_items "
                    "WHERE build_modversion_id = %s",
                    (membership_id,),
                )
                if membership.get("group_id") is not None:
                    cursor.execute(
                        "UPDATE build_modversion SET optional = %s WHERE id = %s",
                        (REQUIRED, membership_id),
                    )
                cls.normalize_group(cursor, membership.get("group_id"))
            conn.commit()
        except AdvancedOptionalError:
            conn.rollback()
            raise
        finally:
            cursor.close()
            conn.close()

    @classmethod
    def set_modversion_listing(cls, build_id, modversion_id, listed=True):
        """Set the work-list state immediately after adding a mod version."""
        build_id = cls._integer(build_id, "build")
        modversion_id = cls._integer(modversion_id, "mod version")
        conn = Database.get_connection()
        if conn is None:
            raise AdvancedOptionalError("Could not connect to the database.")
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                """SELECT id FROM build_modversion
                   WHERE build_id = %s AND modversion_id = %s
                   ORDER BY id DESC LIMIT 1""",
                (build_id, modversion_id),
            )
            membership = cursor.fetchone()
        finally:
            cursor.close()
            conn.close()
        if membership is None:
            raise AdvancedOptionalError("The added build mod was not found.")
        return cls.set_listing(build_id, membership["id"], listed)

    @staticmethod
    def _get_groups(cursor, build_id):
        cursor.execute(
            """SELECT id, build_id, name, description,
                      selection_type, sort_order
               FROM build_optional_groups
               WHERE build_id = %s
               ORDER BY sort_order, id""",
            (build_id,),
        )
        groups = [AdvancedOptionalGroup(**row) for row in cursor.fetchall()]
        if not groups:
            return []
        by_id = {group.id: group for group in groups}
        cursor.execute(
            """SELECT build_optional_group_items.id,
                      build_optional_group_items.group_id,
                      build_optional_group_items.build_modversion_id,
                      build_optional_group_items.selected_by_default,
                      build_optional_group_items.sort_order,
                      build_modversion.optional AS optional_state,
                      modversions.id AS modversion_id,
                      modversions.version,
                      mods.name AS mod_slug,
                      mods.pretty_name
               FROM build_optional_group_items
               INNER JOIN build_optional_groups
                   ON build_optional_groups.id =
                      build_optional_group_items.group_id
               INNER JOIN build_modversion
                   ON build_modversion.id =
                      build_optional_group_items.build_modversion_id
               INNER JOIN modversions
                   ON modversions.id = build_modversion.modversion_id
               INNER JOIN mods ON mods.id = modversions.mod_id
               WHERE build_optional_groups.build_id = %s
               ORDER BY build_optional_groups.sort_order,
                        build_optional_group_items.sort_order,
                        build_optional_group_items.id""",
            (build_id,),
        )
        for row in cursor.fetchall() or []:
            row = dict(row)
            row["selected_by_default"] = bool(row["selected_by_default"])
            group = by_id.get(row["group_id"])
            if group is not None:
                group.items.append(AdvancedOptionalItem(**row))
        return groups

    @classmethod
    def get_groups(cls, build_id):
        build_id = cls._integer(build_id, "build")
        conn = Database.get_connection()
        if conn is None:
            return []
        cursor = conn.cursor(dictionary=True)
        try:
            return cls._get_groups(cursor, build_id)
        finally:
            cursor.close()
            conn.close()

    @classmethod
    def get_active_groups(cls, build_id):
        """Return groups only when their modpack uses advanced optionals."""
        build_id = cls._integer(build_id, "build")
        conn = Database.get_connection()
        if conn is None:
            return []
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                """SELECT modpacks.optional_mode
                   FROM builds
                   INNER JOIN modpacks ON modpacks.id = builds.modpack_id
                   WHERE builds.id = %s""",
                (build_id,),
            )
            row = cursor.fetchone()
            if not row or int(row.get("optional_mode") or 0) != ADVANCED_MODE:
                return []
            return cls._get_groups(cursor, build_id)
        finally:
            cursor.close()
            conn.close()

    @classmethod
    def get_active_groups_for_packages(cls, build_id, packages):
        """Return exportable groups for packages with build membership IDs.

        Older callers and lightweight test doubles do not always include the
        membership ID used to associate packages with group items. In that
        case no group could match, so avoid an unnecessary database query.
        """
        if not any(
            getattr(package, "membership_id", None) for package in packages
        ):
            return ()
        return cls.get_active_groups(build_id)

    @staticmethod
    def clone_build(cursor, source_build_id, target_build_id, membership_ids):
        """Clone groups using an old-to-new build membership id mapping."""
        cursor.execute(
            """SELECT id, name, description, selection_type, sort_order
               FROM build_optional_groups WHERE build_id = %s
               ORDER BY id""",
            (source_build_id,),
        )
        for group in cursor.fetchall() or []:
            cursor.execute(
                """INSERT INTO build_optional_groups
                   (build_id, name, description, selection_type, sort_order)
                   VALUES (%s, %s, %s, %s, %s)""",
                (
                    target_build_id,
                    group["name"],
                    group["description"],
                    group["selection_type"],
                    group["sort_order"],
                ),
            )
            new_group_id = cursor.lastrowid
            cursor.execute(
                """SELECT build_modversion_id, selected_by_default, sort_order
                   FROM build_optional_group_items WHERE group_id = %s""",
                (group["id"],),
            )
            for item in cursor.fetchall() or []:
                new_membership_id = membership_ids.get(
                    item["build_modversion_id"]
                )
                if new_membership_id is not None:
                    cursor.execute(
                        """INSERT INTO build_optional_group_items
                           (group_id, build_modversion_id,
                            selected_by_default, sort_order)
                           VALUES (%s, %s, %s, %s)""",
                        (
                            new_group_id,
                            new_membership_id,
                            item["selected_by_default"],
                            item["sort_order"],
                        ),
                    )
        cursor.execute(
            """SELECT build_optional_group_items.build_modversion_id
               FROM build_optional_group_items
               INNER JOIN build_modversion
                 ON build_modversion.id =
                    build_optional_group_items.build_modversion_id
               WHERE build_modversion.build_id = %s
                 AND build_optional_group_items.group_id IS NULL""",
            (source_build_id,),
        )
        for item in cursor.fetchall() or []:
            new_membership_id = membership_ids.get(
                item["build_modversion_id"]
            )
            if new_membership_id is not None:
                cursor.execute(
                    """INSERT INTO build_optional_group_items
                       (group_id, build_modversion_id,
                        selected_by_default, sort_order)
                       VALUES (NULL, %s, 0, 0)""",
                    (new_membership_id,),
                )

    @staticmethod
    def delete_build(cursor, build_id):
        cursor.execute(
            """DELETE build_optional_group_items
               FROM build_optional_group_items
               INNER JOIN build_modversion
                   ON build_modversion.id =
                      build_optional_group_items.build_modversion_id
               WHERE build_modversion.build_id = %s""",
            (build_id,),
        )
        cursor.execute(
            "DELETE FROM build_optional_groups WHERE build_id = %s",
            (build_id,),
        )

    @classmethod
    def delete_modversion_memberships(cls, cursor, modversion_ids):
        """Remove advanced metadata before deleting build memberships."""
        modversion_ids = tuple(int(value) for value in modversion_ids)
        if not modversion_ids:
            return
        # IDs are normalized to integers; only the bound placeholder count is
        # dynamic in these two statements.
        placeholders = ", ".join(["%s"] * len(modversion_ids))
        cursor.execute(
            f"""SELECT DISTINCT build_optional_group_items.group_id
                FROM build_optional_group_items
                INNER JOIN build_modversion
                    ON build_modversion.id =
                       build_optional_group_items.build_modversion_id
                WHERE build_modversion.modversion_id IN ({placeholders})""",  # nosec B608
            modversion_ids,
        )
        group_ids = [row["group_id"] for row in (cursor.fetchall() or [])]
        cursor.execute(
            f"""DELETE build_optional_group_items
                FROM build_optional_group_items
                INNER JOIN build_modversion
                    ON build_modversion.id =
                       build_optional_group_items.build_modversion_id
                WHERE build_modversion.modversion_id IN ({placeholders})""",  # nosec B608
            modversion_ids,
        )
        for group_id in group_ids:
            cls.normalize_group(cursor, group_id)
