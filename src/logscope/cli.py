"""CLI arguments, streamed HTTPX submission, and human-readable rendering.

Installed entry point: ``logscope``.

Usage::

    logscope analyze examples/sample.log
    logscope analyze examples/sample.log --json
    logscope analyze examples/sample.log --error-policy error_only
    logscope analyze examples/sample.log --fail-on-unparseable
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import stat
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, BinaryIO

import httpx
from pydantic import ValidationError

from logscope.models import AnalysisResponse

# ---------------------------------------------------------------------------
# Exit codes (PRD §8)
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_SERVER_ERROR = 1
EXIT_LOCAL_ERROR = 2
EXIT_UNPARSEABLE = 3


class LocalInputError(Exception):
    """The opened file could not provide its snapshotted bytes."""


def _positive_timeout(value: str) -> float:
    try:
        timeout = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a positive number") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise argparse.ArgumentTypeError("timeout must be a finite positive number")
    return timeout


async def _snapshot_stream(file: BinaryIO, file_size: int, deadline: float) -> AsyncIterator[bytes]:
    """Send exactly the opened file's initial size, detecting truncation."""
    remaining = file_size
    while remaining > 0:
        if time.monotonic() >= deadline:
            raise TimeoutError("Total deadline exceeded")
        data = await asyncio.to_thread(file.read, min(65_536, remaining))
        if not data:
            raise LocalInputError(f"File was truncated during read (expected {file_size} bytes)")
        remaining -= len(data)
        yield data


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point registered in pyproject.toml."""
    parser = argparse.ArgumentParser(
        prog="logscope",
        description="LogScope — streaming log file analyzer",
    )
    subparsers = parser.add_subparsers(dest="command")

    analyze_p = subparsers.add_parser("analyze", help="Analyze a log file")
    analyze_p.add_argument("file", type=Path, help="Path to the log file")
    analyze_p.add_argument(
        "--server",
        default=os.environ.get("LOGSCOPE_SERVER_URL", "http://127.0.0.1:8000"),
        help="API server base URL (default: LOGSCOPE_SERVER_URL or http://127.0.0.1:8000)",
    )
    analyze_p.add_argument(
        "--error-policy",
        default="error_and_fatal",
        choices=["error_and_fatal", "error_only"],
        help="Error counting policy (default: error_and_fatal)",
    )
    analyze_p.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit validated server JSON only to stdout",
    )
    analyze_p.add_argument(
        "--fail-on-unparseable",
        action="store_true",
        help="Return exit code 3 when the analysis contains unparseable lines",
    )
    analyze_p.add_argument(
        "--timeout",
        type=_positive_timeout,
        default=130.0,
        help="Total request wall-clock budget in seconds (default: 130)",
    )

    args = parser.parse_args()

    if args.command != "analyze":
        parser.print_help(sys.stderr)
        sys.exit(EXIT_LOCAL_ERROR)

    try:
        code = asyncio.run(_analyze(args))
    except KeyboardInterrupt:
        _err("Interrupted.")
        code = EXIT_SERVER_ERROR

    sys.exit(code)


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------


async def _analyze(args: argparse.Namespace) -> int:
    """Execute the ``analyze`` command."""
    file_path: Path = args.file

    try:
        file = file_path.open("rb")
    except OSError as exc:
        _err(f"Cannot open file: {exc}")
        return EXIT_LOCAL_ERROR

    with file:
        try:
            file_stat = os.fstat(file.fileno())
        except OSError as exc:
            _err(f"Cannot inspect opened file: {exc}")
            return EXIT_LOCAL_ERROR
        if not stat.S_ISREG(file_stat.st_mode):
            _err(f"Not a regular file: {file_path}")
            return EXIT_LOCAL_ERROR

        file_size = file_stat.st_size

        # --- Headers ---
        headers: dict[str, str] = {
            "Content-Type": "application/octet-stream",
            "Content-Length": str(file_size),
        }

        token = os.environ.get("LOGSCOPE_API_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"

        url = f"{args.server.rstrip('/')}/api/v1/analyses"
        params = {"error_policy": args.error_policy}

        deadline = time.monotonic() + args.timeout

        # --- Streaming upload and response, under one wall-clock deadline ---
        try:
            transport = httpx.AsyncHTTPTransport(retries=0)
            async with asyncio.timeout(args.timeout):
                async with httpx.AsyncClient(
                    transport=transport,
                    timeout=httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0),
                    follow_redirects=False,
                    verify=True,
                ) as client:
                    response = await client.post(
                        url,
                        params=params,
                        headers=headers,
                        content=_snapshot_stream(file, file_size, deadline),
                    )
        except LocalInputError as exc:
            _err(str(exc))
            return EXIT_LOCAL_ERROR
        except httpx.ConnectError:
            _err(f"Could not connect to server at {args.server}")
            return EXIT_SERVER_ERROR
        except httpx.TimeoutException:
            _err("Request timed out while connecting or waiting for network activity")
            return EXIT_SERVER_ERROR
        except TimeoutError:
            _err(f"Total deadline of {args.timeout}s exceeded")
            return EXIT_SERVER_ERROR
        except OSError as exc:
            _err(f"Local file read failed: {exc}")
            return EXIT_LOCAL_ERROR
        except httpx.RequestError as exc:
            _err(f"Network request failed: {type(exc).__name__}")
            return EXIT_SERVER_ERROR
        except Exception as exc:
            _err(f"Request failed: {type(exc).__name__}")
            return EXIT_SERVER_ERROR

    # --- Handle non-200 responses ---
    if response.status_code != 200:
        try:
            error_data = response.json()
            error = error_data.get("error", {})
            msg = error.get("message", response.text)
            code = error.get("code", f"HTTP {response.status_code}")
            _err(f"[{code}] {msg}")
        except Exception:
            _err(f"Server returned {response.status_code}: {response.text[:500]}")
        return EXIT_SERVER_ERROR

    # --- Parse and validate response ---
    try:
        data = response.json()
    except Exception:
        _err("Invalid JSON response from server")
        return EXIT_SERVER_ERROR

    try:
        validated = AnalysisResponse.model_validate(data)
    except ValidationError:
        _err("Incompatible server response: invalid summary schema or counts")
        return EXIT_SERVER_ERROR
    data = validated.model_dump()

    # --- Output ---
    if args.json_output:
        print(json.dumps(data, indent=2))
    else:
        _print_human(data)

    # --- Exit code ---
    if args.fail_on_unparseable and data["unparseable_lines"] > 0:
        return EXIT_UNPARSEABLE

    return EXIT_OK


# ---------------------------------------------------------------------------
# Human-readable formatting
# ---------------------------------------------------------------------------


def _print_human(data: dict[str, Any]) -> None:
    """Print aligned, human-readable analysis output."""
    total_lines: int = data["total_lines"]
    unparseable: int = data["unparseable_lines"]
    error_levels_str = ", ".join(data["error_levels"])
    error_counts: dict[str, int] = data["error_counts"]

    print(f"Lines processed: {total_lines:,}")
    print(f"Unparseable lines: {unparseable:,}")
    print(f"Error levels: {error_levels_str}")
    print()

    if error_counts:
        # Sort by descending error count, then ascending service name.
        sorted_services = sorted(
            error_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )

        # Column widths.
        name_width = max(len("Service Name"), max(len(s) for s, _ in sorted_services))
        formatted_counts = [f"{c:,}" for _, c in sorted_services]
        count_width = max(len("Error count"), max(len(fc) for fc in formatted_counts))

        print(f"{'Service Name':<{name_width}}  {'Error count':>{count_width}}")
        for (service, _count), fc in zip(sorted_services, formatted_counts, strict=True):
            print(f"{service:<{name_width}}  {fc:>{count_width}}")

    print()

    # Top offender line.
    top_services: list[str] = data["top_services"]
    top_count: int = data["top_error_count"]

    if not top_services:
        print("Top offender: none")
    elif len(top_services) == 1:
        error_word = "error" if top_count == 1 else "errors"
        print(f"Top offender: {top_services[0]} ({top_count:,} {error_word})")
    else:
        names = ", ".join(top_services)
        error_word = "error" if top_count == 1 else "errors"
        print(f"Top offenders: {names} ({top_count:,} {error_word} each)")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _err(msg: str) -> None:
    """Write an error message to stderr (no traceback)."""
    print(f"Error: {msg}", file=sys.stderr)


if __name__ == "__main__":
    main()
