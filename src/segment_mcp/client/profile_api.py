"""Client for the Segment Profile API — a separate, higher trust tier.

**Auth is HTTP Basic with the access token as username and a BLANK
password** — different from every other Segment API (the Public API uses
`Authorization: Bearer`). This looks like a bug in this code. It isn't —
it's Segment's own documented mechanism for this API. See BUILD-PLAN.md §4.

Path shape: `/v1/spaces/{spaceId}/collections/{users|accounts}/profiles/{id_type:value}/{route}`,
`route` one of `traits`, `external_ids`, `events`, `metadata`, `links`.

This API returns PII on named individuals — the most privacy-sensitive
read in the whole surface. Accordingly:

- It is only ever constructed with a **separate credential**
  (`SEGMENT_PROFILE_TOKEN`), never the main `SEGMENT_API_TOKEN`.
- Every lookup is logged (collection, normalized key, route, and the
  caller-supplied `requested_by` label) via the `segment_mcp.profile_api`
  logger, before the request is sent — so a lookup is on record even if
  the call then fails.
- Whether this client is ever constructed at all is an explicit opt-in
  decision made by whatever wires a profile tool into `server.py`: absent
  `SEGMENT_PROFILE_TOKEN`, no such tool should be registered. See
  README.md's Profile API section.

Lookups are case-sensitive; the wrong case returns an **empty result, not
an error** — `_normalize_id` lowercases every lookup value at this
client's boundary and logs a warning when it had to change something,
since that's a sign of caller input that would otherwise have silently
returned nothing.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any, Literal, cast

import httpx

from segment_mcp.client.regions import Region, endpoints_for

logger = logging.getLogger("segment_mcp.profile_api")

Collection = Literal["users", "accounts"]
ProfileRoute = Literal["traits", "external_ids", "events", "metadata", "links"]

# Per-route limits from BUILD-PLAN.md §4 / this prompt. `events` has no
# client-settable limit — the API enforces a fixed 14-day window
# server-side, not a row count. `links` is capped at 20 by the API and
# isn't client-configurable at all, so there's no constant for it here.
_TRAITS_DEFAULT_LIMIT = 10
_TRAITS_MAX_LIMIT = 200


# --------------------------------------------------------------------------
# Errors — deliberately a separate hierarchy from client/public_api.py's.
# Different auth mechanism, different rate-limit shape (flat 100 req/sec
# per Space vs. the Public API's per-endpoint header-driven limits), and
# conflating the two trust tiers' error types would blur exactly the
# boundary this module exists to keep sharp.
# --------------------------------------------------------------------------


class SegmentProfileAPIError(RuntimeError):
    """Base class for all Segment Profile API errors."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SegmentProfileAuthError(SegmentProfileAPIError):
    """401 — the Profile token was not recognized. Check
    `SEGMENT_PROFILE_TOKEN` specifically; it is not the same credential
    as `SEGMENT_API_TOKEN`."""


class SegmentProfileNotFoundError(SegmentProfileAPIError):
    """404 — no profile found for this key. Note this is also what a
    case-mismatched lookup looks like *before* normalization — which is
    exactly why `_normalize_id` exists."""


class SegmentProfileRateLimitError(SegmentProfileAPIError):
    """429 — rate limited (100 req/sec per Space)."""

    def __init__(self, message: str, *, retry_after: float | None) -> None:
        super().__init__(message, status_code=429)
        self.retry_after = retry_after


class SegmentProfileMalformedResponseError(SegmentProfileAPIError):
    """The response body was not valid JSON, or wasn't a JSON object."""


