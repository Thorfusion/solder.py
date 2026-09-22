import os
from copy import copy
from pathlib import Path
import tempfile
import boto3
import requests
from urllib.parse import urlsplit

from api import solderpy_version
from flask import Blueprint, app, flash, g, jsonify, redirect, render_template, request, send_file, session, url_for
from models.build import (
    Build,
    InvalidJavaRuntimeError,
    MOJANG_JAVA_RUNTIME_OPTIONS,
)
from models.build_export import BuildCsvExport, BuildExportError
from models.build_modversion import Build_modversion
from models.api_token import ApiToken
from models.client import Client
from models.client_modpack import Client_modpack
from models.compatibility import (
    InvalidModloaderError,
    minecraft_version_storage,
)
from models.database import Database
from models.dashboard import Dashboard
from models.advanced_optional import (
    ADVANCED_MODE,
    BASIC_MODE,
    AdvancedOptional,
    AdvancedOptionalError,
)
from models.help_docs import (
    HELP_DOCUMENTS,
    get_help_document,
    render_help_document,
)
from models.distribution_settings import (
    DistributionSettings,
    DistributionSettingsError,
)
from models.distribution import DistributionExportError
from models.key import Key
from models.mcinstance import (
    MCInstanceExport,
    MCInstanceExportError,
    MCInstanceJar,
)
from models.platform_export import PlatformExportError, PlatformPackExport
from models.technic_solderpy_loader import (
    TechnicSolderPyLoader,
    TechnicSolderPyLoaderError,
)
from models.platform_export_override import (
    PlatformExportOverride,
    PlatformExportOverrideError,
)
from models.platform_publishing import (
    CURSEFORGE as PUBLISH_CURSEFORGE,
    MODRINTH as PUBLISH_MODRINTH,
    PlatformPublishing,
    PublishingError,
)
from models.integration import (
    GITHUB,
    MAVEN,
    MODRINTH,
    IntegrationError,
    ModIntegration,
    ModrinthProvider,
    external_id,
    normalize_provider,
    provider_for_user,
)
from models.integration_manifest import (
    IntegrationManifest,
    IntegrationManifestError,
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
from models.mod import DuplicateModError, Mod, UploadVerificationError
from models.mod_dependency import DependencyError, ModDependency
from models.modpack import Modpack
from models.modversion import IncompatibleModVersionError, MissingDependencyVersionError, Modversion
from models.modversion_provider_id import (
    ModversionProviderId,
    ModversionProviderIdError,
)
from models.session import Session
from models.user import User
from mysql import connector
from werkzeug.utils import secure_filename
from models.common import api_only, app_url, public_repo_url, debug, host, port, md5_repo_url, R2_URL, db_name, R2_BUCKET, new_user, migratetechnic, solderpy_version, R2_REGION, R2_ENDPOINT, R2_ACCESS_KEY, R2_SECRET_KEY, UPLOAD_FOLDER, common, DB_IS_UP, cache_size, cache_ttl, write_api, curseforge_api_key, legacy_modversion_adding
from models.user_modpack import User_modpack
from models.errorPrinter import ErrorPrinter

__version__ = solderpy_version

asite = Blueprint("asite", __name__)

if DB_IS_UP == 1 and not api_only:
    Session.start_session_loop()


def _redirect_back():
    """Return to a same-host management page without allowing an open redirect."""
    referrer = request.referrer
    if referrer:
        parsed = urlsplit(referrer)
        current_host = (urlsplit(request.host_url).hostname or "").casefold()
        referrer_host = (parsed.hostname or "").casefold()
        path = parsed.path or "/"
        if (
            parsed.scheme.casefold() in {"http", "https"}
            and referrer_host == current_host
            and path.startswith("/")
            and not path.startswith("//")
            and not any(ord(character) < 32 for character in path)
        ):
            destination = path
            if parsed.query:
                destination += "?" + parsed.query
            # Only a relative path from a same-host URL reaches redirect().
            return redirect(destination)  # lgtm[py/url-redirection]
    return redirect(url_for("asite.index"))


def _enabled_downloader_specs(settings):
    """Return downloader families enabled for this installation."""
    return tuple(
        downloader
        for downloader in PlatformPackExport.downloader_specs()
        if settings.get(downloader.setting_key, False)
    )


def _available_downloaders(build, settings, platform):
    specs = _enabled_downloader_specs(settings)
    if not specs:
        return (), None
    try:
        return (
            PlatformPackExport.available_downloaders(
                build,
                platform,
                specs=specs,
                curseforge_api_key=curseforge_api_key,
            ),
            None,
        )
    except PlatformExportError as error:
        return (), str(error)


def _downloader_export_enabled(selection):
    downloader = PlatformPackExport.downloader_spec(selection)
    if downloader is None:
        return True
    if DistributionSettings.is_enabled(downloader.setting_key):
        return True
    flash(f"Enable {downloader.label} exports before using that downloader.", "error")
    return False

## Allowed extensions to be uploaded
ALLOWED_EXTENSIONS = {'zip', 'jar'}

R2 = boto3.client('s3',
                  region_name=R2_REGION,
                  endpoint_url=R2_ENDPOINT,
                  aws_access_key_id=R2_ACCESS_KEY,
                  aws_secret_access_key=R2_SECRET_KEY)


def _materialize_integration_version(mod_id, build_id, version_id):
    if User.get_permission_token(session["token"], "mods_manage") == 0:
        raise IntegrationError(
            "Mod management permission is required to import a provider version."
        )
    mod = Mod.get_by_id(mod_id)
    build = Build.get_by_id(build_id)
    if mod is None or build is None:
        raise IntegrationError("The selected mod or build no longer exists.")
    try:
        return ModIntegration.materialize(
            mod,
            build,
            version_id,
            Session.get_user_id(session["token"]),
            UPLOAD_FOLDER,
            r2_client=R2 if R2_BUCKET else None,
            r2_bucket=R2_BUCKET,
        )
    except IntegrationError:
        raise
    except Exception as error:
        ErrorPrinter.message("failed to import provider mod version", error)
        raise IntegrationError(
            "The provider version could not be imported. Check the server log."
        ) from error


def _selected_integration_version(value):
    value = str(value or "")
    prefix = "integration:"
    if not value.startswith(prefix):
        return None
    return external_id(value[len(prefix):])


def _latest_provider_versions(integrated_mods, build, user_id):
    """Return newest compatible provider metadata without importing files."""
    latest = {}
    errors = []
    for integrated_mod in integrated_mods or ():
        try:
            mod = Mod.get_by_id(integrated_mod["id"])
            versions = ModIntegration.list_versions(mod, build, user_id)
            if versions:
                latest[int(integrated_mod["id"])] = versions[0]
        except IntegrationError as error:
            errors.append(f'{integrated_mod["pretty_name"]}: {error}')
        except Exception as error:
            ErrorPrinter.message("failed to check provider-managed mod", error)
            errors.append(
                f'{integrated_mod["pretty_name"]}: provider update check failed'
            )
    return latest, errors

def createFolder(dirName):
    os.makedirs(dirName, exist_ok=True)


def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@asite.context_processor
def inject_menu():
    user_id = (
        Session.get_user_id(session["token"])
        if "token" in session
        else None
    )
    markedbuildid2 = Build.get_marked_build(user_id) if user_id else 0
    pinnedmodpacks = Modpack.get_by_pinned(user_id) if user_id else []
    
    return dict(
        markedbuildid2=markedbuildid2,
        solderversion=solderpy_version,
        pinnedmodpacks=pinnedmodpacks,
        write_api_enabled=write_api,
        night_mode=bool(getattr(g, "solder_night_mode", False)),
    )


@asite.route("/")
def index():
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for('alogin.login'))

    dashboard = Dashboard.load(Session.get_user_id(session["token"]))
    dashboard["repository_health"] = Dashboard.repository_health(
        public_repo_url, md5_repo_url, R2_BUCKET
    )
    return render_template("index.html", dashboard=dashboard)


@asite.route("/logout", methods=["POST"])
def logout():
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for('alogin.login'))

    Session.delete_session(session["token"])
    return redirect(url_for("alogin.login"))


@asite.route("/modversion/<id>", methods=["GET"])
def modversion(id):
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for('alogin.login'))
    
    if User.get_permission_token(session["token"], "mods_manage") == 0:
        return _redirect_back()

    mod = Mod.get_by_id(id)
    upstream_versions = []
    upstream_error = None

    try:
        modversions = mod.get_versions()
        for version in modversions:
            version["mcil_ready"] = MCInstanceJar.has_complete_metadata(
                version.get("jarmd5"), version.get("jarfilesize")
            )
        dependencies, available_dependencies = ModDependency.get_management_data(id)
    except connector.ProgrammingError as e:
        Database.create_tables()
        modversions = []
        dependencies = []
        available_dependencies = []
        flash("unable to get modversions", "error")

    if getattr(mod, "integration_provider", None):
        try:
            upstream_versions = ModIntegration.list_unimported_versions(
                mod, Session.get_user_id(session["token"])
            )
        except IntegrationError as error:
            upstream_error = str(error)
        except Exception as error:
            ErrorPrinter.message("failed to list provider mod versions", error)
            upstream_error = (
                "Upstream versions could not be loaded. Check the server log."
            )

    return render_template(
        "modversion.html",
        modSlug=mod.name,
        modversions=modversions,
        mod=mod,
        mirror_url=public_repo_url,
        dependencies=dependencies,
        available_dependencies=available_dependencies,
        upstream_versions=upstream_versions,
        upstream_error=upstream_error,
        legacy_modversion_adding=legacy_modversion_adding,
    )


@asite.route(
    "/modversion/<int:mod_id>/manage/<int:version_id>",
    methods=["GET", "POST"],
)
def manage_modversion(mod_id, version_id):
    if "token" not in session or not Session.verify_session(
        session["token"], request.remote_addr
    ):
        return redirect(url_for("alogin.login"))
    if User.get_permission_token(session["token"], "mods_manage") == 0:
        return redirect(url_for("asite.index"))

    mod = Mod.get_by_id(mod_id)
    version = Modversion.get_by_id(version_id)
    if (
        mod is None
        or version is None
        or int(version.mod_id) != int(mod.id)
    ):
        return render_template("404.html", error="Mod version not found"), 404

    if request.method == "POST":
        from api import clear_api_caches

        if "verify_zip_submit" in request.form:
            try:
                version.rehash(md5_repo_url, mod.name)
            except (OSError, requests.RequestException, ValueError) as error:
                flash(str(error), "error")
            else:
                clear_api_caches()
                flash(
                    f"Verified ZIP ({version.md5}, {version.filesize} bytes).",
                    "success",
                )
        elif "jar_action_submit" in request.form:
            was_ready = MCInstanceJar.is_ready(version.jarmd5)
            try:
                jar_md5 = MCInstanceJar.create(
                    mod,
                    version,
                    md5_repo_url,
                    UPLOAD_FOLDER,
                    R2,
                    R2_BUCKET,
                )
            except MCInstanceExportError as error:
                flash(str(error), "error")
            except Exception as error:
                ErrorPrinter.message("failed to create or verify raw JAR", error)
                flash("Failed to create or verify the raw JAR.", "error")
            else:
                clear_api_caches()
                action = "Verified" if was_ready else "Created"
                flash(f"{action} the raw JAR ({jar_md5}).", "success")
        elif "curseforge_file_id_submit" in request.form:
            mapping = None
            if str(getattr(mod, "integration_provider", "") or "").upper() == MODRINTH:
                mapping = PlatformExportOverride.get_by_modrinth_project_id(
                    getattr(mod, "integration_project_id", None)
                )
            try:
                if mapping is None:
                    raise ModversionProviderIdError(
                        "Configure a Modrinth-CurseForge sync mapping for this "
                        "mod before adding a CurseForge file ID."
                    )
                value = request.form.get("curseforge_file_id", "").strip()
                if value:
                    ModversionProviderId.save(
                        version.id,
                        ModversionProviderId.CURSEFORGE,
                        mapping.curseforge_project_id,
                        value,
                    )
                    flash("Saved the manual CurseForge file ID.", "success")
                else:
                    ModversionProviderId.delete(
                        version.id, ModversionProviderId.CURSEFORGE
                    )
                    flash("Removed the manual CurseForge file ID.", "success")
            except ModversionProviderIdError as error:
                flash(str(error), "error")
            else:
                clear_api_caches()
        else:
            value = request.form.get("jar_url_override", "")
            try:
                if value.strip() and not Modversion.JAR_MD5_PATTERN.fullmatch(
                    str(version.jarmd5 or "").strip()
                ):
                    raise ValueError(
                        "Create and verify the raw JAR before adding an override URL."
                    )
                Modversion.update_jar_url_override(version.id, mod.id, value)
            except ValueError as error:
                flash(str(error), "error")
            else:
                clear_api_caches()
                flash("updated the JAR download override", "success")
        return redirect(
            url_for(
                "asite.manage_modversion",
                mod_id=mod.id,
                version_id=version.id,
            )
        )

    maven_artifact = None
    if getattr(mod, "integration_provider", None) == MAVEN:
        maven_artifact = MavenArtifact.get_by_mod_id(mod.id)
    curseforge_mapping = None
    curseforge_file_id = None
    if str(getattr(mod, "integration_provider", "") or "").upper() == MODRINTH:
        curseforge_mapping = PlatformExportOverride.get_by_modrinth_project_id(
            getattr(mod, "integration_project_id", None)
        )
        if curseforge_mapping is not None:
            curseforge_file_id = ModversionProviderId.get(
                version.id,
                ModversionProviderId.CURSEFORGE,
                curseforge_mapping.curseforge_project_id,
            )
    return render_template(
        "manage_modversion.html",
        mod=mod,
        version=version,
        mirror_url=public_repo_url,
        builds=version.get_management_builds(),
        maven_artifact=maven_artifact,
        curseforge_mapping=curseforge_mapping,
        curseforge_file_id=curseforge_file_id,
        jar_ready=MCInstanceJar.is_ready(version.jarmd5),
    )


