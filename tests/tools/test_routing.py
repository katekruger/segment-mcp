"""Tests for `audit_event_routing` and `trace_event`.

No test here calls a live Segment API — see AGENTS.md.
"""

from __future__ import annotations

from segment_mcp.client.regions import Region
from segment_mcp.tools.routing import audit_event_routing, trace_event
from tests.tools._helpers import make_client

# --------------------------------------------------------------------------
# audit_event_routing
# --------------------------------------------------------------------------


async def test_audits_routing_with_subscriptions() -> None:
    client = make_client(
        {
            "/sources": "tools/sources_two_200.json",
            "/sources/src_1/connected-destinations": "tools/connected_destinations_one_200.json",
            "/sources/src_2/connected-destinations": ("source_no_connected_destinations_200.json"),
            "/destinations/dest_1": "tools/destination_detail_200.json",
            "/destinations/dest_1/subscriptions": "tools/destination_subscriptions_one_200.json",
        }
    )
    async with client:
        result = await audit_event_routing(client)

    assert result.subscriptions_available is True
    assert result.gaps == []

    web_app = next(s for s in result.routing if s.id == "src_1")
    assert web_app.has_no_connected_destinations is False
    assert len(web_app.destinations) == 1
    destination = web_app.destinations[0]
    assert destination.name == "Amplitude"
    assert destination.subscriptions is not None
    assert destination.subscriptions[0].id == "sub_1"


async def test_source_with_no_connected_destinations_says_so_explicitly() -> None:
    client = make_client(
        {
            "/sources/src_2": "tools/source_single_block_200.json",
            "/sources/src_2/connected-destinations": ("source_no_connected_destinations_200.json"),
        }
    )
    async with client:
        result = await audit_event_routing(client, source_id="src_2")

    assert len(result.routing) == 1
    source = result.routing[0]
    assert source.has_no_connected_destinations is True
    assert source.destinations == []


async def test_degrades_gracefully_when_subscriptions_unavailable() -> None:
    client = make_client(
        {
            "/sources": "tools/sources_two_200.json",
            "/sources/src_1/connected-destinations": "tools/connected_destinations_one_200.json",
            "/sources/src_2/connected-destinations": ("source_no_connected_destinations_200.json"),
            "/destinations/dest_1": "tools/destination_detail_200.json",
            "/destinations/dest_1/subscriptions": "tools/destination_subscriptions_403.json",
        }
    )
    async with client:
        result = await audit_event_routing(client)

    # The audit must NOT error — it degrades.
    assert result.subscriptions_available is False
    assert any("subscriptions" in gap.area for gap in result.gaps)
    web_app = next(s for s in result.routing if s.id == "src_1")
    assert web_app.destinations[0].subscriptions is None
    # Routing itself is still fully reported despite the gap.
    assert web_app.destinations[0].name == "Amplitude"


async def test_include_subscriptions_false_skips_the_alpha_endpoint_entirely() -> None:
    client = make_client(
        {
            "/sources/src_1": "tools/source_single_200.json",
            "/sources/src_1/connected-destinations": "tools/connected_destinations_one_200.json",
            "/destinations/dest_1": "tools/destination_detail_200.json",
            # Deliberately no route for /destinations/dest_1/subscriptions —
            # if the tool called it, path_routed_transport would raise.
        }
    )
    async with client:
        result = await audit_event_routing(client, source_id="src_1", include_subscriptions=False)

    assert result.subscriptions_available is False
    assert result.routing[0].destinations[0].subscriptions is None


async def test_audit_works_against_eu_region_fixtures() -> None:
    client = make_client(
        {
            "/sources/src_1": "tools/source_single_200.json",
            "/sources/src_1/connected-destinations": "tools/connected_destinations_one_200.json",
            "/destinations/dest_1": "tools/destination_detail_200.json",
            "/destinations/dest_1/subscriptions": "tools/destination_subscriptions_one_200.json",
        },
        region=Region.EU,
    )
    async with client:
        result = await audit_event_routing(client, source_id="src_1")
    assert result.region == "eu"
    assert result.routing[0].destinations[0].name == "Amplitude"


# --------------------------------------------------------------------------
# trace_event
# --------------------------------------------------------------------------


async def test_event_governed_by_a_tracking_plan_and_confirmed_emitting() -> None:
    client = make_client(
        {
            "/tracking-plans": "tools/tracking_plans_one_200.json",
            "/tracking-plans/tp_1/rules": "tools/tracking_plan_rules_match_200.json",
            "/events/volume": "tools/events_volume_emitting_200.json",
            "/sources/src_1/connected-destinations": "tools/connected_destinations_one_200.json",
            "/sources/src_1/connected-warehouses": "tools/connected_warehouses_one_200.json",
        }
    )
    async with client:
        result = await trace_event(client, event_name="Order Completed")

    assert result.governed is True
    assert "Governed by 1 tracking plan" in result.governance_note
    assert result.tracking_plans[0].tracking_plan_id == "tp_1"
    assert result.emission_confirmed is True
    assert result.emitting_sources[0].source_id == "src_1"
    assert result.destinations[0].destination_name == "Amplitude"
    assert result.warehouses[0].warehouse_id == "wh_1"


async def test_event_in_no_tracking_plan_says_governed_by_nothing() -> None:
    client = make_client(
        {
            "/tracking-plans": "tools/tracking_plans_one_200.json",
            "/tracking-plans/tp_1/rules": "tools/tracking_plan_rules_no_match_200.json",
            "/events/volume": "tools/events_volume_empty_200.json",
        }
    )
    async with client:
        result = await trace_event(client, event_name="Order Completed")

    assert result.governed is False
    assert result.governance_note == (
        "This event appears in no tracking plan. It is governed by nothing."
    )
    assert result.tracking_plans == []


async def test_scoped_to_one_source_skips_emission_based_scoping() -> None:
    client = make_client(
        {
            "/tracking-plans": "tools/tracking_plans_empty_200.json",
            "/events/volume": "tools/events_volume_empty_200.json",
            "/sources/src_1/connected-destinations": "tools/connected_destinations_one_200.json",
            "/sources/src_1/connected-warehouses": "tools/connected_warehouses_one_200.json",
        }
    )
    async with client:
        result = await trace_event(client, event_name="Order Completed", source_id="src_1")

    assert result.destinations[0].source_id == "src_1"
    assert result.destination_warehouse_scope_note is None


async def test_too_many_emitting_sources_omits_destination_lookup_with_a_note() -> None:
    client = make_client(
        {
            "/tracking-plans": "tools/tracking_plans_empty_200.json",
            "/events/volume": "tools/events_volume_many_emitters_200.json",
        }
    )
    async with client:
        result = await trace_event(client, event_name="Order Completed", max_related_sources=1)

    assert result.destinations == []
    assert result.warehouses == []
    assert result.destination_warehouse_scope_note is not None
    assert "source_id" in result.destination_warehouse_scope_note


async def test_trace_event_works_against_eu_region_fixtures() -> None:
    client = make_client(
        {
            "/tracking-plans": "tools/tracking_plans_one_200.json",
            "/tracking-plans/tp_1/rules": "tools/tracking_plan_rules_match_200.json",
            "/events/volume": "tools/events_volume_emitting_200.json",
            "/sources/src_1/connected-destinations": "tools/connected_destinations_one_200.json",
            "/sources/src_1/connected-warehouses": "tools/connected_warehouses_one_200.json",
        },
        region=Region.EU,
    )
    async with client:
        result = await trace_event(client, event_name="Order Completed")
    assert result.region == "eu"
    assert result.governed is True
