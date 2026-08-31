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
# Every lookup is logged: id_type, a digest of the key, the route, and the
# caller — never the raw identifier. See ENG-3a: a Profile API client
# logging `email:jane@example.com` in plaintext at INFO is a GDPR/CCPA
# liability regardless of how the README frames "every lookup is logged"
# as a privacy feature.
# --------------------------------------------------------------------------


async def test_every_lookup_is_logged_with_digest_and_caller(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async with make_client("us/profile/traits_200.json") as client:
        with caplog.at_level(logging.INFO, logger="segment_mcp.profile_api"):
            await client.get_traits(
                "users", "email", "jane@example.com", requested_by="trace_event"
            )

    messages = [r.message for r in caplog.records]
    assert any(
        "id_type=email" in m and "key_sha256=" in m and "traits" in m and "trace_event" in m
        for m in messages
    )


async def test_lookup_log_never_contains_the_raw_identifier(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async with make_client("us/profile/traits_200.json") as client:
        with caplog.at_level(logging.INFO, logger="segment_mcp.profile_api"):
            await client.get_traits("users", "email", "jane@example.com")

    for record in caplog.records:
        assert "jane@example.com" not in record.message


async def test_normalization_warning_never_contains_the_raw_or_normalized_value(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # CLOSE-3: the normalization warning used to log both the raw and
    # lowercased value at WARNING — a level that survives any log level a
    # deployment would plausibly set — and it fires on any non-lowercase
    # input, which for an email address is the common case, not the edge
    # case. Captures at DEBUG (not just WARNING) so this can't pass by
    # accident because caplog happened to be scoped above the level that
    # would have caught the leak.
    async with make_client("us/profile/traits_200.json") as client:
        with caplog.at_level(logging.DEBUG, logger="segment_mcp.profile_api"):
            await client.get_traits("users", "email", "Jane.Doe@Example.com")

    assert caplog.records, "expected at least the WARNING + INFO lookup log lines"
    for record in caplog.records:
        for leaked in ("Jane.Doe@Example.com", "jane.doe@example.com", "Jane.Doe", "Example.com"):
            assert leaked not in record.message, f"{leaked!r} leaked in log: {record.message!r}"


async def test_httpx_logger_never_leaks_the_raw_identifier_via_the_request_url(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # CLOSE3-2: this client's own logging is correctly digest-only, but
    # `httpx` logs the full request URL at INFO from inside
    # `Client._send_single_request` — and the Profile API puts the
    # identifier in the URL path (`.../profiles/email:jane@example.com/
    # traits`). A caller who does `logging.basicConfig(level=INFO)` — the
    # exact setup this module's docstring says is needed to capture the
    # audit trail — would see the raw email in plaintext via the `httpx`
    # logger, even though every assertion above is scoped to
    # `segment_mcp.profile_api` and would never catch it. Capture at the
    # root logger, not scoped to any one logger name, so this actually
    # exercises the same surface a real deployment's logging config sees.
    async with make_client("us/profile/traits_200.json") as client:
        with caplog.at_level(logging.DEBUG):
            await client.get_traits("users", "email", "Jane.Doe@Example.com")

    log_text = "\n".join(f"{r.name}: {r.message}" for r in caplog.records)
    for leaked in ("Jane.Doe@Example.com", "jane.doe@example.com", "Jane.Doe", "Example.com"):
        assert leaked not in log_text, f"{leaked!r} leaked in logs: {log_text!r}"


async def test_404_error_never_contains_the_raw_identifier(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Second instance of the same defect: SegmentProfileNotFoundError's
    # message embedded the raw lookup key via `{lookup_key!r}`, which
    # reaches logs and error trackers the same way a log line does.
    async with make_client("us/profile/not_found_404.json") as client:
        with caplog.at_level(logging.DEBUG, logger="segment_mcp.profile_api"):
            with pytest.raises(SegmentProfileNotFoundError) as exc_info:
                await client.get_traits("users", "email", "Jane.Doe@Example.com")

    exception_text = str(exc_info.value)
    log_text = "\n".join(r.message for r in caplog.records)
    for leaked in ("Jane.Doe@Example.com", "jane.doe@example.com", "Jane.Doe", "Example.com"):
        assert leaked not in exception_text, f"{leaked!r} leaked in exception: {exception_text!r}"
        assert leaked not in log_text, f"{leaked!r} leaked in logs: {log_text!r}"


async def test_lookup_digest_is_stable_across_calls_for_the_same_identifier(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async with make_client("us/profile/traits_200.json", "us/profile/traits_200.json") as client:
        with caplog.at_level(logging.INFO, logger="segment_mcp.profile_api"):
            await client.get_traits("users", "email", "jane@example.com")
            await client.get_traits("users", "email", "jane@example.com")

    digests = [_extract_digest(r.message) for r in caplog.records if "key_sha256=" in r.message]
    assert len(digests) == 2
    assert digests[0] == digests[1]


def _extract_digest(message: str) -> str:
    for part in message.split():
        if part.startswith("key_sha256="):
            return part.removeprefix("key_sha256=")
    raise AssertionError(f"no key_sha256= field in {message!r}")


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
