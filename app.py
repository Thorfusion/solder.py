import secrets

from api import api, solderpy_version
from alogin import alogin
from asetup import asetup
from asite import asite
from flask import Flask, jsonify, render_template, request
from models.common import debug, host, port, api_only, management_only, migratetechnic, new_user, DB_IS_UP, reverse_proxy, write_api
from werkzeug.middleware.proxy_fix import ProxyFix

__version__ = solderpy_version

app: Flask = Flask(__name__)
app.json.sort_keys = False

if reverse_proxy:
    app.wsgi_app = ProxyFix(
        app.wsgi_app, x_for=1
    )

if management_only == False or DB_IS_UP != 2:
    app.register_blueprint(api)
if write_api and DB_IS_UP == 1:
    from api_write import write_api_blueprint

    app.register_blueprint(write_api_blueprint)
if not api_only:
    if migratetechnic is True or new_user is True or DB_IS_UP != 1:
        app.register_blueprint(asetup)
    if DB_IS_UP != 2:
        # Note that asite must be after setup
        app.register_blueprint(alogin)
        app.register_blueprint(asite)

    app.secret_key = secrets.token_hex()

@app.errorhandler(404)
def page_not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not Found"}), 404
    return render_template("404.html", error=e), 404


@app.errorhandler(405)
def method_not_allowed(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Method Not Allowed"}), 405
    return render_template("404.html", error=e), 405

if __name__ == "__main__":
    app.run(debug=debug, use_reloader=False, host=host, port=port)
