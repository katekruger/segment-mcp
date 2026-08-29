# Contributing

This is currently a solo project in active early development. Issues are
welcome; unsolicited large PRs are likely to be rejected on scope grounds
even if the code is correct — open an issue first for anything beyond a small
fix.

## Setup

```bash
gh repo clone katekruger/segment-mcp
cd segment-mcp
uv sync
uv run pre-commit install
cp .env.example .env      # fill in your own values; never commit this file
```

Run the full check before opening a PR:

```bash
uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest
```

## Use of AI

AI-assisted contributions are welcome. Disclose it in the PR body: which
model, which harness, and what was and wasn't AI-generated. Review the diff
yourself before opening the PR — "the agent wrote it" is not a defense for
code you didn't understand well enough to explain. See `AGENTS.md` for the
house rules an agent working in this repo is expected to follow.

## What Gets Rejected

- Direct commits to `main`.
- PRs that reformat or refactor code unrelated to the change.
- New dependencies without a one-line justification in the PR body.
- Any tool, mode, or code path that can reach Tier 1 (regulation/deletion
  creation) — see `AGENTS.md` and
  `docs/decisions/0002-tier-1-permanently-unreachable.md`. This is not a
  gate to design around; it does not get relaxed by any PR.
- An auto-generated tool surface from the OpenAPI spec.
- A write tool shipped without `SEGMENT_MCP_MODE` gating and a confirmation
  echo of the change before it executes.
- Live API calls in tests. Fixtures only, under `tests/fixtures/{us,eu}/`.
- PRs that remove or weaken a test to make CI pass.

### Why Issues Rather Than Pull Requests

For anything larger than a small, obviously-correct fix, open an issue
describing the problem or proposal first. This project's design has a lot of
non-obvious constraints (see `BUILD-PLAN.md` and `AGENTS.md`) and a PR built
on a wrong assumption is more expensive to review than a short conversation
up front.
