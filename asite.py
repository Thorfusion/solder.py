import os
from pathlib import Path
import tempfile
import threading
import boto3

from api import solderpy_version
from flask import Blueprint, app, flash, jsonify, redirect, render_template, request, send_file, session, url_for
from models.build import Build
from models.build_modversion import Build_modversion
from models.client import Client
from models.client_modpack import Client_modpack
from models.compatibility import InvalidModloaderError
from models.database import Database
from models.key import Key
from models.mcinstance import (
    MCInstanceExport,
    MCInstanceExportError,
    MCInstanceJar,
)
from models.integration import (
    CURSEFORGE,
    MODRINTH,
    CurseForgeProvider,
    IntegrationCredential,
    IntegrationError,
    ModIntegration,
    external_id,
    normalize_provider,
    provider_for_user,
)
from models.mod import DuplicateModError, Mod, UploadVerificationError
from models.mod_dependency import DependencyError, ModDependency
from models.modpack import Modpack
from models.modversion import IncompatibleModVersionError, MissingDependencyVersionError, Modversion
from models.session import Session
from models.user import User
from mysql import connector
from werkzeug.utils import secure_filename
from models.common import public_repo_url, debug, host, port, md5_repo_url, R2_URL, db_name, R2_BUCKET, new_user, migratetechnic, solderpy_version, R2_REGION, R2_ENDPOINT, R2_ACCESS_KEY, R2_SECRET_KEY, UPLOAD_FOLDER, common, DB_IS_UP, cache_size, cache_ttl
from models.user_modpack import User_modpack
from models.errorPrinter import ErrorPrinter

__version__ = solderpy_version

asite = Blueprint("asite", __name__)

if DB_IS_UP == 1:
    Session.start_session_loop()

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
    
    return dict(markedbuildid2=markedbuildid2, solderversion=solderpy_version, pinnedmodpacks=pinnedmodpacks)


@asite.route("/")
def index():
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for('alogin.login'))

    return render_template('index.html')


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

    return render_template(
        "modversion.html",
        modSlug=mod.name,
        modversions=modversions,
        mod=mod,
        mirror_url=public_repo_url,
        dependencies=dependencies,
        available_dependencies=available_dependencies,
    )


@asite.route("/modversion/<id>", methods=["POST"])
def newmodversion(id):
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for('alogin.login'))
    
    if User.get_permission_token(session["token"], "mods_manage") == 0:
                return redirect(request.referrer)

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

        if request.form["rehash_md5"] != "":
            version = Modversion.get_by_id(request.form["rehash_id"])
            version.update_hash(request.form["rehash_md5"], md5_repo_url + request.form["rehash_url"])
        else:
            version = Modversion.get_by_id(request.form["rehash_id"])
            t = threading.Thread(target=version.rehash, args=(md5_repo_url + request.form["rehash_url"],))
            t.start()
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
        filesie2 = Modversion.get_file_size(md5_repo_url + request.form["newmodvermanual_url"])
        if request.form["newmodvermanual_md5"] != "":
            try:
                Modversion.new(id, request.form["newmodvermanual_version"], request.form["newmodvermanual_mcversion"], request.form["newmodvermanual_md5"], filesie2, "0", modloader=request.form.get("newmodvermanual_modloader"))
            except InvalidModloaderError as error:
                flash(str(error), "error")
        else:
            # Todo Add filesize rehash and md5 hash, if fails do not add
            try:
                Modversion.new(id, request.form["newmodvermanual_version"], request.form["newmodvermanual_mcversion"], "0", filesie2, "0", md5_repo_url + request.form["newmodvermanual_url"], modloader=request.form.get("newmodvermanual_modloader"))
            except InvalidModloaderError as error:
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
        return redirect(request.referrer or url_for("asite.index"))

    user_id = Session.get_user_id(session["token"])
    if request.method == "POST":
        try:
            if "save_curseforge_key" in request.form:
                api_key = request.form.get("curseforge_api_key", "").strip()
                provider = CurseForgeProvider(api_key)
                if not provider.validate_credentials():
                    raise IntegrationError("CurseForge rejected the API key.")
                IntegrationCredential.set(user_id, CURSEFORGE, api_key)
                flash("saved your CurseForge API key", "success")
            elif "delete_curseforge_key" in request.form:
                IntegrationCredential.delete(user_id, CURSEFORGE)
                flash("removed your CurseForge API key", "success")
            elif "import_project" in request.form:
                mod, created = ModIntegration.import_project(
                    request.form.get("provider"),
                    request.form.get("project_id"),
                    user_id,
                )
                if created:
                    flash(f"added {mod.pretty_name} to the mod library", "success")
                else:
                    flash(f"{mod.pretty_name} is already in the mod library", "success")
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
    query = request.args.get("q", "").strip()[:100]
    curseforge_configured = bool(
        IntegrationCredential.get(user_id, CURSEFORGE)
    )
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

    return render_template(
        "integrations.html",
        provider=selected_provider,
        query=query,
        projects=projects,
        curseforge_configured=curseforge_configured,
        modrinth=MODRINTH,
        curseforge=CURSEFORGE,
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
            if "min_java" in request.form:
                min_java = request.form['min_java']
                if "NONE" in min_java:
                    min_java = None
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
                Build.new(id, request.form["version"], request.form["mcversion"], publish, private, min_java, request.form["memory"], clonebuild, request.form.get("forge") or None, request.form.get("modloader"))
            except InvalidModloaderError as error:
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

    return render_template("modpack.html", modpack=builds, modpackname=modpack)


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


@asite.route("/mainsettings")
def mainsettings():
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for('alogin.login'))

    if User.get_permission_token(session["token"], "solder_env") == 0:
        return redirect(request.referrer)
    
    return render_template("mainsettings.html", nam=__name__, deb=debug, host=host, port=port, public_repo_url=public_repo_url, md5_repo_url=md5_repo_url, r2_url=R2_URL, db_name=db_name, versr=__version__, r2_bucket=R2_BUCKET, newuser=new_user, technic=migratetechnic, DB_IS_UP=DB_IS_UP, cache_size=cache_size, cache_ttl=cache_ttl)


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


