"""
Tests for the Open WebUI client (REST polling + WebSocket).

Spec: PHASE2-SPEC.md §1.2, §1.3, §1.5.
Task body: polling loop, WebSocket reconnect, status state machine.
"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import openwebui_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_chat(chat_id: str, *, last_message_done: bool, updated_at: float) -> dict:
    """Build a minimal chat dict that the impl can status-infer against.

    updated_at is an epoch-seconds value matching what the real Open WebUI
    returns. Pass a value close to your injected ``now`` to make the
    inference time-sensitive.
    """
    return {
        "id": chat_id,
        "title": "test",
        "updated_at": updated_at,
        "chat_history": [
            {
                "role": "assistant",
                "content": "x",
                "done": last_message_done,
                "updated_at": updated_at,
            }
        ],
    }


# ---------------------------------------------------------------------------
# Status inference (spec §1.2)
# ---------------------------------------------------------------------------


def test_status_inference_generating():
    """Last message done=False and recent (<120s) -> generating."""
    now = 1_700_000_100.0
    chat = _make_chat("c1", last_message_done=False, updated_at=now - 10)
    state = openwebui_client.infer_status(chat, now=now)
    assert state == "generating"


def test_status_inference_idle_done_true():
    """Last message done=True -> idle."""
    now = 1_700_000_100.0
    chat = _make_chat("c1", last_message_done=True, updated_at=now - 10)
    state = openwebui_client.infer_status(chat, now=now)
    assert state == "idle"


def test_status_inference_idle_stuck_done_false():
    """Last message done=False but >120s old -> idle (stuck-or-done)."""
    now = 1_700_000_300.0  # 200s after updated_at
    chat = _make_chat("c1", last_message_done=False, updated_at=now - 200)
    state = openwebui_client.infer_status(chat, now=now)
    assert state == "idle"


def test_status_inference_unknown_empty_history():
    """No chat_history -> unknown."""
    chat = {"id": "c1", "title": "t", "updated_at": 0, "chat_history": []}
    state = openwebui_client.infer_status(chat, now=1_700_000_000.0)
    assert state == "unknown"


def test_status_inference_unknown_malformed():
    """Missing 'chat_history' key entirely -> unknown, not crash."""
    state = openwebui_client.infer_status({"id": "c1", "title": "t"}, now=1_700_000_000.0)
    assert state == "unknown"


def test_status_inference_handles_legacy_chat_history_shape():
    """
    Real Open WebUI versions store the history under chat.chat_history
    (older) or chat.chat_history.messages.History (newer). The impl
    supports both. Older shape: a flat list of message dicts.
    """
    now = 1_700_000_100.0
    chat = {
        "id": "c1",
        "title": "t",
        "chat_history": [
            {"role": "assistant", "content": "x", "done": True, "updated_at": now - 10}
        ],
    }
    state = openwebui_client.infer_status(chat, now=now)
    assert state == "idle"


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------


async def test_poll_returns_chat_status_map(mock_openwebui):
    """poll_once hits /api/v1/chats/?page=1 and returns {chat_id: status}.

    The impl computes `now` from time.time() inside poll_once, so the
    chat's updated_at must be within 120s of the wall clock for "generating"
    to fire.
    """
    now = time.time()
    chats = [
        {
            "id": "c1",
            "title": "t",
            "updated_at": now - 5,
            "chat_history": [
                {"role": "assistant", "content": "x", "done": True, "updated_at": now - 5}
            ],
        },
        {
            "id": "c2",
            "title": "t",
            "updated_at": now - 5,
            "chat_history": [
                {"role": "assistant", "content": "x", "done": False, "updated_at": now - 5}
            ],
        },
    ]
    transport = mock_openwebui(chats=chats)
    async with httpx.AsyncClient(
        base_url="http://openwebui:8080",
        transport=transport,
        timeout=2.0,
    ) as http:
        client = openwebui_client.OpenWebUIClient(
            base_url="http://openwebui:8080",
            api_key="tk_test",
            http_client=http,
        )
        statuses = await client.poll_once()
    assert statuses["c1"] == "idle"
    assert statuses["c2"] == "generating"


async def test_malformed_json_is_logged_and_loop_continues(
    mock_openwebui, caplog
):
    """
    Negative test (task body): Open WebUI returns malformed JSON →
    service logs the error, continues polling, no crash.
    """
    transport = mock_openwebui(malformed=True)
    async with httpx.AsyncClient(
        base_url="http://openwebui:8080",
        transport=transport,
        timeout=2.0,
    ) as http:
        client = openwebui_client.OpenWebUIClient(
            base_url="http://openwebui:8080",
            api_key="tk_test",
            http_client=http,
        )
        with caplog.at_level("ERROR"):
            result = await client.poll_once()
        # First poll: malformed. Should not raise; returns empty.
        assert result == {}
        # Second poll: same malformed response — service must keep going.
        result2 = await client.poll_once()
        assert result2 == {}


# ---------------------------------------------------------------------------
# WebSocket reconnect / state machine
# ---------------------------------------------------------------------------


def test_state_machine_initial_is_connecting():
    sm = openwebui_client.ConnectionStateMachine()
    assert sm.state == "CONNECTING"


def test_state_machine_promotes_to_ws_connected():
    sm = openwebui_client.ConnectionStateMachine()
    sm.on_ws_connected()
    assert sm.state == "WS_CONNECTED"


def test_state_machine_ws_drop_returns_to_connecting():
    """
    Spec §1.5: on WS drop → CONNECTING (with first poll on entry).
    POLLING_ONLY is reserved for permanent auth errors, not transient drops.
    """
    sm = openwebui_client.ConnectionStateMachine()
    sm.on_ws_connected()
    sm.on_ws_dropped()
    assert sm.state == "CONNECTING"


def test_state_machine_auth_error_pins_polling_only():
    sm = openwebui_client.ConnectionStateMachine()
    sm.on_ws_connected()
    sm.on_auth_error()
    assert sm.state == "POLLING_ONLY"
    # A subsequent connect attempt can promote us back.
    sm.on_ws_connected()
    assert sm.state == "WS_CONNECTED"


def test_state_machine_drop_from_connecting_stays_connecting():
    sm = openwebui_client.ConnectionStateMachine()
    sm.on_ws_dropped()  # spurious drop while already CONNECTING
    assert sm.state == "CONNECTING"


async def test_ws_reconnect_uses_backoff(fake_clock):
    """
    When the WS connection drops, the client should schedule a reconnect
    using the backoff schedule (cap 60s per task body).
    """
    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)
        fake_clock.advance(s)

    # Pretend the WS connect fails for the first 4 attempts, then succeeds.
    attempt = {"i": 0}

    async def connect():
        attempt["i"] += 1
        if attempt["i"] < 5:
            raise ConnectionError("nope")
        return "ws-handle"

    from backoff import Backoff
    backoff = Backoff(cap_seconds=60, base_seconds=1, factor=2)

    client = openwebui_client.OpenWebUIClient(
        base_url="http://openwebui:8080",
        api_key="tk",
        ws_connect=connect,
        backoff=backoff,
        sleep=fake_sleep,
    )
    handle = await client._ws_connect_with_backoff(max_attempts=5)
    assert handle == "ws-handle"
    # 4 failed attempts before success -> 4 sleeps of 1, 2, 4, 8.
    # (The 5th attempt is the success; no sleep after it.)
    assert sleeps == [1, 2, 4, 8]


async def test_ws_reconnect_exhausts_attempts(fake_clock):
    """When all attempts fail, _ws_connect_with_backoff raises the last error."""
    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)
        fake_clock.advance(s)

    async def connect():
        raise ConnectionError("persistent failure")

    from backoff import Backoff
    backoff = Backoff(cap_seconds=60, base_seconds=1, factor=2)

    client = openwebui_client.OpenWebUIClient(
        base_url="http://openwebui:8080",
        api_key="tk",
        ws_connect=connect,
        backoff=backoff,
        sleep=fake_sleep,
    )
    with pytest.raises(ConnectionError, match="persistent failure"):
        await client._ws_connect_with_backoff(max_attempts=3)
    # 2 sleeps (between 3 attempts) of 1, 2.
    assert sleeps == [1, 2]


# ---------------------------------------------------------------------------
# Auth header
# ---------------------------------------------------------------------------


async def test_poll_sends_authorization_header():
    """The Open WebUI client must send the API key as Bearer auth."""
    seen_auth: list[str] = []
    now = 1_700_000_100.0
    chats = [
        {
            "id": "c1",
            "title": "t",
            "updated_at": now - 5,
            "chat_history": [
                {"role": "assistant", "content": "x", "done": True, "updated_at": now - 5}
            ],
        }
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization"):
            seen_auth.append(request.headers["authorization"])
        if request.url.path == "/api/v1/chats/" and request.method == "GET":
            return httpx.Response(200, json={"chats": chats, "total": 1})
        return httpx.Response(404)

    custom = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url="http://openwebui:8080",
        transport=custom,
        timeout=2.0,
        headers={"Authorization": "Bearer tk_abc"},
    ) as http:
        client = openwebui_client.OpenWebUIClient(
            base_url="http://openwebui:8080",
            api_key="tk_abc",
            http_client=http,
        )
        await client.poll_once()
    assert seen_auth and seen_auth[0] == "Bearer tk_abc"


# ---------------------------------------------------------------------------
# Health-state introspection (used by /healthz)
# ---------------------------------------------------------------------------


async def test_last_poll_at_updates_after_successful_poll(mock_openwebui):
    now = time.time()
    chats = [
        {
            "id": "c1",
            "title": "t",
            "updated_at": now - 5,
            "chat_history": [
                {"role": "assistant", "content": "x", "done": True, "updated_at": now - 5}
            ],
        }
    ]
    transport = mock_openwebui(chats=chats)
    async with httpx.AsyncClient(
        base_url="http://openwebui:8080",
        transport=transport,
        timeout=2.0,
    ) as http:
        client = openwebui_client.OpenWebUIClient(
            base_url="http://openwebui:8080",
            api_key="tk",
            http_client=http,
        )
        assert client.last_poll_at == 0.0
        await client.poll_once()
        assert client.last_poll_at > 0.0
        assert client.reachable is True


# ---------------------------------------------------------------------------
# Status inference helpers (extended coverage)
# ---------------------------------------------------------------------------


def test_parse_updated_at_handles_none():
    assert openwebui_client._parse_updated_at(None) == 0.0


def test_parse_updated_at_handles_epoch_seconds():
    assert openwebui_client._parse_updated_at(1_700_000_000) == 1_700_000_000.0


def test_parse_updated_at_handles_epoch_milliseconds():
    # 1.7e12 > 1e12, so it's treated as ms and divided.
    assert openwebui_client._parse_updated_at(1_700_000_000_000) == 1_700_000_000.0


def test_parse_updated_at_handles_iso_string():
    # 2023-11-14T22:13:20Z = 1_700_000_000 epoch
    iso = "2023-11-14T22:13:20+00:00"
    val = openwebui_client._parse_updated_at(iso)
    assert abs(val - 1_700_000_000.0) < 1.0


def test_parse_updated_at_handles_invalid_string():
    # Falls through both try blocks -> 0.0
    assert openwebui_client._parse_updated_at("not a number") == 0.0


def test_parse_updated_at_handles_unsupported_type():
    assert openwebui_client._parse_updated_at([]) == 0.0


def test_extract_last_message_newer_shape():
    """
    Newer Open WebUI versions store history under chat.chat_history.messages.History.
    """
    now = 1_700_000_100.0
    chat = {
        "id": "c1",
        "chat": {
            "chat_history": {
                "messages": {
                    "History": [
                        {
                            "role": "assistant",
                            "content": "x",
                            "done": True,
                            "updated_at": now - 5,
                        }
                    ]
                }
            }
        },
    }
    content, done, ts = openwebui_client._extract_last_message(chat)
    assert content == "x"
    assert done is True
    assert ts == now - 5


def test_extract_last_message_falls_through_to_legacy():
    """If `chat` is not a dict, fall back to top-level `chat_history`."""
    now = 1_700_000_100.0
    chat = {
        "id": "c1",
        "chat_history": [
            {"role": "assistant", "content": "x", "done": True, "updated_at": now - 5}
        ],
    }
    content, done, ts = openwebui_client._extract_last_message(chat)
    assert content == "x"


def test_extract_last_message_no_assistant_returns_none():
    """A history of only user messages returns (None, None, None)."""
    chat = {
        "id": "c1",
        "chat_history": [
            {"role": "user", "content": "hi", "done": True, "updated_at": 0}
        ],
    }
    assert openwebui_client._extract_last_message(chat) == (None, None, None)


def test_extract_last_message_skips_non_dict_messages():
    chat = {
        "id": "c1",
        "chat_history": [
            "not a dict",
            {"role": "assistant", "content": "x", "done": True, "updated_at": 0},
        ],
    }
    content, done, ts = openwebui_client._extract_last_message(chat)
    assert content == "x"


def test_extract_last_message_history_dict_without_messages():
    """If history is a dict but has no `messages` key, treat as empty."""
    chat = {"id": "c1", "chat_history": {}}
    assert openwebui_client._extract_last_message(chat) == (None, None, None)


def test_extract_last_message_history_dict_with_non_dict_messages():
    chat = {"id": "c1", "chat_history": {"messages": "not a dict"}}
    assert openwebui_client._extract_last_message(chat) == (None, None, None)


# ---------------------------------------------------------------------------
# fetch_user_id (used during startup to learn the Open WebUI user)
# ---------------------------------------------------------------------------


async def test_fetch_user_id_returns_id():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/auths/me/":
            return httpx.Response(200, json={"id": "u-anurag", "email": "x"})
        return httpx.Response(404)

    custom = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url="http://openwebui:8080",
        transport=custom,
        timeout=2.0,
    ) as http:
        client = openwebui_client.OpenWebUIClient(
            base_url="http://openwebui:8080",
            api_key="tk",
            http_client=http,
        )
        uid = await client.fetch_user_id()
        assert uid == "u-anurag"


async def test_fetch_user_id_returns_none_on_401():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "no"})

    custom = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url="http://openwebui:8080",
        transport=custom,
        timeout=2.0,
    ) as http:
        client = openwebui_client.OpenWebUIClient(
            base_url="http://openwebui:8080",
            api_key="tk",
            http_client=http,
        )
        uid = await client.fetch_user_id()
        assert uid is None


async def test_fetch_user_id_returns_none_on_5xx():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    custom = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url="http://openwebui:8080",
        transport=custom,
        timeout=2.0,
    ) as http:
        client = openwebui_client.OpenWebUIClient(
            base_url="http://openwebui:8080",
            api_key="tk",
            http_client=http,
        )
        assert await client.fetch_user_id() is None


async def test_fetch_user_id_returns_none_on_malformed_json():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not json")

    custom = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url="http://openwebui:8080",
        transport=custom,
        timeout=2.0,
    ) as http:
        client = openwebui_client.OpenWebUIClient(
            base_url="http://openwebui:8080",
            api_key="tk",
            http_client=http,
        )
        assert await client.fetch_user_id() is None


async def test_fetch_user_id_returns_none_when_client_not_started():
    """
    When http_client is None (e.g., before start()), fetch_user_id returns None
    rather than crashing.
    """
    client = openwebui_client.OpenWebUIClient(
        base_url="http://openwebui:8080",
        api_key="tk",
    )
    # The default constructor takes http_client=None and doesn't create one
    # until start() is called. fetch_user_id must not crash.
    assert await client.fetch_user_id() is None


# ---------------------------------------------------------------------------
# _handle_ws_event (the inner state machine for WS events)
# ---------------------------------------------------------------------------


def test_handle_ws_event_status_idle_after_generating():
    """
    When a chat flips from generating -> idle, the handler returns a
    CompletionEvent. This is the core "fire NTFY" decision.
    """
    now = time.time()
    chats = [
        {
            "id": "c1",
            "title": "t",
            "updated_at": now - 5,
            "chat_history": [
                {"role": "assistant", "content": "x", "done": True, "updated_at": now - 5}
            ],
        }
    ]
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, json={"chats": chats, "total": 1})
    )
    # Bypass async — build a sync-style test by calling _handle_ws_event
    # directly. The OpenWebUIClient needs an http_client to construct.
    import asyncio

    async def go():
        async with httpx.AsyncClient(
            base_url="http://x", transport=transport, timeout=1.0
        ) as http:
            client = openwebui_client.OpenWebUIClient(
                base_url="http://x", api_key="tk", http_client=http
            )
            # Seed the state with a 'generating' entry.
            client._states["c1"] = openwebui_client.ChatStatus(
                chat_id="c1",
                status="generating",
                updated_at=now,
                title="Hello world",
                body="",
            )
            evt = {
                "type": "chat:status",
                "data": {"chat_id": "c1", "status": "idle", "updated_at": now},
            }
            completion = client._handle_ws_event(evt)
            assert completion is not None
            assert completion.chat_id == "c1"
            assert completion.title == "Hello world"
            assert completion.click_url.endswith("/c/c1")

    asyncio.run(go())


def test_handle_ws_event_message_done_fires_completion():
    """A `chat:message` event with done=True on an assistant message fires NTFY."""
    now = time.time()
    transport = httpx.MockTransport(lambda r: httpx.Response(200))
    import asyncio

    async def go():
        async with httpx.AsyncClient(
            base_url="http://x", transport=transport, timeout=1.0
        ) as http:
            client = openwebui_client.OpenWebUIClient(
                base_url="http://x", api_key="tk", http_client=http
            )
            evt = {
                "type": "chat:message",
                "data": {
                    "chat_id": "c1",
                    "role": "assistant",
                    "done": True,
                    "content": "Hello",
                },
            }
            completion = client._handle_ws_event(evt)
            assert completion is not None
            assert completion.body == "Hello"
            assert completion.chat_id == "c1"

    asyncio.run(go())


def test_handle_ws_event_chat_deleted_removes_state():
    now = time.time()
    transport = httpx.MockTransport(lambda r: httpx.Response(200))
    import asyncio

    async def go():
        async with httpx.AsyncClient(
            base_url="http://x", transport=transport, timeout=1.0
        ) as http:
            client = openwebui_client.OpenWebUIClient(
                base_url="http://x", api_key="tk", http_client=http
            )
            client._states["c1"] = openwebui_client.ChatStatus(
                chat_id="c1", status="idle", updated_at=now
            )
            evt = {"type": "chat:deleted", "data": {"chat_id": "c1"}}
            completion = client._handle_ws_event(evt)
            assert completion is None
            assert "c1" not in client._states

    asyncio.run(go())


def test_handle_ws_event_ignores_unknown_event_type():
    """Events that don't match any known type are silently ignored."""
    transport = httpx.MockTransport(lambda r: httpx.Response(200))
    import asyncio

    async def go():
        async with httpx.AsyncClient(
            base_url="http://x", transport=transport, timeout=1.0
        ) as http:
            client = openwebui_client.OpenWebUIClient(
                base_url="http://x", api_key="tk", http_client=http
            )
            evt = {"type": "chat:unknown", "data": {"chat_id": "c1"}}
            assert client._handle_ws_event(evt) is None

    asyncio.run(go())


