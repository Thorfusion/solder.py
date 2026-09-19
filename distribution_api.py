"""Read-only public distribution-format endpoints."""

import hashlib

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    redirect,
    request,
    url_for,
)

from models.common import app_url, public_repo_url
from models.advanced_optional import AdvancedOptional
from models.distribution import (
    DistributionBuildNotFound,
    DistributionExport,
    DistributionExportError,
    FileDirectorExport,
    PackwizExport,
)
from models.distribution_settings import DistributionSettings
from models.platform_export import PlatformExportError, PlatformPackExport


distribution_api = Blueprint("distribution_api", __name__)
PUBLIC_EXPORT_ERROR = "Unable to generate the requested distribution file."


def _require_enabled(setting: str) -> None:
    if not DistributionSettings.is_enabled(setting):
        abort(404)


def _response(content: bytes, mimetype: str, excluded: int = 0) -> Response:
    response = Response(content, mimetype=mimetype)
    response.set_etag(hashlib.sha256(content).hexdigest())
    response.headers["Cache-Control"] = "public, no-cache"
    if excluded:
        response.headers["X-Solder-Excluded-Packages"] = str(excluded)
    return response.make_conditional(request)


def _export_error_response() -> Response:
    current_app.logger.warning("Distribution export failed.", exc_info=True)
    return Response(PUBLIC_EXPORT_ERROR, status=422, mimetype="text/plain")


def _load_build(pack_slug: str, selector: str):
    try:
        return DistributionExport.load_build(pack_slug, selector)
    except DistributionBuildNotFound:
        abort(404)
    except DistributionExportError:
        return _export_error_response()


def _source_mode(value):
    try:
        return PlatformPackExport.source_mode(value)
    except PlatformExportError:
        abort(404)


def _exact_redirect(build, endpoint: str, **values):
    if not build.is_channel:
        return None
    response = redirect(
        url_for(
            endpoint,
            pack_slug=build.pack_slug,
            selector=build.version,
            **values,
        )
    )
    response.headers["Cache-Control"] = "public, no-cache"
    return response


@distribution_api.get(
    "/packwiz/<pack_slug>/<selector>/pack.toml",
    defaults={"source": "solder"},
)
@distribution_api.get("/packwiz/<pack_slug>/<selector>/<source>/pack.toml")
def packwiz_pack(pack_slug, selector, source):
    _require_enabled(DistributionSettings.PACKWIZ)
    source = _source_mode(source)
    build = _load_build(pack_slug, selector)
    if isinstance(build, Response):
        return build
    try:
        build = PlatformPackExport.override_modloader_version(
            build, request.args.get("forge_version")
        )
    except PlatformExportError:
        return _export_error_response()
    redirect_values = {"source": source}
    if "forge_version" in request.args:
        redirect_values["forge_version"] = request.args.get("forge_version")
    channel_redirect = _exact_redirect(
        build, "distribution_api.packwiz_pack", **redirect_values
    )
    if channel_redirect:
        return channel_redirect
    try:
        packages = DistributionExport.load_packages(build.id)
        native_files = (
            PlatformPackExport.native_modrinth_files(build, packages)
            if source == "hybrid"
            else {}
        )
        index, excluded = PackwizExport.index_toml(
            packages, public_repo_url, native_files=native_files
        )
        content = PackwizExport.pack_toml(build, index)
        return _response(content, "application/toml", excluded)
    except (DistributionExportError, PlatformExportError):
        return _export_error_response()


@distribution_api.get(
    "/packwiz/<pack_slug>/<selector>/index.toml",
    defaults={"source": "solder"},
)
@distribution_api.get("/packwiz/<pack_slug>/<selector>/<source>/index.toml")
def packwiz_index(pack_slug, selector, source):
    _require_enabled(DistributionSettings.PACKWIZ)
    source = _source_mode(source)
    build = _load_build(pack_slug, selector)
    if isinstance(build, Response):
        return build
    channel_redirect = _exact_redirect(
        build, "distribution_api.packwiz_index", source=source
    )
    if channel_redirect:
        return channel_redirect
    try:
        packages = DistributionExport.load_packages(build.id)
        native_files = (
            PlatformPackExport.native_modrinth_files(build, packages)
            if source == "hybrid"
            else {}
        )
        content, excluded = PackwizExport.index_toml(
            packages, public_repo_url, native_files=native_files
        )
        return _response(content, "application/toml", excluded)
    except (DistributionExportError, PlatformExportError):
        return _export_error_response()


