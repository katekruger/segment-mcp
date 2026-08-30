"""Client for the Segment Public API.

Base URL: `https://api.segmentapis.com` (US) or
`https://eu1.api.segmentapis.com` (EU). **There is no `/v1` in the path** —
endpoints are bare, e.g. `GET https://api.segmentapis.com/sources`. This is
the single most common thing to get wrong (BUILD-PLAN.md §4).

Auth is `Authorization: Bearer $TOKEN`.

Rate limits are enforced per endpoint, per token, and at token level
separately, and the global default is not published — this client never
hard-codes a request budget. Instead it reacts to what the API tells it:
`X-RateLimit-Remaining` / `X-RateLimit-Reset` on success, `Retry-After` /
`data.msBeforeNext` on a 429. See `PerEndpointRateLimiter`.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, cast

import httpx

from segment_mcp.client.regions import (
    Region,
    RegionMismatchError,
    endpoints_for,
    other_region,
)

# The one endpoint every workspace can reach regardless of what else is
# enabled, used as the "is this token valid for this region" self-check.
_SELF_CHECK_PATH = "/workspaces"

# Tier 1 (BUILD-PLAN.md §6, docs/decisions/0002-tier-1-permanently-unreachable.md):
# workspace-scoped, permanent, irreversible data deletion. This client
# refuses to send a mutating request here regardless of what calls it —
# independent of modes.py's own refusal (belt and braces; see
# tests/test_tier1_unreachable.py). GET stays open: the reads
# (GET /regulations, GET /regulations/{id}, GET /suppressions) are in
# scope for v0.2 and are not what this guards against.
_TIER1_BLOCKED_PATH_PREFIX = "/regulations"

# Floor applied only when a 429 carries no Retry-After header and no
# data.msBeforeNext — i.e. genuinely no timing signal. Not a guess at
# Segment's unpublished global rate limit; see BUILD-PLAN.md §4.
_DEFAULT_RATE_LIMIT_BACKOFF_SECONDS = 1.0


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class SegmentAPIError(RuntimeError):
    """Base class for all Segment Public API errors."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SegmentAuthError(SegmentAPIError):
    """401 — the token was not recognized. Often a region mismatch: a
    token minted in one region's workspace does not authenticate against
    the other region's Public API domain. See `verify_region`."""


class SegmentPermissionError(SegmentAPIError):
    """403 — the token is valid but the request was refused.

    Two known causes, distinguished on a best-effort basis from the error
    body (unverified against a live workspace — see BUILD-PLAN.md §11.4):

    - The workspace is on Free or Add-on tier, where the Public API is not
      available at all: use/check `SegmentTierError`.
    - The token's role does not permit this operation. Only a Workspace
      Owner can mint a Public API token in the first place, so a token
      that exists at all should be able to read; a write/admin operation
      refused here likely needs a different role than the one the token
      was minted under.
    """


class SegmentTierError(SegmentPermissionError):
    """403 — the workspace is not on Team or Business tier, so the Public
    API is unavailable. Raised instead of a bare `SegmentPermissionError`
    so callers can say so explicitly rather than surfacing a raw 403."""


class SegmentRateLimitError(SegmentAPIError):
    """429 — rate limited. Carries whatever the response told us about
    when it's safe to retry, per BUILD-PLAN.md §4:

    - `retry_after`: seconds to wait. Populated from the `Retry-After`
      header when present (token-level 429s carry this instead of the
      `data.*` fields), else derived from `data.msBeforeNext`. Segment's
      docs say to prefer `Retry-After` over a self-computed backoff when
      both could apply, which is what this class does.
    - `remaining_points` / `consumed_points`: from `data.remainingPoints` /
      `data.consumedPoints` on endpoint/token-complexity 429s. `None` on a
      token-level 429, which omits them.
    """

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None,
        remaining_points: int | None = None,
        consumed_points: int | None = None,
    ) -> None:
        super().__init__(message, status_code=429)
        self.retry_after = retry_after
        self.remaining_points = remaining_points
        self.consumed_points = consumed_points


class SegmentMalformedResponseError(SegmentAPIError):
    """The response body was not valid JSON, or wasn't a JSON object."""


class Tier1BlockedError(SegmentAPIError):
    """Refused before sending: a mutating request to `/regulations*`.

    This client never sends this request over the network — it's rejected
    client-side, before auth, before rate limiting, before anything else.
    See `docs/decisions/0002-tier-1-permanently-unreachable.md` and
    `modes.py`'s independent refusal of the same tier.
    """


# --------------------------------------------------------------------------
# Tier 1 refusal — see Tier1BlockedError
# --------------------------------------------------------------------------


