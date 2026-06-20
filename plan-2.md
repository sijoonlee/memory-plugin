# Plan 2 — Memory Read Side & Taxonomy

Next-phase plan, derived from the design discussion in `brain-storm.md`. memory-mcp
has the **write** side right (decoupled extractor over events). This plan builds the
**read** side and sharpens **what** gets stored, in three prioritized steps.

> Status: **not started** — this is the plan, not yet implementation.

Guiding principle (see `brain-storm.md` §0): *push, don't pull.* memory-mcp is a
**guest** in the host agent (Claude Code) — it can't own the system prompt, so it
makes memory **present** through channels it controls (hooks), rather than relying
on the agent to remember to search.

---

## Milestone 18 — Storage layer: unify the model + memory lifecycle

Two live sub-steps on the unified memory model. **18-1 unifies the candidate and memory
into one model** (a candidate is a memory with `status="pending_review"`); **18-3 drops
the approval gate** — extraction creates `active` memories directly, with a read/unread
inbox + archive/delete for post-hoc curation. **18-2 (storage adapter boundary) is
dropped** — see its tombstone below. Data does not need to be migrated — the store has
been experimental so far, so the existing `.memory-mcp` can be recreated from scratch.

> Status: 18-1 and 18-3 implemented. 18-2 dropped. **18-4** (optional map-reduce
> extraction) is specced at the **bottom of this file**.

### Milestone 18-1 — Unify candidate & memory into one model

#### Goal
Make a memory candidate and a memory the **same model**, distinguished only by
`status`. "Approving" a candidate becomes a status transition
(`pending_review → active`), not a translation between two schemas. Today the two
carry near-identical data under different field names
(`situation/lesson/action` vs `what_happened/when_useful/helpful_explanation`) and
live in different stores; this collapses them.

#### Model (two-field core)
- `when_useful` — recall cue doing triple duty: the Layer-1 catalog line (M20), the
  `memory_search` embedding cue, and the `memory_get` trigger. Keystone field; keep
  the name.
- `details` — free-form body (Layer 2). **Replaces** today's `what_happened` +
  `helpful_explanation`; type-neutral across the M19 taxonomy.
- plus `memory_type` (M19), `tags`, `source`, `confidence`, `score`, `project`.
- `status` — union lifecycle: `pending_review → active → {stale | superseded |
  invalid}`, plus `rejected | merged | archived`. `memory_search` already filters to
  `status="active"`, so pending rows are naturally excluded from retrieval.

#### Approval = transition handler (not a bare flip)
`pending_review → active` must still do the two things `create_memory` does today:
(a) **embed** the text into the vector store, and (b) **dedupe-on-create**. The three
dedupe outcomes map onto statuses — keep → `active`, duplicate-merge → `merged`,
reject → `rejected`. **Embed at activation**: pending rows stay un-embedded; merge
clustering (M13) keeps its lexical signal for now.

#### Extractor stays at its JSON contract
The LLM extractor's structured output (`situation/lesson/action/category/…`,
`extractors.py`) is unchanged. The single composition chokepoint
(`ExtractionWorker._create_candidates`) maps it into the unified shape instead of a
`MemoryCandidateCreate`:
- `situation` → `when_useful`
- `lesson` + `action` → `details` (composed)
- `category` → `memory_type` (M19)
- `confidence`, `evidence_event_ids`, `evidence_summary`, `source_session_segment_id`
  → `confidence` / `source`

Candidate-only provenance lives in `MemorySource`, which already carries
`evidence_event_ids` + `creation_reason`.

#### Changes
- **Model:** fold `MemoryCandidateCreate` / `MemoryCandidateRecord` into
  `MemoryCreate` / `MemoryRecord` (`core/models.py`); rename body fields to
  `when_useful` + `details`; extend `MemoryStatus` with `pending_review` + `merged`;
  remove the parallel candidate model from `core/events.py`.
- **Generation:** `ExtractionWorker._create_candidates` and the merge workers
  (`merge_proposal_worker.py`, `CandidateWorker.merge_candidates`) construct
  `pending_review` memories.
- **Approval:** replace `_candidate_to_memory_create` + `approve_candidate` with an
  `activate(memory_id)` transition that embeds + dedupes and sets the resulting status.
