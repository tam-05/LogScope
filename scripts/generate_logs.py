#!/usr/bin/env python3
"""Generate reproducible log fixtures with known ground truth.

Usage:
    python scripts/generate_logs.py [--size SIZE_MB] [--seed SEED] [--output PATH]

Creates a log file with a fixed seed so counts are reproducible.
Prints the expected ground-truth summary to stdout as JSON.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

SERVICES = [
    "auth-service",
    "billing-service",
    "payment-service",
    "user-service",
    "notification-service",
    "gateway",
    "order-service",
    "inventory-service",
    "search-service",
    "analytics-service",
]

LEVELS = ["DEBUG", "INFO", "WARN", "ERROR", "FATAL"]
# Weighted towards non-error levels
LEVEL_WEIGHTS = [10, 40, 20, 25, 5]

MESSAGES = [
    "Request processed successfully",
    "Connection timeout after 30s",
    "User login successful",
    "Response time degraded",
    "Invalid account state",
    "Retry succeeded",
    "Cache miss for key",
    "Database connection pool exhausted",
    "Rate limit exceeded for client",
    "Health check passed",
    "Configuration reloaded",
    "Memory usage above threshold",
    "Disk space low on volume",
    "SSL certificate expiring soon",
    "Backup completed successfully",
]


def generate(
    target_bytes: int,
    seed: int = 42,
    output: Path | None = None,
) -> dict:
    """Generate a log file and return the ground-truth summary."""
    rng = random.Random(seed)

    error_counts: dict[str, int] = {}
    total_lines = 0
    total_bytes = 0

    base_date = "2026-09-18"
    hour, minute, second = 0, 0, 0

    destination = output.open("wb") if output is not None else None
    try:
        while total_bytes < target_bytes:
            service = rng.choice(SERVICES)
            level = rng.choices(LEVELS, weights=LEVEL_WEIGHTS, k=1)[0]
            message = rng.choice(MESSAGES)

            timestamp = f"{base_date} {hour:02d}:{minute:02d}:{second:02d}"
            line = f"{timestamp} {level} {service} {message}\n".encode()
            if total_bytes + len(line) > target_bytes:
                break  # Keep whole records and stay within the requested byte cap.
            if destination is not None:
                destination.write(line)
            total_bytes += len(line)
            total_lines += 1
            error_counts.setdefault(service, 0)
            if level in ("ERROR", "FATAL"):
                error_counts[service] += 1

            second += 1
            if second >= 60:
                second = 0
                minute += 1
            if minute >= 60:
                minute = 0
                hour += 1
            if hour >= 24:
                hour = 0
    finally:
        if destination is not None:
            destination.close()

    total_errors = sum(error_counts.values())
    if total_errors > 0:
        max_count = max(error_counts.values())
        top_services = sorted(s for s, c in error_counts.items() if c == max_count)
    else:
        max_count = 0
        top_services = []

    summary = {
        "schema_version": "1.0",
        "total_lines": total_lines,
        "parsed_lines": total_lines,
        "unparseable_lines": 0,
        "total_errors": total_errors,
        "error_levels": ["ERROR", "FATAL"],
        "error_counts": dict(sorted(error_counts.items())),
        "top_service": top_services[0] if top_services else None,
        "top_services": top_services,
        "top_error_count": max_count,
        "parse_error_counts": {},
        "parse_error_samples": [],
        "actual_bytes": total_bytes,
        "actual_lines": total_lines,
    }

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate reproducible log fixtures")
    parser.add_argument("--size", type=float, default=1.0, help="Target size in MiB")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--output", type=Path, default=None, help="Output file path")
    args = parser.parse_args()

    target_bytes = int(args.size * 1024 * 1024)
    output = args.output or Path(f"examples/generated_{args.size:.0f}mb.log")

    summary = generate(target_bytes, seed=args.seed, output=output)

    print(
        f"Generated {output} ({summary['actual_bytes']:,} bytes, {summary['actual_lines']:,} lines)"
    )
    print(json.dumps(summary, indent=2))

    # Write expected output
    expected_path = output.with_suffix(".expected.json")
    expected_path.write_text(json.dumps(summary, indent=2) + "\n")
    print(f"Expected output written to {expected_path}")


if __name__ == "__main__":
    main()
