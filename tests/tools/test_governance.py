"""Tests for `find_ungoverned_sources`.

No test here calls a live Segment API — see AGENTS.md.
"""

from __future__ import annotations

from segment_mcp.client.regions import Region
from segment_mcp.tools.governance import find_ungoverned_sources
from tests.tools._helpers import make_client


async def test_classifies_ungoverned_governed_and_allowing_unplanned() -> None:
    client = make_client(
        {
            "/sources": "tools/sources_two_200.json",
            "/tracking-plans": "tools/tracking_plans_one_200.json",
            "/tracking-plans/tp_1/sources": "tools/tracking_plan_sources_with_src1_200.json",
            "/sources/src_1/settings": "tools/source_settings_allow_200.json",
            "/sources/src_2/settings": "tools/source_settings_block_200.json",
        }
    )
    async with client:
        result = await find_ungoverned_sources(client)

    # src_1: governed (in tp_1's sources) but allowUnplannedEvents=True.
    assert [g.source_id for g in result.governed_but_allowing_unplanned] == ["src_1"]
    assert result.governed_but_allowing_unplanned[0].tracking_plan_ids == ["tp_1"]

    # src_2: not attached to any tracking plan at all.
    assert [g.source_id for g in result.ungoverned] == ["src_2"]
    assert result.ungoverned[0].tracking_plan_ids == []

    assert result.fully_governed == []
    assert result.gaps == []


async def test_fully_governed_when_plan_blocks_unplanned_events() -> None:
    client = make_client(
        {
            "/sources/src_2": "tools/source_single_block_200.json",
            "/tracking-plans": "tools/tracking_plans_one_200.json",
            "/tracking-plans/tp_1/sources": "tools/tracking_plan_sources_with_src2_200.json",
            "/sources/src_2/settings": "tools/source_settings_block_200.json",
        }
    )
    async with client:
        result = await find_ungoverned_sources(client, source_id="src_2")

    assert [g.source_id for g in result.fully_governed] == ["src_2"]
    assert result.ungoverned == []
    assert result.governed_but_allowing_unplanned == []


async def test_all_sources_ungoverned_when_no_tracking_plans_exist() -> None:
    client = make_client(
        {
            "/sources": "tools/sources_two_200.json",
            "/tracking-plans": "tools/tracking_plans_empty_200.json",
            "/sources/src_1/settings": "tools/source_settings_allow_200.json",
            "/sources/src_2/settings": "tools/source_settings_block_200.json",
        }
    )
    async with client:
        result = await find_ungoverned_sources(client)

    assert {g.source_id for g in result.ungoverned} == {"src_1", "src_2"}
    assert result.fully_governed == []
    assert result.governed_but_allowing_unplanned == []


async def test_works_against_eu_region_fixtures() -> None:
    client = make_client(
        {
            "/sources": "tools/sources_two_200.json",
            "/tracking-plans": "tools/tracking_plans_one_200.json",
            "/tracking-plans/tp_1/sources": "tools/tracking_plan_sources_with_src1_200.json",
            "/sources/src_1/settings": "tools/source_settings_allow_200.json",
            "/sources/src_2/settings": "tools/source_settings_block_200.json",
        },
        region=Region.EU,
    )
    async with client:
        result = await find_ungoverned_sources(client)
    assert result.region == "eu"
    assert [g.source_id for g in result.ungoverned] == ["src_2"]
