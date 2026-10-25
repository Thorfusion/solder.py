"""Session-cookie policy that follows the management request scheme."""

from flask import request
from flask.sessions import SecureCookieSessionInterface


class RequestSchemeSessionInterface(SecureCookieSessionInterface):
    """Use HTTPS-only cookies automatically unless explicitly overridden."""

    def get_cookie_secure(self, app):
        configured = app.config.get("SESSION_COOKIE_SECURE")
        if configured is None:
            return request.is_secure
        return bool(configured)
