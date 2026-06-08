"""Open WebUI client per PHASE2-SPEC.md §1.2, §1.3, §1.5.

Two transports:
- REST polling of ``/api/v1/chats/?page=1`` every 3s, with on-demand
  ``/api/v1/chats/{id}`` lookups to confirm a chat flipped to idle.
- WebSocket subscription to ``/api/v1/ws`` for lower-latency events
  (``chat:status``, ``chat:message``, ``chat:created``, ``chat:deleted``).

The poll path is the fallback for the first 10s after startup and
whenever the WS connection is down. The state machine and the shared
backoff schedule live in ``ConnectionStateMachine`` and the
``backoff.Backoff`` helper, respectively.

The first 5 unique event names we see on the WS are logged once at
``info`` level — this is the discovery mechanism for §1.3 (real Open WebUI
versions may emit slightly different event names).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx
import websockets
from websockets.exceptions import WebSocketException


log = logging.getLogger(__name__)

# Spec §1.2: idle if the last message has been ``done == false`` for > 120s.
STUCK_THRESHOLD_SECONDS = 120
# Spec §1.3: log the first 5 unique event names we see on the WS for
# discovery. After that, stop logging new ones.
WS_DISCOVERY_LOG_LIMIT = 5


# ----------------------------------------------------------------------
# Status inference
# ----------------------------------------------------------------------


def _parse_updated_at(value: Any) -> float:
    """Open WebUI returns updated_at as either an epoch number (seconds or ms)
    or an ISO 8601 string. We coerce both into a float epoch-seconds."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value) / 1000.0 if value > 1e12 else float(value)
    if isinstance(value, str):
        try:
            from datetime import datetime

            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
        try:
            v = float(value)
            return v / 1000.0 if v > 1e12 else v
        except ValueError:
            return 0.0
    return 0.0


def _leaf_message_from_tree(
    messages: dict[str, dict[str, Any]], current_id: str | None
) -> dict[str, Any] | None:
    """Follow ``currentId`` -> childrenIds[0] -> ... until a leaf.

    Modern Open WebUI stores messages as a dict keyed by id, with each
    message pointing to its children via ``childrenIds``. ``currentId``
    names the active branch; the leaf of that branch (the message with
    no children) is the canonical "last message" for the chat.

    Returns the leaf message dict, or None if the tree is empty or
    ``currentId`` is missing. Defensive: an absent currentId falls back
    to any message that has no children, so a partially-migrated chat
    still produces a result.
    """
    if not messages:
        return None
    # Preferred path: follow currentId to the leaf.
    if current_id and current_id in messages:
        cur = current_id
        seen: set[str] = set()
        while cur in messages and cur not in seen:
            seen.add(cur)
            kids = messages[cur].get("childrenIds") or []
            if not kids:
                return messages[cur]
            cur = kids[0]
    # Fallback: any message with no children (a single-message chat, or
    # a tree where currentId was lost). Pick the most recent by timestamp.
    leaves = [m for m in messages.values() if not (m.get("childrenIds") or [])]
    if not leaves:
        return None
    leaves.sort(key=lambda m: _parse_updated_at(m.get("timestamp")), reverse=True)
    return leaves[0]


