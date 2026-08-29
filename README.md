# segment-mcp

**A read-first MCP server for Twilio Segment.** Answers which destinations
get which events, which sources are dead, and which are governed by
nothing — the questions nobody can answer without clicking through forty
screens.

**Read-only by default.** `SEGMENT_MCP_MODE` defaults to `read` and v0.1
ships with zero write tools. This is a feature, not a limitation — see
`BUILD-PLAN.md` §2.

**Data deletion is not exposed. At all.** `POST /regulations` and
`POST /regulations/sources/{id}` are permanently unreachable in every mode.
See `docs/decisions/0002-tier-1-permanently-unreachable.md`.

Status: not started. See `BUILD-PLAN.md` for the full design and scope.

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
