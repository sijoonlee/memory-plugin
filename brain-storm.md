# Memory Systems — Brainstorm

A thinking doc (not a spec) capturing a design discussion comparing **Letta /
MemGPT**, **Claude Code's own file-based memory**, and **memory-mcp**, plus the
directions worth exploring next. Nothing here is decided; it's raw material for
organizing thoughts. Date: 2026-06-18.

Related memories: `memory-formation-coupling-decision`,
`memory-retrieval-semantic-only` (in the project memory dir).

---

## 0. The one idea everything hangs on

**Foreground agents forget to write *and* forget to read memory.** Writing and
retrieving are overhead with no immediate reward while the agent is optimizing to
answer the user. So every durable memory system eventually stops *trusting the
agent to do memory bookkeeping* and moves the work into **scaffolding**:

> **Push, don't pull.** The system pushes memory *in* (formation) and *out*
> (retrieval) so the agent never has to decide to do either.

- memory-mcp pushes memory **in** via the offline extractor (agent doesn't write).
- The unbuilt other half: push memory **out** via injection (agent doesn't search).

Reliability comes from **scaffolding, not from the agent being smart about
memory.**

---

## 1. Three-way comparison

| Dimension | **Letta / MemGPT** | **Claude Code (my memory)** | **memory-mcp** |
|---|---|---|---|
| **Who writes memory** | The agent itself, live, via self-edit tools | The agent (me), in-the-moment, by hand | An **offline extractor** over the event log |
| **Formation trigger** | (a) model judgment in-loop, (b) context-window pressure → summarizer, (c) every-N-turns **sleeptime agent** | My judgment, when something clears the bar | Session goes **idle** → extraction worker (or manual `process`) |
| **Who decides to *read*** | Agent, but core memory is **always in context** (rarely has to decide) | Me, but `MEMORY.md` index is **injected every session** | **The agent must call `memory_search`** (pull-only) ← the gap |
| **Memory tiers** | Core (in-context blocks) + Archival (vector passages) + Recall (history) | Flat files + always-loaded index | Single semantic store + event log |
| **Dedup** | Prompt instruction ("don't write redundant info"); sleeptime rewrites | At write time: update existing, delete wrong | **Pipeline stage** (M13 merge / dedupe-on-create) |
| **Staleness handling** | Prompt: use absolute dates; sleeptime reorganizes | Read-time skepticism ("verify it still exists") | Feedback signals (`stale`/`incorrect`/`contradicted`) + decay |
| **Curation / review** | None — agent edits in place | None — I author directly | **Candidate queue + review UI** before storing |
| **Scope** | Per-agent; archives shareable | Per-project memory dir | Project-scoped, inclusive (M17) |
| **Where the "prompt power" lives** | **System prompt** (owns the agent's identity) | **System prompt** (Anthropic owns it) | **MCP server instructions** (weak, advisory) — does *not* own the host's prompt |

### The decisive asymmetry
Letta and Claude Code work because the prompt that drives memory behavior lives in
the **system prompt** — highest authority, present every turn. **memory-mcp is a
guest** inside someone else's agent (Claude Code, Cursor, …) and can only use weak
channels (MCP `instructions`, tool descriptions, injected context). It **cannot**
write the host's identity. That single fact is *why* the offline extractor is the
right architecture — it's not a workaround, it's the correct response to not
owning the prompt.

---

## 2. How Letta forms memory (detail)

- **Core-memory self-edits** (`functions/function_sets/base.py`):
  `core_memory_append/replace`, `memory_insert/replace/apply_patch`,
  `memory_rethink`, `memory_finish_edits`. Direct string mutation of in-context
  blocks; line-numbered for precision.
- **Archival inserts**: `archival_memory_insert(content, tags)` →
  embedded passage; `archival_memory_search(query, tags, top_k, …)`. Docstring
  tells the model *what* to store: "self-contained facts or summaries, not
  conversational fragments."
- **Summarizer** (`services/summarizer/`): on context-window pressure, evicts +
  compresses oldest messages (partial-evict ~30% default).
- **Sleeptime agent** (`groups/sleeptime_multi_agent_v2.py`): fires when
  `turns_counter % sleeptime_agent_frequency == 0`; a background agent whose only
  job is memory upkeep. **This ≈ memory-mcp's extractor.**

### The loop mechanic (why in-the-moment writes work)
`_decide_continuation` (`agents/letta_agent_v3.py`): **call a tool → loop
continues; call no tool → turn ends.** So a memory edit is just a tool call that
keeps the turn alive — the agent chains several edits, then sends a message to
finish. Tool rules can *force* a tool (terminal / required-before-exit) if you
want hard guarantees.

### What tells it when/what (all prompt, no code logic)
- System prompt (`prompts/system_prompts/memgpt_v2_chat.py`): memory editing
  framed as core to "being sentient."
- `<context_instructions>`: answer directly if context already has the info; only
  use tools when it doesn't. (Primes check-before-store.)
- Sleeptime prompt (`sleeptime_v2.py`) reads like an extractor spec: *"be
  selective… but aim for high recall," "comprehensive, readable, up to date,"
  remove redundant/outdated, use absolute dates, finish-tool if nothing to add.*
- `<memory_metadata>` block (`prompts/prompt_generator.py`): injects counts of
  recall/archival memory + available tags so the agent knows what's retrievable.

---

## 3. Claude Code memory — the principles/rules (transferable discipline)

These are entirely prompt-driven, so they're copyable into memory-mcp's **extractor
prompt** (write side) and **review bar** (curation side).

1. **Forced taxonomy with per-type shape** — `user` / `feedback` / `project` /
   `reference`. A forcing function: if a thought doesn't fit a type, it's usually
   not worth saving. (memory-mcp has freeform `category`; a small fixed taxonomy
   would raise precision.)
2. **Don't save what's derivable** — not code structure, past fixes, git history,
   or CLAUDE.md. *If asked to remember something derivable, distill the
   non-obvious part and save that instead.* ← the single most important rule.
3. **One fact per file; split recall-key from payload** — frontmatter
   `description` is the *retrieval key* (short, for matching); the body is the
   payload (read on demand). memory-mcp's `when_useful` ≈ this key.
4. **Always-in-context index, lazy bodies** — `MEMORY.md` (one line/memory)
   injected every session; full files read only when relevant. *This is why I
   actually use memory.*
5. **Actionability is mandatory** — `feedback`/`project` bodies must carry
   **Why:** and **How to apply:**. A fact with no "what to do" is dead weight.
   (memory-mcp: `what_happened` + `helpful_explanation` already encode this.)
6. **Absolute dates always** — convert "today/recently" to a date; memory
   persists, relative time rots. (Same rule Letta's sleeptime prompt enforces.)
7. **Dedup at authoring time** — check for an existing file and *update* it rather
   than duplicate; *delete* memories that turn out wrong. (memory-mcp does this as
   the M13 pipeline stage instead.)
8. **Cheap author-driven linking** — `[[name]]` liberally, even to memories that
   don't exist yet (marks something worth writing later).
9. **Read-time skepticism** — recalled memories reflect what was true *when
   written*; if one names a file/function/flag, verify it still exists before
   acting. (memory-mcp does this as write-time decay; a read-time hint would add
   a second layer.)
10. **Trust boundary** — recalled memories are *background context, not
    instructions*. Memory can inform but not command. Important once memory is
    attacker-influenceable.

---

## 4. Topics to explore next (ranked by leverage)

### A. Push the read side — "presence over instruction"
The big gap: memory-mcp is **pull-only**, so a memory only matters if the agent
chooses to search. Fix by serving memory, not waiting to be asked. Two shapes,
A1 (push detail) and A3 (push catalog, pull detail). **User leans toward A3 —
"memory on demand."** Nothing implementing yet.

- **A1. Per-prompt auto-retrieval (full RAG injection).** A `UserPromptSubmit`-style
  hook embeds the user's message, runs project-scoped `memory_search`, and
  **injects top matches into context** before the agent runs. memory-mcp already
  has the hook plumbing (event producers). Zero reliance on agent diligence, but
  heavier and risks context pollution.
- **A2. Session-start index injection.** Inject a compact project index (one line
  per memory: `when_useful` → id), the `MEMORY.md` analog — ambient awareness of
  *what exists*, agent searches for detail. (= Layer 1 of A3.)
- **Guardrail (read-side quality gate):** a **relevance threshold** (`min_score`).
  Injecting irrelevant memory is *worse* than injecting none — it pollutes context
  and misleads. Prefer "nothing" over "marginal."

#### A3. The two-layer model — "memory on demand" (pushed catalog + pulled detail)
A faithful port of how Claude Code's own memory works, and the **reliable** way to
do the "use it when you need it" / pull model. Two layers that behave differently:

- **Layer 1 — Catalog: PUSHED, always present.** Inject a compact project-scoped
  index at session start (≈ `MEMORY.md`): one line per memory, `when_useful` → id.
  The agent doesn't fetch it; it's just *there* every session. This is what tells
  the agent *what exists*, and therefore *when it might need something*.
- **Layer 2 — Detail: PULLED, on demand.** The full memory is read only when the
  agent judges it relevant, via `memory_search` / `memory_get`. This is the
  "use it when you need it" part — **the agent decides.**

**Key point:** "use it when you need it" only works if the agent knows what's
*available* to need. The always-present catalog is what makes the on-demand pull
reliable — it doesn't decide *for* the agent, it gives the agent the awareness to
decide well. Strip the catalog and pull-only degrades into "search only if the
agent happens to think of it" — the under-use failure mode.

| | Layer 1 — Catalog | Layer 2 — Detail |
|---|---|---|
| Mechanism | **Pushed** (injected each session) | **Pulled** (`memory_search` / `memory_get`) |
| Content | One line/memory (`when_useful` → id) | Full memory body |
| Who acts | System (automatic) | Agent (on demand) |
| Claude Code analog | `MEMORY.md` index | Reading a memory file |

**A1 vs A3:** A1 pushes the *detail* (full relevant memories) into every prompt —
no agent action, but heavier and pollution-prone. A3 pushes only the *catalog* and
lets the agent pull detail — lighter, respects the on-demand preference, but
depends on the agent acting on the catalog. **Not exclusive:** A2's index = A3's
Layer 1; start with A3 (catalog + pull), and later add A1-style detail injection
for high-relevance hits if pull alone proves too passive.

### B. Timeliness of formation (cheap branch — NOT a stateful agent)
The "double agent" the user imagined is mostly a *trigger + gate* change to the
existing extractor, not a new component.

- **B1. Cadence trigger (cron `process`).** Wall-clock periodic runs. Middle ground
  between manual and real-time. Safe because `process` is incremental (M12 CDC),
  so periodic runs are idempotent. User intends this *later*, after organizing.
- **B2. Cheap-model gate.** A tiny/cheap pass decides "is anything memorable here?"
  before the expensive distillation fires. Most segments are junk; gating kills
  most of the cost. (Mirrors Letta's "not every observation warrants an edit" and
  the extractor's `no_memory_reason`.)
- **B3. Asymmetric models.** Memory distillation is easier than the primary task —
  run it on a cheaper model (`process --model … --effort …` already supports this).

### C. Real-time / intra-session (only if genuinely needed)
- **Key insight:** real-time *writing is inert without real-time re-injection.* A
  freshly-written memory won't reach the running agent unless something re-injects
  it (see A1/A2). So speeding up writes alone only helps the *next* session — for
  which idle-triggering was already fine.
- **Real-time for whom?** Next session → idle is fine, don't bother. Current
  session (user corrects early, want it to stick) → needs *both halves*: prompt
  write **and** re-injection. This is the narrow high-signal case.
- **Quality cost:** idle-triggering waits for the **whole arc**; eager extraction
  risks capturing a "lesson" the user contradicts later. Real-time trades
  correctness for freshness.

### D. Smarter (stateful) memory — expensive, defer
Only if cross-session reasoning is needed: cross-segment **consolidation pass**
(noticing patterns across many sessions), reorganization/rethink, multi-step
memory decisions. Prefer a *periodic batch consolidation* over a continuously
reasoning agent — get sleeptime *behavior* without paying for a live agent.

---

## 5. Caveats — the incentive problem & how to encourage use

### Why it's hard (the gradient)
- Writing/reading memory has **no immediate reward** for the foreground agent; it
  fights the task gradient (the agent is rewarded for answering the user *now*).
- **Tool presence ≠ tool use.** A bare `memory_create`/`memory_search` in the tool
  list gets under-used. Proven in this very session: I only wrote a memory when
  the user gave a **direct, high-authority instruction** — the MCP server's
  advisory instructions alone wouldn't have triggered it.
- **You don't own the host's system prompt.** The strong channel (identity-level
  conditioning) is unavailable to a guest MCP server.
- **Everyone who bets on the foreground adds a background net** — Letta's sleeptime,
  memory-mcp's extractor. This convergence is not a coincidence; it's the gradient.

### How to encourage use anyway (ranked by what a guest can actually control)
1. **Presence over instruction** (A1/A2) — inject relevant memory so "using
   memory" isn't a decision. Strongest lever a guest has.
2. **Make any desired write cheap & obviously useful** — fixed format/taxonomy so
   the agent doesn't deliberate; mandatory Why/How so output is plainly valuable.
3. **Use the highest-authority channel you control** — CLAUDE.md / project
   instructions > MCP `instructions` > tool descriptions.
4. **Don't rely on "remember to check your memory" prose** — it's the weakest
   thing and the first to be ignored.

### Two failure modes to guard (one per side)
- **Write side:** premature/contradicted lessons (mitigate: wait for the arc /
  idle-trigger; cheap gate).
- **Read side:** context pollution from injecting marginal memories (mitigate:
  `min_score` threshold; inject nothing over junk).

---

## 6. Recommended reading

### Inside memory-mcp (ground the design in what exists)
- `plan.md` — esp. **Memory Model**, **Feedback Processing Pipeline**, **How The
  Pipeline Catches Memory Candidates**, **What Must Become Memory**, and milestones
  **M3B** (event processing), **M4D** (LLM extractor), **M13** (merge/dedupe),
  **M17** (project scope), **M16** (segment observability).
- `flow.md` — pipeline flow.
- The MCP server `instructions` string (the retrieval-first guidance:
  `memory_search` → `memory_feedback`); contrast how little it says about
  *creating* — that's the decoupling, by design.
- `skills/memory-mcp/claude_behaviour.md` — capture of Claude Code's memory
  behavior (the file already being drafted).
- Existing project memories: `memory-formation-coupling-decision`,
  `memory-retrieval-semantic-only`.

### Inside Letta (`/Users/sijoonlee/Documents/coding/letta`)
- `letta/prompts/system_prompts/memgpt_v2_chat.py` — the base memory-agent prompt.
- `letta/prompts/system_prompts/sleeptime_v2.py` — the background memory-manager
  prompt (reads like an extractor spec).
- `letta/functions/function_sets/base.py` — the memory tools + their docstrings
  (the "what to store" guidance).
- `letta/prompts/prompt_generator.py` — `<memory_metadata>` injection.
- `letta/agents/letta_agent_v3.py` — `_decide_continuation` (the loop mechanic).
- `letta/services/summarizer/` + `letta/groups/sleeptime_multi_agent_v2.py` —
  eviction summarization + cadence-triggered sleeptime agent.

### Concepts worth reading externally (general)
- MemGPT paper (the core/archival/recall memory hierarchy + self-editing).
- Retrieval-augmented generation basics (for the A1 per-prompt injection path) —
  relevance thresholds, top-k, embedding-based recall.

---

## 7. One-line summary

memory-mcp has the **write** side right (decoupled extractor — the correct move
for a guest that doesn't own the prompt). The open frontier is the **read** side.
Preferred shape: the **two-layer "memory on demand" model (A3)** — *push* a compact
catalog/index every session (so the agent knows what exists) and let the agent
*pull* full detail on demand via `memory_search`/`memory_get`. The pushed catalog
is what makes pull-only reliable. Optionally add a cron cadence + cheap gate to
shorten formation lag. Don't reach for a stateful "double agent" unless
cross-session reasoning is actually needed.

---

# Good idea: using the two-layer approach

The concrete plan that came out of the discussion — the "memory on demand" design
for memory-mcp, written down so it's ready to pick up later. **Not implementing
yet.**

## The idea in one sentence
Inject a small **catalog** of `when_useful` → `id` lines every session (push), and
let the agent **fetch full detail by id on demand** (pull). The catalog tells the
agent *what exists* so it knows *when to reach for memory*; the agent still decides
*whether* to pull.

## The retrieval loop
1. **Catalog (Layer 1, pushed):** a block of `when_useful` → `id` lines injected at
   session start.
2. **Agent notices** a `when_useful` line that matches the current task.
3. **Pull (Layer 2):** agent calls **`memory_get(id)`** to read the full memory.

```
<memory-catalog project="/Users/sijoonlee/Documents/coding/memory-mcp">
- mem_8a03d7…  When deciding whether to use Graph RAG for the memory store
- mem_a4f966…  When deciding how memories get formed in memory-mcp
</memory-catalog>
Call memory_get(<id>) to read the full memory when a line looks relevant.
```

## Why this is a good fit
- **`memory_get` becomes the primary path, not `memory_search`.** Seeing the exact
  id means no fuzzy search needed — fetch by id: cheaper, more precise, no
  embedding or `min_score` tuning. `memory_search` stays as the fallback for "no
  matching line, but here's my situation."
- **Catalog = a thin formatter over existing code.** It's `list_memories(project=…)`
  projected to `when_useful` + `id` (both already returned by `memory_list`, which
  now also returns `project`). No new storage, no schema change.
- **Respects the on-demand preference** while fixing pull-only's fragility: the
  always-present catalog is what makes "use it when you need it" reliable.

## Implementation notes (for later)
- **Generator:** thin function/formatter over `list_memories(project=…)` →
  `when_useful` + `id` lines. Project-scoped (M17: repo memories + globals).
- **Injection channel:** a **SessionStart hook** (Claude Code) emitting the catalog
  as context — reuses the existing hook plumbing. Weaker alternatives: MCP server
  `instructions`, CLAUDE.md.
- **Bounding:** small store → inject all; large store → cap to top-N by score.
  `when_useful` is short by design, so lines are cheap.
- **Refresh cadence:** catalog is a session-start snapshot; memories formed
  mid-session appear next session. Consistent with the next-session / on-demand
  model. (Intra-session freshness would need re-injection — out of scope.)

## The one dependency this exposes
**`when_useful` now does double duty** — it's both the embedding cue for
`memory_search` *and* the catalog line the agent scans. Its quality directly
decides whether the agent picks the right memory. → puts more weight on the
**extractor prompt** writing sharp, situation-specific `when_useful` text. Loops
back to the extractor-prompt discipline (§3, §5).

## Honest caveat
It's still **pull**: even with the catalog the agent must (a) notice a relevant
line and (b) choose to call `memory_get`. The catalog makes that *likely* (it's how
Claude Code's own memory works), not *guaranteed* the way A1's full injection would
be. That's the accepted trade for lighter context + agent agency.

## Smallest first step (when ready)
Build the **catalog generator** (formatter over `list_memories`) + a **SessionStart
hook** that injects it. Keep `memory_get`/`memory_search` exactly as-is. That's the
whole MVP of A3 — no changes to formation, storage, or the extractor required.

---

# Hook to inject prompt

Why the **hook** is the right injection channel given memory-mcp is a **guest /
tool** (it can't be the system prompt).

## The core reason: harness-executed, not agent-discretion
A hook is run by the **harness**, deterministically — the agent doesn't choose to
run it, can't skip it, can't deprioritize it. So a hook gives a guest tool the one
property of the system prompt that matters here — **guaranteed presence** — *without
owning the system prompt.*

## Presence, not authority
The catalog needs **presence, not authority** — and that's exactly why the hook
fully closes the "I'm a guest" gap for this design:

- An **instruction** ("always write memory") needs *authority*: it has to override
  the agent's task-focus, which is why it really wants to live in the system
  prompt / identity. Injected via a hook it carries weaker authority, so it leaks.
- A **catalog** is just information the agent scans. It needs no authority — only to
  be *in front of the agent*. Deterministic presence is precisely what a hook gives.

→ A3 (catalog) is a *better fit for a guest* than trying to make the agent reliably
call `memory_create` — the latter would need authority a guest can't get; the
former needs only awareness, which a hook delivers.

## Channel hierarchy for a guest (by reliability)
1. **Hook** — harness-enforced, deterministic injection. *Strongest a guest has.*
   ✅ catalog
2. **MCP server `instructions`** — advisory; agent may ignore.
3. **Tool descriptions** — weakest nudge.

## SessionStart vs UserPromptSubmit
Both can emit added context; pick by freshness vs cost:

| Hook | When | Trade |
|---|---|---|
| **SessionStart** | Once per session | Cheaper, static snapshot. **MVP choice.** |
| **UserPromptSubmit** | Every turn | Can re-inject or **filter the catalog by the current prompt**; fresher, slightly pricier. Upgrade path. |

## Residual (unchanged)
The hook guarantees the catalog is *present*; it does **not** guarantee the agent
*acts* on it (still pull). "Present" was the missing half and the part a guest can
control; "acts on it" is the inherent, accepted trade of the on-demand model.
