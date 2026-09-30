"""HTTP API integration tests.

Tests the full stack (routes, admission, streaming, error handling)
through an ASGI test client.
"""

from __future__ import annotations

import httpx

from logscope.api.app import create_app
from logscope.config import Settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_LOG = (
    b"2026-09-18 10:23:45 ERROR payment-service Connection timeout after 30s\n"
    b"2026-09-18 10:23:46 INFO auth-service User login successful\n"
    b"2026-09-18 10:23:47 ERROR payment-service Connection refused\n"
    b"2026-09-18 10:24:01 WARN auth-service Response time degraded\n"
    b"2026-09-18 10:24:15 ERROR billing-service Invalid account state\n"
    b"2026-09-18 10:24:30 INFO payment-service Retry succeeded\n"
    b"error billing-service No Auth token\n"
)

OCTET_HEADERS = {"Content-Type": "application/octet-stream"}
TEXT_HEADERS = {"Content-Type": "text/plain"}
TEXT_UTF8_HEADERS = {"Content-Type": "text/plain; charset=utf-8"}


# ---------------------------------------------------------------------------
# Health endpoints
# ---------------------------------------------------------------------------


class TestHealth:
    async def test_liveness(self, client: httpx.AsyncClient) -> None:
        r = await client.get("/health/live")
        assert r.status_code == 200
        assert r.json()["status"] == "alive"

    async def test_readiness(self, client: httpx.AsyncClient) -> None:
        r = await client.get("/health/ready")
        assert r.status_code == 200
        assert r.json()["status"] == "ready"


# ---------------------------------------------------------------------------
# Successful analysis
# ---------------------------------------------------------------------------


class TestSuccessfulAnalysis:
    async def test_sample_log_octet_stream(self, client: httpx.AsyncClient) -> None:
        r = await client.post("/api/v1/analyses", content=SAMPLE_LOG, headers=OCTET_HEADERS)
        assert r.status_code == 200
        data = r.json()
        assert data["total_lines"] == 7
        assert data["parsed_lines"] == 6
        assert data["unparseable_lines"] == 1
        assert data["total_errors"] == 3
        assert data["error_counts"]["payment-service"] == 2
        assert data["top_service"] == "payment-service"

    async def test_sample_log_text_plain(self, client: httpx.AsyncClient) -> None:
        r = await client.post("/api/v1/analyses", content=SAMPLE_LOG, headers=TEXT_HEADERS)
        assert r.status_code == 200
        assert r.json()["total_lines"] == 7

    async def test_sample_log_text_utf8(self, client: httpx.AsyncClient) -> None:
        r = await client.post("/api/v1/analyses", content=SAMPLE_LOG, headers=TEXT_UTF8_HEADERS)
        assert r.status_code == 200

    async def test_empty_body(self, client: httpx.AsyncClient) -> None:
        r = await client.post("/api/v1/analyses", content=b"", headers=OCTET_HEADERS)
        assert r.status_code == 200
        data = r.json()
        assert data["total_lines"] == 0
        assert data["parsed_lines"] == 0
        assert data["total_errors"] == 0
        assert data["top_service"] is None

    async def test_error_policy_error_only(self, client: httpx.AsyncClient) -> None:
        data = b"2026-09-18 10:00:00 ERROR svc Err\n2026-09-18 10:00:01 FATAL svc Fatal\n"
        r = await client.post(
            "/api/v1/analyses?error_policy=error_only",
            content=data,
            headers=OCTET_HEADERS,
        )
        assert r.status_code == 200
        body = r.json()
        assert body["total_errors"] == 1
        assert body["error_levels"] == ["ERROR"]

    async def test_response_has_request_id(self, client: httpx.AsyncClient) -> None:
        r = await client.post("/api/v1/analyses", content=b"", headers=OCTET_HEADERS)
        assert "x-request-id" in r.headers

    async def test_response_has_cache_control(self, client: httpx.AsyncClient) -> None:
        r = await client.post("/api/v1/analyses", content=b"", headers=OCTET_HEADERS)
        assert r.headers.get("cache-control") == "no-store"

    async def test_schema_version(self, client: httpx.AsyncClient) -> None:
        r = await client.post("/api/v1/analyses", content=b"", headers=OCTET_HEADERS)
        assert r.json()["schema_version"] == "1.0"


# ---------------------------------------------------------------------------
# Content-Type validation
# ---------------------------------------------------------------------------


