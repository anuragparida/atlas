"""
Shared pytest fixtures for atlas-notifier unit tests.

Spec: PHASE2-SPEC.md §5.1.

Provides:
- fake_clock: a monotonic clock that can be advanced manually (no real sleep).
- mock_openwebui: an httpx MockTransport that serves /api/v1/chats and /api/v1/auths/me.
- mock_ntfy: an httpx MockTransport that records POSTs to /atlas-<userid>.
- reset_dedup: clears the global dedup cache between tests.
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import contextmanager
from typing import Any, Callable

import httpx
import pytest


class FakeClock:
    """A monotonic clock that can be advanced manually. No real sleep."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self._now = start
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self._now

    def monotonic(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds

    def sleep(self, seconds: float) -> None:
        """Record the requested sleep and (if needed) advance time, but do not block."""
        self.sleeps.append(seconds)
        self._now += seconds

    async def async_sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self._now += seconds

    def reset(self) -> None:
        self._now = 1_000_000.0
        self.sleeps.clear()


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def ntfy_publish_log() -> list[dict[str, Any]]:
    """In-memory record of NTFY POSTs captured by mock_ntfy."""
    return []


@pytest.fixture
def mock_ntfy(ntfy_publish_log) -> Callable[[list[int] | None], httpx.MockTransport]:
    """
    Returns a factory for an httpx MockTransport that pretends to be NTFY.

    Usage:
        transport = mock_ntfy(status_codes=[200, 200, 503])
        async with httpx.AsyncClient(transport=transport) as client:
            ...

    `status_codes` is a queue of response statuses to return in order. When the
    queue is exhausted, the last value is repeated (or 200 if queue was empty).
    """

    def _factory(status_codes: list[int] | None = None) -> httpx.MockTransport:
        codes = list(status_codes or [200])
        idx = {"i": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            status = codes[min(idx["i"], len(codes) - 1)]
            idx["i"] += 1
            if request.method == "POST":
                ntfy_publish_log.append({
                    "url": str(request.url),
                    "method": request.method,
                    "headers": dict(request.headers),
                    "body": request.content.decode("utf-8", errors="replace"),
                    "status": status,
                })
            return httpx.Response(status, text='{"event":"message","id":"abc123"}')

        return httpx.MockTransport(handler)

    return _factory


@pytest.fixture
def mock_openwebui() -> Callable[..., httpx.MockTransport]:
    """
    Returns a factory for an httpx MockTransport that pretends to be Open WebUI.

    Pass `chats=[...]` to control the /api/v1/chats response and `me={...}` for
    /api/v1/auths/me/. Set `malformed=True` to return invalid JSON for the
    chats endpoint.
    """

    def _factory(
        chats: list[dict] | None = None,
        me: dict | None = None,
        malformed: bool = False,
    ) -> httpx.MockTransport:
        chats_data = chats or []
        me_data = me or {"id": "u-anurag", "email": "anurag@example.com", "name": "Anurag"}

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path == "/api/v1/chats/" and request.method == "GET":
                if malformed:
                    return httpx.Response(200, text='{"chats": [oops this is not valid json,,,')
                return httpx.Response(200, json={"chats": chats_data, "total": len(chats_data)})
            if path == "/api/v1/auths/me/" and request.method == "GET":
                return httpx.Response(200, json=me_data)
            if path.startswith("/api/v1/chats/") and request.method == "GET":
                chat_id = path.rsplit("/", 1)[-1]
                chat = next((c for c in chats_data if c.get("id") == chat_id), None)
                if chat is None:
                    return httpx.Response(404, json={"detail": "not found"})
                return httpx.Response(200, json=chat)
            if path == "/api/v1/models/" and request.method == "GET":
                return httpx.Response(200, json={"data": [{"id": "llama3"}]})
            return httpx.Response(404, json={"detail": "not handled by mock"})

        return httpx.MockTransport(handler)

    return _factory


@pytest.fixture
def reset_dedup():
    """Clear any module-level dedup cache between tests.

    The impl exposes ``idempotency.reset_cache()`` (or the cache is a fresh
    instance per test, see test_idempotency.py). This fixture is a no-op
    safety net; tests should construct their own cache instance where
    possible.
    """
    try:
        import idempotency  # type: ignore
        if hasattr(idempotency, "reset_cache"):
            idempotency.reset_cache()
    except Exception:
        pass
    yield
    try:
        import idempotency  # type: ignore
        if hasattr(idempotency, "reset_cache"):
            idempotency.reset_cache()
    except Exception:
        pass


@pytest.fixture
def event_loop_policy():
    """Use the default asyncio policy for the platform."""
    return asyncio.DefaultEventLoopPolicy()