@asite.route("/modversion/<id>", methods=["POST"])
def newmodversion(id):
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for('alogin.login'))
    
    if User.get_permission_token(session["token"], "mods_manage") == 0:
                return _redirect_back()

    if "link_modrinth_submit" in request.form or "link_github_submit" in request.form:
        mod = Mod.get_by_id(id)
        provider = (
            GITHUB if "link_github_submit" in request.form else MODRINTH
        )
        reference = request.form.get(
            "github_reference" if provider == GITHUB else "modrinth_reference",
            "",
        )
        try:
            linked, project = ModIntegration.link_existing(
                mod,
                provider,
                reference,
                Session.get_user_id(session["token"]),
            )
        except IntegrationError as error:
            flash(str(error), "error")
        else:
            flash(
                f"Linked {linked.pretty_name} to {project.provider.title()} "
                f"project {project.title}.",
                "success",
            )
        return redirect(url_for("asite.modversion", id=id))

    if "unlink_integration_submit" in request.form:
        mod = Mod.get_by_id(id)
        if mod is None or mod.integration_provider not in {MODRINTH, GITHUB}:
            flash("This mod has no detachable integration.", "error")
        elif Mod.unlink_integration(id, mod.integration_provider) is None:
            flash("The integration could not be disconnected.", "error")
        else:
            flash(
                f"Disconnected {mod.integration_provider.title()} without deleting local versions.",
                "success",
            )
        return redirect(url_for("asite.modversion", id=id))

    if "import_integration_version_submit" in request.form:
        mod = Mod.get_by_id(id)
        try:
            if mod is None:
                raise IntegrationError("The managed mod no longer exists.")
            result = ModIntegration.materialize_for_management(
                mod,
                external_id(request.form.get("integration_version_id")),
                request.form.getlist("integration_minecraft"),
                request.form.getlist("integration_modloader"),
                Session.get_user_id(session["token"]),
                UPLOAD_FOLDER,
                r2_client=R2 if R2_BUCKET else None,
                r2_bucket=R2_BUCKET,
            )
        except IntegrationError as error:
            flash(str(error), "error")
        except Exception as error:
            ErrorPrinter.message("failed to import provider mod version", error)
            flash(
                "The provider version could not be imported. Check the server log.",
                "error",
            )
        else:
            action = "Imported" if result.created else "Already imported"
            flash(f"{action} version {result.version.version}.", "success")
        return redirect(url_for("asite.modversion", id=id))

    if (
        "sync_github_ref_submit" in request.form
        or "sync_github_tag_submit" in request.form
    ):
        mod = Mod.get_by_id(id)
        try:
            common_arguments = (
                mod,
                request.form.get("github_minecraft"),
                request.form.get("github_modloader"),
            )
            common_keywords = {
                "r2_client": R2 if R2_BUCKET else None,
                "r2_bucket": R2_BUCKET,
            }
            if "sync_github_tag_submit" in request.form:
                result = ModIntegration.materialize_latest_github_tag(
                    *common_arguments,
                    Session.get_user_id(session["token"]),
                    UPLOAD_FOLDER,
                    **common_keywords,
                )
            else:
                result = ModIntegration.materialize_github_ref(
                    *common_arguments,
                    request.form.get("github_ref"),
                    request.form.get("github_version"),
                    Session.get_user_id(session["token"]),
                    UPLOAD_FOLDER,
                    **common_keywords,
                )
        except (IntegrationError, InvalidModloaderError) as error:
            flash(str(error), "error")
        except Exception as error:
            ErrorPrinter.message("failed to synchronize GitHub config package", error)
            flash(
                "The GitHub config package could not be synchronized. Check the server log.",
                "error",
            )
        else:
            action = "Imported" if result.created else "Already imported"
            flash(f"{action} config version {result.version.version}.", "success")
        return redirect(url_for("asite.modversion", id=id))

    if "adddependency_submit" in request.form:
        dependency_mod_id = request.form.get("dependency_mod_id", "").strip()
        if not dependency_mod_id:
            flash("select a required dependency", "error")
            return redirect(url_for("asite.modversion", id=id))
        try:
            ModDependency.add(id, dependency_mod_id)
        except DependencyError as error:
            flash(str(error), "error")
        else:
            flash("added required dependency", "success")
        return redirect(url_for("asite.modversion", id=id))

    if "deletedependency_submit" in request.form:
        if "dependency_id" not in request.form:
            return redirect(url_for("asite.modversion", id=id))
        if ModDependency.delete(request.form["dependency_id"], id):
            flash("removed required dependency", "success")
        else:
            flash("required dependency was not found", "error")
        return redirect(url_for("asite.modversion", id=id))

    if "form-submit" in request.form:
        mod_side = request.form['flexRadioDefault']
        mod_type = request.form['type']
        Mod.update(id, request.form["name"], request.form["description"], request.form["author"], request.form["link"], request.form["pretty_name"], mod_side, mod_type, request.form.get("notes", request.form.get("internal_note", "")))
        flash("updated " + id, "success")
        return redirect(url_for("asite.modversion", id=id))
    if "deleteversion_submit" in request.form:
        if User.get_permission_token(session["token"], "mods_delete") == 0:
                return _redirect_back()
        if "delete_id" not in request.form:
            return redirect(url_for("asite.modversion", id=id))
        Modversion.delete_modversion(request.form["delete_id"])
        flash("deleted " + id, "success")
        return redirect(url_for("asite.modversion", id=id))
    if "addtoselbuild_submit" in request.form:
        if User.get_permission_token(session["token"], "modpacks_manage") == 0:
                return _redirect_back()
        if "addtoselbuild_id" not in request.form:
            return redirect(url_for("asite.modversion", id=id))
        try:
            added_dependencies = Modversion.add_modversion_to_selected_build(request.form["addtoselbuild_id"], id, "0", "1", "0")
        except (IncompatibleModVersionError, MissingDependencyVersionError) as error:
            flash(str(error), "error")
            return redirect(url_for("asite.modversion", id=id))
        message = "added to marked build " + id
        if added_dependencies:
            message += " with required dependencies: " + ", ".join(added_dependencies)
        flash(message, "success")
        return redirect(url_for("asite.modversion", id=id))
    if "deletemod_submit" in request.form:
        if User.get_permission_token(session["token"], "mods_delete") == 0:
                return _redirect_back()
        if "mod_delete_id" not in request.form:
            return redirect(url_for("asite.modversion", id=id))
        Mod.delete_mod(request.form["mod_delete_id"])
        flash("deleted mod" + id, "success")
        return redirect(url_for('asite.modlibrary'))
    if "newmodvermanual_submit" in request.form:
        if not legacy_modversion_adding:
            flash(
                "Legacy manual version adding is disabled by the server.",
                "error",
            )
            return redirect(url_for("asite.modversion", id=id))
        if User.get_permission_token(session["token"], "mods_create") == 0:
                return _redirect_back()
        mod = Mod.get_by_id(id)
        if mod is None:
            flash("the selected mod no longer exists", "error")
            return redirect(url_for("asite.modlibrary"))
        if mod.integration_provider:
            flash(
                "Provider-managed mods import versions from the build editor.",
                "error",
            )
            return redirect(url_for("asite.modversion", id=id))
        try:
            filesie2 = Modversion.get_file_size(
                md5_repo_url,
                mod.name,
                request.form["newmodvermanual_version"],
            )
        except (OSError, requests.RequestException, ValueError) as error:
            flash(str(error), "error")
            return redirect(url_for("asite.modversion", id=id))
        if request.form["newmodvermanual_md5"] != "":
            try:
                Modversion.new(id, request.form["newmodvermanual_version"], request.form["newmodvermanual_mcversion"], request.form["newmodvermanual_md5"], filesie2, "0", modloader=request.form.getlist("newmodvermanual_modloader"))
            except ValueError as error:
                flash(str(error), "error")
        else:
            # Todo Add filesize rehash and md5 hash, if fails do not add
            try:
                Modversion.new(
                    id,
                    request.form["newmodvermanual_version"],
                    request.form["newmodvermanual_mcversion"],
                    "0",
                    filesie2,
                    "0",
                    md5_repo_url,
                    modloader=request.form.getlist("newmodvermanual_modloader"),
                    repository_mod_slug=mod.name,
                )
            except ValueError as error:
                flash(str(error), "error")
    return redirect(url_for("asite.modversion", id=id))


@asite.route("/newmod", methods=["GET", "POST"])
def newmod():
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for('alogin.login'))
    
    if User.get_permission_token(session["token"], "mods_create") == 0:
        return _redirect_back()
    
    if request.method == "POST":
        mod_side = request.form['flexRadioDefault']
        mod_type = request.form['type']
        try:
            Mod.new(request.form["name"], request.form["description"], request.form["author"], request.form["link"], request.form["pretty_name"], mod_side, mod_type, request.form.get("notes", request.form.get("internal_note", "")))
        except DuplicateModError:
            flash(
                f'A mod with the slug "{request.form["name"]}" already exists.',
                "error",
            )
            return render_template("newmod.html"), 409
        flash("added mod", "success")
        return redirect(url_for('asite.modlibrary'))

    return render_template("newmod.html")


@asite.route("/integrations", methods=["GET", "POST"])
def integrations():
    if "token" not in session or not Session.verify_session(
        session["token"], request.remote_addr
    ):
        return redirect(url_for("alogin.login"))
    if User.get_permission_token(session["token"], "mods_create") == 0:
        return redirect(url_for("asite.index"))

    user_id = Session.get_user_id(session["token"])
    if request.method == "POST":
        try:
            if "import_project" in request.form:
                mod, created = ModIntegration.import_project(
                    request.form.get("provider"),
                    request.form.get("project_id"),
                    user_id,
                )
                if created:
                    flash(f"added {mod.pretty_name} to the mod library", "success")
                else:
                    flash(f"{mod.pretty_name} is linked to Modrinth", "success")
                return redirect(url_for("asite.modversion", id=mod.id))
        except IntegrationError as error:
            flash(str(error), "error")
        return redirect(
            url_for(
                "asite.integrations",
                provider=request.form.get("provider", MODRINTH).lower(),
            )
        )

    try:
        selected_provider = normalize_provider(
            request.args.get("provider", MODRINTH)
        )
    except IntegrationError:
        selected_provider = MODRINTH
    # The integration browser creates ordinary mod entries and is therefore
    # intentionally Modrinth-only. GitHub repositories are attached from an
    # existing CONFIG entry so they can never become downloadable mod jars.
    if selected_provider != MODRINTH:
        selected_provider = MODRINTH
    query = request.args.get("q", "").strip()[:100]
    projects = []
    if query:
        try:
            projects = provider_for_user(selected_provider, user_id).search(query)
            imported_ids = Mod.get_integration_project_ids(selected_provider)
            projects = [
                (project, project.project_id in imported_ids)
                for project in projects
            ]
        except IntegrationError as error:
            flash(str(error), "error")

    distribution_settings = DistributionSettings.get_all()
    return render_template(
        "integrations.html",
        provider=selected_provider,
        query=query,
        projects=projects,
        modrinth=MODRINTH,
        can_manage_repositories=bool(
            User.get_permission_token(session["token"], "solder_env")
        ),
    )


@asite.route("/github", methods=["GET", "POST"])
def github():
    if "token" not in session or not Session.verify_session(
        session["token"], request.remote_addr
    ):
        return redirect(url_for("alogin.login"))
    if User.get_permission_token(session["token"], "mods_manage") == 0:
        return redirect(url_for("asite.index"))

    user_id = Session.get_user_id(session["token"])
    if request.method == "POST":
        try:
            raw_mod_id = str(request.form.get("mod_id", ""))
            if not raw_mod_id.isdigit():
                raise IntegrationError("Select a valid config entry.")
            mod_id = int(raw_mod_id)
            mod = Mod.get_by_id(mod_id)
            if mod is None:
                raise IntegrationError("The selected config entry was not found.")
            linked, project = ModIntegration.link_existing(
                mod,
                GITHUB,
                request.form.get("repository"),
                user_id,
            )
            flash(
                f"linked {linked.pretty_name} to {project.author}/{project.slug}",
                "success",
            )
            return redirect(url_for("asite.modversion", id=linked.id))
        except IntegrationError as error:
            flash(str(error), "error")
        return redirect(url_for("asite.github"))

    mods = Mod.get_all()
    available_configs = sorted(
        (
            mod for mod in mods
            if mod.modtype == "CONFIG"
            and not mod.integration_provider
            and not mod.integration_project_id
        ),
        key=lambda mod: (mod.pretty_name or mod.name).casefold(),
    )
    configured_configs = []
    provider = provider_for_user(GITHUB, user_id)
    for mod in (mod for mod in mods if mod.integration_provider == GITHUB):
        project = None
        error = None
        try:
            project = provider.get_project(mod.integration_project_id)
        except IntegrationError as provider_error:
            error = str(provider_error)
        configured_configs.append(
            {"mod": mod, "project": project, "error": error}
        )

    return render_template(
        "github.html",
        available_configs=available_configs,
        configured_configs=configured_configs,
    )


