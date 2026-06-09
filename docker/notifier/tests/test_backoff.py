"""
Tests for the exponential backoff schedule.

Spec: PHASE2-SPEC.md §1.5.
Task body overrides cap to 60s (spec says 30s).
Schedule: failures 1..4 = 1, 2, 4, 8 s. 5+ capped at the configured maximum.
A success resets the failure counter so the next failure uses base delay.
"""

from __future__ import annotations

import pytest


def _try_import_backoff():
    pytest.importorskip("backoff", reason="backoff module not yet implemented")


def test_first_failure_returns_base_delay():
    _try_import_backoff()
    import backoff
    b = backoff.Backoff(cap_seconds=60, base_seconds=1, factor=2)
    assert b.delay(1) == 1


def test_exponential_curve():
    _try_import_backoff()
    import backoff
    b = backoff.Backoff(cap_seconds=60, base_seconds=1, factor=2)
    assert b.delay(1) == 1
    assert b.delay(2) == 2
    assert b.delay(3) == 4
    assert b.delay(4) == 8


def test_cap_at_60s():
    _try_import_backoff()
    import backoff
    b = backoff.Backoff(cap_seconds=60, base_seconds=1, factor=2)
    # The cap is reached when base * factor^(n-1) >= cap. With base=1, factor=2:
    # n=7 -> 1 * 2^6 = 64, capped to 60.
    assert b.delay(7) == 60
    assert b.delay(10) == 60
    assert b.delay(100) == 60


def test_cap_is_configurable():
    _try_import_backoff()
    import backoff
    b = backoff.Backoff(cap_seconds=30, base_seconds=1, factor=2)
    # With cap=30, base=1, factor=2: n=6 -> 32, capped to 30.
    assert b.delay(6) == 30
    assert b.delay(20) == 30


def test_reset_returns_to_base():
    _try_import_backoff()
    import backoff
    b = backoff.Backoff(cap_seconds=60, base_seconds=1, factor=2)
    # Use-and-increment path: calls without args increment and return.
    b.delay()  # 1
    b.delay()  # 2
    b.delay()  # 3
    b.reset()
    assert b.delay() == 1  # back to base after reset


def test_full_schedule_1_to_20():
    """
    Spec §1.5: 1, 2, 4, 8, ... capped at the configured cap.

    With base=1, factor=2, cap=60: the raw sequence is 1, 2, 4, 8, 16, 32, 64,
    128, ... and the cap kicks in at n=7 (64 -> 60).
    """
    _try_import_backoff()
    import backoff
    b = backoff.Backoff(cap_seconds=60, base_seconds=1, factor=2)
    expected = [1, 2, 4, 8, 16, 32, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60]
    actual = [b.delay(n) for n in range(1, 21)]
    assert actual == expected


def test_sleep_records_requested_duration(fake_clock):
    """
    The poll/WS loop must call the injected clock's sleep, not time.sleep.
    Spec §1.7 forbids log-spam and CPU burn; the smoke test is that the
    backoff's delay gets passed to the clock.
    """
    _try_import_backoff()
    import backoff
    b = backoff.Backoff(cap_seconds=60, base_seconds=1, factor=2)
    b.sleep_with(fake_clock, failures=3)
    assert fake_clock.sleeps == [4]


def test_async_sleep_records_requested_duration(fake_clock):
    _try_import_backoff()
    import backoff
    import asyncio
    b = backoff.Backoff(cap_seconds=60, base_seconds=1, factor=2)
    asyncio.run(b.asleep_with(fake_clock, failures=5))
    assert fake_clock.sleeps == [16]


def test_zero_failures_returns_zero():
    """A fresh backoff (0 failures) should not sleep at all."""
    _try_import_backoff()
    import backoff
    b = backoff.Backoff(cap_seconds=60, base_seconds=1, factor=2)
    assert b.delay(0) == 0


def test_immediate_reconnect_on_success():
    """
    The full loop: 5 failures, then a success, then 1 failure -> base delay.

    This is the 'immediate reconnect on a successful probe' property from
    the task body.
    """
    _try_import_backoff()
    import backoff
    b = backoff.Backoff(cap_seconds=60, base_seconds=1, factor=2)
    # Use-and-increment path
    for _ in range(5):
        b.delay()  # increments counter
    b.reset()  # success
    assert b.delay() == 1  # back to base


def test_incremental_use_and_increment():
    """delay() with no arg increments the internal counter and returns."""
    _try_import_backoff()
    import backoff
    b = backoff.Backoff(cap_seconds=60, base_seconds=1, factor=2)
    assert b.delay() == 1
    assert b.delay() == 2
    assert b.delay() == 4
    assert b.failures == 3