def _refuse_if_tier1_mutation(method: str, path: str, *, base_url: httpx.URL) -> None:
    """Raise before a single byte goes over the network if this is a
    mutating (non-GET) call anywhere under `/regulations`.

    Matches on the URL httpx will actually resolve, not the raw string
    passed in — an earlier version of this guard did a bare
    `path.split("?", 1)[0]` string-prefix check, which missed a
    `#fragment` it never stripped, a `.`/`..` path segment it never
    normalized, a request path with no leading slash, an absolute URL
    override, and case variation (`/REGULATIONS`). `base_url.join(path)`
    resolves all of those the same way the underlying request will, which
    is exactly why the check runs against it rather than reimplementing
    URL resolution here. See
    docs/decisions/0004-tier1-guard-matches-resolved-url.md.
    """
    if method.upper() == "GET":
        return
    resolved = base_url.join(path)
    normalized_path = resolved.path.rstrip("/").casefold()
    blocked_prefix = _TIER1_BLOCKED_PATH_PREFIX.casefold()
    if normalized_path == blocked_prefix or normalized_path.startswith(blocked_prefix + "/"):
        raise Tier1BlockedError(
            f"Refused: {method.upper()} {path} is a Tier 1 action "
            "(regulation/deletion creation) and is permanently unreachable "
            "through this client, in every mode. See "
            "docs/decisions/0002-tier-1-permanently-unreachable.md.",
            status_code=None,
        )


# --------------------------------------------------------------------------
# Per-endpoint rate limiting
# --------------------------------------------------------------------------


def _endpoint_key(method: str, path: str) -> str:
    # Query strings and path parameters both vary per call; limits are
    # documented per *endpoint*, not per exact URL, so this key is
    # deliberately coarse. Good enough for "this path is currently
    # throttled" without trying to template out path parameters.
    return f"{method.upper()} {path.split('?', 1)[0]}"


@dataclass
class _EndpointState:
    remaining: int | None = None
    reset_at: datetime | None = None
    retry_after: float | None = None


@dataclass
class PerEndpointRateLimiter:
    """Tracks rate-limit state observed from response headers, keyed per
    endpoint. Never hard-codes a request budget for any endpoint — the
    global default is unpublished and the documented tight per-endpoint
    limits (audience previews, reverse ETL syncs, etc.) belong to v0.2
    tools this client doesn't call yet. This limiter only reacts to what
    the API actually told it on a prior call to *that* endpoint.
    """

    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    sleep: Callable[[float], Awaitable[None]] = field(default=asyncio.sleep)
    _state: dict[str, _EndpointState] = field(default_factory=dict[str, _EndpointState])

    async def wait_if_needed(self, method: str, path: str) -> None:
        """Sleep if the last known state for this endpoint says we should."""
        state = self._state.get(_endpoint_key(method, path))
        if state is None:
            return
        if state.retry_after is not None:
            wait_seconds = state.retry_after
            state.retry_after = None
            if wait_seconds > 0:
                await self.sleep(wait_seconds)
            return
        if state.remaining == 0 and state.reset_at is not None:
            delay = (state.reset_at - self.clock()).total_seconds()
            if delay > 0:
                await self.sleep(delay)

    def observe_response(self, method: str, path: str, headers: httpx.Headers) -> None:
        """Record `X-RateLimit-*` from a non-429 response, if present."""
        remaining_header = headers.get("X-RateLimit-Remaining")
        reset_header = headers.get("X-RateLimit-Reset")
        if remaining_header is None and reset_header is None:
            return
        state = self._state.setdefault(_endpoint_key(method, path), _EndpointState())
        if remaining_header is not None:
            try:
                state.remaining = int(remaining_header)
            except ValueError:
                state.remaining = None
        if reset_header is not None:
            state.reset_at = _parse_http_date(reset_header)

    def observe_retry_after(self, method: str, path: str, retry_after: float | None) -> None:
        """Record a wait derived from a 429 response, for the *next* call
        to this endpoint. Does not affect the request that just failed —
        callers decide for themselves whether/when to retry."""
        if retry_after is None:
            return
        state = self._state.setdefault(_endpoint_key(method, path), _EndpointState())
        state.retry_after = retry_after


def _parse_http_date(value: str) -> datetime | None:
    """Parse an RFC 5322 / HTTP-date header value. Returns None, rather
    than raising, on anything unparseable — a malformed date header should
    degrade to "unknown reset time", not crash the request."""
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _parse_retry_after(value: str) -> float | None:
    """`Retry-After` is delta-seconds or an HTTP-date (RFC 7231 §7.1.3)."""
    try:
        return float(value)
    except ValueError:
        pass
    parsed = _parse_http_date(value)
    if parsed is None:
        return None
    delta = (parsed - datetime.now(UTC)).total_seconds()
    return max(delta, 0.0)


