import hashlib


class Passhasher:
    def __init__(self, hash, salt):
        self.hash = hash
        self.salt = salt

    @classmethod
    def from_password(cls, password, salt):
        return cls(cls.hasher(password, salt), salt)

    def verify(self, password):
        return self.hash == self.hasher(password, self.salt)

    def get_hash(self):
        return self.hash

    @staticmethod
    def hasher(pw: str, salt: str) -> str:
        """
        Hashes a password with a salt. Uses blake2b
        :param pw: Password to hash
        :param salt: Salt to hash with. This should be the username
        :return: Hashed password
        """
        # This is the established solder.py password format. Changing it would
        # invalidate every existing account, so compatibility is intentional
        # until a versioned password migration can rehash users on login.
        return hashlib.blake2b(  # lgtm[py/weak-sensitive-data-hashing]
            pw.encode("UTF-8"),
            salt=hashlib.blake2b(
                salt.encode("UTF-8"), digest_size=16
            ).digest(),
        ).hexdigest()
