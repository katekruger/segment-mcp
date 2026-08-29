"""Tests for region resolution — see BUILD-PLAN.md §0.6."""

import pytest

from segment_mcp.client.regions import (
    Region,
    RegionConfigError,
    endpoints_for,
    other_region,
    resolve_region,
)


def test_resolve_region_requires_the_variable_to_be_set() -> None:
    with pytest.raises(RegionConfigError, match="not set"):
        resolve_region(env={})


@pytest.mark.parametrize("blank", ["", "   "])
def test_resolve_region_rejects_blank_values(blank: str) -> None:
    with pytest.raises(RegionConfigError, match="not set"):
        resolve_region(env={"SEGMENT_REGION": blank})


def test_resolve_region_error_names_both_valid_values() -> None:
    with pytest.raises(RegionConfigError) as exc_info:
        resolve_region(env={})
    message = str(exc_info.value)
    assert "'us'" in message
    assert "'eu'" in message


def test_resolve_region_rejects_unknown_values() -> None:
    with pytest.raises(RegionConfigError, match="not a valid region"):
        resolve_region(env={"SEGMENT_REGION": "oregon"})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("us", Region.US),
        ("eu", Region.EU),
        ("US", Region.US),
        ("Eu", Region.EU),
        (" us ", Region.US),
    ],
)
def test_resolve_region_accepts_case_and_whitespace_variance(raw: str, expected: Region) -> None:
    assert resolve_region(env={"SEGMENT_REGION": raw}) is expected


def test_resolve_region_never_defaults_to_us_when_config_is_broken() -> None:
    # Defends the specific failure mode from BUILD-PLAN.md §0.6: this must
    # raise, never silently fall back to US.
    with pytest.raises(RegionConfigError):
        resolve_region(env={"SOME_OTHER_VAR": "us"})


def test_us_endpoints_match_build_plan() -> None:
    endpoints = endpoints_for(Region.US)
    assert endpoints.public_api == "https://api.segmentapis.com"
    assert endpoints.tracking_api == "https://api.segment.io/v1"
    assert endpoints.profile_api == "https://profiles.segment.com"


def test_eu_endpoints_match_build_plan() -> None:
    endpoints = endpoints_for(Region.EU)
    assert endpoints.public_api == "https://eu1.api.segmentapis.com"
    assert endpoints.tracking_api == "https://events.eu1.segmentapis.com"
    assert endpoints.profile_api == "https://profiles.euw1.segment.com"


def test_public_api_has_no_v1_path_segment() -> None:
    # The single most common thing to get wrong, per BUILD-PLAN.md §4.
    for region in Region:
        assert "/v1" not in endpoints_for(region).public_api


def test_other_region_is_symmetric() -> None:
    assert other_region(Region.US) is Region.EU
    assert other_region(Region.EU) is Region.US
