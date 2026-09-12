"""Boot a built solder.py image and verify its public health endpoint."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid


def docker(*arguments: str, **kwargs):
    return subprocess.run(["docker", *arguments], check=True, text=True, **kwargs)


def main(image: str) -> None:
    container = f"solderpy-smoke-{uuid.uuid4().hex}"
    started = False

    try:
        docker(
            "run",
            "--detach",
            "--name",
            container,
            "--publish",
            "127.0.0.1:0:5000",
            "--env",
            "AWS_EC2_METADATA_DISABLED=true",
            "--env",
            "DB_HOST=127.0.0.1",
            "--env",
            "DB_PORT=3306",
            "--env",
            "DB_USER=smoke-test",
            "--env",
            "DB_PASSWORD=smoke-test",
            "--env",
            "DB_DATABASE=smoke-test",
            "--env",
            "PUBLIC_REPO_LOCATION=https://example.test/mods/",
            "--env",
            "MD5_REPO_LOCATION=https://example.test/mods/",
            "--env",
            "R2_URL=https://example.test/mods/",
            "--env",
            "R2_ACCESS_KEY=smoke-test",
            "--env",
            "R2_SECRET_KEY=smoke-test",
            "--env",
            "R2_REGION=auto",
            image,
            capture_output=True,
        )
        started = True

        port_mapping = subprocess.check_output(
            ["docker", "port", container, "5000/tcp"], text=True
        ).strip()
        port = port_mapping.rsplit(":", 1)[1]
        endpoint = f"http://127.0.0.1:{port}/api/"
        deadline = time.monotonic() + 45
        last_error: Exception | None = None

        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(endpoint, timeout=3) as response:
                    payload = json.load(response)
                if (
                    response.status == 200
                    and payload.get("api") == "solder.py"
                    and payload.get("capabilities", {}).get("write_api") is False
                ):
                    print(f"Container smoke test passed: {payload}")
                    return
                last_error = RuntimeError(
                    f"Unexpected response status/payload: {response.status} {payload}"
                )
            except (OSError, ValueError, urllib.error.URLError) as error:
                last_error = error

            running = subprocess.check_output(
                ["docker", "inspect", "--format", "{{.State.Running}}", container],
                text=True,
            ).strip()
            if running != "true":
                raise RuntimeError(f"Container exited before becoming ready: {last_error}")
            time.sleep(1)

        raise RuntimeError(f"Container did not become ready: {last_error}")
    finally:
        if started:
            logs = subprocess.run(
                ["docker", "logs", container],
                check=False,
                capture_output=True,
                text=True,
            )
            if logs.stdout:
                print(logs.stdout)
            if logs.stderr:
                print(logs.stderr, file=sys.stderr)
            subprocess.run(
                ["docker", "rm", "--force", container],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(f"Usage: {sys.argv[0]} IMAGE")
    main(sys.argv[1])
