"""Persistent login throttling shared by all management workers."""

import datetime
import hashlib

from .database import Database


class LoginThrottle:
    WINDOW_MINUTES = 15
    BLOCK_MINUTES = 15
    ACCOUNT_LIMIT = 5
    ADDRESS_LIMIT = 30

    @staticmethod
    def _key(scope, value):
        payload = f"{scope}\0{str(value or '').strip().casefold()}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def _entries(cls, username, address):
        return (
            # Keep the account limit independent of the source address so an
            # attacker cannot reset the password-guess budget by rotating IPs.
            (cls._key("account", username), cls.ACCOUNT_LIMIT),
            (cls._key("address", address), cls.ADDRESS_LIMIT),
        )

    @classmethod
    def retry_after(cls, username, address):
        conn = Database.get_connection()
        if conn is None:
            return 0
        cur = conn.cursor(dictionary=True)
        try:
            keys = [key for key, _limit in cls._entries(username, address)]
            cur.execute(
                """SELECT blocked_until, NOW() AS checked_at
                   FROM login_attempts
                   WHERE attempt_key IN (%s, %s)
                     AND blocked_until > NOW()""",
                tuple(keys),
            )
            delays = [
                max(1, int((row["blocked_until"] - row["checked_at"]).total_seconds()))
                for row in cur.fetchall() or []
            ]
            return max(delays, default=0)
        finally:
            cur.close()
            conn.close()

    @classmethod
    def failure(cls, username, address):
        conn = Database.get_connection()
        if conn is None:
            return 0
        cur = conn.cursor(dictionary=True)
        try:
            cur.execute(
                "DELETE FROM login_attempts WHERE last_attempt < NOW() - INTERVAL 1 DAY"
            )
            cur.execute("SELECT NOW() AS checked_at")
            now = cur.fetchone()["checked_at"]
            for key, limit in cls._entries(username, address):
                cur.execute(
                    """SELECT attempts, first_attempt
                       FROM login_attempts
                       WHERE attempt_key = %s FOR UPDATE""",
                    (key,),
                )
                row = cur.fetchone()
                if row is None or (
                    now - row["first_attempt"]
                ).total_seconds() > cls.WINDOW_MINUTES * 60:
                    attempts = 1
                    first_attempt = now
                else:
                    attempts = int(row["attempts"]) + 1
                    first_attempt = row["first_attempt"]
                blocked_until = (
                    now + datetime.timedelta(minutes=cls.BLOCK_MINUTES)
                    if attempts >= limit
                    else None
                )
                cur.execute(
                    """INSERT INTO login_attempts
                              (attempt_key, attempts, first_attempt,
                               blocked_until, last_attempt)
                       VALUES (%s, %s, %s, %s, %s)
                       ON DUPLICATE KEY UPDATE
                           attempts = VALUES(attempts),
                           first_attempt = VALUES(first_attempt),
                           blocked_until = VALUES(blocked_until),
                           last_attempt = VALUES(last_attempt)""",
                    (key, attempts, first_attempt, blocked_until, now),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()
        return cls.retry_after(username, address)

    @classmethod
    def success(cls, username, address):
        conn = Database.get_connection()
        if conn is None:
            return
        cur = conn.cursor()
        try:
            # A successful login proves ownership of the account, so clear its
            # account-specific failures. Retain the address budget because it
            # may include attempts against other accounts.
            key = cls._entries(username, address)[0][0]
            cur.execute(
                "DELETE FROM login_attempts WHERE attempt_key = %s",
                (key,),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()