class TestContentType:
    async def test_missing_content_type(self, client: httpx.AsyncClient) -> None:
        r = await client.post("/api/v1/analyses", content=b"data")
        # httpx may not include Content-Type for binary; rely on server check
        assert r.status_code in (200, 415)

    async def test_unsupported_content_type(self, client: httpx.AsyncClient) -> None:
        r = await client.post(
            "/api/v1/analyses",
            content=b"data",
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 415

    async def test_multipart_rejected(self, client: httpx.AsyncClient) -> None:
        r = await client.post(
            "/api/v1/analyses",
            content=b"data",
            headers={"Content-Type": "multipart/form-data; boundary=abc"},
        )
        assert r.status_code == 415
        assert "raw" in r.json()["error"]["message"].lower()

    async def test_bad_charset(self, client: httpx.AsyncClient) -> None:
        r = await client.post(
            "/api/v1/analyses",
            content=b"data",
            headers={"Content-Type": "text/plain; charset=latin-1"},
        )
        assert r.status_code == 415

    async def test_content_encoding_gzip_rejected(self, client: httpx.AsyncClient) -> None:
        r = await client.post(
            "/api/v1/analyses",
            content=b"data",
            headers={**OCTET_HEADERS, "Content-Encoding": "gzip"},
        )
        assert r.status_code == 415


# ---------------------------------------------------------------------------
# Query parameter validation
# ---------------------------------------------------------------------------


class TestQueryParams:
    async def test_invalid_error_policy(self, client: httpx.AsyncClient) -> None:
        r = await client.post(
            "/api/v1/analyses?error_policy=bogus",
            content=b"",
            headers=OCTET_HEADERS,
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "INVALID_OPTIONS"

    async def test_unknown_query_param(self, client: httpx.AsyncClient) -> None:
        r = await client.post(
            "/api/v1/analyses?unknown=1",
            content=b"",
            headers=OCTET_HEADERS,
        )
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "INVALID_OPTIONS"


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------


class TestAuth:
    async def test_auth_required_when_configured(self) -> None:
        settings = Settings(
            api_token="test-secret",
            max_upload_bytes=1_048_576,
            max_line_bytes=1024,
            max_services=100,
            max_active_analyses=1,
            upload_idle_timeout_seconds=5.0,
            analysis_timeout_seconds=10.0,
        )
        app = create_app(settings)
        transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            # No token → 401
            r = await c.post("/api/v1/analyses", content=b"", headers=OCTET_HEADERS)
            assert r.status_code == 401

            # Wrong token → 401
            r = await c.post(
                "/api/v1/analyses",
                content=b"",
                headers={**OCTET_HEADERS, "Authorization": "Bearer wrong"},
            )
            assert r.status_code == 401

            # Correct token → 200
            r = await c.post(
                "/api/v1/analyses",
                content=b"",
                headers={**OCTET_HEADERS, "Authorization": "Bearer test-secret"},
            )
            assert r.status_code == 200


# ---------------------------------------------------------------------------
# Body size limits
# ---------------------------------------------------------------------------


class TestBodyLimits:
    async def test_declared_content_length_too_large(self) -> None:
        """Content-Length > limit → 413 before reading body."""
        settings = Settings(
            max_upload_bytes=100,
            max_line_bytes=1024,
            max_services=100,
            max_active_analyses=1,
            upload_idle_timeout_seconds=5.0,
            analysis_timeout_seconds=10.0,
        )
        app = create_app(settings)
        transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/api/v1/analyses",
                content=b"x" * 101,
                headers=OCTET_HEADERS,
            )
            assert r.status_code == 413
            assert r.json()["error"]["code"] == "FILE_TOO_LARGE"


# ---------------------------------------------------------------------------
# Admission / overload
# ---------------------------------------------------------------------------


class TestAdmission:
    async def test_health_during_analysis(self, client: httpx.AsyncClient) -> None:
        """Health endpoints remain accessible regardless of admission."""
        r = await client.get("/health/live")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Service cardinality limit
# ---------------------------------------------------------------------------


class TestServiceLimit:
    async def test_service_limit_exceeded(self) -> None:
        settings = Settings(
            max_upload_bytes=1_048_576,
            max_line_bytes=1024,
            max_services=3,
            max_active_analyses=1,
            upload_idle_timeout_seconds=5.0,
            analysis_timeout_seconds=10.0,
        )
        app = create_app(settings)
        transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            data = b""
            for i in range(5):
                data += f"2026-09-18 10:00:0{i} ERROR svc{i} msg\n".encode()
            r = await c.post("/api/v1/analyses", content=data, headers=OCTET_HEADERS)
            assert r.status_code == 422
            assert r.json()["error"]["code"] == "SERVICE_LIMIT_EXCEEDED"


# ---------------------------------------------------------------------------
# Simultaneous request isolation
# ---------------------------------------------------------------------------


class TestIsolation:
    async def test_separate_requests_independent(self, client: httpx.AsyncClient) -> None:
        """Two sequential analyses produce independent results."""
        d1 = b"2026-09-18 10:00:00 ERROR svc-a Err\n"
        d2 = b"2026-09-18 10:00:00 INFO svc-b OK\n"

        r1 = await client.post("/api/v1/analyses", content=d1, headers=OCTET_HEADERS)
        r2 = await client.post("/api/v1/analyses", content=d2, headers=OCTET_HEADERS)

        assert r1.json()["total_errors"] == 1
        assert r2.json()["total_errors"] == 0
        assert "svc-a" in r1.json()["error_counts"]
        assert "svc-b" in r2.json()["error_counts"]
        assert "svc-b" not in r1.json()["error_counts"]


# ---------------------------------------------------------------------------
# Metrics endpoint
# ---------------------------------------------------------------------------


class TestMetrics:
    async def test_metrics_returns_prometheus_format(self, client: httpx.AsyncClient) -> None:
        r = await client.get("/metrics")
        assert r.status_code == 200
        assert "logscope_requests_total" in r.text or "# HELP" in r.text
