import base64
import unittest
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from models.bootstrap_signing import BootstrapSigning


class BootstrapSigningTests(unittest.TestCase):
    def setUp(self):
        BootstrapSigning._private_key = None
        self.addCleanup(setattr, BootstrapSigning, "_private_key", None)

    def test_schema_repair_generates_one_installation_key(self):
        cursor = Mock()
        cursor.fetchone.return_value = None

        BootstrapSigning.ensure_key(cursor)

        insert = cursor.execute.call_args_list[1]
        self.assertIn("INSERT IGNORE INTO solder_settings", insert.args[0])
        self.assertEqual(insert.args[1][0], BootstrapSigning.SETTING_NAME)
        encoded = insert.args[1][1]
        self.assertLessEqual(len(encoded), 255)
        private_key = serialization.load_der_private_key(
            base64.b64decode(encoded), password=None
        )
        self.assertEqual(private_key.curve.name, "secp256r1")

    def test_existing_installation_key_is_not_replaced(self):
        cursor = Mock()
        existing = BootstrapSigning._encode_private_key(
            ec.generate_private_key(ec.SECP256R1())
        )
        cursor.fetchone.return_value = (existing,)

        BootstrapSigning.ensure_key(cursor)

        self.assertEqual(cursor.execute.call_count, 1)

    def test_signed_manifest_verifies_and_tampering_fails(self):
        BootstrapSigning._private_key = ec.generate_private_key(ec.SECP256R1())
        manifest = {
            "schema": "solder.py/bootstrap",
            "manifest_hash": "a" * 64,
            "packages": [{"name": "example", "version": "1.0"}],
        }

        BootstrapSigning.sign_manifest(manifest)
        verification = BootstrapSigning.public_config()

        self.assertEqual(
            manifest["signature"]["key_id"], verification["keyId"]
        )
        self.assertTrue(
            BootstrapSigning.verify_manifest(
                manifest, verification["publicKey"]
            )
        )
        manifest["packages"][0]["version"] = "tampered"
        self.assertFalse(
            BootstrapSigning.verify_manifest(
                manifest, verification["publicKey"]
            )
        )

    def test_private_key_is_read_from_internal_settings_only(self):
        private_key = ec.generate_private_key(ec.SECP256R1())
        value = BootstrapSigning._encode_private_key(private_key)
        connection = Mock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = {"value": value}

        with patch(
            "models.bootstrap_signing.Database.get_connection",
            return_value=connection,
        ):
            loaded = BootstrapSigning.private_key()

        self.assertEqual(
            loaded.private_numbers(), private_key.private_numbers()
        )
        query, parameters = cursor.execute.call_args.args
        self.assertIn("solder_settings", query)
        self.assertEqual(parameters, (BootstrapSigning.SETTING_NAME,))
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
