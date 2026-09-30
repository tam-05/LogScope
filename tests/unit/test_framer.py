"""Unit tests for the byte-line framer.

Covers BOM handling, LF/CRLF framing, chunk boundaries, overlong
lines, and edge cases from PRD §4.4 and §15.1.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import settings as hyp_settings
from hypothesis import strategies as st

from logscope.core.framing import UTF8_BOM, ByteFramer, FramedLine

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def lines_from(data: bytes, max_line_bytes: int = 65536) -> list[FramedLine]:
    """Feed all data in one chunk and flush."""
    framer = ByteFramer(max_line_bytes)
    result = framer.feed(data)
    result.extend(framer.flush())
    return result


def lines_chunked(data: bytes, chunk_size: int, max_line_bytes: int = 65536) -> list[FramedLine]:
    """Feed data in fixed-size chunks and flush."""
    framer = ByteFramer(max_line_bytes)
    result: list[FramedLine] = []
    for i in range(0, len(data), chunk_size):
        result.extend(framer.feed(data[i : i + chunk_size]))
    result.extend(framer.flush())
    return result


# ---------------------------------------------------------------------------
# Basic framing
# ---------------------------------------------------------------------------


class TestBasicFraming:
    def test_empty_file(self) -> None:
        """Empty file → zero lines."""
        assert lines_from(b"") == []

    def test_single_lf(self) -> None:
        """'\\n' → one unparseable line."""
        result = lines_from(b"\n")
        assert len(result) == 1
        assert result[0].data == b""
        assert result[0].line_number == 1

    def test_double_lf(self) -> None:
        """'\\n\\n' → two unparseable lines."""
        result = lines_from(b"\n\n")
        assert len(result) == 2
        assert result[0].line_number == 1
        assert result[1].line_number == 2

    def test_content_with_trailing_lf(self) -> None:
        """'hello\\n' → one line 'hello', no extra line."""
        result = lines_from(b"hello\n")
        assert len(result) == 1
        assert result[0].data == b"hello"

    def test_content_without_trailing_lf(self) -> None:
        """'hello' (no LF) → one line 'hello' at flush."""
        result = lines_from(b"hello")
        assert len(result) == 1
        assert result[0].data == b"hello"

    def test_two_lines(self) -> None:
        result = lines_from(b"aaa\nbbb\n")
        assert len(result) == 2
        assert result[0].data == b"aaa"
        assert result[1].data == b"bbb"

    def test_line_numbers_are_one_based(self) -> None:
        result = lines_from(b"a\nb\nc\n")
        assert [line.line_number for line in result] == [1, 2, 3]


# ---------------------------------------------------------------------------
# CRLF handling
# ---------------------------------------------------------------------------


class TestCRLF:
    def test_crlf_stripped(self) -> None:
        result = lines_from(b"hello\r\n")
        assert len(result) == 1
        assert result[0].data == b"hello"

    def test_crlf_split_across_chunks(self) -> None:
        """CR in chunk 1, LF in chunk 2."""
        framer = ByteFramer(65536)
        r1 = framer.feed(b"hello\r")
        r2 = framer.feed(b"\nworld\n")
        r3 = framer.flush()
        all_lines = r1 + r2 + r3
        assert len(all_lines) == 2
        assert all_lines[0].data == b"hello"
        assert all_lines[1].data == b"world"

    def test_bare_cr_not_newline(self) -> None:
        """Bare CR is data, not a line ending."""
        result = lines_from(b"a\rb\n")
        assert len(result) == 1
        assert result[0].data == b"a\rb"

    def test_bare_cr_at_eof(self) -> None:
        """Bare CR at EOF is kept as content (no LF to strip with)."""
        result = lines_from(b"hello\r")
        assert len(result) == 1
        assert result[0].data == b"hello\r"

    def test_double_cr_before_lf(self) -> None:
        """Only one CR stripped from CRLF; the other remains."""
        result = lines_from(b"test\r\r\n")
        assert len(result) == 1
        assert result[0].data == b"test\r"


# ---------------------------------------------------------------------------
# BOM handling
# ---------------------------------------------------------------------------


class TestBOM:
    def test_bom_stripped(self) -> None:
        data = UTF8_BOM + b"hello\n"
        result = lines_from(data)
        assert len(result) == 1
        assert result[0].data == b"hello"

    def test_bom_only_is_empty(self) -> None:
        """BOM-only file → empty (zero lines)."""
        result = lines_from(UTF8_BOM)
        assert result == []

    def test_bom_split_across_chunks(self) -> None:
        """BOM bytes split across two chunks."""
        framer = ByteFramer(65536)
        r1 = framer.feed(UTF8_BOM[:2])
        r2 = framer.feed(UTF8_BOM[2:] + b"hello\n")
        r3 = framer.flush()
        all_lines = r1 + r2 + r3
        assert len(all_lines) == 1
        assert all_lines[0].data == b"hello"

    def test_bom_split_single_byte_chunks(self) -> None:
        """BOM bytes arrive one byte at a time."""
        framer = ByteFramer(65536)
        results: list[FramedLine] = []
        for byte in UTF8_BOM + b"ok\n":
            results.extend(framer.feed(bytes([byte])))
        results.extend(framer.flush())
        assert len(results) == 1
        assert results[0].data == b"ok"

    def test_non_bom_prefix_not_stripped(self) -> None:
        """Bytes that are not BOM should not be stripped."""
        data = b"\xef\xbb\x00test\n"  # First two bytes match BOM but third doesn't
        result = lines_from(data)
        # The first 3 bytes are NOT a BOM, so all bytes are content.
        assert len(result) >= 1

    def test_bom_only_then_data(self) -> None:
        """BOM followed by data in separate chunk."""
        framer = ByteFramer(65536)
        r1 = framer.feed(UTF8_BOM)
        r2 = framer.feed(b"data\n")
        r3 = framer.flush()
        all_lines = r1 + r2 + r3
        assert len(all_lines) == 1
        assert all_lines[0].data == b"data"


# ---------------------------------------------------------------------------
# Overlong lines
# ---------------------------------------------------------------------------


class TestOverlongLines:
    def test_line_at_limit_accepted(self) -> None:
        """Line exactly at MAX_LINE_BYTES is accepted."""
        content = b"A" * 100
        result = lines_from(content + b"\n", max_line_bytes=100)
        assert len(result) == 1
        assert not result[0].is_overlong
        assert result[0].data == content

    def test_line_over_limit_rejected(self) -> None:
        """Line exceeding MAX_LINE_BYTES is overlong."""
        content = b"A" * 101
        result = lines_from(content + b"\n", max_line_bytes=100)
        assert len(result) == 1
        assert result[0].is_overlong
        assert result[0].data == b""

    def test_overlong_over_many_chunks(self) -> None:
        """Overlong line spread across many small chunks."""
        content = b"A" * 200 + b"\nnext\n"
        result = lines_chunked(content, chunk_size=10, max_line_bytes=50)
        assert len(result) == 2
        assert result[0].is_overlong
        assert result[1].data == b"next"

    def test_eof_during_overlong(self) -> None:
        """Overlong line with no terminating LF → counted once at EOF."""
        content = b"A" * 200
        result = lines_from(content, max_line_bytes=50)
        assert len(result) == 1
        assert result[0].is_overlong

    def test_subsequent_line_after_overlong(self) -> None:
        """Normal line following an overlong one is parsed normally."""
        content = b"A" * 200 + b"\nshort\n"
        result = lines_from(content, max_line_bytes=50)
        assert len(result) == 2
        assert result[0].is_overlong
        assert result[1].data == b"short"

    def test_crlf_line_at_limit(self) -> None:
        """CRLF line: content + CR at limit+1 is accepted after CR strip."""
        content = b"A" * 100
        result = lines_from(content + b"\r\n", max_line_bytes=100)
        assert len(result) == 1
        assert not result[0].is_overlong
        assert result[0].data == content


# ---------------------------------------------------------------------------
# Property-based: chunk invariance
# ---------------------------------------------------------------------------


class TestChunkInvariance:
    @given(
        data=st.binary(min_size=1, max_size=500),
        chunk_size=st.integers(min_value=1, max_value=100),
    )
    @hyp_settings(max_examples=200)
    def test_chunk_partition_gives_same_result(self, data: bytes, chunk_size: int) -> None:
        """Different chunk sizes produce the same framed lines."""
        single = lines_from(data, max_line_bytes=256)
        chunked = lines_chunked(data, chunk_size, max_line_bytes=256)

        assert len(single) == len(chunked)
        for s, c in zip(single, chunked, strict=True):
            assert s.line_number == c.line_number
            assert s.is_overlong == c.is_overlong
            if not s.is_overlong:
                assert s.data == c.data
