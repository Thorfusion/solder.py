import os
from dotenv import load_dotenv

from flask import Flask, redirect, render_template, request, url_for
from flask import session, request
from werkzeug.utils import secure_filename

import secrets
import hashlib

from db_config import select_mod_versions, select_all_mods, select_all_modpacks_internal, select_mod, init_db, get_user_info, select_builds_from_modpack, select_mod_versions_from_build, select_all_clients, select_perms_from_client_modpack
from mysql import connector

from api import api

from datetime import datetime
import time
import threading

load_dotenv(".env")
host = os.getenv("APP_URL")
port = os.getenv("APP_PORT")

app: Flask = Flask(__name__)
app.register_blueprint(api)

app.config["UPLOAD_FOLDER"] = "./mods/"

app.secret_key = secrets.token_hex()
app.sessions = {}

def hasher(pw: str, salt: str) -> str:
    """
    Hashes a password with a salt. Uses blake2b
    :param pw: Password to hash
    :param salt: Salt to hash with. This should be the username
    :return: Hashed password
    """
    return hashlib.blake2b(pw.encode("UTF-8"), salt=hashlib.blake2b(salt.encode("UTF-8"), digest_size=16).digest()).hexdigest()

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
        user = get_user_info(request.form["username"])
        if user is None:
            return render_template("login.html", failed=True)
        else:
            if user["password"] == hasher(request.form["password"], user["username"]):
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

    modSlug = select_mod(id)
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
            # zipObj = ZipFile(modSlug+"-"+modver, 'w')

            # zipObj.write(jarfile.read())
            # zipObj.close()

            # add_modversion_db(id,modver,hash,size)

        else:
            print("error")

    try:
        modversions = select_mod_versions(id)
    except connector.ProgrammingError as e:
        init_db()
        modversions = []

    return render_template("modversion.html", modSlug=modSlug, name=name, size=size, modversions=modversions)

@app.route("/newmod")
def newmod():
    if "key" in session and session["key"] in app.sessions:
        # Valid session, refresh token
        app.sessions[session["key"]] = datetime.utcnow()
    else:
        # New or invalid session, send to login
        return redirect(url_for("login"))

    return render_template("newmod.html")

@app.route("/viewmodpack/<id>", methods=["GET", "POST"])
def viewmodpack(id):
    if "key" in session and session["key"] in app.sessions:
        # Valid session, refresh token
        app.sessions[session["key"]] = datetime.utcnow()
    else:
        # New or invalid session, send to login
        return redirect(url_for("login"))

    try:
        modpack = select_builds_from_modpack(id)
    except connector.ProgrammingError as e:
        init_db()
        modpack = []

    return render_template("viewmodpack.html", modpack=modpack)

@app.route("/mainsettings")
def mainsettings():
    if "key" in session and session["key"] in app.sessions:
        # Valid session, refresh token
        app.sessions[session["key"]] = datetime.utcnow()
    else:
        # New or invalid session, send to login
        return redirect(url_for("login"))

    try:
        clients = select_all_clients()
    except connector.ProgrammingError as e:
        init_db()
        clients = []

    return render_template("mainsettings.html", clients=clients)

@app.route("/modpackbuild/<id>", methods=["GET", "POST"])
def modpackbuild(id):
    if "key" in session and session["key"] in app.sessions:
        # Valid session, refresh token
        app.sessions[session["key"]] = datetime.utcnow()
    else:
        # New or invalid session, send to login
        return redirect(url_for("login"))

    try:
        modpackbuild = select_mod_versions_from_build(id)
    except connector.ProgrammingError as e:
        init_db()
        modpackbuild = []

    return render_template("modpackbuild.html", modpackbuild=modpackbuild)

@app.route("/modlibrary")
def modlibrary():
    if "key" in session and session["key"] in app.sessions:
        # Valid session, refresh token
        app.sessions[session["key"]] = datetime.utcnow()
    else:
        # New or invalid session, send to login
        return redirect(url_for("login"))

    try:
        mods = select_all_mods()
    except connector.ProgrammingError as e:
        init_db()
        mods = []

    return render_template("modlibrary.html", mods=mods)

@app.route("/modpacks")
def modpacks():
    if "key" in session and session["key"] in app.sessions:
        # Valid session, refresh token
        app.sessions[session["key"]] = datetime.utcnow()
    else:
        # New or invalid session, send to login
        return redirect(url_for("login"))

    try:
        modpacks = select_all_modpacks_internal()
    except connector.ProgrammingError as e:
        init_db()
        modpacks = []

    return render_template("modpacks.html", modpacks=modpacks)

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
        clients = select_perms_from_client_modpack(id)
    except connector.ProgrammingError as e:
        init_db()
        clients = []

    return render_template("clients.html", clients=clients)

if __name__ == "__main__":
    app.run(debug=True, host=host, port=port)  # endre denne når nettsiden skal ut på nett
