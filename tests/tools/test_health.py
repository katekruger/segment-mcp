"""Tests for `find_stale_sources` and `check_delivery_health`.

No test here calls a live Segment API — see AGENTS.md.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from segment_mcp.client.regions import Region
from segment_mcp.tools.health import check_delivery_health, find_stale_sources
from tests.tools._helpers import make_client

# --------------------------------------------------------------------------
# find_stale_sources
# --------------------------------------------------------------------------


async def test_classifies_active_and_stale_sources() -> None:
    client = make_client(
        {
            "/sources": "tools/sources_two_200.json",
            "/events/volume": [
                "tools/events_volume_recent_mixed_200.json",
                "tools/events_volume_historical_active_200.json",
            ],
        }
    )
    async with client:
        result = await find_stale_sources(client)

    assert result.active_count == 1
    assert [s.source_id for s in result.stale] == ["src_2"]
    assert result.stale[0].recent_event_count == 0
    assert result.stale[0].historical_event_count == 10
    assert result.insufficient_data == []
    assert "does not expose a source creation date" in result.note_on_source_age.lower()


async def test_classifies_insufficient_data_when_no_history_either() -> None:
    client = make_client(
        {
            "/sources/src_3": "tools/source_single_new_200.json",
            "/events/volume": [
                "tools/events_volume_recent_zero_one_200.json",
                "tools/events_volume_empty_200.json",
            ],
        }
    )
    async with client:
        result = await find_stale_sources(client, source_id="src_3")

    assert result.active_count == 0
    assert result.stale == []
    assert len(result.insufficient_data) == 1
    assert result.insufficient_data[0].status == "insufficient_data"


async def test_rejects_historical_window_not_wider_than_recent() -> None:
    client = make_client({"/sources": "tools/sources_two_200.json"})
    async with client:
        with pytest.raises(ValueError, match="historical_days must be greater"):
            await find_stale_sources(client, recent_days=30, historical_days=30)


async def test_works_against_eu_region_fixtures() -> None:
    client = make_client(
        {
            "/sources": "tools/sources_two_200.json",
            "/events/volume": [
                "tools/events_volume_recent_mixed_200.json",
                "tools/events_volume_historical_active_200.json",
            ],
        },
        region=Region.EU,
    )
    async with client:
        result = await find_stale_sources(client)
    assert result.region == "eu"
    assert result.active_count == 1


# --------------------------------------------------------------------------
# check_delivery_health
# --------------------------------------------------------------------------


async def test_delivery_health_happy_path() -> None:
    client = make_client(
        {
            "/destinations/dest_1": "tools/destination_detail_200.json",
            "/destinations/dest_1/delivery-metrics": "tools/delivery_metrics_200.json",
        }
    )
    async with client:
        result = await check_delivery_health(client, destination_id="dest_1", source_id="src_1")

    assert result.destination_name == "Amplitude"
    assert result.window_capped is False
    names = {m.name for m in result.metrics}
    assert names == {"successful_requests", "failed_requests"}
    failed = next(m for m in result.metrics if m.name == "failed_requests")
    assert failed.total == 20
    assert failed.breakdown == [{"reason": "timeout", "total": 20}]


async def test_delivery_health_caps_range_exceeding_granularity_max() -> None:
    client = make_client(
        {
            "/destinations/dest_1": "tools/destination_detail_200.json",
            "/destinations/dest_1/delivery-metrics": "tools/delivery_metrics_200.json",
        }
    )
    now = datetime.now(UTC)
    start = (now - timedelta(days=60)).isoformat()
    end = now.isoformat()
    async with client:
        result = await check_delivery_health(
            client,
            destination_id="dest_1",
            source_id="src_1",
            granularity="DAY",
            start_time=start,
            end_time=end,
        )

    assert result.window_capped is True
    assert result.cap_note is not None
    assert "14" in result.cap_note


async def test_delivery_health_degrades_gracefully_when_destination_name_unavailable() -> None:
    client = make_client(
        {
            "/destinations/dest_1": "tools/destination_subscriptions_403.json",
            "/destinations/dest_1/delivery-metrics": "tools/delivery_metrics_200.json",
        }
    )
    async with client:
        result = await check_delivery_health(client, destination_id="dest_1", source_id="src_1")

    assert result.destination_name is None
    assert len(result.gaps) == 1
    assert result.metrics  # the metrics call itself still succeeded
