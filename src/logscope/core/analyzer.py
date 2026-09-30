"""Request-local streaming aggregation and finalization.

The :class:`StreamingAnalyzer` accepts arbitrary byte chunks, frames
them into physical lines via :class:`ByteFramer`, parses each line, and
maintains running counters.  Call :meth:`finish` once at EOF to obtain
the :class:`AnalysisSummary`.

No HTTP, global state, or cross-request coupling.
"""

from __future__ import annotations

from collections.abc import Iterable

from logscope.config import Settings
from logscope.core.framing import ByteFramer, FramedLine
from logscope.core.parser import parse_line
from logscope.models import (
    AnalysisSummary,
    ErrorPolicy,
    ParsedRecord,
    ParseErrorSample,
    ParseFailure,
    ParseFailureReason,
    ServiceLimitExceededError,
)


class StreamingAnalyzer:
    """Request-local, single-threaded streaming log analyzer.

    Thread safety: only one thread may call :meth:`feed` or
    :meth:`finish` at a time.  :meth:`finish` caches its result so
    repeated calls do not double-count.  :meth:`feed` after
    finalization raises :exc:`RuntimeError`.
    """

    def __init__(
        self,
        *,
        error_policy: ErrorPolicy = ErrorPolicy.ERROR_AND_FATAL,
        max_line_bytes: int = 65_536,
        max_services: int = 10_000,
        max_parse_error_samples: int = 10,
    ) -> None:
        self._error_policy = error_policy
        self._max_services = max_services
        self._max_parse_error_samples = max_parse_error_samples
        self._framer = ByteFramer(max_line_bytes)

        self._total_lines = 0
        self._parsed_lines = 0
        self._unparseable_lines = 0
        self._service_error_counts: dict[str, int] = {}
        self._parse_error_counts: dict[str, int] = {}
        self._parse_error_samples: list[ParseErrorSample] = []

        self._error_level_set = set(error_policy.error_levels())

        self._finalized = False
        self._result: AnalysisSummary | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def feed(self, chunk: bytes) -> None:
        """Accept the next byte segment; no HTTP dependencies."""
        if self._finalized:
            raise RuntimeError("Cannot feed after finalization")
        # Keep the framing result list bounded even for offline callers that
        # supply a file-sized chunk rather than API-sized batches.
        for start in range(0, len(chunk), 65_536):
            self._process_lines(self._framer.feed(chunk[start : start + 65_536]))

    def finish(self) -> AnalysisSummary:
        """Flush a final unterminated line and return the summary.

        Safe to call multiple times — repeated calls return the cached
        result without incrementing any counters.
        """
        if self._result is not None:
            return self._result

        remaining = self._framer.flush()
        self._process_lines(remaining)
        self._finalized = True

        # Compute top offenders.
        error_counts = dict(sorted(self._service_error_counts.items()))
        total_errors = sum(error_counts.values())

        if total_errors > 0:
            max_count = max(error_counts.values())
            top_services = sorted(s for s, c in error_counts.items() if c == max_count)
            top_service: str | None = top_services[0]
            top_error_count = max_count
        else:
            top_services = []
            top_service = None
            top_error_count = 0

        self._result = AnalysisSummary(
            schema_version="1.0",
            total_lines=self._total_lines,
            parsed_lines=self._parsed_lines,
            unparseable_lines=self._unparseable_lines,
            total_errors=total_errors,
            error_levels=self._error_policy.error_level_names(),
            error_counts=error_counts,
            top_service=top_service,
            top_services=top_services,
            top_error_count=top_error_count,
            parse_error_counts={k: v for k, v in sorted(self._parse_error_counts.items()) if v > 0},
            parse_error_samples=list(self._parse_error_samples),
        )
        return self._result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _process_lines(self, lines: list[FramedLine]) -> None:
        """Parse and aggregate a batch of framed lines."""
        for line in lines:
            self._total_lines += 1

            if line.is_overlong:
                self._record_failure(
                    ParseFailure(line.line_number, ParseFailureReason.LINE_TOO_LONG)
                )
                continue

            result = parse_line(line.data, line.line_number)

            if isinstance(result, ParseFailure):
                self._record_failure(result)
            else:
                assert isinstance(result, ParsedRecord)
                self._parsed_lines += 1

                # Register service (respecting cardinality cap).
                if result.service not in self._service_error_counts:
                    if len(self._service_error_counts) >= self._max_services:
                        raise ServiceLimitExceededError(
                            f"Service cardinality cap ({self._max_services}) exceeded"
                        )
                    self._service_error_counts[result.service] = 0

                # Count errors under the active policy.
                if result.level in self._error_level_set:
                    self._service_error_counts[result.service] += 1

    def _record_failure(self, failure: ParseFailure) -> None:
        """Record a parse failure in counters and diagnostic samples."""
        self._unparseable_lines += 1
        reason_key = failure.reason.value
        self._parse_error_counts[reason_key] = self._parse_error_counts.get(reason_key, 0) + 1
        if len(self._parse_error_samples) < self._max_parse_error_samples:
            self._parse_error_samples.append(
                ParseErrorSample(failure.line_number, failure.reason.value)
            )


# ---------------------------------------------------------------------------
# Convenience adapter
# ---------------------------------------------------------------------------


def analyze_chunks(
    chunks: Iterable[bytes],
    *,
    settings: Settings | None = None,
    error_policy: ErrorPolicy = ErrorPolicy.ERROR_AND_FATAL,
) -> AnalysisSummary:
    """Convenience adapter for synchronous offline tests and reuse."""
    if settings is None:
        settings = Settings()

    analyzer = StreamingAnalyzer(
        error_policy=error_policy,
        max_line_bytes=settings.max_line_bytes,
        max_services=settings.max_services,
        max_parse_error_samples=settings.max_parse_error_samples,
    )

    for chunk in chunks:
        analyzer.feed(chunk)

    return analyzer.finish()
