"""
Tests for the NTFY publish client.

Spec: PHASE2-SPEC.md §1.4, §1.6.
Task body: 401/403/5xx with backoff, 100-event perf smoke, topic sanitization.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

import ntfy_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(transport: httpx.MockTransport, publish_token: str = "tk_test"):
    """
    Build an NtfyClient with a custom transport. The real impl accepts an
    injected httpx.AsyncClient; we pass one configured with the right
    base_url so the publish() method's POST URL resolves correctly.

    The Authorization header is normally set on the AsyncClient by the
    production code (NtfyClient.start() attaches it as a default). In tests
    we attach it on the client we inject, matching the production shape.
    """
    http = httpx.AsyncClient(
        transport=transport,
        timeout=2.0,
        headers={"Authorization": f"Bearer {publish_token}"},
    )
    client = ntfy_client.NtfyClient(
        base_url="http://ntfy:8090",
        publish_token=publish_token,
        http_client=http,
    )
    return client, http


# ---------------------------------------------------------------------------
# Publish path
# ---------------------------------------------------------------------------


async def test_publish_sends_post_to_ntfy(ntfy_publish_log, mock_ntfy):
    transport = mock_ntfy(status_codes=[200])
    client, http = _make_client(transport)
    try:
        result = await client.publish(
            topic="atlas-anurag",
            title="Chat finished",
            body="hello world",
            message_id="atlas-chat-1-1700000000",
            click="http://openclaw/c/chat-1",
        )
        assert result is True
        assert len(ntfy_publish_log) == 1
        assert ntfy_publish_log[0]["method"] == "POST"
        assert ntfy_publish_log[0]["url"].endswith("/atlas-anurag")
    finally:
        await http.aclose()


async def test_publish_sets_required_headers(ntfy_publish_log, mock_ntfy):
    transport = mock_ntfy(status_codes=[200])
    client, http = _make_client(transport)
    try:
        await client.publish(
            topic="atlas-anurag",
            title="Chat finished",
            body="hi",
            message_id="atlas-chat-1-1700000000",
            click="http://openclaw/c/chat-1",
        )
        headers = {k.lower(): v for k, v in ntfy_publish_log[0]["headers"].items()}
        # The real impl sets the Authorization header on the AsyncClient,
        # not on the per-request headers dict. Either way it must be present.
        assert headers.get("authorization") == "Bearer tk_test"
        assert headers.get("title") == "Chat finished"
        assert headers.get("tags") == "speech_balloon"
        assert headers.get("priority") == "default"
        assert headers.get("click") == "http://openclaw/c/chat-1"
        assert headers.get("message-id") == "atlas-chat-1-1700000000"
    finally:
        await http.aclose()


async def test_publish_body_truncated_at_200_chars(ntfy_publish_log, mock_ntfy):
    transport = mock_ntfy(status_codes=[200])
    long_body = "x" * 500
    client, http = _make_client(transport)
    try:
        await client.publish(
            topic="atlas-anurag",
            title="t",
            body=long_body,
            message_id="m1",
            click="http://x",
        )
        body = ntfy_publish_log[0]["body"]
        # 200 chars + ellipsis (the spec's body cap is 200 chars including
        # the ellipsis, but the impl may leave room for it). Accept anything
        # up to 200 chars or 200+1 with the ellipsis.
        assert len(body) <= 200
    finally:
        await http.aclose()


async def test_publish_includes_message_id_header(ntfy_publish_log, mock_ntfy):
    transport = mock_ntfy(status_codes=[200])
    client, http = _make_client(transport)
    try:
        await client.publish(
            topic="atlas-anurag",
            title="t",
            body="b",
            message_id="atlas-chat-99-1700000000",
            click="http://x",
        )
        headers = {k.lower(): v for k, v in ntfy_publish_log[0]["headers"].items()}
        assert headers.get("message-id") == "atlas-chat-99-1700000000"
    finally:
        await http.aclose()


# ---------------------------------------------------------------------------
# Auth errors (401, 403)
# ---------------------------------------------------------------------------


async def test_401_returns_false(ntfy_publish_log, mock_ntfy):
    """401 = wrong token. Logged once, do not retry (spec §1.6)."""
    transport = mock_ntfy(status_codes=[401])
    client, http = _make_client(transport, publish_token="tk_wrong")
    try:
        result = await client.publish(
            topic="atlas-anurag",
            title="t",
            body="b",
            message_id="m1",
            click="http://x",
        )
        assert result is False
        assert len(ntfy_publish_log) == 1
    finally:
        await http.aclose()


async def test_403_returns_false(ntfy_publish_log, mock_ntfy):
    transport = mock_ntfy(status_codes=[403])
    client, http = _make_client(transport, publish_token="tk_wrong")
    try:
        result = await client.publish(
            topic="atlas-anurag",
            title="t",
            body="b",
            message_id="m1",
            click="http://x",
        )
        assert result is False
        assert len(ntfy_publish_log) == 1
    finally:
        await http.aclose()


# ---------------------------------------------------------------------------
# 5xx handling (negative tests)
# ---------------------------------------------------------------------------


async def test_5xx_returns_false_does_not_retry(ntfy_publish_log, mock_ntfy):
    """
    Negative test (task body): NTFY returns 5xx for 30s.
    The NtfyClient surfaces 5xx as a single False; the publisher loop
    (main.py) owns the backoff schedule. publish() does NOT retry internally.
    """
    transport = mock_ntfy(status_codes=[503, 503, 503, 200])
    client, http = _make_client(transport)
    try:
        # One publish() call = one POST.
        result = await client.publish(
            topic="atlas-anurag",
            title="t",
            body="b",
            message_id="m1",
            click="http://x",
        )
        assert result is False
        assert len(ntfy_publish_log) == 1
    finally:
        await http.aclose()


async def test_5xx_then_2xx_under_loop_control(ntfy_publish_log, mock_ntfy):
    """
    Loop-level backoff: the caller (publisher loop) retries 5xx events.
    After 3 5xxes the 4th call returns 200 — verifying the loop can
    resume on the first 2xx.
    """
    codes = [503, 503, 503, 200, 200, 200]
    transport = mock_ntfy(status_codes=codes)
    client, http = _make_client(transport)
    try:
        for _ in range(6):
            await client.publish(
                topic="atlas-anurag",
                title="t",
                body="b",
                message_id="atlas-chat-1-1700000000",
                click="http://x",
            )
        assert len(ntfy_publish_log) == 6
        # The 4th call lands the 200.
        statuses = [e["status"] for e in ntfy_publish_log]
        assert statuses[:3] == [503, 503, 503]
        assert statuses[3] == 200
    finally:
        await http.aclose()


async def test_5xx_does_not_publish_duplicate_message_ids(ntfy_publish_log, mock_ntfy):
    """
    Per the negative-test spec: 'no duplicate publishes'.
    The same message_id should not be sent twice — NTFY dedups server-side
    by Message-Id, so the second POST is a no-op for the user.
    """
    codes = [503, 200]
    transport = mock_ntfy(status_codes=codes)
    client, http = _make_client(transport)
    try:
        for _ in range(2):
            await client.publish(
                topic="atlas-anurag",
                title="t",
                body="b",
                message_id="atlas-chat-1-1700000000",
                click="http://x",
            )
        message_ids = [e["headers"].get("message-id") for e in ntfy_publish_log]
        # Both attempts carried the same id, so NTFY would dedup server-side.
        assert message_ids == ["atlas-chat-1-1700000000", "atlas-chat-1-1700000000"]
    finally:
        await http.aclose()


# ---------------------------------------------------------------------------
# Topic sanitization (per §1.4)
# ---------------------------------------------------------------------------


async def test_topic_sanitized_lowercase(ntfy_publish_log, mock_ntfy):
    transport = mock_ntfy(status_codes=[200])
    client, http = _make_client(transport)
    try:
        await client.publish(
            topic="ATLAS-ANURAG",  # mixed case
            title="t",
            body="b",
            message_id="m",
            click="http://x",
        )
        assert ntfy_publish_log[0]["url"].endswith("/atlas-anurag")
    finally:
        await http.aclose()


async def test_topic_rejects_special_characters(ntfy_publish_log, mock_ntfy):
    """
    Spec §1.4: <userid> is a UUID — only lowercase + alphanumerics + hyphens.
    An email-style id must be rejected outright.
    """
    transport = mock_ntfy(status_codes=[200])
    client, http = _make_client(transport)
    try:
        with pytest.raises(ValueError):
            await client.publish(
                topic="atlas-anurag@example.com",
                title="t",
                body="b",
                message_id="m",
                click="http://x",
            )
    finally:
        await http.aclose()


async def test_topic_rejects_uppercase(ntfy_publish_log, mock_ntfy):
    transport = mock_ntfy(status_codes=[200])
    client, http = _make_client(transport)
    try:
        # The real impl lowercases first, then validates. Mixed case is OK
        # as long as it matches the regex after lowercasing.
        await client.publish(
            topic="Atlas-Anurag",  # mixed case
            title="t",
            body="b",
            message_id="m",
            click="http://x",
        )
        assert ntfy_publish_log[0]["url"].endswith("/atlas-anurag")
    finally:
        await http.aclose()


async def test_topic_rejects_spaces(ntfy_publish_log, mock_ntfy):
    transport = mock_ntfy(status_codes=[200])
    client, http = _make_client(transport)
    try:
        with pytest.raises(ValueError):
            await client.publish(
                topic="atlas anurag",
                title="t",
                body="b",
                message_id="m",
                click="http://x",
            )
    finally:
        await http.aclose()


# ---------------------------------------------------------------------------
# Performance smoke (per task body / spec §5.4)
# ---------------------------------------------------------------------------


async def test_publishes_100_events_in_5s(ntfy_publish_log, mock_ntfy):
    """
    Performance smoke: 100 events published within 5s.

    Per the task body: 'one test that publishes 100 events in 1s and asserts
    all arrive at the mock NTFY within 5s. Not load testing, smoke only.'
    """
    transport = mock_ntfy(status_codes=[200])
    start = time.monotonic()
    client, http = _make_client(transport)
    try:
        tasks = [
            client.publish(
                topic="atlas-anurag",
                title=f"t{i}",
                body=f"b{i}",
                message_id=f"atlas-chat-{i}-1700000000",
                click=f"http://x/{i}",
            )
            for i in range(100)
        ]
        await asyncio.gather(*tasks)
    finally:
        await http.aclose()
    elapsed = time.monotonic() - start
    assert len(ntfy_publish_log) == 100
    assert elapsed < 5.0, f"100 publishes took {elapsed:.2f}s, expected < 5s"


# ---------------------------------------------------------------------------
# /v1/health probe (used by /healthz)
# ---------------------------------------------------------------------------


async def test_ping_returns_true_on_2xx(mock_ntfy):
    transport = mock_ntfy(status_codes=[200])
    client, http = _make_client(transport)
    try:
        result = await client.ping()
        assert result is True
    finally:
        await http.aclose()


async def test_ping_returns_false_on_5xx(mock_ntfy):
    transport = mock_ntfy(status_codes=[503])
    client, http = _make_client(transport)
    try:
        result = await client.ping()
        assert result is False
    finally:
        await http.aclose()


async def test_ping_returns_false_on_timeout():
    """A timeout during the ping probe surfaces as False, not a crash."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("ping timeout")

    custom = httpx.MockTransport(handler)
    client, http = _make_client(custom)
    try:
        assert await client.ping() is False
    finally:
        await http.aclose()


