from flask import Blueprint, jsonify, request
from models.key import Key
from models.mod import Mod
from models.modpack import Modpack
from models.globals import solderpy_version, mirror_url

api = Blueprint("api", __name__)


@api.route("/api/")
def api_info():
    return jsonify({"api": "solder.py", "version": "v" + solderpy_version, "stream": "DEV"})


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
    else:
        return jsonify({"error": "Invalid key provided."})


@api.route("/api/modpack")
def modpack():
    modpacks = Modpack.get_by_cid(request.args.get("cid"))
    return jsonify({"modpacks": {modpack.slug: modpack.name for modpack in modpacks}, "mirror_url": mirror_url})


@api.route("/api/modpack/<slug>")
def modpack_slug(slug: str):
    modpack = Modpack.get_by_cid_slug(request.args.get("cid"), slug)
    if modpack:
        modpack.builds = modpack.get_builds()
        return jsonify(modpack.to_json())
    else:
        return jsonify({"error": "Modpack does not exist/Build does not exist"}), 404


@api.route("/api/modpack/<slug>/<build>")
def modpack_slug_build(slug: str, build: str):
    modpack = Modpack.get_by_cid_slug(request.args.get("cid"), slug)
    if not modpack:
        return jsonify({"error": "Modpack does not exist/Build does not exist"}), 404
    build = modpack.get_build(build)
    if not build:
        return jsonify({"error": "Modpack does not exist/Build does not exist"}), 404
    modversions = build.get_modversions_minimal()
    moddata = []
    for mv in modversions:
        moddata.append(
            {
                "name": mv.modname,
                "version": mv.version,
                "md5": mv.md5,
                "url": f"{mirror_url}/mods/{mv.modname}/{mv.version}.zip",
            }
        )
    return {"minecraft": build.minecraft, "java": build.min_java, "memory": build.min_memory, "forge": None, "mods": moddata}


@api.route("/api/mod")
def mod():
    return jsonify(
        {"error": "Mod does not exist"}
    ), 404


@api.route("/api/mod/<name>")
def mod_name(name: str):
    mod = Mod.get_by_name(name)
    if not mod:
        return jsonify({"error": "Mod does not exist"}), 404
    else:
        versions = mod.get_versions()
        res = mod.to_json()
        res["versions"] = [v.version for v in versions]
        return jsonify(res)


@api.route("/api/mod/<name>/<version>")
def mod_name_version(name: str, version: str):
    mod = Mod.get_by_name(name)
    if not mod:
        return jsonify({"error": "Mod does not exist"}), 404
    version = mod.get_version(version)
    if not version:
        return jsonify({"error": "Mod version does not exist"}), 404
    else:
        res = version.to_json()
        res["url"] = f"{mirror_url}{name}/{version.version}.zip"
        return jsonify(res)
