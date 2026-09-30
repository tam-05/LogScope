"""FastAPI application factory, routes, handlers, and lifecycle.

``create_app`` builds an isolated, testable application instance.
All routes, middleware, and lifecycle hooks are wired inside the
factory — no module-level app singleton.
"""

from __future__ import annotations

import hmac
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import anyio
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.middleware.base import RequestResponseEndpoint
from starlette.requests import ClientDisconnect

from logscope.api.admission import AdmissionController
from logscope.api.observability import (
    ACTIVE_ANALYSES,
    ANALYSIS_DURATION,
    BUSY_REJECTIONS,
    INPUT_BYTES,
    PARSED_LINES,
    REQUEST_COUNT,
    UNPARSEABLE_LINES,
    setup_logging,
)
from logscope.config import Settings
from logscope.core.analyzer import StreamingAnalyzer
from logscope.models import (
    AnalysisResponse,
    AnalysisTimeoutError,
    ErrorPolicy,
    ServiceLimitExceededError,
)

logger = logging.getLogger("logscope")

_SAMPLE_RESPONSE: dict[str, Any] = {
    "schema_version": "1.0",
    "total_lines": 7,
    "parsed_lines": 6,
    "unparseable_lines": 1,
    "total_errors": 3,
    "error_levels": ["ERROR", "FATAL"],
    "error_counts": {"auth-service": 0, "billing-service": 1, "payment-service": 2},
    "top_service": "payment-service",
    "top_services": ["payment-service"],
    "top_error_count": 2,
    "parse_error_counts": {"invalid_format": 1},
    "parse_error_samples": [{"line_number": 7, "reason": "invalid_format"}],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _error_response(
    status: int,
    code: str,
    message: str,
    request_id: str,
    *,
    extra_headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build a standard error envelope."""
    headers: dict[str, str] = {
        "X-Request-ID": request_id,
        "Cache-Control": "no-store",
    }
    if extra_headers:
        headers.update(extra_headers)
    return JSONResponse(
        status_code=status,
        content={
            "error": {
                "code": code,
                "message": message,
                "request_id": request_id,
            }
        },
        headers=headers,
    )


def _parse_content_type(raw: str) -> tuple[str, dict[str, str]]:
    """Parse ``Content-Type`` into (media_type, params)."""
    parts = [p.strip() for p in raw.split(";")]
    media = parts[0].lower()
    params: dict[str, str] = {}
    for part in parts[1:]:
        if "=" in part:
            k, v = part.split("=", 1)
            params[k.strip().lower()] = v.strip().strip('"').lower()
    return media, params


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construct an isolated, testable application instance."""
    if settings is None:
        settings = Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        app.state.ready = True
        logger.info("LogScope ready")
        yield
        app.state.ready = False
        logger.info("LogScope shutting down")

    app = FastAPI(
        title="LogScope",
        description="Streaming log file analyzer",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.state.settings = settings
    app.state.admission = AdmissionController(settings.max_active_analyses)
    app.state.ready = True

    setup_logging(settings.log_level)

    # ------------------------------------------------------------------
    # Middleware: request ID, common headers, structured request log
    # ------------------------------------------------------------------

    @app.middleware("http")
    async def common_middleware(request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        start = time.monotonic()

        response = await call_next(request)

        elapsed_ms = round((time.monotonic() - start) * 1000, 2)

        route = request.scope.get("route")
        route_path = getattr(route, "path", "unmatched")
        fields: dict[str, Any] = {
            "request_id": request_id,
            "method": request.method,
            "path": route_path,
            "status": response.status_code,
            "elapsed_ms": elapsed_ms,
        }
        if hasattr(request.state, "received_bytes"):
            fields["received_bytes"] = request.state.received_bytes
        if hasattr(request.state, "summary"):
            fields.update(request.state.summary)
        logger.info("request completed", extra=fields)

        REQUEST_COUNT.labels(
            method=request.method,
            path=route_path,
            status=str(response.status_code),
        ).inc()

        response.headers["X-Request-ID"] = request_id
        response.headers["Cache-Control"] = "no-store"
        return response

    # ------------------------------------------------------------------
    # Health endpoints (bypass admission)
    # ------------------------------------------------------------------

    @app.get("/health/live")
    async def liveness() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready")
    async def readiness() -> Response:
        if not getattr(app.state, "ready", False):
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        return JSONResponse(content={"status": "ready"})

    # ------------------------------------------------------------------
    # Metrics endpoint (internal only)
    # ------------------------------------------------------------------

    @app.get("/metrics")
    async def metrics() -> Response:
        return Response(
            content=generate_latest(),
            media_type=CONTENT_TYPE_LATEST,
        )

    # ------------------------------------------------------------------
    # Analysis endpoint
    # ------------------------------------------------------------------

    @app.post(
        "/api/v1/analyses",
        response_model=AnalysisResponse,
        responses={200: {"content": {"application/json": {"example": _SAMPLE_RESPONSE}}}},
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/octet-stream": {"schema": {"type": "string", "format": "binary"}},
                    "text/plain": {"schema": {"type": "string", "format": "binary"}},
                },
            }
        },
    )
    async def analyze(request: Request) -> Response:
        request_id: str = getattr(request.state, "request_id", str(uuid.uuid4()))
        s: Settings = request.app.state.settings
        admission: AdmissionController = request.app.state.admission

        # --- 1. Validate query parameters ---
        valid_params = {"error_policy"}
        unknown = set(request.query_params.keys()) - valid_params
        if unknown:
            return _error_response(
                422,
                "INVALID_OPTIONS",
                f"Unknown query parameter(s): {', '.join(sorted(unknown))}",
                request_id,
            )

        error_policy_str = request.query_params.get("error_policy", "error_and_fatal")
        if error_policy_str not in ("error_and_fatal", "error_only"):
            return _error_response(
                422,
                "INVALID_OPTIONS",
                f"Invalid error_policy: '{error_policy_str}'. "
                "Must be 'error_and_fatal' or 'error_only'.",
                request_id,
            )

        # --- 2. Validate Content-Type ---
        ct_raw = request.headers.get("content-type", "")
        if not ct_raw:
            return _error_response(
                415,
                "UNSUPPORTED_MEDIA_TYPE",
                "Content-Type is required. Use application/octet-stream or text/plain.",
                request_id,
            )

        media_type, ct_params = _parse_content_type(ct_raw)

        if media_type == "multipart/form-data":
            return _error_response(
                415,
                "UNSUPPORTED_MEDIA_TYPE",
                "multipart/form-data is not supported. "
                "Send raw file bytes with Content-Type: application/octet-stream.",
                request_id,
            )

        if media_type == "text/plain":
            charset = ct_params.get("charset")
            if charset is not None and charset != "utf-8":
                return _error_response(
                    415,
                    "UNSUPPORTED_MEDIA_TYPE",
                    f"Unsupported charset: '{charset}'. Only UTF-8 is accepted.",
                    request_id,
                )
        elif media_type != "application/octet-stream":
            return _error_response(
                415,
                "UNSUPPORTED_MEDIA_TYPE",
                f"Unsupported Content-Type: '{media_type}'. "
                "Use application/octet-stream or text/plain.",
                request_id,
            )

        # --- 3. Validate Content-Encoding ---
        encoding = request.headers.get("content-encoding", "").lower().strip()
        if encoding and encoding != "identity":
            return _error_response(
                415,
                "UNSUPPORTED_MEDIA_TYPE",
                f"Unsupported Content-Encoding: '{encoding}'. "
                "Only identity (no encoding) is accepted.",
                request_id,
            )

        # --- 4. Authentication ---
        if s.api_token:
            auth_header = request.headers.get("authorization", "")
            if not auth_header.startswith("Bearer "):
                return _error_response(
                    401, "UNAUTHORIZED", "Missing or invalid bearer token.", request_id
                )
            token = auth_header[7:]
            if not hmac.compare_digest(token, s.api_token):
                return _error_response(
                    401, "UNAUTHORIZED", "Missing or invalid bearer token.", request_id
                )

        # --- 5. Declared Content-Length ---
        cl_header = request.headers.get("content-length")
        if cl_header is not None:
            try:
                declared_length = int(cl_header)
            except ValueError:
                return _error_response(
                    400, "INVALID_REQUEST", "Invalid Content-Length header.", request_id
                )
            if declared_length < 0:
                return _error_response(
                    400, "INVALID_REQUEST", "Invalid Content-Length header.", request_id
                )
            if declared_length > s.max_upload_bytes:
                limit_mib = s.max_upload_bytes // (1024 * 1024)
                return _error_response(
                    413,
                    "FILE_TOO_LARGE",
                    f"The upload exceeds the configured {limit_mib} MiB limit.",
                    request_id,
                )

        # --- 6. Admission ---
        if not admission.try_acquire():
            BUSY_REJECTIONS.inc()
            return _error_response(
                503,
                "SERVER_BUSY",
                "No analysis capacity available. Retry shortly.",
                request_id,
                extra_headers={"Retry-After": "1"},
            )

        ACTIVE_ANALYSES.inc()
        start_time = time.monotonic()
        received_bytes = 0
        request.state.received_bytes = 0

        try:
            policy = ErrorPolicy(error_policy_str)
            analyzer = StreamingAnalyzer(
                error_policy=policy,
                max_line_bytes=s.max_line_bytes,
                max_services=s.max_services,
                max_parse_error_samples=s.max_parse_error_samples,
            )

            deadline = start_time + s.analysis_timeout_seconds

            # --- 7. Stream body and parse ---
            body_iter = request.stream().__aiter__()
            while True:
                now = time.monotonic()
                if now >= deadline:
                    raise AnalysisTimeoutError("Analysis deadline exceeded.")

                remaining = deadline - now
                idle = min(s.upload_idle_timeout_seconds, remaining)

                try:
                    with anyio.fail_after(idle):
                        chunk = await body_iter.__anext__()
                except TimeoutError as exc:
                    raise AnalysisTimeoutError("Upload idle timeout exceeded.") from exc
                except StopAsyncIteration:
                    break

                received_bytes += len(chunk)
                request.state.received_bytes = received_bytes
                if received_bytes > s.max_upload_bytes:
                    return _error_response(
                        413,
                        "FILE_TOO_LARGE",
                        f"The upload exceeds the configured "
                        f"{s.max_upload_bytes // (1024 * 1024)} MiB limit.",
                        request_id,
                    )

                # Offload bounded batches to worker thread.
                for i in range(0, len(chunk), s.processing_chunk_bytes):
                    batch = chunk[i : i + s.processing_chunk_bytes]
                    await anyio.to_thread.run_sync(analyzer.feed, batch)

                    if time.monotonic() >= deadline:
                        raise AnalysisTimeoutError("Analysis deadline exceeded.")

            # --- 8. Finalize ---
            summary = await anyio.to_thread.run_sync(analyzer.finish)
            if time.monotonic() >= deadline:
                raise AnalysisTimeoutError("Analysis deadline exceeded during finalization.")

            elapsed = time.monotonic() - start_time
            ANALYSIS_DURATION.observe(elapsed)
            PARSED_LINES.inc(summary.parsed_lines)
            UNPARSEABLE_LINES.inc(summary.unparseable_lines)
            request.state.summary = {
                "total_lines": summary.total_lines,
                "parsed_lines": summary.parsed_lines,
                "unparseable_lines": summary.unparseable_lines,
                "total_errors": summary.total_errors,
            }

            logger.info(
                "analysis completed",
                extra={
                    "request_id": request_id,
                    "received_bytes": received_bytes,
                    "elapsed_ms": round(elapsed * 1000, 2),
                    "total_lines": summary.total_lines,
                    "parsed_lines": summary.parsed_lines,
                    "unparseable_lines": summary.unparseable_lines,
                    "total_errors": summary.total_errors,
                },
            )

            return JSONResponse(
                content=summary.to_dict(),
                headers={
                    "X-Request-ID": request_id,
                    "Cache-Control": "no-store",
                },
            )

        except AnalysisTimeoutError:
            return _error_response(
                408,
                "ANALYSIS_TIMEOUT",
                "Upload idle timeout or analysis deadline exceeded.",
                request_id,
            )
        except ServiceLimitExceededError:
            return _error_response(
                422,
                "SERVICE_LIMIT_EXCEEDED",
                f"Service cardinality cap ({s.max_services}) exceeded.",
                request_id,
            )
        except ClientDisconnect:
            raise
        except Exception as exc:
            logger.error(
                "Unexpected error during analysis",
                extra={"request_id": request_id, "exc_type": type(exc).__name__},
            )
            return _error_response(
                500, "INTERNAL_ERROR", "An unexpected error occurred.", request_id
            )
        finally:
            INPUT_BYTES.inc(received_bytes)
            ACTIVE_ANALYSES.dec()
            admission.release()

    return app
