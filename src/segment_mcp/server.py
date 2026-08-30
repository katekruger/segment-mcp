"""Entry point for the segment-mcp server.

Builds one `SegmentPublicAPIClient` lazily from `SEGMENT_API_TOKEN` /
`SEGMENT_REGION`, runs fatal startup checks against it, and registers
only the tools `SEGMENT_MCP_MODE` actually reaches — every v0.1 tool is
`Tier.READ`, reachable in every mode, so nothing is filtered out yet, but
the mechanism is real and tested now rather than bolted on when the
first `write`/`admin` tool lands (see `modes.py`).

`main()` parses `sys.argv` before running the fatal startup checks —
`segment-mcp --help`/`--version` exit 0 with no configuration and no
network call, via argparse's own built-in handling of those flags.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from segment_mcp import __version__
from segment_mcp.client.public_api import SegmentPublicAPIClient
from segment_mcp.client.regions import resolve_region
from segment_mcp.modes import Mode, Tier, is_tier_reachable, resolve_mode
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

# Every tool in this server is a read. The MCP spec DEFAULTS
# destructiveHint to True — omitting it here would declare all five of
# these destructive, which is the opposite of the point. See AGENTS.md.
_READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False)

_MODE = resolve_mode()


def _mode_instructions(mode: Mode) -> str:
    """The three-tier model, in the text a connecting client sees at
    initialize time — not just documented in README.md."""
    return (
        f"segment-mcp is running in {mode.value!r} mode "
        f"(SEGMENT_MCP_MODE={mode.value}).\n\n"
        "Modes: read (default) -> write -> admin. Every tool currently "
        "registered is Tier.READ and is reachable in all three modes — "
        "this server ships zero write tools in v0.1, by design, not as a "
        "temporary limitation.\n\n"
        "Tier 1 (creating a data-deletion/suppression regulation) is "
        "permanently unreachable, in every mode, with no path to enable "
        "it — see docs/what-this-refuses-to-do.md. Tier 2 (deletes) will "
        "require admin mode plus a typed confirmation naming the exact "
        "resource. Tier 3 (replace-semantics changes) will require write "
        "mode plus echoing the pending change back for confirmation "
        "before it executes. Neither exists as a callable tool yet."
    )


mcp = MCPServer(
    "segment_mcp",
    description=(
        "Read-first MCP server for Twilio Segment. Answers which "
        "destinations get which events, which sources are dead, and "
        "which are governed by nothing. Read-only by default; data "
        "deletion is not exposed at all."
    ),
    instructions=_mode_instructions(_MODE),
    version=__version__,
)

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


async def run_startup_checks() -> None:
    """Fatal, in order: SEGMENT_REGION is set and valid (`get_client()` ->
    `resolve_region()`), the token is present (`get_client()`) and
    actually authenticates against that region (`verify_region()`), and
    the workspace's tier supports the Public API — a Free-tier 403
    surfaces through `verify_region()`'s existing `SegmentTierError`
    classification as a clear message, not a raw 403.

    Every error these three checks can raise in this codebase derives
    from `RuntimeError` by design (`RegionConfigError`, `RegionMismatchError`,
    the plain `RuntimeError` from a missing token, and every
    `SegmentAPIError` subclass) — catching it here, printing it, and
    exiting is deliberate: a misconfigured server should fail loudly at
    startup, not on whatever tool call happens to run first.
    """
    try:
        client = get_client()
        await client.verify_region()
    except RuntimeError as exc:
        print(f"segment-mcp startup check failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


@dataclass(frozen=True, slots=True)
class ToolSpec:
    fn: Callable[..., Any]
    name: str
    tier: Tier
    description: str


_TOOL_SPECS: list[ToolSpec] = []


def _tool(
    tier: Tier, name: str, description: str
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Collects a tool for conditional registration instead of decorating
    it onto `mcp` immediately — see `register_tools()`."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        _TOOL_SPECS.append(ToolSpec(fn=fn, name=name, tier=tier, description=description))
        return fn

    return decorator


@_tool(
    Tier.READ,
    "audit_event_routing",
    "Which destinations get which events — the #1 unanswerable question "
    "in every Segment workspace. Composes sources, their connected "
    "destinations, each destination's settings, and (if available) its "
    "subscriptions into one routing report.",
)
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
    return await _audit_event_routing(
        get_client(),
        source_id=source_id,
        max_sources=max_sources,
        include_subscriptions=include_subscriptions,
    )


@_tool(
    Tier.READ,
    "trace_event",
    "Given an event name: which sources actually emit it, whether it's "
    'in a tracking plan (an event in NO plan is reported as "governed by '
    'nothing", not an empty result), and which destinations/warehouses '
    "it reaches.",
)
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
    return await _trace_event(
        get_client(),
        event_name=event_name,
        source_id=source_id,
        emission_window_days=emission_window_days,
        max_related_sources=max_related_sources,
    )


@_tool(
    Tier.READ,
    "find_stale_sources",
    "Which sources have no recent data — abandoned instrumentation and "
    "unnecessary MTU spend — distinguished from sources that are simply "
    "too new to judge (the Public API exposes no source creation date).",
)
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
    return await _find_stale_sources(
        get_client(),
        source_id=source_id,
        recent_days=recent_days,
        historical_days=historical_days,
        max_sources=max_sources,
    )


@_tool(
    Tier.READ,
    "check_delivery_health",
    "Is this destination silently failing? The thing that quietly "
    "breaks attribution for a quarter before anyone notices.",
)
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
    return await _check_delivery_health(
        get_client(),
        destination_id=destination_id,
        source_id=source_id,
        granularity=granularity,
        start_time=start_time,
        end_time=end_time,
    )


@_tool(
    Tier.READ,
    "find_ungoverned_sources",
    "Which sources are governed by no tracking plan at all, and which "
    "are governed but still ALLOWING unplanned events through — directly "
    "actionable schema-settings gaps, not just a report.",
)
async def find_ungoverned_sources(
    source_id: Annotated[str | None, Field(description="Check one source instead of all.")] = None,
    max_sources: Annotated[int, Field(description="Cap when source_id isn't given.", ge=1)] = (
        DEFAULT_MAX_ITEMS
    ),
    max_tracking_plans: Annotated[
        int, Field(description="Cap on tracking plans checked.", ge=1)
    ] = DEFAULT_MAX_ITEMS,
) -> FindUngovernedSourcesResult:
    return await _find_ungoverned_sources(
        get_client(),
        source_id=source_id,
        max_sources=max_sources,
        max_tracking_plans=max_tracking_plans,
    )


def register_tools(target: MCPServer, mode: Mode, specs: list[ToolSpec] | None = None) -> list[str]:
    """Register only the tools whose tier `mode` actually reaches.
    Returns the names registered — used by tests to assert on the
    mechanism without needing a real Tier.WRITE/ADMIN tool to exist yet.
    """
    registered: list[str] = []
    for spec in specs if specs is not None else _TOOL_SPECS:
        if not is_tier_reachable(spec.tier, mode):
            continue
        # Every tool registered so far is Tier.READ, so _READ_ONLY is
        # correct for all of them. When the first Tier.TIER2/TIER3 tool
        # lands, its annotations (destructiveHint: true for a delete,
        # idempotentHint, etc.) need defining here instead of reusing this.
        target.add_tool(
            spec.fn,
            name=spec.name,
            description=spec.description,
            annotations=_READ_ONLY,
        )
        registered.append(spec.name)
    return registered


register_tools(mcp, _MODE)


def _build_arg_parser() -> argparse.ArgumentParser:
    """`--help`/`--version` must work with zero configuration: no
    SEGMENT_API_TOKEN, no SEGMENT_REGION, no network call. `main()` used
    to ignore `sys.argv` entirely and run the fatal startup checks
    unconditionally, so `segment-mcp --help` died with a config error
    instead of printing usage — the first thing anyone types after
    installing. Parsing args (and letting argparse's built-in `--help`/
    `--version` actions exit 0 on their own) happens before
    `run_startup_checks()` is ever reached."""
    parser = argparse.ArgumentParser(
        prog="segment-mcp",
        description=(
            "Read-first MCP server for Twilio Segment. Configuration is via "
            "environment variables — see .env.example."
        ),
    )
    parser.add_argument("--version", action="version", version=f"segment-mcp {__version__}")
    return parser


def main() -> None:
    _build_arg_parser().parse_args()
    asyncio.run(run_startup_checks())
    mcp.run()


if __name__ == "__main__":
    main()
