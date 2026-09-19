import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ApiDocumentationTests(unittest.TestCase):
    def test_json_examples_are_valid(self):
        documentation = "\n".join(
            (ROOT / "docs" / name).read_text(encoding="utf-8")
            for name in ("api.md", "bootstrap-api.md", "write-api.md")
        )
        examples = re.findall(r"```json\n(.*?)\n```", documentation, re.DOTALL)

        self.assertGreater(len(examples), 0)
        for number, example in enumerate(examples, start=1):
            with self.subTest(example=number):
                json.loads(example)

    def test_read_api_routes_are_documented(self):
        documentation = (ROOT / "docs" / "api.md").read_text(encoding="utf-8")
        documented_routes = (
            "/api/",
            "/api/verify",
            "/api/verify/{key}",
            "/api/modpack",
            "/api/modpack/{slug}",
            "/api/modpack/{slug}/{build}",
            "/api/modpack/{slug}/{build}/bootstrap",
            "/api/mod",
            "/api/mod/{name}",
            "/api/mod/{name}/{version}",
        )

        for route in documented_routes:
            with self.subTest(route=route):
                self.assertIn(route, documentation)

    def test_readme_links_to_api_reference(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("[API reference](docs/api.md)", readme)
        self.assertIn("[write API reference](docs/write-api.md)", readme)

    def test_github_config_guide_documents_versioning_and_ignore_rules(self):
        documentation = (ROOT / "docs" / "github-config.md").read_text(
            encoding="utf-8"
        )

        self.assertIn("Tagged versions", documentation)
        self.assertIn("Manual versions", documentation)
        self.assertIn("`.solderpyignore`", documentation)
        self.assertIn("Submodules", documentation)

    def test_write_api_routes_are_documented(self):
        documentation = (ROOT / "docs" / "write-api.md").read_text(
            encoding="utf-8"
        )
        routes = (
            "/api/token",
            "/api/modpack",
            "/api/modpack/{slug}/clone",
            "/api/modpack/{slug}/build",
            "/api/modpack/{slug}/{version}/mod",
            "/api/mod",
            "/api/mod/{slug}/version",
            "/api/client",
            "/api/integration/modrinth/search",
            "/api/integration/modrinth/mod",
            "/api/integration/maven/repository",
            "/api/integration/maven/artifact",
            "/api/mod/{slug}/{version}/mcil-jar",
        )
        for route in routes:
            with self.subTest(route=route):
                self.assertIn(route, documentation)


if __name__ == "__main__":
    unittest.main()
