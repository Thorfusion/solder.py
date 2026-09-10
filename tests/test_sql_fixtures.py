import re
import unittest
from pathlib import Path


FIXTURES = Path(__file__).parent / "fixtures"


class SanitizedSqlFixtureTests(unittest.TestCase):
    def test_fixtures_contain_only_synthetic_data_rows(self):
        expected_insert_counts = {
            "technic_solder.sql": 10,
            "solderpy.sql": 12,
        }

        for name, expected_count in expected_insert_counts.items():
            with self.subTest(fixture=name):
                contents = (FIXTURES / name).read_text(encoding="utf-8")
                schema, separator, synthetic_data = contents.partition(
                    "-- Synthetic CI records. These values are deliberately non-production."
                )

                self.assertTrue(separator)
                self.assertIn("SANITIZED TEST FIXTURE", schema)
                self.assertNotIn("INSERT INTO", schema)
                self.assertEqual(synthetic_data.count("INSERT INTO"), expected_count)
                self.assertIn("ci-user@example.invalid", synthetic_data)
                self.assertIn("ci-api-key-not-a-secret", synthetic_data)
                self.assertIn("ci-client-id-not-a-secret", synthetic_data)

                urls = re.findall(r"https?://[^'\s]+", synthetic_data)
                self.assertTrue(urls)
                self.assertTrue(
                    all(url.startswith("https://example.invalid/") for url in urls)
                )

                emails = re.findall(
                    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
                    synthetic_data,
                )
                self.assertEqual(emails, ["ci-user@example.invalid"])

    def test_source_auto_increment_values_are_normalized(self):
        for fixture in FIXTURES.glob("*.sql"):
            with self.subTest(fixture=fixture.name):
                contents = fixture.read_text(encoding="utf-8")
                counters = re.findall(r"AUTO_INCREMENT=(\d+)", contents)
                self.assertTrue(all(counter == "1" for counter in counters))


if __name__ == "__main__":
    unittest.main()
