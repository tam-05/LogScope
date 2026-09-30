"""Configuration and wire-schema regression tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from logscope.config import Settings
from logscope.models import AnalysisResponse

SAMPLE_EXPECTED = Path(__file__).resolve().parents[2] / "examples" / "expected_sample.json"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), 0.0])
def test_invalid_timeouts_prevent_startup(value: float) -> None:
    with pytest.raises(ValueError):
        Settings(upload_idle_timeout_seconds=value)
    with pytest.raises(ValueError):
        Settings(analysis_timeout_seconds=value)


def test_scale_profile_requires_token() -> None:
    with pytest.raises(ValueError, match="API_TOKEN"):
        Settings(require_api_token=True)
    Settings(require_api_token=True, api_token="local-test-token")


def test_wire_response_validates_counts_and_types() -> None:
    sample = json.loads(SAMPLE_EXPECTED.read_text(encoding="utf-8"))
    AnalysisResponse.model_validate(sample)
    with pytest.raises(ValidationError):
        AnalysisResponse.model_validate({**sample, "total_errors": 99})
    with pytest.raises(ValidationError):
        AnalysisResponse.model_validate({**sample, "total_lines": "7"})