# --------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------

# Best-effort keyword match against a 403 body to distinguish "workspace
# isn't on Team/Business tier" from "token lacks the needed role". This is
# a heuristic, not a documented API contract — Segment doesn't publish a
# stable error code for it, and it hasn't been verified against a live
# Free-tier workspace (BUILD-PLAN.md §11.4). If a real 403 body doesn't
# match either, callers still get a SegmentPermissionError with the raw
# server message, which is strictly more informative than a bare 403.
_TIER_KEYWORDS = ("tier", "plan", "upgrade", "not available on")


class SegmentPublicAPIClient:
    """Async client for the Segment Public API, bound to one region."""

    def __init__(
        self,
        token: str,
        region: Region,
        *,
        base_url: str | None = None,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        rate_limiter: PerEndpointRateLimiter | None = None,
        probe_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.region = region
        self._token = token
        self._timeout = timeout
        # Used only by verify_region() to build the other-region probe
        # client. Separate from `transport` because tests exercising a
        # region mismatch need to control both regions' responses
        # independently; production leaves both None (real network).
        self._probe_transport = probe_transport
        self._limiter = rate_limiter or PerEndpointRateLimiter()
        self._client = httpx.AsyncClient(
            base_url=base_url or endpoints_for(region).public_api,
            timeout=timeout,
            transport=transport,
            headers={"Authorization": f"Bearer {token}"},
        )

    async def __aenter__(self) -> SegmentPublicAPIClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request("GET", path, params=params)

    async def get_data(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """`GET path` and unwrap Segment's `data` envelope.

        Every Public API response wraps its payload in a top-level `data`
        object — verified against docs.segmentapis.com (Prompt 2 research;
        this wasn't confirmed when the client shipped in Prompt 1). Use
        this instead of `get()` for any endpoint that follows the
        convention, which is all of them in `tools/`.
        """
        body = await self.get(path, params=params)
        raw_data = body.get("data")
        data = _as_str_keyed_dict(raw_data)
        if not data and raw_data != {}:
            raise SegmentMalformedResponseError(
                f"Segment's response for {path} had no 'data' object "
                f"(got {type(raw_data).__name__})."
            )
        return data

    async def paginate(
        self,
        path: str,
        *,
        items_key: str,
        params: dict[str, Any] | None = None,
        page_size: int = 200,
        max_pages: int = 10,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Follow Segment's cursor pagination (`pagination.count` /
        `pagination.cursor` request params, `data.pagination.next`
        response field) until exhausted or `max_pages` is hit.

        Returns `(items, truncated)`. `truncated` is True only when
        `max_pages` was reached before the API signaled there were no
        more pages — callers must surface that rather than treating a
        partial list as complete (BUILD-PLAN.md §5's "workspace with 200
        sources" edge case).
        """
        items: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(max_pages):
            query: dict[str, Any] = dict(params or {})
            query["pagination.count"] = page_size
            if cursor is not None:
                query["pagination.cursor"] = cursor
            data = await self.get_data(path, params=query)
            page_items = data.get(items_key)
            if isinstance(page_items, list):
                items.extend(cast("list[dict[str, Any]]", page_items))
            pagination = _as_str_keyed_dict(data.get("pagination"))
            next_cursor = pagination.get("next")
            cursor = next_cursor if isinstance(next_cursor, str) else None
            if not cursor:
                return items, False
        return items, True

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _refuse_if_tier1_mutation(method, path, base_url=self._client.base_url)
        await self._limiter.wait_if_needed(method, path)
        response = await self._client.request(method, path, params=params, json=json_body)

        if response.status_code == 429:
            raise self._build_rate_limit_error(method, path, response)

        self._limiter.observe_response(method, path, response.headers)

        if response.status_code == 401:
            raise SegmentAuthError(
                "Segment rejected this token (401). If this token works "
                "against the other region, set SEGMENT_REGION to that "
                "region instead of guessing — see "
                "SegmentPublicAPIClient.verify_region().",
                status_code=401,
            )
        if response.status_code == 403:
            raise self._classify_permission_error(response)

        response.raise_for_status()
        return _parse_json_object(response)

    def _build_rate_limit_error(
        self, method: str, path: str, response: httpx.Response
    ) -> SegmentRateLimitError:
        retry_after_header = response.headers.get("Retry-After")
        retry_after = _parse_retry_after(retry_after_header) if retry_after_header else None

        body = _try_json_object(response)
        data = _as_str_keyed_dict(body.get("data"))

        ms_before_next = data.get("msBeforeNext")
        if retry_after is None and isinstance(ms_before_next, int | float):
            retry_after = ms_before_next / 1000

        used_fallback_backoff = False
        if retry_after is None:
            # No Retry-After header and no data.msBeforeNext — the 429
            # gave us no timing signal at all. This is *not* a guess at
            # Segment's unpublished global rate limit (BUILD-PLAN.md §4
            # explicitly says never hard-code that); it's just a floor so
            # the next call to this endpoint doesn't immediately hammer
            # the API again while we wait for a real signal.
            retry_after = _DEFAULT_RATE_LIMIT_BACKOFF_SECONDS
            used_fallback_backoff = True

        self._limiter.observe_retry_after(method, path, retry_after)

        is_token_level = retry_after_header is not None and not data
        scope = "token-level" if is_token_level else "endpoint"
        message = f"Segment rate-limited this request ({scope}, 429)."
        if used_fallback_backoff:
            message += (
                f" No timing signal in the response; backing off "
                f"{retry_after:.1f}s as a conservative default."
            )
        else:
            message += f" Retry after {retry_after:.1f}s."

        return SegmentRateLimitError(
            message,
            retry_after=retry_after,
            remaining_points=data.get("remainingPoints"),
            consumed_points=data.get("consumedPoints"),
        )

    def _classify_permission_error(self, response: httpx.Response) -> SegmentPermissionError:
        message = _extract_error_message(response) or "Segment refused this request (403)."
        lowered = message.lower()
        if any(keyword in lowered for keyword in _TIER_KEYWORDS):
            return SegmentTierError(
                "The Segment Public API is unavailable for this workspace "
                f"(403): {message} The Public API requires Team or "
                "Business tier — it is not available on Free or Add-on "
                "plans.",
                status_code=403,
            )
        return SegmentPermissionError(
            f"Segment refused this request (403): {message} If this token "
            "is valid but lacks permission, note that only a Workspace "
            "Owner can mint a Public API token — check that the workspace "
            "owner who created it has the role this operation needs.",
            status_code=403,
        )

    async def verify_region(self) -> None:
        """Startup self-check: confirm this token authenticates against
        the configured region before doing anything else.

        Calls the cheap `GET /workspaces` endpoint. On a 401, probes the
        *other* region with the same token as a diagnostic: if it
        succeeds there, raises `RegionMismatchError` naming the correct
        region instead of leaving the caller to debug a bare 401. This
        assumes Segment Public API tokens only authenticate against their
        own region's API domain — unverified against a live workspace,
        see BUILD-PLAN.md §11.4; if that assumption is wrong the probe
        simply fails the same way and the original 401 is re-raised.
        """
        try:
            await self.get(_SELF_CHECK_PATH)
        except SegmentAuthError:
            probe_region = other_region(self.region)
            async with SegmentPublicAPIClient(
                self._token,
                probe_region,
                base_url=endpoints_for(probe_region).public_api,
                timeout=self._timeout,
                transport=self._probe_transport,
            ) as probe:
                try:
                    await probe.get(_SELF_CHECK_PATH)
                except SegmentAPIError:
                    raise  # Not a region issue — the token itself is bad.
            raise RegionMismatchError(
                f"SEGMENT_REGION={self.region.value!r} does not match this "
                f"token's workspace: it authenticates against the "
                f"{probe_region.value!r} region instead. Set "
                f"SEGMENT_REGION={probe_region.value!r}."
            ) from None


def _as_str_keyed_dict(value: object) -> dict[str, Any]:
    """Narrow `object` to `dict[str, Any]`, or `{}` if it isn't one."""
    if isinstance(value, dict):
        untyped = cast("dict[object, object]", value)
        if all(isinstance(key, str) for key in untyped):
            return cast("dict[str, Any]", value)
    return {}


def _try_json_object(response: httpx.Response) -> dict[str, Any]:
    """Best-effort JSON object parse. Returns `{}` on any failure — for
    call sites that want to degrade gracefully rather than raise."""
    try:
        body: object = response.json()
    except (json.JSONDecodeError, ValueError):
        return {}
    return _as_str_keyed_dict(body)


def _extract_error_message(response: httpx.Response) -> str | None:
    body = _try_json_object(response)
    for key in ("message", "error", "errorMessage"):
        value = body.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _parse_json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        body: object = response.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise SegmentMalformedResponseError(
            f"Segment returned a response that was not valid JSON (status {response.status_code}).",
            status_code=response.status_code,
        ) from exc
    parsed = _as_str_keyed_dict(body)
    if not parsed and body != {}:
        raise SegmentMalformedResponseError(
            f"Segment returned JSON that was not a string-keyed object "
            f"(got {type(body).__name__}, status {response.status_code}).",
            status_code=response.status_code,
        )
    return parsed
