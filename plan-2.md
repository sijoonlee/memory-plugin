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

## Milestone 18 — Storage layer: unify the model, then draw the adapter boundary

Two sub-steps. **18-1 unifies the candidate and memory into one model** (a candidate
is a memory with `status="pending_review"`); **18-2 draws the storage-protocol seam**
so that one model can move to a shared backend in M22 while events stay local. 18-1
lands first because it decides *what* the 18-2 protocol wraps. Data does not need to
be migrated — the store has been experimental so far, so the existing `.memory-mcp`
can be recreated from scratch.

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

### Milestone 18-2 — Storage adapter boundary (M22 prep)

#### Goal
After 18-1 there is **one** model and **one** store, so draw a single storage-protocol
seam: a `MemoryStore` protocol that `LocalMemoryStore` satisfies, so the whole
memory + candidate dataset can move to a shared backend in M22 (Postgres/pgvector or
hosted) while **events** stay local in `EventStore`. That swap is only cheap if callers
depend on the *interface*, not on `LocalMemoryStore` concretely — this is that
interface.

#### What changed vs the original adapter plan
18-1 makes a separate `CandidateStore` unnecessary: candidates are `pending_review`
rows in the memory store, so there is **no** `memory_candidates` table to relocate out
of `events.sqlite`. One protocol, not two; no table move.

#### Changes
- **Protocol:** add a `MemoryStore` Protocol (new `core/protocols.py`) matching
  `LocalMemoryStore`'s public surface (`create_memory`, `search_memories`,
  `get_memory`, `list_memories`, `record_feedback`, `delete_memory`, the
  pending/`activate` ops from 18-1, …); assert `LocalMemoryStore` satisfies it
  structurally.
- **Rewire callers** to depend on the protocol, not the concrete class — the few
  construction sites (`cli.py`, `operator.py`, `review/service.py`,
  `mcp_server/server.py`). `EventStore` is untouched (events stay local).
- **Cross-store references stay opaque audit ids.** A pending memory's
  `source.evidence_event_ids` / `source_session_segment_id` point into local event
  data; resolved against `EventStore` only when needed (e.g. project lookup from a
  segment). No cross-database join assumed.

#### Files
`core/protocols.py` (new), `core/store.py` (conform), `cli.py`, `operator.py`,
`review/service.py`, `mcp_server/server.py`, tests.

#### Verification
- `uv run pytest` green; no behavior change to retrieval/scoring/review.
- A test-only in-memory `MemoryStore` fake substitutes through the same callers
  (proves swappability for M22).

> Sequencing: 18-1 → 18-2, both **M22 prep** and independent of the read-side
> taxonomy/catalog/hook work (M19–M21). Land before M22.

---

## Milestone 19 — Improve taxonomy: `user` / `feedback` / `project` / `reference`

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

## Milestone 22: Shared Online Memory Server (BE + FE)

This milestone graduates Memory MCP from a single-user local tool into a shared,
online service used by multiple developers. It is explicitly post-V1 (see
"Multi-user cloud service" under Non-Goals For V1) and should not begin until the
local MVP and adapter/packaging milestones are stable.

Guiding principle: keep all behavior in the service/store layer so the MCP server,
CLI, and a new HTTP backend are thin front doors over the same logic. Do not push
agent- or transport-specific assumptions into core retrieval, scoring, or review.

- backend service:
  - extract a backend API (REST or GraphQL) over the existing service functions
    (`mcp_server/service.py`, `operator.py`) without forking business logic
  - replace file-based SQLite + LanceDB with a concurrent server datastore
    (for example Postgres + pgvector or a hosted vector store)
  - keep a migration/export path from local `.memory-mcp` stores into the server
    (reuse JSONL import/export)
  - make the MCP server connect to the backend (remote MCP over HTTP, or MCP as a
    thin client of the backend API) instead of reading local files directly
- identity, scoping, and access control:
  - add developer/user identity on events, feedback, memories, and candidates
  - extend scoping beyond project to support per-user, per-project, and explicitly
    shared memories
  - add authentication and authorization (who can read, create, approve, delete)
  - preserve provenance and audit trails across users
- multi-writer correctness:
  - replace single-writer file assumptions with concurrent-safe transactions
  - handle concurrent dedupe-on-create and candidate approval without races
- frontend:
  - build a dedicated web UI for search, browse, candidate review, and store health
  - reuse the read-only inspection contracts (`memory_status`, `memory_list`,
    `candidate_list`) as the first read API, and the review service for actions
  - treat the local review UI (Milestone 5A) as throwaway once the FE exists
- operations:
  - deployment, backups, observability, and rate limiting for a shared service
  - privacy controls and tenant isolation; redaction (Milestone 11) becomes mandatory
- compatibility:
  - keep the local single-user mode working for offline/solo use
  - keep the normal operator workflow verbs (`status` / `process` / `review`)
    available against the backend
