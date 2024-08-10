import secrets

from api import api, solderpy_version
from alogin import alogin
from asetup import asetup
from asite import asite
from flask import Flask, render_template
from models.globals import debug, host, port

__version__ = solderpy_version

app: Flask = Flask(__name__)
app.register_blueprint(api)
app.register_blueprint(alogin)
app.register_blueprint(asetup)
app.register_blueprint(asite)

app.secret_key = secrets.token_hex()

@app.errorhandler(404)
def page_not_found(e):
    return render_template("404.html", error=e), 404

if __name__ == "__main__":
    app.run(debug=debug, use_reloader=False, host=host, port=port)
