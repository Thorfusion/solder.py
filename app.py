from concurrent.futures import thread
import os
from zipfile import ZipFile
from dotenv import load_dotenv

from flask import Flask, redirect, render_template, request, request_started, url_for
from flask import session, request
from werkzeug.utils import secure_filename

import secrets
import hashlib

from db_config import add_modversion_db, select_all_mods, select_all_modpacks, select_mod, init_db, get_user_info
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
    Hashes a password with a salt. Uses sha512
    :param pw: Password to hash
    :param salt: Salt to hash with. This should be the username
    :return: Hashed password
    """
    return hashlib.sha512((pw + salt).encode("utf-8")).hexdigest()

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

    return render_template("modversion.html", modSlug=modSlug, name=name, size=size)

@app.route("/newmod")
def newmod():
    if "key" in session and session["key"] in app.sessions:
        # Valid session, refresh token
        app.sessions[session["key"]] = datetime.utcnow()
    else:
        # New or invalid session, send to login
        return redirect(url_for("login"))

    return render_template("newmod.html")

@app.route("/newmodpack")
def newmodpack():
    if "key" in session and session["key"] in app.sessions:
        # Valid session, refresh token
        app.sessions[session["key"]] = datetime.utcnow()
    else:
        # New or invalid session, send to login
        return redirect(url_for("login"))

    return render_template("newmodpack.html")

@app.route("/newmodpackbuild")
def newmodpackbuild():
    if "key" in session and session["key"] in app.sessions:
        # Valid session, refresh token
        app.sessions[session["key"]] = datetime.utcnow()
    else:
        # New or invalid session, send to login
        return redirect(url_for("login"))

    return render_template("newmodpackbuild.html")

@app.route("/modpackversion")
def modpackversion():
    if "key" in session and session["key"] in app.sessions:
        # Valid session, refresh token
        app.sessions[session["key"]] = datetime.utcnow()
    else:
        # New or invalid session, send to login
        return redirect(url_for("login"))

    return render_template("modpackversion.html")

@app.route("/editmodpack")
def editmodpack():
    if "key" in session and session["key"] in app.sessions:
        # Valid session, refresh token
        app.sessions[session["key"]] = datetime.utcnow()
    else:
        # New or invalid session, send to login
        return redirect(url_for("login"))

    return render_template("editmodpack.html")

@app.route("/editmodpackbuild")
def editmodpackbuild():
    if "key" in session and session["key"] in app.sessions:
        # Valid session, refresh token
        app.sessions[session["key"]] = datetime.utcnow()
    else:
        # New or invalid session, send to login
        return redirect(url_for("login"))

    return render_template("editmodpackbuild.html")

@app.route("/modpackbuild")
def modpackbuild():
    if "key" in session and session["key"] in app.sessions:
        # Valid session, refresh token
        app.sessions[session["key"]] = datetime.utcnow()
    else:
        # New or invalid session, send to login
        return redirect(url_for("login"))

    return render_template("modpackbuild.html")

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
        mods = select_all_mods()
    except connector.ProgrammingError as e:
        init_db()
        mods = []

    return render_template("modpacks.html", mods=mods)

@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html", error=e), 404


if __name__ == "__main__":
    app.run(debug=True, host=host, port=port)  # endre denne når nettsiden skal ut på nett
