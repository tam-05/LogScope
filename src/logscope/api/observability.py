"""Structured logging and Prometheus metrics.

Metrics follow the PRD contract: no service names, request IDs,
filenames, or raw messages in Prometheus labels.
"""

from __future__ import annotations

import logging
import sys

from prometheus_client import Counter, Gauge, Histogram

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

REQUEST_COUNT = Counter(
    "logscope_requests_total",
    "Total HTTP requests by method, path template, and status code.",
    ["method", "path", "status"],
)

ANALYSIS_DURATION = Histogram(
    "logscope_analysis_duration_seconds",
    "Wall-clock analysis duration in seconds.",
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0],
)

INPUT_BYTES = Counter(
    "logscope_input_bytes_total",
    "Total input bytes received for analysis.",
)

ACTIVE_ANALYSES = Gauge(
    "logscope_active_analyses",
    "Number of currently active analyses.",
)

BUSY_REJECTIONS = Counter(
    "logscope_busy_rejections_total",
    "Total 503 busy rejections.",
)

PARSED_LINES = Counter(
    "logscope_parsed_lines_total",
    "Total parsed lines across all analyses.",
)

UNPARSEABLE_LINES = Counter(
    "logscope_unparseable_lines_total",
    "Total unparseable lines across all analyses.",
)


# ---------------------------------------------------------------------------
# Structured logging
# ---------------------------------------------------------------------------


class _JSONFormatter(logging.Formatter):
    """Minimal JSON-lines formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        import json

        obj: dict[str, object] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Merge extra fields (set by logger.info("...", extra={...}))
        for key in (
            "request_id",
            "method",
            "path",
            "status",
            "elapsed_ms",
            "received_bytes",
            "total_lines",
            "parsed_lines",
            "unparseable_lines",
            "total_errors",
            "exc_type",
        ):
            value = getattr(record, key, None)
            if value is not None:
                obj[key] = value

        return json.dumps(obj, default=str)


def setup_logging(level: str = "INFO") -> None:
    """Configure structured JSON logging to stderr."""
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JSONFormatter())

    root = logging.getLogger("logscope")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.propagate = False
