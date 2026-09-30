"""Contract details that an in-process client can verify precisely."""

from __future__ import annotations

import httpx
import pytest

from logscope.api.app import create_app
from logscope.config import Settings
from logscope.core.analyzer import StreamingAnalyzer

HEADERS = {"Content-Type": "application/octet-stream"}


def test_openapi_describes_raw_body_and_result() -> None:
    operation = create_app().openapi()["paths"]["/api/v1/analyses"]["post"]
    request_body = operation["requestBody"]
    assert request_body["content"]["application/octet-stream"]["schema"]["format"] == "binary"
    assert request_body["content"]["text/plain"]["schema"]["format"] == "binary"
    success = operation["responses"]["200"]["content"]["application/json"]
    assert success["schema"]
    assert success["example"]["total_lines"] == 7


async def test_injected_parser_failure_is_safe_and_releases_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(Settings(max_active_analyses=1))

    def broken_feed(self: StreamingAnalyzer, chunk: bytes) -> None:
        raise RuntimeError("RAW_SENTINEL_FROM_EXCEPTION")

    with monkeypatch.context() as patch:
        patch.setattr(StreamingAnalyzer, "feed", broken_feed)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/api/v1/analyses", content=b"x", headers=HEADERS)
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "RAW_SENTINEL" not in response.text
    assert app.state.admission.active == 0
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        later = await client.post("/api/v1/analyses", content=b"", headers=HEADERS)
    assert later.status_code == 200


async def test_metrics_do_not_label_unmatched_user_path() -> None:
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.get("/SECRET_SENTINEL_UNMATCHED_PATH")
        metrics = await client.get("/metrics")
    assert "SECRET_SENTINEL_UNMATCHED_PATH" not in metrics.text
    assert 'path="unmatched"' in metrics.text


async def test_application_logs_do_not_include_raw_messages_or_tokens(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app = create_app(Settings(api_token="TOKEN_SENTINEL_PRIVATE"))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/analyses",
            content=b"2026-09-18 10:00:00 ERROR svc MESSAGE_SENTINEL_PRIVATE\n",
            headers={**HEADERS, "Authorization": "Bearer TOKEN_SENTINEL_PRIVATE"},
        )
    assert response.status_code == 200
    emitted = capsys.readouterr().err
    assert "MESSAGE_SENTINEL_PRIVATE" not in emitted
    assert "TOKEN_SENTINEL_PRIVATE" not in emitted
