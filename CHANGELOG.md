# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

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
