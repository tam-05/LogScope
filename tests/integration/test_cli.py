"""CLI integration tests.

Tests the CLI as a subprocess against a running server.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SAMPLE_LOG = Path(__file__).resolve().parents[2] / "examples" / "sample.log"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run_cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run the logscope CLI as a subprocess."""
    return subprocess.run(
        [sys.executable, "-m", "logscope.cli", *args],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    )


# ---------------------------------------------------------------------------
# Argument validation (no server needed)
# ---------------------------------------------------------------------------


class TestCLIArguments:
    def test_no_command(self) -> None:
        result = run_cli()
        assert result.returncode == 2

    def test_missing_file(self) -> None:
        result = run_cli("analyze", "nonexistent_file.log")
        assert result.returncode == 2
        assert "not found" in result.stderr.lower() or "error" in result.stderr.lower()

    def test_connection_refused(self) -> None:
        """Server not running → exit code 1."""
        result = run_cli(
            "analyze",
            str(SAMPLE_LOG),
            "--server",
            "http://127.0.0.1:19999",
            "--timeout",
            "3",
        )
        assert result.returncode == 1
        assert "connect" in result.stderr.lower() or "error" in result.stderr.lower()
