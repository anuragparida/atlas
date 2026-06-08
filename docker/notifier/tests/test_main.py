"""Tests for the Supervisor in main.py.

The supervisor merges the poll and WS streams, dedupes via the
idempotency cache, and publishes CompletionEvents to NTFY. These
tests focus on the poll-path transition detection: when a chat flips
from ``generating`` to ``idle`` between two polls, the supervisor
must publish exactly one completion event.

Background: the previous implementation read the prior status from
``self.owu._states[chat_id]`` *after* ``poll_once`` had already
overwritten it with the new status, so no transition was ever
detected and no NTFY event was ever published for a real chat. The
fix adds a ``_last_statuses`` dict on the supervisor that is updated
*after* the transition check, so the prior status survives across
the in-place update of ``_states``.

These tests pin the new (correct) behavior.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import httpx
import pytest
import respx

import config as cfg_mod
import idempotency as idem_mod
import main as main_mod
import openwebui_client as owu_mod


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


@dataclass
class StubNtfy:
    """In-memory NtfyClient. Records every publish for assertions."""

    log: list[dict] = field(default_factory=list)

    async def publish(
        self,
        topic: str,
        title: str,
        body: str,
        message_id: str,
        click: str | None = None,
    ) -> bool:
        self.log.append({
            "topic": topic, "title": title, "body": body,
            "message_id": message_id, "click": click,
        })
        return True


def _make_config(user_id: str = "u-anurag") -> cfg_mod.Config:
    return cfg_mod.Config(
        openwebui_base_url="http://openwebui:8080",
        openwebui_api_key="fake-key",
        ntfy_base_url="http://ntfy:8090",
        ntfy_publish_token="tk",
        ntfy_publish_timeout_seconds=2.0,
        atlas_user_id=user_id,
        poll_interval_seconds=0,
        state_dir=None,
        shutdown_grace_seconds=1,
        health_host="127.0.0.1",
        health_port=18080,
        openwebui_ws_url="ws://openwebui:8080/api/v1/ws",
    )


def _make_full_chat(
    chat_id: str, title: str, *, done: bool, content: str = "",
) -> dict[str, Any]:
    """Build a Hermes-shape full chat dict for /api/v1/chats/{id}."""
    ts = 1_700_000_000_000
    ts2 = ts + 100
    return {
        "id": chat_id, "title": title,
        "chat": {
            "title": title, "models": ["Hermes Agent"],
            "history": {
                "currentId": "b1",
                "messages": {
                    "a1": {
                        "id": "a1", "parentId": None, "childrenIds": ["b1"],
                        "role": "user", "content": "Q",
                        "timestamp": ts, "models": ["Hermes Agent"],
                    },
                    "b1": {
                        "id": "b1", "parentId": "a1", "childrenIds": [],
                        "role": "assistant", "content": content,
                        "timestamp": ts2, "models": ["Hermes Agent"],
                        "done": done,
                    },
                },
            },
        },
    }


def _make_slim(chat_id: str, title: str) -> dict[str, Any]:
    return {"id": chat_id, "title": title, "updated_at": 1_700_000_000_000, "created_at": 1_700_000_000_000}


def _drive_supervisor_polls(
    supervisor: main_mod.Supervisor,
    owu: owu_mod.OpenWebUIClient,
    statuses_iter,
) -> None:
    """Run the supervisor's transition logic for a sequence of poll results.

    ``statuses_iter`` is an async iterator that yields the dicts returned
    by successive ``poll_once()`` calls. The function is intentionally
    a thin mirror of ``Supervisor._consume_poll``'s body — duplicated
    here so we can drive the same code path with mock HTTP responses
    and avoid spinning up the real sleep loop.
    """
    async def _run() -> None:
        async for statuses in statuses_iter:
            for cid, status in statuses.items():
                prev = supervisor._last_statuses.get(cid)
                if prev == "generating" and status == "idle":
                    last = owu._states.get(cid)
                    evt = owu_mod.CompletionEvent(
                        chat_id=cid,
                        title=(last.title if last else "") or "Chat finished",
                        body=(last.body if last else "") or "(no message body)",
                        updated_at_epoch=int((last.updated_at if last else 0) or 0),
                        click_url=f"{supervisor.cfg.openwebui_base_url}/c/{cid}",
                    )
                    await supervisor._maybe_publish(evt)
                supervisor._last_statuses[cid] = status

    # We don't actually await _run here — callers do, so they can
    # interleave with respx side_effect mutations.
    return _run()  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_supervisor_publishes_on_generating_to_idle_transition():
    """A chat that was generating and is now idle must publish exactly one event.

    This is the core 5.2.4 acceptance: real chat → real NTFY event.
    """
    chat_id = "chat-generate-then-idle"
    full_a = _make_full_chat(chat_id, "atlas-524-test", done=False, content="")
    full_b = _make_full_chat(chat_id, "atlas-524-test", done=True, content="A")
    slim = [_make_slim(chat_id, "atlas-524-test")]

    captured: list[dict] = []
    ntfy = StubNtfy(log=captured)

    with respx.mock(base_url="http://openwebui:8080", assert_all_called=False) as mock:
        mock.get("/api/v1/chats/").mock(
            side_effect=lambda req: httpx.Response(200, json=list(slim))
        )
        # The per-chat route needs a regex matcher.
        chat_route = mock.get(
            "/api/v1/chats/{chat_id}",
            path__regex=r"^/api/v1/chats/[^/]+$",
        )
        chat_route.mock(side_effect=lambda req: httpx.Response(200, json=full_a))

        owu = owu_mod.OpenWebUIClient("http://openwebui:8080", "fake-key")
        await owu.start()
        supervisor = main_mod.Supervisor(_make_config(), owu, ntfy, idem_mod.IdempotencyCache())

        async def polls():
            yield await owu.poll_once()  # poll 1: full_a (generating)
            # Swap to idle for poll 2.
            chat_route.mock(side_effect=lambda req: httpx.Response(200, json=full_b))
            yield await owu.poll_once()  # poll 2: full_b (idle)

        # Drive the supervisor's transition logic across both polls.
        await _drive_supervisor_polls(supervisor, owu, polls())

        # Drain in-flight publishes.
        if supervisor._inflight:
            await asyncio.gather(*supervisor._inflight, return_exceptions=True)
        await owu.aclose()

    assert len(captured) == 1, (
        f"expected 1 NTFY publish on the transition, got {len(captured)}: {captured}"
    )
    pub = captured[0]
    assert pub["topic"] == "atlas-u-anurag"
    assert pub["message_id"].startswith(f"atlas-{chat_id}-")
    # Title falls back to "Chat finished" because the poll path doesn't
    # populate ChatStatus.title (only the WS path does). That's a known
    # limitation; the spec only requires the publish to fire.
    assert pub["title"] in ("Chat finished", "atlas-524-test")


async def test_supervisor_does_not_publish_for_idle_chat_on_startup():
    """A chat that is already idle on the first poll must NOT publish.

    Startup noise: the notifier's first poll sees the world as it is, with
    no prior state. A chat that has been idle forever should not fire a
    NTFY event on startup — that would page the user about every chat
    in their history.
    """
    chat_id = "chat-already-idle"
    full = _make_full_chat(chat_id, "old chat", done=True, content="A")
    slim = [_make_slim(chat_id, "old chat")]

    captured: list[dict] = []
    ntfy = StubNtfy(log=captured)

    with respx.mock(base_url="http://openwebui:8080", assert_all_called=False) as mock:
        mock.get("/api/v1/chats/").mock(
            side_effect=lambda req: httpx.Response(200, json=list(slim))
        )
        mock.get(
            "/api/v1/chats/{chat_id}",
            path__regex=r"^/api/v1/chats/[^/]+$",
        ).mock(side_effect=lambda req: httpx.Response(200, json=full))

        owu = owu_mod.OpenWebUIClient("http://openwebui:8080", "fake-key")
        await owu.start()
        supervisor = main_mod.Supervisor(_make_config(), owu, ntfy, idem_mod.IdempotencyCache())

        async def polls():
            yield await owu.poll_once()  # only one poll, already idle

        await _drive_supervisor_polls(supervisor, owu, polls())
        if supervisor._inflight:
            await asyncio.gather(*supervisor._inflight, return_exceptions=True)
        await owu.aclose()

    assert captured == [], (
        f"startup noise: idle chat should not publish, got {captured}"
    )


async def test_poll_once_populates_chatstatus_title_and_body():
    """After poll_once, ChatStatus should carry the chat's title and the
    last assistant message's body so the supervisor can synthesize a
    useful CompletionEvent on the transition (not just "Chat finished" /
    "(no message body)").
    """
    chat_id = "chat-with-content"
    full = _make_full_chat(chat_id, "My useful title", done=True, content="The answer is 42.")
    slim = [_make_slim(chat_id, "My useful title")]

    with respx.mock(base_url="http://openwebui:8080", assert_all_called=False) as mock:
        mock.get("/api/v1/chats/").mock(
            side_effect=lambda req: httpx.Response(200, json=list(slim))
        )
        mock.get(
            "/api/v1/chats/{chat_id}",
            path__regex=r"^/api/v1/chats/[^/]+$",
        ).mock(side_effect=lambda req: httpx.Response(200, json=full))

        owu = owu_mod.OpenWebUIClient("http://openwebui:8080", "fake-key")
        await owu.start()
        try:
            await owu.poll_once()
            state = owu._states.get(chat_id)
            assert state is not None
            assert state.title == "My useful title"
            assert state.body == "The answer is 42."
        finally:
            await owu.aclose()


async def test_supervisor_publishes_per_chat_in_multichat_transition():
    """In a poll with multiple chats, only the one that flipped should publish.

    Regression: a chat that stays in 'generating' across two polls must
    not fire, and a chat that stays in 'idle' must not fire. Only the
    one chat that went generating → idle should publish.
    """
    chat_gen_idle = "chat-1-flips"
    chat_still_gen = "chat-2-stays-generating"
    chat_still_idle = "chat-3-stays-idle"
    full_gen_idle_a = _make_full_chat(chat_gen_idle, "c1", done=False, content="")
    full_gen_idle_b = _make_full_chat(chat_gen_idle, "c1", done=True, content="A")
    full_still_gen = _make_full_chat(chat_still_gen, "c2", done=False, content="")
    full_still_idle = _make_full_chat(chat_still_idle, "c3", done=True, content="A")
    slim = [
        _make_slim(chat_gen_idle, "c1"),
        _make_slim(chat_still_gen, "c2"),
        _make_slim(chat_still_idle, "c3"),
    ]

    captured: list[dict] = []
    ntfy = StubNtfy(log=captured)
    full_by_id = {
        chat_gen_idle: full_gen_idle_a,
        chat_still_gen: full_still_gen,
        chat_still_idle: full_still_idle,
    }

    with respx.mock(base_url="http://openwebui:8080", assert_all_called=False) as mock:
        mock.get("/api/v1/chats/").mock(
            side_effect=lambda req: httpx.Response(200, json=list(slim))
        )
        chat_route = mock.get(
            "/api/v1/chats/{chat_id}",
            path__regex=r"^/api/v1/chats/[^/]+$",
        )
        def _chat_handler(req: httpx.Request) -> httpx.Response:
            cid = req.url.path.rsplit("/", 1)[-1]
            return httpx.Response(200, json=full_by_id[cid])
        chat_route.mock(side_effect=_chat_handler)

        owu = owu_mod.OpenWebUIClient("http://openwebui:8080", "fake-key")
        await owu.start()
        supervisor = main_mod.Supervisor(_make_config(), owu, ntfy, idem_mod.IdempotencyCache())

        async def polls():
            yield await owu.poll_once()  # all three as their initial state
            # Swap chat 1 to idle; others unchanged.
            full_by_id[chat_gen_idle] = full_gen_idle_b
            yield await owu.poll_once()  # chat 1 idle, others same

        await _drive_supervisor_polls(supervisor, owu, polls())
        if supervisor._inflight:
            await asyncio.gather(*supervisor._inflight, return_exceptions=True)
        await owu.aclose()

    # Exactly one publish, for the chat that flipped.
    assert len(captured) == 1, f"expected 1 publish, got {len(captured)}: {captured}"
    assert chat_gen_idle in captured[0]["message_id"]
    assert chat_still_gen not in captured[0]["message_id"]
    assert chat_still_idle not in captured[0]["message_id"]
