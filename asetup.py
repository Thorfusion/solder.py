from flask import Blueprint, flash, redirect, render_template, request, url_for

from models.database import Database
from models.session import Session
from models.user import User
from models.common import migratetechnic, new_user, DB_IS_UP

asetup = Blueprint("asetup", __name__)

if DB_IS_UP != 2:
    if migratetechnic is True or new_user is True:
        Database.create_session_table()
    if DB_IS_UP == 0:
        Database.create_tables()
        Session.start_session_loop()

@asetup.route("/setup", methods=["GET"])
def setup():
    if Database.is_setup() == 2:
        flash("An error occurred whilst trying to check database connection", "error")
        return render_template("setup.html")
    if DB_IS_UP == 0:
        Database.create_tables()
        Session.start_session_loop()
    if new_user == True or User.any_user_exists() == False:
        if migratetechnic:
            Database.migratetechnic_tables()
            return render_template("setup.html")
        return render_template("setup.html")
    else:
        return redirect(url_for('alogin.login'))


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
                    request.form["setuppassword"], request.remote_addr, '1', True)
            flash("user added", "success")
            return redirect(url_for('alogin.login'))
    return render_template("setup.html")