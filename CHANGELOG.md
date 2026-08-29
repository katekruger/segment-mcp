# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- The five v0.1 composed tools (BUILD-PLAN.md §5), registered in
  `server.py` with `readOnlyHint: true` / `destructiveHint: false`
  explicit on all five (the MCP spec defaults `destructiveHint` to
  `true`):
  - `audit_event_routing` — sources → connected destinations → settings →
    subscriptions, one routing report. Degrades gracefully (not an error)
    when destination Subscriptions is unavailable — confirmed Alpha,
    workspace-enablement-gated.
  - `trace_event` — given an event name: tracking-plan coverage (an event
    in no plan is reported as "governed by nothing", not an empty
    result), confirmed emitting sources via `GET /events/volume`, and
    connected destinations/warehouses for a bounded set of sources.
  - `find_stale_sources` — a source with zero activity in the entire
    queried window is `insufficient_data`, not `stale`: the Public API
    exposes no source creation date, so this tool can't tell "new" from
    "dead" and says so rather than guessing.
  - `check_delivery_health` — a requested window wider than what the
    endpoint allows is capped, not silently narrowed.
  - `find_ungoverned_sources` — cross-references tracking-plan coverage
    against `sources/{id}/settings.track.allowUnplannedEvents`, so
    "governed but still allowing unplanned events" is its own category,
    not lumped in with "fully governed".
  - `client/public_api.py`: `get_data()` (unwraps Segment's `data`
    envelope) and `paginate()` (cursor pagination via
    `pagination.count`/`pagination.cursor`), both verified against
    docs.segmentapis.com.
  - `tools/_shared.py`: resource-fetch helpers (`list_sources_scoped`,
    `list_tracking_plans`, `event_volume_by_source`) shared across tools,
    plus the `ScopedList`/`Gap` shapes every tool uses to report
    truncation and graceful degradation consistently.

### Fixed

- Three Prompt 1 fixtures (`workspaces_200.json` both regions,
  `source_no_connected_destinations_200.json`,
  `tracking_plan_no_sources_200.json`) were missing Segment's `data`
  response envelope — confirmed against docs.segmentapis.com during this
  prompt's research, unverified when they were written. Corrected in
  place; the two client tests that asserted on the old shape now go
  through `get_data()`.

### Known discrepancy from BUILD-PLAN.md

- §5 describes `check_delivery_health`'s window as "30-DAY WINDOW MAX".
  The live docs (docs.segmentapis.com, Destinations tag) show tighter,
  granularity-specific limits instead: MINUTE allows at most a 4-hour
  range with data no older than 48 hours; HOUR allows 7 days with data no
  older than 7 days; DAY allows 14 days with data no older than 14 days —
  none of which is 30 days. Implemented against the verified API rather
  than the plan text; see `tools/health.py`'s module docstring.

- Project scaffolding: `src/` layout, package skeleton, CI, and repo hygiene
  files, per `BUILD-PLAN.md`.
- ADR 0002: Tier 1 (`POST /regulations` and its per-source sibling) is
  permanently unreachable, in every mode.
- `BUILD-PLAN.md` committed as the first commit on `main`.
- `client/regions.py`: explicit `SEGMENT_REGION` resolution with no US
  default — unset or invalid values fail loudly and name both valid
  values, per BUILD-PLAN.md §0.6.
- `client/public_api.py`: async Public API client — Bearer auth, no `/v1`
  in the path, a per-endpoint rate limiter driven entirely by observed
  `X-RateLimit-*`/`Retry-After`/`data.msBeforeNext` response data (never a
  hard-coded global budget), RFC 5322 header parsing, and error
  classification for 401/403/429/malformed responses.
- `SegmentPublicAPIClient.verify_region()`: startup self-check that probes
  the other region on a 401 and raises `RegionMismatchError` naming the
  correct region instead of surfacing a bare auth failure.
- `tests/fixtures/{us,eu}/`: recorded-shaped Public API response fixtures
  (429 with/without `Retry-After`, a 429 with no timing signal at all, a
  source with no connected destinations, a tracking plan with no sources,
  a Free-tier 403, an insufficient-permissions 403, and two malformed
  responses) — hand-authored from documented API shapes, not yet captured
  from a live workspace; see `tests/fixtures/README.md` for why.
