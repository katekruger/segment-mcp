"""Shared building blocks for the composed tools in this package.

Resource shapes here (Source, Destination, TrackingPlan, Rule, event-volume
fields) were verified against docs.segmentapis.com during Prompt 2 — see
each tool module's docstring for the specific pages checked. This module
exists because `audit_event_routing`, `find_stale_sources`,
`find_ungoverned_sources`, and `trace_event` all need to list sources
and/or tracking plans; extracting the shared fetch/bound/report pattern
here keeps each tool file focused on its own composition. See AGENTS.md's
"never auto-generate tools from the OpenAPI spec — hand-pick and compose"
rule: these are hand-picked, minimally-typed views onto the raw API
objects, not a generated client.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from pydantic import BaseModel, Field

from segment_mcp.client.public_api import SegmentPublicAPIClient


def as_dict(value: object) -> dict[str, Any]:
    """Narrow `object` to `dict[str, Any]`, or `{}` if it isn't one."""
    if isinstance(value, dict):
        untyped = cast("dict[object, object]", value)
        if all(isinstance(key, str) for key in untyped):
            return cast("dict[str, Any]", value)
    return {}


def as_dict_list(value: object) -> list[dict[str, Any]]:
    """Narrow `object` to a list of string-keyed dicts, dropping anything
    else in the list — a defensively-typed raw API array."""
    if not isinstance(value, list):
        return []
    untyped_items = cast("list[object]", value)
    return [parsed for item in untyped_items if (parsed := as_dict(item))]


# Bounds applied by default when a tool would otherwise have to walk an
# unbounded workspace. BUILD-PLAN.md §5 calls this out explicitly for
# audit_event_routing ("a workspace with 200 sources -> paginate, and
# respect the rate limiter. Warn if the full audit will take more than
# ~30s and offer a filtered scope") — this project measures the proxy
# (item count) rather than wall-clock, since wall-clock isn't
# deterministic or testable; capping the item count bounds the work the
# same way and the resulting note tells the user exactly what to do
# about it (pass a narrower scope).
DEFAULT_MAX_ITEMS = 25


class ScopedList(BaseModel):
    """The common "bounded fetch" result shape: how many items are in
    this answer, how many exist in total (if known), and whether the
    workspace has more than this tool looked at."""

    items_in_scope: int
    total_in_workspace: int | None = Field(
        default=None,
        description="Total item count Segment reported, or null if a full "
        "count wasn't available (e.g. scoped to a single explicit ID).",
    )
    truncated: bool = Field(
        description="True if the workspace has more items than were fetched — "
        "the answer below is a partial view, not the complete workspace."
    )
    scope_note: str | None = Field(
        default=None,
        description="Set when truncated, or when scoped to a single ID: "
        "explains what was (and wasn't) covered and how to narrow or widen it.",
    )


class Gap(BaseModel):
    """A piece of data this tool tried to get but couldn't — a degraded
    part of an otherwise-successful answer, not a tool failure. Compose
    into a response's `gaps` list rather than raising, per BUILD-PLAN.md
    §5's "degrade gracefully... do not error" instruction for Destination
    Subscriptions (Alpha, requires workspace enablement)."""

    area: str = Field(description="What this gap is about, e.g. 'destination subscriptions'.")
    reason: str = Field(description="Why the data is missing, in plain language.")


def utc_now() -> datetime:
    return datetime.now(UTC)


def iso8601(when: datetime) -> str:
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def days_ago(days: int, *, now: datetime | None = None) -> datetime:
    return (now or utc_now()) - timedelta(days=days)


class SourceSummary(BaseModel):
    """The subset of the Source object (GET /sources, GET /sources/{id})
    every tool in this package actually needs. Full shape per Segment's
    docs also includes writeKeys, metadata, settings, labels — omitted
    here since no tool reads them; add fields only when a tool needs them,
    per AGENTS.md's "hand-pick, don't auto-generate" rule."""

    id: str
    slug: str
    name: str
    enabled: bool


def parse_source_summary(raw: dict[str, Any]) -> SourceSummary:
    return SourceSummary(
        id=str(raw.get("id", "")),
        slug=str(raw.get("slug", "")),
        name=str(raw.get("name", raw.get("slug", raw.get("id", "")))),
        enabled=bool(raw.get("enabled", False)),
    )


