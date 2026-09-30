"""Stateful bounded byte-line framing.

Handles UTF-8 BOM stripping, LF/CRLF line termination (including CRLF
split across chunks), and byte-length limits on physical lines.

Does NOT interpret a bare CR as a newline.
"""

from __future__ import annotations

from dataclasses import dataclass

UTF8_BOM = b"\xef\xbb\xbf"


@dataclass(frozen=True, slots=True)
class FramedLine:
    """One physical line produced by the framer."""

    data: bytes
    """Line content (empty for overlong lines)."""

    line_number: int
    """One-based physical line number."""

    is_overlong: bool
    """True when the line exceeded the byte limit."""


class ByteFramer:
    """Incremental byte-line framer with bounded buffering.

    Feed arbitrary byte segments via :meth:`feed`; completed lines are
    returned immediately.  Call :meth:`flush` at EOF to emit a final
    unterminated line if present.

    The byte limit applies to line *content* excluding the LF terminator
    and one CR belonging to CRLF.  The buffer retains at most
    ``max_line_bytes + 1`` bytes (the extra byte is a CR candidate that
    may later be stripped) before declaring the line overlong.
    """

    def __init__(self, max_line_bytes: int) -> None:
        self._max_line_bytes = max_line_bytes
        self._buffer = bytearray()
        self._bom_stripped = False
        self._bom_buffer = bytearray()
        self._line_number = 0
        self._in_overlong = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def feed(self, chunk: bytes) -> list[FramedLine]:
        """Accept the next byte segment and return any completed lines."""
        if not chunk:
            return []

        data = chunk
        if not self._bom_stripped:
            bom_data = self._handle_bom(chunk)
            if bom_data is None:
                return []  # still accumulating for BOM check
            data = bom_data

        results: list[FramedLine] = []
        self._scan_lines(data, results)
        return results

    def flush(self) -> list[FramedLine]:
        """Finalize at EOF: emit any remaining buffered content as one line."""
        results: list[FramedLine] = []

        # Resolve a pending BOM check that never received enough bytes.
        if not self._bom_stripped:
            self._bom_stripped = True
            remaining = bytes(self._bom_buffer)
            self._bom_buffer.clear()
            if len(remaining) >= 3 and remaining[:3] == UTF8_BOM:
                remaining = remaining[3:]
            if remaining:
                self._buffer.extend(remaining)

        if self._in_overlong:
            self._line_number += 1
            results.append(FramedLine(b"", self._line_number, True))
            self._in_overlong = False
        elif self._buffer:
            line_data = bytes(self._buffer)
            self._buffer.clear()
            # No CR stripping at EOF — there is no LF to pair with.
            if len(line_data) > self._max_line_bytes:
                self._line_number += 1
                results.append(FramedLine(b"", self._line_number, True))
            else:
                self._line_number += 1
                results.append(FramedLine(line_data, self._line_number, False))

        return results

    @property
    def line_count(self) -> int:
        """Total lines emitted so far (before flush)."""
        return self._line_number

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _handle_bom(self, data: bytes) -> bytes | None:
        """Accumulate the first bytes to check for a UTF-8 BOM.

        Returns the data to process (BOM removed if present) or ``None``
        if more bytes are needed.
        """
        needed = max(0, 3 - len(self._bom_buffer))
        self._bom_buffer.extend(data[:needed])

        if len(self._bom_buffer) < 3:
            # Could still be a partial BOM prefix — wait for more.
            if bytes(self._bom_buffer) == UTF8_BOM[: len(self._bom_buffer)]:
                return None
            # Not a BOM prefix; release everything.
            self._bom_stripped = True
            result = bytes(self._bom_buffer) + data[needed:]
            self._bom_buffer.clear()
            return result

        # Enough bytes to decide.
        self._bom_stripped = True
        if bytes(self._bom_buffer[:3]) == UTF8_BOM:
            result = data[needed:]
        else:
            result = bytes(self._bom_buffer) + data[needed:]
        self._bom_buffer.clear()
        return result

    def _scan_lines(self, data: bytes, results: list[FramedLine]) -> None:
        """Scan *data* for LF terminators, emitting completed lines."""
        pos = 0
        while pos < len(data):
            lf_pos = data.find(b"\n", pos)

            if lf_pos == -1:
                # No LF remaining in this chunk.
                segment = data[pos:]
                if self._in_overlong:
                    pass  # discard until next LF
                else:
                    if len(self._buffer) + len(segment) > self._max_line_bytes + 1:
                        self._in_overlong = True
                        self._buffer.clear()
                    else:
                        self._buffer.extend(segment)
                break

            # Found an LF at *lf_pos*.
            segment = data[pos:lf_pos]

            if self._in_overlong:
                # End of the overlong line.
                self._line_number += 1
                results.append(FramedLine(b"", self._line_number, True))
                self._in_overlong = False
            else:
                if len(self._buffer) + len(segment) > self._max_line_bytes + 1:
                    self._line_number += 1
                    results.append(FramedLine(b"", self._line_number, True))
                    self._buffer.clear()
                    pos = lf_pos + 1
                    continue
                self._buffer.extend(segment)
                line_data = bytes(self._buffer)
                self._buffer.clear()

                # Strip exactly one CR belonging to CRLF.
                if line_data.endswith(b"\r"):
                    line_data = line_data[:-1]

                if len(line_data) > self._max_line_bytes:
                    self._line_number += 1
                    results.append(FramedLine(b"", self._line_number, True))
                else:
                    self._line_number += 1
                    results.append(FramedLine(line_data, self._line_number, False))

            pos = lf_pos + 1
