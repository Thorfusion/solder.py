import os
from pathlib import Path
import tempfile
import threading
import boto3
import requests

from api import solderpy_version
from flask import Blueprint, app, flash, jsonify, redirect, render_template, request, send_file, session, url_for
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
from models.compatibility import InvalidModloaderError
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
from models.session import Session
from models.user import User
from mysql import connector
from werkzeug.utils import secure_filename
from models.common import api_only, app_url, public_repo_url, debug, host, port, md5_repo_url, R2_URL, db_name, R2_BUCKET, new_user, migratetechnic, solderpy_version, R2_REGION, R2_ENDPOINT, R2_ACCESS_KEY, R2_SECRET_KEY, UPLOAD_FOLDER, common, DB_IS_UP, cache_size, cache_ttl, write_api, curseforge_api_key
from models.user_modpack import User_modpack
from models.errorPrinter import ErrorPrinter

__version__ = solderpy_version

asite = Blueprint("asite", __name__)

if DB_IS_UP == 1 and not api_only:
    Session.start_session_loop()


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

def createFolder(dirName):
    os.makedirs(dirName, exist_ok=True)


def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@asite.context_processor
def inject_menu():
    
    markedbuildid2 = Build.get_marked_build()
    pinnedmodpacks = Modpack.get_by_pinned()
    
    return dict(
        markedbuildid2=markedbuildid2,
        solderversion=solderpy_version,
        pinnedmodpacks=pinnedmodpacks,
        write_api_enabled=write_api,
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


@asite.route("/logout")
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
        return redirect(request.referrer)

    mod = Mod.get_by_id(id)
    upstream_versions = []
    upstream_error = None

    try:
        modversions = mod.get_versions()
        for version in modversions:
            version["mcil_ready"] = MCInstanceJar.is_ready(version.get("jarmd5"))
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
    )


