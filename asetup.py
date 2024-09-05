from flask import Blueprint, flash, redirect, render_template, request, url_for

from models.database import Database
from models.user import User
from models.common import migratetechnic, new_user

asetup = Blueprint("asetup", __name__)

if Database.is_setup() != 2:
    if migratetechnic is True or new_user is True or Database.is_setup() == 0:
        Database.create_session_table()

@asetup.route("/setup", methods=["GET"])
def setup():
    if Database.is_setup() == 2:
        flash("An error occurred whilst trying to check database connection", "error")
        return render_template("setup.html")
    if Database.is_setup() == 0:
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
    if Database.is_setup() != 2:
        if new_user or not User.any_user_exists():
            if request.form["setupemail"] is None:
                print("setup failed")
                flash("setup failed due to missing email", "error")
                return render_template("setup.html")
            if request.form["setuppassword"] is None:
                print("setup failed")
                flash("setup failed due to missing password", "error")
                return render_template("setup.html")
            User.new(request.form["setupemail"], request.form["setupemail"],
                    request.form["setuppassword"], request.remote_addr, '1')
            return redirect(url_for('asite.index'))
    return render_template("setup.html")