def _as_dict(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        untyped = cast("dict[object, object]", value)
        if all(isinstance(key, str) for key in untyped):
            return cast("dict[str, Any]", value)
    return {}


class ProfileAPIClient:
    """Async client for the Segment Profile API, bound to one Space in
    one region. See the module docstring for the trust-tier requirements
    a caller constructing this client is responsible for."""

    def __init__(
        self,
        token: str,
        region: Region,
        *,
        space_id: str,
        base_url: str | None = None,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.region = region
        self._space_id = space_id
        credentials = base64.b64encode(f"{token}:".encode()).decode("ascii")
        self._client = httpx.AsyncClient(
            base_url=base_url or endpoints_for(region).profile_api,
            timeout=timeout,
            transport=transport,
            headers={"Authorization": f"Basic {credentials}"},
        )

    async def __aenter__(self) -> ProfileAPIClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _normalize_id(id_type: str, value: str) -> str:
        """Lowercase `value`. Profile API lookups are case-sensitive and
        the wrong case returns an empty result, not an error — silently
        "working" while returning nothing is worse than raising, so this
        at least logs when normalization changed something."""
        lowered = value.lower()
        if lowered != value:
            logger.warning(
                "Profile lookup id_type=%s value=%r was not lowercase; "
                "normalized to %r before calling Segment (wrong case "
                "returns an empty result, not an error).",
                id_type,
                value,
                lowered,
            )
        return lowered

    async def _get(
        self,
        collection: Collection,
        id_type: str,
        id_value: str,
        route: ProfileRoute,
        *,
        params: dict[str, Any] | None = None,
        requested_by: str = "unknown",
    ) -> dict[str, Any]:
        normalized_value = self._normalize_id(id_type, id_value)
        lookup_key = f"{id_type}:{normalized_value}"
        # Logged before the request is sent — a lookup is on record even
        # if the call itself then fails or times out.
        logger.info(
            "Profile lookup: space=%s collection=%s key=%s route=%s requested_by=%s",
            self._space_id,
            collection,
            lookup_key,
            route,
            requested_by,
        )
        path = f"/v1/spaces/{self._space_id}/collections/{collection}/profiles/{lookup_key}/{route}"
        response = await self._client.get(path, params=params)

        if response.status_code == 429:
            retry_after_header = response.headers.get("Retry-After")
            retry_after = float(retry_after_header) if retry_after_header else None
            raise SegmentProfileRateLimitError(
                "Profile API rate-limited this request (429, 100 req/sec per Space).",
                retry_after=retry_after,
            )
        if response.status_code == 401:
            raise SegmentProfileAuthError(
                "Profile API rejected this token (401). Check "
                "SEGMENT_PROFILE_TOKEN — it is a separate credential from "
                "SEGMENT_API_TOKEN.",
                status_code=401,
            )
        if response.status_code == 404:
            raise SegmentProfileNotFoundError(
                f"No profile found for {lookup_key!r} in collection {collection!r}. "
                "If this key was recently case-mismatched, note that wrong "
                "case also returns an empty/not-found result, not an error "
                "— this client already lowercased it before asking.",
                status_code=404,
            )
        response.raise_for_status()

        try:
            body: object = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise SegmentProfileMalformedResponseError(
                f"Profile API returned a response that was not valid JSON "
                f"(status {response.status_code}).",
                status_code=response.status_code,
            ) from exc
        parsed = _as_dict(body)
        if not parsed and body != {}:
            raise SegmentProfileMalformedResponseError(
                f"Profile API returned JSON that was not a string-keyed "
                f"object (got {type(body).__name__}, status {response.status_code}).",
                status_code=response.status_code,
            )
        return parsed

    async def get_traits(
        self,
        collection: Collection,
        id_type: str,
        id_value: str,
        *,
        limit: int = _TRAITS_DEFAULT_LIMIT,
        requested_by: str = "unknown",
    ) -> dict[str, Any]:
        """`GET .../traits`. Defaults to 10 traits; pass `limit` up to 200."""
        if not 1 <= limit <= _TRAITS_MAX_LIMIT:
            raise ValueError(f"limit must be between 1 and {_TRAITS_MAX_LIMIT}, got {limit}")
        return await self._get(
            collection,
            id_type,
            id_value,
            "traits",
            params={"limit": limit},
            requested_by=requested_by,
        )

    async def get_external_ids(
        self, collection: Collection, id_type: str, id_value: str, *, requested_by: str = "unknown"
    ) -> dict[str, Any]:
        """`GET .../external_ids`."""
        return await self._get(
            collection, id_type, id_value, "external_ids", requested_by=requested_by
        )

    async def get_events(
        self, collection: Collection, id_type: str, id_value: str, *, requested_by: str = "unknown"
    ) -> dict[str, Any]:
        """`GET .../events`. Always a 14-day window — enforced by the API
        itself, not a parameter this client can widen."""
        return await self._get(collection, id_type, id_value, "events", requested_by=requested_by)

    async def get_metadata(
        self, collection: Collection, id_type: str, id_value: str, *, requested_by: str = "unknown"
    ) -> dict[str, Any]:
        """`GET .../metadata`."""
        return await self._get(collection, id_type, id_value, "metadata", requested_by=requested_by)

    async def get_links(
        self, collection: Collection, id_type: str, id_value: str, *, requested_by: str = "unknown"
    ) -> dict[str, Any]:
        """`GET .../links`. Capped at 20 by the API — not configurable higher."""
        return await self._get(collection, id_type, id_value, "links", requested_by=requested_by)
