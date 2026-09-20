import hashlib
import json
import logging
from threading import RLock

from cachetools import cached, TTLCache
from flask import Blueprint, g, jsonify, request

from models.common import (
    cache_size,
    cache_ttl,
    public_repo_url,
    solderpy_version,
    write_api,
)
from models.compatibility import compatibility_values
from models.api_token import ApiToken
from models.bootstrap_manifest import (
    BOOTSTRAP_SCHEMA_VERSION,
    BootstrapManifest,
    BootstrapManifestError,
)
from models.key import Key
from models.mod import Mod
from models.modversion import Modversion
from models.mod_dependency import ModDependency
from models.modpack import Modpack
from models.advanced_optional import AdvancedOptional
from models.integration import IntegrationError, ModrinthProvider
from models.maven import MavenArtifact, MavenError
from models.platform_export_override import PlatformExportOverride
from models.technic_solderpy_loader import TechnicSolderPyLoader

api = Blueprint("api", __name__)
_api_caches = []
logger = logging.getLogger(__name__)


def _api_cached(key):
    # Gunicorn serves this application with multiple threads, while cachetools
    # cache objects require external synchronization for shared access.
    cache = TTLCache(cache_size, cache_ttl)
    _api_caches.append(cache)
    return cached(cache, key=key, lock=RLock())


def clear_api_caches():
    """Discard read responses after a successful write in this process."""
    for cache in _api_caches:
        cache.clear()


@_api_cached(key=lambda references: references)
def _bootstrap_maven_downloads(references):
    """Resolve Maven JARs only for artifacts that explicitly opt in."""
    if not references:
        return {}
    try:
        return MavenArtifact.solderpy_loader_downloads(references)
    except MavenError:
        logger.warning(
            "Could not resolve Maven bootstrap sources; using Solder JARs.",
            exc_info=True,
        )
        return {}


def _backfill_bootstrap_modrinth_downloads(packages, minecraft, modloader):
    """Persist native metadata once for Modrinth versions imported previously."""
    missing = [
        package
        for package in packages
        if str(getattr(package, "integration_provider", "") or "").upper()
        == "MODRINTH"
        and getattr(package, "integration_project_id", None)
        and getattr(package, "integration_version_id", None)
        and not getattr(package, "download_source_url", None)
        and str(getattr(package, "modtype", "") or "").upper() == "MOD"
        and Modversion.JAR_MD5_PATTERN.fullmatch(
            str(getattr(package, "jarmd5", "") or "").strip()
        )
    ]
    if not missing:
        return

    references = tuple(
        sorted(
            {
                (
                    str(package.integration_project_id),
                    str(package.integration_version_id),
                )
                for package in missing
            }
        )
    )
    provider = ModrinthProvider()
    try:
        versions = provider.get_versions(references, minecraft, modloader)
    except (IntegrationError, KeyError):
        logger.warning(
            "Could not backfill Modrinth bootstrap sources; using Solder JARs."
        )
        return

    for package in missing:
        version = versions.get(str(package.integration_version_id))
        if version is None:
            continue
        try:
            provider._validate_download_url(version.download_url)
            source = Modversion.normalize_download_source(
                {
                    "provider": "MODRINTH",
                    "url": version.download_url,
                    "filename": version.filename,
                    "md5": package.jarmd5,
                    "sha1": version.hashes.get("sha1"),
                    "sha512": version.hashes.get("sha512"),
                    "filesize": package.jarfilesize or version.size,
                }
            )
        except (IntegrationError, ValueError):
            logger.warning(
                "Modrinth returned invalid bootstrap source metadata; using "
                "the Solder JAR."
            )
            continue
        try:
            Modversion.store_download_source(package.id, source)
        except Exception:
            logger.warning(
                "Could not persist a Modrinth bootstrap source; using the "
                "resolved URL for this response.",
                exc_info=True,
            )
        package.download_source_provider = source["provider"]
        package.download_source_url = source["url"]
        package.download_source_filename = source["filename"]
        package.download_source_md5 = source["md5"]
        package.download_source_sha1 = source["sha1"]
        package.download_source_sha512 = source["sha512"]
        package.download_source_filesize = source["filesize"]