@asite.route("/modversion/<id>", methods=["POST"])
def newmodversion(id):
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for('alogin.login'))
    
    if User.get_permission_token(session["token"], "mods_manage") == 0:
                return redirect(request.referrer)

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
    if "createmciljar_submit" in request.form:
        version_id = request.form.get("createmciljar_id", "").strip()
        mod = Mod.get_by_id(id)
        version = Modversion.get_by_id(version_id) if version_id else None
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
            ErrorPrinter.message("failed to create MCInstanceLoader JAR", error)
            flash("Failed to store the MCInstanceLoader JAR.", "error")
        else:
            flash(
                f"Created and verified the MCInstanceLoader JAR ({jar_md5}).",
                "success",
            )
        return redirect(url_for("asite.modversion", id=id))
    if "deleteversion_submit" in request.form:
        if User.get_permission_token(session["token"], "mods_delete") == 0:
                return redirect(request.referrer)
        if "delete_id" not in request.form:
            return redirect(url_for("asite.modversion", id=id))
        Modversion.delete_modversion(request.form["delete_id"])
        flash("deleted " + id, "success")
        return redirect(url_for("asite.modversion", id=id))
    if "addtoselbuild_submit" in request.form:
        if User.get_permission_token(session["token"], "modpacks_manage") == 0:
                return redirect(request.referrer)
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
                return redirect(request.referrer)
        if "mod_delete_id" not in request.form:
            return redirect(url_for("asite.modversion", id=id))
        Mod.delete_mod(request.form["mod_delete_id"])
        flash("deleted mod" + id, "success")
        return redirect(url_for('asite.modlibrary'))
    if "rehash_submit" in request.form:
        if "rehash_id" not in request.form:
            return redirect(url_for('asite.clientlibrary'))

        mod = Mod.get_by_id(id)
        version = Modversion.get_by_id(request.form["rehash_id"])
        if (
            mod is None
            or version is None
            or int(version.mod_id) != int(mod.id)
        ):
            flash("the selected mod version no longer exists", "error")
            return redirect(url_for("asite.modversion", id=id))
        try:
            if request.form["rehash_md5"] != "":
                version.update_hash(
                    request.form["rehash_md5"], md5_repo_url, mod.name
                )
            else:
                t = threading.Thread(
                    target=version.rehash,
                    args=(md5_repo_url, mod.name),
                )
                t.start()
        except (OSError, requests.RequestException, ValueError) as error:
            flash(str(error), "error")
            return redirect(url_for("asite.modversion", id=id))
    if "newmodvermanual_submit" in request.form:
        if User.get_permission_token(session["token"], "mods_create") == 0:
                return redirect(request.referrer)
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
                Modversion.new(id, request.form["newmodvermanual_version"], request.form["newmodvermanual_mcversion"], request.form["newmodvermanual_md5"], filesie2, "0", modloader=request.form.get("newmodvermanual_modloader"))
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
                    modloader=request.form.get("newmodvermanual_modloader"),
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
        return redirect(request.referrer)
    
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
        return redirect(request.referrer)
    
    if User_modpack.get_user_modpackpermission(session["token"], id) == False:
        return redirect(request.referrer)

    try:
        modpack = Modpack.get_by_id(id)
        builds = modpack.get_builds()
    except connector.ProgrammingError as e:
        Database.create_tables()
        builds = []
        flash("error when building modpack build", "error")

    if request.method == "POST":
        if "form-submit" in request.form:
            
            if User.get_permission_token(session["token"], "modpacks_create") == 0:
                return redirect(request.referrer)
            
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
            try:
                Build.new(id, request.form["version"], request.form["mcversion"], publish, private, min_java, request.form["memory"], clonebuild, request.form.get("forge") or None, request.form.get("modloader"), java_runtime)
            except (InvalidJavaRuntimeError, InvalidModloaderError) as error:
                flash(str(error), "error")
                return redirect(url_for("asite.modpack", id=id))
            flash("added build", "success")
            return redirect(url_for("asite.modpack", id=id))
        if "recommended_submit" in request.form:
            common.update_checkbox(id, request.form["modid"], "recommended", "modpacks")
            flash("updated " + id, "success")
            return redirect(url_for("asite.modpack", id=id))
        if "latest_submit" in request.form:
            common.update_checkbox(id, request.form["modid"], "latest", "modpacks")
            flash("updated " + id, "success")
            return redirect(url_for("asite.modpack", id=id))
        if "is_published_submit" in request.form:
            common.update_checkbox(request.form["modid"], request.form["check"], "is_published", "builds")
            flash("updated " + id, "success")
            return redirect(url_for("asite.modpack", id=id))
        if "private_submit" in request.form:
            common.update_checkbox(request.form["modid"], request.form["check"], 'private', 'builds')
            flash("updated " + id, "success")
            return redirect(url_for("asite.modpack", id=id))
        if "marked_submit" in request.form:
            Build.update_checkbox_marked(request.form["modid"], request.form["check"])
            flash("updated " + id, "success")
            return redirect(url_for("asite.modpack", id=id))
        if "changelog_submit" in request.form:
            oldversion = request.form["changelog_oldver"]
            newversion = request.form["changelog_newver"]
            return redirect(url_for('asite.changelog', oldver=oldversion, newver=newversion))
        if "deletemod_submit" in request.form:
            
            if User.get_permission_token(session["token"], "modpacks_delete") == 0:
                return redirect(request.referrer)
            
            if "modpack_delete_id" not in request.form:
                return redirect(url_for("asite.modpack", id=id))
            modpack.delete_modpack(request.form["modpack_delete_id"])
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
                return redirect(request.referrer)

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
        return redirect(request.referrer)

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
    return render_template("mainsettings.html", nam=__name__, deb=debug, host=host, port=port, app_url=app_url, public_repo_url=public_repo_url, md5_repo_url=md5_repo_url, r2_url=R2_URL, db_name=db_name, versr=__version__, r2_bucket=R2_BUCKET, newuser=new_user, technic=migratetechnic, DB_IS_UP=DB_IS_UP, cache_size=cache_size, cache_ttl=cache_ttl, distribution_settings=distribution_settings, curseforge_api_configured=bool(curseforge_api_key))


@asite.route("/platform-export-overrides", methods=["GET", "POST"])
def platform_export_overrides():
    if "token" not in session or not Session.verify_session(
        session["token"], request.remote_addr
    ):
        return redirect(url_for("alogin.login"))
    if User.get_permission_token(session["token"], "solder_env") == 0:
        return redirect(request.referrer or url_for("asite.index"))

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


