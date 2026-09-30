"""Shared models, enums, and data structures for LogScope."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class LogLevel(enum.Enum):
    """Valid log levels in the strict format."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    FATAL = "FATAL"


class ErrorPolicy(enum.Enum):
    """Which log levels count as errors."""

    ERROR_AND_FATAL = "error_and_fatal"
    ERROR_ONLY = "error_only"

    def error_levels(self) -> list[LogLevel]:
        """Return the log levels that count as errors under this policy."""
        if self == ErrorPolicy.ERROR_AND_FATAL:
            return [LogLevel.ERROR, LogLevel.FATAL]
        return [LogLevel.ERROR]

    def error_level_names(self) -> list[str]:
        """Return level name strings for the response."""
        return [level.value for level in self.error_levels()]


class ParseFailureReason(enum.Enum):
    """Reasons a physical line cannot be parsed, in precedence order."""

    LINE_TOO_LONG = "line_too_long"
    INVALID_UTF8 = "invalid_utf8"
    EMPTY_LINE = "empty_line"
    INVALID_FORMAT = "invalid_format"
    INVALID_TIMESTAMP = "invalid_timestamp"
    INVALID_LEVEL = "invalid_level"
    INVALID_SERVICE = "invalid_service"
    EMPTY_MESSAGE = "empty_message"


@dataclass(frozen=True, slots=True)
class ParsedRecord:
    """A successfully parsed log record. Only retains level and service."""

    level: LogLevel
    service: str


@dataclass(frozen=True, slots=True)
class ParseFailure:
    """A line that could not be parsed."""

    line_number: int
    reason: ParseFailureReason


@dataclass(frozen=True, slots=True)
class ParseErrorSample:
    """A diagnostic sample for a parse failure."""

    line_number: int
    reason: str


@dataclass
class AnalysisSummary:
    """Complete analysis result returned by the analyzer."""

    schema_version: str = "1.0"
    total_lines: int = 0
    parsed_lines: int = 0
    unparseable_lines: int = 0
    total_errors: int = 0
    error_levels: list[str] = field(default_factory=list)
    error_counts: dict[str, int] = field(default_factory=dict)
    top_service: str | None = None
    top_services: list[str] = field(default_factory=list)
    top_error_count: int = 0
    parse_error_counts: dict[str, int] = field(default_factory=dict)
    parse_error_samples: list[ParseErrorSample] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary with stable key ordering."""
        return {
            "schema_version": self.schema_version,
            "total_lines": self.total_lines,
            "parsed_lines": self.parsed_lines,
            "unparseable_lines": self.unparseable_lines,
            "total_errors": self.total_errors,
            "error_levels": self.error_levels,
            "error_counts": dict(sorted(self.error_counts.items())),
            "top_service": self.top_service,
            "top_services": self.top_services,
            "top_error_count": self.top_error_count,
            "parse_error_counts": {
                k: v for k, v in sorted(self.parse_error_counts.items()) if v > 0
            },
            "parse_error_samples": [
                {"line_number": s.line_number, "reason": s.reason} for s in self.parse_error_samples
            ],
        }


class ResponseParseErrorSample(BaseModel):
    model_config = ConfigDict(extra="forbid")

    line_number: StrictInt = Field(ge=1)
    reason: str


class AnalysisResponse(BaseModel):
    """Strict wire contract shared by OpenAPI and the CLI."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str
    total_lines: StrictInt = Field(ge=0)
    parsed_lines: StrictInt = Field(ge=0)
    unparseable_lines: StrictInt = Field(ge=0)
    total_errors: StrictInt = Field(ge=0)
    error_levels: list[str]
    error_counts: dict[str, StrictInt]
    top_service: str | None
    top_services: list[str]
    top_error_count: StrictInt = Field(ge=0)
    parse_error_counts: dict[str, StrictInt]
    parse_error_samples: list[ResponseParseErrorSample]

    @model_validator(mode="after")
    def check_invariants(self) -> AnalysisResponse:
        if self.schema_version != "1.0":
            raise ValueError("unsupported schema version")
        if self.error_levels not in (["ERROR", "FATAL"], ["ERROR"]):
            raise ValueError("invalid error levels")
        if self.total_lines != self.parsed_lines + self.unparseable_lines:
            raise ValueError("line counts are inconsistent")
        if any(count < 0 for count in self.error_counts.values()):
            raise ValueError("negative service error count")
        if self.total_errors != sum(self.error_counts.values()):
            raise ValueError("total error count is inconsistent")
        if self.total_errors > self.parsed_lines:
            raise ValueError("error count exceeds parsed lines")
        if any(count <= 0 for count in self.parse_error_counts.values()):
            raise ValueError("invalid parse error count")
        if sum(self.parse_error_counts.values()) != self.unparseable_lines:
            raise ValueError("parse error counts are inconsistent")
        highest = max(self.error_counts.values(), default=0)
        leaders = (
            sorted(service for service, count in self.error_counts.items() if count == highest)
            if highest
            else []
        )
        if (self.top_error_count, self.top_service, self.top_services) != (
            highest,
            leaders[0] if leaders else None,
            leaders,
        ):
            raise ValueError("top offender fields are inconsistent")
        if any(sample.reason not in self.parse_error_counts for sample in self.parse_error_samples):
            raise ValueError("sample reason has no matching count")
        if self.parse_error_samples != sorted(
            self.parse_error_samples, key=lambda sample: sample.line_number
        ):
            raise ValueError("parse error samples are out of order")
        return self


class ServiceLimitExceededError(Exception):
    """Raised when the service cardinality cap is exceeded."""

    pass


class AnalysisTimeoutError(Exception):
    """Raised when the analysis deadline or idle timeout is exceeded."""

    pass
