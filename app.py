__version__ = "0.0.1-dev"

import os
from dotenv import load_dotenv

import threading

from flask import Flask, redirect, render_template, request, url_for, session, request, Response
from werkzeug.utils import secure_filename

import secrets

from models.database import Database
from models.user import User
from models.mod import Mod
from models.build import Build
from models.key import Key
from models.client import Client
from models.modpack import Modpack
from models.session import Session
from models.client_modpack import Client_modpack
from models.modversion import Modversion
from models.build_modversion import Build_modversion

from mysql import connector

from api import api

new_user = False
migratetechnic = False
load_dotenv(".env")
host = os.getenv("APP_URL")
port = os.getenv("APP_PORT")
new_user = os.getenv("NEW_USER")
migratetechnic = os.getenv("TECHNIC_MIGRATION")
debug = False
try:
    debug = os.getenv("APP_DEBUG").lower() in ["true", "t", "1", "yes", "y"]
except AttributeError:
    pass
mirror_url = os.getenv("SOLDER_MIRROR_URL")
repo_url = os.getenv("SOLDER_REPO_LOCATION")
r2_url = os.getenv("R2_URL")
db_name = os.getenv("DB_DATABASE")

app: Flask = Flask(__name__)
app.register_blueprint(api)

app.config["UPLOAD_FOLDER"] = "./mods/"

app.secret_key = secrets.token_hex()

Session.start_session_loop()


def createFolder(dirName):
    os.makedirs(dirName, exist_ok=True)


@app.route("/")
def index():
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for("login"))

    return render_template("index.html")


@app.route("/setup", methods=["GET"])
def setup():
    if not Database.is_setup():
        Database.create_tables()
    if new_user or not User.any_user_exists():
        if migratetechnic:
            Database.migratetechnic_tables()
            return render_template("setup.html")
        return render_template("setup.html")
    else:
        return redirect(url_for("index"))

@app.route("/setup", methods=["POST"])
def setup_creation():
    if new_user or not User.any_user_exists():
        if request.form["setupemail"] is None:
            print("setup failed")
            return render_template("setup.html", failed=True)
        if request.form["setuppassword"] is None:
            print("setup failed")
            return render_template("setup.html", failed=True)
        User.new(request.form["setupemail"], request.form["setupemail"],
            request.form["setuppassword"], request.remote_addr, '1')
        return redirect(url_for("index"))


@app.route("/login", methods=["GET"])
def login_page():
    if "key" in session and Session.verify_session(session["token"], request.remote_addr):
        # Already logged in
        return redirect(url_for("index"))

    return render_template("login.html", failed=False)


@app.route("/login", methods=["POST"])
def login():
    if "key" in session and Session.verify_session(session["token"], request.remote_addr):
        # Already logged in
        print("already logged in")
        return redirect(url_for("index"))

    user = User.get_by_username(request.form["username"])
    if user is None:
        print("login failed")
        return render_template("login.html", failed=True)
    else:
        if user.verify_password(request.form["password"]):
            # if new_user:
            # return render_template("login.html", failed=True)
            session["token"] = Session.new_session(request.remote_addr)
            print("login success")
            return redirect(url_for("index"))
        else:
            return render_template("login.html", failed=True)


@app.route("/logout")
def logout():
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for("login"))

    Session.delete_session(session["token"])
    return redirect(url_for("login"))


@app.route("/modversion/<id>", methods=["GET"])
def modversion(id):
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for("login"))

    mod = Mod.get_by_id(id)
    name = ""
    size = ""
    # if request.method == "POST":
    #     # make check later (check if modid  exists in database)
    #     modver = request.form["modver"]
    #     jarfile = request.files["jarfile"]

    #     if modver != "" and jarfile != "":
    #         jarfile.save(secure_filename(jarfile.filename))
    #         size = len(jarfile.read())
    #         name = jarfile.filename.strip(".jar")
    #         # createFolder("/mods")
    #         createFolder("mods/" + modSlug)
    #         # zipObj = ZipFile(mod.slug+"-"+modver, 'w')

    #         # zipObj.write(jarfile.read())
    #         # zipObj.close()

    #         # add_modversion_db(id,modver,hash,size)

    #     else:
    #         print("error")

    try:
        modversions = mod.get_versions()
    except connector.ProgrammingError as e:
        Database.create_tables()
        modversions = []

    return render_template("modversion.html", modSlug=mod.name, name=name, size=size, modversions=modversions, mod=mod, mirror_url=mirror_url)