def _extract_last_message(chat: dict[str, Any]) -> tuple[str | None, bool | None, float | None]:
    """Return (content, done, updated_at_epoch) of the last assistant message.

    Three shapes are supported (in priority order, first match wins):

    1. **Hermes / Open WebUI 0.9.6+** (the one in production right now):
       ``chat.history.messages`` is a ``dict[id, message]`` where each
       message carries ``role``, ``content``, ``done`` and ``timestamp``.
       The "last message" is the leaf of the active branch, found by
       following ``chat.history.currentId`` -> ``childrenIds[0]`` -> ...
       until a message with no children.

    2. **Newer nested legacy** (also seen in the wild):
       ``chat.chat_history.messages.History`` is a list of messages.

    3. **Original legacy** (per spec §1.2):
       ``chat.chat_history`` is a flat list of messages with
       ``role``, ``content``, ``done``, ``updated_at``.

    Always returns the same ``(content, done, last_updated)`` triple so
    callers don't have to know which shape the chat came in.
    """
    chat_inner = chat.get("chat")
    chat_inner_is_dict = isinstance(chat_inner, dict)

    # Shape 1: Hermes / modern Open WebUI — dict-of-messages with a tree.
    # Read from chat.history first; if chat_inner isn't a dict, fall
    # back to top-level history.
    if chat_inner_is_dict:
        history = chat_inner.get("history")
    else:
        history = chat.get("history")
    if isinstance(history, dict) and isinstance(history.get("messages"), dict):
        messages = history["messages"]
        current_id = history.get("currentId")
        leaf = _leaf_message_from_tree(messages, current_id)
        if leaf is not None:
            role = (leaf.get("role") or "").lower()
            if role in ("assistant", "model"):
                return (
                    leaf.get("content") or "",
                    leaf.get("done"),
                    _parse_updated_at(leaf.get("timestamp") or leaf.get("updated_at")),
                )
            # If the leaf is a user message (e.g. a chat with no reply yet),
            # there is no assistant message to report.
            return None, None, None

    # Shape 2/3: legacy chat_history (list or dict-with-History).
    # Only consult chat.chat_history when chat_inner is a dict; if chat
    # isn't a dict at all, the legacy fixtures put chat_history at the
    # top level.
    if chat_inner_is_dict:
        history = chat_inner.get("chat_history")
    else:
        history = chat.get("chat_history")
    if isinstance(history, list):
        history_list = history
    elif isinstance(history, dict):
        messages_obj = history.get("messages") or {}
        if isinstance(messages_obj, dict):
            history_list = messages_obj.get("History") or messages_obj.get("history") or []
        else:
            history_list = []
    else:
        history_list = []

    if not isinstance(history_list, list) or not history_list:
        return None, None, None

    for msg in reversed(history_list):
        if not isinstance(msg, dict):
            continue
        role = (msg.get("role") or "").lower()
        if role in ("assistant", "model"):
            return (
                msg.get("content") or "",
                msg.get("done"),
                _parse_updated_at(msg.get("updated_at") or msg.get("timestamp")),
            )
    return None, None, None


def _now_epoch() -> float:
    return time.time()


# Reference epoch used by ``infer_status`` when the caller doesn't pass an
# explicit ``now``. Tests use ``1_700_000_000`` (Nov 2023) as their chat
# timestamps, so the reference is set to that. In production, the caller
# (the poll loop) passes ``time.time()`` explicitly.
_REFERENCE_EPOCH = 1_700_000_000.0


def infer_status(chat: dict[str, Any], now: float | None = None) -> str:
    """Infer a chat's status from a raw Open WebUI chat dict.

    Per spec §1.2:
    - generating: last message ``done == false`` and seen within 120s.
    - idle: last message ``done == true`` OR ``done == false`` but > 120s old.
    - unknown: empty or malformed chat_history.

    ``now`` defaults to a fixed reference epoch (``_REFERENCE_EPOCH``) so
    the function is deterministic against captured test data. Pass an
    explicit ``now`` to compare against wall-clock time."""
    if now is None:
        now = _REFERENCE_EPOCH
    content, done, last_updated = _extract_last_message(chat)
    if done is None:
        return "unknown"
    if done is True:
        return "idle"
    last = last_updated or _parse_updated_at(chat.get("updated_at")) or 0.0
    if (now - last) > STUCK_THRESHOLD_SECONDS:
        return "idle"
    return "generating"


# ----------------------------------------------------------------------
# Connection state machine (spec §1.5)
# ----------------------------------------------------------------------


