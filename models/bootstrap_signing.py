"""Installation-wide signatures for SolderPy Modpack Loader manifests."""

import base64
import hashlib
import json
from threading import RLock

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .database import Database


class BootstrapSigningError(RuntimeError):
    """Raised when the installation signing key is missing or invalid."""


class BootstrapSigning:
    """Sign bootstrap manifests with one private key shared by the instance."""

    SETTING_NAME = "bootstrap_signing_private_key"
    ALGORITHM = "SHA256withECDSA"
    CURVE = "secp256r1"
    PUBLIC_KEY_FORMAT = "X.509"
    ENCODING = "base64"

    _private_key = None
    _lock = RLock()

    @staticmethod
    def _encode_private_key(private_key) -> str:
        encoded = private_key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        return base64.b64encode(encoded).decode("ascii")

    @classmethod
    def ensure_key(cls, cursor) -> None:
        """Create the site-wide key once during writable schema repair."""
        cursor.execute(
            "SELECT value FROM solder_settings WHERE name = %s",
            (cls.SETTING_NAME,),
        )
        row = cursor.fetchone()
        if row is not None:
            cls._decode_private_key(row[0])
            return

        private_key = ec.generate_private_key(ec.SECP256R1())
        value = cls._encode_private_key(private_key)
        cursor.execute(
            """INSERT IGNORE INTO solder_settings (name, value)
               VALUES (%s, %s)""",
            (cls.SETTING_NAME, value),
        )

    @classmethod
    def _decode_private_key(cls, value):
        try:
            encoded = base64.b64decode(str(value), validate=True)
            private_key = serialization.load_der_private_key(
                encoded, password=None
            )
        except (TypeError, ValueError) as error:
            raise BootstrapSigningError(
                "The stored bootstrap signing key is invalid."
            ) from error
        if not isinstance(private_key, ec.EllipticCurvePrivateKey):
            raise BootstrapSigningError(
                "The stored bootstrap signing key has the wrong type."
            )
        if private_key.curve.name != cls.CURVE:
            raise BootstrapSigningError(
                "The stored bootstrap signing key uses an unsupported curve."
            )
        return private_key

    @classmethod
    def private_key(cls):
        """Load the private key internally; it is never serialized to an API."""
        with cls._lock:
            if cls._private_key is not None:
                return cls._private_key

            connection = Database.get_connection()
            if connection is None:
                raise BootstrapSigningError(
                    "The bootstrap signing key could not be loaded."
                )
            cursor = None
            try:
                cursor = connection.cursor(dictionary=True)
                cursor.execute(
                    "SELECT value FROM solder_settings WHERE name = %s",
                    (cls.SETTING_NAME,),
                )
                row = cursor.fetchone()
            except Exception as error:
                raise BootstrapSigningError(
                    "The bootstrap signing key could not be loaded."
                ) from error
            finally:
                if cursor is not None:
                    cursor.close()
                connection.close()
            if not row:
                raise BootstrapSigningError(
                    "The bootstrap signing key has not been generated."
                )
            cls._private_key = cls._decode_private_key(row["value"])
            return cls._private_key

    @classmethod
    def public_config(cls) -> dict:
        """Return the public verification material embedded in exports."""
        public_key = cls.private_key().public_key()
        encoded = public_key.public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return {
            "required": True,
            "algorithm": cls.ALGORITHM,
            "curve": cls.CURVE,
            "publicKeyFormat": cls.PUBLIC_KEY_FORMAT,
            "encoding": cls.ENCODING,
            "keyId": "sha256:" + hashlib.sha256(encoded).hexdigest(),
            "publicKey": base64.b64encode(encoded).decode("ascii"),
        }

    @staticmethod
    def canonical_payload(manifest: dict) -> bytes:
        """Serialize every response field except the detached signature."""
        unsigned = dict(manifest)
        unsigned.pop("signature", None)
        return json.dumps(
            unsigned,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")

    @classmethod
    def sign_manifest(cls, manifest: dict) -> dict:
        """Attach an ECDSA signature without exposing private key material."""
        verification = cls.public_config()
        signature = cls.private_key().sign(
            cls.canonical_payload(manifest),
            ec.ECDSA(hashes.SHA256()),
        )
        manifest["signature"] = {
            "algorithm": verification["algorithm"],
            "key_id": verification["keyId"],
            "encoding": verification["encoding"],
            "value": base64.b64encode(signature).decode("ascii"),
        }
        return manifest

    @classmethod
    def verify_manifest(cls, manifest: dict, public_key_base64: str) -> bool:
        """Reference verifier used by tests and client implementers."""
        signature = manifest.get("signature") or {}
        try:
            encoded_public_key = base64.b64decode(
                public_key_base64, validate=True
            )
            expected_key_id = (
                "sha256:" + hashlib.sha256(encoded_public_key).hexdigest()
            )
            if (
                signature.get("algorithm") != cls.ALGORITHM
                or signature.get("encoding") != cls.ENCODING
                or signature.get("key_id") != expected_key_id
            ):
                return False
            public_key = serialization.load_der_public_key(encoded_public_key)
            value = base64.b64decode(signature["value"], validate=True)
            public_key.verify(
                value,
                cls.canonical_payload(manifest),
                ec.ECDSA(hashes.SHA256()),
            )
        except (InvalidSignature, KeyError, TypeError, ValueError):
            return False
        return True
