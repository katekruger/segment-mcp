"""Region resolution for the Segment Public/Profile/Tracking APIs.

`SEGMENT_REGION` must be set explicitly to `us` or `eu` — there is no
default. An EU workspace whose calls are pointed at the US (Oregon)
Tracking API endpoint get **no error**; events are silently dropped. The
same class of mistake against the Public/Profile API base URLs fails loudly
instead (401/403), but region is still resolved once, explicitly, and never
inferred. See BUILD-PLAN.md §0.6 and §4.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum


class Region(StrEnum):
    """A Segment workspace region. Each region is a wholly separate set of
    API deployments — there is no cross-region fallback or redirect."""

    US = "us"
    EU = "eu"


class RegionConfigError(RuntimeError):
    """`SEGMENT_REGION` is unset, blank, or not one of the valid values."""


class RegionMismatchError(RuntimeError):
    """The configured region does not match the token's actual workspace region.

    Raised by a startup self-check, not by region resolution itself — see
    `segment_mcp.client.public_api.SegmentPublicAPIClient.verify_region`.
    """


@dataclass(frozen=True, slots=True)
class RegionEndpoints:
    """Base URLs for the three Segment APIs in one region.

    Public and Profile API endpoints have no `/v1` (or similar) path
    prefix — see BUILD-PLAN.md §4. The Tracking API base URL below already
    includes its `/v1` segment because Segment's own docs quote it that
    way; do not add a second one.
    """

    public_api: str
    tracking_api: str
    profile_api: str


_ENDPOINTS: dict[Region, RegionEndpoints] = {
    Region.US: RegionEndpoints(
        public_api="https://api.segmentapis.com",
        tracking_api="https://api.segment.io/v1",
        profile_api="https://profiles.segment.com",
    ),
    Region.EU: RegionEndpoints(
        public_api="https://eu1.api.segmentapis.com",
        tracking_api="https://events.eu1.segmentapis.com",
        profile_api="https://profiles.euw1.segment.com",
    ),
}

_VALID_VALUES = ", ".join(repr(r.value) for r in Region)


def resolve_region(env: Mapping[str, str] | None = None) -> Region:
    """Resolve `SEGMENT_REGION` from the environment. Never defaults.

    Args:
        env: Mapping to read from. Defaults to `os.environ`. Tests should
            pass an explicit mapping rather than mutating process env.

    Returns:
        Region: the resolved region.

    Raises:
        RegionConfigError: if `SEGMENT_REGION` is unset, blank, or not one
            of the valid values. The message names both valid values, per
            BUILD-PLAN.md's explicit requirement — a caller must not have
            to go read the source to find out what to set it to.
    """
    source = env if env is not None else os.environ
    raw = source.get("SEGMENT_REGION")
    if raw is None or not raw.strip():
        raise RegionConfigError(
            "SEGMENT_REGION is not set. Set it explicitly to one of: "
            f"{_VALID_VALUES}. There is no default — an EU workspace "
            "pointed at the US Tracking API fails silently (events never "
            "appear, no error is raised). See BUILD-PLAN.md §0.6."
        )
    normalized = raw.strip().lower()
    try:
        return Region(normalized)
    except ValueError:
        raise RegionConfigError(
            f"SEGMENT_REGION={raw!r} is not a valid region. Set it to one of: {_VALID_VALUES}."
        ) from None


def endpoints_for(region: Region) -> RegionEndpoints:
    """Return the base URLs for the three Segment APIs in `region`."""
    return _ENDPOINTS[region]


def other_region(region: Region) -> Region:
    """The one other region — used by the startup self-check to probe for
    a mismatch. Only meaningful while there are exactly two regions."""
    return Region.EU if region is Region.US else Region.US