def _cache_key(*path_parts):
    # Include every query argument that can influence a response. This keeps
    # extension arguments such as target/optional/from from sharing a cached
    # response, while also making argument order irrelevant.
    query_arguments = tuple(
        (name, tuple(request.args.getlist(name)))
        for name in sorted(request.args.keys())
    )
    principal = _read_principal()
    bearer_identity = principal.cache_identity if principal else None
    return (*path_parts, query_arguments, bearer_identity)


def _read_principal():
    """Authenticate Technic-style read requests when write API is enabled."""
    if not write_api:
        return None
    if not hasattr(g, "read_api_principal"):
        g.read_api_principal = ApiToken.authenticate(
            request.headers.get("Authorization"), touch=False
        )
    return g.read_api_principal


def _has_valid_api_key():
    supplied_key = request.args.get("k")
    return bool(supplied_key and Key.get_key(supplied_key))


def _principal_can_access(principal, modpack):
    return bool(principal and principal.can_access_modpack(modpack.id))


def _get_accessible_modpack(slug, cid, api_key, principal=None):
    if api_key:
        return Modpack.get_all_by_slug_api(slug)
    if principal:
        if principal.permissions.get("solder_full"):
            return Modpack.get_all_by_slug_api(slug)
        client_visible = Modpack.get_by_cid_slug_api(cid, slug)
        if client_visible:
            return client_visible
        modpack = Modpack.get_all_by_slug_api(slug)
        if modpack and _principal_can_access(principal, modpack):
            return modpack
        return None
    return Modpack.get_by_cid_slug_api(cid, slug)


def _get_build_by_version_or_channel(modpack, requested_version, cid, api_key):
    build = modpack.get_build_api(requested_version, cid=cid, api_key=api_key)
    if build:
        return build

    if requested_version in {"recommended", "latest"}:
        channel_version = getattr(modpack, requested_version, None)
        if channel_version:
            return modpack.get_build_api(
                channel_version, cid=cid, api_key=api_key
            )
    return None


def _get_requested_build(modpack, requested_version, cid, api_key):
    # Prefer a real build whose name ends in -optional or -server. The virtual
    # solder.py variants are only considered when no exact build exists.
    build = _get_build_by_version_or_channel(
        modpack, requested_version, cid, api_key
    )
    if build:
        return build, ""

    variants = (
        ("-optional", "optional", modpack.enable_optionals),
        ("-server", "server", modpack.enable_server),
    )
    for suffix, tag, enabled in variants:
        if enabled and requested_version.endswith(suffix):
            base_version = requested_version[: -len(suffix)]
            if not base_version:
                break
            build = _get_build_by_version_or_channel(
                modpack, base_version, cid, api_key
            )
            if build:
                return build, tag
    return None, ""


def _boolean_argument(name, default=False):
    value = request.args.get(name)
    if value is None:
        return default
    normalized = value.casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _manifest_options(legacy_variant):
    requested_target = request.args.get("target")
    target = (requested_target or "client").casefold()
    if target not in {"client", "server"}:
        raise ValueError("target must be client or server")

    optional_was_requested = request.args.get("optional") is not None
    include_optional = _boolean_argument("optional", default=False)

    if legacy_variant == "server":
        if requested_target is not None and target != "server":
            raise ValueError("The -server build suffix requires target=server")
        target = "server"
    elif legacy_variant == "optional":
        if requested_target is not None and target != "client":
            raise ValueError("The -optional build suffix requires target=client")
        if optional_was_requested and not include_optional:
            raise ValueError("The -optional build suffix requires optional=true")
        include_optional = True

    return target, include_optional


def _mod_download_url(mod_name, version):
    return f"{public_repo_url}{mod_name}/{mod_name}-{version}.zip"


def _optional_string_attribute(value):
    return value if isinstance(value, str) else None