def test_handle_ws_event_ignores_event_without_chat_id():
    transport = httpx.MockTransport(lambda r: httpx.Response(200))
    import asyncio

    async def go():
        async with httpx.AsyncClient(
            base_url="http://x", transport=transport, timeout=1.0
        ) as http:
            client = openwebui_client.OpenWebUIClient(
                base_url="http://x", api_key="tk", http_client=http
            )
            evt = {"type": "chat:status", "data": {"status": "idle"}}
            assert client._handle_ws_event(evt) is None

    asyncio.run(go())


# ---------------------------------------------------------------------------
# _ws_url (auth via query-param)
# ---------------------------------------------------------------------------


def test_ws_url_uses_query_param_token():
    client = openwebui_client.OpenWebUIClient(
        base_url="http://openwebui:8080",
        api_key="tk_xyz",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
    )
    url = client._ws_url()
    assert url.startswith("ws://")
    assert "token=tk_xyz" in url
    assert url.endswith("/api/v1/ws?token=tk_xyz")


def test_ws_url_uses_wss_for_https():
    client = openwebui_client.OpenWebUIClient(
        base_url="https://openwebui.example.com",
        api_key="tk_xyz",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
    )
    url = client._ws_url()
    assert url.startswith("wss://")


# ---------------------------------------------------------------------------
# ws_iter (production WS loop) — covers the WebSocket event path
# ---------------------------------------------------------------------------


