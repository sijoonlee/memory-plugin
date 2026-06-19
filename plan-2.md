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

## Priority 1 — Improve taxonomy: `user` / `feedback` / `project` / `reference`

### Goal
Give each memory a constrained **type** from a fixed 4-value taxonomy, instead of
the current freeform candidate `category` string. Carry it through to the stored
memory.

- `user` — who the user is (role, expertise, durable preferences)
- `feedback` — how the agent should *work* (corrections, confirmed approaches) + *why*
- `project` — ongoing work / goals / constraints **not derivable from code or git**
- `reference` — pointers to external resources (URLs, docs, dashboards)

### Why this improvement is important
- **Precision forcing-function.** A fixed taxonomy makes the extractor (and any
  reviewer) classify before saving. If a candidate doesn't fit a type cleanly, it's
  usually junk — this raises the store's signal-to-noise, the same way it does for
  Claude Code's own memory.
- **It is the prerequisite for a good catalog (Priority 2).** A typed catalog can be
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
  to `MemoryCreate` (inherited by `MemoryRecord`) in `core/models.py`, plus a DB
  column (mirror how `project` was added in M17 — denormalized column + backfill
  for pre-existing rows; pick a default like `project` or `reference` for legacy
  memories).
- **Candidate side:** introduce the same typed field on `MemoryCandidateCreate`
  (`core/events.py`) — either replace the freeform `category` or keep `category`
  as an optional free sub-label *under* the type.
- **Extractor:** update the extraction prompt (`pipeline/extractors.py`) to classify
  each candidate into exactly one type, with the per-type definitions above and the
  "if it doesn't fit, skip it" instruction.
- **Propagation:** carry the type from candidate → memory on approval
  (`pipeline/workers/candidate_worker.py`).
- **Surface:** add `memory_type` to `_MEMORY_SUMMARY_FIELDS` (`mcp_server/service.py`)
  and the `create` / `search` CLI + MCP tools.

### Files
`core/models.py`, `core/events.py`, `pipeline/extractors.py`,
`pipeline/workers/candidate_worker.py`, `mcp_server/service.py`, `cli.py`, tests.

---

## Priority 2 — Two-layer "memory on demand": catalog `when_useful` → `id`, pull on need

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
  `when_useful` → `id` line, **grouped by `memory_type`** (Priority 1), project-scoped
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
prompt (ties back to Priority 1's extractor work).

### Files
`mcp_server/service.py` (or a new `catalog.py`), `cli.py`, tests.

---

## Priority 3 — Use a hook (harness) to inject the catalog

### Goal
Inject the Priority 2 catalog into the host agent's context **deterministically** via
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
- **It completes the loop.** Priorities 1–2 produce a good, typed, scannable catalog;
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

1. **Priority 1 (taxonomy) first** — it shapes both the catalog grouping (P2) and the
   extractor output; doing it first avoids reworking the catalog later.
2. **Priority 2 (catalog) second** — depends on P1 for typed grouping; pure read-side,
   no formation changes.
3. **Priority 3 (hook) last** — depends on P2's catalog command existing; it's the
   thin delivery layer that makes P1+P2 visible to the agent.

Each step is independently shippable and reversible. P2+P3 touch **only the read
path** — formation, storage, and the extractor are untouched except for P1's typing.

---

## Verification (end-to-end, when built)

- **P1:** `uv run pytest` for schema/extractor/propagation; create a memory of each
  type via CLI + MCP and confirm `memory_type` round-trips and appears in
  `memory_list` / `memory_get`.
- **P2:** `uv run memory-mcp catalog --project <repo>` prints typed `when_useful`→`id`
  lines, project-scoped (repo + globals), bounded by `--limit`; tests assert grouping,
  scoping, and that ids resolve via `memory_get`.
- **P3:** install the plugin, start a Claude Code session in a repo with memories, and
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