def _mod_manifest_entry(
    modversion, expanded=False, extended=False, dependencies=None
):
    entry = {
        "id": modversion.id,
        "name": modversion.modname,
        "version": modversion.version,
        "md5": modversion.md5,
        "filesize": modversion.filesize,
        "url": _mod_download_url(modversion.modname, modversion.version),
    }
    if expanded:
        entry.update(
            {
                "pretty_name": modversion.pretty_name,
                "author": modversion.author,
                "description": modversion.description,
                "link": modversion.link,
            }
        )
    if expanded or extended:
        entry.update(
            {
                "side": getattr(modversion, "side", "BOTH"),
                "type": getattr(modversion, "modtype", "MOD"),
                "modtype": getattr(modversion, "modtype", "MOD"),
                "minecraft": _optional_string_attribute(
                    getattr(modversion, "mcversion", None)
                ),
                "modloader": _optional_string_attribute(
                    getattr(modversion, "modloader", None)
                ),
                "minecraft_versions": list(
                    getattr(modversion, "minecraft_versions", ())
                    or compatibility_values(
                        getattr(modversion, "mcversion", None)
                    )
                ),
                "modloaders": list(
                    compatibility_values(
                        getattr(modversion, "modloader", None),
                        modloaders=True,
                    )
                ),
                "optional": int(getattr(modversion, "optional", 0) or 0) == 1,
                "dependencies": dependencies or [],
            }
        )
    return entry


def _mod_manifest_entries(modversions, build_id, expanded=False, extended=False):
    dependencies = {}
    if expanded or extended:
        dependencies = ModDependency.get_for_build_api(build_id)
    return [
        _mod_manifest_entry(
            modversion,
            expanded=expanded,
            extended=extended,
            dependencies=dependencies.get(getattr(modversion, "mod_id", None), []),
        )
        for modversion in modversions
    ]


def _technic_solderpy_loader_manifest(
    modversions, build, modpack, *, target, expanded=False, extended=False
):
    """Let SolderPy Loader own packages for one configured Technic build."""
    if target != "client":
        return modversions, []
    configuration = TechnicSolderPyLoader.get_active(build.id)
    if configuration is None:
        return modversions, []
    if (
        getattr(configuration, "delivery_mode", TechnicSolderPyLoader.LOADER_DELIVERY)
        == TechnicSolderPyLoader.TECHNIC_DELIVERY
    ):
        # This list already contains only the normal Technic/basic selection.
        # SolderPy Loader receives optional and excluded packages separately.
        retained = modversions
    else:
        # Technic must still install its modloader and the initial bootstrap.
        # The loader fetches every other package from the dedicated bootstrap
        # API, so leaving normal entries here would install them twice.
        retained = [
            modversion
            for modversion in modversions
            if str(getattr(modversion, "modtype", "") or "").upper()
            in {"LAUNCHER", "BOOTSTRAP", "MCIL"}
        ]
    return retained, configuration.manifest_entries(
        public_repo_url, expanded=expanded, extended=extended
    )


def _manifest_changes(previous, current, from_version, to_version):
    previous_by_name = {mod["name"]: mod for mod in previous}
    current_by_name = {mod["name"]: mod for mod in current}

    added = [
        mod for mod in current if mod["name"] not in previous_by_name
    ]
    removed = [
        mod for mod in previous if mod["name"] not in current_by_name
    ]
    updated = []
    for current_mod in current:
        old_mod = previous_by_name.get(current_mod["name"])
        if old_mod and (
            old_mod["version"] != current_mod["version"]
            or old_mod["md5"] != current_mod["md5"]
        ):
            updated.append({"from": old_mod, "to": current_mod})

    return {
        "from": from_version,
        "to": to_version,
        "added": added,
        "updated": updated,
        "removed": removed,
    }


