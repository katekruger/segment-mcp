---
status: "accepted"
date: "2026-08-30"
deciders: "Kate Kruger"
---

# The Tier 1 client guard matches on the resolved URL, not the raw path string

## Context and Problem Statement

`_refuse_if_tier1_mutation()` is one of the three independent checks
(`docs/decisions/0002-tier-1-permanently-unreachable.md`) that keep
`POST /regulations` unreachable in every mode. Before this decision, it
worked by string inspection: split off a `?query`, then compare the
remainder to `/regulations` by prefix. An external audit (30 Aug 2026)
found seven ways to construct a mutating request the guard would let
through while `httpx` still resolves it to `/regulations` once the
request is actually sent:

```
/regulations#frag
/./regulations
/foo/../regulations
regulations                                   (no leading slash)
https://api.segmentapis.com/regulations       (absolute URL)
/REGULATIONS
/Regulations
```

None of these are exploitable *today* — every other tool in this server
only issues `GET`s, and nothing currently constructs a path from
attacker-controlled input the way ENG-1's `source_id`/`destination_id`
did. But the guard exists specifically so that the *next* non-GET tool
this project ships (v0.2/v0.3, per BUILD-PLAN.md §6) inherits a safe
default rather than a guard that only covers the three forms someone
happened to write a test for. Should the guard keep doing string
comparison against a wider set of variants, or match against the URL as
it will actually be sent?

## Decision Drivers

- The guard's entire purpose is to be the belt (or brace) that holds even
  when a caller's input isn't the exact string the guard's author
  imagined. A string-comparison approach that has to enumerate every
  bypass form by hand is structurally the same mistake as ENG-1's
  path-traversal bug: reasoning about strings instead of about what the
  HTTP client will actually do with them.
- `httpx.URL.join()` already implements RFC 3986 URL resolution — dot-
  segment normalization, fragment stripping, absolute-URL override, and
  relative-reference handling — correctly and is exactly what
  `self._client.request(method, path, ...)` uses internally to resolve
  `path` against the client's base URL before sending. Reusing it means
  the guard checks the same resolution the request itself performs,
  instead of a parallel, potentially-diverging reimplementation.
- This is the second location in this codebase (after ENG-1) where "match
  on the string that was passed in" turned out to be the vulnerability
  and "match on what actually gets sent" is the fix — worth naming as its
  own principle rather than a one-off patch.

## Considered Options

- Extend the string-comparison guard to also strip fragments, normalize
  dot-segments, add a leading slash if missing, detect absolute URLs, and
  lowercase before comparing
- Resolve the path against the client's base URL with `httpx.URL.join()`
  first, then compare the resolved path
- Leave the guard as-is and rely on code review to keep any future
  non-GET tool from constructing a path in one of these seven ways

## Decision Outcome

Chosen option: "Resolve the path against the client's base URL with
`httpx.URL.join()` first, then compare the resolved path."
`_refuse_if_tier1_mutation()` now takes the client's `base_url` and
computes `base_url.join(path).path.rstrip("/").casefold()`, comparing
that against `_TIER1_BLOCKED_PATH_PREFIX.casefold()` with the same
prefix-plus-`/`-boundary check as before (so `/regulations-adjacent`
still isn't caught). Because this resolves the URL exactly as httpx
itself will before sending, any future bypass form has to be a bypass of
httpx's own URL resolution, not of this guard's approximation of it.

### Consequences

- Good, because the fix closes all seven confirmed bypasses with one
  change, by construction, rather than seven individually-reasoned
  special cases.
- Good, because it can't silently drift from what the request layer
  actually does — there's only one URL-resolution implementation in the
  path now, not two that have to be kept in sync by hand.
- Good, because `tests/test_tier1_unreachable.py` is now parametrized
  over ten bypass strings × four non-GET methods (40 cases) plus explicit
  negative cases (`/sources`, `/regulations-adjacent`), so this can never
  quietly regress back to "the guard passes its own three hand-picked
  test strings" without the test suite catching it.
- Neutral, because this is still, ultimately, one function that a future
  refactor could reintroduce a string-comparison shortcut into — the fix
  is in the implementation, not in a structural guarantee the type
  checker enforces.

### Confirmation

`tests/test_tier1_unreachable.py`'s `test_client_refuses_every_mutation_bypass_form`
parametrizes over the ten known bypass strings (three original,
seven from the audit) across `POST`/`PUT`/`PATCH`/`DELETE`, asserting
`Tier1BlockedError` in every one of the forty cases, with no live
transport configured — if any case reached the network layer, it would
attempt a real connection instead of raising immediately. Negative-case
tests confirm `POST /sources` and `POST /regulations-adjacent` are not
caught.

## Assumption this relies on

That `httpx.URL.join()`'s resolution semantics match what
`httpx.AsyncClient.request()` does internally when combining `base_url`
and a request path — both use `httpx.URL`'s own join logic, so this holds
as long as this project doesn't switch away from httpx as the underlying
transport library.

## Known limitation

This guard still only recognizes `/regulations` and its two documented
per-source siblings as Tier 1. If Segment ever adds another
irreversible-deletion endpoint under a different path, it does not
automatically become Tier 1 here — that's still a manual addition to
`_TIER1_BLOCKED_PATH_PREFIX`'s reasoning (currently a single prefix, which
would need to become a set if a second prefix were ever needed) and
`docs/what-this-refuses-to-do.md`, not something this fix makes
self-updating.

