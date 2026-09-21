"""Session-bound CSRF protection for management HTML forms."""

import hmac
import html
import re
import secrets

from flask import abort, request, session


_POST_FORM = re.compile(
    r"(<form\b(?=[^>]*\bmethod\s*=\s*['\"]?post\b)[^>]*>)",
    re.IGNORECASE,
)
_MANAGEMENT_BLUEPRINTS = frozenset({"asite", "alogin", "asetup"})


class CsrfProtection:
    SESSION_KEY = "_csrf_token"

    @classmethod
    def token(cls):
        value = session.get(cls.SESSION_KEY)
        if not value:
            value = secrets.token_urlsafe(32)
            session[cls.SESSION_KEY] = value
        return value

    @classmethod
    def verify_request(cls):
        if request.blueprint not in _MANAGEMENT_BLUEPRINTS:
            return
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return
        expected = session.get(cls.SESSION_KEY)
        supplied = request.form.get("_csrf_token") or request.headers.get(
            "X-CSRF-Token"
        )
        if not expected or not supplied or not hmac.compare_digest(
            str(expected), str(supplied)
        ):
            abort(400, description="The form expired or came from another site.")

    @classmethod
    def inject_forms(cls, response):
        if request.blueprint not in _MANAGEMENT_BLUEPRINTS:
            return response
        if not response.is_sequence or response.mimetype != "text/html":
            return response
        body = response.get_data(as_text=True)
        if "<form" not in body.casefold():
            return response
        field = (
            '<input type="hidden" name="_csrf_token" value="'
            + html.escape(cls.token(), quote=True)
            + '">'
        )
        body = _POST_FORM.sub(lambda match: match.group(1) + field, body)
        response.set_data(body)
        return response
