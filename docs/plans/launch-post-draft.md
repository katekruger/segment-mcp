# Launch post — DRAFT, not published

Targets: Segment's own community, r/RevOps, the MCP community (e.g. r/mcp,
MCP Discord). Adjust tone per venue; this draft is written for a
technical-but-not-Segment-insider audience (roughly r/RevOps or a
general MCP community post).

---

## What a Segment MCP server should refuse to do

I built [segment-mcp](https://github.com/katekruger/segment-mcp), an MCP
server for Twilio Segment — and the thing I want to talk about isn't a
feature. It's a refusal.

**The best question in a Segment workspace nobody can currently
answer** is "which destinations get which events?" You can find out, but
it takes forty clicks through Sources → connected destinations →
settings → subscriptions, one at a time, per source. `audit_event_routing`
answers it in one call — joins those four endpoints into a single routing
report, because the value of an MCP server here isn't exposing Segment's
API to an LLM, it's composing the join a human would otherwise do by
hand.

That's the pitch. Here's the part I actually want to talk about.

**Segment's Public API has an endpoint that will workspace-wide,
irreversibly delete or suppress user data — `POST /regulations` — and it
takes an array, so one malformed or hallucinated call can delete
thousands of profiles in a single request. There's no undo.**

Every MCP server for Segment I could find takes a different stance on
this than I did. At least three exist as of this writing:

- One ships as a pure write path — five tools, all Tracking API writes
  (`track`, `identify`, `group`, `page`, `batch`). Its own author
  describes it as "a write-only rail." No reads at all.
- One (the npm `segment-mcp-server` package) exposes 18 tools including
  `delete_source` and full Tracking API writes, with no visible
  confirmation gating in its docs and no region handling — meaning an EU
  workspace pointed at it may silently drop every event with no error.
- One is more comprehensive still — ~40 tools spanning functions,
  audiences, transformations — and it does gate its mutations behind a
  `confirm: bool` preview step. That's a real safety mechanism. But it
  still includes deletion/suppression regulations as a gated mutation
  tool. It's a gate, not a line.

segment-mcp draws the line instead of gating it. `POST /regulations` and
its per-source sibling are unreachable — not "requires admin mode,"
not "requires a typed confirmation," **unreachable, full stop, checked
three independent ways**: the authorization layer refuses it before
even checking what mode you're in, the API client refuses to send the
request before it touches the network, and no tool definition in the
server references it in any form. There's a test that asserts all
three, on purpose, redundantly, because this is the one failure mode
with no undo and I wanted it to be genuinely hard to accidentally
relax.

I want to be fair here, not dismissive: these are one-person prototypes
built by people scratching a real itch, not funded products with a
security team. The point isn't "they got it wrong" — it's that
**"expose the whole API surface and gate the scary parts" is a
structurally different design decision than "some parts don't get
exposed, period,"** and I think the second one is right for anything
that can permanently destroy someone else's customer data. That's the
actual design principle underneath this project, and it's the reason
`segment-mcp` ships read-only in v0.1 by default — zero write tools,
not because writes weren't built yet, but because the five things worth
answering right now are all reads, and I'd rather ship a smaller
surface I trust completely than a bigger one I have to keep gating.

**The five tools, if you're curious what "composed, not raw" looks
like in practice:**

- `audit_event_routing` — which destinations get which events (the one
  above)
- `trace_event` — given an event name, is it governed by any tracking
  plan, and if not, the answer is literally "governed by nothing," not
  an empty result
- `find_stale_sources` — which sources have gone dead vs. which are
  just new (the API doesn't expose a creation date, so "we can't tell"
  is its own answer, not a guess)
- `check_delivery_health` — which destinations are silently failing,
  the thing that quietly breaks attribution for a quarter before anyone
  notices
- `find_ungoverned_sources` — governed by nothing, vs. governed but
  still letting unplanned events through

```
pip install segment-mcp
```

MIT licensed. [github.com/katekruger/segment-mcp](https://github.com/katekruger/segment-mcp).
Full writeup of what it refuses and why: [docs/what-this-refuses-to-do.md](https://github.com/katekruger/segment-mcp/blob/main/docs/what-this-refuses-to-do.md).

---

## Shorter version, for a tighter community (e.g. r/mcp)

**segment-mcp** — a read-first MCP server for Twilio Segment.
`audit_event_routing` answers "which destinations get which events?" in
one call instead of forty UI clicks. Ships with zero write tools by
design; the one endpoint that can permanently, irreversibly delete
workspace data is unreachable in every mode, checked three independent
ways, not just gated behind a confirmation. `pip install segment-mcp`.
MIT. [github.com/katekruger/segment-mcp](https://github.com/katekruger/segment-mcp)

---

## Notes for whoever posts this

- The "at least three" framing replaces an earlier "first"/"only two
  other servers" draft — a GitHub search during the Prompt 5 audit
  turned up `tduong-sys/segment-mcp-server`, a real ~40-tool server
  BUILD-PLAN's original research missed (mcp.so was robots-blocked both
  times; only smithery.ai could be checked directly). Don't revert to a
  "first" claim without re-verifying.
- Adjust the "at least three" bullet list's tone per venue — Segment's
  own community forum probably wants less "here's what's wrong with
  the others" and more "here's the pattern," r/RevOps likely wants the
  audit_event_routing pitch foregrounded even more.
- Link targets to fill in once live: MCP Registry entry, Smithery (if
  ever built), any registry badge worth screenshotting.
