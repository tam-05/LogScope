"""CLI behavior against real TCP servers."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SAMPLE_LOG = Path(__file__).resolve().parents[2] / "examples" / "sample.log"


def run_cli(path: Path, server: str, *options: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "logscope.cli", "analyze", str(path), "--server", server, *options],
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_live_cli_human_and_json(live_server: str) -> None:
    human = run_cli(SAMPLE_LOG, live_server)
    assert human.returncode == 0
    assert "Lines processed: 7" in human.stdout
    assert "Top offender: payment-service (2 errors)" in human.stdout
    structured = run_cli(SAMPLE_LOG, live_server, "--json")
    assert structured.returncode == 0
    assert structured.stderr == ""
    assert json.loads(structured.stdout)["total_errors"] == 3


def test_live_cli_strict_exit_ties_and_empty(live_server: str, tmp_path: Path) -> None:
    strict = run_cli(SAMPLE_LOG, live_server, "--fail-on-unparseable")
    assert strict.returncode == 3
    tie = tmp_path / "tie.log"
    tie.write_bytes(b"2026-09-18 10:00:00 ERROR b failed\n2026-09-18 10:00:01 ERROR a failed\n")
    result = run_cli(tie, live_server)
    assert result.returncode == 0
    assert "Top offenders: a, b (1 error each)" in result.stdout
    empty = tmp_path / "empty.log"
    empty.write_bytes(b"")
    result = run_cli(empty, live_server)
    assert result.returncode == 0
    assert "Lines processed: 0" in result.stdout
    assert "Top offender: none" in result.stdout


@pytest.mark.parametrize("body", [b"not json", b'{"total_lines": "seven"}'])
def test_cli_rejects_bad_server_response(tmp_path: Path, body: bytes) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = run_cli(SAMPLE_LOG, f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert result.returncode == 1
    assert "Traceback" not in result.stderr


def test_cli_total_deadline_covers_slow_response() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            time.sleep(0.8)
            try:
                self.send_response(200)
                self.end_headers()
            except OSError:
                pass

        def log_message(self, fmt: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = run_cli(SAMPLE_LOG, f"http://127.0.0.1:{server.server_port}", "--timeout", "0.2")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert result.returncode == 1
    assert "deadline" in result.stderr.lower()


def test_cli_handles_proxy_html_error() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            body = b"<html>request too large</html>"
            self.send_response(413)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = run_cli(SAMPLE_LOG, f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert result.returncode == 1
    assert "413" in result.stderr
    assert "Traceback" not in result.stderr
