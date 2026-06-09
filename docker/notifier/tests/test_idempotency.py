"""
Tests for the dedup cache.

Spec: PHASE2-SPEC.md §1.8.
Task body says: in-memory dedup keyed on (chat_id, completion_timestamp).
Window: 1h. 1000-entry LRU cap. Restart loses state (documented trade-off).
"""

from __future__ import annotations

import pytest


def _cache_cls():
    pytest.importorskip("idempotency", reason="idempotency module not yet implemented")
    import idempotency
    for name in ("DedupCache", "IdempotencyCache", "Cache"):
        cls = getattr(idempotency, name, None)
        if cls is not None:
            return cls
    raise RuntimeError(
        "idempotency module does not expose DedupCache / IdempotencyCache / Cache"
    )


def test_first_event_is_not_duplicate():
    Cls = _cache_cls()
    cache = Cls()
    assert cache.is_duplicate("chat-1", 1_700_000_000.0) is False


def test_same_chat_id_and_ts_is_duplicate():
    Cls = _cache_cls()
    cache = Cls()
    cache.is_duplicate("chat-1", 1_700_000_000.0)
    assert cache.is_duplicate("chat-1", 1_700_000_000.0) is True


def test_same_chat_id_different_ts_is_not_duplicate():
    Cls = _cache_cls()
    cache = Cls()
    cache.is_duplicate("chat-1", 1_700_000_000.0)
    assert cache.is_duplicate("chat-1", 1_700_000_000.5) is False


def test_different_chat_id_same_ts_is_not_duplicate():
    Cls = _cache_cls()
    cache = Cls()
    cache.is_duplicate("chat-1", 1_700_000_000.0)
    assert cache.is_duplicate("chat-2", 1_700_000_000.0) is False


def test_100_replays_yield_one_publish():
    """
    Spec §5.1: feed the publisher the same chat_id 100 times,
    assert exactly 1 NTFY POST.
    """
    Cls = _cache_cls()
    cache = Cls()
    publish_count = 0
    for _ in range(100):
        if not cache.is_duplicate("chat-x", 1_700_000_000.0):
            publish_count += 1
    assert publish_count == 1


def test_eviction_after_1h_window(fake_clock):
    """
    Spec §1.8: cache eviction after the 1h window.
    After 3600s elapse, the same (chat_id, ts) is no longer a duplicate.
    """
    Cls = _cache_cls()
    # The cache stores the wall-clock at insertion time and compares against
    # it on every access. Inject the fake clock so we can advance time.
    cache = Cls(ttl_seconds=3600, clock=fake_clock.time)
    cache.is_duplicate("chat-1", 1_700_000_000.0)
    assert cache.is_duplicate("chat-1", 1_700_000_000.0) is True  # immediate
    fake_clock.advance(3601)  # 1h + 1s
    assert cache.is_duplicate("chat-1", 1_700_000_000.0) is False


def test_eviction_at_under_1h(fake_clock):
    """Boundary: under 1h, the entry is still a duplicate."""
    Cls = _cache_cls()
    cache = Cls(ttl_seconds=3600, clock=fake_clock.time)
    cache.is_duplicate("chat-1", 1_700_000_000.0)
    fake_clock.advance(3599)  # just under 1h
    assert cache.is_duplicate("chat-1", 1_700_000_000.0) is True


def test_restart_loses_state():
    """
    Spec §1.8: restart loses dedup state, which is acceptable.
    A fresh cache (simulating a process restart) must not consider the old
    chat_id a duplicate.
    """
    Cls = _cache_cls()
    cache_a = Cls()
    cache_a.is_duplicate("chat-1", 1_700_000_000.0)
    assert cache_a.is_duplicate("chat-1", 1_700_000_000.0) is True

    cache_b = Cls()  # fresh process
    assert cache_b.is_duplicate("chat-1", 1_700_000_000.0) is False


def test_lru_cap_evicts_oldest():
    """
    Spec §1.8: 1000-entry LRU. Adding a 1001st entry evicts the oldest.
    The eviction means the evicted entry is no longer a duplicate.
    """
    Cls = _cache_cls()
    cache = Cls(max_size=1000, ttl_seconds=3600)
    for i in range(1000):
        cache.is_duplicate(f"chat-{i}", 1_700_000_000.0)
    # 1000th entry pushes us past the cap; the oldest (chat-0) is evicted
    # only when a NEW entry arrives.
    cache.is_duplicate("chat-1000", 1_700_000_000.0)  # forces eviction
    # chat-0 is no longer in cache
    assert cache.is_duplicate("chat-0", 1_700_000_000.0) is False
    # chat-999 is still in cache (it's at the end of the LRU)
    assert cache.is_duplicate("chat-999", 1_700_000_000.0) is True


def test_cache_size_tracks_inserts():
    Cls = _cache_cls()
    cache = Cls()
    assert len(cache) == 0
    cache.is_duplicate("a", 1.0)
    cache.is_duplicate("b", 2.0)
    assert len(cache) == 2


def test_purge_drops_all():
    Cls = _cache_cls()
    cache = Cls()
    cache.is_duplicate("a", 1.0)
    cache.purge()
    assert len(cache) == 0
    assert cache.is_duplicate("a", 1.0) is False


def test_save_and_load_roundtrip(tmp_path):
    """Spec §1.8 Layer 2: persistence to disk on shutdown."""
    Cls = _cache_cls()
    import idempotency
    keys = [("chat-1", 1.0), ("chat-2", 2.0), ("chat-3", 3.0)]
    path = tmp_path / "seen.json"
    idempotency.save_to_disk(path, keys)
    loaded = idempotency.load_from_disk(path)
    assert loaded == keys


def test_load_missing_file_returns_empty(tmp_path):
    import idempotency
    loaded = idempotency.load_from_disk(tmp_path / "nonexistent.json")
    assert loaded == []


def test_load_corrupt_file_returns_empty(tmp_path):
    import idempotency
    path = tmp_path / "seen.json"
    path.write_text("not valid json {{")
    assert idempotency.load_from_disk(path) == []