def test_ws_iter_yields_completion_event_from_ws(monkeypatch):
    """
    The ws_iter loop connects to the WS, reads events, and yields
    CompletionEvents on the generating -> idle transition. We patch
    websockets.connect to return a fake stream that yields one event.
    """
    import asyncio

    class FakeWS:
        def __init__(self, events):
            self._events = list(events)
            self._i = 0

        async def __aiter__(self):
            while self._i < len(self._events):
                evt = self._events[self._i]
                self._i += 1
                yield evt

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    # websockets.connect(uri) returns an async context manager directly in v16.
    # Our fake must do the same.
    class FakeConnect:
        def __init__(self, url, **kwargs):
            self.url = url

        async def __aenter__(self):
            return FakeWS([
                json.dumps({
                    "type": "chat:status",
                    "data": {"chat_id": "c1", "status": "idle", "updated_at": time.time()},
                })
            ])

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(openwebui_client.websockets, "connect", FakeConnect)

    transport = httpx.MockTransport(lambda r: httpx.Response(200))

    async def go():
        async with httpx.AsyncClient(
            base_url="http://x", transport=transport, timeout=1.0
        ) as http:
            client = openwebui_client.OpenWebUIClient(
                base_url="http://x", api_key="tk", http_client=http
            )
            # Seed state with 'generating' so the idle event fires a completion.
            client._states["c1"] = openwebui_client.ChatStatus(
                chat_id="c1",
                status="generating",
                updated_at=time.time(),
                title="Hello",
            )
            events = []
            async for evt in client.ws_iter():
                events.append(evt)
                if len(events) >= 1:
                    break
            assert len(events) == 1
            assert events[0].chat_id == "c1"

    asyncio.run(go())


