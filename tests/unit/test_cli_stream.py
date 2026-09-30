"""Opened-file snapshot behavior without timing-dependent filesystem races."""

from __future__ import annotations

import io
import time

import pytest

from logscope.cli import LocalInputError, _snapshot_stream


async def test_append_after_start_is_not_uploaded() -> None:
    file = io.BytesIO(b"a" * 65_536 + b"b")
    stream = _snapshot_stream(file, 65_537, time.monotonic() + 5)
    first = await anext(stream)
    read_position = file.tell()
    file.seek(0, io.SEEK_END)
    file.write(b"new data")
    file.seek(read_position)
    second = await anext(stream)
    assert first + second == b"a" * 65_536 + b"b"
    with pytest.raises(StopAsyncIteration):
        await anext(stream)


async def test_truncation_reports_local_input_failure() -> None:
    file = io.BytesIO(b"a" * 65_536 + b"b")
    stream = _snapshot_stream(file, 65_537, time.monotonic() + 5)
    await anext(stream)
    file.truncate(65_536)
    with pytest.raises(LocalInputError, match="truncated"):
        await anext(stream)
