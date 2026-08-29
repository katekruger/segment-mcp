---
status: "accepted"
date: "2026-08-29"
deciders: "Kate Kruger"
---

# Tier 1 (regulation/deletion creation) is permanently unreachable

## Context and Problem Statement

`POST /regulations` is workspace-scoped deletion and suppression across
**every source** in a Segment workspace. Its `type` field accepts
`DELETE_ONLY`, `SUPPRESS_WITH_DELETE`, and `DELETE_INTERNAL` — all three
**permanently destroy user data. There is no undo.** `UNSUPPRESS` reverses
suppression but **cannot restore data already deleted.** The endpoint (and
its per-source sibling, `POST /regulations/sources/{id}`) accepts an
**array of subjects**, so a single malformed tool call can delete thousands
of profiles in one request. Should this server ever expose these endpoints
as callable tools, in any mode, gated behind any confirmation flow?

## Decision Drivers

- The blast radius of a single mistaken call is workspace-wide,
  irreversible data loss — categorically different from every other
  destructive operation in the API surface (BUILD-PLAN.md §6, Tiers 2-4),
  all of which are at least survivable or reversible in the sense that
  configuration can be recreated.
- LLM agents are not reliably careful about array-valued, bulk-destructive
  inputs — that's exactly the failure mode a malformed or hallucinated
  subject list produces here.
- No confirmation-echo pattern (as used for Tier 2/3) meaningfully bounds
  the damage of an array-valued permanent-deletion call, because the
  confirmation would have to be re-verified per subject to be meaningful,
  and at that point the tool is not saving the user any real effort over
  the UI.

## Considered Options

- Expose `POST /regulations` behind `admin` mode with typed, per-call
  confirmation (the Tier 2/3 pattern)
- Expose only the reads (`GET /regulations`, `GET /regulations/{id}`,
  `GET /suppressions`); never expose creation, in any mode, ever
- Expose creation but hard-cap the subjects array to size 1

## Decision Outcome

Chosen option: "Expose only the reads; never expose creation, in any mode,
ever." This is not deferred to a future version behind a stronger gate — it
is a permanent line. Creation stays in the Segment UI.

### Consequences

- Good, because no agent using this server, in any mode, misconfiguration,
  or prompt-injection scenario, can trigger irreversible data loss through
  it.
- Good, because it is a clean, defensible, marketable claim: "this server
  cannot delete your users' data," full stop, no asterisk about mode
  settings.
- Bad, because a user who genuinely wants agent-initiated GDPR/CCPA
  deletion automation cannot have it through this server — see Known
  limitation below.

### Confirmation

`test_tier1_unreachable.py` (BUILD-PLAN.md §8, milestone M4) proves that no
tool, in no mode, can reach `POST /regulations` or
`POST /regulations/sources/{id}`. `AGENTS.md` lists this as a rejection
criterion for any PR.

## Assumption this relies on

That no legitimate agent workflow needs to initiate a GDPR/CCPA-style
deletion or suppression without a human in the Segment UI at the moment of
initiation. If a real, well-scoped use case for agent-initiated deletion
emerges (e.g. a narrowly-typed single-subject request with a human-reviewed
diff), it would need its own ADR superseding this one — it does not get
smuggled in as a mode upgrade.

## Known limitation

A user who wants agent-initiated deletion, even carefully gated, cannot
have it here. This server treats "an LLM can delete your users' data" as
unacceptable regardless of how the gate is designed, not as a problem to be
engineered around.

## Pros and Cons of the Options

### Gate behind `admin` mode with typed confirmation (Tier 2/3 pattern)

- Good, because it's consistent with how every other destructive operation
  in this server is handled
- Bad, because the array-valued, workspace-scoped blast radius makes a
  single confirmation prompt an inadequate safeguard — confirming "yes,
  delete these" for a list an agent generated is not the same guarantee as
  confirming a single named resource

### Expose only reads, never creation

- Good, because it removes the failure mode entirely rather than
  mitigating it
- Good, because it is simple to state, simple to test, and simple to
  market
- Bad, because it forecloses a real (if narrow) use case

### Hard-cap the subjects array to size 1

- Good, because it bounds the blast radius of any single call
- Bad, because `DELETE_ONLY`/`SUPPRESS_WITH_DELETE`/`DELETE_INTERNAL` are
  still individually irreversible — capping the array doesn't change that
  a single wrong subject is still permanent, unrecoverable data loss
- Bad, because it still requires this server to hold and exercise a
  code path capable of triggering permanent deletion, which is the thing
  being avoided

## More Information

See BUILD-PLAN.md §6 (Tier 1) and §3 ("Explicit non-goals, permanently").
