"""Start Streamlit briefly and verify its health endpoint."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import duckdb
from build_phase10_ci_fixture import build_phase10_ci_fixture

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _available_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _verify_fixture(database_path: Path) -> tuple[int, int]:
    if not database_path.is_file():
        raise RuntimeError(f"Streamlit fixture database is unavailable: {database_path}")
    with duckdb.connect(str(database_path), read_only=True) as connection:
        employer_row = connection.execute("SELECT count(*) FROM employer_metrics").fetchone()
        institution_row = connection.execute("SELECT count(*) FROM institution_metrics").fetchone()
    if employer_row is None or institution_row is None:
        raise RuntimeError("Streamlit smoke fixture count query returned no row")
    employer_count = int(employer_row[0])
    institution_count = int(institution_row[0])
    if employer_count == 0 or institution_count == 0:
        raise RuntimeError("Streamlit smoke fixture must contain employers and institutions")
    return employer_count, institution_count


def main() -> None:
    """Fail unless Streamlit starts with a nonempty sanitized fixture."""

    configured_root = os.environ.get("SPONSOR_INTEL_CI_FIXTURE_ROOT")
    temporary_directory: tempfile.TemporaryDirectory[str] | None = None
    if configured_root:
        database_path = Path(configured_root).resolve() / "db" / "phase10-ci.duckdb"
    else:
        temporary_directory = tempfile.TemporaryDirectory(prefix="sponsor-intel-streamlit-")
        database_path = build_phase10_ci_fixture(Path(temporary_directory.name))
    employer_count, institution_count = _verify_fixture(database_path)

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
        env={
            **os.environ,
            "SPONSOR_INTEL_DB_PATH": str(database_path),
            "SPONSOR_INTEL_DEPLOYMENT_MODE": "local",
            "SPONSOR_INTEL_REQUIRE_DATA": "true",
        },
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
                        print(
                            "Streamlit health check passed with "
                            f"{employer_count} employers and {institution_count} institutions "
                            f"on port {port}."
                        )
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
        if temporary_directory is not None:
            temporary_directory.cleanup()


if __name__ == "__main__":
    main()
