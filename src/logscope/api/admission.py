"""Capacity reservation and release for analysis admission control.

Uses a thread-safe counter with atomic try-acquire semantics so the
check-and-reserve happens in one scheduling window.
"""

from __future__ import annotations

import threading


class AdmissionController:
    """Non-blocking analysis admission control.

    ``try_acquire`` atomically checks and reserves a slot.
    ``release`` returns exactly one slot.  Health endpoints bypass
    admission entirely.
    """

    def __init__(self, max_active: int) -> None:
        if max_active <= 0:
            raise ValueError(f"max_active must be positive, got {max_active}")
        self._max_active = max_active
        self._lock = threading.Lock()
        self._active = 0

    def try_acquire(self) -> bool:
        """Try to reserve an analysis slot.  Returns True if acquired."""
        with self._lock:
            if self._active >= self._max_active:
                return False
            self._active += 1
            return True

    def release(self) -> None:
        """Release one analysis slot."""
        with self._lock:
            if self._active <= 0:
                raise RuntimeError("AdmissionController: release without acquire")
            self._active -= 1

    @property
    def active(self) -> int:
        """Number of currently active analyses."""
        with self._lock:
            return self._active

    @property
    def available(self) -> bool:
        """Whether at least one slot is free."""
        with self._lock:
            return self._active < self._max_active