@asite.route("/integrations/manifest", methods=["POST"])
def import_integration_manifest():
    if "token" not in session or not Session.verify_session(
        session["token"], request.remote_addr
    ):
        return redirect(url_for("alogin.login"))
    if User.get_permission_token(session["token"], "mods_create") == 0:
        return redirect(url_for("asite.index"))

    try:
        manifest = IntegrationManifest.parse(request.files.get("manifest"))
        if manifest.includes_maven:
            if User.get_permission_token(session["token"], "solder_env") == 0:
                raise IntegrationManifestError(
                    "Environment permission is required for Maven manifest entries."
                )
            if request.form.get("redistribution_confirmed") != "1":
                raise IntegrationManifestError(
                    "Confirm permission to download and rehost Maven artifacts."
                )
        result = manifest.import_all(Session.get_user_id(session["token"]))
    except IntegrationManifestError as error:
        flash(str(error), "error")
        return redirect(url_for("asite.integrations"))

    flash(
        f"Manifest import: {result.created} added, "
        f"{result.existing} already configured, {len(result.errors)} failed.",
        "success" if not result.errors else "error",
    )
    for error in result.errors[:10]:
        flash(error, "error")
    return redirect(url_for("asite.integrations"))


@asite.route("/integrations/manifest/export", methods=["GET"])
def export_integration_manifest():
    if "token" not in session or not Session.verify_session(
        session["token"], request.remote_addr
    ):
        return redirect(url_for("alogin.login"))
    if User.get_permission_token(session["token"], "mods_create") == 0:
        return redirect(url_for("asite.index"))

    try:
        output = IntegrationManifest.from_database().render()
    except IntegrationManifestError as error:
        flash(str(error), "error")
        return redirect(url_for("asite.integrations"))

    return send_file(
        output,
        mimetype="application/json",
        as_attachment=True,
        download_name="solder.py-integration-manifest.json",
    )


@asite.route("/help")
def help_index():
    if "token" not in session or not Session.verify_session(
        session["token"], request.remote_addr
    ):
        return redirect(url_for("alogin.login"))
    return render_template("help_index.html", documents=HELP_DOCUMENTS)


@asite.route("/help/<document>")
def help_document(document):
    if "token" not in session or not Session.verify_session(
        session["token"], request.remote_addr
    ):
        return redirect(url_for("alogin.login"))
    selected = get_help_document(document)
    if selected is None:
        return render_template("404.html", error="Help document not found"), 404
    return render_template(
        "help_document.html",
        document=selected,
        content=render_help_document(selected),
    )


@asite.route("/maven", methods=["GET", "POST"])
def maven():
    if "token" not in session or not Session.verify_session(
        session["token"], request.remote_addr
    ):
        return redirect(url_for("alogin.login"))
    can_create_mods = bool(
        User.get_permission_token(session["token"], "mods_create")
    )
    can_manage_mods = bool(
        User.get_permission_token(session["token"], "mods_manage")
    )
    can_manage_repositories = bool(
        User.get_permission_token(session["token"], "solder_env")
    )
    if not (can_create_mods or can_manage_mods or can_manage_repositories):
        return redirect(url_for("asite.index"))

    if request.method == "POST":
        artifact = None
        try:
            if "add_repository" in request.form:
                if not can_manage_repositories:
                    raise MavenError(
                        "Environment permission is required to add a repository."
                    )
                repository = MavenRepository.new(
                    request.form.get("repository_name"),
                    request.form.get("base_url"),
                )
                flash(f"added Maven repository {repository.name}", "success")
            elif "delete_repository" in request.form:
                if not can_manage_repositories:
                    raise MavenError(
                        "Environment permission is required to delete a repository."
                    )
                MavenRepository.delete(request.form.get("repository_id"))
                flash("deleted Maven repository", "success")
            elif "add_artifact" in request.form:
                if not can_create_mods:
                    raise MavenError(
                        "Mod creation permission is required to add an artifact."
                    )
                if request.form.get("redistribution_confirmed") != "1":
                    raise MavenError(
                        "Confirm that this mod may be downloaded and rehosted."
                    )
                artifact = MavenArtifact.new(
                    request.form.get("repository_id"),
                    request.form.get("group_id"),
                    request.form.get("artifact_id"),
                    request.form.get("classifier"),
                    request.form.get("extension", "jar"),
                    request.form.get("version_mode"),
                    request.form.get("version_pattern"),
                    request.form.get("fixed_minecraft"),
                    request.form.get("modloader"),
                    request.form.get("slug"),
                    request.form.get("title"),
                    request.form.get("description"),
                    request.form.get("author"),
                    request.form.get("link"),
                    request.form.get("side"),
                )
                if request.form.get("solderpy_loader_direct") == "1":
                    artifact = MavenArtifact.update_solderpy_loader_direct(
                        artifact.id, True
                    )
                MavenCatalog.refresh(artifact)
                mod, _created = ModIntegration.import_project(
                    MAVEN, str(artifact.id),
                    Session.get_user_id(session["token"]),
                )
                MavenArtifact.attach_mod(artifact.id, mod.id)
                flash(f"added {artifact.title} from Maven", "success")
                return redirect(url_for("asite.maven_artifact", artifact_id=artifact.id))
        except (MavenError, IntegrationError, InvalidModloaderError) as error:
            if artifact is not None:
                MavenArtifact.delete_unlinked(artifact.id)
            flash(str(error), "error")
        except connector.IntegrityError:
            if artifact is not None:
                MavenArtifact.delete_unlinked(artifact.id)
            flash("That Maven repository, artifact, or mod already exists.", "error")
        return redirect(url_for("asite.maven"))

    return render_template(
        "maven.html",
        repositories=MavenRepository.get_all(),
        artifacts=MavenArtifact.get_all(),
        version_modes=MAVEN_VERSION_MODES,
        default_pattern=DEFAULT_VERSION_PATTERN,
        can_create_mods=can_create_mods,
        can_manage_mods=can_manage_mods,
        can_manage_repositories=can_manage_repositories,
    )


@asite.route("/maven/<int:artifact_id>", methods=["GET", "POST"])
def maven_artifact(artifact_id):
    if "token" not in session or not Session.verify_session(
        session["token"], request.remote_addr
    ):
        return redirect(url_for("alogin.login"))
    if User.get_permission_token(session["token"], "mods_manage") == 0:
        return redirect(url_for("asite.index"))

    artifact = MavenArtifact.get(artifact_id)
    if artifact is None:
        return render_template("404.html", error="Maven artifact not found"), 404
    if request.method == "POST":
        try:
            if "save_rule" in request.form:
                MavenArtifact.update_rule(
                    artifact_id,
                    request.form.get("version_mode"),
                    request.form.get("version_pattern"),
                    request.form.get("fixed_minecraft"),
                    request.form.get("modloader"),
                )
                flash("updated Maven version mapping rule", "success")
            elif "save_direct_downloads" in request.form:
                MavenArtifact.update_solderpy_loader_direct(
                    artifact_id,
                    request.form.get("solderpy_loader_direct") == "1",
                )
                from api import clear_api_caches

                clear_api_caches()
                flash("updated SolderPy Loader Maven downloads", "success")
            elif "refresh_versions" in request.form:
                versions = MavenCatalog.refresh(artifact)
                flash(f"loaded {len(versions)} Maven version(s)", "success")
            elif "save_mapping" in request.form:
                MavenVersion.update_manual(
                    request.form.get("mapping_id"),
                    artifact_id,
                    request.form.get("minecraft"),
                    request.form.get("mod_version"),
                    request.form.get("modloader"),
                    request.form.get("enabled") == "1",
                )
                flash("updated Maven version mapping", "success")
        except (MavenError, InvalidModloaderError) as error:
            flash(str(error), "error")
        return redirect(url_for("asite.maven_artifact", artifact_id=artifact_id))

    versions = MavenVersion.get_all(artifact_id)
    return render_template(
        "mavenartifact.html",
        artifact=artifact,
        versions=versions,
        version_modes=MAVEN_VERSION_MODES,
        default_pattern=DEFAULT_VERSION_PATTERN,
    )


@asite.route("/modpack/<id>", methods=["GET", "POST"])
def modpack(id):
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for('alogin.login'))
    
    if User.get_permission_token(session["token"], "modpacks_manage") == 0:
        return _redirect_back()
    
    if User_modpack.get_user_modpackpermission(session["token"], id) == False:
        return _redirect_back()

    try:
        modpack = Modpack.get_by_id(id)
        builds = modpack.get_builds()
    except connector.ProgrammingError as e:
        Database.create_tables()
        builds = []
        flash("error when building modpack build", "error")

    if request.method == "POST":
        allowed_build_ids = {str(build.id) for build in builds}
        allowed_versions = {str(build.version) for build in builds}
        if "form-submit" in request.form:
            
            if User.get_permission_token(session["token"], "modpacks_create") == 0:
                return _redirect_back()
            
            publish = "0"
            private = "0"
            min_java = request.form.get("min_java", "").strip()
            if not min_java or min_java.upper() == "NONE":
                min_java = None
            java_runtime = request.form.get("java_runtime") or None
            if "publish" in request.form:
                publish = request.form['publish']
            if "private" in request.form:
                private = request.form['private']
            clonebuild = ""
            if "clonebuild" in request.form and request.form['clonebuild'] != "":
                clonebuild = request.form['clonebuild']
            if "clonebuildman" in request.form and request.form['clonebuildman'] != "":
                clonebuild = request.form['clonebuildman']
            if clonebuild:
                source_build = Build.get_by_id(clonebuild)
                if source_build is None or not User_modpack.get_user_modpackpermission(
                    session["token"], source_build.modpack_id
                ):
                    flash("The clone source is unavailable.", "error")
                    return redirect(url_for("asite.modpack", id=id))
            try:
                Build.new(id, request.form["version"], request.form["mcversion"], publish, private, min_java, request.form["memory"], clonebuild, request.form.get("forge") or None, request.form.get("modloader"), java_runtime)
            except (InvalidJavaRuntimeError, InvalidModloaderError) as error:
                flash(str(error), "error")
                return redirect(url_for("asite.modpack", id=id))
            flash("added build", "success")
            return redirect(url_for("asite.modpack", id=id))
        if "recommended_submit" in request.form:
            if request.form["modid"] not in allowed_versions:
                return redirect(url_for("asite.modpack", id=id))
            common.update_checkbox(id, request.form["modid"], "recommended", "modpacks")
            flash("updated " + id, "success")
            return redirect(url_for("asite.modpack", id=id))
        if "latest_submit" in request.form:
            if request.form["modid"] not in allowed_versions:
                return redirect(url_for("asite.modpack", id=id))
            common.update_checkbox(id, request.form["modid"], "latest", "modpacks")
            flash("updated " + id, "success")
            return redirect(url_for("asite.modpack", id=id))
        if "is_published_submit" in request.form:
            if request.form["modid"] not in allowed_build_ids:
                return redirect(url_for("asite.modpack", id=id))
            common.update_checkbox(request.form["modid"], request.form["check"], "is_published", "builds")
            flash("updated " + id, "success")
            return redirect(url_for("asite.modpack", id=id))
        if "private_submit" in request.form:
            if request.form["modid"] not in allowed_build_ids:
                return redirect(url_for("asite.modpack", id=id))
            common.update_checkbox(request.form["modid"], request.form["check"], 'private', 'builds')
            flash("updated " + id, "success")
            return redirect(url_for("asite.modpack", id=id))
        if "marked_submit" in request.form:
            if request.form["modid"] not in allowed_build_ids:
                return redirect(url_for("asite.modpack", id=id))
            Build.update_checkbox_marked(request.form["modid"], request.form["check"])
            flash("updated " + id, "success")
            return redirect(url_for("asite.modpack", id=id))
        if "changelog_submit" in request.form:
            oldversion = request.form["changelog_oldver"]
            newversion = request.form["changelog_newver"]
            return redirect(url_for('asite.changelog', oldver=oldversion, newver=newversion))
        if "deletemod_submit" in request.form:
            
            if User.get_permission_token(session["token"], "modpacks_delete") == 0:
                return _redirect_back()
            
            modpack.delete_modpack(id)
            flash("deleted " + id, "success")
            return redirect(url_for('asite.modpacklibrary'))

    return render_template(
        "modpack.html",
        modpack=builds,
        modpackname=modpack,
        java_runtime_options=MOJANG_JAVA_RUNTIME_OPTIONS,
    )


