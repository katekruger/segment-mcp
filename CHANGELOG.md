# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security

- **The Tier 1 client guard's decode-then-normalize model missed a
  decode-then-*route*-without-normalizing downstream behaviour, letting a
  trailing `%2f..` family through (CLOSE3-1, high severity, latent).** A
  1,164-variant fuzz run found 14 misses, all one root cause:
  `POST /regulations%2f..` decodes to `/regulations/..`, which
  `_normalize_path_segments` collapses to `/` — not blocked — but a
  decode-then-prefix-route gateway (nginx `location /regulations`-style
  routing, which never collapses `.`/`..` segments before matching)
  routes the un-normalized decoded path straight to the `/regulations`
  handler. The guard's pessimism ran one way only: it refused
  `/%2e%2e/regulations` (dangerous only if the edge normalizes) while
  allowing `/regulations%2f..` (dangerous only if it doesn't) — same
  threat model, opposite outcomes. `_refuse_if_tier1_mutation` now
  checks the blocked-prefix comparison against both the decoded-but-not-
  normalized path and the decoded-and-normalized path, refusing if
  either matches. `tests/test_tier1_unreachable.py` adds the 14-variant
  family to `TIER1_BYPASS_PATHS` and an `"append %2f.."` composition
  transform so the existing generative fuzz test covers this family
  going forward instead of only the strings someone already wrote down.
  See ADR 0004's second addendum.

- **A second log line in `client/profile_api.py` printed the raw
  identifier in plaintext (CLOSE-3, high severity — PII).** The
  case-normalization warning logged both the raw and lowercased lookup
  value at WARNING, a level that survives any log level a deployment
  would plausibly set, and fired on any non-lowercase input — the common
  case for an email address, not the edge case. This directly undermined
  the module's own documented purpose (the INFO-level lookup log a few
  lines below it was already correctly hashed). A second instance:
  `SegmentProfileNotFoundError`'s 404 message embedded the raw lookup key
  via `{lookup_key!r}`, and exception messages reach logs and error
  trackers the same way a log line does. Both now log/embed
  `id_type` and the same truncated SHA-256 digest the INFO-level lookup
  log already used, never the raw value.
  `tests/client/test_profile_api.py` captures at DEBUG (not just
  WARNING) and asserts no emitted record, and no 404 exception's
  `str()`, contains the raw identifier in any case variant.

- **The Tier 1 client guard's `base_url.join()`-based resolution wasn't
  actually the resolution httpx uses, and didn't decode percent-encoded
  dot-segments (CLOSE-2, high severity, latent).** A second external
  audit found `httpx.AsyncClient.request()` resolves a relative path with
  its own `_merge_url`, not `URL.join()` — the two disagree on a leading
  `//`, where `join()` models a different host while the real client
  strips it and sends to the base host's root (harmless as reported, but
  the guard's model of where the request was going was simply wrong).
  Separately, neither resolver percent-decodes before comparing, so
  `/%2e%2e/regulations` resolved to the literal path
  `/%2e%2e/regulations`, not `/regulations` — whether Segment's own edge
  decodes-then-normalizes before routing (common at CDN/gateway layers)
  isn't verifiable from outside, so the guard now assumes the pessimistic
  case. `_refuse_if_tier1_mutation` now refuses any `//`-prefixed path
  outright (this client never legitimately constructs one), resolves
  everything else with the client's own `build_request()` instead of a
  parallel reimplementation, and percent-decodes to a fixed point plus
  re-normalizes dot-segments/repeated-slashes before comparing.
  `tests/test_tier1_unreachable.py` adds the percent-encoded-dot-segment
  family to `TIER1_BYPASS_PATHS`, a test asserting the guard's resolved
  URL equals what a real transport receives for every bypass path, and a
  property test that generates bypass compositions instead of only
  listing them. See ADR 0004's addendum.

- **`client/profile_api.py` had the exact ENG-1 path-traversal bug,
  unfixed, in a second file (CLOSE-1, high severity, latent).** Latent
  only because `tools/profiles.py` is a docstring-only stub, so
  `ProfileAPIClient` was unreachable from any registered tool — it would
  have gone live, unaudited, the moment `lookup_profile` was implemented.
  `ProfileAPIClient._get()` interpolated `space_id`, `collection`,
  `route`, and an `{id_type}:{id_value}` lookup key directly into a
  request path with none of them validated — confirmed live against a
  recording transport, `id_value="../../../../regulations"` sent
  `GET .../collections/regulations/traits` instead of a profile lookup.
  `space_id` is now validated once at construction; `collection`/`route`
  get a runtime membership check backing their `Literal` types (which are
  static-only and do nothing at runtime); `id_type` is validated via
  `validate_resource_id`; `id_value` gets a new dedicated validator,
  `validate_profile_lookup_value` (`client/validation.py`), since a
  legitimate value is an email address that `validate_resource_id`'s
  bare-identifier pattern would wrongly refuse. See ADR 0003's
  correction section.
