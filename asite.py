import os
import threading
import boto3

from api import solderpy_version
from flask import Blueprint, app, flash, redirect, render_template, request, session, url_for
from models.build import Build
from models.build_modversion import Build_modversion
from models.client import Client
from models.client_modpack import Client_modpack
from models.database import Database
from models.key import Key
from models.mod import Mod
from models.modpack import Modpack
from models.modversion import Modversion
from models.session import Session
from models.user import User
from mysql import connector
from werkzeug.utils import secure_filename
from models.common import mirror_url, debug, host, port, repo_url, R2_URL, db_name, R2_BUCKET, new_user, migratetechnic, solderpy_version, R2_REGION, R2_ENDPOINT, R2_ACCESS_KEY, R2_SECRET_KEY, UPLOAD_FOLDER, common, DB_IS_UP
from models.user_modpack import User_modpack

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
    except connector.ProgrammingError as e:
        Database.create_tables()
        modversions = []
        flash("unable to get modversions", "error")

    return render_template("modversion.html", modSlug=mod.name, modversions=modversions, mod=mod, mirror_url=mirror_url)


@asite.route("/modversion/<id>", methods=["POST"])
def newmodversion(id):
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for('alogin.login'))
    
    if User.get_permission_token(session["token"], "mods_manage") == 0:
                return redirect(request.referrer)
    
    if "form-submit" in request.form:
        mod_side = request.form['flexRadioDefault']
        mod_type = request.form['type']
        Mod.update(id, request.form["name"], request.form["description"], request.form["author"], request.form["link"], request.form["pretty_name"], mod_side, mod_type, request.form["internal_note"])
        return redirect(url_for("asite.modversion", id=id))
    if "deleteversion_submit" in request.form:
        if User.get_permission_token(session["token"], "mods_delete") == 0:
                return redirect(request.referrer)
        if "delete_id" not in request.form:
            return redirect(url_for("asite.modversion", id=id))
        Modversion.delete_modversion(request.form["delete_id"])
        return redirect(url_for("asite.modversion", id=id))
    if "addtoselbuild_submit" in request.form:
        if User.get_permission_token(session["token"], "modpacks_manage") == 0:
                return redirect(request.referrer)
        if "addtoselbuild_id" not in request.form:
            return redirect(url_for("asite.modversion", id=id))
        Modversion.add_modversion_to_selected_build(request.form["addtoselbuild_id"], id, "0", "1", "0")
        return redirect(url_for("asite.modversion", id=id))
    if "deletemod_submit" in request.form:
        if User.get_permission_token(session["token"], "mods_delete") == 0:
                return redirect(request.referrer)
        if "mod_delete_id" not in request.form:
            return redirect(url_for("asite.modversion", id=id))
        Mod.delete_mod(request.form["mod_delete_id"])
        return redirect(url_for('asite.modlibrary'))
    if "rehash_submit" in request.form:
        if "rehash_id" not in request.form:
            return redirect(url_for('asite.clientlibrary'))

        if request.form["rehash_md5"] != "":
            version = Modversion.get_by_id(request.form["rehash_id"])
            version.update_hash(request.form["rehash_md5"], repo_url + request.form["rehash_url"])
        else:
            version = Modversion.get_by_id(request.form["rehash_id"])
            t = threading.Thread(target=version.rehash, args=(repo_url + request.form["rehash_url"],))
            t.start()
    if "newmodvermanual_submit" in request.form:
        if User.get_permission_token(session["token"], "mods_create") == 0:
                return redirect(request.referrer)
        filesie2 = Modversion.get_file_size(repo_url + request.form["newmodvermanual_url"])
        if request.form["newmodvermanual_md5"] != "":
            Modversion.new(id, request.form["newmodvermanual_version"], request.form["newmodvermanual_mcversion"], request.form["newmodvermanual_md5"], filesie2, "0")
        else:
            # Todo Add filesize rehash and md5 hash, if fails do not add
            Modversion.new(id, request.form["newmodvermanual_version"], request.form["newmodvermanual_mcversion"], "0", filesie2, "0", repo_url + request.form["newmodvermanual_url"])
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
        Mod.new(request.form["name"], request.form["description"], request.form["author"], request.form["link"], request.form["pretty_name"], mod_side, mod_type, request.form["internal_note"])
        return redirect(url_for('asite.modlibrary'))

    return render_template("newmod.html")


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
            Build.new(id, request.form["version"], request.form["mcversion"], publish, private, min_java, request.form["memory"], clonebuild)
            return redirect(url_for("asite.modpack", id=id))
        if "recommended_submit" in request.form:
            common.update_checkbox(id, request.form["modid"], "recommended", "modpacks")
            return redirect(url_for("asite.modpack", id=id))
        if "latest_submit" in request.form:
            common.update_checkbox(id, request.form["modid"], "latest", "modpacks")
            return redirect(url_for("asite.modpack", id=id))
        if "is_published_submit" in request.form:
            common.update_checkbox(request.form["modid"], request.form["check"], "is_published", "builds")
            return redirect(url_for("asite.modpack", id=id))
        if "private_submit" in request.form:
            common.update_checkbox(request.form["modid"], request.form["check"], 'private', 'builds')
            return redirect(url_for("asite.modpack", id=id))
        if "marked_submit" in request.form:
            common.update_checkbox_marked(request.form["modid"], request.form["check"])
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
            return redirect(url_for('asite.modpacklibrary'))

    return render_template("modpack.html", modpack=builds, modpackname=modpack)