@asite.route("/changelog/<oldver>-<newver>", methods=["GET"])
def changelog(oldver, newver):
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for('alogin.login'))
    if User.get_permission_token(session["token"], "modpacks_manage") == 0:
                return _redirect_back()
    old_modpack_id = Build.get_modpackid_by_id(oldver)
    new_modpack_id = Build.get_modpackid_by_id(newver)
    if (
        not old_modpack_id
        or not new_modpack_id
        or not User_modpack.get_user_modpackpermission(
            session["token"], old_modpack_id
        )
        or not User_modpack.get_user_modpackpermission(
            session["token"], new_modpack_id
        )
    ):
        return redirect(url_for("asite.modpacklibrary"))

    try:
        changelog = Build_modversion.get_changelog(oldver, newver)
    except connector.ProgrammingError as e:
        Database.create_tables()
        builds = []

    return render_template("changelog.html", changelog=changelog)


@asite.route("/mainsettings", methods=["GET", "POST"])
def mainsettings():
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for('alogin.login'))

    if User.get_permission_token(session["token"], "solder_env") == 0:
        return _redirect_back()

    if request.method == "POST" and "convert_none_mods_submit" in request.form:
        if User.get_permission_token(session["token"], "mods_manage") == 0:
            return redirect(url_for("asite.modlibrary"))
        try:
            result = MCInstanceJar.promote_jar_only_none_mods(
                md5_repo_url,
                UPLOAD_FOLDER,
                R2,
                R2_BUCKET,
            )
            message = (
                f"Legacy JAR scan finished: {result.converted_mods} of "
                f"{result.scanned_mods} NONE mods converted "
                f"({result.converted_versions} of {result.scanned_versions} versions)."
            )
            if result.failures:
                message += f" {len(result.failures)} mods were left unchanged."
                for failure in result.failures[:25]:
                    ErrorPrinter.message("Legacy JAR scan left a mod unchanged", failure)
                if len(result.failures) > 25:
                    ErrorPrinter.message(
                        "Legacy JAR scan omitted additional unchanged mods",
                        len(result.failures) - 25,
                    )
            if result.converted_mods:
                from api import clear_api_caches

                clear_api_caches()
            flash(message, "success")
        except Exception as error:
            ErrorPrinter.message("Unable to scan legacy JAR packages", error)
            flash("The legacy JAR package scan could not be completed.", "error")
        return redirect(url_for("asite.mainsettings"))

    if request.method == "POST" and "export_settings_submit" in request.form:
        try:
            DistributionSettings.update_exports(
                mcil="mcil_enabled" in request.form,
                solderpy_loader="solderpy_loader_enabled" in request.form,
                packwiz="packwiz_enabled" in request.form,
                filedirector="filedirector_enabled" in request.form,
                modpack_director="modpack_director_enabled" in request.form,
                mrpack="mrpack_enabled" in request.form,
                curseforge="curseforge_export_enabled" in request.form,
                prism="prism_export_enabled" in request.form,
            )
            flash("Distribution settings updated.", "success")
        except DistributionSettingsError as error:
            ErrorPrinter.message("Unable to update distribution settings", error)
            flash(str(error), "error")
        return redirect(url_for("asite.mainsettings"))

    distribution_settings = DistributionSettings.get_all()
    return render_template("mainsettings.html", nam=__name__, deb=debug, host=host, port=port, app_url=app_url, public_repo_url=public_repo_url, md5_repo_url=md5_repo_url, r2_url=R2_URL, db_name=db_name, versr=__version__, r2_bucket=R2_BUCKET, newuser=new_user, technic=migratetechnic, DB_IS_UP=DB_IS_UP, cache_size=cache_size, cache_ttl=cache_ttl, distribution_settings=distribution_settings, curseforge_api_configured=bool(curseforge_api_key), legacy_modversion_adding=legacy_modversion_adding)


@asite.route("/platform-export-overrides", methods=["GET", "POST"])
def platform_export_overrides():
    if "token" not in session or not Session.verify_session(
        session["token"], request.remote_addr
    ):
        return redirect(url_for("alogin.login"))
    if User.get_permission_token(session["token"], "solder_env") == 0:
        return redirect(url_for("asite.index"))

    if request.method == "POST":
        try:
            if "import_sync_manifest" in request.form:
                created, updated = PlatformExportOverride.import_manifest(
                    request.files.get("sync_manifest")
                )
                flash(
                    f"Modrinth-CurseForge sync import: {created} added, "
                    f"{updated} updated.",
                    "success",
                )
            elif "create_override" in request.form:
                project = ModrinthProvider().get_project(
                    request.form.get("modrinth_project")
                )
                PlatformExportOverride.create(
                    request.form.get("name") or project.title,
                    project.project_id,
                    request.form.get("curseforge_project_id"),
                    request.form.get("side") or project.side,
                    "override_solder_only" in request.form,
                )
                flash("Modrinth-CurseForge mapping added.", "success")
            elif "set_override_enabled" in request.form:
                enabled_value = request.form.get("override_enabled")
                PlatformExportOverride.set_enabled(
                    request.form.get("override_id"),
                    enabled_value == "1"
                    if enabled_value is not None
                    else "enabled" in request.form,
                )
                flash("Modrinth-CurseForge mapping updated.", "success")
            elif "set_override_solder_only" in request.form:
                solder_only_value = request.form.get("override_solder_only")
                PlatformExportOverride.set_override_solder_only(
                    request.form.get("override_id"),
                    solder_only_value == "1",
                )
                flash("Modrinth-CurseForge mapping updated.", "success")
            elif "delete_override" in request.form:
                PlatformExportOverride.delete(request.form.get("override_id"))
                flash("Modrinth-CurseForge mapping deleted.", "success")
        except (IntegrationError, PlatformExportOverrideError) as error:
            flash(str(error), "error")
        return redirect(url_for("asite.platform_export_overrides"))

    return render_template(
        "platform_export_overrides.html",
        overrides=PlatformExportOverride.get_all(),
        curseforge_api_configured=bool(curseforge_api_key),
    )


@asite.route("/publishing", methods=["GET", "POST"])
def publishing():
    if "token" not in session or not Session.verify_session(
        session["token"], request.remote_addr
    ):
        return redirect(url_for("alogin.login"))
    if User.get_permission_token(session["token"], "modpacks_manage") == 0:
        return redirect(url_for("asite.index"))

    user_id = Session.get_user_id(session["token"])

    if request.method == "POST":
        try:
            if "save_publishing_account" in request.form:
                PlatformPublishing.save_account(
                    request.form.get("account_id"),
                    request.form.get("provider"),
                    request.form.get("name"),
                    request.form.get("provider_token"),
                    "enabled" in request.form,
                    user_id,
                )
                flash("Publishing account saved.", "success")
            elif "delete_publishing_account" in request.form:
                PlatformPublishing.delete_account(
                    request.form.get("account_id"), user_id
                )
                flash("Publishing account deleted.", "success")
            elif "save_publication_target" in request.form:
                PlatformPublishing.save_target(
                    request.form.get("target_id"),
                    request.form.get("modpack_id"),
                    request.form.get("provider_account_id"),
                    request.form.get("project_id"),
                    "enabled" in request.form,
                    user_id,
                )
                flash("Modpack publication target saved.", "success")
            elif "delete_publication_target" in request.form:
                PlatformPublishing.delete_target(
                    request.form.get("target_id"), user_id
                )
                flash("Modpack publication target deleted.", "success")
            elif "release_publication_run" in request.form:
                PlatformPublishing.release_run_for_retry(
                    request.form.get("run_id"), user_id
                )
                flash(
                    "The publication lock was released. Verify the remote "
                    "project before retrying.",
                    "success",
                )
        except (PublishingError, TypeError, ValueError) as error:
            flash(str(error), "error")
        return redirect(url_for("asite.publishing"))

    return render_template(
        "publishing.html",
        accounts=PlatformPublishing.get_accounts(user_id),
        targets=PlatformPublishing.get_targets(user_id),
        publication_runs=PlatformPublishing.get_recent_runs(user_id),
        modpacks=Modpack.get_all_for_user(user_id),
    )


@asite.route("/platform-export-overrides/export", methods=["GET"])
def export_platform_sync():
    if "token" not in session or not Session.verify_session(
        session["token"], request.remote_addr
    ):
        return redirect(url_for("alogin.login"))
    if User.get_permission_token(session["token"], "solder_env") == 0:
        return redirect(url_for("asite.index"))

    try:
        output = PlatformExportOverride.render_manifest()
    except PlatformExportOverrideError as error:
        flash(str(error), "error")
        return redirect(url_for("asite.platform_export_overrides"))

    return send_file(
        output,
        mimetype="application/json",
        as_attachment=True,
        download_name="solder.py-modrinth-curseforge-sync.json",
    )


@asite.route("/apikeylibrary", methods=["GET"])
def apikeylibrary():
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for('alogin.login'))
    
    if User.get_permission_token(session["token"], "solder_keys") == 0:
        return _redirect_back()

    try:
        keys = Key.get_all_keys()
    except connector.ProgrammingError as e:
        Database.create_tables()
        keys = []

    return render_template("apikeylibrary.html", keys=keys)


@asite.route("/apikeylibrary", methods=["POST"])
def apikeylibrary_post():
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for('alogin.login'))
    
    if User.get_permission_token(session["token"], "solder_keys") == 0:
        return _redirect_back()
    
    if request.method == "POST":
        if "form-submit" in request.form:
            if "keyname" not in request.form:
                return redirect(url_for('asite.apikeylibrary'))
            if "api_key" not in request.form:
                return redirect(url_for('asite.apikeylibrary'))
            Key.new_key(request.form["keyname"], request.form["api_key"])
            flash("added key", "success")
            return redirect(url_for('asite.apikeylibrary'))
        if "form2-submit" in request.form:
            if "delete_id" not in request.form:
                return redirect(url_for('asite.apikeylibrary'))
            Key.delete_key(request.form["delete_id"])
            flash("deleted key", "success")

    return redirect(url_for('asite.apikeylibrary'))


@asite.route("/clientlibrary", methods=["GET"])
def clientlibrary():
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for('alogin.login'))
    
    if User.get_permission_token(session["token"], "solder_clients") == 0:
        return _redirect_back()

    try:
        clients = Client.get_all_clients()
    except connector.ProgrammingError as e:
        Database.create_tables()
        clients = []
        flash("error when accessing client table", "error")

    return render_template("clientlibrary.html", clients=clients)


@asite.route("/clientlibrary", methods=["POST"])
def clientlibrary_post():
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for('alogin.login'))
    
    if User.get_permission_token(session["token"], "solder_clients") == 0:
        return _redirect_back()
    
    if request.method == "POST":
        if "form-submit" in request.form:
            if "client_name" not in request.form:
                return redirect(url_for('asite.clientlibrary'))
            if "client_UUID" not in request.form:
                return redirect(url_for('asite.clientlibrary'))
            Client.new(request.form["client_name"], request.form["client_UUID"])
            flash("added client", "success")
            return redirect(url_for('asite.clientlibrary'))
        if "form2-submit" in request.form:
            if "delete_id" not in request.form:
                return redirect(url_for('asite.clientlibrary'))
            Client.delete_client(request.form["delete_id"])
            flash("deleted client", "success")
            return redirect(url_for('asite.clientlibrary'))

    return redirect(url_for('asite.clientlibrary'))


@asite.route("/userlibrary", methods=["GET"])
def userlibrary():
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for('alogin.login'))
    
    fulluserid = User.get_fulluser(session["token"])

    try:
        users = User.get_all_users()
    except connector.ProgrammingError as e:
        Database.create_tables()
        users = []
        flash("error when accessing user table", "error")

    return render_template("userlibrary.html", users=users, fulluserid=fulluserid)


@asite.route("/userlibrary", methods=["POST"])
def userlibrary_post():
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for('alogin.login'))
    
    
    
    if request.method == "POST":
        if "form-submit" in request.form:
            if User.get_permission_token(session["token"], "solder_users") == 0:
                return _redirect_back()
            if "newemail" not in request.form:
                return redirect(url_for('asite.userlibrary'))
            if "newpassword" not in request.form:
                return redirect(url_for('asite.userlibrary'))
            if "newuser" not in request.form:
                return redirect(url_for('asite.userlibrary'))
            User.new(request.form["newuser"], request.form["newemail"], request.form["newpassword"], request.remote_addr, Session.get_user_id(session["token"]))
            flash("added user", "success")
            return redirect(url_for('asite.userlibrary'))
        if "form2-submit" in request.form:
            if User.get_permission_token(session["token"], "solder_users") == 0:
                return _redirect_back()
            if "delete_id" not in request.form:
                return redirect(url_for('asite.userlibrary'))
            User.delete(request.form["delete_id"])
            flash("deleted user", "success")
            return redirect(url_for('asite.userlibrary'))
        if "changeuser_submit" in request.form:
            if "changeuser_id" not in request.form:
                return redirect(url_for('asite.userlibrary'))
            if "changeuser_password" not in request.form:
                return redirect(url_for('asite.userlibrary'))
            userid = str(Session.get_user_id(session["token"]))
            changeid = request.form["changeuser_id"]
            if userid != changeid:
                if User.get_permission_token(session["token"], "solder_users") == 0:
                    return redirect(url_for('asite.userlibrary'))
            User.change(request.form["changeuser_id"], request.form["changeuser_password"], request.remote_addr, Session.get_user_id(session["token"]))
            flash("updated user", "success")
            return redirect(url_for('asite.userlibrary'))

    return redirect(url_for('asite.userlibrary'))