## Pros and Cons of the Options

### Extend the string-comparison guard case-by-case

- Good, because it's a smaller diff
- Bad, because it treats the symptom (seven specific strings) rather than
  the cause (comparing against the wrong thing), and offers no assurance
  an eighth bypass form doesn't exist
- Bad, because URL resolution has enough edge cases (relative references,
  `..` segments that walk past the root, IPv6 host literals, userinfo)
  that a hand-rolled reimplementation is a second place those edge cases
  can be gotten wrong

### Resolve against base_url first, then compare

- Good, because it reuses a correct, already-dependency implementation
  instead of adding a second one
- Good, because it checks the same thing the request itself will do
- Neutral, because it requires passing `base_url` into the guard function,
  a small signature change

### Rely on code review alone

- Bad, because this is exactly the belt-and-braces design this project
  otherwise rejects for Tier 1 — see ADR 0002's "no confirmation-echo
  pattern meaningfully bounds the damage" reasoning applied to process
  instead of to a confirmation gate: a human remembering to check for
  seven bypass forms on every future PR is not a control, it's a hope

## More Information

See `docs/decisions/0003-refuse-path-traversal-in-resource-ids.md`, which
closes a related bug (ENG-1) using the same principle: validate against
what the HTTP layer will actually do with a value, not against the string
form a caller happened to supply.

## Addendum (30 Aug 2026): `base_url.join()` itself was the wrong model

A second external audit against this fix found the fix's own premise was
half wrong: `httpx.AsyncClient.request()` does not resolve `path` with
`httpx.URL.join()` (RFC 3986 relative-reference resolution) — it resolves
with its own `_merge_url`, which for a relative path strips every leading
`/` off the given path and concatenates it onto the base URL's path,
ignoring any host the given string might otherwise parse as. The two
disagree specifically on a leading `//`: `base_url.join("//regulations")`
resolves to a *different host* (`https://regulations`, empty path) —
which doesn't match `_TIER1_BLOCKED_PATH_PREFIX` and so wasn't blocked —
while the real client strips the pseudo-host and sends to the base host's
**root** (`https://api.segmentapis.com/`). Not exploitable as reported
(the real request lands on a harmless root path, not `/regulations`), but
the guard's own model of where the request was going was simply wrong,
which is the exact defect this ADR exists to close.

Separately, neither `join()` nor `_merge_url` percent-decodes the path
before resolving: `/%2e%2e/regulations` resolves to the literal path
`/%2e%2e/regulations` at the httpx layer, not `/regulations` — httpx's
own dot-segment normalization only recognizes literal `.`/`..` segments,
not their percent-encoded form. Whether Segment's actual edge decodes-
then-normalizes before routing (common at CDN/gateway layers) isn't
something this client can verify from outside, so the guard now assumes
the pessimistic case.

### Second decision outcome

`_refuse_if_tier1_mutation` now:

1. Refuses outright, before resolving anything, if `path` starts with
   `//` — a protocol-relative reference this client's own tool code never
   legitimately constructs. This is deliberately more conservative than
   "resolve and check where it lands": today it lands somewhere harmless,
   but the guard should not depend on that httpx implementation detail
   for safety, and a `//`-prefixed path has no legitimate origin here to
   protect.
2. For everything else, resolves the path with
   `client.build_request(method, path).url` — the client's *own* request
   construction, not a parallel reimplementation — eliminating the
   `join()`-vs-`_merge_url` divergence by construction. Confirmed by a
   test that asserts this resolved URL equals the URL a real
   `MockTransport` actually receives, for every parametrized bypass path.
3. Percent-decodes the resolved path to a fixed point (so a double-
   encoded `%252e%252e` doesn't survive a single decode pass) and then
   re-normalizes `.`/`..` segments and collapses repeated `/` — both of
   which decoding can introduce and neither of which httpx's own
   resolution does for an already-encoded segment.

This does *not* percent-encode-then-compare, or otherwise try to "repair"
the path — same reasoning as ADR 0003: refuse, don't guess. A Segment
resource ID can never legitimately contain a `/`, so nothing this client
does legitimately depends on a repeated slash, a literal dot-segment, or
an encoded one surviving into the comparison.

### Confirmation (addendum)

`tests/test_tier1_unreachable.py` extends `TIER1_BYPASS_PATHS` with the
percent-encoded-dot-segment family (`/%2e%2e/regulations` and nine
variants, including one composed with an encoded slash and one with a
trailing slash), adds a dedicated `//regulations` test proving the
defensive early refusal (kept out of the main list, since it doesn't
actually resolve to `/regulations` — asserting `Tier1BlockedError` via
the prefix-comparison path would be asserting something false), adds
`test_guard_resolved_path_matches_the_transports_actual_path` (the
build-request-equals-transport property, over every bypass path), and
adds a property test that *generates* bypass compositions (prepend
`/./`, wrap in `/foo/../`, percent-encode a character, uppercase, drop
the leading slash, append a fragment, append a query — composed up to
two deep) rather than only listing them, asserting every generated
variant that an independent resolution check says lands on `/regulations`
is still refused. The three original negative cases
(`POST /sources`, `POST /regulations-adjacent`) still pass unchanged.
