"""atlas-notifier entry point.

Long-running asyncio service. Two tasks:
- ``supervisor``: merges the WS subscription and the poll loop, dedupes
  by chat_id, and publishes completions to NTFY.
- ``health_server``: tiny HTTP server on 127.0.0.1:health_port serving
  ``GET /healthz`` (spec §1.7). NOT bound to the public interface.

Shutdown: SIGTERM drains in-flight publishes (max ``shutdown_grace``)
and exits 0. No SIGKILL handling in v0.
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import structlog

import config as cfg_mod
import idempotency as idem_mod
import ntfy_client as ntfy_mod
import openwebui_client as owu_mod
from health import HealthCheck


# ---------- logging ----------

def _configure_logging() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=logging.INFO,
        stream=sys.stdout,
    )
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


log = structlog.get_logger("atlas-notifier")


# ---------- supervisor ----------


class Supervisor:
    """Merges the WS subscription and the poll loop, publishes to NTFY."""

    def __init__(
        self,
        cfg: cfg_mod.Config,
        owu: owu_mod.OpenWebUIClient,
        ntfy: ntfy_mod.NtfyClient,
        cache: idem_mod.IdempotencyCache,
    ) -> None:
        self.cfg = cfg
        self.owu = owu
        self.ntfy = ntfy
        self.cache = cache
        self._inflight: set[asyncio.Task[Any]] = set()
        self._stopped = asyncio.Event()
        # Per-chat status from the previous poll. Used to detect
        # generating→idle transitions; reading self.owu._states after
        # poll_once returns the NEW state, not the previous one, so we
        # track our own copy here.
        self._last_statuses: dict[str, str] = {}

    def request_stop(self) -> None:
        self._stopped.set()

    async def run(self) -> None:
        # Run both transports as parallel tasks. Whichever yields a
        # CompletionEvent first gets published. Dedup is handled by the
        # idempotency cache: if both transports observe the same chat,
        # only the first claim wins.
        tasks: list[asyncio.Task[Any]] = [
            asyncio.create_task(self._consume_ws(), name="ws"),
            # Poll loop is suppressed for the first 10s while WS connects.
            asyncio.create_task(self._consume_poll(), name="poll"),
        ]
        try:
            await self._stopped.wait()
        finally:
            for t in tasks:
                t.cancel()
            for t in tasks:
                with suppress(asyncio.CancelledError, Exception):
                    await t
            # Drain in-flight publishes, bounded by grace.
            if self._inflight:
                log.info("draining_inflight", count=len(self._inflight))
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*self._inflight, return_exceptions=True),
                        timeout=self.cfg.shutdown_grace_seconds,
                    )
                except asyncio.TimeoutError:
                    log.warning("drain_timeout", grace=self.cfg.shutdown_grace_seconds)

    async def _consume_ws(self) -> None:
        try:
            async for evt in self.owu.ws_iter():
                await self._maybe_publish(evt)
        except asyncio.CancelledError:
            raise

    async def _consume_poll(self) -> None:
        try:
            # Hold off 10s to let WS connect first (spec §1.3).
            await asyncio.sleep(10.0)
            while True:
                statuses = await self.owu.poll_once()
                now = asyncio.get_running_loop().time()
                # Walk the prior state and detect generating→idle transitions.
                for chat_id, status in statuses.items():
                    prev = self._last_statuses.get(chat_id)
                    if prev == "generating" and status == "idle":
                        # Build a synthetic completion event from the latest known state.
                        last = self.owu._states.get(chat_id)  # noqa: SLF001
                        completion = owu_mod.CompletionEvent(
                            chat_id=chat_id,
                            title=(last.title if last else "") or "Chat finished",
                            body=(last.body if last else "") or "(no message body)",
                            updated_at_epoch=int(
                                (last.updated_at if last else 0) or now
                            ),
                            click_url=f"{self.cfg.openwebui_base_url}/c/{chat_id}",
                        )
                        await self._maybe_publish(completion)
                    self._last_statuses[chat_id] = status
                # Sleep per config (or backoff). The poll path is in the
                # OpenWebUIClient's poll_iter for production, but the test
                # contract is poll_once. We sleep the configured interval.
                await asyncio.sleep(self.cfg.poll_interval_seconds)
        except asyncio.CancelledError:
            raise

    async def _maybe_publish(self, evt: owu_mod.CompletionEvent) -> None:
        # Dedup: claim the (chat_id, ts) tuple. Skip if already seen.
        if not self.cache.is_duplicate(evt.chat_id, evt.updated_at_epoch):
            log.info(
                "publishing_completion",
                chat_id=evt.chat_id,
                title=evt.title,
            )
            task = asyncio.create_task(
                self.ntfy.publish(
                    topic=self.cfg.ntfy_topic(),
                    title=evt.title,
                    body=evt.body,
                    message_id=f"atlas-{evt.chat_id}-{evt.updated_at_epoch}",
                    click=evt.click_url,
                )
            )
            self._inflight.add(task)
            task.add_done_callback(self._inflight.discard)


# ---------- health server ----------


class _HealthHandler(BaseHTTPRequestHandler):
    server_version = "atlas-notifier/0.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        # Quiet the default stderr access log. Production logs are structured.
        return

    def do_GET(self) -> None:  # noqa: N802 — http.server convention
        snap: dict[str, Any] = self.server.health_snapshot()  # type: ignore[attr-defined]
        hc = HealthCheck(
            openwebui_reachable=bool(snap.get("openwebui_reachable")),
            ntfy_reachable=bool(snap.get("ntfy_reachable")),
            last_poll_at=float(snap.get("last_poll_at") or 0.0),
            dedup_cache_size=int(snap.get("dedup_cache_size") or 0),
        )
        status, body = hc.serve("GET", self.path)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(json.dumps(body))))
        self.end_headers()
        self.wfile.write(json.dumps(body).encode("utf-8"))


class HealthServer:
    def __init__(self, host: str, port: int, snapshot_fn) -> None:
        self._host = host
        self._port = port
        self._snapshot_fn = snapshot_fn
        self._server: ThreadingHTTPServer | None = None
        self._thread: Any = None

    def start(self) -> None:
        server = ThreadingHTTPServer((self._host, self._port), _HealthHandler)
        server.health_snapshot = self._snapshot_fn  # type: ignore[attr-defined]
        self._server = server
        import threading

        self._thread = threading.Thread(target=server.serve_forever, daemon=True)
        self._thread.start()
        log.info("health_server_started", host=self._host, port=self._port)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        log.info("health_server_stopped")


# ---------- main ----------


async def amain() -> int:
    _configure_logging()
    cfg = cfg_mod.load()
    log.info("startup", openwebui=cfg.openwebui_base_url, ntfy=cfg.ntfy_base_url)

    # Layer 1 dedup cache (in-memory); load any persisted keys on startup.
    cache = idem_mod.IdempotencyCache(
        max_size=1000, ttl_seconds=3600.0, state_dir=cfg.state_dir
    )
    await cache.load_from_disk_async()

    owu = owu_mod.OpenWebUIClient(
        base_url=cfg.openwebui_base_url,
        api_key=cfg.openwebui_api_key,
    )
    ntfy = ntfy_mod.NtfyClient(
        base_url=cfg.ntfy_base_url,
        publish_token=cfg.ntfy_publish_token,
    )
    await owu.start()
    await ntfy.start()

    # If ATLAS_USER_ID is not set, fetch it once at startup. The fetched id
    # is used to derive the NTFY topic (spec §1.4).
    if not cfg.atlas_user_id:
        try:
            uid = await owu.fetch_user_id()
            if uid:
                # Rebuild config with the discovered user_id. We do this
                # by monkey-patching the frozen dataclass via object.__setattr__.
                object.__setattr__(cfg, "atlas_user_id", uid)
                log.info("atlas_user_id_discovered", user_id=uid)
        except Exception as e:  # noqa: BLE001
            log.warning("user_id_discovery_failed", error=str(e))

    if not cfg.atlas_user_id:
        log.error("atlas_user_id_missing")
        # Don't exit — healthz will report it. Operators can set ATLAS_USER_ID
        # and restart.

    supervisor = Supervisor(cfg, owu, ntfy, cache)

    # Health server.
    def snapshot() -> dict[str, Any]:
        return {
            "openwebui_reachable": owu.reachable,
            "ntfy_reachable": ntfy.reachable,
            "last_poll_at": owu.last_poll_at,
            "dedup_cache_size": len(cache),
        }

    health = HealthServer(cfg.health_host, cfg.health_port, snapshot)
    health.start()

    # Wire SIGTERM/SIGINT to the supervisor's stop event.
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, supervisor.request_stop)

    try:
        await supervisor.run()
    finally:
        log.info("shutdown_begin")
        # Persist dedup keys (Layer 2 of idempotency, spec §1.8).
        try:
            cache.save_to_disk()
        except Exception as e:  # noqa: BLE001
            log.warning("seen_persist_failed", error=str(e))
        health.stop()
        await owu.aclose()
        await ntfy.aclose()
        log.info("shutdown_complete")
    return 0


def main() -> int:
    return asyncio.run(amain())


if __name__ == "__main__":
    raise SystemExit(main())
