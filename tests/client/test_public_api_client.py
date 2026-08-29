"""Tests for `SegmentPublicAPIClient` against recorded fixtures.

No test here calls a live Segment API — see AGENTS.md and
tests/fixtures/README.md.
"""

from __future__ import annotations

import httpx
import pytest

from segment_mcp.client.public_api import (
    SegmentAuthError,
    SegmentMalformedResponseError,
    SegmentPermissionError,
    SegmentPublicAPIClient,
    SegmentRateLimitError,
    SegmentTierError,
)
from segment_mcp.client.regions import Region, RegionMismatchError
from tests.fixtures.http import load_fixture, mock_transport, response_from_fixture


def make_client(*fixture_paths: str, region: Region = Region.US) -> SegmentPublicAPIClient:
    return SegmentPublicAPIClient("fake-token", region, transport=mock_transport(*fixture_paths))


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------


async def test_sends_bearer_auth_header() -> None:
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"ok": True})

    async with SegmentPublicAPIClient(
        "my-secret-token", Region.US, transport=httpx.MockTransport(handler)
    ) as client:
        await client.get("/sources")

    assert captured["request"].headers["Authorization"] == "Bearer my-secret-token"


async def test_no_v1_in_request_path() -> None:
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"ok": True})

    async with SegmentPublicAPIClient(
        "tok", Region.US, transport=httpx.MockTransport(handler)
    ) as client:
        await client.get("/sources")

    assert "/v1/" not in str(captured["request"].url)


async def test_401_raises_segment_auth_error() -> None:
    async with make_client("us/unauthorized_401.json") as client:
        with pytest.raises(SegmentAuthError):
            await client.get("/sources")


# --------------------------------------------------------------------------
# 403 classification
# --------------------------------------------------------------------------


async def test_free_tier_403_raises_segment_tier_error_not_bare_permission_error() -> None:
    async with make_client("us/free_tier_403.json") as client:
        with pytest.raises(SegmentTierError, match="Team or Business tier"):
            await client.get("/sources")


async def test_insufficient_permissions_403_names_workspace_owner_role() -> None:
    async with make_client("us/insufficient_permissions_403.json") as client:
        with pytest.raises(SegmentPermissionError, match="Workspace Owner") as exc_info:
            await client.get("/sources")
    # And it must NOT be misclassified as a tier error.
    assert not isinstance(exc_info.value, SegmentTierError)


# --------------------------------------------------------------------------
# Rate limiting — 429 parsing
# --------------------------------------------------------------------------


async def test_429_with_retry_after_header_is_preferred() -> None:
    async with make_client("us/rate_limited_429_with_retry_after.json") as client:
        with pytest.raises(SegmentRateLimitError) as exc_info:
            await client.get("/sources")
    error = exc_info.value
    assert error.retry_after == pytest.approx(12.0)
    # Token-level 429s carry Retry-After but omit the data.* fields.
    assert error.remaining_points is None
    assert error.consumed_points is None


async def test_429_without_retry_after_falls_back_to_ms_before_next() -> None:
    async with make_client("us/rate_limited_429_without_retry_after.json") as client:
        with pytest.raises(SegmentRateLimitError) as exc_info:
            await client.get("/audiences/previews")
    error = exc_info.value
    assert error.retry_after == pytest.approx(4.5)
    assert error.remaining_points == 0
    assert error.consumed_points == 61


async def test_429_with_no_timing_signal_falls_back_to_conservative_backoff() -> None:
    async with make_client("us/rate_limited_429_no_signal.json") as client:
        with pytest.raises(SegmentRateLimitError, match="conservative default") as exc_info:
            await client.get("/sources")
    # A usable positive number, not None — callers shouldn't have to
    # special-case "no signal" into "retry immediately".
    assert exc_info.value.retry_after == pytest.approx(1.0)


async def test_429_updates_the_rate_limiter_for_the_next_call_to_that_endpoint() -> None:
    async with make_client(
        "us/rate_limited_429_with_retry_after.json", "us/workspaces_200.json"
    ) as client:
        with pytest.raises(SegmentRateLimitError):
            await client.get("/workspaces")

        # Swap in a recording sleep so the second call's wait is observable
        # without actually waiting in the test.
        waits: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            waits.append(seconds)

        client._limiter.sleep = fake_sleep  # type: ignore[assignment]
        await client.get("/workspaces")

    assert waits == [pytest.approx(12.0)]