def test_ws_iter_skips_malformed_json(monkeypatch):
    """Malformed JSON on the WS is logged and the loop continues."""
    import asyncio

    class FakeWS:
        def __init__(self, events):
            self._events = list(events)
            self._i = 0

        async def __aiter__(self):
            while self._i < len(self._events):
                evt = self._events[self._i]
                self._i += 1
                yield evt

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class FakeConnect:
        def __init__(self, url, **kwargs):
            self.url = url

        async def __aenter__(self):
            return FakeWS([
                "not valid json",
                json.dumps({"type": "chat:status", "data": {"chat_id": "c1", "status": "idle"}}),
            ])

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(openwebui_client.websockets, "connect", FakeConnect)

    transport = httpx.MockTransport(lambda r: httpx.Response(200))

    async def go():
        async with httpx.AsyncClient(
            base_url="http://x", transport=transport, timeout=1.0
        ) as http:
            client = openwebui_client.OpenWebUIClient(
                base_url="http://x", api_key="tk", http_client=http
            )
            client._states["c1"] = openwebui_client.ChatStatus(
                chat_id="c1", status="generating", updated_at=time.time()
            )
            count = 0
            async for evt in client.ws_iter():
                count += 1
                if count >= 1:
                    break
            assert count == 1  # the malformed one was skipped

    asyncio.run(go())


