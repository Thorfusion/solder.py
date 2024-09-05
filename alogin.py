from flask import Blueprint, flash, redirect, render_template, request, session, url_for

from models.session import Session
from models.user import User

alogin = Blueprint("alogin", __name__)

@alogin.route("/login", methods=["GET"])
def login_page():
    if "key" in session and Session.verify_session(session["token"], request.remote_addr):
        # Already logged in
        return redirect(url_for('asite.index'))

    return render_template("login.html", failed=False)


@alogin.route("/login", methods=["POST"])
def login():
    if "key" in session and Session.verify_session(session["token"], request.remote_addr):
        # Already logged in
        return redirect(url_for('asite.index'))

    user = User.get_by_username(request.form["username"])
    if user is None:
        flash("Login Failed!", "error")
        return render_template("login.html")
    else:
        if user.verify_password(request.form["password"]):
            # if new_user:
            # return render_template("login.html", failed=True)
            session["token"] = Session.new_session(request.remote_addr, User.get_userid(request.form["username"]))
            return redirect(url_for('asite.index'))
        else:
            flash("Login Failed!", "error")
            return render_template("login.html")