"""Opt-in authenticated write API compatible with current Technic Solder."""

from datetime import date, datetime
import re
from urllib.parse import urlparse

import boto3
from flask import Blueprint, current_app, g, jsonify, request
from mysql.connector import IntegrityError

from api import clear_api_caches
from models.api_token import ApiToken
from models.build import (
    Build,
    InvalidJavaRuntimeError,
    normalize_java_runtime,
)
from models.cache_revision import CacheRevision
from models.common import (
    R2_ACCESS_KEY,
    R2_BUCKET,
    R2_ENDPOINT,
    R2_REGION,
    R2_SECRET_KEY,
    UPLOAD_FOLDER,
    md5_repo_url,
)
from models.compatibility import (
    compatibility_values,
    InvalidModloaderError,
    minecraft_version_storage,
    normalize_minecraft_versions,
    normalize_modloader,
    normalize_modloaders,
)
from models.integration import (
    IntegrationError,
    MAVEN,
    MODRINTH,
    ModIntegration,
    external_id,
    provider_for_user,
)
from models.maven import (
    DEFAULT_VERSION_PATTERN,
    MAVEN_VERSION_MODES,
    MavenArtifact,
    MavenCatalog,
    MavenError,
    MavenRepository,
    MavenVersion,
)
from models.mcinstance import MCInstanceExportError, MCInstanceJar
from models.mod import Mod, normalize_modtype
from models.mod_dependency import ModDependency
from models.modversion import Modversion
from models.write_api import WriteApiProblem, WriteApiStore


write_api_blueprint = Blueprint("write_api", __name__)
_MISSING = object()
_SLUG = re.compile(r"^[A-Za-z0-9_-]+$")
_MD5 = re.compile(r"^[0-9a-fA-F]{32}$")
_SIDES = {"CLIENT", "SERVER", "BOTH"}
_MOD_TYPES = {"MOD", "LAUNCHER", "RES", "CONFIG", "BOOTSTRAP", "MCIL", "NONE"}

R2 = boto3.client(
    "s3",
    region_name=R2_REGION,
    endpoint_url=R2_ENDPOINT,
    aws_access_key_id=R2_ACCESS_KEY,
    aws_secret_access_key=R2_SECRET_KEY,
)


class ApiRequestProblem(ValueError):
    def __init__(self, error, status=422):
        self.error = error
        self.status = status
        super().__init__(str(error))


def _validation(field, message):
    raise ApiRequestProblem({field: [message]})


def _payload():
    payload = request.get_json(silent=True)
    if payload is None and request.form:
        payload = request.form.to_dict(flat=True)
    if not isinstance(payload, dict):
        raise ApiRequestProblem("A JSON object is required.", 400)
    return payload


def _string(data, field, *, required=False, nullable=False, maximum=255):
    if field not in data:
        if required:
            _validation(field, f"The {field} field is required.")
        return _MISSING
    value = data[field]
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        _validation(field, f"The {field} field must be a string.")
    value = value.strip()
    if required and not value:
        _validation(field, f"The {field} field is required.")
    if len(value) > maximum:
        _validation(field, f"The {field} field may not exceed {maximum} characters.")
    return value


def _boolean(data, field, default=_MISSING):
    if field not in data:
        return default
    value = data[field]
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.casefold()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    _validation(field, f"The {field} field must be true or false.")


def _integer(data, field, default=_MISSING, minimum=None, nullable=False):
    if field not in data:
        return default
    value = data[field]
    if value is None and nullable:
        return None
    if isinstance(value, (bool, float)):
        _validation(field, f"The {field} field must be an integer.")
    if isinstance(value, str) and not re.fullmatch(r"[+-]?\d+", value.strip()):
        _validation(field, f"The {field} field must be an integer.")
    try:
        value = int(value)
    except (TypeError, ValueError):
        _validation(field, f"The {field} field must be an integer.")
    if minimum is not None and value < minimum:
        _validation(field, f"The {field} field must be at least {minimum}.")
    return value


def _array(data, field, default=_MISSING):
    if field not in data:
        return default
    value = data[field]
    if not isinstance(value, list):
        _validation(field, f"The {field} field must be an array.")
    return value


def _url(data, field, default=_MISSING):
    value = _string(data, field, nullable=True)
    if value is _MISSING or value is None or value == "":
        return default if value is _MISSING else None
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        _validation(field, f"The {field} field must be an HTTP or HTTPS URL.")
    return value


def _slug(data, field="slug", required=False):
    value = _string(data, field, required=required)
    if value is not _MISSING and not _SLUG.fullmatch(value):
        _validation(field, f"The {field} field may contain only letters, numbers, dashes, and underscores.")
    return value


