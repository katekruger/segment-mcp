"""Entry point for the segment-mcp server.

Builds one `SegmentPublicAPIClient` lazily from `SEGMENT_API_TOKEN` /
`SEGMENT_REGION` and registers the five v0.1 composed tools from
BUILD-PLAN.md §5 against it. All five are read-only — `SEGMENT_MCP_MODE`
gating and the Tier-1 refusal land in Prompt 3 (`modes.py`); there is
nothing to gate yet, since this server has zero write tools.
"""

from __future__ import annotations

import os
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from segment_mcp.client.public_api import SegmentPublicAPIClient
from segment_mcp.client.regions import resolve_region
from segment_mcp.tools._shared import DEFAULT_MAX_ITEMS
from segment_mcp.tools.governance import FindUngovernedSourcesResult
from segment_mcp.tools.governance import find_ungoverned_sources as _find_ungoverned_sources
from segment_mcp.tools.health import (
    CheckDeliveryHealthResult,
    FindStaleSourcesResult,
    Granularity,
)
from segment_mcp.tools.health import check_delivery_health as _check_delivery_health
from segment_mcp.tools.health import find_stale_sources as _find_stale_sources
from segment_mcp.tools.routing import (
    DEFAULT_TRACE_RELATED_SOURCE_CAP,
    AuditEventRoutingResult,
    TraceEventResult,
)
from segment_mcp.tools.routing import audit_event_routing as _audit_event_routing
from segment_mcp.tools.routing import trace_event as _trace_event

mcp = MCPServer("segment_mcp")

# Every tool in this server is a read. The MCP spec DEFAULTS
# destructiveHint to True — omitting it here would declare all five of
# these destructive, which is the opposite of the point. See AGENTS.md.
_READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False)

_client: SegmentPublicAPIClient | None = None


def get_client() -> SegmentPublicAPIClient:
    """The one client instance this server uses, built on first use.

    Not built at import time: importing this module (e.g. in tests) must
    not require SEGMENT_API_TOKEN/SEGMENT_REGION to be set.
    """
    global _client
    if _client is None:
        token = os.environ.get("SEGMENT_API_TOKEN")
        if not token:
            raise RuntimeError(
                "SEGMENT_API_TOKEN is not set. See .env.example — only a "
                "Workspace Owner can mint a Public API token."
            )
        _client = SegmentPublicAPIClient(token, resolve_region())
    return _client


@mcp.tool(name="audit_event_routing", annotations=_READ_ONLY)
async def audit_event_routing(
    source_id: Annotated[
        str | None,
        Field(description="Scope to one source instead of the whole workspace."),
    ] = None,
    max_sources: Annotated[
        int, Field(description="Cap on sources audited when source_id is not given.", ge=1)
    ] = DEFAULT_MAX_ITEMS,
    include_subscriptions: Annotated[
        bool,
        Field(
            description="Fetch destination Subscriptions (Alpha, requires "
            "workspace enablement, 5 req/min). Degrades gracefully if "
            "unavailable — never errors the whole audit."
        ),
    ] = True,
) -> AuditEventRoutingResult:
    """Which destinations get which events — the #1 unanswerable question
    in every Segment workspace. Composes sources, their connected
    destinations, each destination's settings, and (if available) its
    subscriptions into one routing report."""
    return await _audit_event_routing(
        get_client(),
        source_id=source_id,
        max_sources=max_sources,
        include_subscriptions=include_subscriptions,
    )


@mcp.tool(name="trace_event", annotations=_READ_ONLY)
async def trace_event(
    event_name: Annotated[str, Field(description="Exact event name, e.g. 'Order Completed'.")],
    source_id: Annotated[
        str | None, Field(description="Scope destination/warehouse lookup to one source.")
    ] = None,
    emission_window_days: Annotated[
        int, Field(description="Lookback window for confirming actual emission.", ge=1)
    ] = 14,
    max_related_sources: Annotated[
        int,
        Field(
            description="Cap on emitting sources to expand into destinations/"
            "warehouses when source_id isn't given.",
            ge=1,
        ),
    ] = DEFAULT_TRACE_RELATED_SOURCE_CAP,
) -> TraceEventResult:
    """Given an event name: which sources actually emit it, whether it's
    in a tracking plan (an event in NO plan is reported as "governed by
    nothing", not an empty result), and which destinations/warehouses it
    reaches."""
    return await _trace_event(
        get_client(),
        event_name=event_name,
        source_id=source_id,
        emission_window_days=emission_window_days,
        max_related_sources=max_related_sources,
    )


@mcp.tool(name="find_stale_sources", annotations=_READ_ONLY)
async def find_stale_sources(
    source_id: Annotated[str | None, Field(description="Check one source instead of all.")] = None,
    recent_days: Annotated[
        int, Field(description="A source with activity in this window is active.", ge=1)
    ] = 30,
    historical_days: Annotated[
        int,
        Field(
            description="Wider window checked only for sources with zero "
            "recent activity, to tell 'went dead' apart from 'too new to "
            "tell'. Must exceed recent_days.",
            ge=1,
        ),
    ] = 90,
    max_sources: Annotated[int, Field(description="Cap when source_id isn't given.", ge=1)] = (
        DEFAULT_MAX_ITEMS
    ),
) -> FindStaleSourcesResult:
    """Which sources have no recent data — abandoned instrumentation and
    unnecessary MTU spend — distinguished from sources that are simply
    too new to judge (the Public API exposes no source creation date)."""
    return await _find_stale_sources(
        get_client(),
        source_id=source_id,
        recent_days=recent_days,
        historical_days=historical_days,
        max_sources=max_sources,
    )


@mcp.tool(name="check_delivery_health", annotations=_READ_ONLY)
async def check_delivery_health(
    destination_id: Annotated[str, Field(description="The destination to check.")],
    source_id: Annotated[str, Field(description="Required by the delivery-metrics endpoint.")],
    granularity: Annotated[
        Granularity,
        Field(
            description="MINUTE (max 4h range, data ≤48h old), HOUR (max 7d, "
            "data ≤7d old), or DAY (max 14d, data ≤14d old). A wider request "
            "is capped, not silently returned as if it were complete."
        ),
    ] = "DAY",
    start_time: Annotated[str | None, Field(description="ISO 8601. Defaults per granularity.")] = (
        None
    ),
    end_time: Annotated[str | None, Field(description="ISO 8601. Defaults to now.")] = None,
) -> CheckDeliveryHealthResult:
    """Is this destination silently failing? The thing that quietly
    breaks attribution for a quarter before anyone notices."""
    return await _check_delivery_health(
        get_client(),
        destination_id=destination_id,
        source_id=source_id,
        granularity=granularity,
        start_time=start_time,
        end_time=end_time,
    )


@mcp.tool(name="find_ungoverned_sources", annotations=_READ_ONLY)
async def find_ungoverned_sources(
    source_id: Annotated[str | None, Field(description="Check one source instead of all.")] = None,
    max_sources: Annotated[int, Field(description="Cap when source_id isn't given.", ge=1)] = (
        DEFAULT_MAX_ITEMS
    ),
    max_tracking_plans: Annotated[
        int, Field(description="Cap on tracking plans checked.", ge=1)
    ] = DEFAULT_MAX_ITEMS,
) -> FindUngovernedSourcesResult:
    """Which sources are governed by no tracking plan at all, and which
    are governed but still ALLOWING unplanned events through — directly
    actionable schema-settings gaps, not just a report."""
    return await _find_ungoverned_sources(
        get_client(),
        source_id=source_id,
        max_sources=max_sources,
        max_tracking_plans=max_tracking_plans,
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
