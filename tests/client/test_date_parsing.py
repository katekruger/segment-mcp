"""Tests for the RFC 5322 / Retry-After parsing helpers.

`X-RateLimit-Reset` is an RFC 5322 timestamp, not epoch — BUILD-PLAN.md §4
is explicit that parsing it as an int is the kind of mistake that costs a
day. `Retry-After` (RFC 7231 §7.1.3) can be either delta-seconds or an
HTTP-date, so both forms need to parse.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from segment_mcp.client.public_api import (
    _parse_http_date,  # pyright: ignore[reportPrivateUsage]
    _parse_retry_after,  # pyright: ignore[reportPrivateUsage]
)


def test_parses_rfc5322_date_not_as_an_epoch_int() -> None:
    parsed = _parse_http_date("Sat, 29 Aug 2026 21:00:00 GMT")
    assert parsed == datetime(2026, 8, 29, 21, 0, 0, tzinfo=UTC)


def test_unparseable_date_returns_none_not_an_exception() -> None:
    assert _parse_http_date("not-a-date") is None


def test_unparseable_date_does_not_raise() -> None:
    # A malformed X-RateLimit-Reset must degrade to "unknown", never crash
    # the request that carried it.
    for garbage in ("", "1234567890", "Reset-Not-Provided"):
        assert _parse_http_date(garbage) is None


def test_retry_after_parses_delta_seconds() -> None:
    assert _parse_retry_after("12") == 12.0


def test_retry_after_parses_http_date() -> None:
    future = datetime.now(UTC) + timedelta(seconds=30)
    header_value = future.strftime("%a, %d %b %Y %H:%M:%S GMT")
    result = _parse_retry_after(header_value)
    assert result == pytest.approx(30.0, abs=2.0)


def test_retry_after_http_date_in_the_past_clamps_to_zero() -> None:
    past = datetime.now(UTC) - timedelta(seconds=30)
    header_value = past.strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert _parse_retry_after(header_value) == 0.0


def test_retry_after_garbage_returns_none() -> None:
    assert _parse_retry_after("not-a-retry-value") is None
