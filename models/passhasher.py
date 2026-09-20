import hashlib
import hmac
import re
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from argon2.low_level import Type


_ARGON2 = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=1,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)
_LEGACY_BLAKE2 = re.compile(r"[0-9a-f]{128}", re.IGNORECASE)


class Passhasher:
    _dummy_hash = None

    def __init__(self, hash, salt):
        self.hash = hash
        self.salt = salt

    @classmethod
    def from_password(cls, password, salt):
        return cls(cls.hasher(password, salt), salt)

    def verify(self, password):
        valid, _replacement = self.verify_and_rehash(password)
        return valid

    def verify_and_rehash(self, password):
        """Return validity and an optional upgraded Argon2id hash."""
        stored = str(self.hash or "")
        password = str(password or "")

        if stored.startswith("$argon2"):
            try:
                _ARGON2.verify(stored, password)
            except (InvalidHashError, VerificationError):
                return False, None
            replacement = (
                self.hasher(password, self.salt)
                if _ARGON2.check_needs_rehash(stored)
                else None
            )
            return True, replacement

        if _LEGACY_BLAKE2.fullmatch(stored):
            expected = self.legacy_hasher(password, self.salt)
            if hmac.compare_digest(stored.casefold(), expected):
                return True, self.hasher(password, self.salt)
            self.verify_dummy(password)
            return False, None

        self.verify_dummy(password)
        return False, None

    def get_hash(self):
        return self.hash

    @staticmethod
    def hasher(pw: str, salt: str) -> str:
        """Create a self-contained Argon2id hash with a random salt."""
        return _ARGON2.hash(pw)

    @staticmethod
    def legacy_hasher(pw: str, salt: str) -> str:
        """Reproduce the solder.py 1.x hash solely for login migration."""
        # This is the established solder.py password format. Changing it would
        # invalidate existing accounts, so it is verified once and immediately
        # replaced with Argon2id after a successful login.
        return hashlib.blake2b(  # lgtm[py/weak-sensitive-data-hashing]
            pw.encode("UTF-8"),
            salt=hashlib.blake2b(
                salt.encode("UTF-8"), digest_size=16
            ).digest(),
        ).hexdigest()

    @classmethod
    def verify_dummy(cls, password):
        """Spend one Argon2 verification for nonexistent/unknown accounts."""
        if cls._dummy_hash is None:
            cls._dummy_hash = _ARGON2.hash(secrets.token_urlsafe(32))
        try:
            _ARGON2.verify(cls._dummy_hash, str(password or ""))
        except VerificationError:
            pass