@asite.route("/platform-export-overrides/export", methods=["GET"])
def export_platform_sync():
    if "token" not in session or not Session.verify_session(
        session["token"], request.remote_addr
    ):
        return redirect(url_for("alogin.login"))
    if User.get_permission_token(session["token"], "solder_env") == 0:
        return redirect(request.referrer or url_for("asite.index"))

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
        return redirect(request.referrer)

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
        return redirect(request.referrer)
    
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
        return redirect(request.referrer)

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
        return redirect(request.referrer)
    
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
                return redirect(request.referrer)
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
                return redirect(request.referrer)
            if "delete_id" not in request.form:
                return redirect(url_for('asite.userlibrary'))
            User.delete(request.form["delete_id"])
            flash("deleted user", "success")
            return redirect(url_for('asite.userlibrary'))
        if "changeuser_submit" in request.form:
            userid = str(Session.get_user_id(session["token"]))
            changeid = request.form["changeuser_id"]
            if userid not in changeid:
                if User.get_permission_token(session["token"], "solder_users") == 0:
                    return redirect(request.referrer)
            if "changeuser_id" not in request.form:
                return redirect(url_for('asite.userlibrary'))
            if "changeuser_password" not in request.form:
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
                return redirect(request.referrer)
    
    modpack_id = Build.get_modpackid_by_id(id)
    if User_modpack.get_user_modpackpermission(session["token"], modpack_id) == False:
        return redirect(request.referrer)

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
                for integrated_mod in integrated_mods:
                    try:
                        mod = Mod.get_by_id(integrated_mod["id"])
                        versions = ModIntegration.list_versions(
                            mod, build, user_id
                        )
                        if versions:
                            materialized = _materialize_integration_version(
                                mod.id, id, versions[0].version_id
                            )
                            preferred_versions[mod.id] = materialized.version.id
                    except IntegrationError as error:
                        integration_errors.append(
                            f'{integrated_mod["pretty_name"]}: {error}'
                        )
                    except Exception as error:
                        ErrorPrinter.message(
                            "failed to update provider-managed mod", error
                        )
                        integration_errors.append(
                            f'{integrated_mod["pretty_name"]}: provider update failed'
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
                return redirect(request.referrer)
            if "delete_id" not in request.form:
                return redirect(url_for("asite.modpackbuild", id=id))
            Build_modversion.delete_build_modversion(request.form["delete_id"])
            flash("deleted modversion", "success")
            return redirect(url_for("asite.modpackbuild", id=id))
        if "deletebuild_submit" in request.form:
            if User.get_permission_token(session["token"], "modpacks_delete") == 0:
                return redirect(request.referrer)
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

    distribution_settings = DistributionSettings.get_all()
    export_requested = (
        request.method == "GET" and request.args.get("export") == "1"
    )
    modrinth_downloaders, modrinth_downloader_error = (), None
    prism_downloaders, prism_downloader_error = (), None
    solderpy_loader_downloaders, solderpy_loader_downloader_error = (), None
    curseforge_downloaders, curseforge_downloader_error = (), None
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
            solderpy_loader_downloaders = tuple(
                downloader
                for downloader in resolved_downloaders
                if downloader.key == "solderpyloader"
            )
            solderpy_loader_downloader_error = resolved_error
            if (
                not solderpy_loader_downloaders
                and solderpy_loader_downloader_error is None
            ):
                solderpy_loader_downloader_error = (
                    "No compatible public SolderPy Loader release was found "
                    "for this Minecraft and modloader version."
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
        prism_downloaders=prism_downloaders,
        prism_downloader_error=prism_downloader_error,
        solderpy_loader_downloaders=solderpy_loader_downloaders,
        solderpy_loader_downloader_error=solderpy_loader_downloader_error,
        curseforge_downloaders=curseforge_downloaders,
        curseforge_downloader_error=curseforge_downloader_error,
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
                    r2_client=R2 if R2_BUCKET else None,
                    r2_bucket=R2_BUCKET,
                )
                flash(
                    "SolderPy Loader enabled for this Technic build.",
                    "success",
                )
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
            build, request.args.get("forge_version")
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
    downloader = request.args.get("solderpy_loader_downloader")
    if not _downloader_export_enabled(downloader):
        return redirect(url_for("asite.modpackbuild", id=id))
    try:
        archive = PlatformPackExport.render_solderpy_loader(
            build,
            downloader,
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
    downloader = request.args.get("modrinth_downloader") or request.args.get(
        "downloader"
    )
    if not _downloader_export_enabled(downloader):
        return redirect(url_for("asite.modpackbuild", id=id))
    try:
        archive = PlatformPackExport.render_mrpack(
            build,
            packages,
            downloader,
            public_repo_url,
            UPLOAD_FOLDER,
            app_url,
            source_mode=request.args.get("source"),
            delivery=request.args.get("delivery"),
            selector=request.args.get("selector"),
            export_overrides=PlatformExportOverride.get_enabled(),
            optional_groups=AdvancedOptional.get_active_groups_for_packages(
                id, packages
            ),
        )
    except PlatformExportError as error:
        flash(str(error), "error")
        return redirect(url_for("asite.modpackbuild", id=id))

    filename = secure_filename(f"{build.modpack_slug}-{build.version}.mrpack")
    return send_file(
        archive,
        mimetype="application/x-modrinth-modpack+zip",
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
    downloader = request.args.get("curseforge_downloader") or request.args.get(
        "downloader"
    )
    if not _downloader_export_enabled(downloader):
        return redirect(url_for("asite.modpackbuild", id=id))
    try:
        archive = PlatformPackExport.render_curseforge(
            build,
            packages,
            downloader,
            public_repo_url,
            UPLOAD_FOLDER,
            app_url,
            curseforge_api_key=curseforge_api_key,
            source_mode=request.args.get("source"),
            delivery=request.args.get("delivery"),
            selector=request.args.get("selector"),
            export_overrides=PlatformExportOverride.get_enabled(),
            optional_groups=AdvancedOptional.get_active_groups_for_packages(
                id, packages
            ),
        )
    except PlatformExportError as error:
        flash(str(error), "error")
        return redirect(url_for("asite.modpackbuild", id=id))

    filename = secure_filename(
        f"{build.modpack_slug}-{build.version}-curseforge.zip"
    )
    return send_file(
        archive,
        mimetype="application/zip",
        as_attachment=True,
        download_name=filename,
    )


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
                return redirect(request.referrer)

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
                return redirect(request.referrer)

    if "form-submit" in request.form:
        markedbuild = "0"
        if "markedbuild" in request.form:
            if User.get_permission_token(session["token"], "modpacks_manage") == 0:
                return redirect(request.referrer)
            if User_modpack.get_user_modpackpermission(session["token"], Build.get_modpackid_by_id(request.form['markedbuild'])) == False:
                return redirect(request.referrer)
            markedbuild = request.form['markedbuild']
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
            version = request.form["mcversion"] + "-" + request.form["version"]
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
                    if jarmd5 != "0":
                        Mod.extract_jar_from_zip(
                            staged_zip,
                            output_name=jarfilename,
                            expected_md5=jarmd5,
                        )
                        staged_jar = Path(staging_directory, jarfilename)

                    actual_filesize = staged_zip.stat().st_size
                    Modversion.new(
                        request.form["modid"],
                        version,
                        request.form["mcversion"],
                        verified_md5,
                        actual_filesize,
                        markedbuild,
                        "0",
                        jarmd5.lower(),
                        modloader=request.form.get("modloader"),
                    )
                    final_zip = destination_folder / filename
                    os.replace(staged_zip, final_zip)
                    if staged_jar is not None:
                        final_jar = destination_folder / jarfilename
                        os.replace(staged_jar, final_jar)
            except (
                IncompatibleModVersionError,
                InvalidModloaderError,
                MissingDependencyVersionError,
                UploadVerificationError,
            ) as error:
                flash(str(error), "error")
                return redirect(url_for("asite.modlibrary"))

            if R2_BUCKET != None:
                keyname = "mods/" + safe_mod_name + "/" + filename
                try:
                    R2.upload_file(str(final_zip), R2_BUCKET, keyname, ExtraArgs={'ContentType': 'application/zip'})
                except Exception as e:
                    ErrorPrinter.message("failed to upload zipfile to buckets", e)
                    flash("failed to upload zipfile to bucket", "error")
            if jarmd5 != "0":
                if R2_BUCKET != None:
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
                return redirect(request.referrer)

    try:
        modpacklibrary = Modpack.get_all()
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
                return redirect(request.referrer)

    if request.method == "POST":
        if "form-submit" in request.form:
            if User.get_permission_token(session["token"], "modpacks_create") == 0:
                return redirect(request.referrer)
            hidden = "0"
            private = "0"
            if "hidden" in request.form:
                hidden = request.form['hidden']
            if "private" in request.form:
                private = request.form['private']
            Modpack.new(request.form["pretty_name"], request.form["name"], hidden, private, "0")
            flash("added modpack", "success")
            return redirect(url_for('asite.modpacklibrary'))
        if User_modpack.get_user_modpackpermission(session["token"], Build.get_modpackid_by_id(request.form["modid"])) == False:
            return redirect(request.referrer)
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
        return redirect(request.referrer)

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

@asite.route("/user/<id>", methods=["GET", "POST"])
def user(id):
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for('alogin.login'))
    
    if User.get_permission_token(session["token"], "solder_users") == 0:
        return redirect(request.referrer)
    
    try:
        packs = User_modpack.get_all_user_modpacks(id)
    except connector.ProgrammingError as e:
        Database.create_tables()
        packs = []
        flash("error when getting user modpack list", "error")
        
    user_perms = User_modpack.get_user_permission(id)
        
    if request.method == "POST":
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

    return render_template("user.html", userpacks=packs, modpacklibrary=modpacklibrary, user_perms=user_perms)