async def list_sources_scoped(
    client: SegmentPublicAPIClient,
    *,
    source_id: str | None,
    max_items: int = DEFAULT_MAX_ITEMS,
) -> tuple[list[SourceSummary], ScopedList]:
    """Fetch sources, either one explicit source or a bounded page of the
    workspace. Shared by every tool that starts from "the sources".
    """
    if source_id is not None:
        data = await client.get_data(f"/sources/{source_id}")
        raw_source = as_dict(data.get("source"))
        if not raw_source:
            return [], ScopedList(items_in_scope=0, truncated=False, scope_note=None)
        source = parse_source_summary(raw_source)
        return [source], ScopedList(items_in_scope=1, total_in_workspace=1, truncated=False)

    # A single bounded page, not paginate() — the first page's own
    # `pagination.totalEntries` already tells us the workspace total, so
    # there's no need for a second call just to learn the count.
    data = await client.get_data("/sources", params={"pagination.count": max_items})
    sources = [parse_source_summary(item) for item in as_dict_list(data.get("sources"))]
    pagination = as_dict(data.get("pagination"))
    raw_total = pagination.get("totalEntries")
    total = raw_total if isinstance(raw_total, int) else None
    truncated = total is not None and total > len(sources)
    scope = ScopedList(
        items_in_scope=len(sources),
        total_in_workspace=total,
        truncated=truncated,
        scope_note=(
            f"Audited {len(sources)} of {total} sources in this workspace. "
            f"Pass source_id to scope to one source, or re-run — this tool "
            f"always caps a whole-workspace scan at {max_items} sources per call."
            if truncated
            else None
        ),
    )
    return sources, scope


class TrackingPlanSummary(BaseModel):
    id: str
    name: str
    slug: str


def parse_tracking_plan_summary(raw: dict[str, Any]) -> TrackingPlanSummary:
    return TrackingPlanSummary(
        id=str(raw.get("id", "")),
        name=str(raw.get("name", raw.get("slug", raw.get("id", "")))),
        slug=str(raw.get("slug", "")),
    )


async def list_tracking_plans(
    client: SegmentPublicAPIClient, *, max_items: int = DEFAULT_MAX_ITEMS
) -> list[TrackingPlanSummary]:
    raw_items, _truncated = await client.paginate(
        "/tracking-plans", items_key="trackingPlans", page_size=max_items, max_pages=1
    )
    return [parse_tracking_plan_summary(item) for item in raw_items]


class EventVolumePoint(BaseModel):
    source_id: str
    count: int


async def event_volume_by_source(
    client: SegmentPublicAPIClient,
    *,
    start: datetime,
    end: datetime,
    event_name: str | None = None,
    source_ids: list[str] | None = None,
    granularity: str = "DAY",
) -> list[EventVolumePoint]:
    """`GET /events/volume`, grouped by source, summed across the window.

    Verified endpoint (docs.segmentapis.com, Monitoring > Events tag,
    Prompt 2 research): required `granularity`/`startTime`/`endTime`,
    optional `eventName`/`sourceId` array filters, `groupBy` including
    `source`. Response nests results under `data.result[].series[]`, one
    result entry per `groupBy` combination.

    Assumption not directly confirmed by the fetched docs excerpt: that
    grouping by `source` puts the source ID on each result entry under a
    `source` key. If a live call shows a different key (e.g. `sourceId`,
    or nested under a `groupBy` object), fix it here — every caller in
    this package goes through this one function.
    """
    params: dict[str, Any] = {
        "granularity": granularity,
        "startTime": iso8601(start),
        "endTime": iso8601(end),
        "groupBy": "source",
    }
    if event_name is not None:
        params["eventName"] = event_name
    if source_ids:
        params["sourceId"] = source_ids

    data = await client.get_data("/events/volume", params=params)

    points: list[EventVolumePoint] = []
    for entry in as_dict_list(data.get("result")):
        source_id = entry.get("source")
        if not isinstance(source_id, str):
            continue
        total = 0
        for point in as_dict_list(entry.get("series")):
            count = point.get("count")
            if isinstance(count, int):
                total += count
        points.append(EventVolumePoint(source_id=source_id, count=total))
    return points
