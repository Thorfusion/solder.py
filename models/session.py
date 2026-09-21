from __future__ import annotations

import secrets
import threading
import time
from datetime import datetime

from flask import flash, g, has_request_context

from .database import Database


class Session:
    running: bool = False
    thread: threading.Thread = None

    def __init__(
        self,
        token: str,
        ip,
        expiry: datetime,
        user_id=None,
        night_mode=False,
    ):
        self.token = token
        self.ip = ip
        self.expiry = expiry
        self.user_id = user_id
        self.night_mode = bool(night_mode)

    @staticmethod
    def ip_to_int(ip: str) -> int:
        return int(ip.replace('.', ''))

    @classmethod
    def get_and_update_from_token(cls, token: str) -> Session:
        conn = Database.get_connection()
        if conn is None:
            return None
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT sessions.token, sessions.ip, sessions.expiry, "
                "sessions.user_id, users.night_mode FROM sessions "
                "INNER JOIN users ON users.id = sessions.user_id "
                "WHERE sessions.token = %s AND sessions.expiry > NOW()",
                (token,),
            )
            stored_session = cur.fetchone()
            if stored_session is None:
                return None
            cur.execute(
                "UPDATE sessions SET expiry = DATE_ADD(NOW(), INTERVAL 1 DAY) "
                "WHERE token = %s AND expiry > NOW()",
                (token,),
            )
            conn.commit()
            return cls(
                stored_session[0],
                stored_session[1],
                stored_session[2],
                stored_session[3],
                stored_session[4],
            )
        finally:
            cur.close()
            conn.close()
        
    @staticmethod
    def get_user_id(token: str):
        if (
            has_request_context()
            and getattr(g, "solder_session_token", None) == token
        ):
            return g.solder_user_id
        conn = Database.get_connection()
        if conn is None:
            return 0
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                "SELECT user_id FROM sessions "
                "WHERE token = %s AND expiry > NOW()",
                (token,),
            )
            row = cur.fetchone()
            if row is not None:
                return row["user_id"]
            flash("could not fetch user id", "error")
            return 0
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def new_session(ip, user):
        token = secrets.token_hex(40)
        conn = Database.get_connection()
        cur = conn.cursor()
        try:
            # Users commonly share one VPN address. Replace only this user's
            # previous session instead of logging out everyone behind the VPN.
            cur.execute("DELETE FROM sessions WHERE user_id = %s", (user,))
            cur.execute(
                "INSERT INTO sessions (token, ip, expiry, user_id) "
                "VALUES (%s, %s, DATE_ADD(NOW(), INTERVAL 1 HOUR), %s)",
                (token, ip, user),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()
        return token

    @staticmethod
    def delete_session(token: str):
        conn = Database.get_connection()
        cur = conn.cursor()
        try:
            cur.execute("DELETE FROM sessions WHERE token = %s", (token,))
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def verify_session(token, ip):
        stored_session = Session.get_and_update_from_token(token)
        if stored_session and stored_session.ip == ip:
            if has_request_context():
                g.solder_session_token = token
                g.solder_user_id = stored_session.user_id
                g.solder_night_mode = stored_session.night_mode
            return True
        return False

    def __eq__(self, other):
        if isinstance(other, Session):
            return (
                self.token == other.token
                and self.ip == other.ip
                and self.expiry == other.expiry
                and self.user_id == other.user_id
                and self.night_mode == other.night_mode
            )
        return False

    @staticmethod
    def session_loop():
        while Session.running:
            conn = Database.get_connection()
            if conn is not None:
                cur = conn.cursor()
                try:
                    cur.execute("DELETE FROM `sessions` WHERE expiry < NOW()")
                    conn.commit()
                finally:
                    cur.close()
                    conn.close()
            time.sleep(3600)

    @staticmethod
    def start_session_loop():
        if not Session.running:
            Session.running = True
            Session.thread = threading.Thread(
                target=Session.session_loop,
                name="solder-session-cleanup",
                daemon=True,
            )
            Session.thread.start()

    @staticmethod
    def stop_session_loop():
        Session.running = False
        if Session.thread is not None:
            Session.thread.join(timeout=1)
