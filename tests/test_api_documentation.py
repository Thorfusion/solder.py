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

if __name__ == "__main__":
    unittest.main()
