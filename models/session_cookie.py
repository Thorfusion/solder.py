"""Management session-cookie lifetime and request-scheme policy."""

from datetime import timedelta

from flask import request
from flask.sessions import SecureCookieSessionInterface


def resolve_session_idle_timeout(value):
    """Return the configured idle timeout, defaulting to 24 hours."""
    if not value:
        return timedelta(hours=24)
    try:
        hours = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("SESSION_IDLE_TIMEOUT_HOURS must be an integer") from error
    if hours < 1:
        raise ValueError("SESSION_IDLE_TIMEOUT_HOURS must be at least 1")
    return timedelta(hours=hours)


class RequestSchemeSessionInterface(SecureCookieSessionInterface):
    """Use HTTPS-only cookies automatically unless explicitly overridden."""

    def get_cookie_secure(self, app):
        configured = app.config.get("SESSION_COOKIE_SECURE")
        if configured is None:
            return request.is_secure
        return bool(configured)
