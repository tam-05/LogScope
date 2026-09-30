"""Unit tests for the strict record parser.

Covers all functional cases from PRD §15.1 related to parsing.
"""

from __future__ import annotations

from logscope.core.parser import parse_line
from logscope.models import LogLevel, ParsedRecord, ParseFailure, ParseFailureReason

# ---------------------------------------------------------------------------
# Valid records
# ---------------------------------------------------------------------------


class TestValidRecords:
    """Records that must be accepted."""

    def test_basic_error(self) -> None:
        raw = b"2026-09-18 10:23:45 ERROR payment-service Connection timeout after 30s"
        result = parse_line(raw, 1)
        assert isinstance(result, ParsedRecord)
        assert result.level == LogLevel.ERROR
        assert result.service == "payment-service"

    def test_info_level(self) -> None:
        raw = b"2026-09-18 10:23:46 INFO auth-service User login successful"
        result = parse_line(raw, 2)
        assert isinstance(result, ParsedRecord)
        assert result.level == LogLevel.INFO
        assert result.service == "auth-service"

    def test_warn_level(self) -> None:
        raw = b"2026-09-18 10:24:01 WARN auth-service Response time degraded"
        result = parse_line(raw, 4)
        assert isinstance(result, ParsedRecord)
        assert result.level == LogLevel.WARN

    def test_debug_level(self) -> None:
        raw = b"2026-09-18 00:00:00 DEBUG svc-1 Starting up"
        result = parse_line(raw, 1)
        assert isinstance(result, ParsedRecord)
        assert result.level == LogLevel.DEBUG

    def test_fatal_level(self) -> None:
        raw = b"2026-09-18 23:59:59 FATAL db-service Crash"
        result = parse_line(raw, 1)
        assert isinstance(result, ParsedRecord)
        assert result.level == LogLevel.FATAL

    def test_leading_whitespace_accepted(self) -> None:
        raw = b"  2026-09-18 10:23:45 ERROR svc Message"
        result = parse_line(raw, 1)
        assert isinstance(result, ParsedRecord)

    def test_tab_separators(self) -> None:
        raw = b"2026-09-18\t10:23:45\tERROR\tsvc\tMessage text"
        result = parse_line(raw, 1)
        assert isinstance(result, ParsedRecord)

    def test_unicode_message(self) -> None:
        raw = "2026-09-18 10:23:45 ERROR svc Ünîcödé mëssàge 🔥".encode()
        result = parse_line(raw, 1)
        assert isinstance(result, ParsedRecord)

    def test_service_with_dots_colons_slashes(self) -> None:
        raw = b"2026-09-18 10:23:45 ERROR my.svc:8080/api Message"
        result = parse_line(raw, 1)
        assert isinstance(result, ParsedRecord)
        assert result.service == "my.svc:8080/api"

    def test_leap_year_valid(self) -> None:
        raw = b"2024-02-29 12:00:00 INFO svc Leap day"
        result = parse_line(raw, 1)
        assert isinstance(result, ParsedRecord)

    def test_message_with_error_word(self) -> None:
        """ERROR word in an INFO message must NOT increment errors."""
        raw = b"2026-09-18 10:23:45 INFO svc ERROR happened in downstream"
        result = parse_line(raw, 1)
        assert isinstance(result, ParsedRecord)
        assert result.level == LogLevel.INFO  # Not ERROR

    def test_message_with_trailing_whitespace(self) -> None:
        raw = b"2026-09-18 10:23:45 ERROR svc Message   "
        result = parse_line(raw, 1)
        assert isinstance(result, ParsedRecord)

    def test_service_max_length_128(self) -> None:
        """Service name exactly 128 characters should be accepted."""
        svc = "A" + "b" * 127  # 128 chars total
        raw = f"2026-09-18 10:23:45 ERROR {svc} msg".encode()
        result = parse_line(raw, 1)
        assert isinstance(result, ParsedRecord)
        assert result.service == svc


# ---------------------------------------------------------------------------
# Parse failures
# ---------------------------------------------------------------------------


class TestInvalidUtf8:
    def test_invalid_utf8_byte(self) -> None:
        raw = b"2026-09-18 10:23:45 ERROR svc \xff bad"
        result = parse_line(raw, 1)
        assert isinstance(result, ParseFailure)
        assert result.reason == ParseFailureReason.INVALID_UTF8


