"""Unit tests for `PerEndpointRateLimiter`, independent of HTTP.

The limiter must never hard-code a request budget — it only reacts to
what it's been told via `observe_response` / `observe_retry_after`. These
tests use a fake clock and a recording `sleep` so nothing here actually
waits in real time.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from segment_mcp.client.public_api import (
    PerEndpointRateLimiter,
    _endpoint_key,  # pyright: ignore[reportPrivateUsage]
)


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def __call__(self) -> datetime:
        return self._now


class RecordingSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.fixture
def limiter() -> tuple[PerEndpointRateLimiter, FakeClock, RecordingSleep]:
    clock = FakeClock(datetime(2026, 8, 29, 20, 0, 0, tzinfo=UTC))
    sleep = RecordingSleep()
    return PerEndpointRateLimiter(clock=clock, sleep=sleep), clock, sleep


async def test_untouched_endpoint_never_waits(
    limiter: tuple[PerEndpointRateLimiter, FakeClock, RecordingSleep],
) -> None:
    rate_limiter, _clock, sleep = limiter
    await rate_limiter.wait_if_needed("GET", "/sources")
    assert sleep.calls == []


async def test_waits_until_reset_when_remaining_hits_zero(
    limiter: tuple[PerEndpointRateLimiter, FakeClock, RecordingSleep],
) -> None:
    rate_limiter, clock, sleep = limiter
    reset_at = clock() + timedelta(seconds=30)
    headers = httpx.Headers(
        {
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": reset_at.strftime("%a, %d %b %Y %H:%M:%S GMT"),
        }
    )
    rate_limiter.observe_response("GET", "/sources", headers)

    await rate_limiter.wait_if_needed("GET", "/sources")

    assert sleep.calls == [pytest.approx(30.0, abs=1.0)]


async def test_does_not_wait_when_remaining_is_positive(
    limiter: tuple[PerEndpointRateLimiter, FakeClock, RecordingSleep],
) -> None:
    rate_limiter, clock, sleep = limiter
    reset_at = clock() + timedelta(seconds=30)
    headers = httpx.Headers(
        {
            "X-RateLimit-Remaining": "5",
            "X-RateLimit-Reset": reset_at.strftime("%a, %d %b %Y %H:%M:%S GMT"),
        }
    )
    rate_limiter.observe_response("GET", "/sources", headers)

    await rate_limiter.wait_if_needed("GET", "/sources")

    assert sleep.calls == []


async def test_rate_limit_state_is_scoped_per_endpoint(
    limiter: tuple[PerEndpointRateLimiter, FakeClock, RecordingSleep],
) -> None:
    rate_limiter, clock, sleep = limiter
    reset_at = clock() + timedelta(seconds=30)
    headers = httpx.Headers(
        {
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": reset_at.strftime("%a, %d %b %Y %H:%M:%S GMT"),
        }
    )
    rate_limiter.observe_response("GET", "/sources", headers)

    # A different endpoint has no recorded state, so it should not wait —
    # this is the whole point of a per-endpoint limiter over a global one.
    await rate_limiter.wait_if_needed("GET", "/destinations")

    assert sleep.calls == []


async def test_observe_retry_after_with_none_is_a_no_op(
    limiter: tuple[PerEndpointRateLimiter, FakeClock, RecordingSleep],
) -> None:
    rate_limiter, _clock, sleep = limiter
    rate_limiter.observe_retry_after("GET", "/sources", None)
    await rate_limiter.wait_if_needed("GET", "/sources")
    assert sleep.calls == []


async def test_zero_second_retry_after_does_not_call_sleep(
    limiter: tuple[PerEndpointRateLimiter, FakeClock, RecordingSleep],
) -> None:
    rate_limiter, _clock, sleep = limiter
    rate_limiter.observe_retry_after("GET", "/sources", 0.0)
    await rate_limiter.wait_if_needed("GET", "/sources")
    assert sleep.calls == []


async def test_observe_retry_after_waits_once_then_clears(
    limiter: tuple[PerEndpointRateLimiter, FakeClock, RecordingSleep],
) -> None:
    rate_limiter, _clock, sleep = limiter
    rate_limiter.observe_retry_after("POST", "/tracking-plans/tp_1/rules", 12.0)

    await rate_limiter.wait_if_needed("POST", "/tracking-plans/tp_1/rules")
    assert sleep.calls == [12.0]

    # The wait was consumed — a second call shouldn't wait again for the
    # same recorded 429 unless a new one comes in.
    await rate_limiter.wait_if_needed("POST", "/tracking-plans/tp_1/rules")
    assert sleep.calls == [12.0]


async def test_retry_after_takes_priority_over_remaining_zero(
    limiter: tuple[PerEndpointRateLimiter, FakeClock, RecordingSleep],
) -> None:
    rate_limiter, clock, sleep = limiter
    reset_at = clock() + timedelta(seconds=500)
    headers = httpx.Headers(
        {
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": reset_at.strftime("%a, %d %b %Y %H:%M:%S GMT"),
        }
    )
    rate_limiter.observe_response("GET", "/sources", headers)
    rate_limiter.observe_retry_after("GET", "/sources", 3.0)

    await rate_limiter.wait_if_needed("GET", "/sources")

    # Segment's docs say prefer Retry-After — the much shorter 3s wait,
    # not the 500s reset window.
    assert sleep.calls == [3.0]


async def test_malformed_remaining_header_does_not_raise(
    limiter: tuple[PerEndpointRateLimiter, FakeClock, RecordingSleep],
) -> None:
    rate_limiter, _clock, sleep = limiter
    headers = httpx.Headers({"X-RateLimit-Remaining": "not-a-number"})
    rate_limiter.observe_response("GET", "/sources", headers)

    await rate_limiter.wait_if_needed("GET", "/sources")

    assert sleep.calls == []


def test_endpoint_key_ignores_query_string() -> None:
    assert _endpoint_key("get", "/sources?limit=10") == _endpoint_key("GET", "/sources")