@asite.route("/modpackbuild/<id>", methods=["GET", "POST"])
def modpackbuild(id):
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for('alogin.login'))
    
    if User.get_permission_token(session["token"], "modpacks_manage") == 0:
                return redirect(request.referrer)
    
    if User_modpack.get_user_modpackpermission(session["token"], Build.get_modpackid_by_id(id)) == False:
        return redirect(request.referrer)

    if request.method == "POST":
        if "form-submit" in request.form:
            publish = "0"
            private = "0"
            if "min_java" in request.form:
                min_java = request.form['min_java']
                if "NONE" in min_java:
                    min_java = None
            if "publish" in request.form:
                publish = request.form['publish']
            if "private" in request.form:
                private = request.form['private']
            try:
                Build.update(id, request.form["version"], request.form["mcversion"], publish, private, min_java, request.form["memory"], request.form.get("forge") or None, request.form.get("modloader"))
            except InvalidModloaderError as error:
                flash(str(error), "error")
                return redirect(url_for("asite.modpackbuild", id=id))
            flash("updated " + id, "success")
            return redirect(url_for("asite.modpackbuild", id=id))
        if "update_all_mods_submit" in request.form:
            integration_errors = []
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
                            _materialize_integration_version(
                                mod.id, id, versions[0].version_id
                            )
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

            updated = Build_modversion.update_all_compatible(id)
            if updated:
                flash(f"updated {updated} mod(s)", "success")
            elif not integration_errors:
                flash("all mods are already up to date", "success")
            if integration_errors:
                flash("; ".join(integration_errors), "error")
            return redirect(url_for("asite.modpackbuild", id=id))
        if "optional_submit" in request.form:
            Build_modversion.update_optional(request.form["optional_modid"], request.form["optional_check"], id)
            flash("updated " + id, "success")
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
            newoptional = "0"
            if "newoptional" in request.form:
                newoptional = request.form['newoptional']
            mod_id = request.form.get("modnames", "").strip()
            selected_version = request.form.get("modversion", "").strip()
            if not mod_id or not selected_version:
                flash("select a mod and compatible version", "error")
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
            except (
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

    return render_template(
        "modpackbuild.html",
        listmod=editor.listmod,
        packbuild=editor.packbuild,
        packbuildname=editor.packbuildname,
        listmodversions=editor.listmodversions,
        buildlist=editor.buildlist,
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
        return jsonify({"error": str(error)}), 400


@asite.route("/modpackbuild/<int:id>/mcinstance", methods=["GET"])
def export_mcinstance(id):
    if "token" not in session or not Session.verify_session(
        session["token"], request.remote_addr
    ):
        return redirect(url_for("alogin.login"))

    if User.get_permission_token(session["token"], "modpacks_manage") == 0:
        return redirect(request.referrer or url_for("asite.modpacklibrary"))

    modpack_id = Build.get_modpackid_by_id(id)
    if not modpack_id or not User_modpack.get_user_modpackpermission(
        session["token"], modpack_id
    ):
        return redirect(request.referrer or url_for("asite.modpacklibrary"))

    try:
        build, packages = MCInstanceExport.load(id)
        archive = MCInstanceExport.render(
            build, packages, public_repo_url, UPLOAD_FOLDER
        )
    except MCInstanceExportError as error:
        flash(str(error), "error")
        return redirect(url_for("asite.modpack", id=modpack_id))

    filename = secure_filename(f"{build.modpack_slug}-{build.version}.mcinstance")
    return send_file(
        archive,
        mimetype="application/zip",
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
            filename = f"{mod_name}-{version}.zip"
            jarfilename = f"{mod_name}-{version}.jar"
            if (
                secure_filename(mod_name) != mod_name
                or secure_filename(version) != version
            ):
                flash("The mod slug or version contains unsafe filename characters.", "error")
                return redirect(url_for("asite.modlibrary"))
            destination_folder = Path(UPLOAD_FOLDER, mod_name)
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
                keyname = "mods/" + request.form["mod"] + "/" + filename
                try:
                    R2.upload_file(str(final_zip), R2_BUCKET, keyname, ExtraArgs={'ContentType': 'application/zip'})
                except Exception as e:
                    ErrorPrinter.message("failed to upload zipfile to buckets", e)
                    flash("failed to upload zipfile to bucket", "error")
            if jarmd5 != "0":
                if R2_BUCKET != None:
                    jarkeyname = "mods/" + request.form["mod"] + "/" + jarfilename
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

