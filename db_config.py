

import mariadb
from datetime import datetime

def message( message, e=""):
        print("-"*70+"\n"+str(message)+"\n" + str(e) + "+\n"+"-"*70)

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
        cur.execute("SELECT * FROM mods ORDER BY id ASC")
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
def select_mod(id):
        conn=connect()
        cur = conn.cursor()
        sql=("SELECT * FROM mods WHERE id=?")
        try:
                cur.execute(sql,([id]))
                return cur.fetchall()[0][1]
        except Exception as e:
                message("feil ved å hente enkel mod", e)
       

def add_modversion_db(mod_id,version,hash,filesize):
        conn=connect()
        cur = conn.cursor()
        sql=("INSERT INTO modversions(mod_id,version, md5,created_at,updated_at, filesize ) VALUES(?,?,?,?,?,?)")
        try:
                cur.execute(sql,(mod_id, version, hash,datetime.now(),datetime.now(), filesize))
                conn.commit()
        except Exception as e:
                message("en feil oppstod ved å legge inn ny modverisjon",e)
        conn.close()
        
