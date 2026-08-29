# segment-mcp — Build Plan

**A read-first MCP server for Twilio Segment that answers the questions nobody can answer without clicking through forty screens — which destinations get which events, which sources are dead, which are governed by nothing.**

Owner: Kate Kruger (`github.com/katekruger`)
Status: not started
Plan version: 1.0 — 28 Aug 2026
Research current as of: 28 Aug 2026

---

## 0. Handover context — read this first

1. **The niche is effectively unoccupied.** No official Segment MCP server exists. Two community attempts exist; both are abandoned and between them have **0 stars**. Neither covers audiences, regulations, delivery metrics, reverse ETL, warehouses, functions, or profiles.

2. **Both existing attempts got the read/write split backwards.** One is 100% write (injecting events into a production CDP from an LLM — arguably the most questionable operation in the whole surface). The other advertises "profile lookup" and ships no profiles tool at all. **The value here is reads.** Ship v1 with zero write tools and you beat everything on the market.

3. **Do not auto-generate the tool surface from the OpenAPI spec.** `twilio-labs/openapi-mcp-server` would hand you ~200 flat tools including every `DELETE`, with no notion of blast radius. Hand-pick and **compose reads into questions**, not endpoints.

4. **`PUT /tracking-plans/{id}/rules` replaces ALL rules.** This is the single most likely way an LLM causes real damage here, because `PUT` reads as "update" to a model. An agent sending a partial rule set wipes the governance contract for the entire workspace.

5. **Violations have no API.** They are UI-only — Source → Schema tab → Violations. No endpoint, no export, no documented retention. The only programmatic path is Segment's violation *forwarding*, which lands the data in a stream you control. If "find tracking plan violations" is a headline feature, that ingestion path has to be a first-class design item, not an afterthought.

6. **Region handling has a silent-failure mode.** EU workspaces hitting the Oregon Tracking API endpoint get **no error** and events simply never appear. Resolve region explicitly, always.

---

## 1. The gap

### Official

**None.** No `@segment/mcp` on npm, no `segment-mcp` on PyPI, nothing under `segmentio`.

**Twilio's MCP situation is two things and the distinction matters:**

- **`mcp.twilio.com/docs`** — hosted, Public Beta, **no auth required**, because it only indexes public API specs. Two tools: `twilio__search` and `twilio__retrieve`. It indexes Twilio Segment *docs*. **It can tell an agent how the Segment API works. It cannot call it, read a workspace, or touch data.**
- **`twilio-labs/mcp`** — self-hosted monorepo, 107 stars, TypeScript. **Last release 0.0.3, March 2025** — ~17 months stale. **No mention of Segment anywhere in the repo.**

**SIGNAL 2026 announcements:** Conversation Memory, Conversation Orchestrator, Conversation Intelligence — all conversational AI. **No Segment MCP server, no Segment agent tooling, no agentic profile API.** The CDP appears as positioning language only.

### Community

| Thing | Status |
|---|---|
| `NoBanks/segment-mcp` | Python, **0 stars, 1 commit**, MIT. Tracking API **only** — `track_event`, `identify_user`, `group_user`, `page_event`, `batch_events`. Author explicitly calls it "a write-only rail." |
| npm `segment-mcp-server` | **v1.0.0, published 2026-04-26, only version ever.** No repo linked. 18 tools across sources/destinations/tracking-plans/tracking. Its README header says "Config API" but the code calls the Public API. **Advertises "profile lookup" — no profiles tool exists in the shipped code.** No EU region handling, no confirmation gating on `delete_source` or `create_destination`. |
| PyPI | `segment-mcp`, `segment-mcp-server`, `mcp-segment`, `twilio-segment-mcp`, `segment-mcp-python` — **all 404** |
| Zapier | `zapier.com/mcp/twilio-segment` — generic API wrapper, not purpose-built |

⚠️ **mcp.so and smithery.ai were robots-disallowed from the research environment — treat as unchecked.** Verify before claiming "first."

