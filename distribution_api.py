"""Read-only public distribution-format endpoints."""

import hashlib

from flask import Blueprint, Response, abort, redirect, request, url_for

from models.common import app_url, public_repo_url
from models.distribution import (
    DistributionBuildNotFound,
    DistributionExport,
    DistributionExportError,
    FileDirectorExport,
    PackwizExport,
)
from models.distribution_settings import DistributionSettings


distribution_api = Blueprint("distribution_api", __name__)


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


def _load_build(pack_slug: str, selector: str):
    try:
        return DistributionExport.load_build(pack_slug, selector)
    except DistributionBuildNotFound:
        abort(404)
    except DistributionExportError as error:
        return Response(str(error), status=422, mimetype="text/plain")


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


@distribution_api.get("/packwiz/<pack_slug>/<selector>/pack.toml")
def packwiz_pack(pack_slug, selector):
    _require_enabled(DistributionSettings.PACKWIZ)
    build = _load_build(pack_slug, selector)
    if isinstance(build, Response):
        return build
    channel_redirect = _exact_redirect(
        build, "distribution_api.packwiz_pack"
    )
    if channel_redirect:
        return channel_redirect
    try:
        packages = DistributionExport.load_packages(build.id)
        index, excluded = PackwizExport.index_toml(packages, public_repo_url)
        content = PackwizExport.pack_toml(build, index)
        return _response(content, "application/toml", excluded)
    except DistributionExportError as error:
        return Response(str(error), status=422, mimetype="text/plain")


@distribution_api.get("/packwiz/<pack_slug>/<selector>/index.toml")
def packwiz_index(pack_slug, selector):
    _require_enabled(DistributionSettings.PACKWIZ)
    build = _load_build(pack_slug, selector)
    if isinstance(build, Response):
        return build
    channel_redirect = _exact_redirect(
        build, "distribution_api.packwiz_index"
    )
    if channel_redirect:
        return channel_redirect
    try:
        packages = DistributionExport.load_packages(build.id)
        content, excluded = PackwizExport.index_toml(
            packages, public_repo_url
        )
        return _response(content, "application/toml", excluded)
    except DistributionExportError as error:
        return Response(str(error), status=422, mimetype="text/plain")


@distribution_api.get(
    "/packwiz/<pack_slug>/<selector>/mods/<mod_slug>.pw.toml"
)
def packwiz_mod(pack_slug, selector, mod_slug):
    _require_enabled(DistributionSettings.PACKWIZ)
    build = _load_build(pack_slug, selector)
    if isinstance(build, Response):
        return build
    channel_redirect = _exact_redirect(
        build, "distribution_api.packwiz_mod", mod_slug=mod_slug
    )
    if channel_redirect:
        return channel_redirect
    try:
        package = DistributionExport.load_package(build.id, mod_slug)
        content = PackwizExport.mod_toml(package, public_repo_url)
        return _response(content, "application/toml")
    except DistributionBuildNotFound:
        abort(404)
    except DistributionExportError as error:
        return Response(str(error), status=422, mimetype="text/plain")


@distribution_api.get(
    "/filedirector/<pack_slug>/<selector>/<bundle_name>.bundle.json"
)
def filedirector_bundle(pack_slug, selector, bundle_name):
    _require_enabled(DistributionSettings.FILEDIRECTOR)
    if bundle_name not in {"mods", "required", "optional"}:
        abort(404)
    build = _load_build(pack_slug, selector)
    if isinstance(build, Response):
        return build
    try:
        optional = {"mods": None, "required": False, "optional": True}[
            bundle_name
        ]
        packages = DistributionExport.load_packages(build.id, optional=optional)
        content = FileDirectorExport.bundle(packages, public_repo_url)
        return _response(content, "application/json")
    except DistributionExportError as error:
        return Response(str(error), status=422, mimetype="text/plain")


@distribution_api.get(
    "/filedirector/<pack_slug>/<selector>/<bundle_name>.remote.json"
)
def filedirector_remote(pack_slug, selector, bundle_name):
    _require_enabled(DistributionSettings.FILEDIRECTOR)
    if bundle_name not in {"mods", "required", "optional"}:
        abort(404)
    build = _load_build(pack_slug, selector)
    if isinstance(build, Response):
        return build
    try:
        base_url = DistributionExport.application_base(app_url)
    except DistributionExportError as error:
        return Response(str(error), status=422, mimetype="text/plain")
    bundle_url = base_url + url_for(
        "distribution_api.filedirector_bundle",
        pack_slug=build.pack_slug,
        selector=selector,
        bundle_name=bundle_name,
    )
    return _response(FileDirectorExport.remote(bundle_url), "application/json")


@distribution_api.get("/filedirector/<pack_slug>/<selector>/version.txt")
def filedirector_version(pack_slug, selector):
    _require_enabled(DistributionSettings.FILEDIRECTOR)
    build = _load_build(pack_slug, selector)
    if isinstance(build, Response):
        return build
    return _response((build.version + "\n").encode("utf-8"), "text/plain")
