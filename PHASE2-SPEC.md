# Atlas Phase 2 — atlas-notifier + NTFY Docker Stack

> Spec-only. No code in this file. Produces the design that Phase 2's PR will implement.
> Reads against `SPEC.md` §7 (notifications) and §8 (project structure). Implements the
> `docker/` subtree: `notifier/` (Python listener) and `ntfy/` (self-hosted NTFY).

---

## 0. Goals and non-goals

**Goal.** Ship the two Docker services that turn "Open WebUI finishes a chat" into an iOS notification, with no APNS, no $99 Apple fee, no App Store.

**Non-goals.**
- No mobile-side code (Phase 2 is server-side only; `src/notifications/NtfyListener.ts` ships with Phase 1 separately, or in a follow-up PR).
- No multi-tenant NTFY topics beyond `atlas-<userid>`. One topic per Open WebUI user; that's all Atlas needs in v1.
- No APNS fallback, no push token storage, no token rotation.
- No "rich" NTFY actions (tap-to-mute, threading, attachments). Plain title + body + tag.
- No Phase 4 (Atlas Hermes) hooks. The notifier is single-tenant-aware but single-tenant.

**Exit criteria for Phase 2.**
1. `docker compose up` brings up `atlas-notifier` + `ntfy` alongside the existing Open WebUI stack.
2. Open WebUI generation completion fires an NTFY event within 3 seconds of the chat flipping to `idle`.
3. NTFY self-host serves on `http://openclaw:8090` (LAN) and `http://100.83.146.18:8090` (Tailscale).
4. `atlas-notifier` process RSS ≤ 50 MB at idle (verified by `docker stats`).
5. NTFY auth required (no anonymous publish or subscribe).
6. iOS NTFY app, subscribed to `atlas-<userid>`, receives a notification when a chat completes on a backgrounded Atlas Chat.
7. Idempotency: the same completion event never produces a duplicate NTFY message, even after notifier restart.

---

## 1. atlas-notifier — Python service

### 1.1 Shape

A single Python 3.12 process. Long-running, no workers, no threads beyond the asyncio event loop. No `asyncpg`-style connection pool — it doesn't need one (no DB). No `uvicorn` — it speaks plain HTTP only as an NTFY client (POSTs), and as a WebSocket client to Open WebUI. It does not expose an HTTP server of its own in v1. (See §1.7 for the optional `/metrics` carve-out.)

### 1.2 What it polls in Open WebUI

Open WebUI exposes a REST API. The exact endpoints the notifier uses, with the fields it reads:

| Endpoint | Method | Cadence | Read | Purpose |
|---|---|---|---|---|
| `/api/v1/chats/?page=1` | GET | every 3s while WS disconnected; suppressed when WS is connected | `id`, `title`, `updated_at`, `chat.content` (last message), `chat.models[]` | Authoritative fallback path. Walk the page, build a `chat_id → status` map, diff against last poll. |
| `/api/v1/chats/{id}` | GET | on demand, when a new chat id is seen | full chat record | Confirm `chat.chat_history[].done == true` on the assistant's last turn. Don't trust "title changed" alone. |
| `/api/v1/models/` | GET | once on startup | model list | Health-check the API; surfaces a clear error if auth is wrong. |

> The notifier authenticates to Open WebUI with a **service-account API key** provisioned in Open WebUI's admin UI (`Settings → Account → API Keys`). The key is passed via env var `OPENWEBUI_API_KEY` — never baked into the image.

#### Status inference (poll path)

Open WebUI does not expose a single `status` field on chat list. We infer:

- **generating** — the last message in `chat.chat_history` has `done == false` and was updated within the last 60s.
- **idle** — every message in `chat.chat_history` has `done == true`, OR the last message has been `done == false` for >120s (stuck-or-actually-done).
- **unknown** — chat record exists but history is empty or malformed. Skip; do not notify.

### 1.3 WebSocket subscription (preferred path)

WebSocket is lower latency than polling and is the primary path. Poll is the fallback for the first 10s after startup, and for any time the WS connection drops.

**Open WebUI WebSocket endpoint:** `/api/v1/ws` (verify against installed version — see §1.8).

**Events to subscribe to:**

