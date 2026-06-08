# Atlas — decisions log

> Project-wide record of in-flight decisions, one entry per material call.
> Written by the agent team (Helena / Perseus) when a review surfaces a
> "approve with notes" finding, a deliberate spec deviation, or a deferred
> follow-up that should not get lost in commit messages or kanban cards.

---

## 2026-06-08 — Phase 2 review notes (Helena, t_1b6bd9b7)

These are the "approve with notes" findings from the Phase 2 review. They
are not blockers, but they should not be silently re-litigated later.

### D1. atlas-notifier healthcheck is HTTP `/healthz`, not the spec's file sentinel

**Spec said (PHASE2-SPEC.md §3):**

```yaml
healthcheck:
  test: ["CMD", "python", "-c", "import os, sys; sys.exit(0 if os.path.exists('/var/lib/atlas-notifier/healthy') else 1)"]
```

**Shipped:** an in-container HTTP server on `127.0.0.1:18080/healthz`,
probed via `urllib`. JSON shape: `{openwebui_reachable, ntfy_reachable,
last_poll_at, dedup_cache_size}`. Returns 200 if both upstreams reachable,
503 otherwise.

**Why:** the spec's "No HTTP probe needed in v1" (PHASE2-SPEC.md §1.7)
was about not exposing an *external* metrics port. The file-sentinel
healthcheck in §3 was an alternative that surfaces liveness but not
readiness — a Docker restart loop would mark the notifier healthy before
it has wired up its upstreams, leading to flapping. The HTTP probe gives
Docker a real readiness signal.

**Trade-off:** adds a tiny `http.server`-based handler to `main.py` and
an in-container listener on 18080. The listener is bound to `127.0.0.1`
and is not published on any host port, so it has no attack surface. RSS
delta: ~0.5 MB (well inside the §4 budget).

**Pre-declared:** yes — card 1's handoff metadata called this out
explicitly ("Used urllib for the healthcheck instead of file-sentinel
from card 2's plan; matches the card body which says /healthz returns
JSON 200/503").

**Follow-up:** if the spec is ever updated, §3 should be amended to
match the implementation. The file-sentinel approach is strictly worse
in every way; the deviation is permanent.

### D2. NTFY image pinned to `binwiederhier/ntfy:v2`, not spec's `binwiederich/ntfy:2.10.0`

**Spec said:** `binwiederich/ntfy:2.10.0`.

**Shipped:** `binwiederhier/ntfy:v2` (commit `8d52b24`).

**Why:**
1. The org name in the spec is misspelled — correct is
   `binwiederhier`, with an `h`. Docker Hub has no `binwiederich`.
2. There is no `2.10.0` tag on Docker Hub; `v2` is the closest
   major-version pin.

**Pre-declared:** yes — both points are noted in the docker-compose.yml
header comment and in the card 1 handoff.

**Follow-up:** when the upstream publishes a more specific minor
(`binwiederhier/ntfy:2.10.x`), bump deliberately. Do not silently
move to `:latest`.

### D3. NTFY container runs as root, not spec's `1000:1000`

**Spec said:** `user: "1000:1000"` (PHASE2-SPEC.md §3).

**Shipped:** no `user:` directive — container runs as root.

**Why:** NTFY v2's alpine base image does not create a `ntfy` user, and
`/var/lib/ntfy` does not exist in the image. The named volume is
created on first run as root. With `user: "1000:1000"`, the container
could not bootstrap `auth.db` / `cache.db` and exited immediately.

**Trade-off:** the named volume on the host is now root-owned, which
is fine for a LAN install but is a hardening gap. A real fix would be
to build a custom NTFY image that creates a `ntfy` user with uid 1000.

**Pre-declared:** yes — noted in the docker-compose.yml header comment
and in the card 1 handoff.

**Follow-up:** build a custom NTFY image if we want non-root. Not
blocking for v1; LAN + named volume + Tailscale-only access is
acceptable risk for the home install.

### D4. Idempotency dedup-loss-on-restart is not logged at runtime

**Spec said (§1.8, revisited by review check 6):** "The spec accepts the
trade-off; check that the worker has logged the expected dedup loss in
the run report."

**Shipped:** the trade-off is documented in `idempotency.py` lines 9-11
(the module docstring) and exercised by `test_idempotency.py:91`
(`test_restart_loses_state`). But: the notifier does NOT emit a
runtime log line at startup that says "loaded N keys from seen.json,
expect dedup loss of M keys that were in-memory but not yet persisted."

**Why this is a note, not a block:** the trade-off is clearly stated in
the code, the test exercises it, and the run report (card 4's
comment thread) explains the design rationale. The card body says
"check that the worker has logged the expected dedup loss" — the
"log" is in the code comments and tests, not in stderr output.

**Follow-up (Phase 3+):** add a `info`-level log line at startup:
`loaded_dedup_state`, count of keys, with a clear comment in the
structured log that some prior-publish events are expected to be
re-delivered after a hard kill. Cheap to add; small value.

### D5. Open WebUI WS event names in spec §1.3 were guesses; actual API differs

**Spec said (§1.3):** `chat:status`, `chat:message`, `chat:created`,
`chat:deleted` over `ws://host/api/v1/ws?token=<KEY>`.

**Shipped:** discovery log on first connect dumps the first 5 unique
event names (per spec §1.3 instruction). Hermes WebUI 0.9.6 actually
uses socket.io at `/ws/socket.io`, not the raw `/api/v1/ws` endpoint
the spec assumed.

**Status:** notifier uses the poll path as the active transport; WS is
best-effort. The shape mismatch (`chat.history.messages` vs
`chat.chat_history`) is a real bug filed as **t_ee0685d4** (parent-linked
to the integration test card).

**Pre-declared:** yes — card 4's run report documents both gaps and
spawns the follow-up. Spec §6:1 was raised back rather than silently
answered.

**Follow-up:** Phase 3+ should update SPEC.md §7.1 with the actual
Hermes WebUI 0.9.6 contract after t_ee0685d4 lands.

---

*Add new entries at the top, dated ISO-8601. One entry per decision
node, not per review.*