- **Surfaces:** `candidate_list` / review service / CLI list `pending_review`
  memories; old field names (`what_happened`, `helpful_explanation`) updated
  everywhere (incl. `_MEMORY_SUMMARY_FIELDS`).

#### Files
`core/models.py`, `core/events.py`, `core/store.py`,
`pipeline/workers/extraction_worker.py`, `pipeline/workers/candidate_worker.py`,
`pipeline/workers/merge_proposal_worker.py`, `review/service.py`, `mcp_server/*`,
`cli.py`, tests.

#### Verification
- Round-trip: extractor output → `pending_review` memory → `activate` → `active`
  memory; `when_useful` + `details` populated; dedupe still merges/rejects.
- `memory_search` excludes `pending_review`; the M20 catalog lists only `active`.

### Milestone 18-2 — Storage adapter boundary (DROPPED)

**Dropped — the direction changed from *replacement* to *additive*.** 18-2 was a
`MemoryStore` protocol seam whose sole purpose was to let M22 *swap* `LocalMemoryStore`
for a remote backend without forking callers. But the shared server is no longer a
replacement for the local store — it is an **additional** destination you optionally
push memories to (and later pull from, into the local store). So:

- the local store stays concrete (there is only ever one local store — nothing to
  abstract over);
- the shared server is a separate **client** (`push` / later `pull` + auth), not an
  alternate `MemoryStore` implementation with the same CRUD/search surface;
- even the pull path caches shared memories *into the local store* (`origin=shared`
  rows), so retrieval stays a single local search — no federated-query interface needed.

What's actually needed instead — a `SharedMemoryClient` (push-first, optional pull) —
lives in the shared-server work (M22), not in a local storage-abstraction milestone.

---

### Milestone 18-3 — Drop the approval gate: auto-active memories + read/unread + archive