@asite.route("/apitokens", methods=["GET", "POST"])
def apitokens():
    if "token" not in session or not Session.verify_session(
        session["token"], request.remote_addr
    ):
        return redirect(url_for("alogin.login"))
    if not write_api:
        return render_template("404.html", error="Not Found"), 404

    user_id = Session.get_user_id(session["token"])
    new_token = None
    if request.method == "POST":
        if "create_token" in request.form:
            try:
                new_token = ApiToken.create(
                    user_id, request.form.get("token_name", "")
                )
                flash(
                    "API token created. Copy it now; it will not be shown again.",
                    "success",
                )
            except ValueError as error:
                flash(str(error), "error")
        elif "delete_token" in request.form:
            try:
                token_id = int(request.form.get("token_id", ""))
            except (TypeError, ValueError):
                flash("Invalid API token.", "error")
            else:
                if ApiToken.delete(token_id, user_id):
                    flash("API token revoked.", "success")
                else:
                    flash("API token not found.", "error")
            return redirect(url_for("asite.apitokens"))

    return render_template(
        "apitokens.html",
        tokens=ApiToken.get_all(user_id),
        new_token=new_token,
    )


@asite.route("/modpackbuild/<id>", methods=["GET", "POST"])
def modpackbuild(id):
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for('alogin.login'))
    
    if User.get_permission_token(session["token"], "modpacks_manage") == 0:
                return _redirect_back()
    
    modpack_id = Build.get_modpackid_by_id(id)
    if User_modpack.get_user_modpackpermission(session["token"], modpack_id) == False:
        return _redirect_back()

    if request.method == "POST":
        if "form-submit" in request.form:
            publish = "0"
            private = "0"
            min_java = request.form.get("min_java", "").strip()
            if not min_java or min_java.upper() == "NONE":
                min_java = None
            java_runtime = request.form.get("java_runtime") or None
            if "publish" in request.form:
                publish = request.form['publish']
            if "private" in request.form:
                private = request.form['private']
            try:
                Build.update(id, request.form["version"], request.form["mcversion"], publish, private, min_java, request.form["memory"], request.form.get("forge") or None, request.form.get("modloader"), java_runtime)
            except (InvalidJavaRuntimeError, InvalidModloaderError) as error:
                flash(str(error), "error")
                return redirect(url_for("asite.modpackbuild", id=id))
            flash("updated " + id, "success")
            return redirect(url_for("asite.modpackbuild", id=id))
        if "update_all_mods_submit" in request.form:
            integration_errors = []
            preferred_versions = {}
            integrated_mods = Build_modversion.get_integrated_mods(id)
            if integrated_mods and User.get_permission_token(
                session["token"], "mods_manage"
            ) == 0:
                integration_errors.append(
                    "Provider-managed mods need mod management permission."
                )
            else:
                build = Build.get_by_id(id)
                user_id = Session.get_user_id(session["token"])
                integration_names = {
                    int(item["id"]): item["pretty_name"]
                    for item in integrated_mods
                }
                latest_versions, lookup_errors = _latest_provider_versions(
                    integrated_mods, build, user_id
                )
                integration_errors.extend(lookup_errors)
                for mod_id, latest in latest_versions.items():
                    try:
                        materialized = _materialize_integration_version(
                            mod_id, id, latest.version_id
                        )
                        preferred_versions[mod_id] = materialized.version.id
                    except IntegrationError as error:
                        integration_errors.append(
                            f'{integration_names.get(mod_id, f"Mod {mod_id}")}: '
                            f"{error}"
                        )
                    except Exception as error:
                        ErrorPrinter.message(
                            "failed to update provider-managed mod", error
                        )
                        integration_errors.append(
                            f'{integration_names.get(mod_id, f"Mod {mod_id}")}: '
                            "provider update failed"
                        )

            updated = Build_modversion.update_all_compatible(
                id, preferred_versions
            )
            if updated:
                flash(f"updated {updated} mod(s)", "success")
            elif not integration_errors:
                flash("all mods are already up to date", "success")
            if integration_errors:
                flash("; ".join(integration_errors), "error")
            return redirect(url_for("asite.modpackbuild", id=id))
        if "optional_submit" in request.form:
            modpack = Modpack.get_by_id(modpack_id)
            if modpack is None:
                return redirect(url_for("asite.modpacklibrary"))
            try:
                if modpack.optional_mode == ADVANCED_MODE:
                    AdvancedOptional.set_listing(
                        id,
                        request.form["optional_modid"],
                        request.form["optional_check"],
                    )
                    message = "Advanced optional list updated."
                else:
                    Build_modversion.update_optional(
                        request.form["optional_modid"],
                        request.form["optional_check"],
                        id,
                    )
                    message = "Optional state updated."
            except (AdvancedOptionalError, ValueError) as error:
                flash(str(error), "error")
            else:
                from api import clear_api_caches

                clear_api_caches()
                flash(message, "success")
            return redirect(url_for("asite.modpackbuild", id=id))
        if "selmodver_submit" in request.form:
            try:
                selected_version = request.form["selmodver_ver"]
                integration_version = _selected_integration_version(
                    selected_version
                )
                if integration_version:
                    current = Modversion.get_by_id(
                        request.form["selmodver_oldver"]
                    )
                    if current is None:
                        raise IntegrationError(
                            "The current mod version no longer exists."
                        )
                    materialized = _materialize_integration_version(
                        current.mod_id, id, integration_version
                    )
                    selected_version = materialized.version.id
                Modversion.update_modversion_in_build(
                    request.form["selmodver_oldver"], selected_version, id
                )
            except (IncompatibleModVersionError, IntegrationError, ValueError) as error:
                flash(str(error), "error")
                return redirect(url_for("asite.modpackbuild", id=id))
            flash("updated " + id, "success")
            return redirect(url_for("asite.modpackbuild", id=id))
        if "delete_submit" in request.form:
            if User.get_permission_token(session["token"], "modpacks_delete") == 0:
                return _redirect_back()
            if "delete_id" not in request.form:
                return redirect(url_for("asite.modpackbuild", id=id))
            try:
                Build_modversion.delete_build_modversion(
                    request.form["delete_id"], id
                )
            except ValueError as error:
                flash(str(error), "error")
                return redirect(url_for("asite.modpackbuild", id=id))
            flash("deleted modversion", "success")
            return redirect(url_for("asite.modpackbuild", id=id))
        if "deletebuild_submit" in request.form:
            if User.get_permission_token(session["token"], "modpacks_delete") == 0:
                return _redirect_back()
            Build.delete_build(id)
            flash("deleted build" + id, "success")
            return redirect(url_for('asite.modpacklibrary'))
        if "add_mod_submit" in request.form:
            modpack = Modpack.get_by_id(modpack_id)
            if modpack is None:
                return redirect(url_for("asite.modpacklibrary"))
            advanced_mode = modpack.optional_mode == ADVANCED_MODE
            list_in_advanced = (
                advanced_mode and "newadvancedoptional" in request.form
            )
            newoptional = (
                request.form.get("newoptional", "0")
                if not advanced_mode
                else "0"
            )
            mod_id = request.form.get("modnames", "").strip()
            selected_version = request.form.get("modversion", "").strip()
            if not mod_id or not selected_version:
                flash("select a mod and compatible version", "error")
                return redirect(url_for("asite.modpackbuild", id=id))
            selected_mod = Mod.get_by_id(mod_id) if list_in_advanced else None
            if (
                list_in_advanced
                and selected_mod is not None
                and str(selected_mod.modtype or "").upper()
                in {"LAUNCHER", "BOOTSTRAP", "MCIL"}
            ):
                flash(
                    "Modloader and downloader packages cannot be listed as "
                    "advanced optionals.",
                    "error",
                )
                return redirect(url_for("asite.modpackbuild", id=id))
            try:
                integration_version = _selected_integration_version(
                    selected_version
                )
                if integration_version:
                    materialized = _materialize_integration_version(
                        mod_id, id, integration_version
                    )
                    selected_version = materialized.version.id
                added_dependencies = Modversion.add_modversion_to_selected_build(
                    selected_version,
                    mod_id,
                    id,
                    "0",
                    newoptional,
                )
                if list_in_advanced:
                    AdvancedOptional.set_modversion_listing(
                        id, selected_version
                    )
            except (
                AdvancedOptionalError,
                IncompatibleModVersionError,
                IntegrationError,
                MissingDependencyVersionError,
                ValueError,
            ) as error:
                flash(str(error), "error")
                return redirect(url_for("asite.modpackbuild", id=id))
            message = "added modversion to build"
            if added_dependencies:
                message += " with required dependencies: " + ", ".join(added_dependencies)
            flash(message, "success")
            return redirect(url_for("asite.modpackbuild", id=id))

    try:
        editor = Build_modversion.get_build_editor_data(id)
    except connector.ProgrammingError:
        flash("failed to build modpackbuild", "error")
        raise

    if editor is None:
        flash("unable to find build", "error")
        return redirect(url_for("asite.modpacklibrary"))

    if request.args.get("check_updates") == "1":
        update_memberships = set()
        for combo in editor.buildlist:
            compatible = combo.get("versions") or []
            if (
                compatible
                and int(compatible[0]["id"]) != int(combo["modverid"])
            ):
                update_memberships.add(int(combo["id"]))
                combo["available_version"] = compatible[0]["version"]

        integrated_mods = Build_modversion.get_integrated_mods(id)
        integration_errors = []
        latest_versions = {}
        if integrated_mods and User.get_permission_token(
            session["token"], "mods_manage"
        ) == 0:
            integration_errors.append(
                "Provider-managed mods need mod management permission."
            )
        else:
            latest_versions, integration_errors = _latest_provider_versions(
                integrated_mods,
                editor.packbuild,
                Session.get_user_id(session["token"]),
            )

        for combo in editor.buildlist:
            if not combo.get("integration_provider"):
                continue
            latest = latest_versions.get(int(combo["modid"]))
            if latest is None:
                continue
            membership_id = int(combo["id"])
            if str(combo.get("integration_version_id") or "") == str(
                latest.version_id
            ):
                update_memberships.discard(membership_id)
                combo.pop("available_version", None)
            else:
                update_memberships.add(membership_id)
                combo["available_version"] = latest.version_number

        for combo in editor.buildlist:
            combo["update_available"] = int(combo["id"]) in update_memberships

        if update_memberships:
            flash(
                f"{len(update_memberships)} mod update(s) available.",
                "success",
            )
        elif not integration_errors:
            flash("All checked mods are up to date.", "success")
        if integration_errors:
            flash("; ".join(integration_errors), "error")

    distribution_settings = DistributionSettings.get_all()
    export_requested = (
        request.method == "GET" and request.args.get("export") == "1"
    )
    modrinth_downloaders, modrinth_downloader_error = (), None
    server_downloaders, server_downloader_error = (), None
    prism_downloaders, prism_downloader_error = (), None
    curseforge_downloaders, curseforge_downloader_error = (), None
    publication_targets = ()
    publication_states = {}
    if export_requested and (
        distribution_settings[DistributionSettings.MRPACK]
        or distribution_settings[DistributionSettings.PRISM]
        or distribution_settings[DistributionSettings.SOLDERPY_LOADER]
    ):
        resolved_downloaders, resolved_error = _available_downloaders(
            editor.packbuild, distribution_settings, "modrinth"
        )
        if distribution_settings[DistributionSettings.MRPACK]:
            modrinth_downloaders = resolved_downloaders
            modrinth_downloader_error = resolved_error
        if distribution_settings[DistributionSettings.PRISM]:
            prism_downloaders = resolved_downloaders
            prism_downloader_error = resolved_error
        if distribution_settings[DistributionSettings.SOLDERPY_LOADER]:
            server_downloaders = tuple(
                downloader
                for downloader in resolved_downloaders
                if downloader.key == "solderpyloader"
            )
            server_downloader_error = resolved_error
            if not server_downloaders and server_downloader_error is None:
                server_downloader_error = (
                    "No compatible SolderPy Loader release was found for "
                    "this build."
                )
    if (
        export_requested
        and distribution_settings[DistributionSettings.CURSEFORGE]
    ):
        curseforge_downloaders, curseforge_downloader_error = (
            _available_downloaders(
                editor.packbuild, distribution_settings, "curseforge"
            )
        )
    if export_requested:
        publication_targets = tuple(
            target
            for target in PlatformPublishing.get_targets(
                Session.get_user_id(session["token"]),
                modpack_id,
                enabled_only=True,
            )
            if (
                target.provider == PUBLISH_MODRINTH
                and distribution_settings[DistributionSettings.MRPACK]
            )
            or (
                target.provider == PUBLISH_CURSEFORGE
                and distribution_settings[DistributionSettings.CURSEFORGE]
            )
        )
        if publication_targets:
            publication_states = (
                PlatformPublishing.get_build_publication_states(
                    publication_targets,
                    editor.packbuild.id,
                    editor.packbuild.version,
                    Session.get_user_id(session["token"]),
                )
            )

    return render_template(
        "modpackbuild.html",
        listmod=editor.listmod,
        packbuild=editor.packbuild,
        packbuildname=editor.packbuildname,
        listmodversions=editor.listmodversions,
        buildlist=editor.buildlist,
        java_runtime_options=MOJANG_JAVA_RUNTIME_OPTIONS,
        distribution_settings=distribution_settings,
        optional_mode=getattr(editor, "optional_mode", 0),
        export_requested=export_requested,
        modrinth_downloaders=modrinth_downloaders,
        modrinth_downloader_error=modrinth_downloader_error,
        server_downloaders=server_downloaders,
        server_downloader_error=server_downloader_error,
        prism_downloaders=prism_downloaders,
        prism_downloader_error=prism_downloader_error,
        curseforge_downloaders=curseforge_downloaders,
        curseforge_downloader_error=curseforge_downloader_error,
        publication_targets=publication_targets,
        publication_states=publication_states,
    )


