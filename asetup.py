from flask import Blueprint, redirect, render_template, request, url_for

from models.database import Database
from models.user import User
from models.common import migratetechnic, new_user

asetup = Blueprint("asetup", __name__)

if migratetechnic:
    Database.create_session_table()

@asetup.route("/setup", methods=["GET"])
def setup():
    if not Database.is_setup():
        Database.create_tables()
    if new_user or not User.any_user_exists():
        if migratetechnic:
            Database.migratetechnic_tables()
            return render_template("setup.html")
        return render_template("setup.html")
    else:
        return redirect(url_for('asite.index'))


@asetup.route("/setup", methods=["POST"])
def setup_creation():
    if new_user or not User.any_user_exists():
        if request.form["setupemail"] is None:
            print("setup failed")
            return render_template("setup.html", failed=True)
        if request.form["setuppassword"] is None:
            print("setup failed")
            return render_template("setup.html", failed=True)
        User.new(request.form["setupemail"], request.form["setupemail"],
                 request.form["setuppassword"], request.remote_addr, '1')
        return redirect(url_for('asite.index'))