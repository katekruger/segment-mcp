"""The test that proves the thesis: Tier 1 (`POST /regulations` and
`POST /regulations/sources/{id}` — permanent, irreversible data deletion)
is not reachable, in any mode, through any path. Belt and braces,
deliberately — see BUILD-PLAN.md §6 and
docs/decisions/0002-tier-1-permanently-unreachable.md.

Three independent checks, any one of which failing is a critical bug:
1. `modes.authorize()` refuses Tier 1 outright — before even looking at
   mode.
2. The client itself refuses to send the request, in every mode.
3. No registered tool definition references `/regulations` at all.

Check 2 is parametrized over ten confirmed bypass strings — three that a
naive `path.split("?", 1)[0]` string-prefix check already caught, and
seven an external audit found it missed (a `#fragment` it didn't strip, a
`.`/`..`-segment it didn't normalize, a missing leading slash, an absolute
URL, and case variation) — across all four non-GET methods, per
docs/decisions/0004-tier1-guard-matches-resolved-url.md. Never relax this
back down to the original three strings or to a single method: the whole
point of this rewrite is that the narrow version passed while the guard
was still bypassable.
"""

from __future__ import annotations

import httpx
import pytest

from segment_mcp import server
from segment_mcp.client.public_api import SegmentPublicAPIClient, Tier1BlockedError
from segment_mcp.client.regions import Region
from segment_mcp.modes import Mode, Tier, Tier1UnreachableError, authorize

# --------------------------------------------------------------------------
# 1. modes.py refuses it — belt
# --------------------------------------------------------------------------


@pytest.mark.parametrize("mode", [Mode.READ, Mode.WRITE, Mode.ADMIN])
def test_authorize_refuses_tier1_in_every_mode(mode: Mode) -> None:
    with pytest.raises(Tier1UnreachableError):
        authorize(Tier.TIER1, mode)


def test_authorize_refuses_tier1_even_with_every_confirmation_supplied() -> None:
    # Confirming harder is not a way around this. There is no argument
    # combination that makes Tier 1 authorize() return allowed=True.
    with pytest.raises(Tier1UnreachableError):
        authorize(
            Tier.TIER1,
            Mode.ADMIN,
            confirmed=True,
            typed_confirmation="anything",
            expected_resource="anything",
        )


# --------------------------------------------------------------------------
# 2. The client refuses the path — braces
# --------------------------------------------------------------------------

# The three strings the original (string-prefix) guard already caught,
# plus the seven the audit confirmed it missed. All ten must be refused
# for every non-GET method — see the module docstring.
TIER1_BYPASS_PATHS = [
    "/regulations",
    "/regulations/sources/src_1",
    "/regulations/cloudsources/src_1",
    "/regulations#frag",
    "/./regulations",
    "/foo/../regulations",
    "regulations",
    "https://api.segmentapis.com/regulations",
    "/REGULATIONS",
    "/Regulations",
]

NON_GET_METHODS = ["POST", "PUT", "PATCH", "DELETE"]


@pytest.mark.parametrize("method", NON_GET_METHODS)
@pytest.mark.parametrize("path", TIER1_BYPASS_PATHS)
async def test_client_refuses_every_mutation_bypass_form(path: str, method: str) -> None:
    # No transport is configured — if this reached the network layer at
    # all, it would hang or error on a real connection attempt instead of
    # raising Tier1BlockedError immediately. It must never get that far.
    client = SegmentPublicAPIClient("fake-token", Region.US)
    async with client:
        with pytest.raises(Tier1BlockedError):
            await client._request(method, path)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("method", NON_GET_METHODS)
async def test_client_refuses_every_bypass_form_against_eu_base_url_too(method: str) -> None:
    # The guard resolves against the client's own base_url — prove that
    # holds for the EU region too, not just the US one every other test
    # in this file uses.
    client = SegmentPublicAPIClient("fake-token", Region.EU)
    async with client:
        with pytest.raises(Tier1BlockedError):
            await client._request(method, "/regulations")  # pyright: ignore[reportPrivateUsage]


# --------------------------------------------------------------------------
# Negative cases — a naive `startswith` fix without a `/` boundary check
# would block a legitimate future endpoint that merely starts with the
# same letters. These must NOT raise.
# --------------------------------------------------------------------------


async def test_client_still_allows_reading_regulations() -> None:
    # The refusal is POST-shaped, not path-shaped — GET /regulations,
    # GET /regulations/{id}, and GET /suppressions are in scope for v0.2
    # (BUILD-PLAN.md §6) and must not be caught by this guard.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"regulations": []}})

    client = SegmentPublicAPIClient("fake-token", Region.US, transport=httpx.MockTransport(handler))
    async with client:
        body = await client.get("/regulations")
    assert body == {"data": {"regulations": []}}


async def test_client_allows_posting_to_an_unrelated_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"data": {"source": {"id": "src_new"}}})

    client = SegmentPublicAPIClient("fake-token", Region.US, transport=httpx.MockTransport(handler))
    async with client:
        body = await client._request("POST", "/sources")  # pyright: ignore[reportPrivateUsage]
    assert body == {"data": {"source": {"id": "src_new"}}}


async def test_client_allows_posting_to_a_path_that_merely_starts_with_regulations() -> None:
    # A resource genuinely named "regulations-adjacent" is not
    # "/regulations" or a child of it — a boundary-unaware `startswith`
    # fix would wrongly block this.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"data": {"id": "ra_1"}})

    client = SegmentPublicAPIClient("fake-token", Region.US, transport=httpx.MockTransport(handler))
    async with client:
        body = await client._request(  # pyright: ignore[reportPrivateUsage]
            "POST", "/regulations-adjacent"
        )
    assert body == {"data": {"id": "ra_1"}}


# --------------------------------------------------------------------------
# 3. No tool definition references it
# --------------------------------------------------------------------------


async def test_no_registered_tool_mentions_regulations() -> None:
    tools = await server.mcp.list_tools()
    assert len(tools) > 0, "expected at least the five v0.1 tools to be registered"
    for tool in tools:
        haystack = " ".join([tool.name, tool.description or "", str(tool.input_schema)]).lower()
        assert "regulation" not in haystack, (
            f"tool {tool.name!r} references 'regulation' — Tier 1 must never "
            "surface as a callable capability, directly or indirectly"
        )