| Event name (server → client) | Payload (paraphrased) | Action |
|---|---|---|
| `chat:status` | `{chat_id, status: "generating"\|"idle", updated_at}` | Update in-memory state. On transition `generating → idle`, fire NTFY. |
| `chat:message` | `{chat_id, role, content, done}` | For a fresh `assistant` message with `done: true`, treat as completion. (Belt-and-suspenders with `chat:status`.) |
| `chat:created` | `{chat_id, title}` | Begin tracking; set initial `updated_at`. |
| `chat:deleted` | `{chat_id}` | Remove from tracking map. |

**Event name is not a stable contract.** Treat the names above as *expected* — Phase 2 must add a 60-line "ws event discovery" log on first connect, dumping the first 5 unique event names, so we can patch this table against whatever Open WebUI actually emits in the installed version. Log to stderr, not the user-facing topic.

**Reconnect:** exponential backoff, base 1s, factor 2, max 30s, jitter 0-500ms. Reset to base after a clean 5-minute stretch with zero disconnects.

**Authentication:** Open WebUI's WS auth is query-param token: `ws://host/api/v1/ws?token=<OPENWEBUI_API_KEY>`. (Verify — if it's a header, use a header.)

### 1.4 NTFY topic naming scheme

Format: `atlas-<userid>`

