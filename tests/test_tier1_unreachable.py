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
"""

from __future__ import annotations

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


@pytest.mark.parametrize("region", [Region.US, Region.EU])
@pytest.mark.parametrize(
    "path",
    [
        "/regulations",
        "/regulations/sources/src_1",
        "/regulations/cloudsources/src_1",
    ],
)
async def test_client_refuses_post_to_regulations_paths(path: str, region: Region) -> None:
    # No transport is configured — if this reached the network layer at
    # all, it would hang or error on a real connection attempt instead of
    # raising Tier1BlockedError immediately. It must never get that far.
    client = SegmentPublicAPIClient("fake-token", region)
    async with client:
        with pytest.raises(Tier1BlockedError):
            await client._request("POST", path)  # pyright: ignore[reportPrivateUsage]


async def test_client_still_allows_reading_regulations() -> None:
    # The refusal is POST-shaped, not path-shaped — GET /regulations,
    # GET /regulations/{id}, and GET /suppressions are in scope for v0.2
    # (BUILD-PLAN.md §6) and must not be caught by this guard.
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"regulations": []}})

    client = SegmentPublicAPIClient("fake-token", Region.US, transport=httpx.MockTransport(handler))
    async with client:
        body = await client.get("/regulations")
    assert body == {"data": {"regulations": []}}


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