@asite.route("/modpackbuild/<int:build_id>/optionals", methods=["GET", "POST"])
def advanced_optionals(build_id):
    if "token" not in session or not Session.verify_session(
        session["token"], request.remote_addr
    ):
        return redirect(url_for("alogin.login"))
    if User.get_permission_token(session["token"], "modpacks_manage") == 0:
        return redirect(url_for("asite.index"))

    modpack_id = Build.get_modpackid_by_id(build_id)
    if not modpack_id or not User_modpack.get_user_modpackpermission(
        session["token"], modpack_id
    ):
        return redirect(url_for("asite.modpacklibrary"))
    modpack = Modpack.get_by_id(modpack_id)
    if modpack is None:
        return redirect(url_for("asite.modpacklibrary"))

    if request.method == "POST":
        try:
            if "enable_technic_solderpy_loader" in request.form:
                if not DistributionSettings.is_enabled(
                    DistributionSettings.SOLDERPY_LOADER
                ):
                    raise TechnicSolderPyLoaderError(
                        "Enable SolderPy Loader in Settings first."
                    )
                build = Build.get_by_id(build_id)
                if build is None:
                    raise TechnicSolderPyLoaderError(
                        "The build no longer exists."
                    )
                selected = PlatformPackExport.resolve_downloader(
                    "solderpyloader:"
                    + str(request.form.get("solderpy_loader_version") or ""),
                    build,
                    "modrinth",
                )
                TechnicSolderPyLoader.configure(
                    build,
                    modpack,
                    selected,
                    UPLOAD_FOLDER,
                    public_repo_url,
                    app_url,
                    delivery_mode=request.form.get("technic_delivery_mode"),
                    r2_client=R2 if R2_BUCKET else None,
                    r2_bucket=R2_BUCKET,
                )
                flash("Technic delivery updated for this build.", "success")
            elif "disable_technic_solderpy_loader" in request.form:
                TechnicSolderPyLoader.disable(build_id)
                flash(
                    "SolderPy Loader disabled for this Technic build.",
                    "success",
                )
            elif "set_optional_mode" in request.form:
                AdvancedOptional.set_modpack_mode(
                    modpack_id, request.form.get("optional_mode", BASIC_MODE)
                )
                flash("Optional management mode updated.", "success")
            elif "create_optional_group" in request.form:
                if modpack.optional_mode != ADVANCED_MODE:
                    AdvancedOptional.set_modpack_mode(modpack_id, ADVANCED_MODE)
                AdvancedOptional.create_group(
                    build_id,
                    request.form.get("name"),
                    request.form.get("description"),
                    request.form.get("selection_type", 0),
                    request.form.get("sort_order", 0),
                )
                flash("Advanced optional group added.", "success")
            elif "save_optional_choice" in request.form:
                if modpack.optional_mode != ADVANCED_MODE:
                    raise AdvancedOptionalError(
                        "Enable advanced optionals before configuring choices."
                    )
                AdvancedOptional.save_membership(
                    build_id,
                    request.form.get("build_modversion_id"),
                    request.form.get("group_id"),
                    request.form.get("optional_state", 0),
                    "selected_by_default" in request.form,
                    request.form.get("sort_order", 0),
                )
                flash("Advanced optional choice updated.", "success")
            elif "delete_optional_group" in request.form:
                AdvancedOptional.delete_group(
                    build_id, request.form.get("group_id")
                )
                flash("Advanced optional group deleted.", "success")
        except (
            AdvancedOptionalError,
            DistributionExportError,
            IntegrationError,
            PlatformExportError,
            TechnicSolderPyLoaderError,
        ) as error:
            flash(str(error), "error")
        except Exception as error:
            ErrorPrinter.message(
                "failed to update Technic SolderPy Loader delivery", error
            )
            flash(
                "The Technic SolderPy Loader configuration could not be updated. "
                "Check the server log.",
                "error",
            )
        # Advanced choices can now change the public Technic manifest. Clear
        # only this process's short-lived read cache after any submitted edit.
        from api import clear_api_caches

        clear_api_caches()
        return redirect(
            url_for("asite.advanced_optionals", build_id=build_id)
        )

    editor = Build_modversion.get_build_editor_data(build_id)
    if editor is None:
        return redirect(url_for("asite.modpacklibrary"))
    groups = AdvancedOptional.get_groups(build_id)
    optional_items = {
        item.build_modversion_id: item
        for group in groups
        for item in group.items
    }
    if modpack.optional_mode == ADVANCED_MODE:
        listed_builds = [
            combo
            for combo in editor.buildlist
            if bool(combo.get("advanced_listed"))
        ]
    else:
        # Basic mode has no separate work list. Show the same legacy optional
        # entries represented by build_modversion.optional = 1.
        listed_builds = [
            combo
            for combo in editor.buildlist
            if int(combo.get("optional") or 0) == 1
        ]
    distribution_settings = DistributionSettings.get_all()
    technic_solderpy_loader = TechnicSolderPyLoader.get(build_id)
    technic_solderpy_loader_active = bool(
        technic_solderpy_loader
        and TechnicSolderPyLoader.get_active(build_id)
    )
    solderpy_loader_releases = ()
    solderpy_loader_error = None
    if request.args.get("solderpy_loader") == "configure":
        if not distribution_settings[DistributionSettings.SOLDERPY_LOADER]:
            solderpy_loader_error = (
                "Enable SolderPy Loader in Settings first."
            )
        else:
            spec = PlatformPackExport.downloader_spec("solderpyloader")
            try:
                available = PlatformPackExport.available_downloaders(
                    editor.packbuild,
                    "modrinth",
                    specs=(spec,),
                )
                if available:
                    solderpy_loader_releases = available[0].releases
                else:
                    solderpy_loader_error = (
                        "No compatible SolderPy Loader release was found for "
                        "this Minecraft and modloader version."
                    )
            except PlatformExportError as error:
                solderpy_loader_error = str(error)
    return render_template(
        "advanced_optionals.html",
        packbuild=editor.packbuild,
        packbuildname=editor.packbuildname,
        buildlist=listed_builds,
        modpack=modpack,
        groups=groups,
        optional_items=optional_items,
        distribution_settings=distribution_settings,
        technic_solderpy_loader=technic_solderpy_loader,
        technic_solderpy_loader_active=technic_solderpy_loader_active,
        solderpy_loader_releases=solderpy_loader_releases,
        solderpy_loader_error=solderpy_loader_error,
        configure_solderpy_loader=(
            request.args.get("solderpy_loader") == "configure"
        ),
    )


@asite.route(
    "/modpackbuild/<int:build_id>/integration-versions/<int:mod_id>",
    methods=["GET"],
)
def integration_versions(build_id, mod_id):
    if "token" not in session or not Session.verify_session(
        session["token"], request.remote_addr
    ):
        return jsonify({"error": "Authentication required."}), 401
    if (
        User.get_permission_token(session["token"], "modpacks_manage") == 0
        or User.get_permission_token(session["token"], "mods_manage") == 0
    ):
        return jsonify({"error": "Permission denied."}), 403

    modpack_id = Build.get_modpackid_by_id(build_id)
    if not modpack_id or not User_modpack.get_user_modpackpermission(
        session["token"], modpack_id
    ):
        return jsonify({"error": "Permission denied."}), 403

    mod = Mod.get_by_id(mod_id)
    build = Build.get_by_id(build_id)
    if mod is None or build is None:
        return jsonify({"error": "The selected mod or build was not found."}), 404
    try:
        versions = ModIntegration.list_versions(
            mod,
            build,
            Session.get_user_id(session["token"]),
        )
        imported_ids = Modversion.get_integration_version_ids(mod.id)
        return jsonify(
            {
                "versions": [
                    version.management_json()
                    for version in versions
                    if version.version_id not in imported_ids
                ]
            }
        )
    except IntegrationError as error:
        ErrorPrinter.message("failed to list compatible provider versions", error)
        return jsonify({"error": "Compatible provider versions could not be loaded."}), 400


@asite.route("/modpackbuild/<int:id>/mcinstance", methods=["GET"])
def export_mcinstance(id):
    if not DistributionSettings.is_enabled(DistributionSettings.MCIL):
        return render_template("404.html", error="Not Found"), 404

    if "token" not in session or not Session.verify_session(
        session["token"], request.remote_addr
    ):
        return redirect(url_for("alogin.login"))

    if User.get_permission_token(session["token"], "modpacks_manage") == 0:
        return redirect(url_for("asite.modpacklibrary"))

    modpack_id = Build.get_modpackid_by_id(id)
    if not modpack_id or not User_modpack.get_user_modpackpermission(
        session["token"], modpack_id
    ):
        return redirect(url_for("asite.modpacklibrary"))

    try:
        build, packages = MCInstanceExport.load(id)
        build = PlatformPackExport.override_modloader_version(
            build, request.values.get("forge_version")
        )
        source_mode = PlatformPackExport.source_mode(
            request.args.get("source"), default="solder"
        )
        native_files = (
            PlatformPackExport.native_modrinth_files(build, packages)
            if source_mode == "hybrid"
            else {}
        )
        archive = MCInstanceExport.render(
            build,
            packages,
            public_repo_url,
            UPLOAD_FOLDER,
            native_files=native_files,
            optional_groups=AdvancedOptional.get_active_groups_for_packages(
                id, packages
            ),
        )
    except (MCInstanceExportError, PlatformExportError) as error:
        flash(str(error), "error")
        return redirect(url_for("asite.modpack", id=modpack_id))

    filename = secure_filename(f"{build.modpack_slug}-{build.version}.mcinstance")
    return send_file(
        archive,
        mimetype="application/zip",
        as_attachment=True,
        download_name=filename,
    )


def _platform_export_build(id):
    """Authorize and load a build used by a management-side pack export."""
    if "token" not in session or not Session.verify_session(
        session["token"], request.remote_addr
    ):
        return None, redirect(url_for("alogin.login"))
    if User.get_permission_token(session["token"], "modpacks_manage") == 0:
        return None, redirect(url_for("asite.modpacklibrary"))

    modpack_id = Build.get_modpackid_by_id(id)
    if not modpack_id or not User_modpack.get_user_modpackpermission(
        session["token"], modpack_id
    ):
        return None, redirect(url_for("asite.modpacklibrary"))
    try:
        build, packages = MCInstanceExport.load(id)
        build = PlatformPackExport.override_modloader_version(
            build, request.args.get("forge_version")
        )
        return (build, packages), None
    except (MCInstanceExportError, PlatformExportError) as error:
        flash(str(error), "error")
        return None, redirect(url_for("asite.modpackbuild", id=id))


def _hosted_export_allowed(build):
    if build.is_published and not build.private:
        return True
    flash("Hosted configs require a published, non-private build.", "error")
    return False


def _render_native_platform_archive(
    provider, build, packages, build_id, *, version_override=None
):
    """Render the same archive used by downloads and direct publishing."""
    selector = request.values.get("selector")
    if version_override is not None:
        # Patch publication changes the native platform version number, not the
        # Solder build that a hosted downloader follows.
        if not selector or selector == "build":
            selector = str(build.version)
        build = copy(build)
        build.version = str(version_override)
    if provider == PUBLISH_MODRINTH:
        downloader = request.values.get("modrinth_downloader") or request.values.get(
            "downloader"
        )
        if not _downloader_export_enabled(downloader):
            return None
        archive = PlatformPackExport.render_mrpack(
            build,
            packages,
            downloader,
            public_repo_url,
            UPLOAD_FOLDER,
            app_url,
            source_mode=request.values.get("source"),
            delivery=request.values.get("delivery"),
            selector=selector,
            export_overrides=PlatformExportOverride.get_enabled(),
            optional_groups=AdvancedOptional.get_active_groups_for_packages(
                build_id, packages
            ),
        )
        return (
            archive,
            secure_filename(f"{build.modpack_slug}-{build.version}.mrpack"),
            "application/x-modrinth-modpack+zip",
        )

    if provider == PUBLISH_CURSEFORGE:
        downloader = request.values.get("curseforge_downloader") or request.values.get(
            "downloader"
        )
        if not _downloader_export_enabled(downloader):
            return None
        archive = PlatformPackExport.render_curseforge(
            build,
            packages,
            downloader,
            public_repo_url,
            UPLOAD_FOLDER,
            app_url,
            curseforge_api_key=curseforge_api_key,
            source_mode=request.values.get("source"),
            delivery=request.values.get("delivery"),
            selector=selector,
            export_overrides=PlatformExportOverride.get_enabled(),
            optional_groups=AdvancedOptional.get_active_groups_for_packages(
                build_id, packages
            ),
        )
        return (
            archive,
            secure_filename(
                f"{build.modpack_slug}-{build.version}-curseforge.zip"
            ),
            "application/zip",
        )

    raise PublishingError("The selected publishing provider is not supported.")


