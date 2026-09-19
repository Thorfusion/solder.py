from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from models.session import Session
from models.user import User

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
    user = User.get_by_username(username)
    if user is None:
        flash("Login Failed!", "error")
        return render_template("login.html")
    else:
        if user.verify_password(password):
            # if new_user:
            # return render_template("login.html", failed=True)
            session["token"] = Session.new_session(
                request.remote_addr, User.get_userid(username)
            )
            return redirect(url_for('asite.index'))
        else:
            flash("Login Failed!", "error")
            return render_template("login.html")
