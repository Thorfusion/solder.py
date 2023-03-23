__version__ = "0.0.1-dev"

import os
from dotenv import load_dotenv

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

from mysql import connector

from api import api

from datetime import datetime
import time
import threading

load_dotenv(".env")
host = os.getenv("APP_URL")
port = os.getenv("APP_PORT")
debug = bool(os.getenv("APP_DEBUG"))

app: Flask = Flask(__name__)
app.register_blueprint(api)

app.config["UPLOAD_FOLDER"] = "./mods/"

app.secret_key = secrets.token_hex()
app.sessions = {}

def sessionLoop() -> None:
    while True:
        to_delete = []
        for key in app.sessions:
            if (datetime.utcnow() - app.sessions[key]).total_seconds() > 420:
                to_delete.append(key)
        for key in to_delete:
            del app.sessions[key]
        time.sleep(1)

t = threading.Thread(target=sessionLoop)
t.start()

def createFolder(dirName):
    os.makedirs(dirName, exist_ok=True)

@app.route("/")
def index():
    if "key" in session and session["key"] in app.sessions:
        # Valid session, refresh token
        app.sessions[session["key"]] = datetime.utcnow()
    else:
        # New or invalid session, send to login
        return redirect(url_for("login"))


    return render_template("index.html")

@app.route("/setup", methods=["GET", "POST"])
def setup():
    if request.method == "GET":
        if Database.is_setup():
            return redirect(url_for("index"))
        Database.create_tables()
        return render_template("setup.html")
    else:
        if Database.is_setup():
            return Response(status=400)
        return redirect(url_for("index"))

@app.route("/login", methods=["GET"])
def login_page():
    if "key" in session and session["key"] in app.sessions:
        return redirect(url_for("index"))
    else:
        return render_template("login.html", failed=False)

@app.route("/login", methods=["POST"])
def login():
    # already logged in
    if "key" in session and session["key"] in app.sessions:
        return redirect(url_for("index"))
    else:
        user = User.get_by_username(request.form["username"])
        if user is None:
            return render_template("login.html", failed=True)
        else:
            if user.verify_password(request.form["password"]):
                session["key"] = secrets.token_hex()
                app.sessions[session["key"]] = datetime.utcnow()
                return redirect(url_for("index"))
            else:
                return render_template("login.html", failed=True)

@app.route("/logout")
def logout():
    if "key" in session and session["key"] in app.sessions:
        del app.sessions[session["key"]]
        session.pop("key")
    return render_template("login.html", failed=False)


@app.route("/modversion/<id>", methods=["GET", "POST"])
def modversion(id):
    if "key" in session and session["key"] in app.sessions:
        # Valid ession, refresh token
        app.sessions[session["key"]] = datetime.utcnow()
    else:
        # New or invalid session, send to login
        return redirect(url_for("login"))

    mod = Mod.get_by_id(id)
    name = ""
    size = ""
    if request.method == "POST":
        # make check later (check if modid  exists in database)
        modver = request.form["modver"]
        jarfile = request.files["jarfile"]

        if modver != "" and jarfile != "":
            jarfile.save(secure_filename(jarfile.filename))
            size = len(jarfile.read())
            name = jarfile.filename.strip(".jar")
            # createFolder("/mods")
            createFolder("mods/" + modSlug)
            # zipObj = ZipFile(mod.slug+"-"+modver, 'w')

            # zipObj.write(jarfile.read())
            # zipObj.close()

            # add_modversion_db(id,modver,hash,size)

        else:
            print("error")

    try:
        modversions = mod.get_versions()
    except connector.ProgrammingError as e:
        Database.create_tables()
        modversions = []

    return render_template("modversion.html", modSlug=mod.name, name=name, size=size, modversions=modversions, mod=mod)

@app.route("/newmod")
def newmod():
    if "key" in session and session["key"] in app.sessions:
        # Valid session, refresh token
        app.sessions[session["key"]] = datetime.utcnow()
    else:
        # New or invalid session, send to login
        return redirect(url_for("login"))

    return render_template("newmod.html")

@app.route("/modpack/<id>", methods=["GET", "POST"])
def modpack(id):
    if "key" in session and session["key"] in app.sessions:
        # Valid session, refresh token
        app.sessions[session["key"]] = datetime.utcnow()
    else:
        # New or invalid session, send to login
        return redirect(url_for("login"))

    try:
        builds = Modpack.get_by_id(id).get_builds()
    except connector.ProgrammingError as e:
        Database.create_tables()
        builds = []

    return render_template("modpack.html", modpack=builds)

