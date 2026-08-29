# AGENTS.md

## Stop and read this before you write code

This repo has conventions. Violating them wastes a review cycle.

## Commands

- Install: `uv sync`
- Test: `uv run pytest`
- Lint: `uv run ruff check . && uv run ruff format --check .`
- Types: `uv run pyright`
- All of it: `uv run ruff check . && uv run ruff format --check . && uv run pyright && uv run pytest`

## Layout

- `src/segment_mcp/` — the package. `tests/` mirrors it. Never put tests inside the package.
- `docs/decisions/` — ADRs (MADR 4). Permanent, numbered, never renumbered.
- `docs/plans/` — dated design plans. Disposable once executed.
- `.env.example` — every var the server reads, no values. Copy to `.env` to use.

## Non-negotiable

1. **Never commit directly to `main`.** Branch, commit, open a PR. Even for a typo.
2. **Tests before implementation.** If you are adding behavior, the failing test comes first.
3. **No secrets in the repo, ever** — not in tests, not in fixtures, not in examples. Use env vars and `.env.example`.
4. **Never re-type a file's contents from tool output.** Output can be truncated. Edit in place.
5. **Every dependency added needs a one-line justification in the PR body.**
6. **If a decision is expensive to reverse, write an ADR in the same PR.**
7. **Linter versions are pinned exactly.** Do not float them to fix a failure — fix the code, or bump deliberately in its own PR.

## Project-specific non-negotiables

1. **READ-ONLY IS THE DEFAULT.** `SEGMENT_MCP_MODE` defaults to `read`.
   Shipping v1 with zero write tools is a FEATURE, not a limitation — it
   beats everything currently on the market. See BUILD-PLAN.md §2.
2. **TIER 1 IS PERMANENTLY UNREACHABLE.** `POST /regulations` and
   `POST /regulations/sources/{id}` permanently destroy user data with no
   undo. They must not be callable in ANY mode. This is a line, not a v3
   feature waiting for the right gate. See
   `docs/decisions/0002-tier-1-permanently-unreachable.md` and
   `docs/what-this-refuses-to-do.md`. `tests/test_tier1_unreachable.py`
   must keep passing, deliberately redundantly (modes.py, the client, and
   tool introspection each refuse it independently) — never simplify that
   test down to one check.
3. **`PUT`-replace endpoints (`tracking-plans/{id}/rules`,
   `sources/{id}/labels`) are not exposed before v0.3, and only then with
   the full-set-diff requirement in `docs/what-this-refuses-to-do.md`
   implemented, not skipped as scope creep.**
4. **NEVER auto-generate tools from the OpenAPI spec.** It would produce
   ~200 flat tools including every `DELETE`, with no notion of blast
   radius. Hand-pick and COMPOSE.
5. **Compose reads into QUESTIONS, not endpoints.** The value is the join.
   An LLM chaining four endpoint calls per source hits rate limits and
   loses the thread. See BUILD-PLAN.md §5.
6. **Resolve region EXPLICITLY on every call. Never default.** An EU
   workspace hitting the wrong base URL fails silently — no error, data
   simply never appears or never arrives. See BUILD-PLAN.md §0.6.
7. **No live API calls in tests.** Recorded fixtures only, under
   `tests/fixtures/{us,eu}/`.
8. **The Profile API is a separate trust tier.** Never construct
   `ProfileAPIClient` implicitly or fall back to `SEGMENT_API_TOKEN` for
   it. A profile-lookup tool must not be registered when
   `SEGMENT_PROFILE_TOKEN` is unset, and every lookup must be logged. See
   `client/profile_api.py` and README.md's Profile API section.

## Before opening a PR

- [ ] The full check command above passes locally
- [ ] `CHANGELOG.md` has an entry under `## Unreleased`
- [ ] No new file lacks a test, or the PR says why
- [ ] The PR body discloses: model, harness, and that it was AI-assisted
- [ ] You showed the human the full diff and got approval

## What gets rejected

- Direct commits to `main`
- Reformatting unrelated code
- New dependencies without justification
- "Improvements" nobody asked for, bundled into an unrelated PR
- Removing a test to make CI pass
- Anything that makes an approval gate optional
- Any tool call, mode, or code path that can reach Tier 1 (regulation/deletion creation)
- Live API calls in tests
- Auto-generated tool surfaces from the OpenAPI spec