@asite.route("/modpackbuild/<int:id>/solderpy-loader", methods=["GET"])
def export_solderpy_loader(id):
    if not DistributionSettings.is_enabled(
        DistributionSettings.SOLDERPY_LOADER
    ):
        return render_template("404.html", error="Not Found"), 404

    loaded, failure = _platform_export_build(id)
    if failure is not None:
        return failure
    build, _packages = loaded
    try:
        archive = PlatformPackExport.render_solderpy_loader(
            build,
            app_url,
            selector=request.args.get("selector"),
        )
    except PlatformExportError as error:
        flash(str(error), "error")
        return redirect(url_for("asite.modpackbuild", id=id))

    filename = secure_filename(
        f"{build.modpack_slug}-{build.version}-solderpy-loader.zip"
    )
    return send_file(
        archive,
        mimetype="application/zip",
        as_attachment=True,
        download_name=filename,
    )


@asite.route("/modpackbuild/<int:id>/server", methods=["GET"])
def export_server(id):
    if not DistributionSettings.is_enabled(
        DistributionSettings.SOLDERPY_LOADER
    ):
        return render_template("404.html", error="Not Found"), 404

    loaded, failure = _platform_export_build(id)
    if failure is not None:
        return failure
    build, packages = loaded
    downloader = request.args.get("server_downloader")
    if not _downloader_export_enabled(downloader):
        return redirect(url_for("asite.modpackbuild", id=id))
    try:
        archive = PlatformPackExport.render_server(
            build,
            packages,
            downloader,
            public_repo_url,
            UPLOAD_FOLDER,
            app_url,
            selector=request.args.get("selector"),
        )
    except PlatformExportError as error:
        flash(str(error), "error")
        return redirect(url_for("asite.modpackbuild", id=id))

    filename = secure_filename(
        f"{build.modpack_slug}-{build.version}-server.zip"
    )
    return send_file(
        archive,
        mimetype="application/zip",
        as_attachment=True,
        download_name=filename,
    )


@asite.route("/modpackbuild/<int:id>/prism", methods=["GET"])
def export_prism(id):
    if not DistributionSettings.is_enabled(DistributionSettings.PRISM):
        return render_template("404.html", error="Not Found"), 404

    loaded, failure = _platform_export_build(id)
    if failure is not None:
        return failure
    build, packages = loaded
    downloader = request.args.get("prism_downloader")
    if not _downloader_export_enabled(downloader):
        return redirect(url_for("asite.modpackbuild", id=id))
    try:
        archive = PlatformPackExport.render_prism(
            build,
            packages,
            public_repo_url,
            UPLOAD_FOLDER,
            downloader=downloader,
            application_url=app_url,
            source_mode=request.args.get("source"),
            delivery=request.args.get("delivery"),
            selector=request.args.get("selector"),
            optional_groups=AdvancedOptional.get_active_groups_for_packages(
                id, packages
            ),
        )
    except PlatformExportError as error:
        flash(str(error), "error")
        return redirect(url_for("asite.modpackbuild", id=id))

    filename = secure_filename(
        f"{build.modpack_slug}-{build.version}-prism.zip"
    )
    return send_file(
        archive,
        mimetype="application/zip",
        as_attachment=True,
        download_name=filename,
    )


@asite.route("/modpackbuild/<int:id>/packwiz", methods=["GET"])
def export_packwiz(id):
    if not DistributionSettings.is_enabled(DistributionSettings.PACKWIZ):
        return render_template("404.html", error="Not Found"), 404

    loaded, failure = _platform_export_build(id)
    if failure is not None:
        return failure
    build, packages = loaded
    try:
        source_mode = PlatformPackExport.source_mode(request.args.get("source"))
        delivery = PlatformPackExport.delivery_mode(request.args.get("delivery"))
        if delivery == "hosted":
            if not _hosted_export_allowed(build):
                return redirect(url_for("asite.modpackbuild", id=id))
            selector = PlatformPackExport.hosted_selector(
                build, request.args.get("selector")
            )
            route_values = {
                "pack_slug": build.modpack_slug,
                "selector": selector,
                "source": source_mode,
            }
            if "forge_version" in request.args:
                route_values["forge_version"] = request.args.get("forge_version")
            return redirect(
                url_for("distribution_api.packwiz_pack", **route_values)
            )
        archive = PlatformPackExport.render_packwiz(
            build,
            packages,
            public_repo_url,
            source_mode=source_mode,
        )
    except PlatformExportError as error:
        flash(str(error), "error")
        return redirect(url_for("asite.modpackbuild", id=id))

    filename = secure_filename(f"{build.modpack_slug}-{build.version}-packwiz.zip")
    return send_file(
        archive,
        mimetype="application/zip",
        as_attachment=True,
        download_name=filename,
    )


@asite.route("/modpackbuild/<int:id>/filedirector", methods=["GET"])
def export_filedirector(id):
    if not DistributionSettings.is_enabled(DistributionSettings.FILEDIRECTOR):
        return render_template("404.html", error="Not Found"), 404

    loaded, failure = _platform_export_build(id)
    if failure is not None:
        return failure
    build, packages = loaded
    try:
        source_mode = PlatformPackExport.source_mode(request.args.get("source"))
        delivery = PlatformPackExport.delivery_mode(request.args.get("delivery"))
        if delivery == "hosted":
            if not _hosted_export_allowed(build):
                return redirect(url_for("asite.modpackbuild", id=id))
            selector = PlatformPackExport.hosted_selector(
                build, request.args.get("selector")
            )
            return redirect(
                url_for(
                    "distribution_api.filedirector_remote",
                    pack_slug=build.modpack_slug,
                    selector=selector,
                    bundle_name="mods",
                    source=source_mode,
                )
            )
        archive = PlatformPackExport.render_filedirector(
            build,
            packages,
            public_repo_url,
            source_mode=source_mode,
            optional_groups=AdvancedOptional.get_active_groups_for_packages(
                id, packages
            ),
        )
    except PlatformExportError as error:
        flash(str(error), "error")
        return redirect(url_for("asite.modpackbuild", id=id))

    filename = secure_filename(
        f"{build.modpack_slug}-{build.version}-filedirector.zip"
    )
    return send_file(
        archive,
        mimetype="application/zip",
        as_attachment=True,
        download_name=filename,
    )


@asite.route("/modpackbuild/<int:id>/modpackdirector", methods=["GET"])
def export_modpack_director(id):
    if not DistributionSettings.is_enabled(
        DistributionSettings.MODPACK_DIRECTOR
    ):
        return render_template("404.html", error="Not Found"), 404

    loaded, failure = _platform_export_build(id)
    if failure is not None:
        return failure
    build, packages = loaded
    try:
        source_mode = PlatformPackExport.source_mode(request.args.get("source"))
        delivery = PlatformPackExport.delivery_mode(request.args.get("delivery"))
        selector = request.args.get("selector")
        if delivery == "hosted" and not _hosted_export_allowed(build):
            return redirect(url_for("asite.modpackbuild", id=id))
        archive = PlatformPackExport.render_modpack_director(
            build,
            packages,
            public_repo_url,
            app_url,
            source_mode=source_mode,
            delivery=delivery,
            selector=selector,
            optional_groups=AdvancedOptional.get_active_groups_for_packages(
                id, packages
            ),
        )
    except PlatformExportError as error:
        flash(str(error), "error")
        return redirect(url_for("asite.modpackbuild", id=id))

    filename = secure_filename(
        f"{build.modpack_slug}-{build.version}-modpack-director.zip"
    )
    return send_file(
        archive,
        mimetype="application/zip",
        as_attachment=True,
        download_name=filename,
    )


@asite.route("/modpackbuild/<int:id>/mrpack", methods=["GET"])
def export_mrpack(id):
    if not DistributionSettings.is_enabled(DistributionSettings.MRPACK):
        return render_template("404.html", error="Not Found"), 404

    loaded, failure = _platform_export_build(id)
    if failure is not None:
        return failure
    build, packages = loaded
    try:
        rendered = _render_native_platform_archive(
            PUBLISH_MODRINTH, build, packages, id
        )
    except PlatformExportError as error:
        flash(str(error), "error")
        return redirect(url_for("asite.modpackbuild", id=id))
    if rendered is None:
        return redirect(url_for("asite.modpackbuild", id=id))
    archive, filename, mimetype = rendered

    return send_file(
        archive,
        mimetype=mimetype,
        as_attachment=True,
        download_name=filename,
    )


@asite.route("/modpackbuild/<int:id>/curseforge", methods=["GET"])
def export_curseforge(id):
    if not DistributionSettings.is_enabled(DistributionSettings.CURSEFORGE):
        return render_template("404.html", error="Not Found"), 404

    loaded, failure = _platform_export_build(id)
    if failure is not None:
        return failure
    build, packages = loaded
    try:
        rendered = _render_native_platform_archive(
            PUBLISH_CURSEFORGE, build, packages, id
        )
    except PlatformExportError as error:
        flash(str(error), "error")
        return redirect(url_for("asite.modpackbuild", id=id))
    if rendered is None:
        return redirect(url_for("asite.modpackbuild", id=id))
    archive, filename, mimetype = rendered

    return send_file(
        archive,
        mimetype=mimetype,
        as_attachment=True,
        download_name=filename,
    )


@asite.route(
    "/modpackbuild/<int:id>/publish/<int:target_id>", methods=["POST"]
)
def publish_build(id, target_id):
    loaded, failure = _platform_export_build(id)
    if failure is not None:
        return failure
    build, packages = loaded
    user_id = Session.get_user_id(session["token"])
    targets = PlatformPublishing.get_targets(
        user_id, build.modpack_id, enabled_only=True
    )
    target = next((item for item in targets if item.id == target_id), None)
    if target is None:
        flash("The selected publication target is not enabled.", "error")
        return redirect(url_for("asite.modpackbuild", id=id))

    setting = (
        DistributionSettings.MRPACK
        if target.provider == PUBLISH_MODRINTH
        else DistributionSettings.CURSEFORGE
    )
    if not DistributionSettings.is_enabled(setting):
        flash(
            f"Enable {target.provider_label} exports before publishing.",
            "error",
        )
        return redirect(url_for("asite.modpackbuild", id=id))

    try:
        publication_state = PlatformPublishing.get_build_publication_states(
            (target,), build.id, build.version, user_id
        )[target.id]
        if publication_state.locked:
            raise PublishingError(
                "This build already has a publishing attempt in progress or "
                "with an unknown result. Check the provider before allowing "
                "a retry."
            )
        rendered = _render_native_platform_archive(
            target.provider,
            build,
            packages,
            id,
            version_override=publication_state.version_number,
        )
        if rendered is None:
            return redirect(url_for("asite.modpackbuild", id=id))
        archive, filename, _mimetype = rendered
        try:
            result = PlatformPublishing.publish(
                target.id,
                build.modpack_id,
                build,
                archive,
                filename,
                request.form.get("release_type"),
                request.form.get("changelog"),
                user_id,
                expected_version=publication_state.version_number,
            )
        finally:
            archive.close()
    except (PlatformExportError, PublishingError) as error:
        flash(str(error), "error")
        return redirect(url_for("asite.modpackbuild", id=id, export=1))
    except Exception as error:
        ErrorPrinter.message("Unable to publish modpack build", error)
        flash("The modpack could not be published.", "error")
        return redirect(url_for("asite.modpackbuild", id=id, export=1))

    remote_reference = (
        f" as remote version {result.remote_file_id}"
        if result.remote_file_id
        else ""
    )
    flash(
        f"Published {result.version_number} to {target.provider_label}"
        f"{remote_reference}.",
        "success",
    )
    return redirect(url_for("asite.modpackbuild", id=id, export=1))


@asite.route("/modpackbuild/<int:id>/csv", methods=["GET"])
def export_build_csv(id):
    if "token" not in session or not Session.verify_session(
        session["token"], request.remote_addr
    ):
        return redirect(url_for("alogin.login"))
    if User.get_permission_token(session["token"], "modpacks_manage") == 0:
        return redirect(url_for("asite.modpacklibrary"))

    modpack_id = Build.get_modpackid_by_id(id)
    if not modpack_id or not User_modpack.get_user_modpackpermission(
        session["token"], modpack_id
    ):
        return redirect(url_for("asite.modpacklibrary"))

    try:
        build, rows = BuildCsvExport.load(id)
    except BuildExportError as error:
        flash(str(error), "error")
        return redirect(url_for("asite.modpack", id=modpack_id))

    filename = secure_filename(f"{build.modpack_slug}_{build.version}.csv")
    return send_file(
        BuildCsvExport.render(rows),
        mimetype="text/csv",
        as_attachment=True,
        download_name=filename,
    )