@app.route("/modversion/<id>", methods=["POST"])
def newmodversion(id):
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for("login"))
    if "form-submit" in request.form:
        radio = request.form['flexRadioDefault']
        Mod.update(id, request.form["name"], request.form["description"], request.form["author"], request.form["link"], request.form["pretty_name"], radio, request.form["internal_note"])
        return redirect(id)
    if "deleteversion_submit" in request.form:
        if "delete_id" not in request.form:
            return redirect(url_for("clientlibrary"))
        Modversion.delete_modversion(request.form["delete_id"])
        return redirect(id)
    if "form3-submit" in request.form:
        if "delete_id" not in request.form:
            return redirect(url_for("clientlibrary"))
        Mod.delete_mod(request.form["delete_id"])
    if "rehash_submit" in request.form:
        if "rehash_id" not in request.form:
            return redirect(url_for("clientlibrary"))

        if request.form["rehash_md5"] != "":
            version = Modversion.get_by_id(request.form["rehash_id"])
            version.update_hash(request.form["rehash_md5"])
        else:
            version = Modversion.get_by_id(request.form["rehash_id"])
            t = threading.Thread(target=version.rehash, args=(request.form["rehash_url"],))
            t.start()
        print(request.form["rehash_id"])
        print(request.form["rehash_md5"])
    if "newmodvermanual_submit" in request.form:
        print(request.form["newmodvermanual_md5"])
        print(request.form["newmodvermanual_version"])
        print(request.form["newmodvermanual_mcversion"])
        print(request.form["newmodvermanual_url"])
    return redirect(id)


@app.route("/newmod", methods=["GET", "POST"])
def newmod():
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for("login"))
    if request.method == "POST":
        radio = request.form['flexRadioDefault']
        Mod.new(request.form["name"], request.form["description"], request.form["author"], request.form["link"], request.form["pretty_name"], radio, request.form["internal_note"])

    return render_template("newmod.html")


@app.route("/modpack/<id>", methods=["GET", "POST"])
def modpack(id):
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for("login"))

    try:
        modpack = Modpack.get_by_id(id)
        builds = modpack.get_builds()
    except connector.ProgrammingError as e:
        Database.create_tables()
        builds = []

    if request.method == "POST":
        if "form-submit" in request.form:
            publish="0"
            private="0"
            if "min_java" in request.form:
                min_java=request.form['min_java']
                if "NONE" in min_java:
                    min_java=None
            if "publish" in request.form:
                publish=request.form['publish']
            if "private" in request.form:
                private=request.form['private']
            Build.new(id, request.form["version"], request.form["mcversion"], publish, private, min_java, request.form["memory"])
            return redirect(id)
        if "recommended_submit" in request.form:
            Build.update_checkbox(id, request.form["recommended_modid"], "recommended", "modpacks")
            return redirect(id)
        if "latest_submit" in request.form:
            Build.update_checkbox(id, request.form["latest_modid"], "latest", "modpacks")
            return redirect(id)
        if "is_published_submit" in request.form:
            Build.update_checkbox(request.form["is_published_modid"], request.form["is_published_check"], "is_published", "builds")
            return redirect(id)
        if "private_submit" in request.form:
            Build.update_checkbox(request.form["private_modid"], request.form["private_check"], 'private', 'builds')
            return redirect(id)
        if "marked_submit" in request.form:
            Build.update_checkbox_marked(request.form["marked_modid"], request.form["marked_check"])
            return redirect(id)

    return render_template("modpack.html", modpack=builds, modpackname=modpack)


