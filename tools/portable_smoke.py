#!/usr/bin/env python3
"""Verify a runtime-only copy starts without site packages, local tools, or user data."""

import base64
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def main():
    """Build a clean temporary runtime, verify hosted boundaries, and shut it down."""
    with tempfile.TemporaryDirectory(prefix="orchestration-portable-") as temporary:
        directory = Path(temporary)
        shutil.copy2(ROOT / "app.py", directory / "app.py")
        shutil.copytree(
            ROOT / "orchestration",
            directory / "orchestration",
            ignore=shutil.ignore_patterns("__pycache__"),
        )
        (directory / "frontend").mkdir()
        for name in ("index.html", "styles.css", "favicon.svg"):
            shutil.copy2(ROOT / "frontend" / name, directory / "frontend" / name)
        for name in ("src", "styles"):
            shutil.copytree(ROOT / "frontend" / name, directory / "frontend" / name)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        password = secrets.token_urlsafe(24)
        environment = {
            "PATH": os.defpath,
            "HOME": str(directory),
            "PYTHONDONTWRITEBYTECODE": "1",
            "APP_MODE": "hosted",
            "HOST": "127.0.0.1",
            "PORT": str(port),
            "PUBLIC_ORIGIN": origin,
            "ACCESS_PASSWORD": password,
            "EXECUTION_LIMIT": "2",
            "DATA_DIR": str(directory / "data"),
        }
        process = subprocess.Popen(
            [sys.executable, "-S", "-u", "app.py"],
            cwd=directory,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        try:
            deadline = time.monotonic() + 10
            while True:
                if process.poll() is not None:
                    raise RuntimeError("Clean runtime exited before becoming ready")
                try:
                    with urlopen(origin + "/healthz", timeout=1) as response:
                        assert json.load(response) == {"status": "ok"}
                    break
                except URLError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Clean runtime did not become ready")
                    time.sleep(0.05)
            try:
                urlopen(origin, timeout=2)
            except HTTPError as error:
                assert error.code == 401
                error.close()
            else:
                raise AssertionError("Hosted HTML was available without authentication")
            basic = (
                "Basic " + base64.b64encode(f"workspace:{password}".encode()).decode()
            )
            with urlopen(
                Request(origin, headers={"Authorization": basic}), timeout=2
            ) as response:
                page = response.read().decode()
            token = re.search(
                r'name="orchestration-session" content="([^"]+)"', page
            ).group(1)
            with urlopen(
                Request(
                    origin + "/api/state", headers={"Authorization": "Bearer " + token}
                ),
                timeout=2,
            ) as response:
                state = json.load(response)
            assert state["projects"] == []
            assert state["features"] == {"local_tools": False}
            for asset in ("/assets/main.js", "/assets/styles/themes.css"):
                with urlopen(
                    Request(origin + asset, headers={"Authorization": basic}), timeout=2
                ) as response:
                    assert response.read()
            print(
                json.dumps(
                    {
                        "status": "passed",
                        "runtime": "Python standard library only",
                        "personal_data_copied": False,
                        "hosted_authentication": "verified",
                        "local_tools": "disabled",
                        "frontend_assets": "served",
                    },
                    indent=2,
                )
            )
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            process.stderr.close()


if __name__ == "__main__":
    main()
