from __future__ import annotations

from datetime import datetime
import threading
import time
import secrets

from .database import Database

class Session:
    running: bool = False
    thread: threading.Thread = None
    def __init__(self, token: str, ip: int, expiry: datetime):
        self.token = token
        self.ip = ip
        self.expiry = expiry

    @staticmethod
    def ip_to_int(ip: str) -> int:
        return int(ip.replace('.', ''))

    @classmethod
    def get_from_token(cls, token: str) -> Session:
        conn = Database.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM sessions WHERE token = %s", (token,))
        session = cur.fetchone()
        if session:
            return cls(session[0], session[1], session[2])
        else:
            return None

    @classmethod
    def get_and_update_from_token(cls, token: str) -> Session:
        conn = Database.get_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM sessions WHERE token = %s", (token,))
        session = cur.fetchone()
        if session:
            cur.execute("UPDATE sessions SET expiry = DATE_ADD(NOW(), INTERVAL 1 DAY) WHERE token = %s", (token,))
            return cls(session[0], session[1], session[2])
        else:
            return None

    @staticmethod
    def new_session(ip: str) -> str:
        token = secrets.token_hex(40)
        conn = Database.get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM sessions WHERE ip = %s", (Session.ip_to_int(ip),))
        cur.execute("INSERT INTO sessions (token, ip, expiry) VALUES (%s, %s, DATE_ADD(NOW(), INTERVAL 1 HOURS))", (token, Session.ip_to_int(ip)))
        conn.commit()
        conn.close()
        return token

    @staticmethod
    def delete_session(token: str):
        conn = Database.get_connection()
        cur = conn.cursor()
        cur.execute("DELETE FROM sessions WHERE token = %s", (token,))
        conn.commit()
        conn.close()

    @staticmethod
    def verify_session(token, ip):
        session = Session.get_and_update_from_token(token)
        if session:
            if session.ip == Session.ip_to_int(ip):
                return True
        return False

    def __eq__(self, other):
        if isinstance(other, Session):
            return self.token == other.token and self.ip == other.ip and self.expiry == other.expiry
        return False

    @staticmethod
    def session_loop():
        while Session.running:
            conn = Database.get_connection()
            cur = conn.cursor()
            print("deleting sessions")
            cur.execute("DELETE FROM sessions WHERE expiry < NOW()")
            conn.commit()
            conn.close()
            time.sleep(30)

    @staticmethod
    def start_session_loop():
        if not Session.running:
            Session.running = True
            Session.thread = threading.Thread(target=Session.session_loop)
            Session.thread.start()

    @staticmethod
    def stop_session_loop():
        Session.running = False
        Session.thread.join()