@app.route("/mainsettings")
def mainsettings():
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for("login"))

    return render_template("mainsettings.html", nam=__name__, deb=debug, host=host, port=port, mirror_url=mirror_url, repo_url=repo_url, r2_url=r2_url, db_name=db_name)


@app.route("/apikeylibrary", methods=["GET"])
def apikeylibrary():
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for("login"))

    try:
        keys = Key.get_all_keys()
    except connector.ProgrammingError as e:
        Database.create_tables()
        keys = []

    return render_template("apikeylibrary.html", keys=keys)

@app.route("/apikeylibrary", methods=["POST"])
def apikeylibrary_post():
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for("login"))
    if request.method == "POST":
        if "form-submit" in request.form:
            if "keyname" not in request.form:
                return redirect(url_for("apikeylibrary"))
            if "api_key" not in request.form:
                return redirect(url_for("apikeylibrary"))
            Key.new_key(request.form["keyname"], request.form["api_key"])
            return redirect(url_for("apikeylibrary"))
        if "form2-submit" in request.form:
            if "delete_id" not in request.form:
                return redirect(url_for("apikeylibrary"))
            Key.delete_key(request.form["delete_id"])
            return redirect(url_for("apikeylibrary"))

    return redirect(url_for("apikeylibrary"))


@app.route("/clientlibrary", methods=["GET"])
def clientlibrary():
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for("login"))

    try:
        clients = Client.get_all_clients()
    except connector.ProgrammingError as e:
        Database.create_tables()
        clients = []

    return render_template("clientlibrary.html", clients=clients)

@app.route("/clientlibrary", methods=["POST"])
def clientlibrary_post():
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for("login"))
    if request.method == "POST":
        if "form-submit" in request.form:
            if "client_name" not in request.form:
                return redirect(url_for("clientlibrary"))
            if "client_UUID" not in request.form:
                return redirect(url_for("clientlibrary"))
            Client.new(request.form["client_name"], request.form["client_UUID"])
            return redirect(url_for("clientlibrary"))
        if "form2-submit" in request.form:
            if "delete_id" not in request.form:
                return redirect(url_for("clientlibrary"))
            Client.delete_client(request.form["delete_id"])
            return redirect(url_for("clientlibrary"))

    return redirect(url_for("clientlibrary"))


@app.route("/userlibrary", methods=["GET"])
def userlibrary():
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for("login"))

    try:
        users = User.get_all_users()
    except connector.ProgrammingError as e:
        Database.create_tables()
        users = []

    return render_template("userlibrary.html", users=users)

@app.route("/userlibrary", methods=["POST"])
def userlibrary_post():
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for("login"))
    if request.method == "POST":
        if "form-submit" in request.form:
            if "newemail" not in request.form:
                return redirect(url_for("userlibrary"))
            if "newpassword" not in request.form:
                return redirect(url_for("userlibrary"))
            if "newuser" not in request.form:
                return redirect(url_for("userlibrary"))
            User.new(request.form["newuser"], request.form["newemail"], request.form["newpassword"], request.remote_addr, '1')
            return redirect(url_for("userlibrary"))
        if "form2-submit" in request.form:
            if "delete_id" not in request.form:
                return redirect(url_for("userlibrary"))
            User.delete(request.form["delete_id"])
            return redirect(url_for("userlibrary"))

    return redirect(url_for("userlibrary"))

