"""Environment-variable configuration for atlas-notifier.

All config is env-driven. Defaults match the docker-compose fragment in
PHASE2-SPEC.md §3 (hostnames ``openwebui`` and ``ntfy`` are the service
names on the atlas compose network). Use .env (gitignored) or the
orchestrator's secret store; never bake secrets into the image.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse, urlunparse


def _env(name: str, default: str | None = None, *, required: bool = False) -> str:
    val = os.environ.get(name, default)
    if required and (val is None or val == ""):
        raise RuntimeError(f"Required env var {name!r} is not set")
    if val is None:
        val = ""
    return val


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as e:
        raise RuntimeError(f"Env var {name!r} must be an integer, got {raw!r}") from e


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as e:
        raise RuntimeError(f"Env var {name!r} must be a float, got {raw!r}") from e


def _ws_url(http_url: str) -> str:
    """Convert ``http://host:port/api/foo`` to ``ws://host:port/api/foo``."""
    p = urlparse(http_url)
    scheme = "wss" if p.scheme == "https" else "ws"
    return urlunparse(p._replace(scheme=scheme))


@dataclass(frozen=True)
class Config:
    # Open WebUI side
    openwebui_base_url: str
    openwebui_api_key: str

    # NTFY side
    ntfy_base_url: str
    ntfy_publish_token: str
    ntfy_publish_timeout_seconds: float

    # Atlas user (optional; fetched at startup via /api/v1/auths/me/)
    atlas_user_id: str | None

    # Loop tuning
    poll_interval_seconds: int
    state_dir: str
    shutdown_grace_seconds: int

    # Health endpoint
    health_host: str
    health_port: int

    # Derived
    openwebui_ws_url: str

    def ntfy_topic(self, user_id: str | None = None) -> str:
        """The NTFY topic name; per spec §1.4 = ``atlas-<userid>``."""
        uid = user_id or self.atlas_user_id
        if not uid:
            raise RuntimeError("atlas_user_id is not set; cannot derive NTFY topic")
        return f"atlas-{uid}"


def load() -> Config:
    openwebui_base_url = _env(
        "OPENWEBUI_BASE_URL",
        os.environ.get("OPENWEBUI_URL", "http://openwebui:8080"),
    ).rstrip("/")
    return Config(
        openwebui_base_url=openwebui_base_url,
        openwebui_api_key=_env("OPENWEBUI_API_KEY", required=True),
        ntfy_base_url=_env(
            "NTFY_BASE_URL",
            os.environ.get("NTFY_URL", "http://ntfy:8090"),
        ).rstrip("/"),
        ntfy_publish_token=_env("NTFY_PUBLISH_TOKEN", required=True),
        ntfy_publish_timeout_seconds=_env_float("ATLAS_NTFY_TIMEOUT", 2.0),
        atlas_user_id=_env("ATLAS_USER_ID") or None,
        poll_interval_seconds=_env_int("ATLAS_POLL_INTERVAL", 3),
        state_dir=_env("ATLAS_STATE_DIR", "/var/lib/atlas-notifier"),
        shutdown_grace_seconds=_env_int("ATLAS_SHUTDOWN_GRACE", 5),
        health_host=_env("ATLAS_HEALTH_HOST", "127.0.0.1"),
        health_port=_env_int("ATLAS_HEALTH_PORT", 18080),
        openwebui_ws_url=_env("OPENWEBUI_WS_URL", _ws_url(openwebui_base_url) + "/api/v1/ws"),
    )