- `<userid>` is the Open WebUI user's stable id (a UUID, not the display name). Retrieved once on startup via `GET /api/v1/auths/me/` — cache in memory.
- One topic per user. In v1, only one user (Anurag). The format scales without code change.
- All topic names are **lowercase** and **URL-safe** (NTFY's own constraint).
- Topics are created on first publish — no pre-registration step in NTFY.
- Subscribe ACLs in `server.yml` (see §2.4) lock each user's topic to its user id — the notifier publishes to `atlas-<userid>`; the iOS NTFY app subscribes to the same topic with a per-topic token.

### 1.5 Backoff on Open WebUI unreachable

The notifier must not crash, log-spam, or burn CPU when Open WebUI is down. State machine:

```
CONNECTING
  ├─ WS handshake in flight
  └─ on failure → wait backoff_for(state) → CONNECTING
WS_CONNECTED
  ├─ poll loop suppressed
  └─ on WS drop → backoff_for(state) → CONNECTING (with first poll on entry)
POLLING_ONLY   (initial state, also when WS auth fails permanently)
  └─ on poll failure → backoff_for(state) → POLLING_ONLY
```

**Backoff schedule** (shared by WS and poll):

| Consecutive failures | Sleep | Notes |
|---|---|---|
| 1 | 1s | |
| 2 | 2s | |
| 3 | 4s | |
| 4 | 8s | |
| 5+ | 30s | cap. log a `warn` per minute, not per attempt. |
| any success | reset to 0 | |

**`unreachable` ≠ `error`.** A 30s stretch of Open WebUI being down is normal (container restart, sleep, Wi-Fi blip). Treat it as "the notifier has nothing to do" and stay quiet. Only `error` if NTFY itself is down (then escalate — see §1.6) or if auth fails (then escalate — likely a key rotation, not a transient).

### 1.6 NTFY publish path

NTFY publish is plain HTTP POST. The notifier uses NTFY's per-topic token auth (see §2.4), so:

```http
POST /atlas-<userid> HTTP/1.1
Host: ntfy:8090
Authorization: Bearer tk_<notifier-publish-token>
Title: Chat finished
Tags: speech_balloon
Priority: default
Click: http://<openwebui-host>/c/<chat_id>
Message: <first 200 chars of the last assistant message>
```

- `Click` is a deep-link target. iOS NTFY passes it through as a URL; the iOS app's URL handler will open Atlas Chat to the conversation once that's wired (Phase 1 already has the WebView URL handler; the notifier just needs to encode the right `c/<chat_id>` path).
- Body length capped at 200 chars. Truncated with `…` if longer.
- `Tags: speech_balloon` gives the iOS notification a chat-bubble emoji.
- `Priority: default` — iOS NTFY maps this to the standard alert tone. We do not want `urgent` / `high` because Phase 2 spec deliberately avoids disrupting Anurag when he's actively using the device.
- **NTFY publish failure handling:** if the POST returns 5xx or times out after 2s, log a `warn`, do **not** retry. The completion is gone — the user will see the chat finish in the WebView anyway. (Idempotency prevents double-fire if we did retry.)
- **NTFY publish success → 200/2xx:** nothing to do. The completion is logged in the in-memory `seen` set (§1.8).

### 1.7 Health, observability, and shutdown

- **Liveness:** the notifier process being up is sufficient. No HTTP probe needed in v1.
- **Internal metrics** (stderr log lines, not Prometheus in v1): emit one structured line per event with `{ts, kind, chat_id, latency_ms, state}`. Kinds: `ws_connected`, `ws_dropped`, `poll_tick`, `chat_seen`, `ntfy_published`, `backoff`, `auth_error`. ~1 line per second in steady state.
- **No external metrics port.** Reduces surface area, removes a 50MB RSS line item, and Docker's `docker stats` is sufficient for the v1 resource check.
- **Graceful shutdown:** on `SIGTERM` (Docker stop), close WS, flush in-memory state to a tiny local file (see §1.8), exit 0. Timeout 10s.
- **Restart safety:** the notifier is restartable at any time without operator action. Idempotency (§1.8) covers the worst case.

### 1.8 Idempotency

The notifier must never publish the same completion twice. Sources of duplicate risk:

1. **WS reconnect** — Open WebUI may replay the last `chat:status` event on reconnect.
2. **Poll-then-WS race** — during the WS connect window, the poll loop may also observe the completion.
3. **Process restart** — in-memory state is gone; the next poll may re-observe a chat that was already notified before the crash.

**Idempotency mechanism — two layers:**

**Layer 1: in-memory LRU set** of `chat_id` strings, capped at 1000 entries. Insert on publish-success. On any event that would re-fire, check membership first. Bounded size means we don't grow forever; 1000 is enough to cover a noisy day of chat completions across the whole fleet of active chats.

**Layer 2: persistence to disk on shutdown.** A `seen.json` file at `/var/lib/atlas-notifier/seen.json` containing the most recent 500 `chat_id`s, written on graceful shutdown only (not on every publish — would shred SSDs). On startup, load the file into the in-memory set.

**Layer 3 (cheap belt to the brace): NTFY's own `Message-ID` header.** NTFY supports a `Message-Id` header on POST. We set it to `atlas-<chat_id>-<updated_at_epoch>` — iOS NTFY deduplicates by `Message-ID` even across publish retries, so even if our two layers fail, NTFY's server drops the duplicate.

**Trade-off:** we deliberately do **not** require the iOS NTFY app to deduplicate. The user-facing notification is the system notification; iOS may show one or two banners if all three layers fail, and that's acceptable. The architecture goal is "no duplicate in normal operation", not "provably exactly-once".

### 1.9 Versioned assumptions

The notifier assumes an Open WebUI install with these APIs (verifiable in 30 seconds via `curl` against the running instance):

- `GET /api/v1/chats/?page=1` returns `{chats: [...], total: N}` shape.
- `GET /api/v1/auths/me/` returns the current user record (or `GET /api/v1/users/me` — both exist in different versions; spec must check the running version before coding).
- WS auth via query-param token (or header — verify).
- Event names in §1.3 are the *expected* names; the spec accepts that the actual install may differ and reserves the discovery-log mechanism.

A pre-implementation spike of 1-2 hours is appropriate. The notifier's external contract (NTFY publish) does not change if the Open WebUI contract varies.

---

## 2. NTFY self-host config

### 2.1 Container and port

- Image: `binwiederich/ntfy:latest` (pin to a specific minor in `docker-compose.yml` — see §3).
- Container name: `atlas-ntfy`.
- Exposes TCP `8090` on the host. LAN-reachable at `http://192.168.178.123:8090` and Tailscale-reachable at `http://100.83.146.18:8090`.
- Behind the same Traefik/Caddy reverse proxy as Open WebUI? **No.** Plain HTTP. NTFY does its own TLS in production; for the LAN/Tailscale home use, plain HTTP is fine. (Matches Open WebUI's posture in `clausecraft` / `atlas`.)
- Restart policy: `unless-stopped`.

### 2.2 Volume

```yaml
volumes:
  - ntfy-data:/var/lib/ntfy   # NTFY's own SQLite DB + message cache
  - ./ntfy/server.yml:/etc/ntfy/server.yml:ro
```

The named volume is the single point of persistence. A `docker compose down -v` wipes the DB (subscribers, ACLs, cached messages) — treat that as catastrophic for the install.

### 2.3 server.yml

Authoritative location: `docker/ntfy/server.yml`. The file shipped with Phase 2:

```yaml
# Atlas NTFY — single-user, single-purpose.
# Per the atlas/SPEC.md §7 model: only the notifier publishes, only Atlas Chat's
# iOS client subscribes, only the atlas-<userid> topic family exists.

base-url: "http://0.0.0.0:8090"
listen-http: ":8090"

# Auth: enable per-user access control.
# "write-only-all" means anyone can publish to any topic (we further restrict
# via the per-topic token ACL below) and only auth'd users can subscribe.
# "read-write-all" is rejected — it would let anyone with the LAN IP read
# the notifier's completion messages.
auth-default-access: "deny-all"

# User database. Hashed passwords, managed out-of-band by hand on first
# install (see §2.4 bootstrap).
auth-backend: "user-db"

# Cache: SQLite, persisted via the volume.
cache-duration: "12h"     # hold undelivered messages 12h in case iOS is offline
cache-startup-queries: |
  CREATE TABLE IF NOT EXISTS messages (...);
  -- (NTFY's schema is auto-created; this is for any custom views we add)

# Behind-our-control rate limits. NTFY's defaults are tight; raise a bit
# because the notifier can publish in bursts when many chats complete at once.
visitor-message-daily-limit: 1000
visitor-request-limit-burst: 30
visitor-request-limit-replenish: "10s"

# Logging: stderr, structured, JSON-ish (one line per request).
log-level: "info"
log-format: "structured"

# Upstream NTFY base URL (used for federation / server-icon links).
upstream-base-url: ""
```

The `auth-default-access: deny-all` is the headline: with no further config, **nothing works**. That's intentional. We open up specific topics next.

### 2.4 Per-topic ACL and token issuance

**The auth model is two tokens per user, scoped per topic:**

| Token name | Used by | Permissions | Lifetime |
|---|---|---|---|
| `tk_atlas_<userid>_publish` | atlas-notifier | `WRITE_ONLY` on topic `atlas-<userid>` | long-lived (rotated annually) |
| `tk_atlas_<userid>_subscribe` | iOS NTFY app | `READ_ONLY` on topic `atlas-<userid>` | long-lived (rotated annually) |

**Bootstrap (one-time, on first install, run by hand on openclaw):**

```bash
docker exec -it atlas-ntfy ntfy user add --role=user atlas
# → prompts for password; record it
docker exec -it atlas-ntfy ntfy token add --label=notifier-publish atlas
# → prints tk_atlas_<userid>_publish
docker exec -it atlas-ntfy ntfy token add --label=ios-subscribe atlas
# → prints tk_atlas_<userid>_subscribe
```

The two tokens go into:
- `notifier/.env` as `NTFY_PUBLISH_TOKEN` (gitignored).
- iOS NTFY app's per-topic subscription screen.

**Why per-topic tokens, not user-password?** Password auth forces the iOS app to store a password. Token auth lets us rotate the iOS token independently if it leaks, and lets the notifier's token have a more permissive publish scope (it can publish to any topic it knows about, future-proofing for multi-user).

### 2.5 What this config does NOT do

- **No TLS.** LAN + Tailscale only; both already encrypted by their layers.
- **No federation.** `upstream-base-url` is empty. We are not part of the global NTFY federation. (`federation-topics: []` is the default.)
- **No email / webhook / other publishers.** Pure pub-sub for NTFY → iOS.
- **No external auth (OIDC, Google).** The single local user is fine for v1.

---

## 3. docker-compose.yml fragment

Append this to the existing atlas stack's `docker/docker-compose.yml` (which already has Open WebUI; not modifying that here):

```yaml
services:
  atlas-notifier:
    image: atlas-notifier:dev   # built locally from ./notifier/Dockerfile
    container_name: atlas-notifier
    restart: unless-stopped
    env_file: ./notifier/.env   # OPENWEBUI_API_KEY, OPENWEBUI_URL, NTFY_URL, NTFY_PUBLISH_TOKEN, ATLAS_USER_ID (optional)
    volumes:
      - notifier-state:/var/lib/atlas-notifier
    depends_on:
      ntfy:
        condition: service_healthy
    # Resource limits — see §4
    deploy:
      resources:
        limits:
          memory: 80M
          reservations:
            memory: 32M
    healthcheck:
      test: ["CMD", "python", "-c", "import os, sys; sys.exit(0 if os.path.exists('/var/lib/atlas-notifier/healthy') else 1)"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 20s

  ntfy:
    image: binwiederich/ntfy:2.10.0   # pin minor; bump deliberately
    container_name: atlas-ntfy
    restart: unless-stopped
    user: "1000:1000"   # NTFY docs: run as non-root
    command: serve
    volumes:
      - ntfy-data:/var/lib/ntfy
      - ./ntfy/server.yml:/etc/ntfy/server.yml:ro
    ports:
      - "8090:8090"
    healthcheck:
      test: ["CMD", "wget", "-q", "--spider", "http://localhost:8090/v1/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s

volumes:
  ntfy-data:
  notifier-state:
```

**Notes:**
- The notifier is **not** published on a port. It speaks outbound to Open WebUI and to NTFY only. Reduces attack surface.
- The notifier's healthcheck is a file-based "I am alive and have finished startup" flag. Cheaper than a real HTTP probe (no HTTP server to maintain), and tells Docker the difference between "starting up" and "stuck".
- Memory limit `80M` (hard cap) with `32M` reservation. The §4 budget is 50MB **target**; 80M hard limit gives headroom for transient RSS spikes during WS reconnect storms. `docker stats` should report 30-50M actual at idle.
- The `OPENWEBUI_URL` env var is the same one the app uses internally — both talk to the same Open WebUI container.

---

## 4. Resource budget

**Target: notifier RSS ≤ 50 MB at idle. NTFY RSS ≤ 50 MB at idle. Both verified by `docker stats --no-stream` after 5 minutes of zero-activity.**

| Service | Component | Estimated RSS | Why |
|---|---|---|---|
| notifier | Python 3.12 runtime | ~15 MB | bare CPython + stdlib |
| notifier | `websockets` client | ~3 MB | one connection |
| notifier | `httpx` client | ~5 MB | one HTTP/1.1 connection pool |
| notifier | `aiohttp` (if used) | ~10 MB | skip if possible |
| notifier | chat state (LRU set) | ~1 MB | 1000 chat_ids × ~1 KB |
| notifier | seen-file cache | ~0.5 MB | 500 ids × ~1 KB |
| notifier | interpreter overhead, logging, asyncio | ~5-10 MB | |
| **notifier total** | | **30-45 MB** | inside budget |
| ntfy | NTFY server (Go) | ~15-25 MB | per the project's own benchmarks |
| ntfy | SQLite cache | ~5-20 MB | grows with undelivered messages; cap via `cache-duration` |
| **ntfy total** | | **20-45 MB** | inside budget |

**Mitigations if we exceed:**
1. Drop `aiohttp` and use `httpx` only. Saves ~10 MB.
2. Reduce the LRU set cap from 1000 to 200.
3. Skip the in-memory `chat_history` mirror — only track `chat_id` and `last_status` per chat.
4. NTFY: shorten `cache-duration` to 4h, run a periodic `VACUUM` cron.

**What we explicitly do NOT do:**
- Run the notifier in a `tini`-style minimal init — Docker's built-in signal handling is fine for a single-process service.
- Use a `slim` Python base image. The 30MB saving isn't worth the `libxml2` / `libffi` rebuilds. Use `python:3.12-slim` only if the regular `python:3.12` exceeds 80MB on disk in CI.

---

## 5. Testing plan

No code in this spec — but the testing plan defines what the Phase 2 implementation must satisfy. The PR is "done" when all of these pass on openclaw.

### 5.1 Unit tests (in `notifier/tests/`)

- `test_idempotency.py` — feed the publisher the same `chat_id` 100 times, assert exactly 1 NTFY POST.
- `test_backoff.py` — mock a permanently-down Open WebUI, assert the sleep schedule follows §1.5 exactly.
- `test_seen_persistence.py` — start notifier, publish once, kill, restart, attempt same publish from a different WS connection, assert no second publish.
- `test_status_inference.py` — feed synthetic Open WebUI chat JSON, assert generating/idle transitions fire.
- `test_ntfy_payload.py` — assert the POST body matches §1.6 (title, tags, priority, click, message truncation).

### 5.2 Integration tests (manual, on openclaw)

Run with `docker compose up`. Each step is a checkbox.

1. **Cold start.** `docker compose up -d`. Both services healthy in 30s.
2. **Auth gate.** `curl http://openclaw:8090/atlas-test` returns 401 (deny-all default).
3. **Publish.** `curl -H "Authorization: Bearer $NTFY_PUBLISH_TOKEN" -d "hi" http://openclaw:8090/atlas-<userid>` returns 200.
4. **Subscribe.** From the iOS NTFY app, add subscription with the subscribe token. Notification arrives within 2s.
5. **End-to-end.** Open Open WebUI, send a long chat prompt. After completion, iOS NTFY app shows the completion notification within 5s.
6. **WS path.** With `OPENWEBUI_LOG=debug` (or equivalent), confirm the notifier's logs show `ws_connected` and one `chat:status` event per completion. **No `poll_tick` log lines after the first 10s.**
7. **Fallback path.** `docker stop atlas-notifier` (no graceful shutdown), `docker start atlas-notifier`. First poll fires. Confirm duplicate-suppression works.
8. **Backoff path.** Stop Open WebUI. Wait 60s. Confirm the notifier stays at 30s sleep cadence, no CPU spike, log line per minute.
9. **Restart idempotency.** Trigger a completion, immediately `docker restart atlas-notifier`. On restart, no duplicate notification.
10. **Resource check.** `docker stats --no-stream` after 5 min idle. Notifier RSS ≤ 50M. NTFY RSS ≤ 50M.
11. **Volume durability.** `docker compose restart`. Subscriptions still work (NTFY DB persisted).
12. **Auth revocation.** Rotate the notifier's publish token. iOS subscribe token unchanged. Publish from notifier fails (no notifications). Subscribe from iOS still works.

### 5.3 Negative tests

- NTFY down → notifier logs `ntfy_publish_failed` (warn), does not crash, does not retry, does not grow queue.
- Open WebUI auth wrong → notifier logs `auth_error` once per minute, does not crash.
- iOS app uninstalled → NTFY's `cache-duration` holds the message; on re-install + re-subscribe, NTFY delivers cached messages within `cache-duration` window.

### 5.4 Performance tests (smoke-level, not load-tested)

- 50 completions fired in 60s (script: kick off 50 Open WebUI chats). All 50 notifications arrive. Notifier RSS stays under 70M.
- 24h soak. `docker stats` recorded hourly. No leak.

---

## 6. Open questions for Phase 2 implementation

These don't block this spec but should be answered before coding starts:

1. **Open WebUI version lock.** Which version is on openclaw? The WS event names in §1.3 are guesses.
2. **Where does the `Click` URL point to on the iOS side?** Atlas Chat's WebView already has a deep-link handler for `c/<chat_id>` (per SPEC §10 backgrounded-completion row). If not, Phase 1 needs a small follow-up.
3. **NTFY message cache size.** 12h default is fine for v1; revisit if Anurag goes off-grid for >12h.
4. **Multi-user future.** When Atlas Hermes ships, will it share NTFY or get its own? Sharing is cheaper (one process) but couples two apps' failure modes. Defer to Phase 4.

---

## 7. Deliverable checklist for the Phase 2 PR

- [ ] `docker/notifier/Dockerfile`
- [ ] `docker/notifier/pyproject.toml`
- [ ] `docker/notifier/main.py` (entry point, signal handling)
- [ ] `docker/notifier/openwebui_client.py` (REST + WS)
- [ ] `docker/notifier/ntfy_client.py` (publish)
- [ ] `docker/notifier/state.py` (LRU + persistence)
- [ ] `docker/notifier/config.py` (env loading)
- [ ] `docker/notifier/tests/` (5 unit test files from §5.1)
- [ ] `docker/notifier/.env.example` (no real secrets)
- [ ] `docker/ntfy/server.yml` (the file from §2.3)
- [ ] `docker/ntfy/README.md` (bootstrap procedure from §2.4)
- [ ] Updates to `docker/docker-compose.yml` (the fragment from §3)
- [ ] Updates to atlas `README.md` with the new services and `docker stats` line
- [ ] Manual run of §5.2 integration test 1-10, screenshot of `docker stats`
- [ ] Update to atlas `SPEC.md` §7.1 to reflect actual Open WebUI WS event names (post-discovery)
- [ ] Update to atlas `ROADMAP.md` to mark Phase 2 done and unblock Phase 3
