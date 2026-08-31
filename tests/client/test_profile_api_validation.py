"""Tests for path-traversal validation in `ProfileAPIClient` — the exact
ENG-1 bug (`docs/decisions/0003-refuse-path-traversal-in-resource-ids.md`),
unfixed, in a second file.

`ProfileAPIClient._get` interpolates four caller-controlled values
(`space_id`, `collection`, `route`, and an `{id_type}:{id_value}` lookup
key) directly into a Public API request path. Confirmed live against a
recording transport before this fix:

    id_value="../../../../regulations"      -> GET .../collections/regulations/traits
    id_value="../../../v1beta/users?x=1"    -> GET .../collections/users/v1beta/users?limit=10
    id_value="x/../../../../../regulations" -> GET .../v1/spaces/regulations/traits
    id_type="../../../../regulations"       -> GET .../profiles/regulations:v/traits
    space_id="sp1/../../.."                 -> GET .../profiles/email:a@b.com/traits
    route="../../../../../regulations"      -> GET .../v1/spaces/regulations

`collection` and `route` are `Literal` types, which is static-only and does
nothing at runtime — this module is proof of it, since `ProfileAPIClient`
was unreachable from any registered tool (`tools/profiles.py` is a
docstring-only stub) and so was never exercised by the ENG-1 fix.

No test here calls a live Segment API — see AGENTS.md.
"""

from __future__ import annotations

import httpx
import pytest

from segment_mcp.client.profile_api import ProfileAPIClient
from segment_mcp.client.regions import Region
from segment_mcp.client.validation import InvalidResourceIdError


def _no_call_transport() -> httpx.MockTransport:
    """A transport that fails the test if it's ever asked to send anything —
    the only way to actually prove the refusal happened before the network,
    not just that *an* error was eventually raised."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected transport call: {request.method} {request.url}")

    return httpx.MockTransport(handler)


# --------------------------------------------------------------------------
# Each of the four caller-controlled path-building values, parametrized
# over the reproduction payloads.
# --------------------------------------------------------------------------

TRAVERSAL_ID_VALUES = [
    "../../../../regulations",
    "../../../v1beta/users?x=1",
    "x/../../../../../regulations",
]


@pytest.mark.parametrize("traversal", TRAVERSAL_ID_VALUES)
async def test_refuses_traversal_id_value_before_any_request(traversal: str) -> None:
    client = ProfileAPIClient("tok", Region.US, space_id="spa_123", transport=_no_call_transport())
    async with client:
        with pytest.raises(InvalidResourceIdError):
            await client.get_traits("users", "email", traversal)


async def test_refuses_traversal_id_type_before_any_request() -> None:
    client = ProfileAPIClient("tok", Region.US, space_id="spa_123", transport=_no_call_transport())
    async with client:
        with pytest.raises(InvalidResourceIdError):
            await client.get_traits("users", "../../../../regulations", "jane@example.com")


async def test_refuses_traversal_space_id_at_construction() -> None:
    # Validated in __init__, not at call time — the bad space ID should
    # never even produce a usable client.
    with pytest.raises(InvalidResourceIdError):
        ProfileAPIClient("tok", Region.US, space_id="sp1/../../..", transport=_no_call_transport())


async def test_refuses_traversal_route_before_any_request() -> None:
    # `route` isn't a public parameter of `get_traits` et al. — it's fixed
    # per method — so this goes through the private `_get` the way an
    # internal call (or a future route this client adds) would, proving
    # the runtime membership check catches a value the `Literal` type
    # would otherwise only reject statically.
    client = ProfileAPIClient("tok", Region.US, space_id="spa_123", transport=_no_call_transport())
    async with client:
        with pytest.raises(InvalidResourceIdError):
            await client._get(  # pyright: ignore[reportPrivateUsage]
                "users",
                "email",
                "jane@example.com",
                "../../../../../regulations",  # pyright: ignore[reportArgumentType]
            )


async def test_refuses_traversal_collection_before_any_request() -> None:
    client = ProfileAPIClient("tok", Region.US, space_id="spa_123", transport=_no_call_transport())
    async with client:
        with pytest.raises(InvalidResourceIdError):
            await client._get(  # pyright: ignore[reportPrivateUsage]
                "../../../../regulations",  # pyright: ignore[reportArgumentType]
                "email",
                "jane@example.com",
                "traits",
            )


# --------------------------------------------------------------------------
# Legitimate values still work — the whole point of a dedicated id_value
# validator instead of reusing validate_resource_id, which would wrongly
# refuse an email address.
# --------------------------------------------------------------------------


async def test_legitimate_email_still_works() -> None:
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"traits": {}})

    client = ProfileAPIClient(
        "tok", Region.US, space_id="spa_123", transport=httpx.MockTransport(handler)
    )
    async with client:
        await client.get_traits("users", "email", "jane@example.com")

    assert "email:jane@example.com" in str(captured["request"].url)


async def test_legitimate_plus_tagged_email_still_works() -> None:
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"traits": {}})

    client = ProfileAPIClient(
        "tok", Region.US, space_id="spa_123", transport=httpx.MockTransport(handler)
    )
    async with client:
        await client.get_traits("users", "email", "jane+newsletter@example.com")

    assert "jane+newsletter@example.com" in str(captured["request"].url)


async def test_legitimate_unicode_local_part_still_works() -> None:
    captured: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json={"traits": {}})

    client = ProfileAPIClient(
        "tok", Region.US, space_id="spa_123", transport=httpx.MockTransport(handler)
    )
    async with client:
        await client.get_traits("users", "email", "josé@example.com")

    sent_path = captured["request"].url.raw_path.decode()
    assert "%c3%a9" in sent_path.lower()  # "é" percent-encoded, not rejected


async def test_legitimate_user_id_still_works() -> None:
    async with ProfileAPIClient(
        "tok",
        Region.US,
        space_id="spa_123",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"external_ids": []})
        ),
    ) as client:
        body = await client.get_external_ids("users", "user_id", "abc-123")
    assert body == {"external_ids": []}