@asite.route("/modlibrary", methods=["GET"])
def modlibrary():
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for('alogin.login'))
    
    if User.get_permission_token(session["token"], "mods_manage") == 0:
                return _redirect_back()

    try:
        mods = Mod.get_all()
    except connector.ProgrammingError as e:
        Database.create_tables()
        mods = []
        flash("error when building mod list", "error")

    return render_template("modlibrary.html", mods=mods)


@asite.route("/modlibrary", methods=["POST", "PUT"])
def modlibrary_post():
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for('alogin.login'))
    
    if User.get_permission_token(session["token"], "mods_manage") == 0:
                return _redirect_back()

    if "form-submit" in request.form:
        markedbuild = "0"
        if "markedbuild" in request.form:
            if User.get_permission_token(session["token"], "modpacks_manage") == 0:
                return _redirect_back()
            markedbuild = Build.get_marked_build(
                Session.get_user_id(session["token"])
            )
            if not markedbuild or User_modpack.get_user_modpackpermission(
                session["token"], Build.get_modpackid_by_id(markedbuild)
            ) == False:
                return _redirect_back()
        if 'file' not in request.files:
            print('No file part')
            return redirect(url_for('asite.modlibrary'))
        filew = request.files['file']
        if filew.filename == '':
            print('No selected file')
            return redirect(url_for('asite.modlibrary'))
        if filew and allowed_file(filew.filename):
            mod = Mod.get_by_id(request.form.get("modid"))
            mod_name = request.form.get("mod", "")
            if mod is None or mod.name != mod_name:
                flash("The selected mod is invalid.", "error")
                return redirect(url_for("asite.modlibrary"))
            if mod.integration_provider:
                flash(
                    "Provider-managed mods import versions from the build editor.",
                    "error",
                )
                return redirect(url_for("asite.modlibrary"))
            try:
                version_minecraft, _minecraft_versions = (
                    minecraft_version_storage(request.form["mcversion"])
                )
                if version_minecraft is None:
                    raise ValueError("Enter at least one Minecraft version.")
            except ValueError as error:
                flash(str(error), "error")
                return redirect(url_for("asite.modlibrary"))
            version = version_minecraft + "-" + request.form["version"]
            safe_mod_name = secure_filename(mod.name)
            safe_version = secure_filename(version)
            if (
                not safe_mod_name
                or safe_mod_name != mod.name
                or not safe_version
                or safe_version != version
            ):
                flash("The mod slug or version contains unsafe filename characters.", "error")
                return redirect(url_for("asite.modlibrary"))

            # Build every filesystem path from secure_filename output and
            # verify the resolved mod folder remains below the repository root.
            # The equality checks above intentionally reject, rather than
            # silently rename, unsafe database slugs and submitted versions.
            filename = f"{safe_mod_name}-{safe_version}.zip"
            jarfilename = f"{safe_mod_name}-{safe_version}.jar"
            repository_root = Path(UPLOAD_FOLDER).resolve()
            destination_folder = (repository_root / safe_mod_name).resolve()
            try:
                destination_folder.relative_to(repository_root)
            except ValueError:
                flash("The selected mod has an unsafe repository path.", "error")
                return redirect(url_for("asite.modlibrary"))
            destination_folder.mkdir(parents=True, exist_ok=True)
            jarmd5 = request.form.get("jarmd5", "0").strip() or "0"

            try:
                with tempfile.TemporaryDirectory(
                    prefix=".solder-upload-", dir=destination_folder
                ) as staging_directory:
                    staged_zip = Path(staging_directory, filename)
                    filew.save(staged_zip)
                    verified_md5 = Mod.verify_file_md5(
                        staged_zip, request.form.get("md5"), "the Solder ZIP"
                    )
                    staged_jar = None
                    actual_jarfilesize = None
                    if jarmd5 != "0":
                        Mod.extract_jar_from_zip(
                            staged_zip,
                            output_name=jarfilename,
                            expected_md5=jarmd5,
                            allow_any_jar=(
                                str(getattr(mod, "modtype", "MOD")).upper()
                                == "LAUNCHER"
                            ),
                        )
                        staged_jar = Path(staging_directory, jarfilename)
                        actual_jarfilesize = staged_jar.stat().st_size

                    actual_filesize = staged_zip.stat().st_size
                    final_zip = destination_folder / filename
                    final_jar = (
                        destination_folder / jarfilename
                        if staged_jar is not None
                        else None
                    )
                    if final_zip.exists() or (
                        final_jar is not None and final_jar.exists()
                    ):
                        raise ValueError(
                            "A repository file for this version already exists."
                        )

                    moved_files = []
                    try:
                        os.replace(staged_zip, final_zip)
                        moved_files.append(final_zip)
                        if staged_jar is not None:
                            os.replace(staged_jar, final_jar)
                            moved_files.append(final_jar)
                        # Publish the database row only after every local
                        # artifact is in its final location. Roll back those
                        # artifacts if the database transaction fails.
                        Modversion.new(
                            request.form["modid"],
                            version,
                            request.form["mcversion"],
                            verified_md5,
                            actual_filesize,
                            markedbuild,
                            "0",
                            jarmd5.lower(),
                            modloader=request.form.getlist("modloader"),
                            jarfilesize=actual_jarfilesize,
                        )
                    except Exception:
                        for moved_file in reversed(moved_files):
                            moved_file.unlink(missing_ok=True)
                        raise
            except (
                IncompatibleModVersionError,
                InvalidModloaderError,
                MissingDependencyVersionError,
                UploadVerificationError,
                ValueError,
            ) as error:
                flash(str(error), "error")
                return redirect(url_for("asite.modlibrary"))

            if R2_BUCKET:
                keyname = "mods/" + safe_mod_name + "/" + filename
                try:
                    R2.upload_file(str(final_zip), R2_BUCKET, keyname, ExtraArgs={'ContentType': 'application/zip'})
                except Exception as e:
                    ErrorPrinter.message("failed to upload zipfile to buckets", e)
                    flash("failed to upload zipfile to bucket", "error")
            if jarmd5 != "0":
                if R2_BUCKET:
                    jarkeyname = "mods/" + safe_mod_name + "/" + jarfilename
                    try:
                        R2.upload_file(str(final_jar), R2_BUCKET, jarkeyname, ExtraArgs={'ContentType': 'application/jar'})
                    except Exception as e:
                        ErrorPrinter.message("failed to upload jarfile to buckets", e)
                        flash("failed to upload jarfile to bucket", "error")
            flash("added modversion", "success")
            return redirect(url_for('asite.modlibrary'))

    return redirect(url_for('asite.modlibrary'))


@asite.route("/modpacklibrary", methods=["GET"])
def modpacklibrary():
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for('alogin.login'))
    
    if User.get_permission_token(session["token"], "modpacks_manage") == 0:
                return _redirect_back()

    try:
        modpacklibrary = Modpack.get_all_for_user(
            Session.get_user_id(session["token"])
        )
    except connector.ProgrammingError as e:
        Database.create_tables()
        modpacklibrary = []
        flash("error when getting modpacklist", "error")

    return render_template("modpacklibrary.html", modpacklibrary=modpacklibrary)


@asite.route("/modpacklibrary", methods=["POST"])
def modpacklibrary_post():
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for('alogin.login'))
    
    if User.get_permission_token(session["token"], "modpacks_manage") == 0:
                return _redirect_back()

    if request.method == "POST":
        if "form-submit" in request.form:
            if User.get_permission_token(session["token"], "modpacks_create") == 0:
                return _redirect_back()
            hidden = "0"
            private = "0"
            if "hidden" in request.form:
                hidden = request.form['hidden']
            if "private" in request.form:
                private = request.form['private']
            Modpack.new(
                request.form["pretty_name"],
                request.form["name"],
                hidden,
                private,
                Session.get_user_id(session["token"]),
            )
            flash("added modpack", "success")
            return redirect(url_for('asite.modpacklibrary'))
        if User_modpack.get_user_modpackpermission(
            session["token"], request.form["modid"]
        ) == False:
            return _redirect_back()
        if "hidden_submit" in request.form:
            common.update_checkbox(request.form["modid"], request.form["check"], "hidden", "modpacks")
            flash("updated modpack", "success")
        if "private_submit" in request.form:
            common.update_checkbox(request.form["modid"], request.form["check"], "private", "modpacks")
            flash("updated modpack", "success")
        if "pinned_submit" in request.form:
            common.update_checkbox(request.form["modid"], request.form["check"], "pinned", "modpacks")
            flash("updated modpack", "success")
        if "optional_submit" in request.form:
            common.update_checkbox(request.form["modid"], request.form["check"], "enable_optionals", "modpacks")
            flash("updated modpack", "success")
        if "server_submit" in request.form:
            common.update_checkbox(request.form["modid"], request.form["check"], "enable_server", "modpacks")
            flash("updated modpack", "success")

    return redirect(url_for('asite.modpacklibrary'))


@asite.route("/clients/<id>", methods=["GET", "POST"])
def clients(id):
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for('alogin.login'))
    
    if User.get_permission_token(session["token"], "solder_clients") == 0:
        return _redirect_back()

    try:
        packs = Client_modpack.get_all_client_modpacks(id)
    except connector.ProgrammingError as e:
        Database.create_tables()
        packs = []
        flash("error when getting clients", "error")

    if request.method == "POST":
        if "form-submit" in request.form:
            if "modpack" not in request.form:
                return redirect(url_for("asite.clients", id=id))
            Client_modpack.new(id, request.form["modpack"])
            flash("added client", "success")
            return redirect(url_for("asite.clients", id=id))
        if "form2-submit" in request.form:
            if "delete_id" not in request.form:
                return redirect(url_for("asite.clients", id=id))
            Client_modpack.delete_client_modpack(request.form["delete_id"])
            flash("deleted client", "success")
            return redirect(url_for("asite.clients", id=id))

    try:
        modpacklibrary = Modpack.get_all()
    except connector.ProgrammingError as e:
        flash("error when getting modpack list", "error")
        Database.create_tables()
        modpacklibrary = []

    return render_template("clients.html", clients=packs, modpacklibrary=modpacklibrary)

@asite.route("/user/<int:id>", methods=["GET", "POST"])
def user(id):
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for('alogin.login'))
    
    if User.get_permission_token(session["token"], "solder_users") == 0:
        return _redirect_back()

    managed_user = User.get_by_id(id)
    if managed_user is None:
        return render_template("404.html", error="User not found"), 404
    
    try:
        packs = User_modpack.get_all_user_modpacks(id)
    except connector.ProgrammingError as e:
        Database.create_tables()
        packs = []
        flash("error when getting user modpack list", "error")
        
    user_perms = User_modpack.get_user_permission(id)
        
    if request.method == "POST":
        if "appearance-submit" in request.form:
            User.set_night_mode(
                id,
                "night_mode" in request.form,
                request.remote_addr,
                Session.get_user_id(session["token"]),
            )
            flash("updated user appearance", "success")
            return redirect(url_for("asite.user", id=id))
        if "form-submit" in request.form:
            if "modpack" not in request.form:
                return redirect(url_for("asite.user", id=id))
            User_modpack.new(id, request.form["modpack"])
            return redirect(url_for("asite.user", id=id))
        if "form2-submit" in request.form:
            if "delete_id" not in request.form:
                return redirect(url_for("asite.user", id=id))
            User_modpack.delete_user_modpack(request.form["delete_id"])
            return redirect(url_for("asite.user", id=id))
        if "perm-submit" in request.form:
            solder_full = "0"
            solder_users = "0"
            solder_keys = "0"
            solder_clients = "0"
            solder_env = "0"
            mods_create = "0"
            mods_manage = "0"
            mods_delete = "0"
            modpacks_create = "0"
            modpacks_manage = "0"
            modpacks_delete = "0"
            if "solder_full" in request.form:
                solder_full = request.form['solder_full']
            if "solder_users" in request.form:
                solder_users = request.form['solder_users']
            if "solder_keys" in request.form:
                solder_keys = request.form['solder_keys']
            if "solder_clients" in request.form:
                solder_clients = request.form['solder_clients']
            if "solder_env" in request.form:
                solder_env = request.form['solder_env']
            if "mods_create" in request.form:
                mods_create = request.form['mods_create']
            if "mods_manage" in request.form:
                mods_manage = request.form['mods_manage']
            if "mods_delete" in request.form:
                mods_delete = request.form['mods_delete']
            if "modpacks_create" in request.form:
                modpacks_create = request.form['modpacks_create']
            if "modpacks_manage" in request.form:
                modpacks_manage = request.form['modpacks_manage']
            if "modpacks_delete" in request.form:
                modpacks_delete = request.form['modpacks_delete']
            User_modpack.update_userpermissions(id, solder_full, solder_users, solder_keys, solder_clients, solder_env, mods_create, mods_manage, mods_delete, modpacks_create, modpacks_manage, modpacks_delete)
            return redirect(url_for("asite.user", id=id))
    
    try:
        modpacklibrary = Modpack.get_all()
    except connector.ProgrammingError as e:
        flash("error when getting modpack list", "error")
        Database.create_tables()
        modpacklibrary = []

    return render_template("user.html", userpacks=packs, modpacklibrary=modpacklibrary, user_perms=user_perms, managed_user=managed_user)