class TestEmptyLine:
    def test_empty_bytes(self) -> None:
        assert parse_line(b"", 1) == ParseFailure(1, ParseFailureReason.EMPTY_LINE)

    def test_whitespace_only(self) -> None:
        assert parse_line(b"   ", 1) == ParseFailure(1, ParseFailureReason.EMPTY_LINE)

    def test_tabs_only(self) -> None:
        assert parse_line(b"\t\t", 1) == ParseFailure(1, ParseFailureReason.EMPTY_LINE)


class TestInvalidFormat:
    def test_non_ascii_timestamp_digits(self) -> None:
        raw = "٢٠٢٦-٠٩-١٨ ١٠:٢٣:٤٥ ERROR svc msg".encode()
        result = parse_line(raw, 1)
        assert isinstance(result, ParseFailure)
        assert result.reason == ParseFailureReason.INVALID_FORMAT

    def test_nul_character(self) -> None:
        raw = b"2026-09-18 10:23:45 ERROR svc \x00bad"
        result = parse_line(raw, 1)
        assert isinstance(result, ParseFailure)
        assert result.reason == ParseFailureReason.INVALID_FORMAT

    def test_embedded_cr(self) -> None:
        raw = b"2026-09-18 10:23:45 ERROR svc bad\rmsg"
        result = parse_line(raw, 1)
        assert isinstance(result, ParseFailure)
        assert result.reason == ParseFailureReason.INVALID_FORMAT

    def test_fewer_than_five_fields(self) -> None:
        raw = b"error billing-service No Auth token"
        result = parse_line(raw, 7)
        assert isinstance(result, ParseFailure)
        assert result.reason == ParseFailureReason.INVALID_FORMAT
        assert result.line_number == 7

    def test_timestamp_free_line(self) -> None:
        """PRD §4.3: timestamp-free lowercase line is unparseable."""
        raw = b"error billing-service No Auth token"
        result = parse_line(raw, 1)
        assert isinstance(result, ParseFailure)
        assert result.reason == ParseFailureReason.INVALID_FORMAT

    def test_wrong_date_shape(self) -> None:
        raw = b"2026/09/18 10:23:45 ERROR svc msg"
        result = parse_line(raw, 1)
        assert isinstance(result, ParseFailure)
        assert result.reason == ParseFailureReason.INVALID_FORMAT

    def test_wrong_time_shape(self) -> None:
        raw = b"2026-09-18 10-23-45 ERROR svc msg"
        result = parse_line(raw, 1)
        assert isinstance(result, ParseFailure)
        assert result.reason == ParseFailureReason.INVALID_FORMAT

    def test_missing_message_field(self) -> None:
        """Four fields only (no separator after service) → invalid_format."""
        raw = b"2026-09-18 10:23:45 ERROR svc"
        result = parse_line(raw, 1)
        assert isinstance(result, ParseFailure)
        assert result.reason == ParseFailureReason.INVALID_FORMAT


class TestInvalidTimestamp:
    def test_invalid_month(self) -> None:
        raw = b"2026-13-18 10:23:45 ERROR svc msg"
        result = parse_line(raw, 1)
        assert isinstance(result, ParseFailure)
        assert result.reason == ParseFailureReason.INVALID_TIMESTAMP

    def test_invalid_day(self) -> None:
        raw = b"2026-02-30 10:23:45 ERROR svc msg"
        result = parse_line(raw, 1)
        assert isinstance(result, ParseFailure)
        assert result.reason == ParseFailureReason.INVALID_TIMESTAMP

    def test_non_leap_year(self) -> None:
        raw = b"2026-02-29 10:23:45 ERROR svc msg"
        result = parse_line(raw, 1)
        assert isinstance(result, ParseFailure)
        assert result.reason == ParseFailureReason.INVALID_TIMESTAMP

    def test_invalid_hour(self) -> None:
        raw = b"2026-09-18 25:23:45 ERROR svc msg"
        result = parse_line(raw, 1)
        assert isinstance(result, ParseFailure)
        assert result.reason == ParseFailureReason.INVALID_TIMESTAMP

    def test_invalid_second_60(self) -> None:
        raw = b"2026-09-18 10:23:60 ERROR svc msg"
        result = parse_line(raw, 1)
        assert isinstance(result, ParseFailure)
        assert result.reason == ParseFailureReason.INVALID_TIMESTAMP


