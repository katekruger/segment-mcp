"""The test that proves the thesis: Tier 1 (`POST /regulations` and
`POST /regulations/sources/{id}` — permanent, irreversible data deletion)
is not reachable, in any mode, through any path. Belt and braces,
deliberately — see BUILD-PLAN.md §6 and
docs/decisions/0002-tier-1-permanently-unreachable.md.

Three independent checks, any one of which failing is a critical bug:
1. `modes.authorize()` refuses Tier 1 outright — before even looking at
   mode.
2. The client itself refuses to send the request, in every mode.
3. No registered tool definition references `/regulations` at all.

Check 2 is parametrized over the bypass strings a first audit found (three
that a naive `path.split("?", 1)[0]` string-prefix check already caught,
seven a `base_url.join()`-based rewrite closed — a `#fragment`, a
`.`/`..`-segment, a missing leading slash, an absolute URL, and case
variation) plus a second audit's percent-encoded-dot-segment family
(`/%2e%2e/regulations` and friends) — across all four non-GET methods, per
docs/decisions/0004-tier1-guard-matches-resolved-url.md. Never relax this
back down to a smaller list or to a single method: the whole point of each
rewrite is that the previous, narrower version passed while the guard was
still bypassable.

`//regulations` is deliberately *not* in that "must raise" list — verified
against `httpx.AsyncClient.build_request`, it resolves to the base host's
*root* (`/`), not to `/regulations`, so asserting `Tier1BlockedError` for
it would assert something false about where it actually goes. It has its
own dedicated test below instead, proving the guard refuses it anyway
(defensively, as a protocol-relative reference this client never
legitimately constructs) without claiming a resolution that isn't real.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from segment_mcp import server
from segment_mcp.client.public_api import (
    SegmentPublicAPIClient,
    Tier1BlockedError,
    _decode_fully,  # pyright: ignore[reportPrivateUsage]
    _normalize_path_segments,  # pyright: ignore[reportPrivateUsage]
)
from segment_mcp.client.regions import Region
from segment_mcp.modes import Mode, Tier, Tier1UnreachableError, authorize

# --------------------------------------------------------------------------
# 1. modes.py refuses it — belt
# --------------------------------------------------------------------------


@pytest.mark.parametrize("mode", [Mode.READ, Mode.WRITE, Mode.ADMIN])
def test_authorize_refuses_tier1_in_every_mode(mode: Mode) -> None:
    with pytest.raises(Tier1UnreachableError):
        authorize(Tier.TIER1, mode)


def test_authorize_refuses_tier1_even_with_every_confirmation_supplied() -> None:
    # Confirming harder is not a way around this. There is no argument
    # combination that makes Tier 1 authorize() return allowed=True.
    with pytest.raises(Tier1UnreachableError):
        authorize(
            Tier.TIER1,
            Mode.ADMIN,
            confirmed=True,
            typed_confirmation="anything",
            expected_resource="anything",
        )


# --------------------------------------------------------------------------
# 2. The client refuses the path — braces
# --------------------------------------------------------------------------

# The three strings the original (string-prefix) guard already caught,
# the seven the first audit confirmed it missed, and the second audit's
# percent-encoded-dot-segment family (including one composed with an
# encoded slash, and one with a trailing slash) — all of which must be
# refused for every non-GET method. See the module docstring for why
# `//regulations` is a separate list.
TIER1_BYPASS_PATHS = [
    # Original three.
    "/regulations",
    "/regulations/sources/src_1",
    "/regulations/cloudsources/src_1",
    # First audit's seven.
    "/regulations#frag",
    "/./regulations",
    "/foo/../regulations",
    "regulations",
    "https://api.segmentapis.com/regulations",
    "/REGULATIONS",
    "/Regulations",
    # Second audit's percent-encoded-dot-segment family.
    "/%2e%2e/regulations",
    "/%2e/regulations",
    "/%2E%2E/regulations",
    "/..%2fregulations",
    "/%2f/regulations",
    "/./%2e%2e/regulations",
    "/%2e%2e/REGULATIONS",
    "/%2e%2e/regulations?x=1",
    "/%2e%2e/regulations/sources/s1",
    "/%2e%2e/regulations/",
    # Third audit's trailing-%2f.. family: decode-then-normalize collapses
    # "/regulations/.." to "/" (not blocked), but decode-then-prefix-route
    # *without* normalizing — how nginx `location /regulations` and most
    # API gateways behave — routes the un-normalized decoded path to the
    # /regulations handler. The guard must refuse whichever model is
    # pessimistic, not just the one httpx's own normalization happens to
    # collapse. See docs/decisions/0004-tier1-guard-matches-resolved-url.md.
    "/regulations%2f..",
    "/REGULATIONS%2F..",
    "regulations%2f..",
    "/./regulations%2f..",
    "/foo/../regulations%2f..",
    "/regulations%2f..#frag",
    "/regulations%2f..?x=1",
    "/Regulations%2f..",
    "https://api.segmentapis.com/regulations%2f..",
    "/regulations%2F..",
    "/regulations%2f../",
    "/regulations%2f../sources/s1",
    "/regulations%2f..%2f",
    "/regulations%2f%2e%2e",
]

# `//regulations` resolves to the base host's root, not to `/regulations`
# (verified against `build_request`) — refused defensively by the
# leading-`//` check, not by the prefix comparison the paths above go
# through. Kept separate so the equality test below (which proves the
# guard's *resolved* path matches the transport's) isn't asked to assert
# an equality that would be false for this one.
DOUBLE_SLASH_BYPASS_PATH = "//regulations"

NON_GET_METHODS = ["POST", "PUT", "PATCH", "DELETE"]


@pytest.mark.parametrize("method", NON_GET_METHODS)
@pytest.mark.parametrize("path", TIER1_BYPASS_PATHS)
async def test_client_refuses_every_mutation_bypass_form(path: str, method: str) -> None:
    # No transport is configured — if this reached the network layer at
    # all, it would hang or error on a real connection attempt instead of
    # raising Tier1BlockedError immediately. It must never get that far.
    client = SegmentPublicAPIClient("fake-token", Region.US)
    async with client:
        with pytest.raises(Tier1BlockedError):
            await client._request(method, path)  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("method", NON_GET_METHODS)
async def test_client_refuses_every_bypass_form_against_eu_base_url_too(method: str) -> None:
    # The guard resolves against the client's own base_url — prove that
    # holds for the EU region too, not just the US one every other test
    # in this file uses.
    client = SegmentPublicAPIClient("fake-token", Region.EU)
    async with client:
        with pytest.raises(Tier1BlockedError):
            await client._request(method, "/regulations")  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("method", NON_GET_METHODS)
async def test_client_refuses_double_slash_bypass_defensively(method: str) -> None:
    # See DOUBLE_SLASH_BYPASS_PATH's definition above: this doesn't
    # actually resolve to /regulations (it resolves to the base host's
    # root), so it's refused by the dedicated protocol-relative-path
    # check, not the prefix comparison — but it must still raise.
    client = SegmentPublicAPIClient("fake-token", Region.US)
    async with client:
        with pytest.raises(Tier1BlockedError):
            await client._request(  # pyright: ignore[reportPrivateUsage]
                method, DOUBLE_SLASH_BYPASS_PATH
            )


# --------------------------------------------------------------------------
# Gap B, closed: the guard used to resolve URLs differently than the
# transport (`base_url.join()` vs. what `AsyncClient` actually sends),
# which is how `//regulations` — and, worse, a `//host/path` form —
# could pass the guard's reasoning while landing somewhere the guard
# never considered. Prove the two now agree, for every bypass string,
# not just spot-check one.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", TIER1_BYPASS_PATHS)
async def test_guard_resolved_path_matches_the_transports_actual_path(path: str) -> None:
    # URL resolution (base_url + path -> final URL) doesn't depend on the
    # HTTP method, only on the path — so a GET to the same path (which
    # the guard doesn't intercept) reaches the real transport and lets us
    # capture the URL httpx actually sends, to compare against what the
    # guard computes for the *blocked* method it would never let through.
    # This is the test Gap B's fix promises: the guard isn't just
    # "probably right" about what will be sent, it's checked against it.
    captured: dict[str, httpx.URL] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = request.url
        return httpx.Response(200, json={"data": {}})

    client = SegmentPublicAPIClient("fake-token", Region.US, transport=httpx.MockTransport(handler))
    async with client:
        await client.get(path)
        guard_resolved_url = client._client.build_request(  # pyright: ignore[reportPrivateUsage]
            "POST", path
        ).url
        assert guard_resolved_url == captured["url"]


# --------------------------------------------------------------------------
# Gap C, closed: the old bypass list was exactly the set of strings a
# previous audit happened to report — it proved the last fix held, not
# that the next one didn't exist. This generates bypass compositions
# instead of only listing them, by composing the transformations that
# caused each historical bypass, and asserts every composition that
# resolves to /regulations is still refused.
# --------------------------------------------------------------------------

_COMPOSITION_TRANSFORMS: list[tuple[str, Callable[[str], str]]] = [
    ("prepend /./", lambda p: "/." + p),
    ("wrap in /foo/../", lambda p: "/foo/.." + p),
    ("percent-encode first char", lambda p: f"/%{ord(p[1]):02x}{p[2:]}" if len(p) > 1 else p),
    ("uppercase", lambda p: p.upper()),
    ("drop leading slash", lambda p: p.lstrip("/")),
    ("append fragment", lambda p: p + "#frag"),
    ("append query", lambda p: p + "?x=1"),
    ("append %2f..", lambda p: p + "%2f.."),
]


def _compose_bypass_variants(base: str, *, max_depth: int = 2) -> list[str]:
    """All compositions of `_COMPOSITION_TRANSFORMS`, up to `max_depth`
    deep, applied to `base`. `max_depth=2` already gives 7 + 7*7 = 56
    variants per base path — enough to catch a transform interacting
    badly with another without exploding runtime."""
    variants = [base]
    frontier = [base]
    for _ in range(max_depth):
        next_frontier: list[str] = []
        for path in frontier:
            for _name, transform in _COMPOSITION_TRANSFORMS:
                try:
                    candidate = transform(path)
                except (ValueError, IndexError):
                    continue
                next_frontier.append(candidate)
        variants.extend(next_frontier)
        frontier = next_frontier
    return variants


def _matches_regulations_prefix(candidate: str) -> bool:
    folded = candidate.rstrip("/").casefold()
    return folded == "/regulations" or folded.startswith("/regulations/")


def _resolves_to_regulations(client: SegmentPublicAPIClient, path: str) -> bool:
    """Independent re-implementation of the guard's own resolution, used
    only to decide which generated variants *should* be blocked — kept
    deliberately identical in spirit to, but a separate call path from,
    the guard under test, the same way the guard itself now matches
    httpx's `build_request` instead of reimplementing resolution.

    Models *both* downstream behaviours a real edge/gateway might apply
    to the decoded path: routing on the un-normalized decoded path
    directly (nginx `location /regulations`-style prefix routing) and
    routing after collapsing `.`/`..` segments (a decode-then-normalize
    gateway). A path only has to be dangerous under one of the two for
    the guard to be required to refuse it — see the trailing-%2f..
    family in TIER1_BYPASS_PATHS above."""
    if path.startswith("//"):
        return True  # refused defensively regardless of resolution
    resolved_path = client._client.build_request(  # pyright: ignore[reportPrivateUsage]
        "POST", path
    ).url.path
    decoded = _decode_fully(resolved_path)
    normalized = _normalize_path_segments(decoded)
    return _matches_regulations_prefix(decoded) or _matches_regulations_prefix(normalized)


async def test_every_composed_bypass_that_resolves_to_regulations_is_refused() -> None:
    client = SegmentPublicAPIClient("fake-token", Region.US)
    checked = 0
    async with client:
        for base in ["/regulations", "regulations"]:
            for variant in _compose_bypass_variants(base):
                if not _resolves_to_regulations(client, variant):
                    continue
                checked += 1
                with pytest.raises(Tier1BlockedError):
                    await client._request(  # pyright: ignore[reportPrivateUsage]
                        "POST", variant
                    )
    # A regression here (checked == 0) would mean this test silently
    # stopped exercising anything — fail loudly instead of passing empty.
    assert checked > 0, "no composed variant resolved to /regulations — nothing was exercised"


# --------------------------------------------------------------------------
# Negative cases — a naive `startswith` fix without a `/` boundary check
# would block a legitimate future endpoint that merely starts with the
# same letters. These must NOT raise.
# --------------------------------------------------------------------------


async def test_client_still_allows_reading_regulations() -> None:
    # The refusal is POST-shaped, not path-shaped — GET /regulations,
    # GET /regulations/{id}, and GET /suppressions are in scope for v0.2
    # (BUILD-PLAN.md §6) and must not be caught by this guard.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"regulations": []}})

    client = SegmentPublicAPIClient("fake-token", Region.US, transport=httpx.MockTransport(handler))
    async with client:
        body = await client.get("/regulations")
    assert body == {"data": {"regulations": []}}


async def test_client_allows_posting_to_an_unrelated_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"data": {"source": {"id": "src_new"}}})

    client = SegmentPublicAPIClient("fake-token", Region.US, transport=httpx.MockTransport(handler))
    async with client:
        body = await client._request("POST", "/sources")  # pyright: ignore[reportPrivateUsage]
    assert body == {"data": {"source": {"id": "src_new"}}}


async def test_client_allows_posting_to_a_path_that_merely_starts_with_regulations() -> None:
    # A resource genuinely named "regulations-adjacent" is not
    # "/regulations" or a child of it — a boundary-unaware `startswith`
    # fix would wrongly block this.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"data": {"id": "ra_1"}})

    client = SegmentPublicAPIClient("fake-token", Region.US, transport=httpx.MockTransport(handler))
    async with client:
        body = await client._request(  # pyright: ignore[reportPrivateUsage]
            "POST", "/regulations-adjacent"
        )
    assert body == {"data": {"id": "ra_1"}}


# --------------------------------------------------------------------------
# 3. No tool definition references it
# --------------------------------------------------------------------------


async def test_no_registered_tool_mentions_regulations() -> None:
    tools = await server.mcp.list_tools()
    assert len(tools) > 0, "expected at least the five v0.1 tools to be registered"
    for tool in tools:
        haystack = " ".join([tool.name, tool.description or "", str(tool.input_schema)]).lower()
        assert "regulation" not in haystack, (
            f"tool {tool.name!r} references 'regulation' — Tier 1 must never "
            "surface as a callable capability, directly or indirectly"
        )