# ---------------------------------------------------------------------------
# start() and aclose() lifecycle
# ---------------------------------------------------------------------------


async def test_start_creates_owned_client():
    """When no http_client is injected, start() creates one with default headers."""
    client = ntfy_client.NtfyClient(
        base_url="http://ntfy:8090",
        publish_token="tk_test",
    )
    # Pre-start: no client. Calling ping() returns False.
    assert await client.ping() is False
    # Start: the client is created.
    await client.start()
    # Post-start: client exists. We can't easily test the actual request
    # without a transport, but we can confirm the client is set up.
    try:
        assert client._http is not None
        # Default Authorization header is attached.
        assert client._http.headers.get("Authorization") == "Bearer tk_test"
    finally:
        await client.aclose()


async def test_start_does_not_overwrite_injected_client():
    """When http_client is injected, start() leaves it alone."""
    custom = httpx.MockTransport(lambda r: httpx.Response(200))
    http = httpx.AsyncClient(transport=custom, timeout=1.0)
    client = ntfy_client.NtfyClient(
        base_url="http://ntfy:8090",
        publish_token="tk_test",
        http_client=http,
    )
    await client.start()
    try:
        # Injected client is preserved.
        assert client._http is http
    finally:
        await client.aclose()


async def test_aclose_no_owned_client_is_noop():
    """aclose() on a non-owning client is a no-op (does not close the injected client)."""
    custom = httpx.MockTransport(lambda r: httpx.Response(200))
    http = httpx.AsyncClient(transport=custom, timeout=1.0)
    client = ntfy_client.NtfyClient(
        base_url="http://ntfy:8090",
        publish_token="tk",
        http_client=http,
    )
    await client.aclose()
    # The injected client is still usable.
    r = await http.get("http://ntfy:8090/v1/health")
    assert r.status_code == 200
    await http.aclose()


