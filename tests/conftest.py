"""Shared test fixtures."""

from __future__ import annotations

import pytest

from logscope.config import Settings


@pytest.fixture
def settings() -> Settings:
    """Settings with small limits suitable for fast automated tests."""
    return Settings(
        max_upload_bytes=1_048_576,  # 1 MiB
        max_line_bytes=1024,
        max_services=100,
        processing_chunk_bytes=1024,
        max_parse_error_samples=10,
        max_active_analyses=1,
        upload_idle_timeout_seconds=5.0,
        analysis_timeout_seconds=10.0,
    )


@pytest.fixture
def sample_log() -> bytes:
    """The exact 7-line fixture from PRD §5.2."""
    return (
        b"2026-09-18 10:23:45 ERROR payment-service Connection timeout after 30s\n"
        b"2026-09-18 10:23:46 INFO auth-service User login successful\n"
        b"2026-09-18 10:23:47 ERROR payment-service Connection refused\n"
        b"2026-09-18 10:24:01 WARN auth-service Response time degraded\n"
        b"2026-09-18 10:24:15 ERROR billing-service Invalid account state\n"
        b"2026-09-18 10:24:30 INFO payment-service Retry succeeded\n"
        b"error billing-service No Auth token\n"
    )
