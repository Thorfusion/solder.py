import csv
import io
import unittest
from unittest.mock import MagicMock, patch

from tests.environment import configure_test_environment


configure_test_environment()

from models.build_export import BuildCsvExport  # noqa: E402


class BuildCsvExportTests(unittest.TestCase):
    def test_load_uses_technic_columns_and_render_quotes_values(self):
        connection = MagicMock()
        cursor = connection.cursor.return_value
        cursor.fetchone.return_value = {
            "id": 7,
            "version": "2.0",
            "modpack_slug": "example-pack",
        }
        cursor.fetchall.return_value = [
            {
                "mod_name": "Example, Mod",
                "mod_slug": "example-mod",
                "version": "1.2.3",
                "md5": "a" * 32,
                "filesize": 123,
            }
        ]

        with patch(
            "models.build_export.Database.get_connection",
            return_value=connection,
        ):
            build, rows = BuildCsvExport.load(7)

        output = BuildCsvExport.render(rows).read().decode("utf-8")
        parsed = list(csv.reader(io.StringIO(output)))
        self.assertEqual(build.modpack_slug, "example-pack")
        self.assertEqual(
            parsed[0],
            ["mod_name", "mod_slug", "version", "md5", "filesize"],
        )
        self.assertEqual(parsed[1][0], "Example, Mod")
        self.assertEqual(parsed[1][-1], "123")
        self.assertEqual(cursor.execute.call_count, 2)
        cursor.close.assert_called_once_with()
        connection.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