- **`check_delivery_health`'s `source_id` was the one Public API tool
  argument left unvalidated (CLOSE-1 bonus, informational — not a
  traversal vector).** It only ever reaches a query parameter, which
  httpx percent-encodes, not a request path. Validated anyway via
  `validate_resource_id` for consistency with every other resource ID
  this server accepts as a tool argument.

## 0.1.2 - 2026-08-30

### Security

- **Path traversal via tool-supplied `source_id`/`destination_id`
  (ENG-1, high severity).** Every ID interpolated into a Public API
  request path now goes through `client/validation.py`'s
  `validate_resource_id()`, which refuses anything that isn't a bare
  Segment identifier before the request is built — confirmed live before
  this fix, `source_id="../regulations"` sent
  `GET https://api.segmentapis.com/regulations` instead of a source
  lookup, and `source_id="../../v1beta/users?x=1"` escaped further and
  injected a query string, both using a Workspace Owner token. Applied at
  every tool-argument boundary and at every ID drawn from a prior API
  response (`source.id`, `plan.id`) before it's reused in the next
  request. See ADR 0003.
- **Tier 1 client guard was bypassable via URL forms it never normalized
  (ENG-2, high severity, latent — no non-GET tool exists yet to trigger
  it).** `_refuse_if_tier1_mutation()` now resolves the request path
  against the client's base URL with `httpx.URL.join()` before comparing
  it to the blocked prefix, closing seven bypasses the old string-prefix
  check missed: a `#fragment` it never stripped, a `.`/`..` path segment
  it never normalized, a missing leading slash, an absolute URL override,
  and case variation (`/REGULATIONS`, `/Regulations`).
  `tests/test_tier1_unreachable.py` is now parametrized over all ten known
  bypass strings across all four non-GET methods (40 cases), plus negative
  cases proving `POST /sources` and a legitimate `/regulations-adjacent`
  path are not caught by the boundary check. See ADR 0004.

### Fixed

- Profile API lookups (`client/profile_api.py`) no longer log the raw
  identifier at INFO level — `email:jane@example.com`-shaped values are
  now logged as `id_type=email key_sha256=<16-hex-char digest>`, keeping
  lookups correlatable across log lines for auditing without putting a
  reversible PII value in application logs.
- `segment-mcp --help` and `segment-mcp --version` now exit 0 without
  requiring `SEGMENT_API_TOKEN`/`SEGMENT_REGION` or making any network
  call — previously `main()` ran the fatal startup checks unconditionally
  and ignored `sys.argv` entirely, so the first thing anyone typed after
  installing died with a config error.
- `server.json`'s `description` was 206 characters — over the MCP
  Registry's 100-character limit. This is what broke the v0.1.1 release's
  `publish to the MCP Registry` job (a 422 after PyPI publish and the
  GitHub Release had already succeeded, leaving that release partially
  published). Shortened to fit; `tests/test_server_json.py` now guards
  `description`/`title`/`name` against the registry's length limits so
  this can't silently regress on a future release.
- `release.yml`: `publish-mcp-registry` now sits behind its own
  `mcp-registry-release` environment, requiring manual approval
  independent of the existing `release` environment that gates PyPI
  publish. Previously it auto-ran the instant PyPI publish succeeded, with
  no window to decide "PyPI now, registry separately" for a given tag.

## 0.1.1 - 2026-08-30

### Added

- `server.json`: the MCP Registry manifest for `io.github.katekruger/segment-mcp`,
  describing the PyPI package and its environment variables.
- `<!-- mcp-name: io.github.katekruger/segment-mcp -->` marker in
  README.md, required by the MCP Registry's PyPI ownership check (it
  reads this from the *published* package README, which is why this is a
  patch release rather than a retrofit onto 0.1.0 — the check reads
  whatever's live on PyPI, and 0.1.0 shipped without it).
- `release.yml`: `publish-mcp-registry` job, running after the PyPI
  publish, authenticating via GitHub OIDC (no token, same Trusted-Publishing
  posture as the PyPI job) and re-stamping `server.json`'s version from
  the release tag before publishing — so every future tagged release
  re-publishes to the MCP Registry automatically, not just this one.

