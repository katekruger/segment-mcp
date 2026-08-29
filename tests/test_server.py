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
from segment_mcp.modes import Mode, Tier
from tests.fixtures.http import mock_transport
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


# --------------------------------------------------------------------------
# The three-tier model is exposed in the server's `instructions`
# --------------------------------------------------------------------------


def test_instructions_expose_the_current_mode() -> None:
    assert server.mcp.instructions is not None
    assert "read" in server.mcp.instructions
    assert "SEGMENT_MCP_MODE" in server.mcp.instructions


def test_instructions_state_tier1_is_unreachable() -> None:
    assert server.mcp.instructions is not None
    assert "permanently unreachable" in server.mcp.instructions.lower()


def test_description_states_read_only_by_default() -> None:
    assert server.mcp.description is not None
    assert "read-only" in server.mcp.description.lower() or "read-first" in (
        server.mcp.description.lower()
    )


# --------------------------------------------------------------------------
# register_tools() — only what the mode permits
# --------------------------------------------------------------------------


def _fake_specs() -> list[server.ToolSpec]:
    async def _read_fn() -> None:  # pragma: no cover - never called
        return None

    async def _write_fn() -> None:  # pragma: no cover - never called
        return None

    async def _admin_fn() -> None:  # pragma: no cover - never called
        return None

    return [
        server.ToolSpec(fn=_read_fn, name="fake_read", tier=Tier.READ, description="d"),
        server.ToolSpec(fn=_write_fn, name="fake_write", tier=Tier.TIER3, description="d"),
        server.ToolSpec(fn=_admin_fn, name="fake_admin", tier=Tier.TIER2, description="d"),
    ]


async def test_register_tools_in_read_mode_registers_only_read_tier() -> None:
    from mcp.server.mcpserver import MCPServer

    target = MCPServer("test")
    registered = server.register_tools(target, Mode.READ, _fake_specs())
    assert registered == ["fake_read"]
    tools = await target.list_tools()
    assert {t.name for t in tools} == {"fake_read"}


async def test_register_tools_in_write_mode_adds_tier3_but_not_tier2() -> None:
    from mcp.server.mcpserver import MCPServer

    target = MCPServer("test")
    registered = server.register_tools(target, Mode.WRITE, _fake_specs())
    assert set(registered) == {"fake_read", "fake_write"}


async def test_register_tools_in_admin_mode_adds_everything() -> None:
    from mcp.server.mcpserver import MCPServer

    target = MCPServer("test")
    registered = server.register_tools(target, Mode.ADMIN, _fake_specs())
    assert set(registered) == {"fake_read", "fake_write", "fake_admin"}


async def test_the_real_server_registers_only_the_five_read_tools() -> None:
    # Sanity check that the actual module-level registration used
    # Tier.READ for all five and nothing slipped through unfiltered.
    specs = server._TOOL_SPECS  # pyright: ignore[reportPrivateUsage]
    assert {spec.name for spec in specs} == EXPECTED_TOOLS
    assert all(spec.tier is Tier.READ for spec in specs)


# --------------------------------------------------------------------------
# run_startup_checks() — all fatal
# --------------------------------------------------------------------------


async def test_startup_check_exits_when_token_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEGMENT_API_TOKEN", raising=False)
    server._client = None  # pyright: ignore[reportPrivateUsage]
    try:
        with pytest.raises(SystemExit) as exc_info:
            await server.run_startup_checks()
        assert exc_info.value.code == 1
    finally:
        server._client = None  # pyright: ignore[reportPrivateUsage]


async def test_startup_check_exits_on_region_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEGMENT_API_TOKEN", "fake-token")
    monkeypatch.setenv("SEGMENT_REGION", "not-a-region")
    server._client = None  # pyright: ignore[reportPrivateUsage]
    try:
        with pytest.raises(SystemExit) as exc_info:
            await server.run_startup_checks()
        assert exc_info.value.code == 1
    finally:
        server._client = None  # pyright: ignore[reportPrivateUsage]


async def test_startup_check_exits_when_token_fails_to_authenticate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEGMENT_API_TOKEN", "fake-token")
    monkeypatch.setenv("SEGMENT_REGION", "us")
    server._client = None  # pyright: ignore[reportPrivateUsage]
    # Both regions reject the token — verify_region() cannot resolve it
    # to a mismatch, so it re-raises the original auth failure.
    server._client = SegmentPublicAPIClient(  # pyright: ignore[reportPrivateUsage]
        "fake-token",
        Region.US,
        transport=mock_transport("us/unauthorized_401.json"),
        probe_transport=mock_transport("eu/unauthorized_401.json"),
    )
    try:
        with pytest.raises(SystemExit) as exc_info:
            await server.run_startup_checks()
        assert exc_info.value.code == 1
    finally:
        server._client = None  # pyright: ignore[reportPrivateUsage]


async def test_startup_check_exits_with_region_mismatch_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SEGMENT_API_TOKEN", "fake-token")
    monkeypatch.setenv("SEGMENT_REGION", "us")
    server._client = SegmentPublicAPIClient(  # pyright: ignore[reportPrivateUsage]
        "fake-token",
        Region.US,
        transport=mock_transport("us/unauthorized_401.json"),
        probe_transport=mock_transport("eu/workspaces_200.json"),
    )
    try:
        with pytest.raises(SystemExit) as exc_info:
            await server.run_startup_checks()
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "eu" in captured.err
    finally:
        server._client = None  # pyright: ignore[reportPrivateUsage]


async def test_startup_check_exits_with_clear_message_on_free_tier(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SEGMENT_API_TOKEN", "fake-token")
    monkeypatch.setenv("SEGMENT_REGION", "us")
    server._client = SegmentPublicAPIClient(  # pyright: ignore[reportPrivateUsage]
        "fake-token", Region.US, transport=mock_transport("us/free_tier_403.json")
    )
    try:
        with pytest.raises(SystemExit) as exc_info:
            await server.run_startup_checks()
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Team or Business tier" in captured.err
    finally:
        server._client = None  # pyright: ignore[reportPrivateUsage]


async def test_startup_check_succeeds_when_token_and_region_are_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEGMENT_API_TOKEN", "fake-token")
    monkeypatch.setenv("SEGMENT_REGION", "us")
    server._client = SegmentPublicAPIClient(  # pyright: ignore[reportPrivateUsage]
        "fake-token", Region.US, transport=mock_transport("us/workspaces_200.json")
    )
    try:
        await server.run_startup_checks()  # must not raise
    finally:
        server._client = None  # pyright: ignore[reportPrivateUsage]
