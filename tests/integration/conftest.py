"""Integration test fixtures: ASGI test client and server helpers."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager

import httpx
import pytest

from logscope.api.app import create_app
from logscope.config import Settings


@contextmanager
def running_server(extra_env: dict[str, str] | None = None) -> Iterator[str]:
    """Run the actual Uvicorn transport on an ephemeral loopback port."""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = os.environ.copy()
    environment.update(extra_env or {})
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "logscope.api.app:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-access-log",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError(f"Uvicorn exited with code {process.returncode}")
            try:
                if httpx.get(f"{base_url}/health/live", timeout=0.2).status_code == 200:
                    break
            except httpx.RequestError:
                time.sleep(0.05)
        else:
            raise RuntimeError("Uvicorn did not start")
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.fixture(scope="module")
def live_server() -> Iterator[str]:
    with running_server() as base_url:
        yield base_url


@pytest.fixture
def test_settings() -> Settings:
    """Small limits for fast integration tests."""
    return Settings(
        max_upload_bytes=1_048_576,  # 1 MiB
        max_line_bytes=1024,
        max_services=100,
        processing_chunk_bytes=512,
        max_parse_error_samples=10,
        max_active_analyses=1,
        upload_idle_timeout_seconds=5.0,
        analysis_timeout_seconds=10.0,
    )


@pytest.fixture
def app(test_settings: Settings):  # type: ignore[no-untyped-def]
    return create_app(test_settings)


@pytest.fixture
async def client(app):  # type: ignore[no-untyped-def]
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
