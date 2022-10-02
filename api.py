import os
from dotenv import load_dotenv
from flask import Blueprint, jsonify, request

from models.key import Key
from models.modpack import Modpack
from db_config import select_all_modpacks_cid, select_modpack_cid, select_builds, select_modpack_build, select_mod_versions_from_build, select_mod_name, select_mod_versions, select_mod_version

api = Blueprint("api", __name__)

load_dotenv(".env")
mirror_url = os.getenv("SOLDER_MIRROR_URL")

@api.route("/api/")
def api_info():
    return jsonify({"api": "solder.py", "version": "v0.0.1a", "stream": "DEV"})


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
    modpack = select_modpack_cid(slug, request.args.get("cid"))
    if not modpack:
        return jsonify({"error": "Modpack does not exist/Build does not exist"}), 404
    modpack_id = modpack["id"]

    build_data = select_modpack_build(modpack_id, build)
    if not build_data:
        return jsonify({"error": "Modpack does not exist/Build does not exist"}), 404

    mods = select_mod_versions_from_build(build_data["id"])
    for mod in mods:
        mod["url"] = mirror_url + mod["name"] + "/" + mod["name"] + "-" + mod["version"] + ".zip"

    build_data["mods"] = mods
    return jsonify(build_data)


@api.route("/api/mod")
def mod():
    return jsonify(
        {"error": "Mod does not exist"}
    ), 404


@api.route("/api/mod/<name>")
def mod_name(name: str):
    mod = select_mod_name(name)
    if not mod:
        return jsonify({"error": "Mod does not exist"}), 404
    else:
        mod["versions"] = select_mod_versions(mod["id"])
        return jsonify(mod)


@api.route("/api/mod/<name>/<version>")
def mod_name_version(name: str, version: str):
    mod = select_mod_name(name)
    if not mod:
        return jsonify({"error": "Mod does not exist"}), 404
    version = select_mod_version(mod["id"], version)
    if not version:
        return jsonify({"error": "Mod version does not exist"}), 404
    else:
        version["url"] = mirror_url + name + "/" + name + "-" + version["version"] + ".zip"
        return jsonify(version)