#### Goal
Stop gating extracted memories behind manual approval. Extraction creates an `active`
memory directly (it's immediately searchable); curation moves from a *pre*-approval
queue to *post-hoc* management. The "what haven't I looked at?" workflow is preserved
by a separate **read/unread** flag, not by withholding the memory from retrieval.

Rationale: approval queues rot in practice, and the extractor already self-gates (it
only emits a lesson when one exists; redaction + dedupe already run on create). A bad
auto-memory is local-only and self-corrects via decay + delete, so the gate buys little
for a personal store.

#### Two independent axes (the key design point)
`status` and `is_reviewed` are **orthogonal**; neither gates the other:

| Axis | Values | Controls |
| --- | --- | --- |
| `status` | `active` ⇄ `archived` (+ `stale`/`invalid`/`superseded` from feedback) | whether it is *retrievable* (`search_memories` filters `status="active"`) |
| `is_reviewed` | `false` (unread) / `true` (read) | the user's review inbox only — **no** effect on retrieval |

So an extracted memory is `active` + `is_reviewed=false`: live for the agent
immediately, but flagged in the user's inbox until checked. `is_reviewed` must be a
*separate field*, never a status value (an "unread" status would fall out of search).

#### Decisions (settled)
- **Field:** `is_reviewed: bool = false`; the user toggles it (check/uncheck) — read
  *and* unread are both explicit, reversible states. (Not auto-derived from agent
  retrieval; `is_reviewed` means *a human looked at it*, distinct from
  `retrieval_count`.)
- **Manual memories** (`memory_create` via CLI/MCP) also start `is_reviewed=false`, and
  are distinguishable by a **`manual`** filter derived from `source.kind == "manual"`
  (vs `pipeline_candidate` for extracted) — no new field needed for origin.
- **Unread memories surface to the agent** (intended — this is the no-gate choice).
- **`pending_review` is retired** — the read/unread flag covers its tracking purpose.
  Leave the approval-gate code (`approve_candidate`/`activate`/`reject`) dormant rather
  than deleting it, so a future opt-in "review mode" stays cheap; keep the merge
  workflow (still useful on active memories).

#### Changes
- **Model:** add `is_reviewed: bool = False` to `MemoryRecord` (`core/models.py`); a
  denormalized `memories.is_reviewed` column + index + in-place backfill, mirroring how
  `project` was added in M17 (existing rows backfill to `false`/unread).
- **Generation:** `ExtractionWorker._create_candidates` calls `create_memory` (active,
  embedded, deduped) instead of `create_pending`. Optional cheap quality knob: only
  auto-create when extractor `confidence ≥ threshold` (deferred unless wanted).
- **Lifecycle helpers (`core/store.py`):** `set_reviewed(id, bool)`;
  `archive_memory(id)` (`active→archived`, status flip only — vector persists, no
  re-embed) and `restore_memory(id)` (`archived→active`). Hard `delete_memory` (M15)
  already exists.
- **Review service/API:** repurpose from approval queue to memory manager — list with
  filters (**unread** / all / manual / archived), `mark reviewed/unreviewed`, archive,
  restore, delete. Routes: `POST /api/memories/{id}/reviewed`, `/archive`, `/restore`,
  `DELETE /api/memories/{id}`.
- **Review UI:** default view = **Unread inbox** (`active` + `is_reviewed=false`) with a
  count; plus All / Manual / Archived; row actions: toggle reviewed, archive/restore,
  delete. (Inline **edit** of an active memory is deferred — it requires re-embedding
  the vector, unlike archive/delete which are status-only.)
- **Operator status (`operator.py`):** add an `unread` count; move `archived` to the
  memory side; drop the now-unused `pending_review`/`rejected` buckets.
- **MCP:** `candidate_list` becomes vestigial — repurpose to a `memory_list` filter
  (e.g. unread) or remove; surface the unread count in `memory_status`.

#### Files
`core/models.py`, `core/store.py`, `pipeline/workers/extraction_worker.py`,
`review/service.py`, `review/server.py`, `review/static/*`, `operator.py`,
`mcp_server/service.py` + `server.py`, `cli.py`, tests, `README.md`.

#### Verification
- Extraction produces an `active`, `is_reviewed=false` memory that is immediately
  returned by `search_memories`; the unread inbox lists it; toggling `is_reviewed`
  does not change retrievability.
- `archive_memory` removes it from search and the default view but keeps the row;
  `restore_memory` brings it back without re-embedding; `delete_memory` is permanent.
- `manual` filter surfaces `source.kind="manual"` memories; both manual and extracted
  start unread.

#### Defer
- **Share / shared server** (publish a memory to a team server): out of scope here;
  revisit when the time is right. The likely shape (settled in discussion): local-first
  and private by default — events never leave; only explicitly selected memories
  publish; a thin shared registry consolidates published memories (dedup/contributor
  count); local may later *pull* shared memories read-only with conflict resolved at
  read time (local wins). Candidate-level pooling was considered and set aside in favor
  of this memory-level, opt-in model.

---

## Milestone 19 — Improve taxonomy: `user` / `feedback` / `project` / `reference`

> Status: **implemented**. Decisions taken: the freeform 5-value `category` was
> **replaced** entirely by the 4-value `memory_type` (merge clustering re-keyed to
> it, with a legacy fallback for pre-M19 rows); **no backfill** — pre-existing rows
> stay untyped (`NULL`). `memory_type` is a denormalized, nullable column mirroring
> `project`, and is surfaced in `memory_create` / `memory_search` (CLI + MCP).

### Goal
Give each memory a constrained **type** from a fixed 4-value taxonomy. This replaces
the extractor's current freeform 5-value `category` label (`clue_location`/…). After
the model unification (18-1) there is a single `Memory` model, so `memory_type` is one
field — set when the `pending_review` memory is created and carried through to
`active` — with no separate candidate model to thread it through.

- `user` — who the user is (role, expertise, durable preferences)
- `feedback` — how the agent should *work* (corrections, confirmed approaches) + *why*
- `project` — ongoing work / goals / constraints **not derivable from code or git**
- `reference` — pointers to external resources (URLs, docs, dashboards)

### Why this improvement is important
- **Precision forcing-function.** A fixed taxonomy makes the extractor (and any
  reviewer) classify before saving. If a candidate doesn't fit a type cleanly, it's
  usually junk — this raises the store's signal-to-noise, the same way it does for
  Claude Code's own memory.
- **It is the prerequisite for a good catalog (Milestone 20).** A typed catalog can be
  **grouped and scoped** — e.g. always surface `user` + `feedback`, surface
  `project` only for the active repo. Without types, the catalog is a flat,
  undifferentiated list that's harder for the agent to scan.
- **Per-type shape and retrieval policy.** Different types want different handling
  (`feedback` must carry a "how to apply"; `reference` is a pointer; `user` is
  rarely stale). Types let later logic treat them differently.
- **Legibility.** A typed memory tells the agent *what kind of thing* it is at a
  glance, which improves both recall decisions and review.

### Changes
- **Schema:** add `memory_type: Literal["user","feedback","project","reference"]`
  to the unified `MemoryCreate` (inherited by `MemoryRecord`) in `core/models.py`,
  plus a DB column (mirror how `project` was added in M17 — denormalized column +
  backfill for pre-existing rows; pick a default like `reference` or `project` for
  legacy rows). One model after 18-1, so this is the only place the field is added.
- **Extractor:** replace the extractor's freeform 5-value `category`
  (`clue_location`/… in `pipeline/extractors.py`) with the 4-value `memory_type`
  taxonomy; update the prompt to classify into exactly one type, with the per-type
  definitions above and the "if it doesn't fit, skip it" instruction. The 18-1
  composition chokepoint (`_create_candidates`) then sets `memory_type` directly.
- **Propagation:** nothing extra — `memory_type` is set when the `pending_review`
  memory is created and simply persists through `pending_review → active` (no
  cross-model carry, since candidate and memory are one model).
- **Surface:** add `memory_type` to `_MEMORY_SUMMARY_FIELDS` (`mcp_server/service.py`)
  and the `create` / `search` CLI + MCP tools.

### Files
`core/models.py`, `pipeline/extractors.py`, `mcp_server/service.py`, `cli.py`, tests.

---

## Milestone 20 — Two-layer "memory on demand": catalog `when_useful` → `id`, pull on need

### Goal
Generate a compact **catalog** (Layer 1) of `when_useful` → `id` lines that the host
can inject; the agent reads full detail **on demand** via `memory_get(id)` (Layer 2).
See `brain-storm.md` → *# Good idea: using the two-layer approach*.

### Why this improvement is important
- **It fixes pull-only's fragility.** Today a memory only matters if the agent
  *chooses* to call `memory_search` — which it usually won't, because nothing tells
  it memories exist. The catalog gives the agent **awareness of what's available**,
  which is what makes "use it when you need it" actually fire.
- **`memory_get` becomes the primary, cheaper path.** Seeing the exact `id` means no
  fuzzy semantic search, no `min_score` tuning — a precise fetch by id. `memory_search`
  remains the fallback for "no matching line, but here's my situation."
- **Cheap and low-risk.** The catalog is a thin formatter over `list_memories`
  (which already returns `when_useful`, `id`, `project`) — no new storage, no schema
  change, and it leaves formation/extraction untouched.
- **It respects the on-demand preference** (agent still decides) while removing the
  reason on-demand normally fails.

### Changes
- **Catalog generator:** a function/formatter over
  `LocalMemoryStore.list_memories(project=…)` projecting each memory to a
  `when_useful` → `id` line, **grouped by `memory_type`** (Milestone 19), project-scoped
  (M17: repo memories + globals).
- **CLI command:** `memory-mcp catalog [--project] [--limit]` that prints the catalog
  block to stdout (so a hook can emit it). Bounding: small store → all; large store →
  top-N by score.
- **Pull path:** none needed — `memory_get` / `memory_search` already exist.

Example catalog output:
```
<memory-catalog project="/Users/sijoonlee/Documents/coding/memory-mcp">
[feedback]
- mem_8a03d7…  When deciding whether to use Graph RAG for the memory store
[project]
- mem_a4f966…  When deciding how memories get formed in memory-mcp
</memory-catalog>
Call memory_get(<id>) to read the full memory when a line looks relevant.
```

### Dependency it exposes
`when_useful` now does **double duty**: embedding cue for `memory_search` *and* the
catalog line the agent scans. Its quality decides whether the agent picks the right
memory → reinforces sharp, situation-specific `when_useful` text in the extractor
prompt (ties back to Milestone 19's extractor work).

### Files
`mcp_server/service.py` (or a new `catalog.py`), `cli.py`, tests.

---

## Milestone 21 — Use a hook (harness) to inject the catalog

### Goal
Inject the Milestone 20 catalog into the host agent's context **deterministically** via
a Claude Code hook, so the agent always sees what memories exist without being asked.
See `brain-storm.md` → *# Hook to inject prompt*.

### Why this improvement is important
- **A hook is harness-executed, not agent-discretion.** The harness runs it every
  time — the agent can't skip or deprioritize it. This gives a *guest tool* the one
  system-prompt property that matters here: **guaranteed presence**, without owning
  the system prompt.
- **Presence, not authority, is what the catalog needs.** The catalog is information
  to scan, not an instruction to obey — so a hook (strong on presence, weak on
  authority) is the *correct* channel. This is exactly why A3 (catalog) is a better
  fit for a guest than trying to force `memory_create` (which would need authority a
  guest can't get).
- **It completes the loop.** Milestones 19–20 produce a good, typed, scannable catalog;
  the hook is what actually puts it in front of the agent. Without it, the catalog is
  inert.
- **Reuses existing infrastructure.** The plugin already ships event-producer hooks
  (`hooks/claude-hooks.json`: UserPromptSubmit / PostToolUse / Stop calling
  `memory-mcp-event append`). Adding a context-injecting hook is the same pattern.

### Changes
- **Add a `SessionStart` hook** to `hooks/claude-hooks.json` that runs the catalog
  command and emits it to **stdout** (Claude Code adds SessionStart/UserPromptSubmit
  stdout to context). Note: unlike the `append` hooks, this one must **not** use
  `--quiet` — stdout *is* the payload. Scope to the session's project.
  ```json
  "SessionStart": [
    { "hooks": [ { "type": "command",
      "command": "uv --directory \"${CLAUDE_PLUGIN_ROOT}\" run memory-mcp catalog --root \"${CLAUDE_PLUGIN_ROOT}/.memory-mcp\"" } ] }
  ]
  ```
- **Entry point:** ensure the `catalog` command is reachable from the hook (either as
  `memory-mcp catalog` or a `memory-mcp-event catalog` subcommand alongside `append`
  in `hooks/cli.py`).
- **(Upgrade path, optional)** a `UserPromptSubmit` variant that filters the catalog
  by the current prompt for freshness — defer; SessionStart is the MVP.

### Files
`hooks/claude-hooks.json`, possibly `hooks/cli.py` (or `cli.py` for the `catalog`
command), `.claude-plugin/plugin.json` (no change if the hooks file path is reused).

---

## Sequencing & rationale

1. **Milestone 19 (taxonomy) first** — it shapes both the catalog grouping (M20) and the
   extractor output; doing it first avoids reworking the catalog later.
2. **Milestone 20 (catalog) second** — depends on M19 for typed grouping; pure read-side,
   no formation changes.
3. **Milestone 21 (hook) last** — depends on M20's catalog command existing; it's the
   thin delivery layer that makes M19+M20 visible to the agent.

Each step is independently shippable and reversible. M20+M21 touch **only the read
path** — formation, storage, and the extractor are untouched except for M19's typing.

---

## Verification (end-to-end, when built)

- **M19:** `uv run pytest` for schema/extractor/propagation; create a memory of each
  type via CLI + MCP and confirm `memory_type` round-trips and appears in
  `memory_list` / `memory_get`.
- **M20:** `uv run memory-mcp catalog --project <repo>` prints typed `when_useful`→`id`
  lines, project-scoped (repo + globals), bounded by `--limit`; tests assert grouping,
  scoping, and that ids resolve via `memory_get`.
- **M21:** install the plugin, start a Claude Code session in a repo with memories, and
  confirm the catalog appears in context at session start; the agent can then
  `memory_get(<id>)` a listed memory. Verify the `append` event hooks still fire
  (the new hook doesn't interfere).

## Open decisions (resolve during implementation)

- **`category` vs `memory_type`:** replace the freeform candidate `category`, or keep
  it as an optional sub-label under the type? (Lean: keep `category` as free sub-label,
  add `memory_type` as the constrained axis.)
- **Legacy default:** what `memory_type` to backfill existing memories with
  (`reference`? `project`? infer from tags like `user_correction` → `feedback`?).
- **Catalog bound:** top-N cutoff and ordering (by `score`? recency? type priority?).
- **Catalog scope at injection:** all types always, or type-filtered (e.g. omit
  `reference` unless relevant)?

---

## Milestone 22: Shared memory registry (additive, local-first)

**Reframed from "graduate local into a cloud service" (replacement) to "add an optional
shared registry alongside the local store" (additive).** The local store stays the
source of truth and the private working set; the shared server is a thin registry you
optionally **push** selected memories to, and later optionally **pull** from. It is
post-V1 and should not begin until the local MVP is stable.

### Privacy invariant (the whole point)
**Private by default; local-first; sharing is explicit, per-memory, never automatic.**
Enforced by architecture, not policy:

- the local pipeline (`events → memory → archive/delete`) never touches the network;
- **events never leave the machine** — only `active`, human-kept, redacted *memories*
  are even eligible to share;
- nothing is published except what the user explicitly selects, one action at a time;
- a `never_share` flag can pin a memory permanently local.

### Shape: thin registry, not a fat backend
The server holds **only published memories** + author/identity + access control +
server-side dedup. **No events, no candidates, no extraction server-side** — the
privacy-bearing pipeline stays local-only. The local-side component is a
`SharedMemoryClient` (push first, pull later), *not* an alternate `MemoryStore`
(this is why [the M18-2 adapter seam](#milestone-18-2--storage-adapter-boundary-dropped)
was dropped).

```
LOCAL (private, full pipeline)            SHARED REGISTRY (thin)
  events ──never leaves──
  memories (active/archived) ──push(opt-in, redacted, no local ids)──►  published memories
       ▲                                                                 + author/identity
       └────── pull (optional): cache as origin=shared, read-only ◄──    + access control
  retrieval = local search over (local ∪ cached shared)                  + server-side dedup
```

### Push (first increment)
- Explicit per-memory **publish** action over `active` memories (review UI + CLI/MCP).
- **Mandatory redaction + a human-visible preview/diff** of exactly what bytes go up.
- Strip local provenance: send `evidence_summary` text, **not** `evidence_event_ids` /
  `source_session_segment_id` (they only resolve against the local `EventStore`).
- Local marker `shared_at` / `shared_memory_id` so the UI knows what's published;
  unshare/retract is reversible (with audit).
- **Standardization without raw events:** server runs dedup-on-publish — a near-duplicate
  of an existing shared memory consolidates (attaches a contributor) instead of creating
  a fork; contributor count ranks canonical memories. (Optional steward curation.)

### Pull (deferred second increment — this is where the complexity lives)
- Subscribe to shared scopes (team/project); cache pulled memories **into the local
  store** as `origin=shared`, **read-only**. Retrieval stays a single local search over
  the union — offline-capable, no per-query network call.
- **Conflict resolved at read time, not storage time** (storage keeps two non-overlapping
  lanes: writable `origin=local`, read-only `origin=shared`, keyed by server id):
  - *overlap / near-duplicate*: advisory only — collapse at retrieval, don't merge;
  - *contradiction*: surface both with origin, never auto-supersede across the boundary;
  - *staleness*: shared memories carry a `version`; refresh replaces the cached row;
    local feedback survives (kept in `feedback_events`).
  - **Tie-break: local wins** — personal context beats team-authoritative, so the team
    can't silently change your agent's behavior.

### Identity & access control
- Author/owner identity on published memories; authn/authz (who can read/publish/retract).
- Scope beyond project: per-user, per-team, explicitly-shared. Audit trail across users.
- Server-side multi-writer correctness (concurrent publish/dedup without races).

### Frontend / ops
- A web UI for the shared registry (browse/search shared memories, contributor counts,
  steward actions). The **local review UI stays** as the local memory manager (M18-3) —
  it is not replaced.
- Deployment, backups, observability, rate limiting; tenant isolation; redaction
  (Milestone 11) becomes mandatory at the publish boundary.

### Phasing
1. **Push-only** — publish + redaction preview + server dedup-on-publish + contributor
   count. Delivers the privacy-respecting sharing model for a fraction of the cost.
2. **Pull + conflict-at-read** — only if local-cache retrieval over team memories is
   wanted (browsing the registry UI may be enough). All the conflict complexity is here.

Candidate-level pooling (sending un-approved candidates to a server for cross-user
pattern mining) was considered and **set aside** in favor of this memory-level, opt-in
model — it keeps events *and* un-vetted candidates local.

---

## Milestone 18-4 — Map-reduce extraction for oversized segments (ChunkingExtractor)

(Logically part of the M18 extraction family; parked at the bottom because it is
optional polish, not on the critical path.)

### Goal
Extract from a segment that is too large for one model call **without losing data**.
M18-3's size caps (per-event truncation + a total-prompt budget) are a *lossy* floor:
events past the budget are dropped. This milestone makes large-segment extraction
*lossless* by splitting the segment's events into prompt-sized chunks, extracting each,
and concatenating the candidates — a shallow **map-reduce** (one level of fan-out, no
deep recursion).

### Why a separate, optional milestone
It trades **LLM cost for completeness**: a chunked segment costs N model calls instead
of 1. So it is opt-in and cost-bounded, and small segments are completely unaffected
(they stay a single call). M18-3's truncation remains the cheap default and the floor.

### Design (option B — chunk in the extractor)
A `ChunkingExtractor` **decorator** over the `MemoryExtractor` protocol; the worker,
prompt builder, and protocol are unchanged. Segments stay *temporal* units (one
coherent session); chunking is purely an extraction concern.

```
ChunkingExtractor(base, *, max_chunks=8):
  extract(segment, events):
    if fits_in_one_prompt(events):        # cost guard: no fan-out when unnecessary
        return base.extract(segment, events)
    chunks = pack(events, budget=_MAX_PROMPT_EVENTS_CHARS)[:max_chunks]
    results = [base.extract(segment, chunk) for chunk in chunks]   # map
    return concat_candidates(results)     # reduce = dedupe-on-create, see below
```

- **Chunk boundary = the M18-3 budget.** Reuse `_MAX_PROMPT_EVENTS_CHARS` as the
  per-chunk size; greedy chronological packing. Per-event truncation
  (`_MAX_EVENT_PAYLOAD_CHARS`) stays as the floor *inside* a chunk (a single giant
  event still gets capped).
- **The "reduce" is mostly free.** After M18-3 each candidate flows through
  `create_memory` → **dedupe-on-create already merges cross-chunk duplicates**. So v1
  needs *no* LLM reduce: concatenate chunk candidates and let dedupe consolidate. (A
  true LLM *synthesis* reduce — for a lesson whose evidence spans chunks — is deferred.)
- **Cost guard.** Only fan out when a single prompt would exceed the budget; cap
  `max_chunks` (default ~8) and fall back to truncation beyond it. Opt-in via a flag
  (e.g. `memory-mcp process --chunk-extraction`), default off.

### Caveats
- N× LLM cost/latency for chunked segments (bounded by `max_chunks`).
- Cross-chunk lessons get split — a candidate's `evidence_event_ids` only reference
  within-chunk events. Acceptable for v1; the synthesis reduce would fix it.
- Chunks are plain extractor CLI calls (each already covered by the
  `MEMORY_MCP_DISABLE_CAPTURE` recursion guard) — **not** nested Claude Code subagents.
  Keep it that way: simpler, no agent orchestration, shallow by construction.

### Changes
- `pipeline/extractors.py`: add `ChunkingExtractor` + a `pack`/`fits_in_one_prompt`
  helper reusing the existing budget constants; factor the events-packing logic shared
  with `build_extraction_prompt`.
- Wiring: an opt-in `--chunk-extraction` (+ `--max-chunks`) flag on `memory-mcp process`
  that wraps the selected extractor; default off (truncation path unchanged).
- Tests: a large multi-chunk segment yields candidates from multiple chunks; a small
  segment makes exactly one call (no fan-out); cross-chunk duplicates dedupe to one
  memory; `max_chunks` cap falls back to truncation.

### Relationship to M18-3
- **M18-3 (shipped):** truncate to fit — cheap, lossy floor, always on.
- **M18-4 (this):** chunk to fit — lossless, opt-in, costs more. Truncation stays the
  fallback when `max_chunks` is exceeded.
