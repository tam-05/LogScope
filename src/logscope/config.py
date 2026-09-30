"""Typed validated settings for LogScope."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

_VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


@dataclass
class Settings:
    """Application configuration loaded from environment variables.

    Invalid values prevent startup with a clear error message.
    """

    max_upload_bytes: int = 104_857_600  # 100 MiB
    max_line_bytes: int = 65_536
    max_services: int = 10_000
    processing_chunk_bytes: int = 65_536
    max_parse_error_samples: int = 10
    max_active_analyses: int = 1
    upload_idle_timeout_seconds: float = 15.0
    analysis_timeout_seconds: float = 120.0
    api_token: str | None = None
    require_api_token: bool = False
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        """Validate all settings on construction."""
        errors: list[str] = []
        if self.max_upload_bytes <= 0:
            errors.append(f"MAX_UPLOAD_BYTES must be positive, got {self.max_upload_bytes}")
        if self.max_line_bytes <= 0:
            errors.append(f"MAX_LINE_BYTES must be positive, got {self.max_line_bytes}")
        if self.max_services <= 0:
            errors.append(f"MAX_SERVICES must be positive, got {self.max_services}")
        if self.processing_chunk_bytes <= 0:
            errors.append(
                f"PROCESSING_CHUNK_BYTES must be positive, got {self.processing_chunk_bytes}"
            )
        if self.max_parse_error_samples < 0:
            errors.append(
                f"MAX_PARSE_ERROR_SAMPLES must be non-negative, got {self.max_parse_error_samples}"
            )
        if self.max_active_analyses <= 0:
            errors.append(f"MAX_ACTIVE_ANALYSES must be positive, got {self.max_active_analyses}")
        if (
            not math.isfinite(self.upload_idle_timeout_seconds)
            or self.upload_idle_timeout_seconds <= 0
        ):
            errors.append(
                "UPLOAD_IDLE_TIMEOUT_SECONDS must be positive, "
                f"got {self.upload_idle_timeout_seconds}"
            )
        if not math.isfinite(self.analysis_timeout_seconds) or self.analysis_timeout_seconds <= 0:
            errors.append(
                f"ANALYSIS_TIMEOUT_SECONDS must be positive, got {self.analysis_timeout_seconds}"
            )
        if self.log_level.upper() not in _VALID_LOG_LEVELS:
            errors.append(
                f"LOG_LEVEL must be one of {sorted(_VALID_LOG_LEVELS)}, got {self.log_level!r}"
            )
        if self.require_api_token and not self.api_token:
            errors.append("API_TOKEN must be set for this deployment")
        if errors:
            raise ValueError("Invalid settings:\n  " + "\n  ".join(errors))

    @classmethod
    def from_env(cls) -> Settings:
        """Load settings from environment variables.

        Unset variables use the dataclass defaults. Invalid values
        raise ``ValueError`` and prevent startup.
        """
        kwargs: dict[str, object] = {}

        _env_map: dict[str, tuple[str, type]] = {
            "MAX_UPLOAD_BYTES": ("max_upload_bytes", int),
            "MAX_LINE_BYTES": ("max_line_bytes", int),
            "MAX_SERVICES": ("max_services", int),
            "PROCESSING_CHUNK_BYTES": ("processing_chunk_bytes", int),
            "MAX_PARSE_ERROR_SAMPLES": ("max_parse_error_samples", int),
            "MAX_ACTIVE_ANALYSES": ("max_active_analyses", int),
            "UPLOAD_IDLE_TIMEOUT_SECONDS": ("upload_idle_timeout_seconds", float),
            "ANALYSIS_TIMEOUT_SECONDS": ("analysis_timeout_seconds", float),
            "API_TOKEN": ("api_token", str),
            "LOG_LEVEL": ("log_level", str),
        }

        for env_key, (field_name, field_type) in _env_map.items():
            value = os.environ.get(env_key)
            if value is not None:
                try:
                    if field_type is str:
                        kwargs[field_name] = value
                    else:
                        kwargs[field_name] = field_type(value)
                except (ValueError, TypeError) as exc:
                    raise ValueError(
                        f"Invalid value for environment variable {env_key}: {value!r}"
                    ) from exc

        kwargs["require_api_token"] = os.environ.get("REQUIRE_API_TOKEN") == "1"
        return cls(**kwargs)  # type: ignore[arg-type]