---

## 2. Positioning

**One line:** the Segment MCP that answers questions instead of exposing endpoints.

**Three defensible claims:**

1. **Read-only by default, and it means it.** `SEGMENT_MCP_MODE=read` is the default and shipping v1 with zero writes is a feature, not a limitation. Consistent with `gtmplugin`, `campaignpreflightplugin`, `instantlymcp` and `n8n-operator` — this is the sixth expression of the same thesis.
2. **Composed questions, not endpoint passthrough.** `audit_event_routing` joins four endpoints into one answer. An LLM chaining four calls per source hits rate limits and loses the thread.
3. **Region-correct and rate-limit-aware.** Handles the EU silent-failure mode, lowercases Profile API external IDs, reads `X-RateLimit-Remaining`, honors `Retry-After`.

**What it is NOT:** a way to write events into a production CDP from a chat window.

---

## 3. Scope

### v0.1 — read-only, composed (target: 2.5 weeks)

| In | Out |
|---|---|
| Public API reads: sources, destinations, tracking plans, warehouses, delivery metrics | **All writes** |
| Five composed question-tools (§5) | Profile API |
| Region resolution (US / EU) | Regulations |
| Rate-limit handling from response headers | Audiences |
| `SEGMENT_MCP_MODE` with `read` as default | Tracking API |

### v0.2 — governance + profiles (target: +2 weeks)

- Profile API as a **separate trust tier** — distinct credential, explicit opt-in, every call logged
- Audiences and computed traits, read-only
- IAM / audit trail reads
- Reverse ETL model and sync-status reads
- Usage and Monitoring endpoints for dead-source detection

### v0.3 — narrow, gated writes (target: +2 weeks)

- Label management, schema-settings adjustment, enable/disable an existing destination
- Every write echoes the change back for confirmation before executing
- Violation-forwarding ingestion path (the only route to violation data)

### Explicit non-goals, permanently

- **`POST /regulations`** in any form. See §6.
- Tracking API writes (`track`/`identify`/`page`/`group`/`batch`) unless pointed at a dedicated test source. Injecting synthetic events into a production CDP pollutes analytics, can trigger downstream automations and real customer messages, and **cannot be un-sent**. This is what both existing servers lead with.
- Auto-generated tool surfaces.

---

## 4. API ground truth

### Public API — the one to build on

| | |
|---|---|
| Docs | `https://docs.segmentapis.com` (spec version 73.2.0 — semver of the spec, **not** a path prefix) |
| Base URL (US) | `https://api.segmentapis.com` |
| Base URL (EU) | `https://eu1.api.segmentapis.com` |
| Auth | `Authorization: Bearer $TOKEN` |
| Token minting | Only a **Workspace Owner** can create tokens |
| Availability | **Team and Business tier only** — not Free, not Add-on |
| Transport | HTTPS only; port 80 refused |

**There is no `/v1` in the path.** `GET https://api.segmentapis.com/sources`. This is the most common thing to get wrong.

### Config API — frozen, not deprecated

Base `https://platform.segmentapis.com/v1beta/`. **As of 1 Feb 2024 new Config API tokens cannot be created in the app.** Existing tokens work; nobody new can get one. Segment's own language: *"Future improvements will be added to the Public API only."* Its coverage is a strict subset. One legacy difference: Config API identifies by **slug**, Public API by **unique ID**. **Ignore it entirely.**

### Tracking API

Base `https://api.segment.io/v1/` (Oregon default) or `https://events.eu1.segmentapis.com/` (EU). Auth: write key in body, or HTTP Basic with base64 of `writeKey:` (trailing colon, empty password). Limits: **32 KB per call**; batch **500 KB per request, 32 KB per event, 2,500 events max**; ~1,000 req/sec per workspace.

⚠️ **EU workspaces hitting Oregon get no error and events never appear.**

### Profile API

