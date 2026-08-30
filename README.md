# segment-mcp

[![CI](https://github.com/katekruger/segment-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/katekruger/segment-mcp/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/segment-mcp.svg)](https://pypi.org/project/segment-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**A read-first MCP server for Twilio Segment.** Answers which destinations
get which events, which sources are dead, and which are governed by
nothing — the questions nobody can answer without clicking through forty
screens.

## Read-only by default

`SEGMENT_MCP_MODE` defaults to `read`, and every tool this server ships
today is a read. Shipping with zero write tools is a feature, not a
limitation — see `BUILD-PLAN.md` §2. `write` and `admin` modes exist in
the tier model (`src/segment_mcp/modes.py`) for when gated writes land;
right now there is nothing for them to unlock.

## What this refuses to do — permanently, not "for now"

`POST /regulations` and `POST /regulations/sources/{id}` — workspace-scoped,
irreversible deletion or suppression of user data across every source —
are **unreachable in every mode, with no configuration path to enable
them.** Three independent things enforce this: the mode-authorization
layer refuses it before even checking the current mode, the API client
refuses to send the request before it reaches the network, and no tool
this server registers references it in any form.

This isn't a gate waiting for the right permission level. It's a line,
because these endpoints accept an array of subjects and one malformed or
hallucinated call can permanently delete thousands of profiles with no
undo. Full reasoning: **[docs/what-this-refuses-to-do.md](docs/what-this-refuses-to-do.md)**.

## Quick start

Requires a Segment workspace on Team or Business tier and a Public API
token (see [Prerequisites](#prerequisites) below).

```bash
gh repo clone katekruger/segment-mcp
cd segment-mcp
uv sync
cp .env.example .env      # fill in SEGMENT_API_TOKEN and SEGMENT_REGION
uv run segment-mcp
```

Point an MCP client (Claude Desktop, Claude Code, etc.) at it over stdio.
The server refuses to start — loudly, with a clear message — if the
token, region, or workspace tier isn't right; see
[Startup checks](#startup-checks).

## The five tools

Each composes several Public API calls into one structured answer, not a
raw endpoint dump:

| Tool | Question it answers |
|---|---|
| `audit_event_routing` | Which destinations get which events? |
| `trace_event` | Given an event name: where does it go, and is it governed by anything? |
| `find_stale_sources` | Which sources have no recent data — dead instrumentation vs. simply new? |
| `check_delivery_health` | Is this destination silently failing? |
| `find_ungoverned_sources` | Which sources are governed by nothing, or allowing unplanned events through? |

## Prerequisites

- **Team or Business tier.** The Public API is not available on Free or
  Add-on plans. There is no workaround, and the server's startup checks
  fail with a clear message rather than a raw 403 if your workspace
  doesn't qualify.
- **A Public API token.** Only a Workspace Owner can mint one: Segment App
  → Workspace Settings → Access Management → Tokens → Create Token →
  Public API (not Config API).

## Region configuration

```
SEGMENT_REGION=us   # or eu
```

There is **no default** — you must set this explicitly. An EU workspace
whose API calls are pointed at the US endpoint doesn't error; it just
silently returns nothing, which is a far worse failure mode than a crash.
This server's startup checks call the API once with your configured
region and fail loudly if the token doesn't actually belong to it,
naming the region that does.

## Startup checks

All fatal — the server refuses to start rather than fail confusingly on
the first tool call:

1. `SEGMENT_REGION` is set and one of `us`/`eu`.
2. `SEGMENT_API_TOKEN` is present and actually authenticates against that
   region.
3. The workspace's tier supports the Public API — a Free-tier workspace
   gets a clear "requires Team or Business tier" message, not a raw 403.

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

See `src/segment_mcp/modes.py` for the full tier model and
[docs/what-this-refuses-to-do.md](docs/what-this-refuses-to-do.md) for
what stays out of scope regardless of mode.

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
  instance.
- **Every lookup is logged** — collection, the normalized lookup key, and
  the caller — before the request is even sent, via
  `client/profile_api.py`'s `segment_mcp.profile_api` logger.
- **Lookups are case-sensitive.** The wrong case returns an empty result,
  not an error — this client lowercases every lookup value at its
  boundary and logs a warning when it had to.

No profile-lookup MCP tool is wired into `server.py` yet — this is the
client and trust-boundary machinery a future tool will be built on, per
`BUILD-PLAN.md`'s v0.2 scope.

## Development

```bash
uv sync
uv run pre-commit install
uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest
```

See `CONTRIBUTING.md` and `AGENTS.md`.

## License

MIT — see `LICENSE`.
