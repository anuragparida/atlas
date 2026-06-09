"""Exponential backoff schedule per PHASE2-SPEC.md §1.5.

Schedule: failures 1..4 = base, base*factor, base*factor^2, base*factor^3.
Failures 5+ capped at ``cap_seconds``. A ``reset()`` puts the schedule back
to base on the next failure. ``delay(0)`` returns 0 (no sleep on the happy
path).

Kept in its own module so the WS loop, the poll loop, and the NTFY client
can all share one schedule (and so the test suite can unit-test the curve
without spinning up the full service).
"""

from __future__ import annotations

import math
from typing import Callable, Protocol


class _ClockLike(Protocol):
    """Minimal interface the Backoff needs from a clock.

    ``FakeClock`` in tests/conftest.py satisfies this protocol; a real
    asyncio sleep is just ``asyncio.sleep`` wrapped in an adapter."""

    def sleep(self, seconds: float) -> None: ...
    async def async_sleep(self, seconds: float) -> None: ...


class Backoff:
    def __init__(
        self,
        cap_seconds: float = 60.0,
        base_seconds: float = 1.0,
        factor: float = 2.0,
    ) -> None:
        self._cap = float(cap_seconds)
        self._base = float(base_seconds)
        self._factor = float(factor)
        self._failures = 0

    @property
    def failures(self) -> int:
        return self._failures

    def reset(self) -> None:
        """Zero the failure counter. Call after a successful probe/connect."""
        self._failures = 0

    def delay(self, failures: int | None = None) -> float:
        """Return the next sleep duration in seconds.

        Pass ``failures`` to peek at a specific step (does not mutate).
        Pass nothing to use-and-increment the internal counter.

        Schedule: ``min(base * factor^(n-1), cap)``. The cap is the
        maximum sleep; the schedule grows exponentially up to it."""
        if failures is None:
            self._failures += 1
            failures = self._failures
        if failures <= 0:
            return 0.0
        raw = self._base * math.pow(self._factor, failures - 1)
        return min(raw, self._cap)

    def sleep_with(self, clock: _ClockLike, failures: int | None = None) -> float:
        """Compute the delay, then ask the clock to sleep. Returns the delay."""
        d = self.delay(failures)
        if d > 0:
            clock.sleep(d)
        return d

    async def asleep_with(self, clock: _ClockLike, failures: int | None = None) -> float:
        """Async variant of sleep_with. Returns the delay."""
        d = self.delay(failures)
        if d > 0:
            await clock.async_sleep(d)
        return d
