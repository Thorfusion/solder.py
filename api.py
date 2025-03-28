from flask import Blueprint, jsonify, request
from cachetools import cached, TTLCache
from models.key import Key
from models.mod import Mod
from models.modpack import Modpack
from models.common import solderpy_version, public_repo_url
from models.common import cache_size, cache_ttl

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
@cached(TTLCache(cache_size, cache_ttl), key=lambda: str(request.args.get("cid")) + str(request.args.get('include')) + str(request.args.get('k')))
def modpack():
    cid = request.args.get("cid")
    keys = request.args.get("k")
    key = Key.get_key(keys)
    if key:
        modpacks = Modpack.get_all_api()
    else:
        modpacks = Modpack.get_by_cid_api(cid)
    if request.args.get('include') == "full":
        if key:
            return jsonify({"modpacks": {modpack.slug: Modpack.to_modpack_json_all(modpack.slug) for modpack in modpacks}, "mirror_url": public_repo_url})
        else:
            return jsonify({"modpacks": {modpack.slug: Modpack.to_modpack_json(cid, modpack.slug) for modpack in modpacks}, "mirror_url": public_repo_url})
    else:
        return jsonify({"modpacks": {modpack.slug: modpack.name for modpack in modpacks}, "mirror_url": public_repo_url})

@api.route("/api/modpack/<slug>")
@cached(TTLCache(cache_size, cache_ttl), key=lambda slug: str(request.args.get("cid")) + str(request.args.get('k')) + slug)
def modpack_slug(slug: str):
    cid = request.args.get("cid")
    keys = request.args.get("k")
    key = Key.get_key(keys)
    if key:
        modpack = Modpack.get_all_by_slug_api(slug)
        if modpack:
            modpack.builds = modpack.get_builds_api()
            return jsonify(modpack.to_json())
    else:
        modpack = Modpack.get_by_cid_slug_api(cid, slug)
        if modpack:
            modpack.builds = modpack.get_builds_cid_api(cid)
            return jsonify(modpack.to_json())
    return jsonify({"error": "Modpack does not exist/Build does not exist"}), 404

@api.route("/api/modpack/<slugstring>/<buildstring>")
@cached(TTLCache(cache_size, cache_ttl), key=lambda slugstring, buildstring: str(request.args.get("cid")) + str(request.args.get("include")) + str(request.args.get("k")) + slugstring + buildstring)
def modpack_slug_build(slugstring: str, buildstring: str):
    keys = request.args.get("k")
    key = Key.get_key(keys)
    # Todo, add api key verification that bypass cid verification
    modpack = Modpack.get_by_cid_slug_api(request.args.get("cid"), slugstring)
    if not modpack:
        return jsonify({"error": "Modpack does not exist/Build does not exist"}), 404
    
    buildsplit = buildstring.split("-")
    buildsplit.append("")
    buildnumber = buildsplit[0]
    buildtag = buildsplit[1]
    build = modpack.get_build_api(buildnumber)
    if not build:
        return jsonify({"error": "Modpack does not exist/Build does not exist"}), 404
    modversions = build.get_modversions_api(buildtag)
    moddata = []
    if request.args.get('include') == "mods":
        for mv in modversions:
            moddata.append(
                {
                    "name": mv.modname,
                    "version": mv.version,
                    "md5": mv.md5,
                    "url": f"{public_repo_url}{mv.modname}/{mv.modname}-{mv.version}.zip",
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
                    "name": mv.modname,
                    "version": mv.version,
                    "md5": mv.md5,
                    "url": f"{public_repo_url}{mv.modname}/{mv.modname}-{mv.version}.zip",
                }
            )
    return {"minecraft": build.minecraft, "java": build.min_java, "memory": build.min_memory, "forge": None, "mods": moddata}


@api.route("/api/mod")
def mod():
    return jsonify(
        {"error": "Mod does not exist"}
    ), 404
    Mods = Mod.get_all()
    return jsonify({"mods": {Mods.name: Mods.pretty_name for Mods in Mods}})

@api.route("/api/mod/<name>")
@cached(TTLCache(cache_size, cache_ttl), key=lambda name: name)
def mod_name(name: str):
    mods = Mod.get_by_name_api(name)
    if not mods:
        return jsonify({"error": "Mod does not exist"}), 404
    else:
        versions = Mod.get_versions_api(mods)
        res = mods.to_json()
        res["versions"] = [v["version"] for v in versions]
        return jsonify(res)

@api.route("/api/mod/<name>/<version>")
@cached(TTLCache(cache_size, cache_ttl), key=lambda name, version: name + version)
def mod_name_version(name: str, version: str):
    mod = Mod.get_by_name_api(name)
    if not mod:
        return jsonify({"error": "Mod does not exist"}), 404
    version = mod.get_version_api(version)
    if not version:
        return jsonify({"error": "Mod version does not exist"}), 404
    else:
        res = version.to_json()
        res["url"] = f"{public_repo_url}{name}/{version.version}.zip"
        return jsonify(res)
