"""Strict record parsing and typed parse failures.

Parses one bounded physical line (bytes, excluding the line terminator)
into a :class:`ParsedRecord` or a :class:`ParseFailure` with a
categorized reason.

The function has no HTTP or state dependencies and is safe to call from
any thread.
"""

from __future__ import annotations

import datetime
import re

from logscope.models import (
    LogLevel,
    ParsedRecord,
    ParseFailure,
    ParseFailureReason,
)

# ---------------------------------------------------------------------------
# Pre-compiled constants
# ---------------------------------------------------------------------------

_VALID_LEVELS: frozenset[str] = frozenset(level.value for level in LogLevel)

_DATE_SHAPE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
_TIME_SHAPE = re.compile(r"^[0-9]{2}:[0-9]{2}:[0-9]{2}$")

# Service name: starts with alphanumeric, then up to 127 more chars from
# the allowed set.  Total length 1–128.
_SERVICE_PATTERN = re.compile(r"^[A-Za-z0-9][-A-Za-z0-9._:/]{0,127}$")

# Whitespace splitter (one or more ASCII spaces or tabs).
_WS_SPLIT = re.compile(r"[ \t]+")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_line(raw: bytes, line_number: int) -> ParsedRecord | ParseFailure:
    """Parse one bounded physical line, excluding its line terminator.

    Applies the precedence defined in PRD §4.5:
      1. line_too_long   — handled by the framer, not here
      2. invalid_utf8
      3. empty_line
      4. invalid_format  — NUL/CR, <5 fields, wrong timestamp shape
      5. invalid_timestamp
      6. invalid_level
      7. invalid_service
      8. empty_message
    """
    # 1. Decode — invalid_utf8
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return ParseFailure(line_number, ParseFailureReason.INVALID_UTF8)

    # 2. Blank check (without mutating) — empty_line
    if not text or text.strip(" \t") == "":
        return ParseFailure(line_number, ParseFailureReason.EMPTY_LINE)

    # 3. Prohibited control characters — invalid_format
    if "\x00" in text or "\r" in text:
        return ParseFailure(line_number, ParseFailureReason.INVALID_FORMAT)

    # 4. Remove leading horizontal whitespace, then split at most 4 times.
    #    Do NOT trim trailing whitespace before splitting — that would erase
    #    the distinction between a missing 5th field and a blank 5th field.
    stripped = text.lstrip(" \t")
    parts = _WS_SPLIT.split(stripped, maxsplit=4)

    # 5. Must have exactly 5 fields — invalid_format
    if len(parts) < 5:
        return ParseFailure(line_number, ParseFailureReason.INVALID_FORMAT)

    date_str, time_str, level_str, service_str, message_str = parts

    # 6. Validate timestamp *shape* — invalid_format
    if not _DATE_SHAPE.match(date_str) or not _TIME_SHAPE.match(time_str):
        return ParseFailure(line_number, ParseFailureReason.INVALID_FORMAT)

    # 7. Validate calendar/time *values* — invalid_timestamp
    try:
        year = int(date_str[:4])
        month = int(date_str[5:7])
        day = int(date_str[8:10])
        hour = int(time_str[:2])
        minute = int(time_str[3:5])
        second = int(time_str[6:8])
        datetime.datetime(year, month, day, hour, minute, second)
    except ValueError:
        return ParseFailure(line_number, ParseFailureReason.INVALID_TIMESTAMP)

    # 8. Validate level — invalid_level (case-sensitive)
    if level_str not in _VALID_LEVELS:
        return ParseFailure(line_number, ParseFailureReason.INVALID_LEVEL)

    # 9. Validate service name — invalid_service
    if not _SERVICE_PATTERN.match(service_str):
        return ParseFailure(line_number, ParseFailureReason.INVALID_SERVICE)

    # 10. Check message is not just horizontal whitespace — empty_message
    if message_str.strip(" \t") == "":
        return ParseFailure(line_number, ParseFailureReason.EMPTY_MESSAGE)

    return ParsedRecord(level=LogLevel(level_str), service=service_str)