@distribution_api.get(
    "/packwiz/<pack_slug>/<selector>/mods/<mod_slug>.pw.toml",
    defaults={"source": "solder"},
)
@distribution_api.get(
    "/packwiz/<pack_slug>/<selector>/<source>/mods/<mod_slug>.pw.toml"
)
def packwiz_mod(pack_slug, selector, mod_slug, source):
    _require_enabled(DistributionSettings.PACKWIZ)
    source = _source_mode(source)
    build = _load_build(pack_slug, selector)
    if isinstance(build, Response):
        return build
    channel_redirect = _exact_redirect(
        build,
        "distribution_api.packwiz_mod",
        mod_slug=mod_slug,
        source=source,
    )
    if channel_redirect:
        return channel_redirect
    try:
        package = DistributionExport.load_package(build.id, mod_slug)
        native_files = (
            PlatformPackExport.native_modrinth_files(build, [package])
            if source == "hybrid"
            else {}
        )
        content = PackwizExport.mod_toml(
            package,
            public_repo_url,
            native_files.get(package.integration_version_id),
        )
        return _response(content, "application/toml")
    except DistributionBuildNotFound:
        abort(404)
    except (DistributionExportError, PlatformExportError):
        return _export_error_response()


@distribution_api.get(
    "/filedirector/<pack_slug>/<selector>/<bundle_name>.bundle.json"
)
def filedirector_bundle(pack_slug, selector, bundle_name):
    return _director_bundle(
        pack_slug,
        selector,
        bundle_name,
        DistributionSettings.FILEDIRECTOR,
    )


@distribution_api.get(
    "/modpackdirector/<pack_slug>/<selector>/<bundle_name>.bundle.json"
)
def modpack_director_bundle(pack_slug, selector, bundle_name):
    return _director_bundle(
        pack_slug,
        selector,
        bundle_name,
        DistributionSettings.MODPACK_DIRECTOR,
    )


def _director_bundle(pack_slug, selector, bundle_name, setting):
    """Render the shared bundle schema accepted by both Director projects."""
    _require_enabled(setting)
    if bundle_name not in {
        "mods",
        "required",
        "optional",
        "modrinth-fallback",
    }:
        abort(404)
    build = _load_build(pack_slug, selector)
    if isinstance(build, Response):
        return build
    try:
        source = _source_mode(request.args.get("source"))
        optional = {
            "mods": None,
            "required": False,
            "optional": True,
            "modrinth-fallback": None,
        }[bundle_name]
        if bundle_name in {"mods", "modrinth-fallback"}:
            packages = DistributionExport.load_packages(
                build.id, optional=optional, include_excluded=True
            )
        else:
            packages = DistributionExport.load_packages(
                build.id, optional=optional
            )
        if bundle_name == "modrinth-fallback":
            packages = [
                package
                for package in packages
                if not package.is_native_modrinth
                or package.optional_state == 2
            ]
        optional_groups = AdvancedOptional.get_active_groups_for_packages(
            build.id, packages
        )
        native_files = (
            PlatformPackExport.native_modrinth_files(build, packages)
            if source == "hybrid"
            else {}
        )
        content = FileDirectorExport.bundle(
            packages,
            public_repo_url,
            native_files=native_files,
            optional_groups=optional_groups,
        )
        return _response(content, "application/json")
    except (DistributionExportError, PlatformExportError):
        return _export_error_response()


@distribution_api.get(
    "/filedirector/<pack_slug>/<selector>/<bundle_name>.remote.json"
)
def filedirector_remote(pack_slug, selector, bundle_name):
    return _director_remote(
        pack_slug,
        selector,
        bundle_name,
        DistributionSettings.FILEDIRECTOR,
        "distribution_api.filedirector_bundle",
    )


@distribution_api.get(
    "/modpackdirector/<pack_slug>/<selector>/<bundle_name>.remote.json"
)
def modpack_director_remote(pack_slug, selector, bundle_name):
    return _director_remote(
        pack_slug,
        selector,
        bundle_name,
        DistributionSettings.MODPACK_DIRECTOR,
        "distribution_api.modpack_director_bundle",
    )


def _director_remote(pack_slug, selector, bundle_name, setting, bundle_endpoint):
    _require_enabled(setting)
    if bundle_name not in {
        "mods",
        "required",
        "optional",
        "modrinth-fallback",
    }:
        abort(404)
    build = _load_build(pack_slug, selector)
    if isinstance(build, Response):
        return build
    try:
        source = _source_mode(request.args.get("source"))
        base_url = DistributionExport.application_base(app_url)
    except DistributionExportError:
        return _export_error_response()
    route_values = {
        "pack_slug": build.pack_slug,
        "selector": selector,
        "bundle_name": bundle_name,
    }
    if source == "hybrid":
        route_values["source"] = source
    bundle_url = base_url + url_for(
        bundle_endpoint,
        **route_values,
    )
    return _response(FileDirectorExport.remote(bundle_url), "application/json")


@distribution_api.get("/filedirector/<pack_slug>/<selector>/version.txt")
def filedirector_version(pack_slug, selector):
    return _director_version(
        pack_slug, selector, DistributionSettings.FILEDIRECTOR
    )


@distribution_api.get("/modpackdirector/<pack_slug>/<selector>/version.txt")
def modpack_director_version(pack_slug, selector):
    return _director_version(
        pack_slug, selector, DistributionSettings.MODPACK_DIRECTOR
    )


def _director_version(pack_slug, selector, setting):
    _require_enabled(setting)
    build = _load_build(pack_slug, selector)
    if isinstance(build, Response):
        return build
    return _response((build.version + "\n").encode("utf-8"), "text/plain")
