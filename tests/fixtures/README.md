# Fixtures

Recorded HTTP responses from the Segment Public API, one directory per
region (`us/`, `eu/`). `tests/fixtures/http.py` loads these and builds an
`httpx.MockTransport` from them — **no test in this repo calls a live
Segment API.** See AGENTS.md.

## Provenance — read before trusting these as ground truth

BUILD-PLAN.md §11.4 lists "get a Team or Business tier workspace for
testing" as an open question not yet resolved (the Public API is
unavailable on Free/Add-on tier, which is what's available right now).
These fixtures were therefore **hand-authored from Segment's documented
Public API response shapes, not captured from a live workspace.**

Treat every fixture body as best-effort, not verified byte-for-byte
against a real response. In particular:

- The exact 403 body shape used to distinguish "Free tier" from
  "insufficient permissions" in `client/public_api.py`
  (`_classify_permission_error`) is a documented-language guess, not a
  confirmed error code. If a real Free-tier 403 body looks different once
  a real workspace is available, `free_tier_403.json` and the classifier
  it exercises both need updating together.
- Object field names for sources/tracking-plans/destinations follow the
  Public API docs' described shapes as closely as they're documented, but
  haven't been diffed against a real recorded response.

**When a Team/Business workspace becomes available**, recapture each
fixture by hand against the live API once, redact anything sensitive
(workspace/source/destination IDs, slugs, names — replace with clearly
fake placeholders), and replace the file in place. Do not wire live calls
into the test suite itself — see AGENTS.md's "no live API calls in tests"
rule.

## Format

Each fixture is a JSON file:

```json
{
  "status_code": 200,
  "headers": { "Content-Type": "application/json" },
  "body": { "...": "..." }
}
```

A deliberately-invalid body (for testing malformed-response handling) uses
`raw_body` (a literal string) instead of `body`:

```json
{
  "status_code": 200,
  "headers": { "Content-Type": "application/json" },
  "raw_body": "{not valid json"
}
```