Base `https://profiles.segment.com` / `https://profiles.euw1.segment.com`. Auth: **HTTP Basic with the access token as username and a blank password** — different from every other Segment API. **100 req/sec per Space.**

```
/v1/spaces/{spaceId}/collections/{users|accounts}/profiles/{id_type:value}/{traits|external_ids|events|metadata|links}
```

⚠️ **Lookups are case-sensitive and must be lowercase.** Wrong casing returns an empty result, not an error. Traits default 10, max 200 via `?limit=200`. `/events` returns **14 days** only. `/links` caps at 20.

### Rate limits

Computed from sender IP + token + request complexity, enforced **per endpoint, per token, and at token level separately**. Every response carries `X-RateLimit-Consumed`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` (RFC 5322 timestamp). On 429: `data.msBeforeNext`, `data.remainingPoints`, `data.consumedPoints`; token-level 429s carry `Retry-After` instead — **the docs say prefer it over your own backoff**.

⚠️ **The global default is not published.** The Deletion & Suppression page describes 600/min as "lower than default 6,000/min," implying 6,000, but that came via a doc summarizer and is unconfirmed. **Do not hard-code it. Read the headers.**

Notably tight per-endpoint limits: audience previews **5/min AND 700/month**, reverse ETL syncs 20/min, audience-references 25/min, audience list 60/min. Agents naturally behave far above these.

---

## 5. The tools — composed questions, not endpoints

### v0.1 — the five that justify the project

**`audit_event_routing`** — *"Which destinations get which events?"*
The #1 unanswerable question in every Segment workspace. Composes `GET /sources` + `GET /sources/{id}/connected-destinations` + `GET /destinations/{id}` (settings + filters) + `GET /destinations/{id}/subscriptions`. **The value is the join.** Ship it as one tool.

**`trace_event`** — given an event name: which sources emit it, is it in a tracking plan, which rule governs it, which destination filters drop it, which subscriptions fire on it, which warehouses receive it.

**`find_stale_sources`** — *"Which sources have no recent data?"*
`GET /sources` joined against Usage (*Daily Per Source API Calls*, *Daily Per Source MTU*) and Monitoring event volumes. Finds abandoned instrumentation and unnecessary MTU spend. Direct RevOps money value.

**`check_delivery_health`** — `GET /destinations/{id}/delivery-metrics`, minute/hour/day granularity, **30-day window**. Finds silently failing destinations — the thing that quietly breaks attribution for a quarter before anyone notices.

**`find_ungoverned_sources`** — `GET /tracking-plans` + `/sources` + `GET /sources/{id}/settings`. Which sources are governed by **nothing at all**, and which are allowing rather than blocking unplanned events.

### v0.2

| Tool | Composes |
|---|---|
| `lookup_profile` | Profile API `/traits`, `/external_ids`, `/events` (14d), `/links` — **separate trust tier** |
| `audit_audience_consumers` | `GET /spaces/{id}/audiences` + `/audience-references` (which destinations consume this audience — genuinely hard otherwise) |
| `check_reverse_etl_syncs` | models, mappings, sync status and history |
| `who_changed_what` | Admin/IAM audit trail |
| `diff_tracking_plans` | `/rules` across two plans |

### Endpoint inventory (verified, for reference)

```
# Sources
GET/POST /sources · GET/PATCH/DELETE /sources/{id}
GET /sources/{id}/connected-destinations · /connected-warehouses
GET/PATCH /sources/{id}/settings
POST /sources/{id}/writekey · DELETE /sources/{id}/writekey/{key}
POST /sources/{id}/labels · PUT /sources/{id}/labels        ← PUT REPLACES

# Destinations
GET/POST /destinations · GET/PATCH/DELETE /destinations/{id}
GET /destinations/{id}/delivery-metrics                      ← the audit goldmine, 30 days
GET/POST/PATCH/DELETE /destinations/{id}/subscriptions       ← Alpha, 5 req/min, needs enablement

