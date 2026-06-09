"""Health endpoint per PHASE2-SPEC.md §1.7.

Exposes ``/healthz`` on 127.0.0.1:18080 (NOT on the public interface) that
returns 200 + JSON ``{openwebui_reachable, ntfy_reachable, last_poll_at,
dedup_cache_size}`` when the service is fully wired up, and 503 if either
of the upstream reachability flags is false.

We do NOT run a full HTTP server in production. ``HealthCheck`` exposes
``.serve(method, path) -> (status, body)`` that an HTTP layer (a tiny
``http.server``-based handler in main.py, or a test stub) can dispatch.
This keeps the service dependency-free of ``aiohttp``/``starlette`` and
stays inside the §4 RSS budget.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class HealthCheck:
    openwebui_reachable: bool
    ntfy_reachable: bool
    last_poll_at: float
    dedup_cache_size: int

    def snapshot(self) -> dict[str, Any]:
        """The exact JSON shape per spec §1.7."""
        return asdict(self)

    def to_http_response(self) -> "_HttpResponse":
        status = 200 if (self.openwebui_reachable and self.ntfy_reachable) else 503
        return _HttpResponse(status_code=status, body=self.snapshot())

    def serve(self, method: str, path: str) -> tuple[int, dict[str, Any] | str]:
        """Tiny dispatch surface for an HTTP handler.

        Returns (status_code, body). Body is a dict on success, a string
        error message on the wrong path/method."""
        if path != "/healthz":
            return 404, "not found"
        if method != "GET":
            return 405, "method not allowed"
        status = 200 if (self.openwebui_reachable and self.ntfy_reachable) else 503
        return status, self.snapshot()


@dataclass
class _HttpResponse:
    status_code: int
    body: dict[str, Any]

    def json(self) -> str:
        return json.dumps(self.body)
