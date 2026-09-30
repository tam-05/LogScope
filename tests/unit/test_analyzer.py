"""Unit tests for the streaming analyzer.

Covers golden fixture, error policies, ties, zero-error services,
diagnostics, service cap, and finalization semantics from PRD §15.1.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st

from logscope.core.analyzer import StreamingAnalyzer, analyze_chunks
from logscope.models import ErrorPolicy, ServiceLimitExceededError

# ---------------------------------------------------------------------------
# Golden fixture — PRD §5.2
# ---------------------------------------------------------------------------


class TestGoldenFixture:
    """The exact seven-line sample must produce the exact expected JSON."""

    def test_exact_result(self, sample_log: bytes) -> None:
        summary = analyze_chunks([sample_log])
        result = summary.to_dict()

        expected_path = Path(__file__).resolve().parents[2] / "examples" / "expected_sample.json"
        expected = json.loads(expected_path.read_text())

        assert result == expected

    def test_exact_result_chunked(self, sample_log: bytes) -> None:
        """Same result when fed one byte at a time."""
        chunks = [sample_log[i : i + 1] for i in range(len(sample_log))]
        summary = analyze_chunks(chunks)
        result = summary.to_dict()

        expected_path = Path(__file__).resolve().parents[2] / "examples" / "expected_sample.json"
        expected = json.loads(expected_path.read_text())

        assert result == expected


# ---------------------------------------------------------------------------
# Error policy
# ---------------------------------------------------------------------------


class TestErrorPolicy:
    def test_error_and_fatal_default(self) -> None:
        data = b"2026-09-18 10:00:00 ERROR svc-a Err\n2026-09-18 10:00:01 FATAL svc-a Fatal\n"
        summary = analyze_chunks([data])
        assert summary.total_errors == 2
        assert summary.error_counts == {"svc-a": 2}
        assert summary.error_levels == ["ERROR", "FATAL"]

    def test_error_only(self) -> None:
        data = b"2026-09-18 10:00:00 ERROR svc-a Err\n2026-09-18 10:00:01 FATAL svc-a Fatal\n"
        summary = analyze_chunks([data], error_policy=ErrorPolicy.ERROR_ONLY)
        assert summary.total_errors == 1
        assert summary.error_counts == {"svc-a": 1}
        assert summary.error_levels == ["ERROR"]

    def test_fatal_only_service_with_error_only_policy(self) -> None:
        """A FATAL-only service still appears with count 0 under error_only."""
        data = b"2026-09-18 10:00:00 FATAL svc-a Something bad\n"
        summary = analyze_chunks([data], error_policy=ErrorPolicy.ERROR_ONLY)
        assert summary.error_counts == {"svc-a": 0}
        assert summary.total_errors == 0


# ---------------------------------------------------------------------------
# Zero-error services and top offender
# ---------------------------------------------------------------------------


class TestZeroErrorServices:
    def test_info_warn_debug_only(self) -> None:
        """Services observed with only non-error levels have zero error count."""
        data = (
            b"2026-09-18 10:00:00 INFO svc-a OK\n"
            b"2026-09-18 10:00:01 WARN svc-b Slow\n"
            b"2026-09-18 10:00:02 DEBUG svc-c Trace\n"
        )
        summary = analyze_chunks([data])
        assert summary.error_counts == {"svc-a": 0, "svc-b": 0, "svc-c": 0}
        assert summary.total_errors == 0
        assert summary.top_service is None
        assert summary.top_services == []
        assert summary.top_error_count == 0


class TestTopOffender:
    def test_single_top(self) -> None:
        data = (
            b"2026-09-18 10:00:00 ERROR svc-a Err\n"
            b"2026-09-18 10:00:01 ERROR svc-a Err\n"
            b"2026-09-18 10:00:02 ERROR svc-b Err\n"
        )
        summary = analyze_chunks([data])
        assert summary.top_service == "svc-a"
        assert summary.top_services == ["svc-a"]
        assert summary.top_error_count == 2

    def test_tied_top_sorted(self) -> None:
        """Multiple services tied → sorted lexicographically."""
        data = b"2026-09-18 10:00:00 ERROR zeta Err\n2026-09-18 10:00:01 ERROR alpha Err\n"
        summary = analyze_chunks([data])
        assert summary.top_services == ["alpha", "zeta"]
        assert summary.top_service == "alpha"  # first in sorted list
        assert summary.top_error_count == 1

    def test_no_errors_no_top(self) -> None:
        data = b"2026-09-18 10:00:00 INFO svc-a OK\n"
        summary = analyze_chunks([data])
        assert summary.top_service is None
        assert summary.top_services == []
        assert summary.top_error_count == 0


# ---------------------------------------------------------------------------
# Line counting
# ---------------------------------------------------------------------------


class TestLineCounting:
    def test_empty_file(self) -> None:
        summary = analyze_chunks([b""])
        assert summary.total_lines == 0
        assert summary.parsed_lines == 0
        assert summary.unparseable_lines == 0

    def test_invariant_total_equals_parsed_plus_unparseable(self) -> None:
        data = b"2026-09-18 10:00:00 ERROR svc-a Err\nbad line\n\n"
        summary = analyze_chunks([data])
        assert summary.total_lines == summary.parsed_lines + summary.unparseable_lines

    def test_duplicate_lines_counted_separately(self) -> None:
        line = b"2026-09-18 10:00:00 ERROR svc-a Err\n"
        summary = analyze_chunks([line * 3])
        assert summary.total_lines == 3
        assert summary.parsed_lines == 3
        assert summary.error_counts == {"svc-a": 3}


# ---------------------------------------------------------------------------
# Parse error diagnostics
# ---------------------------------------------------------------------------


class TestDiagnostics:
    def test_diagnostic_samples_capped(self) -> None:
        """Only first 10 diagnostics are kept."""
        lines = b"".join(b"bad\n" for _ in range(20))
        analyzer = StreamingAnalyzer(max_parse_error_samples=10)
        analyzer.feed(lines)
        summary = analyzer.finish()
        assert len(summary.parse_error_samples) == 10
        # First diagnostic should be line 1
        assert summary.parse_error_samples[0].line_number == 1

    def test_parse_error_counts_by_reason(self) -> None:
        data = (
            b"\n"  # empty_line
            b"\n"  # empty_line
            b"bad\n"  # invalid_format
        )
        summary = analyze_chunks([data])
        assert summary.parse_error_counts == {
            "empty_line": 2,
            "invalid_format": 1,
        }

    def test_zero_reason_omitted(self) -> None:
        """Reasons with zero count do not appear."""
        data = b"bad line\n"
        summary = analyze_chunks([data])
        for v in summary.parse_error_counts.values():
            assert v > 0


# ---------------------------------------------------------------------------
# Service cardinality cap
# ---------------------------------------------------------------------------


class TestServiceCap:
    def test_existing_services_still_allowed(self) -> None:
        """After hitting the cap, existing services are still counted."""
        lines: list[bytes] = []
        for i in range(5):
            lines.append(f"2026-09-18 10:00:0{i} ERROR svc{i} msg\n".encode())
        # Add another line for existing service
        lines.append(b"2026-09-18 10:01:00 ERROR svc0 msg\n")

        analyzer = StreamingAnalyzer(max_services=5)
        for line in lines:
            analyzer.feed(line)
        summary = analyzer.finish()
        assert summary.error_counts["svc0"] == 2

    def test_cap_exceeded_raises(self) -> None:
        """New service beyond cap raises ServiceLimitExceeded."""
        lines: list[bytes] = []
        for i in range(6):
            lines.append(f"2026-09-18 10:00:0{i} ERROR svc{i} msg\n".encode())

        analyzer = StreamingAnalyzer(max_services=5)
        with pytest.raises(ServiceLimitExceededError):
            for line in lines:
                analyzer.feed(line)


# ---------------------------------------------------------------------------
# Finalization semantics
# ---------------------------------------------------------------------------


class TestFinalization:
    def test_repeated_finish_no_double_counting(self) -> None:
        data = b"2026-09-18 10:00:00 ERROR svc-a Err\n"
        analyzer = StreamingAnalyzer()
        analyzer.feed(data)
        s1 = analyzer.finish()
        s2 = analyzer.finish()
        assert s1.total_lines == s2.total_lines == 1
        assert s1.total_errors == s2.total_errors == 1

    def test_feed_after_finish_raises(self) -> None:
        analyzer = StreamingAnalyzer()
        analyzer.feed(b"2026-09-18 10:00:00 ERROR svc Msg\n")
        analyzer.finish()
        with pytest.raises(RuntimeError, match="Cannot feed after finalization"):
            analyzer.feed(b"more data")


# ---------------------------------------------------------------------------
# Chunk invariance (property-based)
# ---------------------------------------------------------------------------


class TestChunkInvariance:
    @given(
        chunk_size=st.integers(min_value=1, max_value=100),
    )
    @hyp_settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_sample_log_chunk_invariant(self, sample_log: bytes, chunk_size: int) -> None:
        """Any chunk partition of the sample log gives the same counts."""
        ref = analyze_chunks([sample_log])
        chunks = [sample_log[i : i + chunk_size] for i in range(0, len(sample_log), chunk_size)]
        test = analyze_chunks(chunks)

        assert ref.total_lines == test.total_lines
        assert ref.parsed_lines == test.parsed_lines
        assert ref.unparseable_lines == test.unparseable_lines
        assert ref.total_errors == test.total_errors
        assert ref.error_counts == test.error_counts
        assert ref.top_service == test.top_service
        assert ref.top_services == test.top_services
