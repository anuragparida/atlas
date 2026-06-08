"""NTFY publish client per PHASE2-SPEC.md §1.6.

Plain HTTP POST. Per-topic Bearer token. Title, tags, click, priority,
message body — all spec-mandated. Body length capped at 200 chars;
truncated with `…` if longer.

Layer-3 idempotency lives in the caller's Message-Id header. NTFY
deduplicates by ``Message-Id`` server-side, which catches the rare
replay after a notifier restart.

Error handling per spec §1.6:
- 401/403: log warn, do not retry (token rotation problem).
- 5xx / timeout: log warn, do not retry.
- 2xx: return True, the completion is delivered.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx


log = logging.getLogger(__name__)

BODY_MAX_CHARS = 200
ELLIPSIS = "…"

# NTFY topic names: lowercase alphanumerics + hyphen + underscore. The spec
# uses ``atlas-<uuid>``. Reject anything containing characters that would
# be unsafe in a URL path or that NTFY would munge.
_TOPIC_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _truncate(body: str, n: int = BODY_MAX_CHARS) -> str:
    if len(body) <= n:
        return body
    return body[: n - len(ELLIPSIS)] + ELLIPSIS


def _sanitize_topic(topic: str) -> str:
    """Lowercase, then reject anything that isn't ``[a-z0-9_-]``."""
    t = (topic or "").strip().lower()
    if not _TOPIC_RE.match(t):
        raise ValueError(
            f"unsafe NTFY topic {topic!r}: must match {_TOPIC_RE.pattern}"
        )
    return t


class NtfyClient:
    def __init__(
        self,
        base_url: str,
        publish_token: str,
        http_client: httpx.AsyncClient | None = None,
        backoff: Any | None = None,
        sleep: Any | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = publish_token
        # Either accept an injected AsyncClient (tests) or own one (prod).
        self._owns_client = http_client is None
        self._http: httpx.AsyncClient | None = http_client
        self._backoff = backoff
        self._sleep = sleep

    async def start(self) -> None:
        if self._owns_client and self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(2.0),
                headers={"Authorization": f"Bearer {self._token}"},
            )

    async def aclose(self) -> None:
        if self._owns_client and self._http is not None:
            await self._http.aclose()
        self._http = None

    @property
    def reachable(self) -> bool:
        """Cheap health flag — true if the client is initialized. We don't
        probe NTFY in the background; the health endpoint does an explicit
        GET on demand."""
        return self._http is not None

    async def publish(
        self,
        topic: str,
        title: str,
        body: str,
        message_id: str,
        click: str | None = None,
    ) -> bool:
        """Publish a single notification. Returns True on 2xx, False otherwise.

        Raises ``ValueError`` if the topic is unsafe (caller's bug). Never
        raises on transport/HTTP errors — those are logged and return False."""
        try:
            safe_topic = _sanitize_topic(topic)
        except ValueError:
            log.warning("ntfy_topic_rejected topic=%r", topic)
            raise

        if self._http is None:
            log.warning("ntfy_publish_failed reason=client_not_started")
            return False

        truncated = _truncate(body)
        headers = {
            "Title": title,
            "Tags": "speech_balloon",
            "Priority": "default",
            "Message-Id": message_id,
        }
        if click:
            headers["Click"] = click

        try:
            resp = await self._http.post(
                f"{self._base_url}/{safe_topic}",
                content=truncated,
                headers=headers,
            )
        except (httpx.TimeoutException, httpx.HTTPError) as e:
            log.warning(
                "ntfy_publish_failed reason=%s topic=%s",
                type(e).__name__,
                safe_topic,
            )
            return False

        if 200 <= resp.status_code < 300:
            return True
        # 401/403 → log warn, no retry. 5xx → log warn, no retry.
        log.warning(
            "ntfy_publish_failed status=%d topic=%s body=%s",
            resp.status_code,
            safe_topic,
            resp.text[:200],
        )
        return False

    async def ping(self) -> bool:
        """Health probe used by /healthz. Returns True if NTFY responds on /v1/health."""
        if self._http is None:
            return False
        try:
            resp = await self._http.get(f"{self._base_url}/v1/health", timeout=1.0)
            return 200 <= resp.status_code < 300
        except (httpx.TimeoutException, httpx.HTTPError):
            return False
