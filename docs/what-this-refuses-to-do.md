# What this refuses to do

Most of what makes a Segment MCP server safe is what it declines to
expose. This document says so plainly, in one place, rather than leaving
it scattered across ADRs and code comments — read it before proposing a
write tool.

## Tier 1: `POST /regulations` and its per-source sibling — never, in any mode

`POST /regulations` is workspace-scoped deletion and suppression across
**every source** in the workspace. `POST /regulations/sources/{id}` and
`POST /regulations/cloudsources/{id}` are its per-source equivalents.
Types `DELETE_ONLY`, `SUPPRESS_WITH_DELETE`, and `DELETE_INTERNAL`
**permanently destroy user data — there is no undo.** `UNSUPPRESS`
reverses suppression but **cannot restore data already deleted**. All of
them accept an **array of subjects**, so one malformed or hallucinated
tool call can delete thousands of profiles in a single request.

This is not gated behind `admin` mode, a typed confirmation, or any
future flag. It is unreachable, full stop:

- `modes.py`'s `authorize()` raises `Tier1UnreachableError` for it
  unconditionally, before even checking what mode is active.
- `client/public_api.py` refuses to send a mutating (non-`GET`) request
  to any `/regulations*` path, independently of `modes.py`, before the
  request ever reaches the network.
- No tool definition registered by this server references `/regulations`
  in any form.

`tests/test_tier1_unreachable.py` asserts all three, deliberately
redundantly. See `docs/decisions/0002-tier-1-permanently-unreachable.md`
for the full reasoning and the assumption it rests on.

**The reads stay in scope.** `GET /regulations`, `GET /regulations/{id}`,
and `GET /suppressions` are v0.2 material — auditing what's already been
deleted or suppressed is a legitimate, safe question. Creating a new
deletion is the line, not the topic.

## The `PUT`-replaces-all trap

Two endpoints use **replace semantics under a verb that reads as "update"
to a model**:

- **`PUT /tracking-plans/{id}/rules` replaces every rule in the plan.**
  An agent that sends a partial rule set — because it was only asked to
  add or change one event — silently wipes the governance contract for
  every other event in that tracking plan. This is judged the single most
  likely way an LLM causes real damage through this server, precisely
  because nothing about the verb signals "destructive."
- **`PUT /sources/{id}/labels` replaces, not appends**, for the same
  reason.

Neither is exposed in v0.1 or v0.2. **If either is ever exposed (earliest
v0.3), the tool MUST:**

1. Require the full current rule set (or label set) as an explicit input
   parameter — not fetch it internally and merge, which would let a stale
   or partial caller-supplied view silently clobber concurrent changes.
2. Diff the supplied full set against what `GET` returns for that
   resource *at call time*, and refuse to proceed (or require a second,
   explicit confirmation naming the diff) if they don't match — so a
   caller who fetched the rules five minutes ago can't unknowingly
   overwrite something changed since.
3. Only then issue the `PUT`, and only under `write` mode with the
   Tier 3 echo-confirmation gate `modes.py` already provides.

This requirement is written down now, in this file, specifically so that
a future session — human or agent — implementing that tool doesn't treat
the diff-before-write step as optional scope creep and skip it. It is the
entire reason `PUT`-replace endpoints are survivable to expose at all.

## Why Tracking API writes are not exposed — ever, not just "not yet"

`track`/`identify`/`page`/`group`/`batch` against the Tracking API inject
synthetic events into a production CDP. This is not a reversible
operation in any meaningful sense:

- It **pollutes analytics** — dashboards, funnels, and reports built on
  that data now include agent-generated noise indistinguishable from real
  user behavior.
- It **can trigger downstream automations**: a `track` call can fire a
  destination subscription that starts an email sequence, updates a CRM
  record, or calls a webhook into another system entirely.
- Those downstream effects **can reach real customers** — a triggered
  email, an SMS, a support ticket. That message cannot be un-sent once
  it's left Segment's pipeline.
- Unlike a config change, there is no `GET` that reveals "this event was
  synthetic" after the fact — it's indistinguishable from real telemetry
  once ingested.

**Both existing community Segment MCP servers lead with exactly this write
path.** `NoBanks/segment-mcp` is Tracking-API-only — its own author
describes it as "a write-only rail." The npm `segment-mcp-server` package
includes it among 18 tools with no region handling and no confirmation
gating on destructive operations. Being the project that explains why
that's backwards, rather than the third project to ship it, is this
server's actual positioning — not a marketing angle bolted on afterward,
but the direct consequence of taking "read-only by default" seriously
enough to also mean "and we thought about why the obvious write path is a
bad idea," instead of stopping at "read-only for now."

## Summary table

| What | Exposed? | Why |
|---|---|---|
| `GET /regulations`, `/regulations/{id}`, `/suppressions` | v0.2 | Reading what's already deleted/suppressed is safe |
| `POST /regulations`, `/regulations/sources/{id}` | **Never** | Irreversible, array-valued, workspace-wide deletion |
| `PUT /tracking-plans/{id}/rules` | Not before v0.3, and only with a required full-set diff | Replaces all rules under an "update"-sounding verb |
| `PUT /sources/{id}/labels` | Not before v0.3, and only with a required full-set diff | Same replace-semantics trap |
| `track`/`identify`/`page`/`group`/`batch` (Tracking API) | **Never** | Un-un-sendable synthetic events with real downstream effects |
| `DELETE /sources/{id}`, `/destinations/{id}`, etc. (Tier 2) | `admin` mode + typed confirmation | Reversible in principle (recreate config) but breaks live pipelines instantly |
