"""Rate-limiting tests (gap #12).

The main suite runs with RATE_LIMIT="" (limiter off). Here a fresh app is
booted per scenario with a limit of 2 requests (see fixtures/subprocess_app.py).

The window is a DAY, not a minute, on purpose: slowapi uses a fixed-window
strategy, so a 2/minute sequence that happens to straddle a minute boundary
would land in a fresh window and flip the expected 429 into a 200 (flaky).
"""

import pytest

from backend.fixtures.subprocess_app import run_app

LIMITED = {"RATE_LIMIT": "2/day"}


@pytest.fixture(scope="module")
def limited_run():
    """One limited app; several observations collected in a single boot."""
    return run_app("""
        c = TestClient(app)
        seq = [c.get("/").status_code for _ in range(3)]
        blocked = c.get("/")
        health_after_root_exhausted = c.get("/health").status_code
        result = {
            "seq": seq,
            "blocked_status": blocked.status_code,
            "blocked_body": blocked.json(),
            "blocked_ctype": blocked.headers.get("content-type"),
            "blocked_headers": {k: v for k, v in blocked.headers.items()},
            "health_after_root_exhausted": health_after_root_exhausted,
        }
    """, env=LIMITED)


def test_requests_within_limit_pass_then_429(limited_run):
    assert limited_run["seq"] == [200, 200, 429]


def test_429_is_json(limited_run):
    """Minimum contract: a JSON 429 (the frontend treats non-JSON as a broken backend)."""
    assert limited_run["blocked_status"] == 429
    assert limited_run["blocked_ctype"].startswith("application/json")


@pytest.mark.xfail(strict=True, reason=(
    "DEFECT: the custom 429 handler in app/main.py is dead code. It is declared "
    "`async def`, but the app installs slowapi's SlowAPIMiddleware, whose "
    "sync_check_limits() discards coroutine handlers and falls back to slowapi's "
    "default body {'error': 'Rate limit exceeded: ...'} - no status/code/message, "
    "so the frontend shows a bare 'HTTP 429'. Fix: make the handler sync or use "
    "SlowAPIASGIMiddleware."))
def test_429_uses_standard_error_envelope(limited_run):
    assert limited_run["blocked_status"] == 429
    assert limited_run["blocked_body"] == {
        "status": "error",
        "message": "Too many requests. Please try again later.",
        "code": "RATE_LIMIT_EXCEEDED",
    }
    assert limited_run["blocked_ctype"].startswith("application/json")


def test_429_still_carries_security_headers_and_request_id(limited_run):
    h = {k.lower(): v for k, v in limited_run["blocked_headers"].items()}
    assert h["x-content-type-options"] == "nosniff"
    assert "x-request-id" in h


def test_limits_are_per_route(limited_run):
    """Pins slowapi default_limits semantics: each endpoint has its own bucket,
    so exhausting `/` does not block `/health` (keeps probes alive)."""
    assert limited_run["health_after_root_exhausted"] == 200


def test_limit_defaults_to_100_per_minute_when_variable_absent(monkeypatch):
    """Production safety: with RATE_LIMIT unset the limiter is ON at 100/minute."""
    monkeypatch.delenv("RATE_LIMIT", raising=False)
    result = run_app("""
        from app import config
        result = [config.RATE_LIMIT, hasattr(app.state, "limiter")]
    """)
    assert result == ["100/minute", True]


def test_empty_rate_limit_disables_limiter(app_client):
    """The in-process suite app (RATE_LIMIT='') never throttles."""
    assert all(app_client.get("/").status_code == 200 for _ in range(150))


@pytest.mark.xfail(strict=True, reason=(
    "DEFECT: the limiter keys on request.client.host (slowapi get_remote_address) "
    "and nothing honours X-Forwarded-For (no ProxyHeadersMiddleware; the "
    "Dockerfile runs uvicorn without --forwarded-allow-ips). Behind a load "
    "balancer every user therefore shares ONE bucket: one heavy user rate-limits "
    "everyone."))
def test_distinct_clients_behind_proxy_have_separate_buckets():
    result = run_app("""
        c = TestClient(app)
        a = [c.get("/", headers={"X-Forwarded-For": "203.0.113.1"}).status_code for _ in range(2)]
        b = c.get("/", headers={"X-Forwarded-For": "198.51.100.7"}).status_code
        result = {"a": a, "b": b}
    """, env=LIMITED)
    assert result == {"a": [200, 200], "b": 200}
