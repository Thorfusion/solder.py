from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from models.session import Session
from models.user import User
from models.login_throttle import LoginThrottle
from models.passhasher import Passhasher

alogin = Blueprint("alogin", __name__)


def _already_logged_in():
    token = session.get("token")
    return bool(token and Session.verify_session(token, request.remote_addr))

@alogin.route("/login", methods=["GET"])
def login_page():
    if _already_logged_in():
        # Already logged in
        return redirect(url_for('asite.index'))

    return render_template("login.html", failed=False)


@alogin.route("/login", methods=["POST"])
def login():
    if _already_logged_in():
        # Already logged in
        return redirect(url_for('asite.index'))

    username = request.form.get("username", "")
    password = request.form.get("password", "")
    retry_after = LoginThrottle.retry_after(username, request.remote_addr)
    if retry_after:
        response = render_template("login.html", failed=True)
        return response, 429, {"Retry-After": str(retry_after)}
    user = User.get_by_username(username)
    if user is None:
        # Keep unknown-user failures comparable to an Argon2 password check.
        Passhasher.verify_dummy(password)
        retry_after = LoginThrottle.failure(username, request.remote_addr)
        flash("Login Failed!", "error")
        status = 429 if retry_after else 200
        headers = {"Retry-After": str(retry_after)} if retry_after else {}
        return render_template("login.html", failed=True), status, headers
    else:
        if user.verify_password(password):
            LoginThrottle.success(username, request.remote_addr)
            # if new_user:
            # return render_template("login.html", failed=True)
            session["token"] = Session.new_session(
                request.remote_addr, User.get_userid(username)
            )
            return redirect(url_for('asite.index'))
        else:
            retry_after = LoginThrottle.failure(username, request.remote_addr)
            flash("Login Failed!", "error")
            status = 429 if retry_after else 200
            headers = {"Retry-After": str(retry_after)} if retry_after else {}
            return render_template("login.html", failed=True), status, headers
