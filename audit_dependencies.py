"""Audit the locked production dependencies with pip-audit."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    requirements = subprocess.run(
        ["pipenv", "requirements"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    with tempfile.TemporaryDirectory(prefix="solder-pip-audit-") as directory:
        requirements_file = Path(directory, "requirements.txt")
        requirements_file.write_text(requirements, encoding="utf-8")
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip_audit",
                "--requirement",
                str(requirements_file),
            ],
            check=False,
        )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
