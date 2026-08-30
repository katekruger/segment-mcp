---
status: "accepted"
date: "2026-08-30"
deciders: "Kate Kruger"
---

# Refuse, don't encode, path traversal in tool-supplied resource IDs

## Context and Problem Statement

Every `source_id`, `destination_id`, and tracking-plan ID this server
handles is interpolated directly into a Public API request path, e.g.
`f"/sources/{source_id}"`. `httpx` normalizes dot-segments in a request
path before the request goes out over the wire. An external audit against
HEAD (30 Aug 2026) confirmed this live: `source_id="../regulations"` sends
`GET https://api.segmentapis.com/regulations` instead of a source lookup,
and `source_id="../../v1beta/users?x=1"` both escapes further and injects
a query string. Every affected call is a `GET`, so this is a read-surface
escape rather than a write — still live, using a Workspace Owner token,
and reachable directly from LLM-supplied tool arguments. Should this
server escape/percent-encode such values, reject them outright, or leave
the client's usual error handling to catch the resulting 404s?

## Decision Drivers

- The tool arguments here (`source_id`, `destination_id`) are LLM-supplied
  in normal operation — this is not a hypothetical malicious-user input,
  it is the ordinary shape of every tool call, and an LLM can be
  manipulated into supplying an adversarial value via prompt injection in
  content it reads (a source name, a destination setting) as easily as by
  a user typing one in directly.
- A Segment resource ID is always an opaque alphanumeric string. There is
  no legitimate ID this rejects, so a validation gate here has zero false
  positives against real usage.
- Percent-encoding `../regulations` turns it into a literal (and
  nonsensical) path segment that 404s — technically "safe" in that the
  traversal doesn't occur, but it hides what happened behind a confusing
  error instead of naming the actual problem to whoever is debugging the
  tool call.
- This client's own hand-picked, non-generated tool surface (AGENTS.md)
  means every path-building call site is enumerable; a single shared
  validator applied at each one is tractable, unlike patching a
  generated ~200-endpoint surface after the fact.

## Considered Options

- Percent-encode resource ID values before interpolating them into a path
- Reject any value that isn't a bare Segment identifier before building
  the request, at every call site that interpolates one
- Leave it to Segment's API to 404 on a malformed path and treat that as
  sufficient

## Decision Outcome

Chosen option: "Reject any value that isn't a bare Segment identifier
before building the request, at every call site that interpolates one."
`validate_resource_id()` (`client/validation.py`) matches against
`\A[A-Za-z0-9_-]{1,64}\Z` and raises `InvalidResourceIdError` — a
`SegmentAPIError` subclass — on anything else. It's applied at the tool
boundary (where a `source_id`/`destination_id` tool argument first enters
a composed tool) and again at every point an ID drawn from a *previous*
API response (`source.id`, `plan.id`, a destination ID from a
`connected-destinations` list) is about to be interpolated into the next
request — a compromised or malformed upstream response is the same attack
with an extra hop, and validating it is cheap.

### Consequences

- Good, because the traversal is refused before a single byte reaches the
  network — proven by tests that assert the mock transport receives zero
  calls for a traversal input, not just that some error was eventually
  raised.
- Good, because the error names the actual problem ("must be a bare
  Segment identifier") instead of a confusing 404 from a path nobody
  intended to request.
- Good, because it composes with existing `except SegmentAPIError`
  degrade-gracefully handling in `routing.py`/`governance.py`: a bad ID
  drawn from an upstream response becomes a reported `Gap`, not a crashed
  tool call, while an explicit tool-argument ID still raises immediately
  and pre-network for the direct LLM-input case.
- Bad, because every new tool that interpolates a resource ID into a path
  must remember to route it through `validate_resource_id()` — this isn't
  enforced structurally (e.g. by a typed wrapper the type checker would
  reject a bare `str` against). See Known limitation.

### Confirmation

`tests/test_resource_id_validation.py`: parametrized unit tests over the
audited traversal/malformed inputs and known-good Segment ID shapes, plus
one end-to-end test per affected tool (`audit_event_routing`,
`trace_event`, `find_ungoverned_sources`, `check_delivery_health`) that
asserts a traversal `source_id`/`destination_id` raises
`InvalidResourceIdError` against a transport that fails the test if it's
ever called — the only way to actually prove the refusal happened before
the network, not just that a raised error's mock transport was never
awaited.

## Assumption this relies on

That every legitimate Segment resource ID matches
`[A-Za-z0-9_-]{1,64}` — verified against this project's own recorded
fixtures (`tests/fixtures/*/tools/*.json`). If Segment ever mints IDs
containing another character (a `.`, say), this pattern would need
updating; it would need to still exclude anything that could form a
dot-segment, slash, `?`, or `#` when doing so.

## Known limitation

This is a validation gate applied at each call site, not a type-level
guarantee. A future tool author who interpolates a new ID-shaped
parameter into a path without routing it through `validate_resource_id()`
reintroduces the same class of bug, and nothing but code review catches
that. The mitigating factor is AGENTS.md's "hand-pick, don't
auto-generate" rule, which keeps every such call site small in number and
visible in a diff.

## Pros and Cons of the Options

### Percent-encode before interpolating

- Good, because it technically prevents the traversal
- Bad, because the resulting request (`GET /sources/..%2Fregulations`)
  still doesn't do what any caller intended, and now fails with a
  confusing 404 instead of a clear refusal
- Bad, because it invites the same reasoning "the value is safe now,
  it's encoded" that under-specified encoding schemes have historically
  gotten wrong elsewhere in the industry

### Reject anything that isn't a bare identifier

- Good, because there is no legitimate ID this excludes
- Good, because it fails fast, before the network, with a message that
  names the actual problem
- Neutral, because it requires every call site to opt in explicitly (see
  Known limitation)

### Rely on the API's own 404 for malformed paths

- Good, because it requires no new code
- Bad, because the second confirmed case
  (`../../v1beta/users?x=1`) doesn't 404 — it's a valid request to a
  *different* endpoint with an injected query string, so "let it 404"
  doesn't even hold for every case this closes
- Bad, because relying on the request having already been sent means the
  traversal already happened by the time anything notices

## More Information

See the audit that found this (external review against HEAD, 30 Aug
2026) and `docs/decisions/0004-tier1-guard-matches-resolved-url.md`,
which closes a related bypass in the independent Tier 1 guard using the
same "match on what actually gets sent, not the string that was passed
in" principle.
