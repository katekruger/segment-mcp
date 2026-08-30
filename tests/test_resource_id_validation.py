"""Tests for `validate_resource_id` and its use at every tool boundary that
interpolates a `source_id`/`destination_id`-shaped value into a Public API
request path.

Confirmed live against the client before this fix: an LLM-controlled
`source_id` such as `"../regulations"` or `"../../v1beta/users?x=1"`
reaches `httpx` unescaped, which normalizes the dot-segments and walks the
request outside the advertised `/sources/...` surface with a Workspace
Owner token. `validate_resource_id` refuses anything that is not a bare
Segment identifier before the request is built, so the traversal never
reaches the transport at all — see the `*_before_any_request` tests below,
which assert zero transport calls, not just a raised error.

No test here calls a live Segment API — see AGENTS.md.
"""

from __future__ import annotations

import httpx
import pytest

from segment_mcp.client.public_api import SegmentPublicAPIClient
from segment_mcp.client.regions import Region
from segment_mcp.client.validation import InvalidResourceIdError, validate_resource_id
from segment_mcp.tools.governance import find_ungoverned_sources
from segment_mcp.tools.health import check_delivery_health
from segment_mcp.tools.routing import audit_event_routing, trace_event

# --------------------------------------------------------------------------
# validate_resource_id() — the unit
# --------------------------------------------------------------------------

INVALID_IDS = [
    "../regulations",
    "../../v1beta/users?x=1",
    "foo/bar",
    "foo?x=1",
    "foo#frag",
    ".",
    "..",
    "",
    "a" * 65,
    "foo bar",
    "%2e%2e%2fregulations",
]

VALID_IDS = [
    "9f8f8f8f8f8f8f8f8f8f8f8f",  # realistic Segment resource ID shape
    "my-source-1",
    "my_source_1",
]


@pytest.mark.parametrize("value", INVALID_IDS)
def test_rejects_anything_that_is_not_a_bare_segment_id(value: str) -> None:
    with pytest.raises(InvalidResourceIdError):
        validate_resource_id(value, kind="source_id")


@pytest.mark.parametrize("value", VALID_IDS)
def test_accepts_bare_segment_ids_unchanged(value: str) -> None:
    assert validate_resource_id(value, kind="source_id") == value


# --------------------------------------------------------------------------
# End-to-end: traversal input is refused before the network layer, per tool
# --------------------------------------------------------------------------


def _no_call_transport() -> httpx.MockTransport:
    """A transport that fails the test if it's ever asked to send anything —
    the only way to actually prove the refusal happened before the network,
    not just that *an* error was eventually raised."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected transport call: {request.method} {request.url}")

    return httpx.MockTransport(handler)


TRAVERSAL_INPUTS = ["../regulations", "../../v1beta/users?x=1"]


@pytest.mark.parametrize("traversal", TRAVERSAL_INPUTS)
async def test_audit_event_routing_refuses_traversal_source_id(traversal: str) -> None:
    client = SegmentPublicAPIClient("tok", Region.US, transport=_no_call_transport())
    async with client:
        with pytest.raises(InvalidResourceIdError):
            await audit_event_routing(client, source_id=traversal)


@pytest.mark.parametrize("traversal", TRAVERSAL_INPUTS)
async def test_trace_event_refuses_traversal_source_id(traversal: str) -> None:
    client = SegmentPublicAPIClient("tok", Region.US, transport=_no_call_transport())
    async with client:
        with pytest.raises(InvalidResourceIdError):
            await trace_event(client, event_name="Order Completed", source_id=traversal)


@pytest.mark.parametrize("traversal", TRAVERSAL_INPUTS)
async def test_find_ungoverned_sources_refuses_traversal_source_id(traversal: str) -> None:
    client = SegmentPublicAPIClient("tok", Region.US, transport=_no_call_transport())
    async with client:
        with pytest.raises(InvalidResourceIdError):
            await find_ungoverned_sources(client, source_id=traversal)


@pytest.mark.parametrize("traversal", TRAVERSAL_INPUTS)
async def test_check_delivery_health_refuses_traversal_destination_id(traversal: str) -> None:
    client = SegmentPublicAPIClient("tok", Region.US, transport=_no_call_transport())
    async with client:
        with pytest.raises(InvalidResourceIdError):
            await check_delivery_health(client, destination_id=traversal, source_id="src_1")
