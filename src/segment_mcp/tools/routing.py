"""`audit_event_routing` and `trace_event` — BUILD-PLAN.md §5.

Endpoint shapes verified against docs.segmentapis.com during Prompt 2:
Sources (`connected-destinations`, `connected-warehouses`), Destinations
(`GET /destinations/{id}`, `GET /destinations/{id}/subscriptions` —
confirmed Alpha, workspace-enablement-gated), Tracking Plans (`rules`,
whose event name lives on the `key` field, not `eventName`).

Subscription `trigger` objects and destination `settings`/filter DSLs are
passed through as raw dicts rather than modeled precisely — their exact
shape wasn't pinned down by the docs fetched for this prompt, and
BUILD-PLAN.md §0.3 is explicit that this project hand-picks and composes
rather than guessing at endpoint internals it hasn't verified.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from segment_mcp.client.public_api import SegmentAPIError, SegmentPublicAPIClient
from segment_mcp.tools._shared import (
    DEFAULT_MAX_ITEMS,
    Gap,
    ScopedList,
    as_dict,
    days_ago,
    event_volume_by_source,
    list_sources_scoped,
    list_tracking_plans,
    utc_now,
)

# --------------------------------------------------------------------------
# audit_event_routing
# --------------------------------------------------------------------------


class SubscriptionSummary(BaseModel):
    id: str
    name: str | None = None
    action_id: str | None = None
    enabled: bool = True
    trigger: dict[str, Any] | None = None


class DestinationRouting(BaseModel):
    id: str
    name: str
    enabled: bool
    settings: dict[str, Any] = Field(default_factory=dict[str, Any])
    subscriptions: list[SubscriptionSummary] | None = Field(
        default=None,
        description="Null only when Subscriptions was unavailable for this "
        "destination (Alpha, requires workspace enablement) — see the "
        "top-level subscriptions_available flag and gaps list. An empty "
        "list means the call succeeded and there genuinely are none.",
    )


class SourceRouting(BaseModel):
    id: str
    slug: str
    name: str
    enabled: bool
    has_no_connected_destinations: bool
    destinations: list[DestinationRouting] = Field(default_factory=list[DestinationRouting])


class AuditEventRoutingResult(BaseModel):
    region: str
    scope: ScopedList
    subscriptions_available: bool = Field(
        description="False once the Subscriptions endpoint has failed once — "
        "it's Alpha and 5 req/min, so this tool stops calling it for the "
        "rest of the audit rather than repeatedly hitting an endpoint "
        "that's already told us it won't work."
    )
    routing: list[SourceRouting] = Field(default_factory=list[SourceRouting])
    gaps: list[Gap] = Field(default_factory=list[Gap])


async def audit_event_routing(
    client: SegmentPublicAPIClient,
    *,
    source_id: str | None = None,
    max_sources: int = DEFAULT_MAX_ITEMS,
    include_subscriptions: bool = True,
) -> AuditEventRoutingResult:
    """Which destinations get which events — the #1 unanswerable question
    in every Segment workspace.

    Composes `GET /sources` -> `GET /sources/{id}/connected-destinations`
    -> `GET /destinations/{id}` (settings) -> `GET /destinations/{id}/subscriptions`.
    Degrades gracefully when Subscriptions is unavailable (Alpha feature,
    not error) rather than failing the whole audit.
    """
    sources, scope = await list_sources_scoped(client, source_id=source_id, max_items=max_sources)

    gaps: list[Gap] = []
    subscriptions_available = include_subscriptions
    subscriptions_probed = False

    routing: list[SourceRouting] = []
    for source in sources:
        try:
            raw_destinations, _truncated = await client.paginate(
                f"/sources/{source.id}/connected-destinations",
                items_key="destinations",
                page_size=200,
            )
        except SegmentAPIError as exc:
            gaps.append(Gap(area=f"source {source.id} connected destinations", reason=str(exc)))
            raw_destinations = []

        destinations: list[DestinationRouting] = []
        for raw_destination in raw_destinations:
            destination_id = raw_destination.get("id")
            if not isinstance(destination_id, str):
                continue
            destinations.append(
                await _describe_destination(
                    client,
                    destination_id,
                    include_subscriptions=subscriptions_available,
                    subscriptions_probed=subscriptions_probed,
                    gaps=gaps,
                )
            )
            if include_subscriptions and not subscriptions_probed:
                subscriptions_probed = True
                # If the very first attempt failed, _describe_destination
                # already recorded the gap and returned subscriptions=None;
                # stop trying for every remaining destination.
                if destinations[-1].subscriptions is None:
                    subscriptions_available = False

        routing.append(
            SourceRouting(
                id=source.id,
                slug=source.slug,
                name=source.name,
                enabled=source.enabled,
                has_no_connected_destinations=len(destinations) == 0,
                destinations=destinations,
            )
        )

    return AuditEventRoutingResult(
        region=client.region.value,
        scope=scope,
        subscriptions_available=subscriptions_available,
        routing=routing,
        gaps=gaps,
    )


async def _describe_destination(
    client: SegmentPublicAPIClient,
    destination_id: str,
    *,
    include_subscriptions: bool,
    subscriptions_probed: bool,
    gaps: list[Gap],
) -> DestinationRouting:
    name = destination_id
    enabled = True
    settings: dict[str, Any] = {}
    try:
        data = await client.get_data(f"/destinations/{destination_id}")
        destination = as_dict(data.get("destination"))
        name = str(destination.get("name", destination_id))
        enabled = bool(destination.get("enabled", True))
        settings = as_dict(destination.get("settings"))
    except SegmentAPIError as exc:
        gaps.append(Gap(area=f"destination {destination_id} details", reason=str(exc)))

    subscriptions: list[SubscriptionSummary] | None = None
    if include_subscriptions:
        try:
            raw_subs, _truncated = await client.paginate(
                f"/destinations/{destination_id}/subscriptions",
                items_key="subscriptions",
                page_size=100,
            )
            subscriptions = [
                SubscriptionSummary(
                    id=str(sub.get("id", "")),
                    name=sub.get("name") if isinstance(sub.get("name"), str) else None,
                    action_id=sub.get("actionId") if isinstance(sub.get("actionId"), str) else None,
                    enabled=bool(sub.get("enabled", True)),
                    trigger=as_dict(sub.get("trigger")) or None,
                )
                for sub in raw_subs
            ]
        except SegmentAPIError as exc:
            if not subscriptions_probed:
                gaps.append(
                    Gap(
                        area="destination subscriptions",
                        reason=(
                            f"Unavailable — Subscriptions is an Alpha endpoint that "
                            f"requires workspace enablement ({exc}). Routing is "
                            f"reported without subscription-level detail."
                        ),
                    )
                )

    return DestinationRouting(
        id=destination_id,
        name=name,
        enabled=enabled,
        settings=settings,
        subscriptions=subscriptions,
    )


# --------------------------------------------------------------------------
# trace_event
# --------------------------------------------------------------------------

DEFAULT_TRACE_RELATED_SOURCE_CAP = 5


class TrackingPlanCoverage(BaseModel):
    tracking_plan_id: str
    tracking_plan_name: str
    rule_type: str | None = None
    rule_deprecated: bool = False


class EmittingSource(BaseModel):
    source_id: str
    event_count: int


class ConnectedDestination(BaseModel):
    source_id: str
    destination_id: str
    destination_name: str
    destination_enabled: bool


class ConnectedWarehouse(BaseModel):
    source_id: str
    warehouse_id: str


class TraceEventResult(BaseModel):
    event_name: str
    region: str
    governed: bool
    governance_note: str
    tracking_plans: list[TrackingPlanCoverage] = Field(default_factory=list[TrackingPlanCoverage])
    emission_window_days: int
    emitting_sources: list[EmittingSource] = Field(default_factory=list[EmittingSource])
    emission_confirmed: bool
    destinations: list[ConnectedDestination] = Field(default_factory=list[ConnectedDestination])
    warehouses: list[ConnectedWarehouse] = Field(default_factory=list[ConnectedWarehouse])
    destination_warehouse_scope_note: str | None = None
    gaps: list[Gap] = Field(default_factory=list[Gap])


async def trace_event(
    client: SegmentPublicAPIClient,
    *,
    event_name: str,
    source_id: str | None = None,
    emission_window_days: int = 14,
    max_tracking_plans: int = DEFAULT_MAX_ITEMS,
    max_related_sources: int = DEFAULT_TRACE_RELATED_SOURCE_CAP,
) -> TraceEventResult:
    """Given an event name: which sources emit it, is it in a tracking
    plan, which sources does that plan cover, and which destinations and
    warehouses does it reach.

    An event in no tracking plan is the interesting answer, not an empty
    result — `governed=False` and `governance_note` say so explicitly.
    """
    tracking_plans = await list_tracking_plans(client, max_items=max_tracking_plans)
    gaps: list[Gap] = []

    coverage: list[TrackingPlanCoverage] = []
    for plan in tracking_plans:
        try:
            rules, _truncated = await client.paginate(
                f"/tracking-plans/{plan.id}/rules", items_key="rules", page_size=200
            )
        except SegmentAPIError as exc:
            gaps.append(Gap(area=f"tracking plan {plan.id} rules", reason=str(exc)))
            continue
        for rule in rules:
            if rule.get("key") == event_name:
                coverage.append(
                    TrackingPlanCoverage(
                        tracking_plan_id=plan.id,
                        tracking_plan_name=plan.name,
                        rule_type=rule.get("type") if isinstance(rule.get("type"), str) else None,
                        rule_deprecated=rule.get("deprecatedAt") is not None,
                    )
                )

    governed = len(coverage) > 0
    governance_note = (
        f"Governed by {len(coverage)} tracking plan(s)."
        if governed
        else "This event appears in no tracking plan. It is governed by nothing."
    )

    now = utc_now()
    volume_points = await event_volume_by_source(
        client,
        start=days_ago(emission_window_days, now=now),
        end=now,
        event_name=event_name,
        source_ids=[source_id] if source_id else None,
    )
    emitting = [
        EmittingSource(source_id=point.source_id, event_count=point.count)
        for point in volume_points
        if point.count > 0
    ]

    scope_note: str | None = None
    if source_id is not None:
        scoped_source_ids = [source_id]
    elif emitting and len(emitting) <= max_related_sources:
        scoped_source_ids = [source.source_id for source in emitting]
    elif emitting:
        scoped_source_ids = []
        scope_note = (
            f"{len(emitting)} sources emit this event — more than the "
            f"{max_related_sources}-source cap for destination/warehouse "
            f"lookup. Pass source_id to inspect one of them."
        )
    else:
        scoped_source_ids = []
        scope_note = (
            "No source emitted this event in the last "
            f"{emission_window_days} day(s), so there's nothing to scope "
            "destination/warehouse lookup to. Pass source_id to check a "
            "specific source regardless of confirmed emission."
        )

    destinations: list[ConnectedDestination] = []
    warehouses: list[ConnectedWarehouse] = []
    for scoped_id in scoped_source_ids:
        try:
            raw_destinations, _truncated = await client.paginate(
                f"/sources/{scoped_id}/connected-destinations", items_key="destinations"
            )
            for raw in raw_destinations:
                destination_id = raw.get("id")
                if isinstance(destination_id, str):
                    destinations.append(
                        ConnectedDestination(
                            source_id=scoped_id,
                            destination_id=destination_id,
                            destination_name=str(raw.get("name", destination_id)),
                            destination_enabled=bool(raw.get("enabled", True)),
                        )
                    )
        except SegmentAPIError as exc:
            gaps.append(Gap(area=f"source {scoped_id} connected destinations", reason=str(exc)))

        try:
            raw_warehouses, _truncated = await client.paginate(
                f"/sources/{scoped_id}/connected-warehouses", items_key="warehouses"
            )
            for raw in raw_warehouses:
                warehouse_id = raw.get("id")
                if isinstance(warehouse_id, str):
                    warehouses.append(
                        ConnectedWarehouse(source_id=scoped_id, warehouse_id=warehouse_id)
                    )
        except SegmentAPIError as exc:
            gaps.append(Gap(area=f"source {scoped_id} connected warehouses", reason=str(exc)))

    return TraceEventResult(
        event_name=event_name,
        region=client.region.value,
        governed=governed,
        governance_note=governance_note,
        tracking_plans=coverage,
        emission_window_days=emission_window_days,
        emitting_sources=emitting,
        emission_confirmed=len(emitting) > 0,
        destinations=destinations,
        warehouses=warehouses,
        destination_warehouse_scope_note=scope_note,
        gaps=gaps,
    )
