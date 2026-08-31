"""`find_stale_sources` and `check_delivery_health` — BUILD-PLAN.md §5.

Endpoint shapes verified against docs.segmentapis.com during Prompt 2
research (not in BUILD-PLAN.md's own endpoint inventory, which doesn't
list Usage/Monitoring endpoints at all):

- Monitoring > Events: `GET /events/volume` — see
  `segment_mcp.tools._shared.event_volume_by_source`.
- Destinations: `GET /destinations/{id}/delivery-metrics` — required
  `sourceId`, optional `startTime`/`endTime`/`granularity`
  (MINUTE/HOUR/DAY). **This client enforces the real documented caps,
  which differ from BUILD-PLAN.md's "30-day window max"**: MINUTE allows
  at most a 4-hour range with data no older than 48 hours; HOUR allows at
  most 7 days with data no older than 7 days; DAY allows at most 14 days
  with data no older than 14 days. BUILD-PLAN's flat "30 days" doesn't
  match any of the three granularities — implemented against the verified
  API instead of the plan text; see this PR's description for the
  discrepancy.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from segment_mcp.client.public_api import SegmentAPIError, SegmentPublicAPIClient
from segment_mcp.client.validation import validate_resource_id
from segment_mcp.tools._shared import (
    DEFAULT_MAX_ITEMS,
    Gap,
    ScopedList,
    as_dict,
    as_dict_list,
    days_ago,
    event_volume_by_source,
    iso8601,
    list_sources_scoped,
    utc_now,
)

# --------------------------------------------------------------------------
# find_stale_sources
# --------------------------------------------------------------------------

SourceStatus = Literal["active", "stale", "insufficient_data"]

_CREATION_DATE_NOTE = (
    "The Public API does not expose a source creation date (verified "
    "against docs.segmentapis.com — no createdAt/created_at field on the "
    "Source object). A source with zero activity in the entire queried "
    "window is reported as insufficient_data, not stale: it may be a "
    "source created after the window started, and this tool has no way "
    "to tell the two apart."
)


class SourceActivity(BaseModel):
    source_id: str
    source_name: str
    status: SourceStatus
    recent_event_count: int = Field(description="Total events in the recent window.")
    historical_event_count: int | None = Field(
        default=None,
        description="Total events in the historical window, only queried for "
        "sources with zero recent activity. Null if not queried (recent "
        "activity was already nonzero).",
    )


class FindStaleSourcesResult(BaseModel):
    region: str
    recent_window_days: int
    historical_window_days: int
    scope: ScopedList
    active_count: int
    stale: list[SourceActivity]
    insufficient_data: list[SourceActivity]
    note_on_source_age: str = _CREATION_DATE_NOTE


async def find_stale_sources(
    client: SegmentPublicAPIClient,
    *,
    source_id: str | None = None,
    recent_days: int = 30,
    historical_days: int = 90,
    max_sources: int = DEFAULT_MAX_ITEMS,
) -> FindStaleSourcesResult:
    """Which sources have no recent data — abandoned instrumentation and
    unnecessary MTU spend, distinguished from sources that are simply new.

    Composes `GET /sources` (or one source), `GET /events/volume` for the
    recent window, and — only for sources with zero recent activity —
    `GET /events/volume` again for a longer historical window, to tell
    "went dead" (stale) apart from "we can't confirm either way" (see
    `note_on_source_age`).
    """
    if historical_days <= recent_days:
        raise ValueError("historical_days must be greater than recent_days")

    sources, scope = await list_sources_scoped(client, source_id=source_id, max_items=max_sources)
    now = utc_now()

    recent_points = await event_volume_by_source(
        client, start=days_ago(recent_days, now=now), end=now, source_ids=[s.id for s in sources]
    )
    recent_by_source = {point.source_id: point.count for point in recent_points}

    zero_recent_ids = [source.id for source in sources if recent_by_source.get(source.id, 0) == 0]
    historical_by_source: dict[str, int] = {}
    if zero_recent_ids:
        historical_points = await event_volume_by_source(
            client,
            start=days_ago(historical_days, now=now),
            end=days_ago(recent_days, now=now),
            source_ids=zero_recent_ids,
        )
        historical_by_source = {point.source_id: point.count for point in historical_points}

    active_count = 0
    stale: list[SourceActivity] = []
    insufficient: list[SourceActivity] = []
    for source in sources:
        recent = recent_by_source.get(source.id, 0)
        if recent > 0:
            active_count += 1
            continue
        historical = historical_by_source.get(source.id, 0)
        activity = SourceActivity(
            source_id=source.id,
            source_name=source.name,
            status="stale" if historical > 0 else "insufficient_data",
            recent_event_count=recent,
            historical_event_count=historical,
        )
        (stale if historical > 0 else insufficient).append(activity)

    return FindStaleSourcesResult(
        region=client.region.value,
        recent_window_days=recent_days,
        historical_window_days=historical_days,
        scope=scope,
        active_count=active_count,
        stale=stale,
        insufficient_data=insufficient,
    )


# --------------------------------------------------------------------------
# check_delivery_health
# --------------------------------------------------------------------------

Granularity = Literal["MINUTE", "HOUR", "DAY"]

# (max range for one request, max age of the oldest available data point),
# both in hours. Verified against docs.segmentapis.com's Destinations tag
# (Prompt 2 research) — NOT the flat 30-day figure in BUILD-PLAN.md §5.
_GRANULARITY_LIMITS: dict[Granularity, tuple[int, int]] = {
    "MINUTE": (4, 48),
    "HOUR": (7 * 24, 7 * 24),
    "DAY": (14 * 24, 14 * 24),
}


class DeliveryMetric(BaseModel):
    name: str
    total: int
    breakdown: list[dict[str, object]] | None = Field(
        default=None,
        description="Raw per-dimension breakdown, passed through as-is — "
        "its shape isn't pinned down in the documentation excerpt this "
        "tool was built against.",
    )


class CheckDeliveryHealthResult(BaseModel):
    region: str
    destination_id: str
    destination_name: str | None
    source_id: str
    granularity: Granularity
    requested_start_time: str | None
    requested_end_time: str | None
    effective_start_time: str
    effective_end_time: str
    window_capped: bool
    cap_note: str | None
    metrics: list[DeliveryMetric]
    gaps: list[Gap] = Field(default_factory=list[Gap])


def _resolve_window(
    granularity: Granularity,
    start_time: str | None,
    end_time: str | None,
    *,
    now: datetime,
) -> tuple[datetime, datetime, bool, str | None]:
    """Apply the real per-granularity caps. Returns (start, end, capped, note).

    Never silently returns a narrower range as if it were what was asked
    for without saying so — BUILD-PLAN.md §5's edge case, generalized to
    the three real (not flat-30-day) limits.
    """
    max_range_hours, max_age_hours = _GRANULARITY_LIMITS[granularity]
    oldest_available = now - _hours(max_age_hours)

    end = _parse_iso8601(end_time) if end_time else now
    if end > now:
        end = now
    start = _parse_iso8601(start_time) if start_time else end - _hours(max_range_hours)

    capped = False
    notes: list[str] = []

    if start < oldest_available:
        start = oldest_available
        capped = True
        notes.append(
            f"the oldest available data for {granularity} granularity is "
            f"{_describe_hours(max_age_hours)} ago"
        )

    if (end - start) > _hours(max_range_hours):
        start = end - _hours(max_range_hours)
        capped = True
        notes.append(
            f"{granularity} granularity allows at most a {_describe_hours(max_range_hours)} range"
        )

    note = "Requested window was capped: " + "; ".join(notes) + "." if notes else None
    return start, end, capped, note


def _hours(count: int) -> timedelta:
    return timedelta(hours=count)


def _describe_hours(count: int) -> str:
    if count % 24 == 0 and count >= 24:
        days = count // 24
        return f"{days} day{'s' if days != 1 else ''}"
    return f"{count}h"


def _parse_iso8601(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


async def check_delivery_health(
    client: SegmentPublicAPIClient,
    *,
    destination_id: str,
    source_id: str,
    granularity: Granularity = "DAY",
    start_time: Annotated[
        str | None, Field(description="ISO 8601. Defaults to the widest allowed window.")
    ] = None,
    end_time: Annotated[str | None, Field(description="ISO 8601. Defaults to now.")] = None,
) -> CheckDeliveryHealthResult:
    """Is this destination silently failing? `GET /destinations/{id}/delivery-metrics`,
    with the requested window validated against the real per-granularity
    caps rather than assumed to fit."""
    # Validated before any client call. destination_id is interpolated
    # directly into both requests below; source_id only ever reaches a
    # query parameter (httpx percent-encodes those), not a path, so this
    # isn't a traversal vector — it's validated anyway for consistency
    # with every other resource ID this server accepts as a tool argument.
    destination_id = validate_resource_id(destination_id, kind="destination_id")
    source_id = validate_resource_id(source_id, kind="source_id")
    now = utc_now()
    effective_start, effective_end, capped, cap_note = _resolve_window(
        granularity, start_time, end_time, now=now
    )

    gaps: list[Gap] = []
    destination_name: str | None = None
    try:
        destination_data = await client.get_data(f"/destinations/{destination_id}")
        destination_name = as_dict(destination_data.get("destination")).get("name")
    except SegmentAPIError as exc:
        gaps.append(Gap(area="destination name", reason=str(exc)))

    metrics_data = await client.get_data(
        f"/destinations/{destination_id}/delivery-metrics",
        params={
            "sourceId": source_id,
            "granularity": granularity,
            "startTime": iso8601(effective_start),
            "endTime": iso8601(effective_end),
        },
    )
    metrics: list[DeliveryMetric] = []
    for item in as_dict_list(metrics_data.get("metrics")):
        raw_total = item.get("total")
        metrics.append(
            DeliveryMetric(
                name=str(item.get("metricName", "")),
                total=raw_total if isinstance(raw_total, int) else 0,
                breakdown=as_dict_list(item.get("breakdown")) or None,
            )
        )

    return CheckDeliveryHealthResult(
        region=client.region.value,
        destination_id=destination_id,
        destination_name=destination_name if isinstance(destination_name, str) else None,
        source_id=source_id,
        granularity=granularity,
        requested_start_time=start_time,
        requested_end_time=end_time,
        effective_start_time=iso8601(effective_start),
        effective_end_time=iso8601(effective_end),
        window_capped=capped,
        cap_note=cap_note,
        metrics=metrics,
        gaps=gaps,
    )