class TestInvalidLevel:
    def test_lowercase_error(self) -> None:
        """Level matching is case-sensitive."""
        raw = b"2026-09-18 10:23:45 error svc msg"
        result = parse_line(raw, 1)
        assert isinstance(result, ParseFailure)
        assert result.reason == ParseFailureReason.INVALID_LEVEL

    def test_unknown_level(self) -> None:
        raw = b"2026-09-18 10:23:45 TRACE svc msg"
        result = parse_line(raw, 1)
        assert isinstance(result, ParseFailure)
        assert result.reason == ParseFailureReason.INVALID_LEVEL


class TestInvalidService:
    def test_service_starts_with_dash(self) -> None:
        raw = b"2026-09-18 10:23:45 ERROR -svc msg"
        result = parse_line(raw, 1)
        assert isinstance(result, ParseFailure)
        assert result.reason == ParseFailureReason.INVALID_SERVICE

    def test_service_too_long(self) -> None:
        svc = "A" + "b" * 128  # 129 chars total — exceeds 128
        raw = f"2026-09-18 10:23:45 ERROR {svc} msg".encode()
        result = parse_line(raw, 1)
        assert isinstance(result, ParseFailure)
        assert result.reason == ParseFailureReason.INVALID_SERVICE

    def test_service_with_space(self) -> None:
        """Space is not allowed in service — it acts as a field separator."""
        # This splits into >5 fields so service becomes "bad" and message is "svc msg"
        raw = b"2026-09-18 10:23:45 ERROR bad svc msg"
        result = parse_line(raw, 1)
        assert isinstance(result, ParsedRecord)  # "bad" is a valid service
        assert result.service == "bad"


class TestEmptyMessage:
    def test_message_is_whitespace(self) -> None:
        """Separator after service, but 5th field is blank → empty_message."""
        raw = b"2026-09-18 10:23:45 ERROR svc   "
        result = parse_line(raw, 1)
        assert isinstance(result, ParseFailure)
        assert result.reason == ParseFailureReason.EMPTY_MESSAGE


# ---------------------------------------------------------------------------
# Precedence checks
# ---------------------------------------------------------------------------


class TestPrecedence:
    """Verify failure reason precedence when multiple conditions hold."""

    def test_invalid_utf8_over_empty(self) -> None:
        """invalid_utf8 has priority over empty_line."""
        # 0xFF is invalid UTF-8
        result = parse_line(b"\xff", 1)
        assert isinstance(result, ParseFailure)
        assert result.reason == ParseFailureReason.INVALID_UTF8

    def test_empty_over_format(self) -> None:
        """empty_line has priority over invalid_format."""
        result = parse_line(b"", 1)
        assert result.reason == ParseFailureReason.EMPTY_LINE

    def test_format_over_timestamp(self) -> None:
        """Wrong shape checked before value validation."""
        raw = b"not-a-date not-time ERROR svc msg"
        result = parse_line(raw, 1)
        assert isinstance(result, ParseFailure)
        assert result.reason == ParseFailureReason.INVALID_FORMAT

    def test_timestamp_over_level(self) -> None:
        """Invalid timestamp value checked before level."""
        raw = b"2026-13-18 10:23:45 BOGUS svc msg"
        result = parse_line(raw, 1)
        assert isinstance(result, ParseFailure)
        assert result.reason == ParseFailureReason.INVALID_TIMESTAMP

    def test_level_over_service(self) -> None:
        raw = b"2026-09-18 10:23:45 TRACE -bad msg"
        result = parse_line(raw, 1)
        assert isinstance(result, ParseFailure)
        assert result.reason == ParseFailureReason.INVALID_LEVEL

    def test_service_over_message(self) -> None:
        raw = b"2026-09-18 10:23:45 ERROR -bad  "
        result = parse_line(raw, 1)
        assert isinstance(result, ParseFailure)
        assert result.reason == ParseFailureReason.INVALID_SERVICE