## 0.1.0 - 2026-08-30

### Changed

- Repo flipped public. Secret scanning, push protection, CodeQL default
  setup, private vulnerability reporting, and Dependabot alerts + security
  updates all enabled. `ci.yml`: expanded from a single job to a Python
  3.12/3.13 matrix, now that Actions minutes aren't metered the way they
  were while private. `zizmor.yml`'s SARIF upload no longer needs
  `continue-on-error` now that code scanning is on.

### Added

- `server.py`: fatal startup checks (`run_startup_checks()`) — region set
  and valid, token present and actually authenticating, workspace tier
  supports the Public API (a Free-tier 403 surfaces as a clear message,
  not a raw error) — all before the server accepts a single tool call.
  `[project.scripts]` now provides a real `segment-mcp` entry point
  (`uv run segment-mcp`).
- `server.py`: `register_tools()` registers only the tools the current
  `SEGMENT_MCP_MODE` reaches, via `modes.is_tier_reachable()`. Every v0.1
  tool is `Tier.READ` so nothing is filtered out yet, but the mechanism
  is real and tested with fabricated Tier 2/3 specs, not deferred until
  the first write tool needs it.
- `server.py`: the three-tier model is now in the server's MCP
  `instructions` (current mode, what each tier requires, Tier 1's
  permanent refusal) — visible to a connecting client, not just
  documented in README.md.
- `.github/workflows/release.yml`: tag-triggered release, parsedmarc
  pattern — verifies the tag matches the package version and that
  `CHANGELOG.md` has a section for it, runs the full CI suite, builds,
  publishes to PyPI via Trusted Publishing (OIDC, no API token secret
  anywhere) into a `release` environment requiring manual approval
  (PEP 740 attestations come free under Trusted Publishing), then creates
  the GitHub Release from that CHANGELOG section.
- README.md rewritten to BUILD-PLAN.md's specified order: badges, one
  line, read-only-by-default above the fold, the Tier 1 refusal stated
  plainly, quick start, the five tools framed as questions, prerequisites,
  region configuration with the EU silent-failure warning, and a link to
  `docs/what-this-refuses-to-do.md` — before any of the Modes/Profile
  API/development detail that follows.

- `modes.py`: the `SEGMENT_MCP_MODE` (`read`/`write`/`admin`) tier model
  from BUILD-PLAN.md §6 — `authorize()` decides whether an action at a
  given tier may run under the current mode, with an echo-confirmation
  gate for Tier 3 and a *typed*, resource-naming confirmation gate for
  Tier 2/4. Tier 1 raises `Tier1UnreachableError` unconditionally, before
  even checking the mode. Reuses the risk-tier/hard-block/confirm-gate
  *pattern* already proven in `instantly-mcp`'s autonomy tiers,
  reimplemented natively (no dependency on that project) under this
  project's own vocabulary.
- `client/public_api.py`: `Tier1BlockedError` — the client independently
  refuses any mutating request to `/regulations*`, before it reaches the
  network, regardless of what calls it or what mode is active.
- `tests/test_tier1_unreachable.py`: proves Tier 1 is unreachable three
  independent ways (modes.py, the client, and tool introspection),
  deliberately redundantly — see BUILD-PLAN.md §6.
- `docs/what-this-refuses-to-do.md`: the differentiating document — Tier 1
  and why, the `PUT`-replaces-all trap on tracking-plan rules and source
  labels (with the full-set-diff requirement any future v0.3 tool must
  implement), and why Tracking API writes are never exposed, including an
  honest note that both existing community Segment MCP servers lead with
  exactly that write path.
- `client/profile_api.py`: the Profile API client — a separate trust tier.
  HTTP Basic auth with the access token as username and a blank password;
  lookup values are lowercased at the client boundary with a logged
  warning when normalization changed something (wrong case returns an
  empty result from Segment, not an error); every lookup is logged
  (collection, normalized key, route, caller) before the request is sent.
  Constructed only with a separate `SEGMENT_PROFILE_TOKEN` +
  `SEGMENT_PROFILE_SPACE_ID` — no profile-lookup tool is wired into
  `server.py` yet (that's v0.2 tool-surface work); this is the trust
  boundary a future tool will be built on.
- README.md: Modes and Profile API sections; `.env.example`:
  `SEGMENT_PROFILE_SPACE_ID`.

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
