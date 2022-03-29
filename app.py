from db_config import select_all_mods
from flask import Flask, render_template, url_for

app=Flask(__name__)
@app.route("/")
def index():
        
        mods=select_all_mods();
        
        return render_template("index.html",mods=mods )

@app.errorhandler(404)
def page_not_found(e):
  return render_template('404.html',error=e), 404
if __name__ == "__main__":
        app.run(debug=True) # endre denne når nettsiden skal ut på nett