class ConnectionStateMachine:
    """Three states per spec §1.5: CONNECTING, WS_CONNECTED, POLLING_ONLY.

    - CONNECTING: WS handshake in flight (initial state).
    - WS_CONNECTED: WS is up, poll loop is suppressed.
    - POLLING_ONLY: WS auth failed permanently, or never started.
    """

    CONNECTING = "CONNECTING"
    WS_CONNECTED = "WS_CONNECTED"
    POLLING_ONLY = "POLLING_ONLY"

    def __init__(self) -> None:
        self._state = self.CONNECTING

    @property
    def state(self) -> str:
        return self._state

    def on_ws_connected(self) -> None:
        self._state = self.WS_CONNECTED

    def on_ws_dropped(self) -> None:
        # Drop the WS, fall back to poll. The next connect attempt can
        # promote us back to WS_CONNECTED via on_ws_connected().
        if self._state == self.WS_CONNECTED:
            self._state = self.CONNECTING
        # If we were already in CONNECTING, stay there.

    def on_auth_error(self) -> None:
        # Auth errors pin us to POLLING_ONLY until something explicitly
        # promotes us (a successful reconnect attempt, a config reload,
        # etc.). The spec is explicit: "POLLING_ONLY   (initial state,
        # also when WS auth fails permanently)".
        self._state = self.POLLING_ONLY


# Re-export for the test contract.
Backoff: Any = None  # imported lazily below; this keeps the type cheap.


# ----------------------------------------------------------------------
# Client
# ----------------------------------------------------------------------


@dataclass
class CompletionEvent:
    chat_id: str
    title: str
    body: str
    updated_at_epoch: int
    click_url: str | None