# Tracking Plans
GET/POST /tracking-plans · GET/PATCH/DELETE /tracking-plans/{id}
GET /tracking-plans/{id}/rules
PUT /tracking-plans/{id}/rules                               ← REPLACES ALL RULES
PATCH/DELETE /tracking-plans/{id}/rules
GET/POST/DELETE /tracking-plans/{id}/sources

# Warehouses
GET/POST /warehouses · GET/PATCH/DELETE /warehouses/{id}
GET /warehouses/{id}/connection-state                        ← 200 req/min
GET/POST/DELETE /warehouses/{id}/connected-sources[/{sourceId}]

# Audiences (Engage)
GET /spaces/{sid}/audiences                     60/min
POST /spaces/{sid}/audiences                    50/min
GET /spaces/{sid}/audiences/{id}               100/min
POST /spaces/{sid}/audiences/previews      5/min AND 700/MONTH
GET /spaces/{sid}/audiences/{id}/audience-references  25/min
POST /spaces/{sid}/audiences/{id}/runs                 ← fires activations, sends real messages

# Reverse ETL
GET/POST /reverse-etl-models · GET/PATCH/DELETE /reverse-etl-models/{id}
POST /reverse-etl-syncs                         20/min  ← pushes records into Salesforce/HubSpot
POST /reverse-etl-models/{id}/syncs/{sid}/cancel

