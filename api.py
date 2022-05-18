from dataclasses import dataclass
import json
import os
from dotenv import load_dotenv
from flask import Blueprint, jsonify, request

from db_config import get_api_key, select_all_modpacks_cid, select_modpack_cid, select_builds, select_modpack_build, select_mod_versions, select_mod

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
    key = get_api_key(key)
    if key:
        return jsonify(
            {
                "valid": "Key validated.",
                "name": key['name'],
                "created_at": "1970-01-01T00:00:00+00:00",
            }
        )
    else:
        return jsonify({"error": "Invalid key provided."})


@api.route("/api/modpack")
def modpack():
    data = select_all_modpacks_cid(request.args.get("cid"))
    modpacks = {}
    for pack in data:
        modpacks[pack["slug"]] = pack["name"]
    return jsonify({"modpacks": modpacks, "mirror_url": mirror_url})


@api.route("/api/modpack/<slug>")
def modpack_slug(slug: str):
    data = select_modpack_cid(slug, request.args.get("cid"))
    if data:
        builds_data = select_builds(data["id"])
        builds = [build["version"] for build in builds_data]
        data["builds"] = builds
        return jsonify(data)
    else:
        return jsonify({"error": "Modpack does not exist/Build does not exist"}), 404


@api.route("/api/modpack/<slug>/<build>")
def modpack_slug_build(slug: str, build: str):
    modpack = select_modpack_cid(slug, request.args.get("cid"))
    if not modpack:
        return jsonify({"error": "Modpack does not exist/Build does not exist"}), 404
    modpack_id = modpack["id"]

    modpack_data = select_modpack_build(modpack_id, build)
    if not modpack_data:
        return jsonify({"error": "Modpack does not exist/Build does not exist"}), 404

    mods = select_mod_versions(modpack_data["id"])
    for mod in mods:
        name = select_mod(mod["mod_id"])["name"]
        mod["url"] = mirror_url + name + "/" + name + "-" + mod["version"] + ".zip"

    modpack_data["mods"] = mods
    return jsonify(modpack_data)


@api.route("/api/mod")
def mod():
    return jsonify(
        {"error": "Mod does not exist"}
    ), 404


@api.route("/api/mod/<name>")
def mod_name(name: str):
    return jsonify(
        {
            "name": "testmod",
            "pretty_name": "TestMod",
            "author": "Technic",
            "description": "This is a test mod for Solder",
            "link": "http://solder.io",
            "donate": None,
            "versions": ["0.1"],
        }
    )


@api.route("/api/mod/<name>/<version>")
def mod_name_version(name: str, version: str):
    return jsonify(
        {
            "md5": "fb6582e4d9c9bc208181907ecc108eb1",
            "url": "http://technic.pagefortress.com/mods/testmod/testmod-0.1.zip",
        }
    )