# --------------------------------------------------------------------------
# Malformed responses
# --------------------------------------------------------------------------


async def test_malformed_response_raises_clear_error_not_a_json_exception() -> None:
    async with make_client("us/malformed_response.json") as client:
        with pytest.raises(SegmentMalformedResponseError):
            await client.get("/sources")


async def test_valid_json_that_is_not_an_object_is_also_malformed() -> None:
    async with make_client("us/list_response_200.json") as client:
        with pytest.raises(SegmentMalformedResponseError, match="not a string-keyed object"):
            await client.get("/sources")


# --------------------------------------------------------------------------
# get_data() — the `data` envelope every Public API response uses
# --------------------------------------------------------------------------


async def test_get_data_unwraps_the_data_envelope() -> None:
    async with make_client("us/workspaces_200.json") as client:
        data = await client.get_data("/workspaces")
    assert data["workspace"]["id"] == "ws_fake0000000000000000"


async def test_get_data_raises_when_envelope_is_missing() -> None:
    async with make_client("us/no_data_envelope_200.json") as client:
        with pytest.raises(SegmentMalformedResponseError, match="no 'data' object"):
            await client.get_data("/sources")


# --------------------------------------------------------------------------
# paginate() — cursor pagination
# --------------------------------------------------------------------------


async def test_paginate_follows_cursor_until_next_is_null() -> None:
    async with make_client("us/sources_page1_200.json", "us/sources_page2_200.json") as client:
        items, truncated = await client.paginate("/sources", items_key="sources")
    assert [item["id"] for item in items] == ["src_1", "src_2", "src_3"]
    assert truncated is False


async def test_paginate_reports_truncation_at_max_pages() -> None:
    async with make_client("us/sources_page1_200.json", "us/sources_page2_200.json") as client:
        items, truncated = await client.paginate("/sources", items_key="sources", max_pages=1)
    assert [item["id"] for item in items] == ["src_1", "src_2"]
    assert truncated is True


async def test_paginate_sends_cursor_from_previous_page() -> None:
    transport_calls: list[httpx.Request] = []
    fixtures = [
        load_fixture("us/sources_page1_200.json"),
        load_fixture("us/sources_page2_200.json"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        transport_calls.append(request)
        index = min(len(transport_calls) - 1, len(fixtures) - 1)
        return response_from_fixture(fixtures[index])

    async with SegmentPublicAPIClient(
        "tok", Region.US, transport=httpx.MockTransport(handler)
    ) as client:
        await client.paginate("/sources", items_key="sources")

    assert "pagination.cursor" not in str(transport_calls[0].url)
    assert "pagination.cursor=Mg%3D%3D" in str(transport_calls[1].url)


# --------------------------------------------------------------------------
# Composed-tool-shaped fixtures (used again once Prompt 2 wires the tools)
# --------------------------------------------------------------------------


async def test_source_with_no_connected_destinations_parses_to_empty_list() -> None:
    async with make_client("us/source_no_connected_destinations_200.json") as client:
        data = await client.get_data("/sources/src_1/connected-destinations")
    assert data["destinations"] == []


async def test_tracking_plan_with_no_sources_parses_to_empty_list() -> None:
    async with make_client("us/tracking_plan_no_sources_200.json") as client:
        data = await client.get_data("/tracking-plans/tp_1/sources")
    assert data["sources"] == []


# --------------------------------------------------------------------------
# verify_region — the startup self-check
# --------------------------------------------------------------------------


async def test_verify_region_succeeds_when_token_matches_configured_region() -> None:
    async with make_client("us/workspaces_200.json", region=Region.US) as client:
        await client.verify_region()  # must not raise


async def test_verify_region_raises_mismatch_when_token_belongs_to_the_other_region() -> None:
    client = SegmentPublicAPIClient(
        "fake-token",
        Region.US,
        transport=mock_transport("us/unauthorized_401.json"),
        probe_transport=mock_transport("eu/workspaces_200.json"),
    )
    async with client:
        with pytest.raises(RegionMismatchError, match="'eu'"):
            await client.verify_region()


async def test_verify_region_reraises_auth_error_when_token_is_bad_everywhere() -> None:
    client = SegmentPublicAPIClient(
        "fake-token",
        Region.US,
        transport=mock_transport("us/unauthorized_401.json"),
        probe_transport=mock_transport("eu/unauthorized_401.json"),
    )
    async with client:
        with pytest.raises(SegmentAuthError):
            await client.verify_region()
