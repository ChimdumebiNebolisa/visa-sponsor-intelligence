"""Start Streamlit briefly and verify its health endpoint."""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _available_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def main() -> None:
    """Fail unless the Phase 0 Streamlit shell becomes healthy within 20 seconds."""

    port = _available_local_port()
    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(PROJECT_ROOT / "app" / "Home.py"),
        "--server.headless=true",
        "--server.address=127.0.0.1",
        f"--server.port={port}",
        "--browser.gatherUsageStats=false",
    ]
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.monotonic() + 20
    health_url = f"http://127.0.0.1:{port}/_stcore/health"

    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                output = process.communicate()[0]
                raise RuntimeError(f"Streamlit exited before becoming healthy:\n{output}")
            try:
                with urllib.request.urlopen(health_url, timeout=1) as response:
                    body = response.read().decode("utf-8").strip()
                    if response.status == 200 and body == "ok":
                        print(f"Streamlit health check passed on port {port}.")
                        return
            except (OSError, urllib.error.URLError):
                time.sleep(0.25)
        raise TimeoutError("Streamlit did not become healthy within 20 seconds.")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