@app.route("/mainsettings")
def mainsettings():
    if "key" in session and session["key"] in app.sessions:
        # Valid session, refresh token
        app.sessions[session["key"]] = datetime.utcnow()
    else:
        # New or invalid session, send to login
        return redirect(url_for("login"))

    return render_template("mainsettings.html", nam=__name__)

@app.route("/apikeylibrary")
def apikeylibrary():
    if "key" in session and session["key"] in app.sessions:
        # Valid session, refresh token
        app.sessions[session["key"]] = datetime.utcnow()
    else:
        # New or invalid session, send to login
        return redirect(url_for("login"))

    try:
        keys = Key.get_all_keys()
    except connector.ProgrammingError as e:
        Database.create_tables()
        keys = []

    return render_template("apikeylibrary.html", keys=keys)

@app.route("/clientlibrary")
def clientlibrary():
    if "key" in session and session["key"] in app.sessions:
        # Valid session, refresh token
        app.sessions[session["key"]] = datetime.utcnow()
    else:
        # New or invalid session, send to login
        return redirect(url_for("login"))

    try:
        clients = Client.get_all_clients()
    except connector.ProgrammingError as e:
        Database.create_tables()
        clients = []

    return render_template("clientlibrary.html", clients=clients)

@app.route("/userlibrary")
def userlibrary():
    if "key" in session and session["key"] in app.sessions:
        # Valid session, refresh token
        app.sessions[session["key"]] = datetime.utcnow()
    else:
        # New or invalid session, send to login
        return redirect(url_for("login"))

    try:
        users = User.get_all_users()
    except connector.ProgrammingError as e:
        Database.create_tables()
        users = []

    return render_template("userlibrary.html", users=users)

@app.route("/modpackbuild/<id>", methods=["GET", "POST"])
def modpackbuild(id):
    if "key" in session and session["key"] in app.sessions:
        # Valid session, refresh token
        app.sessions[session["key"]] = datetime.utcnow()
    else:
        # New or invalid session, send to login
        return redirect(url_for("login"))
    listmod = Mod.get_all()

    try:
        modpackbuild = Build.get_by_id(id).get_modversions_minimal()
        packbuild = Build.get_by_id(id)
        mod_version_combo = [(Mod.get_by_id(build_modversion.mod_id), build_modversion) for build_modversion in modpackbuild]
    except connector.ProgrammingError as e:
        Database.create_tables()
        mod_version_combo = []

    return render_template("modpackbuild.html", mod_version_combo=mod_version_combo, listmod=listmod, packbuild=packbuild)

@app.route("/modlibrary")
def modlibrary():
    if "key" in session and session["key"] in app.sessions:
        # Valid session, refresh token
        app.sessions[session["key"]] = datetime.utcnow()
    else:
        # New or invalid session, send to login
        return redirect(url_for("login"))

    try:
        mods = Mod.get_all()
    except connector.ProgrammingError as e:
        Database.create_tables()
        mods = []

    return render_template("modlibrary.html", mods=mods)

@app.route("/modpacklibrary")
def modpacklibrary():
    if "key" in session and session["key"] in app.sessions:
        # Valid session, refresh token
        app.sessions[session["key"]] = datetime.utcnow()
    else:
        # New or invalid session, send to login
        return redirect(url_for("login"))

    try:
        modpacklibrary = Modpack.get_all()
    except connector.ProgrammingError as e:
        Database.create_tables()
        modpacklibrary = []

    return render_template("modpacklibrary.html", modpacklibrary=modpacklibrary)

@app.route("/newmod", methods=["POST"])
def newmod_submit():
    if "key" in session and session["key"] in app.sessions:
        # Valid session, refresh token
        app.sessions[session["key"]] = datetime.utcnow()
    else:
        # New or invalid session, send to login
        return redirect(url_for("login"))

    form = request.form
    print(form)

    name = form['name']
    pretty_name = form['pretty_name']
    author = form['author']
    description = form['description']
    link = form['link']
    client = form['client']
    server = form['server']
    note = form['internal_note']

    print(client)
    print(server)

    return Response(status=204)

@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html", error=e), 404

@app.route("/clients/<id>", methods=["GET", "POST"])
def clients(id):
    if "key" in session and session["key"] in app.sessions:
        # Valid session, refresh token
        app.sessions[session["key"]] = datetime.utcnow()
    else:
        # New or invalid session, send to login
        return redirect(url_for("login"))

    try:
        packs = Client.get_by_id(id).get_allowed_modpacks()
    except connector.ProgrammingError as e:
        Database.create_tables()
        packs = []

    return render_template("clients.html", clients=packs)

if __name__ == "__main__":
    app.run(debug=debug, use_reloader=False, host=host, port=port)

if __name__ == "__app__":
    app.run(debug=False, use_reloader=False, host=host, port=port)
