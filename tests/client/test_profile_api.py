"""Tests for `ProfileAPIClient` — the Profile API trust tier.

No test here calls a live Segment API — see AGENTS.md and
tests/fixtures/README.md.
"""

from __future__ import annotations

import logging

import pytest

from segment_mcp.client.profile_api import (
    ProfileAPIClient,
    SegmentProfileAuthError,
    SegmentProfileMalformedResponseError,
    SegmentProfileNotFoundError,
    SegmentProfileRateLimitError,
)
from segment_mcp.client.regions import Region
from tests.fixtures.http import mock_transport


def make_client(*fixture_paths: str, region: Region = Region.US) -> ProfileAPIClient:
    return ProfileAPIClient(
        "fake-profile-token",
        region,
        space_id="spa_123",
        transport=mock_transport(*fixture_paths),
    )


# --------------------------------------------------------------------------
# Auth — HTTP Basic, token as username, blank password
# --------------------------------------------------------------------------


async def test_sends_basic_auth_with_token_as_username_and_blank_password() -> None:
    import base64

    import httpx

    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"traits": {}})

    client = ProfileAPIClient(
        "my-profile-token", Region.US, space_id="spa_1", transport=httpx.MockTransport(handler)
    )
    async with client:
        await client.get_traits("users", "email", "jane@example.com")

    auth_header = captured["request"].headers["Authorization"]
    assert auth_header.startswith("Basic ")
    decoded = base64.b64decode(auth_header.removeprefix("Basic ")).decode()
    assert decoded == "my-profile-token:"


async def test_401_raises_segment_profile_auth_error() -> None:
    async with make_client("us/profile/unauthorized_401.json") as client:
        with pytest.raises(SegmentProfileAuthError, match="SEGMENT_PROFILE_TOKEN"):
            await client.get_traits("users", "email", "jane@example.com")


# --------------------------------------------------------------------------
# Lowercase normalization — wrong case returns empty, not an error
# --------------------------------------------------------------------------


async def test_lookup_value_is_lowercased_in_the_request_path() -> None:
    import httpx

    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"traits": {}})

    client = ProfileAPIClient(
        "tok", Region.US, space_id="spa_1", transport=httpx.MockTransport(handler)
    )
    async with client:
        await client.get_traits("users", "email", "Jane@Example.COM")

    assert "jane@example.com" in str(captured["request"].url)
    assert "Jane@Example.COM" not in str(captured["request"].url)


async def test_normalization_logs_a_warning_when_case_was_wrong(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async with make_client("us/profile/traits_200.json") as client:
        with caplog.at_level(logging.WARNING, logger="segment_mcp.profile_api"):
            await client.get_traits("users", "email", "Jane@Example.COM")

    assert any("not lowercase" in record.message for record in caplog.records)


async def test_no_warning_when_value_is_already_lowercase(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async with make_client("us/profile/traits_200.json") as client:
        with caplog.at_level(logging.WARNING, logger="segment_mcp.profile_api"):
            await client.get_traits("users", "email", "jane@example.com")

    assert caplog.records == []


# --------------------------------------------------------------------------
# Every lookup is logged: key, route, and the caller
# --------------------------------------------------------------------------


async def test_every_lookup_is_logged_with_key_and_caller(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async with make_client("us/profile/traits_200.json") as client:
        with caplog.at_level(logging.INFO, logger="segment_mcp.profile_api"):
            await client.get_traits(
                "users", "email", "jane@example.com", requested_by="trace_event"
            )

    messages = [r.message for r in caplog.records]
    assert any(
        "email:jane@example.com" in m and "traits" in m and "trace_event" in m for m in messages
    )


# --------------------------------------------------------------------------
# Each route
# --------------------------------------------------------------------------


async def test_get_traits_default_limit_is_ten() -> None:
    import httpx

    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"traits": {}})

    client = ProfileAPIClient(
        "tok", Region.US, space_id="spa_1", transport=httpx.MockTransport(handler)
    )
    async with client:
        await client.get_traits("users", "email", "jane@example.com")
    assert captured["request"].url.params["limit"] == "10"


async def test_get_traits_rejects_limit_above_200() -> None:
    async with make_client("us/profile/traits_200.json") as client:
        with pytest.raises(ValueError, match="200"):
            await client.get_traits("users", "email", "jane@example.com", limit=201)


async def test_get_external_ids() -> None:
    async with make_client("us/profile/external_ids_200.json") as client:
        body = await client.get_external_ids("users", "email", "jane@example.com")
    assert body["external_ids"][0]["id"] == "jane@example.com"


async def test_get_events_returns_the_fixed_14_day_window() -> None:
    async with make_client("us/profile/events_200.json") as client:
        body = await client.get_events("users", "email", "jane@example.com")
    assert body["events"][0]["event"] == "Order Completed"


async def test_get_metadata() -> None:
    async with make_client("us/profile/metadata_200.json") as client:
        body = await client.get_metadata("users", "email", "jane@example.com")
    assert body["metadata"]["tracks"]["count"] == 120


async def test_get_links_capped_at_20_by_the_api() -> None:
    async with make_client("us/profile/links_200.json") as client:
        body = await client.get_links("users", "email", "jane@example.com")
    assert body["cursor"]["limit"] == 20


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


async def test_404_raises_not_found_and_mentions_case_sensitivity() -> None:
    async with make_client("us/profile/not_found_404.json") as client:
        with pytest.raises(SegmentProfileNotFoundError, match="case"):
            await client.get_traits("users", "email", "jane@example.com")


async def test_429_raises_with_retry_after() -> None:
    async with make_client("us/profile/rate_limited_429.json") as client:
        with pytest.raises(SegmentProfileRateLimitError) as exc_info:
            await client.get_traits("users", "email", "jane@example.com")
    assert exc_info.value.retry_after == pytest.approx(2.0)


async def test_malformed_response_raises_clear_error() -> None:
    async with make_client("us/profile/malformed_200.json") as client:
        with pytest.raises(SegmentProfileMalformedResponseError):
            await client.get_traits("users", "email", "jane@example.com")


# --------------------------------------------------------------------------
# Both regions
# --------------------------------------------------------------------------


async def test_works_against_eu_region_fixtures() -> None:
    async with make_client("eu/profile/traits_200.json", region=Region.EU) as client:
        body = await client.get_traits("users", "email", "jane@example.com")
    assert body["traits"]["plan"] == "pro"