@app.route("/modpackbuild/<id>", methods=["GET", "POST"])
def modpackbuild(id):
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for("login"))
    listmod = Mod.get_all()

    try:
        packbuild = Build.get_by_id(id)
        modpackbuild = packbuild.get_modversions_minimal()
        packbuildname = Build.get_modpackname_by_id(id)
        mods = Mod.get_multi_by_id(
            tuple(build_modversion.mod_id for build_modversion in modpackbuild))
        mod_mapping = {mod.id: mod for mod in mods}
        mod_version_combo = [(mod_mapping[build_modversion.mod_id],
                              build_modversion) for build_modversion in modpackbuild]
        print(mod_version_combo)
    except connector.ProgrammingError as _:
        raise _
        Database.create_tables()
        mod_version_combo = []

    if request.method == "POST":
        if "form-submit" in request.form:
            publish="0"
            private="0"
            if "min_java" in request.form:
                min_java=request.form['min_java']
                if "NONE" in min_java:
                    min_java=None
            if "publish" in request.form:
                publish=request.form['publish']
            if "private" in request.form:
                private=request.form['private']
            Build.update(id, request.form["version"], request.form["mcversion"], publish, private, min_java, request.form["memory"])
            return redirect(id)
        if "optional_submit" in request.form:
            Build_modversion.update_optional(request.form["optional_modid"], request.form["optional_check"])
            return redirect(id)
        if "delete_submit" in request.form:
            if "delete_id" not in request.form:
                return redirect(id)
            Build_modversion.delete_build_modversion(request.form["delete_id"])
            return redirect(id)

    return render_template("modpackbuild.html", mod_version_combo=mod_version_combo, listmod=listmod, packbuild=packbuild, packbuildname=packbuildname)


@app.route("/modlibrary", methods=["GET"])
def modlibrary():
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for("login"))

    try:
        mods = Mod.get_all()
    except connector.ProgrammingError as e:
        Database.create_tables()
        mods = []

    return render_template("modlibrary.html", mods=mods)

@app.route("/modlibrary", methods=["POST"])
def modlibrary_post():
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for("login"))
    if "form-submit" in request.form:
        markedbuild=None
        if "markedbuild" in request.form:
            markedbuild=request.cookies.get('marked_id')
        Modversion.new(request.form["modid"], request.form["version"], request.form["mcversion"], request.form["md5"], request.form["filesize"], markedbuild)
        return redirect(url_for("modlibrary"))

    return redirect(url_for("modlibrary"))


@app.route("/modpacklibrary", methods=["GET"])
def modpacklibrary():
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for("login"))

    try:
        modpacklibrary = Modpack.get_all()
    except connector.ProgrammingError as e:
        Database.create_tables()
        modpacklibrary = []

    return render_template("modpacklibrary.html", modpacklibrary=modpacklibrary)

@app.route("/modpacklibrary", methods=["POST"])
def modpacklibrary_post():
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for("login"))
    if request.method == "POST":
        if "form-submit" in request.form:
            hidden="0"
            private="0"
            if "hidden" in request.form:
                hidden=request.form['hidden']
            if "private" in request.form:
                private=request.form['private']
            Modpack.new(request.form["pretty_name"], request.form["name"], hidden, private, "0")
            return redirect(url_for("modpacklibrary"))
        if "hidden_submit" in request.form:
            Modpack.update_checkbox(request.form["hidden_modid"], request.form["hidden_check"], "hidden", "modpacks")
        if "private_submit" in request.form:
            Modpack.update_checkbox(request.form["private_modid"], request.form["private_check"], "private", "modpacks")
        if "pinned_submit" in request.form:
            Modpack.update_checkbox(request.form["pinned_modid"], request.form["pinned_check"], "pinned", "modpacks")

    return redirect(url_for("modpacklibrary"))


@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html", error=e), 404


@app.route("/clients/<id>", methods=["GET", "POST"])
def clients(id):
    if "token" not in session or not Session.verify_session(session["token"], request.remote_addr):
        # New or invalid session, send to login
        return redirect(url_for("login"))

    try:
        packs = Client_modpack.get_all_client_modpacks(id)
    except connector.ProgrammingError as e:
        Database.create_tables()
        packs = []

    if request.method == "POST":
        if "form-submit" in request.form:
            if "modpack" not in request.form:
                return redirect(id)
            Client_modpack.new(id, request.form["modpack"])
            return redirect(id)
        if "form2-submit" in request.form:
            if "delete_id" not in request.form:
                return redirect(id)
            Client_modpack.delete_client_modpack(request.form["delete_id"])
            return redirect(id)

    return render_template("clients.html", clients=packs)


if __name__ == "__main__":
    app.run(debug=debug, use_reloader=False, host=host, port=port)