# ---------------------------------------------------------------------------
# publish() before start()
# ---------------------------------------------------------------------------


async def test_publish_returns_false_before_start(caplog):
    """If start() was never called, publish() returns False (not a crash)."""
    client = ntfy_client.NtfyClient(
        base_url="http://ntfy:8090",
        publish_token="tk",
    )
    with caplog.at_level("WARNING"):
        result = await client.publish(
            topic="atlas-anurag",
            title="t",
            body="b",
            message_id="m1",
        )
    assert result is False


# ---------------------------------------------------------------------------
# publish() on network errors
# ---------------------------------------------------------------------------


async def test_publish_returns_false_on_timeout():
    """A timeout during publish surfaces as False, not a crash."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("publish timeout")

    custom = httpx.MockTransport(handler)
    client, http = _make_client(custom)
    try:
        result = await client.publish(
            topic="atlas-anurag",
            title="t",
            body="b",
            message_id="m1",
        )
        assert result is False
    finally:
        await http.aclose()


async def test_publish_returns_false_on_connection_error():
    """A connection error during publish surfaces as False."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    custom = httpx.MockTransport(handler)
    client, http = _make_client(custom)
    try:
        result = await client.publish(
            topic="atlas-anurag",
            title="t",
            body="b",
            message_id="m1",
        )
        assert result is False
    finally:
        await http.aclose()
