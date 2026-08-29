## What and why

<!-- What does this change, and what problem does it solve? -->

## AI disclosure

<!-- Required. See CONTRIBUTING.md § Use of AI. -->

- AI-assisted: <!-- yes/no -->
- Model / harness: <!-- e.g. Claude Sonnet 5 via Claude Code -->
- What was and wasn't AI-generated:

## New dependencies

<!-- One line of justification per new dependency, or "None". -->

## Checklist

- [ ] `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest` passes locally
- [ ] `CHANGELOG.md` has an entry under `## Unreleased`
- [ ] New behavior has a test, or this PR explains why not
- [ ] An ADR is included if this decision is expensive to reverse
- [ ] This PR does not add any path — in any mode — that reaches Tier 1 (regulation/deletion creation)
- [ ] Any new write tool is gated by `SEGMENT_MCP_MODE` and echoes the change back for confirmation before executing
- [ ] Region is resolved explicitly wherever this change makes an API call
- [ ] I reviewed the full diff myself before opening this PR
