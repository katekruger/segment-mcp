# segment-mcp

**A read-first MCP server for Twilio Segment.** Answers which destinations
get which events, which sources are dead, and which are governed by
nothing — the questions nobody can answer without clicking through forty
screens.

**Read-only by default.** `SEGMENT_MCP_MODE` defaults to `read`, and every
tool shipped so far is a read. This is a feature, not a limitation — see
`BUILD-PLAN.md` §2.

**Data deletion is not exposed. At all.** `POST /regulations` and
`POST /regulations/sources/{id}` are permanently unreachable in every
mode, checked three independent ways. See
`docs/what-this-refuses-to-do.md` and
`docs/decisions/0002-tier-1-permanently-unreachable.md`.

Status: v0.1 in progress. See `BUILD-PLAN.md` for the full design and
scope.

## Tools (v0.1)

Five composed questions, not raw endpoint wrappers — each joins several
Public API calls into one structured answer:

| Tool | Answers |
|---|---|
| `audit_event_routing` | Which destinations get which events? |
| `trace_event` | Given an event name: where does it go, and is it governed by anything? |
| `find_stale_sources` | Which sources have no recent data? |
| `check_delivery_health` | Is this destination silently failing? |
| `find_ungoverned_sources` | Which sources are governed by nothing, or allowing unplanned events through? |

## Modes

```
SEGMENT_MCP_MODE = read (default) | write | admin
```

- **`read`** — every tool above. No mutation reachable, at any mode.
- **`write`** — would add Tier 3 replace-semantics changes (none shipped
  yet), each echoed back for confirmation before executing.
- **`admin`** — would add Tier 2 deletes (none shipped yet), gated behind
  a *typed* confirmation naming the exact resource — not just
  `confirm=true`.
- **Tier 1 is unreachable in every mode**, unconditionally. See
  `docs/what-this-refuses-to-do.md`.

See `src/segment_mcp/modes.py` for the full tier model.

## Profile API — a separate, higher trust tier

The Profile API returns PII on named individuals — traits, external IDs,
event history, and identity links for a specific person. This is the most
privacy-sensitive read anywhere in this server's surface, so it is walled
off from everything else:

- **A separate credential**, `SEGMENT_PROFILE_TOKEN` — never the main
  `SEGMENT_API_TOKEN`. Also requires `SEGMENT_PROFILE_SPACE_ID` (your
  Unify Space ID, not your workspace ID).
- **Explicit opt-in.** If `SEGMENT_PROFILE_TOKEN` is unset, no profile
  tool is registered — the capability doesn't exist for that server
  instance, not just "requires a flag to use."
- **Every lookup is logged** — collection, the normalized lookup key, and
  the caller — before the request is even sent, via
  `client/profile_api.py`'s `segment_mcp.profile_api` logger.
- **Lookups are case-sensitive.** The wrong case returns an empty result,
  not an error — this client lowercases every lookup value at its
  boundary and logs a warning when it had to.

No profile-lookup MCP tool is wired into `server.py` yet — this is the
client and trust-boundary machinery a future tool will be built on, per
BUILD-PLAN.md's v0.2 scope.

## Setup

Requires a Segment workspace on **Team or Business tier** — the Public API
is not available on Free or Add-on plans — and a Public API token, which
only a Workspace Owner can mint. See `.env.example`.

```bash
gh repo clone katekruger/segment-mcp
cd segment-mcp
uv sync
uv run pre-commit install
cp .env.example .env      # fill in your own values; never commit this file
```

## License

MIT — see `LICENSE`.
