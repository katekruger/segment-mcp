"""`find_ungoverned_sources` — BUILD-PLAN.md §5.

Endpoint shapes verified against docs.segmentapis.com during Prompt 2:
`GET /tracking-plans/{id}/sources` (which sources a plan governs) and
`GET /sources/{id}/settings`, whose response nests the allow/block
governance toggles under `data.settings.track.allowUnplannedEvents` —
that field is exactly BUILD-PLAN's "which sources are ALLOWING rather
than BLOCKING unplanned events."
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from segment_mcp.client.public_api import SegmentAPIError, SegmentPublicAPIClient
from segment_mcp.tools._shared import (
    DEFAULT_MAX_ITEMS,
    Gap,
    ScopedList,
    as_dict,
    list_sources_scoped,
    list_tracking_plans,
)


class SourceGovernance(BaseModel):
    source_id: str
    source_name: str
    governed_by_tracking_plan: bool
    tracking_plan_ids: list[str] = Field(default_factory=list[str])
    allow_unplanned_events: bool | None = Field(
        default=None,
        description="From sources/{id}/settings.track.allowUnplannedEvents. "
        "Null only if that fetch failed — see settings_gap_reason.",
    )
    settings_gap_reason: str | None = None


class FindUngovernedSourcesResult(BaseModel):
    region: str
    scope: ScopedList
    fully_governed: list[SourceGovernance] = Field(default_factory=list[SourceGovernance])
    governed_but_allowing_unplanned: list[SourceGovernance] = Field(
        default_factory=list[SourceGovernance]
    )
    ungoverned: list[SourceGovernance] = Field(default_factory=list[SourceGovernance])
    gaps: list[Gap] = Field(default_factory=list[Gap])


async def find_ungoverned_sources(
    client: SegmentPublicAPIClient,
    *,
    source_id: str | None = None,
    max_sources: int = DEFAULT_MAX_ITEMS,
    max_tracking_plans: int = DEFAULT_MAX_ITEMS,
) -> FindUngovernedSourcesResult:
    """Which sources are governed by no tracking plan at all, and which
    are governed but still ALLOWING unplanned events through — directly
    actionable governance gaps, not just a report.

    Composes `GET /sources`, `GET /tracking-plans`,
    `GET /tracking-plans/{id}/sources` for each plan, and
    `GET /sources/{id}/settings` for each source in scope.
    """
    sources, scope = await list_sources_scoped(client, source_id=source_id, max_items=max_sources)
    tracking_plans = await list_tracking_plans(client, max_items=max_tracking_plans)

    gaps: list[Gap] = []
    governed_source_ids: dict[str, list[str]] = {}
    for plan in tracking_plans:
        try:
            plan_sources, _truncated = await client.paginate(
                f"/tracking-plans/{plan.id}/sources", items_key="sources", page_size=200
            )
        except SegmentAPIError as exc:
            gaps.append(Gap(area=f"tracking plan {plan.id} sources", reason=str(exc)))
            continue
        for raw_source in plan_sources:
            sid = raw_source.get("id")
            if isinstance(sid, str):
                governed_source_ids.setdefault(sid, []).append(plan.id)

    fully_governed: list[SourceGovernance] = []
    allowing_unplanned: list[SourceGovernance] = []
    ungoverned: list[SourceGovernance] = []

    for source in sources:
        plan_ids = governed_source_ids.get(source.id, [])
        allow_unplanned: bool | None = None
        settings_gap: str | None = None
        try:
            settings_data = await client.get_data(f"/sources/{source.id}/settings")
            track_settings = as_dict(as_dict(settings_data.get("settings")).get("track"))
            raw_allow = track_settings.get("allowUnplannedEvents")
            allow_unplanned = raw_allow if isinstance(raw_allow, bool) else None
        except SegmentAPIError as exc:
            settings_gap = str(exc)
            gaps.append(Gap(area=f"source {source.id} settings", reason=settings_gap))

        governance = SourceGovernance(
            source_id=source.id,
            source_name=source.name,
            governed_by_tracking_plan=bool(plan_ids),
            tracking_plan_ids=plan_ids,
            allow_unplanned_events=allow_unplanned,
            settings_gap_reason=settings_gap,
        )

        if not plan_ids:
            ungoverned.append(governance)
        elif allow_unplanned:
            allowing_unplanned.append(governance)
        else:
            fully_governed.append(governance)

    return FindUngovernedSourcesResult(
        region=client.region.value,
        scope=scope,
        fully_governed=fully_governed,
        governed_but_allowing_unplanned=allowing_unplanned,
        ungoverned=ungoverned,
        gaps=gaps,
    )
