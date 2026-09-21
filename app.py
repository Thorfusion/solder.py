import secrets
import os

from api import api, solderpy_version
from alogin import alogin
from asetup import asetup
from asite import asite
from distribution_api import distribution_api
from flask import Flask, jsonify, render_template, request
from models.common import debug, host, port, api_only, management_only, migratetechnic, new_user, DB_IS_UP, reverse_proxy, write_api, session_cookie_secure
from models.csrf import CsrfProtection
from models.cache_revision import CacheRevision
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
    app.register_blueprint(distribution_api)
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

    # A configured key keeps management sessions valid across restarts and
    # multiple workers. The random fallback preserves zero-config development.
    app.secret_key = os.getenv("SECRET_KEY") or secrets.token_hex(32)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=session_cookie_secure,
    )

    @app.before_request
    def verify_management_csrf():
        if not app.testing:
            CsrfProtection.verify_request()

    @app.after_request
    def inject_management_csrf(response):
        if (
            not app.testing
            and
            request.blueprint == "asite"
            and request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and response.status_code < 400
        ):
            try:
                CacheRevision.bump()
            except Exception:
                app.logger.exception("Could not invalidate shared API caches")
        if not app.testing:
            return CsrfProtection.inject_forms(response)
        return response

@app.errorhandler(400)
def bad_request(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Bad Request"}), 400
    return render_template("404.html", error=e), 400

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
