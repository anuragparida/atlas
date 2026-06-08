"""Dedup cache per PHASE2-SPEC.md §1.8.

Layer 1 of the three-layer idempotency. Keyed on ``(chat_id, ts)`` per
the task body (not just ``chat_id``). TTL is 1h per spec; the LRU cap is
1000. On graceful shutdown the cache persists its keys to
``/var/lib/atlas-notifier/seen.json`` (Layer 2) — see ``main.py`` for the
flush call.

Trade-off: a process restart loses in-memory state. Layer 3 (NTFY's
server-side dedup via ``Message-Id``) catches the rare replay after a
restart. The spec explicitly accepts this trade-off.
"""

from __future__ import annotations

import asyncio
import json
import math
import time as _time
from collections import OrderedDict
from pathlib import Path
from typing import Callable


# ---------- persistence helpers (Layer 2) ----------


class IdempotencyCache:
    """Async-friendly wrapper. Loads from disk on startup, supports async flush."""

    def __init__(
        self,
        *,
        max_size: int = 1000,
        ttl_seconds: float = 3600.0,
        clock: Callable[[], float] | None = None,
        state_dir: str | None = None,
    ) -> None:
        self._max_size = int(max_size)
        self._ttl = float(ttl_seconds)
        self._clock = clock or _time.time
        self._data: "OrderedDict[tuple[str, float], float]" = OrderedDict()
        self._state_dir = state_dir

    def __len__(self) -> int:
        return len(self._data)

    def is_duplicate(self, chat_id: str, ts: float) -> bool:
        if not chat_id:
            return False
        key = (chat_id, float(ts))
        now = self._clock()
        self._evict_expired(now)
        if key in self._data:
            self._data.move_to_end(key)
            return True
        if len(self._data) >= self._max_size:
            self._data.popitem(last=False)
        self._data[key] = now
        return False

    def purge(self) -> None:
        self._data.clear()

    def _evict_expired(self, now: float) -> None:
        ttl = self._ttl
        if ttl <= 0:
            return
        expired_keys = [k for k, t in self._data.items() if (now - t) > ttl]
        for k in expired_keys:
            self._data.pop(k, None)

    async def load_from_disk_async(self) -> None:
        """Async wrapper. Loads the persisted seen-keys file at startup."""
        if not self._state_dir:
            return
        path = Path(self._state_dir) / "seen.json"
        # Run the sync loader in a thread to avoid blocking the loop.
        keys = await asyncio.to_thread(load_from_disk, path)
        now = self._clock()
        for cid, ts in keys:
            self._data[(cid, ts)] = now

    def save_to_disk(self) -> None:
        """Sync flush. Called from main.py on graceful shutdown."""
        if not self._state_dir:
            return
        keys = list(self._data.keys())[-500:]
        save_to_disk(Path(self._state_dir) / "seen.json", keys)


# Aliases for the test contract: the test suite accepts any of these names.
DedupCache = IdempotencyCache
Cache = IdempotencyCache


def reset_cache() -> None:
    """Module-level safety net for the ``reset_dedup`` fixture. With per-test
    cache instances, this is rarely needed; the fixture is belt-and-suspenders."""
    # No module-level cache lives here. If one is added, reset it here.
    return None


# ---------- persistence helpers (Layer 2) ----------

def load_from_disk(path: str | Path) -> list[tuple[str, float]]:
    """Load a list of (chat_id, ts) keys from a JSON file. Missing or
    corrupt file → empty list."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[tuple[str, float]] = []
    for item in data:
        if (
            isinstance(item, (list, tuple))
            and len(item) == 2
            and isinstance(item[0], str)
            and isinstance(item[1], (int, float))
        ):
            out.append((item[0], float(item[1])))
    return out


def save_to_disk(path: str | Path, keys: list[tuple[str, float]]) -> None:
    """Persist the (chat_id, ts) keys to a JSON file. Atomic write."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps([list(k) for k in keys]), encoding="utf-8")
    tmp.replace(p)