def _md5(data, field, *, required=False, nullable=False):
    value = _string(
        data, field, required=required, nullable=nullable, maximum=32
    )
    if value is _MISSING or value is None:
        return value
    if value == "0" and nullable:
        return None
    if not _MD5.fullmatch(value):
        _validation(field, f"The {field} field must be a 32-character MD5.")
    return value.lower()


def _loader(data, field="modloader", default=_MISSING, *, multiple=False):
    if field not in data:
        return default
    try:
        normalizer = normalize_modloaders if multiple else normalize_modloader
        return normalizer(data[field])
    except InvalidModloaderError as error:
        _validation(field, str(error))


def _java_runtime(data, field="java_runtime", default=_MISSING):
    if field not in data:
        return default
    value = _string(data, field, nullable=True)
    try:
        return normalize_java_runtime(value)
    except InvalidJavaRuntimeError as error:
        _validation(field, str(error))


def _enum(data, field, choices, default=_MISSING):
    if field not in data:
        return default
    value = _string(data, field, required=True).upper()
    if value not in choices:
        _validation(field, f"Unsupported {field} value.")
    return value


def _include(values, name, value):
    if value is not _MISSING:
        values[name] = value


def _serialized(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _serialized(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialized(item) for item in value]
    return value


def _response(payload, status=200):
    return jsonify(_serialized(payload)), status


def _permission(name):
    if not g.write_principal.allows(name):
        raise ApiRequestProblem("Permission denied.", 403)


def _permission_any(*names):
    if not any(g.write_principal.allows(name) for name in names):
        raise ApiRequestProblem("Permission denied.", 403)


def _modpack(slug, permission=None):
    modpack = WriteApiStore.get_modpack(slug)
    if modpack is None:
        raise ApiRequestProblem("Modpack not found.", 404)
    if permission:
        _permission(permission)
        if not g.write_principal.can_access_modpack(modpack["id"]):
            raise ApiRequestProblem("Permission denied.", 403)
    return modpack


def _build(modpack, version):
    build = WriteApiStore.get_build(modpack["id"], version)
    if build is None:
        raise ApiRequestProblem("Build not found.", 404)
    return build


def _mod(slug):
    mod = WriteApiStore.get_mod(slug)
    if mod is None:
        raise ApiRequestProblem("Mod not found.", 404)
    return mod


def _modversion(mod, version):
    modversion = WriteApiStore.get_modversion(mod["id"], version)
    if modversion is None:
        raise ApiRequestProblem("Mod version not found.", 404)
    return modversion


def _modpack_json(row):
    fields = (
        "id", "name", "slug", "recommended", "latest", "order", "hidden",
        "private", "pinned", "enable_optionals", "enable_server", "created_at",
        "updated_at",
    )
    result = {field: row.get(field) for field in fields}
    for field in ("hidden", "private", "pinned", "enable_optionals", "enable_server"):
        result[field] = bool(result[field])
    return result


def _build_json(row):
    fields = (
        "id", "modpack_id", "version", "minecraft", "forge", "modloader",
        "is_published", "private", "min_java", "java_runtime", "min_memory",
        "created_at", "updated_at",
    )
    result = {field: row.get(field) for field in fields}
    result["is_published"] = bool(result["is_published"])
    result["private"] = bool(result["private"])
    return result


def _mod_json(row):
    fields = (
        "id", "name", "pretty_name", "author", "description", "link", "notes",
        "side", "modtype", "integration_provider", "integration_project_id",
        "created_at", "updated_at",
    )
    result = {field: row.get(field) for field in fields}
    result["type"] = result["modtype"]
    result["replace_on_launch_and_update"] = bool(
        row.get("replace_on_launch_and_update", True)
    )
    result["dependencies"] = ModDependency.get_by_mod_api(row["id"])
    return result


def _modversion_json(row):
    fields = (
        "id", "mod_id", "version", "mcversion", "modloader", "md5", "jarmd5",
        "jarfilesize", "jar_url_override", "filesize",
        "integration_version_id", "created_at", "updated_at",
    )
    result = {field: row.get(field) for field in fields}
    result["minecraft_versions"] = list(
        row.get("minecraft_versions")
        or compatibility_values(result["mcversion"])
    )
    result["modloaders"] = list(
        compatibility_values(result["modloader"], modloaders=True)
    )
    return result


def _client_json(row):
    fields = ("id", "name", "uuid", "created_at", "updated_at")
    return {field: row.get(field) for field in fields}


@write_api_blueprint.before_request
def authenticate_write_request():
    principal = ApiToken.authenticate(request.headers.get("Authorization"))
    if principal is None:
        return jsonify({"error": "Unauthenticated."}), 401
    g.write_principal = principal


@write_api_blueprint.errorhandler(ApiRequestProblem)
def handle_request_problem(error):
    return jsonify({"error": error.error}), error.status


@write_api_blueprint.errorhandler(WriteApiProblem)
def handle_write_problem(error):
    return jsonify({"error": str(error)}), error.status


@write_api_blueprint.errorhandler(IntegrationError)
@write_api_blueprint.errorhandler(MCInstanceExportError)
@write_api_blueprint.errorhandler(MavenError)
def handle_integration_problem(error):
    return jsonify({"error": str(error)}), 422


def _written(payload, status=200):
    if not current_app.testing:
        CacheRevision.bump()
    clear_api_caches()
    return _response(payload, status)


def _modpack_values(data, *, partial=False):
    values = {}
    _include(values, "name", _string(data, "name", required=not partial))
    _include(values, "slug", _slug(data, required=not partial))
    for field, default in (
        ("hidden", _MISSING if partial else True),
        ("private", _MISSING if partial else False),
        ("pinned", _MISSING if partial else False),
        ("enable_optionals", _MISSING if partial else False),
        ("enable_server", _MISSING if partial else False),
    ):
        _include(values, field, _boolean(data, field, default))
    _include(values, "order", _integer(data, "order", _MISSING if partial else 0))
    for field in ("recommended", "latest"):
        _include(values, field, _string(data, field, nullable=True))
    return values


@write_api_blueprint.post("/api/modpack")
def create_modpack():
    _permission("modpacks_create")
    row = WriteApiStore.create_modpack(
        _modpack_values(_payload()), g.write_principal.user_id
    )
    return _written(_modpack_json(row), 201)


@write_api_blueprint.post("/api/modpack/<slug>/clone")
def clone_modpack(slug):
    source = _modpack(slug, "modpacks_manage")
    _permission("modpacks_create")
    data = _payload()
    values = _modpack_values(data, partial=True)
    name = _string(data, "name", required=True)
    new_slug = _slug(data, required=True)
    values.update(name=name, slug=new_slug)
    row = WriteApiStore.clone_modpack(
        source, values, g.write_principal.user_id
    )
    return _written(_modpack_json(row), 201)


@write_api_blueprint.put("/api/modpack/<slug>")
def update_modpack(slug):
    row = _modpack(slug, "modpacks_manage")
    updated = WriteApiStore.update_modpack(
        row, _modpack_values(_payload(), partial=True)
    )
    return _written(_modpack_json(updated))


@write_api_blueprint.delete("/api/modpack/<slug>")
def delete_modpack(slug):
    row = _modpack(slug, "modpacks_delete")
    WriteApiStore.delete_modpack(row["id"])
    return _written({"success": "Modpack deleted."})


def _build_values(data, *, partial=False):
    values = {}
    _include(values, "version", _string(data, "version", required=not partial))
    _include(values, "minecraft", _string(data, "minecraft", required=not partial))
    forge = _string(data, "forge", nullable=True)
    loader = _loader(data)
    if not partial and loader is _MISSING:
        loader = "FORGE" if forge not in {_MISSING, None, ""} else None
    _include(values, "forge", forge)
    _include(values, "modloader", loader)
    for field, default in (
        ("is_published", _MISSING if partial else False),
        ("private", _MISSING if partial else False),
    ):
        _include(values, field, _boolean(data, field, default))
    _include(values, "min_java", _string(data, "min_java", nullable=True))
    _include(values, "java_runtime", _java_runtime(data))
    _include(values, "min_memory", _integer(data, "min_memory", minimum=0))
    return values


@write_api_blueprint.post("/api/modpack/<slug>/build")
def create_build(slug):
    modpack = _modpack(slug, "modpacks_manage")
    data = _payload()
    clone_source = None
    clone_version = _string(data, "clone_from")
    if clone_version is not _MISSING:
        source_slug = _string(data, "clone_from_modpack")
        source_pack = modpack
        if source_slug is not _MISSING:
            source_pack = _modpack(source_slug, "modpacks_manage")
        clone_source = WriteApiStore.get_build(source_pack["id"], clone_version)
        if clone_source is None:
            raise ApiRequestProblem("Clone source build not found.", 404)
    row = WriteApiStore.create_build(
        modpack["id"], _build_values(data), clone_source
    )
    return _written(_build_json(row), 201)


@write_api_blueprint.put("/api/modpack/<slug>/<version>")
def update_build(slug, version):
    modpack = _modpack(slug, "modpacks_manage")
    build = _build(modpack, version)
    row = WriteApiStore.update_build(
        build, _build_values(_payload(), partial=True)
    )
    return _written(_build_json(row))


@write_api_blueprint.delete("/api/modpack/<slug>/<version>")
def delete_build(slug, version):
    modpack = _modpack(slug, "modpacks_manage")
    build = _build(modpack, version)
    WriteApiStore.delete_build(build["id"])
    return _written({"success": "Build deleted."})


def _materialize_version(mod, build, version_id):
    _permission("mods_manage")
    mod_object = Mod.get_by_id(mod["id"])
    build_object = Build.get_by_id(build["id"])
    if mod_object is None or build_object is None:
        raise ApiRequestProblem("The selected mod or build was not found.", 404)
    result = ModIntegration.materialize(
        mod_object,
        build_object,
        external_id(version_id),
        g.write_principal.user_id,
        UPLOAD_FOLDER,
        r2_client=R2 if R2_BUCKET else None,
        r2_bucket=R2_BUCKET,
    )
    row = WriteApiStore.get_modversion(mod["id"], result.version.version)
    if row is None:
        raise ApiRequestProblem("Imported mod version was not found.", 500)
    return row


def _selected_build_modversion(data, mod, build):
    integration_version = _string(data, "integration_version_id")
    if integration_version is not _MISSING:
        if not mod.get("integration_provider"):
            _validation(
                "integration_version_id",
                "The selected mod is not managed by an integration.",
            )
        return _materialize_version(mod, build, integration_version)

    version = _string(data, "mod_version", required=True)
    return _modversion(mod, version)


@write_api_blueprint.post("/api/modpack/<slug>/<version>/mod")
def add_build_mod(slug, version):
    modpack = _modpack(slug, "modpacks_manage")
    build = _build(modpack, version)
    data = _payload()
    mod = _mod(_string(data, "mod_slug", required=True))
    modversion = _selected_build_modversion(data, mod, build)
    optional = _boolean(data, "optional", False)
    dependencies = WriteApiStore.add_build_mod(
        build, mod, modversion, optional
    )
    response = {"success": "Mod added to build."}
    if dependencies:
        response["dependencies_added"] = dependencies
    return _written(response, 201)


@write_api_blueprint.put("/api/modpack/<slug>/<version>/mod/<mod_slug>")
def update_build_mod(slug, version, mod_slug):
    modpack = _modpack(slug, "modpacks_manage")
    build = _build(modpack, version)
    mod = _mod(mod_slug)
    data = _payload()
    modversion = _selected_build_modversion(data, mod, build)
    optional = _boolean(data, "optional")
    dependencies = WriteApiStore.update_build_mod(
        build,
        mod,
        modversion,
        None if optional is _MISSING else optional,
    )
    response = {"success": "Mod version updated in build."}
    if dependencies:
        response["dependencies_added"] = dependencies
    return _written(response)


@write_api_blueprint.delete("/api/modpack/<slug>/<version>/mod/<mod_slug>")
def remove_build_mod(slug, version, mod_slug):
    modpack = _modpack(slug, "modpacks_manage")
    build = _build(modpack, version)
    mod = _mod(mod_slug)
    WriteApiStore.remove_build_mod(build["id"], mod["id"])
    return _written({"success": "Mod removed from build."})


def _dependencies(data):
    values = _array(data, "dependencies")
    if values is _MISSING:
        return None
    for index, value in enumerate(values):
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            _validation(
                f"dependencies.{index}",
                "Each dependency must be a mod ID or mod slug.",
            )
    return values


def _mod_values(data, *, partial=False):
    values = {}
    _include(values, "name", _slug(data, "name", required=not partial))
    _include(
        values,
        "pretty_name",
        _string(data, "pretty_name", required=not partial),
    )
    for field, maximum in (
        ("description", 255),
        ("author", 255),
        ("notes", 65535),
    ):
        value = _string(data, field, nullable=True, maximum=maximum)
        _include(values, field, value)
    _include(values, "link", _url(data, "link"))
    _include(
        values,
        "side",
        _enum(data, "side", _SIDES, _MISSING if partial else "BOTH"),
    )
    modtype = _enum(
        data, "modtype", _MOD_TYPES, _MISSING if partial else "MOD"
    )
    if modtype is not _MISSING:
        modtype = normalize_modtype(modtype)
    _include(values, "modtype", modtype)
    _include(
        values,
        "replace_on_launch_and_update",
        _boolean(
            data,
            "replace_on_launch_and_update",
            _MISSING if partial else True,
        ),
    )
    return values


@write_api_blueprint.post("/api/mod")
def create_mod():
    _permission("mods_create")
    data = _payload()
    row = WriteApiStore.create_mod(_mod_values(data), _dependencies(data))
    return _written(_mod_json(row), 201)


@write_api_blueprint.put("/api/mod/<slug>")
def update_mod(slug):
    _permission("mods_manage")
    mod = _mod(slug)
    data = _payload()
    row = WriteApiStore.update_mod(
        mod, _mod_values(data, partial=True), _dependencies(data)
    )
    return _written(_mod_json(row))


@write_api_blueprint.delete("/api/mod/<slug>")
def delete_mod(slug):
    _permission("mods_delete")
    mod = _mod(slug)
    WriteApiStore.delete_mod(mod["id"])
    return _written({"success": "Mod deleted."})


def _modversion_values(data, *, partial=False):
    values = {}
    if not partial:
        _include(values, "version", _string(data, "version", required=True))
    _include(values, "md5", _md5(data, "md5", required=not partial))
    _include(values, "jarmd5", _md5(data, "jarmd5", nullable=True))
    _include(
        values,
        "jarfilesize",
        _integer(data, "jarfilesize", minimum=0, nullable=True),
    )
    if "jarmd5" in values and "jarfilesize" not in values:
        # A changed JAR hash invalidates the size recorded for the old JAR.
        values["jarfilesize"] = None
    if (
        not partial
        and values.get("jarfilesize") is not None
        and not values.get("jarmd5")
    ):
        _validation("jarfilesize", "A JAR filesize requires a JAR MD5.")
    if "jar_url_override" in data:
        override = _string(
            data,
            "jar_url_override",
            nullable=True,
            maximum=2048,
        )
        try:
            override = Modversion.normalize_jar_url_override(override)
        except ValueError as error:
            _validation("jar_url_override", str(error))
        _include(values, "jar_url_override", override)
    _include(values, "filesize", _integer(data, "filesize", minimum=0))
    if "mcversion" in data:
        try:
            minecraft = normalize_minecraft_versions(data["mcversion"])
            minecraft_version_storage(minecraft)
        except ValueError as error:
            _validation("mcversion", str(error))
        _include(values, "mcversion", minecraft)
    _include(values, "modloader", _loader(data, multiple=True))
    return values


@write_api_blueprint.post("/api/mod/<slug>/version")
def create_modversion(slug):
    _permission("mods_manage")
    mod = _mod(slug)
    if mod.get("integration_provider"):
        raise ApiRequestProblem(
            "Provider-managed versions must be imported through the integration API."
        )
    values = _modversion_values(_payload())
    if values.get("jar_url_override") and not Modversion.JAR_MD5_PATTERN.fullmatch(
        str(values.get("jarmd5") or "")
    ):
        _validation(
            "jar_url_override",
            "A JAR override requires a verified JAR MD5.",
        )
    row = WriteApiStore.create_modversion(mod["id"], values)
    return _written(_modversion_json(row), 201)


@write_api_blueprint.put("/api/mod/<slug>/<version>")
def update_modversion(slug, version):
    _permission("mods_manage")
    mod = _mod(slug)
    current = _modversion(mod, version)
    values = _modversion_values(_payload(), partial=True)
    effective_override = values.get(
        "jar_url_override", current.get("jar_url_override")
    )
    effective_jar_md5 = values.get("jarmd5", current.get("jarmd5"))
    if effective_override and not Modversion.JAR_MD5_PATTERN.fullmatch(
        str(effective_jar_md5 or "")
    ):
        _validation(
            "jar_url_override",
            "A JAR override requires a verified JAR MD5.",
        )
    row = WriteApiStore.update_modversion(
        current, values
    )
    return _written(_modversion_json(row))


@write_api_blueprint.delete("/api/mod/<slug>/<version>")
def delete_modversion(slug, version):
    _permission("mods_manage")
    mod = _mod(slug)
    current = _modversion(mod, version)
    WriteApiStore.delete_modversion(current["id"])
    return _written({"success": "Mod version deleted."})


def _client_values(data, *, partial=False):
    values = {}
    _include(values, "name", _string(data, "name", required=not partial))
    if not partial:
        _include(values, "uuid", _string(data, "uuid", required=True))
    return values


def _client_modpacks(data):
    modpacks = _array(data, "modpacks")
    if modpacks is _MISSING:
        return None
    normalized = []
    for index, value in enumerate(modpacks):
        if isinstance(value, (bool, float)):
            _validation(f"modpacks.{index}", "The modpack ID must be an integer.")
        try:
            modpack_id = int(value)
        except (TypeError, ValueError):
            _validation(f"modpacks.{index}", "The modpack ID must be an integer.")
        if modpack_id < 1:
            _validation(f"modpacks.{index}", "The modpack ID must be positive.")
        normalized.append(modpack_id)
    if len(normalized) != len(set(normalized)):
        _validation("modpacks", "The modpack list must not contain duplicates.")
    return normalized


@write_api_blueprint.post("/api/client")
def create_client():
    _permission("solder_clients")
    row = WriteApiStore.create_client(_client_values(_payload()))
    return _written(_client_json(row), 201)


@write_api_blueprint.put("/api/client/<uuid>")
def update_client(uuid):
    _permission("solder_clients")
    client = WriteApiStore.get_client(uuid)
    if client is None:
        raise ApiRequestProblem("Client not found.", 404)
    data = _payload()
    row = WriteApiStore.update_client(
        client, _client_values(data, partial=True), _client_modpacks(data)
    )
    return _written(_client_json(row))


@write_api_blueprint.delete("/api/client/<uuid>")
def delete_client(uuid):
    _permission("solder_clients")
    client = WriteApiStore.get_client(uuid)
    if client is None:
        raise ApiRequestProblem("Client not found.", 404)
    WriteApiStore.delete_client(client["id"])
    return _written({"success": "Client deleted."})


@write_api_blueprint.get("/api/token")
def list_tokens():
    return _response(
        {"tokens": ApiToken.get_all(g.write_principal.user_id)}
    )


@write_api_blueprint.post("/api/token")
def create_token():
    name = _string(_payload(), "name", required=True)
    token = ApiToken.create(g.write_principal.user_id, name)
    return _response({"token": token}, 201)


@write_api_blueprint.delete("/api/token/<int:token_id>")
def delete_token(token_id):
    if not ApiToken.delete(token_id, g.write_principal.user_id):
        raise ApiRequestProblem("Token not found.", 404)
    return _response({"success": "Token revoked."})


def _project_json(project, imported=False):
    return {
        "provider": project.provider,
        "project_id": project.project_id,
        "slug": project.slug,
        "title": project.title,
        "description": project.description,
        "author": project.author,
        "link": project.link,
        "icon_url": project.icon_url,
        "license": project.license,
        "side": project.side,
        "available": project.available,
        "imported": imported,
    }


def _maven_repository_json(repository):
    return {
        "id": repository.id,
        "name": repository.name,
        "base_url": repository.base_url,
        "created_at": repository.created_at,
        "updated_at": repository.updated_at,
    }


def _maven_artifact_json(artifact):
    return {
        "id": artifact.id,
        "repository_id": artifact.repository_id,
        "repository_name": artifact.repository_name,
        "group_id": artifact.group_id,
        "artifact_id": artifact.artifact_id,
        "classifier": artifact.classifier,
        "extension": artifact.extension,
        "coordinates": artifact.coordinates,
        "version_mode": artifact.version_mode,
        "version_pattern": artifact.version_pattern,
        "fixed_minecraft": artifact.fixed_minecraft,
        "modloader": artifact.modloader,
        "slug": artifact.slug,
        "title": artifact.title,
        "description": artifact.description,
        "author": artifact.author,
        "link": artifact.project_url,
        "side": artifact.side,
        "mod_id": artifact.mod_id,
        "created_at": artifact.created_at,
        "updated_at": artifact.updated_at,
        "solderpy_loader_direct": bool(
            getattr(artifact, "solderpy_loader_direct", False)
        ),
    }


def _maven_version_json(version):
    return {
        "id": version.id,
        "upstream_version": version.upstream_version,
        "integration_version_id": version.integration_version_id,
        "minecraft": version.minecraft,
        "mod_version": version.mod_version,
        "modloader": version.modloader,
        "mapping_source": version.mapping_source,
        "enabled": version.enabled,
        "available": version.available,
        "metadata_order": version.metadata_order,
    }


def _optional_string(data, field, maximum=255):
    value = _string(data, field, maximum=maximum)
    return "" if value is _MISSING else value


@write_api_blueprint.get("/api/integration/maven/repository")
def list_maven_repositories():
    _permission_any("mods_create", "mods_manage", "solder_env")
    return _response(
        {
            "repositories": [
                _maven_repository_json(item) for item in MavenRepository.get_all()
            ]
        }
    )


@write_api_blueprint.post("/api/integration/maven/repository")
def create_maven_repository():
    _permission("solder_env")
    data = _payload()
    try:
        repository = MavenRepository.new(
            _string(data, "name", required=True),
            _string(data, "base_url", required=True, maximum=2048),
        )
    except IntegrityError as error:
        raise ApiRequestProblem("Maven repository already exists.", 409) from error
    return _written({"repository": _maven_repository_json(repository)}, 201)


@write_api_blueprint.delete(
    "/api/integration/maven/repository/<int:repository_id>"
)
def delete_maven_repository(repository_id):
    _permission("solder_env")
    MavenRepository.delete(repository_id)
    return _written({"success": "Maven repository deleted."})


@write_api_blueprint.get("/api/integration/maven/artifact")
def list_maven_artifacts():
    _permission_any("mods_create", "mods_manage")
    return _response(
        {"artifacts": [_maven_artifact_json(item) for item in MavenArtifact.get_all()]}
    )


@write_api_blueprint.post("/api/integration/maven/artifact")
def create_maven_artifact():
    _permission("mods_create")
    data = _payload()
    confirmed = _boolean(data, "redistribution_confirmed", False)
    if not confirmed:
        _validation(
            "redistribution_confirmed",
            "Confirm that this artifact may be downloaded and rehosted.",
        )
    repository_id = _integer(data, "repository_id", minimum=1)
    if repository_id is _MISSING:
        _validation("repository_id", "The repository_id field is required.")
    artifact = None
    try:
        artifact = MavenArtifact.new(
            repository_id,
            _string(data, "group_id", required=True),
            _string(data, "artifact_id", required=True),
            _optional_string(data, "classifier", 128),
            _optional_string(data, "extension", 16) or "jar",
            _enum(data, "version_mode", set(MAVEN_VERSION_MODES), "MANUAL"),
            _optional_string(data, "version_pattern") or DEFAULT_VERSION_PATTERN,
            _optional_string(data, "fixed_minecraft") or None,
            _loader(data, default=None),
            _optional_string(data, "slug"),
            _string(data, "title", required=True),
            _optional_string(data, "description"),
            _optional_string(data, "author"),
            None if (link := _url(data, "link")) is _MISSING else link,
            _enum(data, "side", _SIDES, "BOTH"),
        )
        if _boolean(data, "solderpy_loader_direct", False):
            artifact = MavenArtifact.update_solderpy_loader_direct(
                artifact.id, True
            )
        versions = MavenCatalog.refresh(artifact)
        mod, _created = ModIntegration.import_project(
            MAVEN, str(artifact.id), g.write_principal.user_id
        )
        MavenArtifact.attach_mod(artifact.id, mod.id)
        artifact = MavenArtifact.get(artifact.id)
    except IntegrityError as error:
        if artifact is not None:
            MavenArtifact.delete_unlinked(artifact.id)
        raise ApiRequestProblem("Maven artifact or mod already exists.", 409) from error
    except Exception:
        if artifact is not None:
            MavenArtifact.delete_unlinked(artifact.id)
        raise
    return _written(
        {
            "artifact": _maven_artifact_json(artifact),
            "versions": [_maven_version_json(item) for item in versions],
        },
        201,
    )


def _maven_artifact(artifact_id):
    artifact = MavenArtifact.get(artifact_id)
    if artifact is None:
        raise ApiRequestProblem("Maven artifact not found.", 404)
    return artifact


@write_api_blueprint.get("/api/integration/maven/artifact/<int:artifact_id>")
def get_maven_artifact(artifact_id):
    _permission("mods_manage")
    artifact = _maven_artifact(artifact_id)
    return _response(
        {
            "artifact": _maven_artifact_json(artifact),
            "versions": [
                _maven_version_json(item) for item in MavenVersion.get_all(artifact_id)
            ],
        }
    )


@write_api_blueprint.put("/api/integration/maven/artifact/<int:artifact_id>")
def update_maven_artifact(artifact_id):
    _permission("mods_manage")
    artifact = _maven_artifact(artifact_id)
    data = _payload()
    if any(
        field in data
        for field in (
            "version_mode",
            "version_pattern",
            "fixed_minecraft",
            "modloader",
        )
    ):
        version_mode = _enum(
            data,
            "version_mode",
            set(MAVEN_VERSION_MODES),
            artifact.version_mode,
        )
        version_pattern = _string(data, "version_pattern")
        fixed_minecraft = _string(data, "fixed_minecraft", nullable=True)
        artifact = MavenArtifact.update_rule(
            artifact.id,
            version_mode,
            artifact.version_pattern
            if version_pattern is _MISSING
            else (version_pattern or DEFAULT_VERSION_PATTERN),
            artifact.fixed_minecraft
            if fixed_minecraft is _MISSING
            else (fixed_minecraft or None),
            _loader(data, default=artifact.modloader),
        )
    current_direct_downloads = bool(
        getattr(artifact, "solderpy_loader_direct", False)
    )
    direct_downloads = _boolean(
        data, "solderpy_loader_direct", current_direct_downloads
    )
    if direct_downloads != current_direct_downloads:
        artifact = MavenArtifact.update_solderpy_loader_direct(
            artifact.id, direct_downloads
        )
    return _written({"artifact": _maven_artifact_json(artifact)})


@write_api_blueprint.post(
    "/api/integration/maven/artifact/<int:artifact_id>/refresh"
)
def refresh_maven_artifact(artifact_id):
    _permission("mods_manage")
    artifact = _maven_artifact(artifact_id)
    versions = MavenCatalog.refresh(artifact)
    return _written(
        {"versions": [_maven_version_json(item) for item in versions]}
    )


@write_api_blueprint.put(
    "/api/integration/maven/artifact/<int:artifact_id>/version/<int:mapping_id>"
)
def update_maven_version(artifact_id, mapping_id):
    _permission("mods_manage")
    _maven_artifact(artifact_id)
    data = _payload()
    enabled = _boolean(data, "enabled", False)
    MavenVersion.update_manual(
        mapping_id,
        artifact_id,
        _optional_string(data, "minecraft") or None,
        _optional_string(data, "mod_version") or None,
        _loader(data, default=None),
        enabled,
    )
    version = MavenVersion.get_by_id(mapping_id, artifact_id)
    return _written({"version": _maven_version_json(version)})


@write_api_blueprint.get("/api/integration/modrinth/search")
def search_modrinth():
    _permission("mods_create")
    query = str(request.args.get("q") or "").strip()
    if not query:
        _validation("q", "The q field is required.")
    if len(query) > 100:
        _validation("q", "The q field may not exceed 100 characters.")
    projects = provider_for_user(MODRINTH, g.write_principal.user_id).search(query)
    imported_ids = Mod.get_integration_project_ids(MODRINTH)
    return _response(
        {
            "projects": [
                _project_json(project, project.project_id in imported_ids)
                for project in projects
            ]
        }
    )


@write_api_blueprint.post("/api/integration/modrinth/mod")
def import_modrinth_mod():
    _permission("mods_create")
    project_id = _string(_payload(), "project_id", required=True, maximum=64)
    mod, created = ModIntegration.import_project(
        MODRINTH, external_id(project_id), g.write_principal.user_id
    )
    row = WriteApiStore.get_mod(mod.name)
    return _written(_mod_json(row), 201 if created else 200)


@write_api_blueprint.get(
    "/api/modpack/<slug>/<version>/mod/<mod_slug>/integration-versions"
)
def list_integration_versions(slug, version, mod_slug):
    modpack = _modpack(slug, "modpacks_manage")
    _permission("mods_manage")
    build = _build(modpack, version)
    mod = _mod(mod_slug)
    if not mod.get("integration_provider"):
        raise ApiRequestProblem("The selected mod is not managed by an integration.")
    mod_object = Mod.get_by_id(mod["id"])
    build_object = Build.get_by_id(build["id"])
    versions = ModIntegration.list_versions(
        mod_object, build_object, g.write_principal.user_id
    )
    imported_ids = Modversion.get_integration_version_ids(mod["id"])
    return _response(
        {
            "versions": [
                item.management_json()
                for item in versions
                if item.version_id not in imported_ids
            ]
        }
    )


@write_api_blueprint.post("/api/mod/<slug>/<version>/mcil-jar")
def create_mcil_jar(slug, version):
    _permission("mods_manage")
    mod = _mod(slug)
    current = _modversion(mod, version)
    mod_object = Mod.get_by_id(mod["id"])
    version_object = Modversion.get_by_id(current["id"])
    jarmd5 = MCInstanceJar.create(
        mod_object,
        version_object,
        md5_repo_url,
        UPLOAD_FOLDER,
        r2_client=R2 if R2_BUCKET else None,
        r2_bucket=R2_BUCKET,
    )
    return _written({"success": "MCIL JAR created.", "jarmd5": jarmd5})


@write_api_blueprint.post("/api/minecraft/refresh")
def refresh_minecraft_versions():
    # solder.py uses free-form Minecraft version fields and has no remote
    # version-list cache. Keep the Technic endpoint as a compatible no-op.
    if not current_app.testing:
        CacheRevision.bump()
    clear_api_caches()
    return _response({"success": "Minecraft versions cache refreshed."})
