"""Stable read model for a dedicated Solder API bootstrap client."""

import hashlib
import json
import re

from .compatibility import compatibility_values

BOOTSTRAP_SCHEMA = "solder.py/bootstrap"
BOOTSTRAP_SCHEMA_VERSION = 1
IGNORED_MODTYPES = frozenset({"LAUNCHER", "BOOTSTRAP"})
STATE_NAMES = {0: "required", 1: "optional", 2: "excluded"}
SELECTION_TYPES = {0: "multiple", 1: "single"}
_MD5_RE = re.compile(r"^[0-9a-fA-F]{32}$")


class BootstrapManifestError(ValueError):
    """Raised when stored bootstrap metadata cannot be represented safely."""


class BootstrapManifest:
    """Serialize build packages and advanced choices without Technic flattening."""

    @staticmethod
    def _optional_string(value):
        return value if isinstance(value, str) else None

    @staticmethod
    def _download_url(repository_url, slug, version, extension):
        return f"{repository_url}{slug}/{slug}-{version}.{extension}"

    @classmethod
    def _download(
        cls, package, repository_url, modtype, modrinth_downloads
    ):
        slug = package.modname
        version = package.version
        if modtype == "MOD":
            jar_md5 = str(getattr(package, "jarmd5", "") or "").strip()
            if _MD5_RE.fullmatch(jar_md5) is not None:
                filename = f"{slug}-{version}.jar"
                solder_url = cls._download_url(
                    repository_url, slug, version, "jar"
                )
                download = {
                    "url": solder_url,
                    "md5": jar_md5.lower(),
                    "filesize": getattr(package, "jarfilesize", None),
                    "format": "jar",
                    "path": f"mods/{filename}",
                }
                provider = str(
                    getattr(package, "integration_provider", "") or ""
                ).upper()
                project_id = str(
                    getattr(package, "integration_project_id", "") or ""
                )
                version_id = str(
                    getattr(package, "integration_version_id", "") or ""
                )
                modrinth_url = modrinth_downloads.get(
                    (project_id, version_id)
                )
                if provider == "MODRINTH" and modrinth_url:
                    download["sources"] = [
                        {"provider": "modrinth", "url": modrinth_url},
                        {"provider": "solder", "url": solder_url},
                    ]
                return download

        return {
            "url": cls._download_url(
                repository_url, slug, version, "zip"
            ),
            "md5": package.md5,
            "filesize": package.filesize,
            "format": "solder_zip",
            "extract_to": ".",
        }

    @classmethod
    def _group_maps(cls, groups, available_memberships):
        item_map = {}
        serialized_groups = []
        for group in groups or ():
            choices = []
            for item in group.items:
                if item.build_modversion_id not in available_memberships:
                    continue
                state = int(item.optional_state)
                state_name = STATE_NAMES.get(state)
                if state_name is None:
                    raise BootstrapManifestError(
                        "An advanced optional choice has an invalid state."
                    )
                item_map[item.build_modversion_id] = (group, item)
                choices.append(
                    {
                        "membership_id": item.build_modversion_id,
                        "modversion_id": item.modversion_id,
                        "slug": item.mod_slug,
                        "name": item.pretty_name,
                        "version": item.version,
                        "state": state,
                        "state_name": state_name,
                        "selected_by_default": bool(
                            item.selected_by_default
                        ),
                        "sort_order": int(item.sort_order),
                    }
                )
            if not choices:
                continue
            selection_type = int(group.selection_type)
            selection_name = SELECTION_TYPES.get(selection_type)
            if selection_name is None:
                raise BootstrapManifestError(
                    "An advanced optional group has an invalid selection type."
                )
            serialized_groups.append(
                {
                    "id": group.id,
                    # Names are unique per build and survive build cloning,
                    # making them a better preference key than row IDs.
                    "key": group.name,
                    "name": group.name,
                    "description": group.description,
                    "selection_type": selection_name,
                    "selection_type_id": selection_type,
                    "minimum": 1 if group.is_single else 0,
                    "maximum": 1 if group.is_single else None,
                    "sort_order": int(group.sort_order),
                    "choices": choices,
                }
            )
        return item_map, serialized_groups

    @classmethod
    def render(
        cls,
        modpack,
        build,
        packages,
        groups,
        repository_url,
        dependencies,
        *,
        target="client",
        modrinth_downloads=None,
    ):
        packages = list(packages)
        modrinth_downloads = modrinth_downloads or {}
        missing_memberships = [
            package
            for package in packages
            if getattr(package, "membership_id", None) is None
        ]
        if missing_memberships:
            raise BootstrapManifestError(
                "A build package is missing its membership identifier."
            )
        available_memberships = {
            package.membership_id for package in packages
        }
        item_map, serialized_groups = cls._group_maps(
            groups, available_memberships
        )
        packages_by_mod_id = {
            getattr(package, "mod_id", None): package for package in packages
        }

        serialized_packages = []
        selected_defaults = []
        required_defaults = []
        for package in packages:
            membership_id = getattr(package, "membership_id", None)
            state = int(getattr(package, "optional", 0) or 0)
            state_name = STATE_NAMES.get(state)
            if state_name is None:
                raise BootstrapManifestError(
                    "A build package has an invalid optional state."
                )
            grouped = item_map.get(membership_id)
            group = grouped[0] if grouped else None
            group_item = grouped[1] if grouped else None
            selected_by_default = (
                bool(group_item.selected_by_default)
                if group_item is not None
                else state == 0
            )
            modtype = str(getattr(package, "modtype", "MOD") or "MOD").upper()
            managed = modtype not in IGNORED_MODTYPES and modtype != "MCIL"
            if managed and selected_by_default:
                selected_defaults.append(membership_id)
            if managed and group is None and state == 0:
                required_defaults.append(membership_id)

            slug = package.modname
            version = package.version
            download = cls._download(
                package, repository_url, modtype, modrinth_downloads
            )
            package_dependencies = []
            for dependency in dependencies.get(
                getattr(package, "mod_id", None), []
            ):
                serialized_dependency = dict(dependency)
                resolved = packages_by_mod_id.get(dependency.get("id"))
                serialized_dependency.update(
                    {
                        "required": True,
                        "present": resolved is not None,
                        "membership_id": (
                            resolved.membership_id if resolved else None
                        ),
                        "version": (
                            resolved.version if resolved else None
                        ),
                    }
                )
                package_dependencies.append(serialized_dependency)
            serialized_packages.append(
                {
                    "id": package.id,
                    "membership_id": membership_id,
                    "name": slug,
                    "pretty_name": getattr(package, "pretty_name", slug),
                    "author": getattr(package, "author", None),
                    "description": getattr(package, "description", None),
                    "link": getattr(package, "link", None),
                    "version": version,
                    "minecraft": cls._optional_string(
                        getattr(package, "mcversion", None)
                    ),
                    "minecraft_versions": list(
                        compatibility_values(
                            getattr(package, "mcversion", None)
                        )
                    ),
                    "modloader": cls._optional_string(
                        getattr(package, "modloader", None)
                    ),
                    "modloaders": list(
                        compatibility_values(
                            getattr(package, "modloader", None),
                            modloaders=True,
                        )
                    ),
                    "side": str(
                        getattr(package, "side", "BOTH") or "BOTH"
                    ).upper(),
                    "type": modtype,
                    "modtype": modtype,
                    "bootstrap_managed": managed,
                    "url": download["url"],
                    "md5": download["md5"],
                    "filesize": download["filesize"],
                    "download": download,
                    "optional": state == 1,
                    "selection": {
                        "state": state,
                        "state_name": state_name,
                        "group_id": group.id if group else None,
                        "group_key": group.name if group else None,
                        "selected_by_default": selected_by_default,
                        "sort_order": (
                            int(group_item.sort_order) if group_item else 0
                        ),
                    },
                    "dependencies": package_dependencies,
                }
            )

        optional_mode_id = int(getattr(modpack, "optional_mode", 0) or 0)
        if optional_mode_id not in {0, 1}:
            raise BootstrapManifestError(
                "The modpack has an invalid optional mode."
            )
        manifest = {
            "schema": BOOTSTRAP_SCHEMA,
            "schema_version": BOOTSTRAP_SCHEMA_VERSION,
            "modpack": {
                "id": modpack.id,
                "slug": modpack.slug,
                "name": modpack.name,
            },
            "build": {
                "id": build.id,
                "version": build.version,
                "minecraft": build.minecraft,
                "modloader": cls._optional_string(
                    getattr(build, "modloader", None)
                ),
                "modloader_version": cls._optional_string(
                    getattr(build, "forge", None)
                ),
                "java": cls._optional_string(
                    getattr(build, "min_java", None)
                ),
                "java_runtime": cls._optional_string(
                    getattr(build, "java_runtime", None)
                ),
                "memory": getattr(build, "min_memory", 0),
            },
            "target": target,
            "optional_mode": {
                "id": optional_mode_id,
                "name": "advanced" if optional_mode_id == 1 else "basic",
            },
            "selection_policy": {
                "states": {
                    "0": "required",
                    "1": "optional",
                    "2": "excluded",
                },
                "ignored_modtypes": sorted(IGNORED_MODTYPES),
                "required_memberships": required_defaults,
                "default_memberships": selected_defaults,
            },
            "groups": serialized_groups,
            "packages": serialized_packages,
        }
        serialized = json.dumps(
            manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")
        manifest["manifest_hash"] = hashlib.sha256(serialized).hexdigest()
        return manifest

    @staticmethod
    def _selection_signature(manifest):
        """Return selection rules without build-local database identifiers."""
        groups = []
        for group in manifest["groups"]:
            groups.append(
                {
                    "key": group["key"],
                    "description": group["description"],
                    "selection_type": group["selection_type"],
                    "minimum": group["minimum"],
                    "maximum": group["maximum"],
                    "sort_order": group["sort_order"],
                    "choices": [
                        {
                            "slug": choice["slug"],
                            "state": choice["state"],
                            "selected_by_default": choice[
                                "selected_by_default"
                            ],
                            "sort_order": choice["sort_order"],
                        }
                        for choice in group["choices"]
                    ],
                }
            )
        packages = [
            {
                "name": package["name"],
                "bootstrap_managed": package["bootstrap_managed"],
                "state": package["selection"]["state"],
                "group_key": package["selection"]["group_key"],
                "selected_by_default": package["selection"][
                    "selected_by_default"
                ],
                "sort_order": package["selection"]["sort_order"],
            }
            for package in manifest["packages"]
        ]
        return {
            "optional_mode": manifest["optional_mode"],
            "groups": groups,
            "packages": packages,
        }

    @classmethod
    def changes(cls, previous, current):
        previous_packages = {
            package["name"]: package for package in previous["packages"]
        }
        current_packages = {
            package["name"]: package for package in current["packages"]
        }
        added = [
            package
            for package in current["packages"]
            if package["name"] not in previous_packages
        ]
        removed = [
            package
            for package in previous["packages"]
            if package["name"] not in current_packages
        ]
        updated = []
        for package in current["packages"]:
            old = previous_packages.get(package["name"])
            if old and (
                old["version"] != package["version"]
                or old["md5"] != package["md5"]
            ):
                updated.append({"from": old, "to": package})
        return {
            "from": previous["build"]["version"],
            "to": current["build"]["version"],
            "from_manifest_hash": previous["manifest_hash"],
            "added": added,
            "updated": updated,
            "removed": removed,
            "selection_changed": (
                cls._selection_signature(previous)
                != cls._selection_signature(current)
            ),
        }