def _add_manifest_hash(manifest):
    serialized = json.dumps(
        manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    manifest["manifest_hash"] = hashlib.sha256(serialized).hexdigest()


@api.route("/api/")
def api_info():
    return jsonify(
        {
            "api": "solder.py",
            "version": "v" + solderpy_version,
            "stream": "DEV",
            "capabilities": {
                "advanced_optionals": True,
                "bootstrap_manifest": True,
                "bootstrap_schema": BOOTSTRAP_SCHEMA_VERSION,
                "build_channels": True,
                "build_comparison": True,
                "optional_manifests": True,
                "server_manifests": True,
                "write_api": write_api,
            },
        }
    )


@api.route("/api/verify")
def verify():
    return jsonify({"error": "No API key provided."})


@api.route("/api/verify/<key>")
def verify_key(key: str = None):
    key = Key.get_key(key)
    if key:
        return jsonify(
            {
                "valid": "Key validated.",
                "name": key.name,
                "created_at": "1970-01-01T00:00:00+00:00",
            }
        )
    return jsonify({"error": "Invalid key provided."})


@api.route("/api/modpack")
@_api_cached(key=lambda: _cache_key())
def modpack():
    cid = request.args.get("cid")
    api_key = _has_valid_api_key()
    principal = _read_principal()
    if api_key:
        modpacks = Modpack.get_all_api()
    elif principal and principal.permissions.get("solder_full"):
        modpacks = Modpack.get_all_api()
    elif principal:
        client_visible = {
            current.id: current for current in Modpack.get_by_cid_api(cid)
        }
        for current in Modpack.get_all_api():
            if _principal_can_access(principal, current):
                client_visible[current.id] = current
        modpacks = sorted(client_visible.values(), key=lambda current: current.id)
    else:
        modpacks = Modpack.get_by_cid_api(cid)
    if request.args.get("include") == "full":
        full_modpacks = {}
        for current_modpack in modpacks:
            privileged = api_key or _principal_can_access(
                principal, current_modpack
            )
            current_modpack.builds = current_modpack.get_builds_api(
                cid=cid, api_key=privileged
            )
            full_modpacks[current_modpack.slug] = current_modpack.to_json()
        return jsonify({"modpacks": full_modpacks, "mirror_url": public_repo_url})
    return jsonify(
        {
            "modpacks": {
                current_modpack.slug: current_modpack.name
                for current_modpack in modpacks
            },
            "mirror_url": public_repo_url,
        }
    )


@api.route("/api/modpack/<slug>")
@_api_cached(key=lambda slug: _cache_key(slug))
def modpack_slug(slug: str):
    cid = request.args.get("cid")
    api_key = _has_valid_api_key()
    principal = _read_principal()
    current_modpack = _get_accessible_modpack(
        slug, cid, api_key, principal
    )
    if not current_modpack:
        return jsonify({"error": "Modpack does not exist"}), 404

    privileged = api_key or _principal_can_access(principal, current_modpack)
    current_modpack.builds = current_modpack.get_builds_api(
        cid=cid, api_key=privileged
    )
    return jsonify(current_modpack.to_json())


@api.route("/api/modpack/<slugstring>/<buildstring>")
@_api_cached(
    key=lambda slugstring, buildstring: _cache_key(slugstring, buildstring),
)
def modpack_slug_build(slugstring: str, buildstring: str):
    cid = request.args.get("cid")
    api_key = _has_valid_api_key()
    principal = _read_principal()
    current_modpack = _get_accessible_modpack(
        slugstring, cid, api_key, principal
    )
    if not current_modpack:
        return jsonify({"error": "Modpack does not exist"}), 404

    privileged = api_key or _principal_can_access(principal, current_modpack)
    build, buildtag = _get_requested_build(
        current_modpack, buildstring, cid, privileged
    )
    if not build:
        return jsonify({"error": "Build does not exist"}), 404

    try:
        target, include_optional = _manifest_options(buildtag)
    except ValueError:
        # The parser raises only for invalid public query arguments. Do not
        # serialize exception objects into an HTTP response: keeping the
        # response static also prevents future parser errors leaking details.
        return jsonify({"error": "Invalid manifest options"}), 400

    if target == "server" and not current_modpack.enable_server:
        return jsonify({"error": "Server manifests are not enabled"}), 404
    if include_optional and not current_modpack.enable_optionals:
        return jsonify({"error": "Optional manifests are not enabled"}), 404

    expanded = request.args.get("include") == "mods"
    extended = (
        request.args.get("target") is not None
        or request.args.get("optional") is not None
        or request.args.get("from") is not None
        or buildstring in {"recommended", "latest"}
    )
    modversions = build.get_modversions_api(
        target=target, include_optional=include_optional
    )
    modversions, bootstrap_entries = _technic_solderpy_loader_manifest(
        modversions,
        build,
        current_modpack,
        target=target,
        expanded=expanded,
        extended=extended,
    )
    moddata = _mod_manifest_entries(
        modversions, build.id, expanded=expanded, extended=extended
    )
    moddata.extend(bootstrap_entries)
    manifest = {
        "id": build.id,
        "minecraft": build.minecraft,
        "java": build.min_java,
        "java_runtime": _optional_string_attribute(
            getattr(build, "java_runtime", None)
        ),
        "memory": build.min_memory,
        "forge": build.forge,
        "mods": moddata,
    }

    if extended:
        build_modloader = _optional_string_attribute(
            getattr(build, "modloader", None)
        )
        manifest.update(
            {
                "modpack": current_modpack.slug,
                "version": build.version,
                "modloader": build_modloader,
                "target": target,
                "optional": include_optional,
            }
        )
        _add_manifest_hash(manifest)

    from_version = request.args.get("from")
    if from_version:
        previous_build = _get_build_by_version_or_channel(
            current_modpack, from_version, cid, privileged
        )
        if not previous_build:
            return jsonify({"error": "Comparison build does not exist"}), 404
        previous_versions = previous_build.get_modversions_api(
            target=target, include_optional=include_optional
        )
        previous_versions, previous_bootstrap_entries = (
            _technic_solderpy_loader_manifest(
                previous_versions,
                previous_build,
                current_modpack,
                target=target,
                expanded=expanded,
                extended=True,
            )
        )
        previous_data = _mod_manifest_entries(
            previous_versions,
            previous_build.id,
            expanded=expanded,
            extended=True,
        )
        previous_data.extend(previous_bootstrap_entries)
        manifest["changes"] = _manifest_changes(
            previous_data, moddata, previous_build.version, build.version
        )

    response = jsonify(manifest)
    if extended:
        response.set_etag(manifest["manifest_hash"])
    return response


@api.route("/api/modpack/<slugstring>/<buildstring>/bootstrap")
def modpack_bootstrap(slugstring: str, buildstring: str):
    """Return the complete build model for a dedicated bootstrap client.

    This deliberately does not use the Technic/SolderPy Loader transformation:
    a Solder-aware client needs every stored package and the source advanced
    selection rules so that it can make the choice itself.
    """
    cid = request.args.get("cid")
    api_key = _has_valid_api_key()
    principal = _read_principal()
    current_modpack = _get_accessible_modpack(
        slugstring, cid, api_key, principal
    )
    if not current_modpack:
        return jsonify({"error": "Modpack does not exist"}), 404

    privileged = api_key or _principal_can_access(principal, current_modpack)
    build = _get_build_by_version_or_channel(
        current_modpack, buildstring, cid, privileged
    )
    if not build:
        return jsonify({"error": "Build does not exist"}), 404

    target = (request.args.get("target") or "client").casefold()
    source_mode = (request.args.get("source") or "hybrid").casefold()
    platform = (request.args.get("platform") or "").casefold()
    if (
        target not in {"client", "server"}
        or source_mode not in {"hybrid", "solder"}
        or platform not in {
            "",
            "modrinth",
            "curseforge",
            "prism",
            "technic",
        }
    ):
        return jsonify({"error": "Invalid bootstrap options"}), 400
    if target == "server" and not current_modpack.enable_server:
        return jsonify({"error": "Server manifests are not enabled"}), 404

    def render_manifest(selected_build):
        packages = selected_build.get_modversions_api(
            target=target,
            include_optional=True,
            include_excluded=True,
            include_download_overrides=True,
            include_download_sources=True,
        )
        if platform == "technic":
            # Required packages are installed by Technic's normal Solder API.
            # Only optional/excluded content belongs to SolderPy Loader.
            packages = [
                package
                for package in packages
                if int(getattr(package, "optional", 0) or 0) != 0
            ]
        elif source_mode == "hybrid" and platform == "modrinth":
            packages = [
                package
                for package in packages
                if not (
                    str(package.integration_provider or "").upper()
                    == "MODRINTH"
                    and package.integration_project_id
                    and package.integration_version_id
                    and int(getattr(package, "optional", 0) or 0) != 2
                )
            ]
        elif source_mode == "hybrid" and platform == "curseforge":
            native_projects = {
                str(override.modrinth_project_id)
                for override in PlatformExportOverride.get_enabled()
            }
            packages = [
                package
                for package in packages
                if not (
                    str(package.integration_provider or "").upper()
                    == "MODRINTH"
                    and str(package.integration_project_id or "")
                    in native_projects
                )
            ]
        if source_mode == "hybrid":
            _backfill_bootstrap_modrinth_downloads(
                packages,
                str(selected_build.minecraft),
                getattr(selected_build, "modloader", None),
            )
        def integration_references(provider):
            return tuple(
                sorted(
                    {
                        (
                            str(package.integration_project_id),
                            str(package.integration_version_id),
                        )
                        for package in packages
                        if str(
                            getattr(package, "integration_provider", "") or ""
                        ).upper()
                        == provider
                        and getattr(package, "integration_project_id", None)
                        and getattr(package, "integration_version_id", None)
                        and str(
                            getattr(package, "modtype", "") or ""
                        ).upper()
                        == "MOD"
                        and Modversion.JAR_MD5_PATTERN.fullmatch(
                            str(getattr(package, "jarmd5", "") or "").strip()
                        )
                    }
                )
            )

        maven_references = (
            integration_references("MAVEN")
            if source_mode == "hybrid"
            else ()
        )
        maven_downloads = _bootstrap_maven_downloads(maven_references)
        native_downloads = {
            ("MAVEN", project_id, version_id): url
            for (project_id, version_id), url in maven_downloads.items()
        }
        return BootstrapManifest.render(
            current_modpack,
            selected_build,
            packages,
            AdvancedOptional.get_active_groups(selected_build.id),
            public_repo_url,
            ModDependency.get_for_build_api(selected_build.id),
            target=target,
            native_downloads=native_downloads,
            source_mode=source_mode,
        )

    try:
        manifest = render_manifest(build)
        from_version = request.args.get("from")
        if from_version:
            previous_build = _get_build_by_version_or_channel(
                current_modpack, from_version, cid, privileged
            )
            if not previous_build:
                return (
                    jsonify({"error": "Comparison build does not exist"}),
                    404,
                )
            previous_manifest = render_manifest(previous_build)
            manifest["changes"] = BootstrapManifest.changes(
                previous_manifest, manifest
            )
    except BootstrapManifestError:
        # Stored integrity failures are not safe to expose to anonymous API
        # users, but should still produce a stable machine-readable response.
        return jsonify({"error": "Invalid bootstrap manifest data"}), 422

    response = jsonify(manifest)
    response_etag = manifest["manifest_hash"]
    if "changes" in manifest:
        response_etag = hashlib.sha256(
            (
                response_etag
                + ":"
                + manifest["changes"]["from_manifest_hash"]
            ).encode("ascii")
        ).hexdigest()
    response.set_etag(response_etag)
    response.cache_control.no_cache = True
    response.cache_control.must_revalidate = True
    if cid or request.args.get("k") or request.headers.get("Authorization"):
        response.cache_control.private = True
    else:
        response.cache_control.public = True
    return response.make_conditional(request)


@api.route("/api/mod")
@_api_cached(key=lambda: _cache_key())
def mod():
    mods = Mod.get_all_api()
    return jsonify(
        {
            "mods": {
                current_mod.name: current_mod.pretty_name for current_mod in mods
            }
        }
    )


@api.route("/api/mod/<name>")
@_api_cached(key=lambda name: _cache_key(name))
def mod_name(name: str):
    mods = Mod.get_by_name_api(name)
    if not mods:
        return jsonify({"error": "Mod does not exist"}), 404

    versions = mods.get_versions_api()
    res = mods.to_json()
    res["id"] = mods.id
    res["versions"] = [version["version"] for version in versions]
    res["dependencies"] = ModDependency.get_by_mod_api(mods.id)
    return jsonify(res)


@api.route("/api/mod/<name>/<version>")
@_api_cached(key=lambda name, version: _cache_key(name, version))
def mod_name_version(name: str, version: str):
    mod = Mod.get_by_name_api(name)
    if not mod:
        return jsonify({"error": "Mod does not exist"}), 404
    modversion = mod.get_version_api(version)
    if not modversion:
        return jsonify({"error": "Mod version does not exist"}), 404

    res = modversion.to_json()
    res["id"] = modversion.id
    res["url"] = _mod_download_url(name, modversion.version)
    res["side"] = mod.side
    res["type"] = mod.modtype
    res["modtype"] = mod.modtype
    res["modloader"] = _optional_string_attribute(
        getattr(modversion, "modloader", None)
    )
    res["dependencies"] = ModDependency.get_by_mod_api(mod.id)
    principal = _read_principal()
    build_query = {
        "cid": request.args.get("cid"),
        "api_key": _has_valid_api_key()
        or bool(principal and principal.permissions.get("solder_full")),
    }
    if principal and not principal.permissions.get("solder_full"):
        build_query["modpack_ids"] = principal.accessible_modpack_ids
    res["builds"] = modversion.get_builds_api(**build_query)
    return jsonify(res)
