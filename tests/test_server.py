"""Server wiring: tool registration, annotations, and that a call reaches
the underlying composed-tool implementation. Not a re-test of each tool's
behavior — see tests/tools/ for that.
"""

from __future__ import annotations

import pytest
from mcp.types import CallToolResult

from segment_mcp import server
from segment_mcp.client.public_api import SegmentPublicAPIClient
from segment_mcp.client.regions import Region
from tests.tools._helpers import make_client

EXPECTED_TOOLS = {
    "audit_event_routing",
    "trace_event",
    "find_stale_sources",
    "check_delivery_health",
    "find_ungoverned_sources",
}


async def test_all_five_tools_are_registered() -> None:
    tools = await server.mcp.list_tools()
    assert {tool.name for tool in tools} == EXPECTED_TOOLS


async def test_every_tool_is_read_only_and_not_destructive() -> None:
    tools = await server.mcp.list_tools()
    for tool in tools:
        assert tool.annotations is not None, tool.name
        assert tool.annotations.read_only_hint is True, tool.name
        # The MCP spec defaults destructiveHint to True — this must be
        # explicit, not just "unset and hoping for the best".
        assert tool.annotations.destructive_hint is False, tool.name


def test_get_client_requires_segment_api_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEGMENT_API_TOKEN", raising=False)
    server._client = None  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(RuntimeError, match="SEGMENT_API_TOKEN is not set"):
        server.get_client()


def test_get_client_is_built_once_and_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEGMENT_API_TOKEN", "fake-token")
    monkeypatch.setenv("SEGMENT_REGION", "us")
    server._client = None  # pyright: ignore[reportPrivateUsage]
    try:
        first = server.get_client()
        second = server.get_client()
        assert first is second
    finally:
        server._client = None  # pyright: ignore[reportPrivateUsage]


async def test_a_tool_call_reaches_the_implementation(monkeypatch: pytest.MonkeyPatch) -> None:
    client = make_client(
        {
            "/sources/src_1": "tools/source_single_200.json",
            "/sources/src_1/connected-destinations": "tools/connected_destinations_one_200.json",
            "/destinations/dest_1": "tools/destination_detail_200.json",
            "/destinations/dest_1/subscriptions": "tools/destination_subscriptions_one_200.json",
        }
    )
    monkeypatch.setattr(server, "get_client", lambda: client)

    async with client:
        result = await server.mcp.call_tool(
            "audit_event_routing", {"source_id": "src_1", "max_sources": 25}
        )

    assert isinstance(result, CallToolResult)
    assert result.structured_content is not None
    routing = result.structured_content["routing"]
    assert routing[0]["destinations"][0]["name"] == "Amplitude"


async def test_check_delivery_health_is_registered_with_all_params() -> None:
    tools = await server.mcp.list_tools()
    tool = next(t for t in tools if t.name == "check_delivery_health")
    properties = tool.input_schema["properties"]
    assert set(properties) == {
        "destination_id",
        "source_id",
        "granularity",
        "start_time",
        "end_time",
    }


async def test_region_client_can_be_us_or_eu(monkeypatch: pytest.MonkeyPatch) -> None:
    for region_value, region in (("us", Region.US), ("eu", Region.EU)):
        monkeypatch.setenv("SEGMENT_API_TOKEN", "fake-token")
        monkeypatch.setenv("SEGMENT_REGION", region_value)
        server._client = None  # pyright: ignore[reportPrivateUsage]
        try:
            client = server.get_client()
            assert isinstance(client, SegmentPublicAPIClient)
            assert client.region is region
        finally:
            server._client = None  # pyright: ignore[reportPrivateUsage]
