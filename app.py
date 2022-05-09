import os
from zipfile import ZipFile
from dotenv import load_dotenv

from flask import Flask, render_template, request, request_started, url_for
from werkzeug.utils import secure_filename

from db_config import add_modversion_db, select_all_mods, select_mod, init_db
from mysql import connector

from api import api

load_dotenv(".env")
host = os.getenv("APP_URL")
port = os.getenv("APP_PORT")

app = Flask(__name__)
app.register_blueprint(api)

app.config["UPLOAD_FOLDER"] = "./mods/"

def createFolder(dirName):
        os.makedirs(dirName, exist_ok=True)


@app.route("/")
def index():
    try:
        mods = select_all_mods()
    except connector.ProgrammingError as e:
        init_db()
        mods = []
    return render_template("index.html", mods=mods)


@app.route("/addversion/<id>", methods=["GET", "POST"])
def addversion(id):
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

    return render_template("addversion.html", modSlug=modSlug, name=name, size=size)


@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html", error=e), 404


if __name__ == "__main__":
    app.run(debug=True, host=host, port=port)  # endre denne når nettsiden skal ut på nett