# Regulations — READ ONLY, see §6
GET /regulations · GET /regulations/{id} · GET /suppressions
POST /regulations · POST /regulations/sources/{id}      ← DO NOT EXPOSE
```

---

## 6. Destructive-action tiers — the safety design

### Tier 1 — irreversible, destroys real user data. **Do not expose. At all.**

- `POST /regulations` — **workspace-scoped deletion/suppression across every source**
- `POST /regulations/sources/{sourceId}` and `/cloudsources/{sourceId}`

Types `DELETE_ONLY`, `SUPPRESS_WITH_DELETE`, `DELETE_INTERNAL` **permanently destroy user data. There is no undo.** `UNSUPPRESS` reverses suppression but **cannot restore deleted data.** These accept an **array of subjects**, so one malformed tool call deletes thousands of profiles.

**Decision: expose only the reads (`GET /regulations`, `GET /regulations/{id}`, `GET /suppressions`). Leave creation to the UI, permanently.** This is not a v3 feature waiting for the right gate. It is a line.

### Tier 2 — destroys configuration, breaks live pipelines

`DELETE /sources/{id}` · `DELETE /sources/{id}/writekey/{key}` (**instantly breaks every shipping client, no grace period**) · `DELETE /destinations/{id}` · `DELETE /warehouses/{id}` · `DELETE /functions/{id}` · `DELETE /tracking-plans/{id}` · `DELETE /spaces/{sid}/audiences/{id}`

Gate: `admin` mode + typed confirmation naming the exact resource.

### Tier 3 — replace-semantics that silently discard state. **The sneaky ones.**

- **`PUT /tracking-plans/{id}/rules`** — replaces **all** rules. A partial rule set from an agent wipes workspace governance. **`PUT` reads as "update" to a model.** If this is ever exposed, the tool must require the full current rule set as input and diff it.
- `PUT /sources/{id}/labels` — replaces, not appends
- `PATCH /destinations/{id}` — settings changes can misroute live traffic
- `PATCH /sources/{id}/settings` — can start dropping events workspace-wide

### Tier 4 — side-effecting, expensive or noisy

- `POST /reverse-etl-syncs` — not destructive in Segment, **potentially very destructive in the CRM downstream**
- `POST /spaces/{sid}/audiences/{id}/runs` — fires activations, can send real messages
- `POST /functions/{id}/deploy` — pushes code into the live data path
- `POST /destinations` — new billable resource that **immediately starts shipping customer data to a third party**
- `POST /audiences/previews` — burns a **700/month** budget
- All Tracking API writes

### Mode model

```
SEGMENT_MCP_MODE = read (default) | write | admin
```
- `read` — every tool in §5. No mutations reachable.
- `write` — adds Tier 3 PATCHes and label management, each echoing the change for confirmation first.
- `admin` — adds Tier 2 deletes with typed confirmation.
- **Tier 1 is unreachable in every mode.**

This maps directly onto `instantlymcp`'s existing autonomy-tier pattern. Reuse the implementation.

---

## 7. Feature inventory, scoped

| # | Feature | Effort | Verdict |
|---|---|---|---|
| 1 | Public API client: region resolution, header-driven rate limiting, `Retry-After` | 3d | **v0.1** |
| 2 | `audit_event_routing` | 2d | **v0.1** |
| 3 | `trace_event` | 2d | **v0.1** |
| 4 | `find_stale_sources` | 2d | **v0.1** |
| 5 | `check_delivery_health` | 1d | **v0.1** |
| 6 | `find_ungoverned_sources` | 1.5d | **v0.1** |
| 7 | Mode model with `read` default | 1d | **v0.1** |
| 8 | Tier 1 hard-excluded, documented as a deliberate refusal | 0.5d | **v0.1** |
| 9 | MCP tool annotations (`readOnlyHint: true` on every v0.1 tool) | 0.5d | **v0.1** |
| 10 | Profile API, separate credential + opt-in + per-call logging | 3d | v0.2 |
| 11 | Lowercase-external-ID normalization with a warning | 0.5d | v0.2 |
| 12 | Audiences read + `audit_audience_consumers` | 2d | v0.2 |
| 13 | IAM / audit trail reads | 1.5d | v0.2 |
| 14 | Reverse ETL sync status | 1.5d | v0.2 |
| 15 | Usage + Monitoring for dead-source detection | 2d | v0.2 |
| 16 | Violation-forwarding ingestion path | 4d | v0.3 |
| 17 | Gated writes with confirmation echo | 3d | v0.3 |
| 18 | `diff_tracking_plans` | 1.5d | v0.3 |
| 19 | `agent-audit` emission (dogfood the other project) | 1d | v0.3 |
| 20 | Auto-generated tool surface from OpenAPI | — | **Never** |
| 21 | Tracking API writes against production | — | **Never** |

---

## 8. Repo structure

```
segment-mcp/
├── README.md              # "read-only by default" above the fold; the Tier 1 refusal stated plainly
├── LICENSE                # MIT
├── CHANGELOG.md · CONTRIBUTING.md · SECURITY.md
├── pyproject.toml
├── .github/workflows/{ci,codeql}.yml
├── src/segment_mcp/
│   ├── client/
│   │   ├── public_api.py      # region resolution + rate limiting
│   │   ├── profile_api.py     # Basic auth, blank password; lowercase normalization
│   │   └── regions.py
│   ├── tools/
│   │   ├── routing.py · governance.py · health.py · profiles.py
│   ├── modes.py               # read | write | admin
│   └── server.py
├── docs/
│   ├── what-this-refuses-to-do.md   # Tier 1, and why — the differentiating doc
│   ├── permissions.md
│   ├── regions.md
│   └── violations.md          # why they aren't readable, and the forwarding workaround
└── tests/
    ├── fixtures/              # recorded API responses, US and EU
    └── test_tier1_unreachable.py    # the test that proves the thesis
