from threading import RLock

from cachetools import cached, TTLCache
from flask import Blueprint, jsonify, request

from models.common import cache_size, cache_ttl, public_repo_url, solderpy_version
from models.key import Key
from models.mod import Mod
from models.modpack import Modpack

api = Blueprint("api", __name__)


def _api_cached(key):
    # Gunicorn serves this application with multiple threads, while cachetools
    # cache objects require external synchronization for shared access.
    return cached(TTLCache(cache_size, cache_ttl), key=key, lock=RLock())


def _cache_key(*path_parts):
    return (
        *path_parts,
        request.args.get("cid"),
        request.args.get("include"),
        request.args.get("k"),
    )


def _has_valid_api_key():
    supplied_key = request.args.get("k")
    return bool(supplied_key and Key.get_key(supplied_key))


def _get_accessible_modpack(slug, cid, api_key):
    if api_key:
        return Modpack.get_all_by_slug_api(slug)
    return Modpack.get_by_cid_slug_api(cid, slug)


def _get_requested_build(modpack, requested_version, cid, api_key):
    # Prefer a real build whose name ends in -optional or -server. The virtual
    # solder.py variants are only considered when no exact build exists.
    build = modpack.get_build_api(requested_version, cid=cid, api_key=api_key)
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
            build = modpack.get_build_api(
                base_version, cid=cid, api_key=api_key
            )
            if build:
                return build, tag
    return None, ""


def _mod_download_url(mod_name, version):
    return f"{public_repo_url}{mod_name}/{mod_name}-{version}.zip"


@api.route("/api/")
def api_info():
    return jsonify(
        {"api": "solder.py", "version": "v" + solderpy_version, "stream": "DEV"}
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
    if api_key:
        modpacks = Modpack.get_all_api()
    else:
        modpacks = Modpack.get_by_cid_api(cid)
    if request.args.get("include") == "full":
        full_modpacks = {}
        for current_modpack in modpacks:
            current_modpack.builds = current_modpack.get_builds_api(
                cid=cid, api_key=api_key
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
    current_modpack = _get_accessible_modpack(slug, cid, api_key)
    if not current_modpack:
        return jsonify({"error": "Modpack does not exist"}), 404

    current_modpack.builds = current_modpack.get_builds_api(
        cid=cid, api_key=api_key
    )
    return jsonify(current_modpack.to_json())


@api.route("/api/modpack/<slugstring>/<buildstring>")
@_api_cached(
    key=lambda slugstring, buildstring: _cache_key(slugstring, buildstring),
)
def modpack_slug_build(slugstring: str, buildstring: str):
    cid = request.args.get("cid")
    api_key = _has_valid_api_key()
    current_modpack = _get_accessible_modpack(slugstring, cid, api_key)
    if not current_modpack:
        return jsonify({"error": "Modpack does not exist"}), 404

    build, buildtag = _get_requested_build(
        current_modpack, buildstring, cid, api_key
    )
    if not build:
        return jsonify({"error": "Build does not exist"}), 404
    modversions = build.get_modversions_api(buildtag)
    moddata = []
    if request.args.get("include") == "mods":
        for mv in modversions:
            moddata.append(
                {
                    "id": mv.id,
                    "name": mv.modname,
                    "version": mv.version,
                    "md5": mv.md5,
                    "filesize": mv.filesize,
                    "url": _mod_download_url(mv.modname, mv.version),
                    "pretty_name": mv.pretty_name,
                    "author": mv.author,
                    "description": mv.description,
                    "link": mv.link,
                }
            )
    else:
        for mv in modversions:
            moddata.append(
                {
                    "id": mv.id,
                    "name": mv.modname,
                    "version": mv.version,
                    "md5": mv.md5,
                    "filesize": mv.filesize,
                    "url": _mod_download_url(mv.modname, mv.version),
                }
            )
    return jsonify(
        {
            "id": build.id,
            "minecraft": build.minecraft,
            "java": build.min_java,
            "memory": build.min_memory,
            "forge": build.forge,
            "mods": moddata,
        }
    )


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
    res["builds"] = modversion.get_builds_api(
        cid=request.args.get("cid"), api_key=_has_valid_api_key()
    )
    return jsonify(res)