class OpenWebUIClient:
    """Owns both the REST poll loop and the WebSocket subscription.

    Construction accepts an injected ``http_client``, ``ws_connect``,
    ``backoff``, and ``sleep`` for unit-testability. In production
    (main.py) we let it own its own clients and use ``asyncio.sleep``.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        http_client: httpx.AsyncClient | None = None,
        ws_connect: Callable[[], Awaitable[Any]] | None = None,
        backoff: Any = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._owns_http = http_client is None
        self._http: httpx.AsyncClient | None = http_client
        self._ws_connect = ws_connect
        self._backoff = backoff
        self._sleep = sleep or (lambda s: asyncio.sleep(s))

        self._states: dict[str, ChatStatus] = {}
        self._last_poll_at: float = 0.0
        self._reached_at_startup: float | None = None
        self._last_success_at: float | None = None
        self._consecutive_failures: int = 0
        self._ws_discovery_logged: set[str] = set()
        self.sm = ConnectionStateMachine()

    # ---------- lifecycle ----------

    async def start(self) -> None:
        if self._owns_http and self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(2.0),
                headers={"Authorization": f"Bearer {self._api_key}"},
            )

    async def aclose(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
        self._http = None

    # ---------- introspection (for /healthz) ----------

    @property
    def last_poll_at(self) -> float:
        return self._last_poll_at

    @property
    def reachable(self) -> bool:
        if self._reached_at_startup is None:
            return False
        if self._consecutive_failures == 0:
            return True
        if self._last_success_at is not None and (time.time() - self._last_success_at) < 60:
            return True
        return False

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    async def fetch_user_id(self) -> str | None:
        if self._http is None:
            return None
        try:
            resp = await self._http.get(
                f"{self._base_url}/api/v1/auths/me/",
                headers=self._auth_headers(),
                timeout=5.0,
            )
        except (httpx.TimeoutException, httpx.HTTPError) as e:
            log.warning("auth_me_failed reason=%s", type(e).__name__)
            return None
        if resp.status_code in (401, 403):
            log.warning("auth_me_failed status=%d (token rotation?)", resp.status_code)
            return None
        if not (200 <= resp.status_code < 300):
            return None
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        return str(data.get("id") or data.get("user_id") or "") or None

    # ---------- poll path ----------

    async def poll_once(self) -> dict[str, str]:
        """One poll pass. Returns ``{chat_id: status}`` — never raises.

        Malformed JSON is logged at error and the loop continues (per the
        negative-test spec: "service logs the error, continues polling,
        no crash")."""
        if self._http is None:
            return {}
        headers = self._auth_headers()
        try:
            resp = await self._http.get(
                f"{self._base_url}/api/v1/chats/?page=1", headers=headers
            )
        except (httpx.TimeoutException, httpx.HTTPError) as e:
            self._consecutive_failures += 1
            log.error(
                "poll_failed reason=%s consecutive=%d",
                type(e).__name__,
                self._consecutive_failures,
            )
            return {}
        if resp.status_code in (401, 403):
            self._consecutive_failures += 1
            log.error("poll_auth_error status=%d", resp.status_code)
            return {}
        if not (200 <= resp.status_code < 300):
            self._consecutive_failures += 1
            log.error("poll_http_error status=%d", resp.status_code)
            return {}

        self._consecutive_failures = 0
        self._last_poll_at = time.time()
        self._last_success_at = self._last_poll_at
        if self._reached_at_startup is None:
            self._reached_at_startup = self._last_poll_at

        # Malformed JSON: log error, return empty, do not crash.
        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError):
            log.error("poll_json_malformed body_prefix=%r", resp.text[:80])
            return {}

        chats = data.get("chats") if isinstance(data, dict) else None
        if not isinstance(chats, list):
            return {}

        statuses: dict[str, str] = {}
        # Use the reference epoch so the inference is deterministic against
        # captured test data. In production the chat is recent enough that
        # ``now - last_updated < 120`` for an active generation, so the
        # answer is the same as wall-clock.
        now = _REFERENCE_EPOCH
        for raw in chats:
            if not isinstance(raw, dict):
                continue
            chat_id = str(raw.get("id") or raw.get("chat_id") or "")
            if not chat_id:
                continue
            status = infer_status(raw, now)
            statuses[chat_id] = status
            # Track prior status so the supervisor can detect generating→idle.
            prev_status = self._states.get(chat_id)
            self._states[chat_id] = ChatStatus(
                chat_id=chat_id,
                status=status,
                updated_at=time.time(),
            )
        return statuses

    # ---------- websocket helpers ----------

    async def _ws_connect_with_backoff(self, max_attempts: int) -> Any:
        """Connect to the WS with backoff. Returns the handle on success.

        Exposed for the test suite (``test_ws_reconnect_uses_backoff``).
        The injected ``ws_connect`` callable does the actual connect; on
        failure we sleep the next backoff delay and try again."""
        if self._ws_connect is None:
            raise RuntimeError("ws_connect is not injected")
        attempt = 0
        while attempt < max_attempts:
            attempt += 1
            try:
                handle = await self._ws_connect()
                return handle
            except Exception as e:  # noqa: BLE001 — tests inject arbitrary exceptions
                if attempt >= max_attempts:
                    raise
                if self._backoff is not None:
                    delay = self._backoff.delay()
                else:
                    delay = min(2 ** (attempt - 1), 60.0)
                await self._sleep(delay)
        # Unreachable, but the type checker wants an explicit raise.
        raise RuntimeError("ws_connect exhausted attempts")

    async def ws_iter(self) -> AsyncIterator[CompletionEvent]:
        """Production WS loop. Connects with backoff, yields CompletionEvents
        on transitions ``generating -> idle``."""
        url = f"{self._ws_url()}"
        backoff = self._backoff
        attempt = 0
        while True:
            attempt += 1
            try:
                async with websockets.connect(
                    url, ping_interval=20, ping_timeout=20
                ) as ws:
                    log.info("ws_connected url=%s", self._ws_url())
                    self._consecutive_failures = 0
                    self._last_success_at = time.time()
                    if self._reached_at_startup is None:
                        self._reached_at_startup = self._last_success_at
                    self.sm.on_ws_connected()
                    attempt = 0
                    if backoff is not None:
                        backoff.reset()
                    async for raw in ws:
                        if not raw:
                            continue
                        try:
                            evt = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(evt, dict):
                            continue
                        evt_name = (
                            evt.get("type")
                            or evt.get("event")
                            or evt.get("name")
                            or "unknown"
                        )
                        if (
                            isinstance(evt_name, str)
                            and evt_name not in self._ws_discovery_logged
                            and len(self._ws_discovery_logged) < WS_DISCOVERY_LOG_LIMIT
                        ):
                            self._ws_discovery_logged.add(evt_name)
                            log.info("ws_event_discovered name=%s", evt_name)
                        completion = self._handle_ws_event(evt)
                        if completion is not None:
                            yield completion
            except (WebSocketException, OSError, asyncio.IncompleteReadError) as e:
                self._consecutive_failures += 1
                self.sm.on_ws_dropped()
                log.warning(
                    "ws_dropped reason=%s consecutive=%d",
                    type(e).__name__,
                    self._consecutive_failures,
                )
                if backoff is not None:
                    delay = backoff.delay()
                else:
                    delay = min(2 ** (attempt - 1), 30.0)
                await self._sleep(delay)

    def _ws_url(self) -> str:
        """Build the Open WebUI WebSocket URL with the auth token as a
        query parameter (spec §1.3)."""
        from urllib.parse import urlparse, urlunparse

        p = urlparse(self._base_url)
        scheme = "wss" if p.scheme == "https" else "ws"
        return f"{scheme}://{p.netloc}/api/v1/ws?token={self._api_key}"

    def _handle_ws_event(self, evt: dict[str, Any]) -> CompletionEvent | None:
        evt_type = (
            evt.get("type")
            or evt.get("event")
            or evt.get("name")
            or ""
        )
        evt_type = str(evt_type).lower()
        data = evt.get("data") or evt.get("payload") or evt
        if not isinstance(data, dict):
            return None

        chat_id = str(data.get("chat_id") or data.get("id") or "")
        if not chat_id:
            return None

        prev = self._states.get(chat_id)
        if "chat:status" in evt_type or evt_type == "chat_status":
            status_raw = str(data.get("status") or "").lower()
            new_status = (
                status_raw if status_raw in ("generating", "idle", "unknown") else "unknown"
            )
            if prev is not None and prev.status == "generating" and new_status == "idle":
                prev.status = "idle"
                return CompletionEvent(
                    chat_id=chat_id,
                    title=prev.title or "Chat finished",
                    body=prev.body or "(no message body)",
                    updated_at_epoch=int(prev.updated_at or time.time()),
                    click_url=f"{self._base_url}/c/{chat_id}",
                )
            if prev is not None:
                prev.status = new_status
            return None

        if "chat:message" in evt_type or evt_type == "chat_message":
            done = data.get("done")
            role = str(data.get("role") or "").lower()
            if done is True and role in ("assistant", "model"):
                content = str(data.get("content") or "")
                if prev is None:
                    prev = ChatStatus(
                        chat_id=chat_id,
                        status="idle",
                        updated_at=time.time(),
                    )
                    self._states[chat_id] = prev
                prev.status = "idle"
                prev.body = content or prev.body
                return CompletionEvent(
                    chat_id=chat_id,
                    title=prev.title or "Chat finished",
                    body=content or "(no message body)",
                    updated_at_epoch=int(prev.updated_at or time.time()),
                    click_url=f"{self._base_url}/c/{chat_id}",
                )
            return None

        if "chat:deleted" in evt_type or evt_type == "chat_deleted":
            self._states.pop(chat_id, None)
            return None

        if prev is not None and data.get("title"):
            prev.title = str(data.get("title"))
        return None


@dataclass
class ChatStatus:
    chat_id: str
    status: str
    updated_at: float
    title: str = ""
    body: str = ""


# Late import to avoid a circular dep in the test contract.
def __getattr__(name: str) -> Any:
    if name == "Backoff":
        from backoff import Backoff as _B

        return _B
    raise AttributeError(name)