```

---

## 9. Milestones

| # | Deliverable | Done when |
|---|---|---|
| M1 | Client + region resolution | US and EU both resolve correctly; a deliberate EU-to-Oregon call is caught, not silently dropped |
| M2 | `audit_event_routing` | One call answers the question for a real workspace |
| M3 | Remaining four v0.1 tools | All five composed tools work against recorded fixtures |
| M4 | Mode model + `test_tier1_unreachable` | Regulations creation is not reachable in any mode, proven by test |
| M5 | **v0.1.0 + terminal GIF** | GIF shows one question → one answer that would take 40 UI clicks |
| M6 | Registry submissions | awesome-mcp-servers, MCP Registry, Smithery, Anthropic plugin directory |
| M7 | Profile API tier | Separate credential path; every lookup logged |
| M8 | Violation forwarding | v0.3 |

---

## 10. Distribution

1. **awesome-mcp-servers** — the *Customer Data Platforms* category is notably sparse.
2. **MCP Registry** — `io.github.katekruger/segment-mcp`, publishable from a GitHub Action in the repo.
3. **Smithery** — lowest bar in existence: a public HTTPS endpoint with streamable HTTP transport. No repo required.
4. **Anthropic's plugin directory** — open submission form, quality + security review.
5. **The post: "What a Segment MCP server should refuse to do."** The Tier 1 refusal is the story. Both existing community servers lead with the Tracking API write path; explaining why that's backwards is a genuinely useful piece of writing and it doubles as the positioning.
6. **Segment's own community + r/RevOps.** `audit_event_routing` answers a question every Segment admin has and none can answer.

---

## 11. Open questions to resolve before M1

1. **Check mcp.so and smithery.ai** — both were robots-disallowed from the research environment. Verify nothing substantive exists before claiming "first."
2. **Find the OpenAPI spec download URL.** The Redocly "Download OpenAPI specification" button's href did not survive fetching; `docs.segmentapis.com/openapi.yaml` was proxy-blocked. It is reachable from the docs UI and reflected in `segmentio/public-api-sdk-{python,typescript,go,java,swift}`. Use it for **types and validation**, never for tool generation.
3. **Confirm the global default rate limit.** The 6,000/min figure is inferred, not confirmed. Read headers regardless.
4. **Get a Team or Business tier workspace for testing.** The Public API is not available on Free — this is a hard prerequisite and worth sorting in week one.
5. **Verify Destination Subscriptions availability.** Alpha, requires workspace enablement; `audit_event_routing` should degrade gracefully when they're unavailable rather than erroring.
6. **Decide the Profile API's place.** It returns PII on named individuals — the most privacy-sensitive read in the surface. Recommendation: separate credential, explicit opt-in, per-call logging, and a README section that says so plainly.

---

## 12. Sources

- [Segment Public API docs](https://docs.segmentapis.com/) · [Getting Started](https://docs.segmentapis.com/tag/Getting-Started/) · [Rate Limits](https://docs.segmentapis.com/tag/Rate-Limits/) · [Sources](https://docs.segmentapis.com/tag/Sources/) · [Destinations](https://docs.segmentapis.com/tag/Destinations/) · [Tracking Plans](https://docs.segmentapis.com/tag/Tracking-Plans/) · [Warehouses](https://docs.segmentapis.com/tag/Warehouses/) · [Audiences](https://docs.segmentapis.com/tag/Audiences/) · [Reverse ETL](https://docs.segmentapis.com/tag/Reverse-ETL/) · [Deletion and Suppression](https://docs.segmentapis.com/tag/Deletion-and-Suppression/)
- [Public API overview](https://www.twilio.com/docs/segment/api/public-api) · [Config API](https://www.twilio.com/docs/segment/api/config-api) · [HTTP Tracking API](https://www.twilio.com/docs/segment/connections/sources/catalog/libraries/server/http-api) · [Profile API](https://www.twilio.com/docs/segment/unify/profile-api) · [Review violations](https://www.twilio.com/docs/segment/protocols/validate/review-violations)
- [Twilio MCP server](https://www.twilio.com/docs/ai/mcp) · [twilio-labs/mcp](https://github.com/twilio-labs/mcp) · [SIGNAL 2026 announcements](https://www.twilio.com/en-us/blog/products/signal-2026-product-announcements)
- [NoBanks/segment-mcp](https://github.com/nobanks/segment-mcp) · [npm segment-mcp-server](https://libraries.io/npm/segment-mcp-server)
