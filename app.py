import secrets

from api import api, solderpy_version
from alogin import alogin
from asetup import asetup
from asite import asite
from flask import Flask, render_template
from models.common import debug, host, port, api_only, management_only, migratetechnic, new_user
from models.database import Database

__version__ = solderpy_version

app: Flask = Flask(__name__)
if not management_only:
    app.register_blueprint(api)
if not api_only:
    app.register_blueprint(alogin)
    if migratetechnic is True or new_user is True or Database.is_setup() == 0:
        app.register_blueprint(asetup)
    if Database.is_setup() != 2:
        app.register_blueprint(asite)

    app.secret_key = secrets.token_hex()

@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html", error=e), 404

if __name__ == "__main__":
    app.run(debug=debug, use_reloader=False, host=host, port=port)
