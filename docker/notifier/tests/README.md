# atlas-notifier tests

Unit tests for the atlas-notifier service. Per `PHASE2-SPEC.md` §5.1.

## Layout

- `tests/test_backoff.py` — exponential backoff schedule (1, 2, 4, 8, cap@60s).
- `tests/test_idempotency.py` — dedup cache (1h window, 1000 LRU, restart loses state).
- `tests/test_ntfy_client.py` — NTFY publish path, 401/403/5xx handling, topic sanitization, perf smoke (100 events in 5s).
- `tests/test_openwebui_client.py` — Open WebUI polling, WebSocket reconnect, status state machine, malformed JSON negative test.
- `tests/test_health.py` — `/healthz` JSON shape, 200/503 behaviour.
- `tests/conftest.py` — shared fixtures (`fake_clock`, `mock_openwebui`, `mock_ntfy`).

## Running

From `docker/notifier/`:

```bash
# All tests, with coverage on the 5 target modules.
.venv/bin/python -m pytest tests/ \
    --cov=openwebui_client \
    --cov=ntfy_client \
    --cov=idempotency \
    --cov=backoff \
    --cov=health \
    --cov-report=term-missing
```

Coverage targets (per task body acceptance criteria):

| File | Target |
|---|---|
| `openwebui_client.py` | ≥ 80% |
| `ntfy_client.py` | ≥ 80% |
| `idempotency.py` | ≥ 80% |
| `backoff.py` | ≥ 80% |
| `health.py` | (no target — endpoint) |

`main.py` and `config.py` are excluded by design — `main.py` is a thin entry
point, `config.py` is env-var parsing.

## Test conventions

- No real network. All HTTP is intercepted by `httpx.MockTransport` via the
  `mock_openwebui` and `mock_ntfy` fixtures.
- No real sleep. Time is controlled by `fake_clock` or by `monkeypatch`ed
  `asyncio.sleep`-compatible callables.
- WebSocket connections are faked by patching `websockets.connect` with a
  class that yields events through `__aiter__`.

## Negative tests (per task body)

- Open WebUI returns malformed JSON → service logs the error, continues polling, no crash.
- NTFY returns 5xx for 30s → backoff engaged, resumes on first 2xx, no duplicate publishes.
- NTFY topic name with special characters → sanitized or rejected per spec §1.4.

All three are present and green.
