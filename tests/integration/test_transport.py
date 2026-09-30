"""Real-socket upload, overload, timeout, and disconnect checks."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

from tests.integration.conftest import running_server

SAMPLE = Path(__file__).resolve().parents[2] / "examples" / "sample.log"
EXPECTED = json.loads(
    (Path(__file__).resolve().parents[2] / "examples" / "expected_sample.json").read_text(
        encoding="utf-8"
    )
)
HEADERS = {"Content-Type": "application/octet-stream"}


async def test_live_chunked_upload_and_actual_byte_limit(live_server: str) -> None:
    data = SAMPLE.read_bytes()

    async def chunks():  # type: ignore[no-untyped-def]
        for offset in range(0, len(data), 7):
            yield data[offset : offset + 7]

    async with httpx.AsyncClient(timeout=5) as client:
        response = await client.post(
            f"{live_server}/api/v1/analyses", content=chunks(), headers=HEADERS
        )
    assert response.status_code == 200
    assert response.json() == EXPECTED

    with running_server({"MAX_UPLOAD_BYTES": "50"}) as server:
        async with httpx.AsyncClient(timeout=5) as client:
            at_limit = await client.post(
                f"{server}/api/v1/analyses", content=b"x" * 50, headers=HEADERS
            )
            response = await client.post(
                f"{server}/api/v1/analyses", content=chunks(), headers=HEADERS
            )
        assert at_limit.status_code == 200
        assert at_limit.json()["total_lines"] == 1
        assert response.status_code == 413
        assert response.json()["error"]["code"] == "FILE_TOO_LARGE"


async def test_live_overload_health_and_release() -> None:
    with running_server({"UPLOAD_IDLE_TIMEOUT_SECONDS": "2"}) as server:
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_body():  # type: ignore[no-untyped-def]
            yield b"2026-09-18 10:00:00 INFO held msg\n"
            started.set()
            await release.wait()

        async with httpx.AsyncClient(timeout=5) as client:
            held = asyncio.create_task(
                client.post(f"{server}/api/v1/analyses", content=slow_body(), headers=HEADERS)
            )
            await asyncio.wait_for(started.wait(), 3)
            await asyncio.sleep(0.1)
            busy = await client.post(f"{server}/api/v1/analyses", content=b"", headers=HEADERS)
            health = await client.get(f"{server}/health/live")
            release.set()
            finished = await held
            later = await client.post(f"{server}/api/v1/analyses", content=b"", headers=HEADERS)
        assert busy.status_code == 503
        assert busy.headers["Retry-After"] == "1"
        assert health.status_code == 200
        assert finished.status_code == 200
        assert later.status_code == 200


async def test_live_total_deadline_releases_slot() -> None:
    with running_server(
        {"UPLOAD_IDLE_TIMEOUT_SECONDS": "0.3", "ANALYSIS_TIMEOUT_SECONDS": "0.35"}
    ) as server:
        port = int(server.rsplit(":", 1)[1])
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            b"POST /api/v1/analyses HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Type: application/octet-stream\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n"
        )
        await writer.drain()
        for _ in range(5):
            await asyncio.sleep(0.1)
            try:
                writer.write(b"1\r\nx\r\n")
                await writer.drain()
            except OSError:
                break
        status = await asyncio.wait_for(reader.readline(), 3)
        assert b"408" in status
        writer.close()
        await writer.wait_closed()
        async with httpx.AsyncClient(timeout=3) as client:
            later = await client.post(f"{server}/api/v1/analyses", content=b"", headers=HEADERS)
        assert later.status_code == 200


async def test_live_idle_timeout_and_disconnect_release() -> None:
    with running_server({"UPLOAD_IDLE_TIMEOUT_SECONDS": "0.2"}) as server:
        port = int(server.rsplit(":", 1)[1])
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            b"POST /api/v1/analyses HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Type: application/octet-stream\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n"
            b"5\r\nhello\r\n"
        )
        await writer.drain()
        status = await asyncio.wait_for(reader.readline(), 3)
        assert b"408" in status
        writer.close()
        await writer.wait_closed()

        _, writer = await asyncio.open_connection("127.0.0.1", port)
        writer.write(
            b"POST /api/v1/analyses HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Type: application/octet-stream\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n"
            b"5\r\nhello\r\n"
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()
        await asyncio.sleep(0.2)
        async with httpx.AsyncClient(timeout=3) as client:
            later = await client.post(f"{server}/api/v1/analyses", content=b"", headers=HEADERS)
        assert later.status_code == 200