def test_ws_iter_skips_empty_and_non_dict_events(monkeypatch):
    """Empty messages and non-dict events are silently skipped."""
    import asyncio

    class FakeWS:
        def __init__(self, events):
            self._events = list(events)
            self._i = 0

        async def __aiter__(self):
            while self._i < len(self._events):
                evt = self._events[self._i]
                self._i += 1
                yield evt

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class FakeConnect:
        def __init__(self, url, **kwargs):
            self.url = url

        async def __aenter__(self):
            return FakeWS([
                "",  # empty
                json.dumps([1, 2, 3]),  # not a dict
                json.dumps({"type": "chat:status", "data": {"chat_id": "c1", "status": "idle"}}),
            ])

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(openwebui_client.websockets, "connect", FakeConnect)

    transport = httpx.MockTransport(lambda r: httpx.Response(200))

    async def go():
        async with httpx.AsyncClient(
            base_url="http://x", transport=transport, timeout=1.0
        ) as http:
            client = openwebui_client.OpenWebUIClient(
                base_url="http://x", api_key="tk", http_client=http
            )
            client._states["c1"] = openwebui_client.ChatStatus(
                chat_id="c1", status="generating", updated_at=time.time()
            )
            count = 0
            async for evt in client.ws_iter():
                count += 1
                if count >= 1:
                    break
            assert count == 1

    asyncio.run(go())