@asite.route("/changelog/<oldver>-<newver>", methods=["GET", "POST"])
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
    
    return render_template("mainsettings.html", nam=__name__, deb=debug, host=host, port=port, mirror_url=mirror_url, repo_url=repo_url, r2_url=R2_URL, db_name=db_name, versr=__version__, r2_bucket=R2_BUCKET, newuser=new_user, technic=migratetechnic, DB_IS_UP=DB_IS_UP)


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
            return redirect(url_for('asite.apikeylibrary'))
        if "form2-submit" in request.form:
            if "delete_id" not in request.form:
                return redirect(url_for('asite.apikeylibrary'))
            Key.delete_key(request.form["delete_id"])
            return redirect(url_for('asite.apikeylibrary'))

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
            return redirect(url_for('asite.clientlibrary'))
        if "form2-submit" in request.form:
            if "delete_id" not in request.form:
                return redirect(url_for('asite.clientlibrary'))
            Client.delete_client(request.form["delete_id"])
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
            return redirect(url_for('asite.userlibrary'))
        if "form2-submit" in request.form:
            if User.get_permission_token(session["token"], "solder_users") == 0:
                return redirect(request.referrer)
            if "delete_id" not in request.form:
                return redirect(url_for('asite.userlibrary'))
            User.delete(request.form["delete_id"])
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

    try:
        listmod = Mod.get_all_pretty_names()
        packbuild = Build.get_by_id(id)
        listmodversions = Modversion.get_all()
        buildlist = Build_modversion.get_modpack_build(id)

        packbuildname = Build.get_modpackname_by_id(id)
    except connector.ProgrammingError as _:
        flash("failed to build modpackbuild", "error")
        raise _
        Database.create_tables()
        mod_version_combo = []

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
            Build.update(id, request.form["version"], request.form["mcversion"], publish, private, min_java, request.form["memory"])
            return redirect(url_for("asite.modpackbuild", id=id))
        if "optional_submit" in request.form:
            Build_modversion.update_optional(request.form["optional_modid"], request.form["optional_check"], id)
            return redirect(url_for("asite.modpackbuild", id=id))
        if "selmodver_submit" in request.form:
            Modversion.update_modversion_in_build(request.form["selmodver_oldver"], request.form["selmodver_ver"], id)
            return redirect(url_for("asite.modpackbuild", id=id))
        if "delete_submit" in request.form:
            if User.get_permission_token(session["token"], "modpacks_delete") == 0:
                return redirect(request.referrer)
            if "delete_id" not in request.form:
                return redirect(url_for("asite.modpackbuild", id=id))
            Build_modversion.delete_build_modversion(request.form["delete_id"])
            return redirect(url_for("asite.modpackbuild", id=id))
        if "deletebuild_submit" in request.form:
            if User.get_permission_token(session["token"], "modpacks_delete") == 0:
                return redirect(request.referrer)
            Build.delete_build(id)
            return redirect(url_for('asite.modpacklibrary'))
        if "add_mod_submit" in request.form:
            newoptional = "0"
            if "newoptional" in request.form:
                newoptional = request.form['newoptional']
            Modversion.add_modversion_to_selected_build(request.form["modversion"], request.form["modnames"], id, "0", newoptional)
            return redirect(url_for("asite.modpackbuild", id=id))

    return render_template("modpackbuild.html", listmod=listmod, packbuild=packbuild, packbuildname=packbuildname, listmodversions=listmodversions, buildlist=buildlist)


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
        Modversion.new(request.form["modid"], request.form["mcversion"] + "-" + request.form["version"], request.form["mcversion"], request.form["md5"], request.form["filesize"], markedbuild, "0", request.form["jarmd5"])
        if 'file' not in request.files:
            print('No file part')
            return redirect(url_for('asite.modlibrary'))
        filew = request.files['file']
        if filew.filename == '':
            print('No selected file')
            return redirect(url_for('asite.modlibrary'))
        if filew and allowed_file(filew.filename):
            filename = secure_filename(filew.filename)
            print("saving")
            createFolder(UPLOAD_FOLDER + secure_filename(request.form["mod"]) + "/")
            filew.save(os.path.join(UPLOAD_FOLDER + secure_filename(request.form["mod"]) + "/", filename))
            if R2_BUCKET != None:
                keyname = "mods/" + request.form["mod"] + "/" + filename
                try:
                    R2.upload_file(UPLOAD_FOLDER + request.form["mod"] + "/" + filename, R2_BUCKET, keyname, ExtraArgs={'ContentType': 'application/zip'})
                except:
                    flash("failed to upload zipfile to bucket", "error")
            jarfilew = request.files['jarfile']
            if jarfilew and allowed_file(jarfilew.filename):
                jarfilename = secure_filename(jarfilew.filename)
                print("saving jar")
                createFolder(UPLOAD_FOLDER + secure_filename(request.form["mod"]) + "/")
                jarfilew.save(os.path.join(UPLOAD_FOLDER + secure_filename(request.form["mod"]) + "/", jarfilename))
                if R2_BUCKET != None:
                    jarkeyname = "mods/" + request.form["mod"] + "/" + jarfilename
                    try:
                        R2.upload_file(UPLOAD_FOLDER + request.form["mod"] + "/" + jarfilename, R2_BUCKET, jarkeyname, ExtraArgs={'ContentType': 'application/zip'})
                    except:
                        flash("failed to upload jarfile to bucket", "error")
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
            return redirect(url_for('asite.modpacklibrary'))
        if User_modpack.get_user_modpackpermission(session["token"], Build.get_modpackid_by_id(request.form["modid"])) == False:
            return redirect(request.referrer)
        if "hidden_submit" in request.form:
            common.update_checkbox(request.form["modid"], request.form["check"], "hidden", "modpacks")
        if "private_submit" in request.form:
            common.update_checkbox(request.form["modid"], request.form["check"], "private", "modpacks")
        if "pinned_submit" in request.form:
            common.update_checkbox(request.form["modid"], request.form["check"], "pinned", "modpacks")
        if "optional_submit" in request.form:
            common.update_checkbox(request.form["modid"], request.form["check"], "enable_optionals", "modpacks")
        if "server_submit" in request.form:
            common.update_checkbox(request.form["modid"], request.form["check"], "enable_server", "modpacks")

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
            return redirect(url_for("asite.clients", id=id))
        if "form2-submit" in request.form:
            if "delete_id" not in request.form:
                return redirect(url_for("asite.clients", id=id))
            Client_modpack.delete_client_modpack(request.form["delete_id"])
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

