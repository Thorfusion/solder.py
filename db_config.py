# Module Imports
from multiprocessing import AuthenticationError
import mariadb
import sys
def connect():
        try:
                conn = mariadb.connect(
                        user="testuser",
                        password="password1",
                        host="127.0.0.1",
                        port=3306,
                        database="solder"

                )   
        except mariadb.Error as e:
                print("-"*70+"\n Error connecting to database \n"+"-"*70+str(e))
        return conn

def select_all_mods():
        conn=connect()
        cur = conn.cursor()
        cur.execute("SELECT * FROM mods ORDER ASC")
        mods = []
        for (id, name, description, author, link, created_at, created_at , created_at) in cur:
                mods.append({
                 "id": id,
                 "name": name,
                 "desc": description,
                 "author": author,
                 "link": link})
        conn.close()
        return mods

def add_modversion(mod_id):
        conn=connect()
        cur = conn.cursor()
        sql=("INSERT INTO modversions(mod_id,version) VALUES=?")

        conn.close()
        return mods