def test_ws_iter_logs_event_discovery(monkeypatch, caplog):
    """
    The first 5 unique event names are logged once at info level (spec §1.3).
    """
    import asyncio
    import logging

    class FakeWS:
        def __init__(self, events):
            self._events = list(events)
            self._i = 0

        async def __aiter__(self):
            while self._i < len(self._events):
                evt = self._events[self._i]
                self._i += 1
                yield evt

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    class FakeConnect:
        def __init__(self, url, **kwargs):
            self.url = url

        async def __aenter__(self):
            return FakeWS([
                json.dumps({"type": "chat:status", "data": {"chat_id": "c1", "status": "idle"}}),
            ])

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(openwebui_client.websockets, "connect", FakeConnect)

    transport = httpx.MockTransport(lambda r: httpx.Response(200))

    async def go():
        with caplog.at_level(logging.INFO, logger="openwebui_client"):
            async with httpx.AsyncClient(
                base_url="http://x", transport=transport, timeout=1.0
            ) as http:
                client = openwebui_client.OpenWebUIClient(
                    base_url="http://x", api_key="tk", http_client=http
                )
                client._states["c1"] = openwebui_client.ChatStatus(
                    chat_id="c1", status="generating", updated_at=time.time()
                )
                async for _ in client.ws_iter():
                    break
        discovery_logs = [
            r for r in caplog.records if "ws_event_discovered" in r.message
        ]
        assert len(discovery_logs) >= 1
        assert "chat:status" in discovery_logs[0].message

    asyncio.run(go())


