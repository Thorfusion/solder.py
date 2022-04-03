import json
import os
from os import getenv

from dotenv import load_dotenv
from flask import Blueprint, jsonify, request

load_dotenv(".env")

api_key = getenv("API_KEY")
mirror_url = getenv("URL")

api = Blueprint("api", __name__)


@api.route("/api/")
def api():
    return jsonify({"api": "solder.py", "version": "v0.0.1a", "stream": "DEV"})


@api.route("/api/verify")
def verify(key: str = None):
    return jsonify({"error": "No API key provided."})


@api.route("/api/verify/<key>")
def verify_key(key: str = None):
    if key == api_key:
        return jsonify(
            {
                "valid": "Key validated.",
                "name": "API KEY",
                "created_at": "1970-01-01T00:00:00+00:00",
            }
        )
    else:
        return jsonify({"error": "Invalid key provided."})


@api.route("/api/modpack")
def modpack():
    modpacks: dict = {}
    for filename in os.listdir("./modpacks"):
        with open(f"modpacks/{filename}", "r") as f:
            info = json.load(f)
            if request.args.get("include") == "full":
                fulldata: dict = {}
                fulldata["name"] = filename.removesuffix(".json")
                fulldata["display_name"] = info["display_name"]
                fulldata["url"] = info["url"]
                fulldata["icon"] = info["icon"]
                fulldata["icon_md5"] = info["icon_md5"]
                fulldata["logo"] = info["logo"]
                fulldata["logo_md5"] = info["logo_md5"]
                fulldata["background"] = info["background"]
                fulldata["background_md5"] = info["background_md5"]
                fulldata["recommended"] = info["recommended"]
                fulldata["latest"] = info["latest"]
                builds: list = []
                for version in info["builds"]:
                    builds.apiend(version["version"])
                fulldata["builds"] = builds
                modpacks[filename.removesuffix(".json")] = fulldata
            else:
                modpacks[filename.removesuffix(".json")] = info["display_name"]
    return jsonify({"modpacks": modpacks, "mirror_url": mirror_url})


@api.route("/api/modpack/<slug>")
def modpack_slug(slug: str):
    try:
        with open(f"modpacks/{slug}.json", "r") as f:
            info = json.load(f)
            modpack: dict = {}

            modpack["name"] = slug
            modpack["display_name"] = info["display_name"]
            modpack["url"] = info["url"]
            modpack["icon"] = info["icon"]
            modpack["icon_md5"] = info["icon_md5"]
            modpack["logo"] = info["logo"]
            modpack["logo_md5"] = info["logo_md5"]
            modpack["background"] = info["background"]
            modpack["background_md5"] = info["background_md5"]
            modpack["recommended"] = info["recommended"]
            modpack["latest"] = info["latest"]
            builds = []
            for version in info["builds"]:
                builds.apiend(version["version"])
            modpack["builds"] = builds
            return jsonify(modpack)
    except FileNotFoundError:
        return jsonify({"error": "Modpack does not exist/Build does not exist"})


@api.route("/api/modpack/<slug>/<build>")
def modpack_slug_build(slug: str, build: str):
    return jsonify(
        {
            "minecraft": "1.5.1",
            "minecraft_md5": "5c1219d869b87d233de3033688ec7567",
            "forge": None,
            "mods": [
                {
                    "name": "basemods",
                    "version": "tekkitmain-v1.0.2",
                    "md5": "842658e9a8a03c1210d563be1b7d09f5",
                    "url": "http:\/\/mirror.technicpack.net\/Technic\/mods\/basemods\/basemods-tekkitmain-v1.0.2.zip",
                },
                {
                    "name": "balkonsweaponmod",
                    "version": "v1.11",
                    "md5": "016bb3edb2fa11c7212fd4cf2504e260",
                    "url": "http:\/\/mirror.technicpack.net\/Technic\/mods\/balkonsweaponmod\/balkonsweaponmod-v1.11.zip",
                },
            ],
        }
    )


@api.route("/api/mod")
def mod():
    return jsonify(
        {"error": "No mod requested/Mod does not exist/Mod version does not exist"}
    )


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
