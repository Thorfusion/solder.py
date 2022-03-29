from db_config import add_modversion_db, select_all_mods
from flask import Flask, render_template, request, request_started, url_for

app=Flask(__name__)
@app.route("/")
def index():
        
        
        return render_template("index.html",mods=select_all_mods() )
@app.route("/addversion/<id>", methods=['GET','POST'])
def addversion(id):
        
        if request.method =='POST':
                #make check later (check if modid  exists in database)
                modver = request.form["modver"]
                if (modver!=""):
                        add_modversion_db(id,modver,"treed",112312)
                else:
                        print("error")
                        
                        
        return render_template("addversion.html" )
        
@app.errorhandler(404)
def page_not_found(e):
  return render_template('404.html',error=e), 404
if __name__ == "__main__":
        app.run(debug=True) # endre denne når nettsiden skal ut på nett