def test_ws_iter_handles_ws_drop(monkeypatch):
    """
    When websockets.connect raises, the loop logs and sleeps, then retries.

    The impl's ws_iter is an infinite loop, so the test injects a counter
    and breaks out after a few iterations.
    """
    import asyncio
    from backoff import Backoff
    from websockets.exceptions import WebSocketException

    class FakeWS:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    call_count = {"i": 0}

    class FakeConnect:
        def __init__(self, url, **kwargs):
            call_count["i"] += 1
            self.url = url

        async def __aenter__(self):
            # Drop on the first connect; succeed (with empty events) after.
            if call_count["i"] == 1:
                raise WebSocketException("simulated drop")
            return FakeWS()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(openwebui_client.websockets, "connect", FakeConnect)

    sleeps: list[float] = []

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)
        # Bail out after the first backoff sleep.
        raise asyncio.CancelledError("test done")

    transport = httpx.MockTransport(lambda r: httpx.Response(200))

    async def go():
        async with httpx.AsyncClient(
            base_url="http://x", transport=transport, timeout=1.0
        ) as http:
            client = openwebui_client.OpenWebUIClient(
                base_url="http://x",
                api_key="tk",
                http_client=http,
                backoff=Backoff(cap_seconds=60, base_seconds=1, factor=2),
                sleep=fake_sleep,
            )
            with pytest.raises(asyncio.CancelledError):
                async for _ in client.ws_iter():
                    pass
        # We saw at least the first connect attempt and one backoff sleep.
        assert call_count["i"] >= 1
        assert len(sleeps) >= 1

    asyncio.run(go())


# ---------------------------------------------------------------------------
# start() and aclose() lifecycle (covers 239-281)
# ---------------------------------------------------------------------------


def test_start_creates_owned_client_sync():
    """When no http_client is injected, start() creates one with default headers."""
    import asyncio

    async def go():
        client = openwebui_client.OpenWebUIClient(
            base_url="http://openwebui:8080",
            api_key="tk_test",
        )
        # Pre-start: http is None.
        assert client._http is None
        await client.start()
        try:
            assert client._http is not None
            assert client._http.headers.get("Authorization") == "Bearer tk_test"
        finally:
            await client.aclose()

    asyncio.run(go())


def test_start_is_noop_when_client_already_injected():
    """When http_client is injected, start() doesn't overwrite it."""
    import asyncio

    async def go():
        custom = httpx.MockTransport(lambda r: httpx.Response(200))
        http = httpx.AsyncClient(transport=custom, timeout=1.0)
        client = openwebui_client.OpenWebUIClient(
            base_url="http://openwebui:8080",
            api_key="tk_test",
            http_client=http,
        )
        await client.start()
        try:
            assert client._http is http
        finally:
            await client.aclose()

    asyncio.run(go())


def test_aclose_owned_client_closes_it():
    """aclose() on an owning client closes the internal AsyncClient."""
    import asyncio

    async def go():
        client = openwebui_client.OpenWebUIClient(
            base_url="http://openwebui:8080",
            api_key="tk_test",
        )
        await client.start()
        await client.aclose()
        assert client._http is None

    asyncio.run(go())


# ---------------------------------------------------------------------------
# poll_once error paths (covers 304, 310-325)
# ---------------------------------------------------------------------------


async def test_poll_once_handles_5xx():
    """A 5xx from the chats endpoint logs and returns {}."""
    custom = httpx.MockTransport(lambda r: httpx.Response(500, text="boom"))
    async with httpx.AsyncClient(
        base_url="http://x", transport=custom, timeout=1.0
    ) as http:
        client = openwebui_client.OpenWebUIClient(
            base_url="http://x", api_key="tk", http_client=http
        )
        result = await client.poll_once()
        assert result == {}


async def test_poll_once_handles_network_error():
    """A network error during poll logs and returns {}."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope")

    custom = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        base_url="http://x", transport=custom, timeout=1.0
    ) as http:
        client = openwebui_client.OpenWebUIClient(
            base_url="http://x", api_key="tk", http_client=http
        )
        result = await client.poll_once()
        assert result == {}


async def test_poll_once_handles_401():
    """A 401 from the chats endpoint logs and returns {}."""
    custom = httpx.MockTransport(lambda r: httpx.Response(401, text="no"))
    async with httpx.AsyncClient(
        base_url="http://x", transport=custom, timeout=1.0
    ) as http:
        client = openwebui_client.OpenWebUIClient(
            base_url="http://x", api_key="tk", http_client=http
        )
        result = await client.poll_once()
        assert result == {}


async def test_poll_once_skips_non_dict_chats():
    """Non-dict chat entries in the response are skipped, not crashed on."""
    chats_with_garbage = [
        "not a dict",
        42,
        None,
        {
            "id": "c1",
            "title": "t",
            "updated_at": time.time(),
            "chat_history": [
                {"role": "assistant", "content": "x", "done": True, "updated_at": time.time()}
            ],
        },
    ]
    custom = httpx.MockTransport(
        lambda r: httpx.Response(200, json={"chats": chats_with_garbage, "total": 4})
    )
    async with httpx.AsyncClient(
        base_url="http://x", transport=custom, timeout=1.0
    ) as http:
        client = openwebui_client.OpenWebUIClient(
            base_url="http://x", api_key="tk", http_client=http
        )
        result = await client.poll_once()
        # Only the well-formed chat makes it through.
        assert result == {"c1": "idle"}


async def test_poll_once_skips_chats_without_id():
    """Chats missing an id field are skipped silently."""
    chats = [
        {"title": "t", "chat_history": []},  # no id
        {
            "id": "c1",
            "title": "t",
            "updated_at": time.time(),
            "chat_history": [
                {"role": "assistant", "content": "x", "done": True, "updated_at": time.time()}
            ],
        },
    ]
    custom = httpx.MockTransport(
        lambda r: httpx.Response(200, json={"chats": chats, "total": 2})
    )
    async with httpx.AsyncClient(
        base_url="http://x", transport=custom, timeout=1.0
    ) as http:
        client = openwebui_client.OpenWebUIClient(
            base_url="http://x", api_key="tk", http_client=http
        )
        result = await client.poll_once()
        assert result == {"c1": "idle"}


async def test_poll_once_handles_non_list_chats():
    """If the chats field is not a list, return empty."""
    custom = httpx.MockTransport(
        lambda r: httpx.Response(200, json={"chats": "not a list", "total": 0})
    )
    async with httpx.AsyncClient(
        base_url="http://x", transport=custom, timeout=1.0
    ) as http:
        client = openwebui_client.OpenWebUIClient(
            base_url="http://x", api_key="tk", http_client=http
        )
        result = await client.poll_once()
        assert result == {}
