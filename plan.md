# Memory MCP Plan

## Goal

Build a small local Memory MCP server that gives agents useful retrieval over past experience, plus a processing pipeline that learns which memories are actually useful over time.

The product should stay narrow:

- Store compact memories about prior work.
- Retrieve relevant memories for the current task.
- Track whether retrieved memories were used or helped.
- Adjust memory scores during local processing.
- Expose retrieval through MCP tools so any agent can use it.

This is intentionally not a full agent platform.

## Core Components

```text
Agent / Client
  -> MCP Memory Server
      -> Memory service
      -> LanceDB vector store
      -> Local embedding model
      -> SQLite metadata store, optional but recommended

Hook / Event Logs
  -> Processing Pipeline
      -> usage classifier
      -> score updater
      -> dedupe / consolidation worker
      -> pruning / decay worker
```

## Package Layout

```text
src/memory_mcp/
  core/
    models.py
    content.py
    embeddings.py
    store.py
    events.py

  mcp_server/
    server.py
    service.py

  review/
    service.py
    server.py
    static/
    cli.py

  hooks/
    cli.py
    examples/

  pipeline/
    extractors.py
    scoring.py
    workers/
      event_worker.py
      session_worker.py
      candidate_worker.py
      decay_worker.py
```

Runtime surfaces should stay separate:

- `hooks` catches moments and appends normalized events.
- `pipeline` contains reusable workers used by `memory-mcp process`.
- `mcp_server` exposes memory tools to agents.
- `review` exposes local human review for memory candidates.
- `core` owns shared storage, models, embeddings, and event schema.

## Memory Model

A memory should be a compact reusable lesson, not a raw transcript.

User-facing shape:

```json
{
  "what_happened": "A previous task failed because the agent edited generated SDK files directly instead of updating the OpenAPI source.",
  "when_useful": "Use this when working on SDK or API schema changes where generated code exists.",
  "helpful_explanation": "Check whether files are generated before editing. Prefer changing the source schema and regenerating clients so the generated output remains reproducible."
}
```

Internal stored shape:

```json
{
  "id": "mem_01H...",
  "what_happened": "...",
  "when_useful": "...",
  "helpful_explanation": "...",
  "content_for_embedding": "...",
  "tags": ["sdk", "generated-code", "openapi"],
  "source": {
    "kind": "hook_event",
    "session_id": "...",
    "message_id": "...",
    "task_id": "..."
  },
  "created_at": "2026-06-13T00:00:00Z",
  "updated_at": "2026-06-13T00:00:00Z",
  "last_retrieved_at": null,
  "last_used_at": null,
  "retrieval_count": 0,
  "use_count": 0,
  "positive_feedback_count": 0,
  "negative_feedback_count": 0,
  "score": 0.5,
  "confidence": 0.7,
  "status": "active"
}
```

`content_for_embedding` should combine the three main fields:

```text
What happened: ...
When useful: ...
Helpful explanation: ...
Tags: ...
```

## Storage

Use LanceDB for vector search because it is simple, local, and file-based.

Recommended layout:

```text
.memory-mcp/
  lancedb/
  memory.sqlite
  events.sqlite
  config.toml
```

LanceDB stores:

- memory id
- embedding vector
- embedding text
- searchable fields needed for ranking

SQLite stores:

- full memory records
- event log
- score history
- processing pipeline checkpoints
- source/provenance metadata

SQLite is optional for a prototype, but recommended because metadata updates, audit history, and event processing are easier there than in a vector table alone.

## Embeddings

Use a small local text embedding model.

Good default:

```text
sentence-transformers/all-MiniLM-L6-v2
```

Alternative if wanting stronger retrieval but still local:

```text
BAAI/bge-small-en-v1.5
```

Embedding interface:

```python
class Embedder:
    def embed_text(self, text: str) -> list[float]:
        ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        ...
```

Keep the model behind a small interface so it can later be replaced by another local model or a hosted embedding provider.

## MCP Tools

Start with four tools.

### `memory_search`

Retrieve relevant memories for the current task.

Input:

```json
{
  "query": "I need to update SDK behavior generated from OpenAPI",
  "limit": 5,
  "tags": ["sdk"],
  "min_score": 0.0
}
```

Output:

```json
{
  "memories": [
    {
      "id": "mem_...",
      "what_happened": "...",
      "when_useful": "...",
      "helpful_explanation": "...",
      "score": 0.82,
      "confidence": 0.7,
      "retrieval_reason": "Matched generated-code and OpenAPI terms."
    }
  ]
}
```

### `memory_get`

Fetch one full memory by id.

Input:

```json
{
  "memory_id": "mem_..."
}
```

### `memory_create`

Create a memory explicitly.

Input:

```json
{
  "what_happened": "...",
  "when_useful": "...",
  "helpful_explanation": "...",
  "tags": ["..."],
  "source": {
    "kind": "manual"
  }
}
```

### `memory_feedback`

Record feedback from an agent, hook, or user.

Input:

```json
{
  "memory_id": "mem_...",
  "signal": "used",
  "weight": 1.0,
  "context": {
    "session_id": "...",
    "run_id": "...",
    "reason": "The agent followed this memory in the implementation."
  }
}
```

Supported signals:

```text
retrieved
used
helpful
not_helpful
incorrect
stale
contradicted
```

## Retrieval Ranking

Do not rank only by vector similarity. Useful memory needs both relevance and proven utility.

Initial ranking:

```text
final_score =
  semantic_similarity * 0.55
  + memory_score * 0.25
  + recency_score * 0.10
  + confidence * 0.10
```

Apply penalties:

```text
- stale penalty
- contradicted penalty
- low-confidence penalty
- repeated-near-duplicate penalty
```

Track retrieval separately from usage. Retrieval alone is weak evidence.

## Feedback Processing Pipeline

The processing pipeline runs outside the MCP request path.

Responsibilities:

- Consume hook events and MCP feedback events.
- Infer which retrieved memories were actually used.
- Update memory scores.
- Apply time decay.
- Detect duplicate memories.
- Detect contradicted or stale memories.
- Propose consolidated memories from repeated patterns.

Processing pipeline loop:

```text
1. Read unprocessed events.
2. Group events by session/run/task.
3. Identify retrieved memories.
4. Identify usage signals.
5. Update memory score and counters.
6. Mark processed checkpoint.
7. Periodically run dedupe/prune/consolidation jobs.
```

Initial scoring rules:

```text
retrieved: +0.01
used: +0.10
helpful: +0.25
not_helpful: -0.20
incorrect: -0.50
stale: -0.30
contradicted: -0.60
```

Clamp score:

```text
0.0 <= score <= 1.0
```

Add decay:

```text
score = score * 0.995 per day without use
```

Use explicit events before trying to infer too much. A transparent heuristic is better than a mysterious learned scorer at the start.

## Hook Events

Hooks should write structured events.

Examples:

```json
{
  "event_type": "memory_retrieved",
  "session_id": "...",
  "run_id": "...",
  "memory_ids": ["mem_..."],
  "query": "...",
  "timestamp": "..."
}
```

```json
{
  "event_type": "agent_response_finalized",
  "session_id": "...",
  "run_id": "...",
  "content": "...",
  "timestamp": "..."
}
```

```json
{
  "event_type": "user_feedback",
  "session_id": "...",
  "run_id": "...",
  "rating": "positive",
  "comment": "That was the right fix.",
  "timestamp": "..."
}
```

## Critical Additions

### Provenance

Every memory needs source metadata. Without provenance, bad memories become hard to debug.

Store:

- source kind
- session/run/message ids
- original text or reference
- creator: user, agent, processing pipeline, import
- creation reason

### Memory Quality Gates

Do not store every observation. Store only reusable lessons.

Reject memory candidates that are:

- too specific to one transient task
- unsupported by evidence
- duplicates of existing memory
- raw logs without a reusable lesson
- secrets or sensitive data
- vague advice like "be careful"

### Dedupe And Consolidation

Memory systems get noisy quickly. Add dedupe early.

When creating memory:

1. Search similar memories.
2. If similarity is high, update existing memory instead of creating a new one.
3. If several memories describe the same lesson, consolidate them in the processing pipeline.

### Conflict Handling

A new memory may contradict an old memory.

Track statuses:

```text
active
stale
superseded
invalid
rejected
archived
```

A contradicted memory should not disappear silently. Mark it and keep an audit trail.

### Evals

Add a tiny eval harness from day one.

Test cases should check:

- relevant memory is retrieved
- irrelevant memory is not retrieved
- useful memory score increases after positive feedback
- incorrect memory score decreases
- duplicate memory is merged or rejected
- stale memory is penalized

### Observability

Log retrieval and ranking decisions.

For each search, record:

- query
- returned memory ids
- semantic score
- final score
- rank features
- selected filters
- latency

This makes ranking debuggable.

### Privacy And Secret Filtering

Memory should not store secrets by accident.

Add a simple redaction pass before memory creation:

- API keys
- tokens
- private keys
- passwords
- obvious credentials

Also support deleting memories by id.

### Import And Export

Keep the memory store portable.

Support JSONL import/export:

```text
memory export -> memories.jsonl
memory import memories.jsonl
```

This makes the system easy to inspect, migrate, and back up.

## What Must Become Memory

Memory creation should be selective. A memory is a compact reusable lesson that
changes future agent behavior. It is not a transcript, log line, temporary
status, or generic reminder.

Create a memory when at least one of these is true:

- The user explicitly says to remember something.
- The user corrects the agent and the correction is likely to apply again.
- A task reveals a durable project convention, such as the right test command,
  build command, generated-file workflow, deployment path, or ownership rule.
- A failed attempt reveals a reusable pitfall, especially one that caused wasted
  time or incorrect edits.
- A successful resolution reveals a reusable fix pattern for this project or
  stack.
- The same issue, correction, or workflow appears in multiple sessions.
- Retrieved memory is explicitly marked helpful, incorrect, stale, or
  contradicted, and the feedback teaches a reusable update.

Do not create a memory from:

- Raw shell output, stack traces, or transcripts without a reusable lesson.
- One-off temporary state, such as "CI is currently failing today".
- Secrets, credentials, private keys, tokens, customer data, or unnecessary
  personal data.
- Vague advice, such as "be careful" or "check thoroughly".
- Facts that are likely to expire quickly unless they include an expiration or
  stale condition.
- Implementation details that are already obvious from checked-in docs,
  `AGENTS.md`, or source code and do not add a learned constraint.

Required fields for every pipeline-created memory:

```json
{
  "what_happened": "...",
  "when_useful": "...",
  "helpful_explanation": "...",
  "tags": ["..."],
  "source": {
    "kind": "pipeline_candidate",
    "session_id": "...",
    "run_id": "...",
    "evidence_event_ids": ["..."],
    "creation_reason": "user_correction | repeated_pattern | failed_attempt | successful_resolution | explicit_remember"
  },
  "confidence": 0.0,
  "status": "active"
}
```

Minimum acceptance rules:

- The memory must be actionable.
- The memory must identify when it is useful.
- The memory must have at least one evidence event.
- Redaction must pass before storage.
- Dedupe must run before storage.
- Confidence must meet the configured threshold, except for explicit
  user-created memories.

## How The Pipeline Catches Memory Candidates

Hooks catch lifecycle moments. The processing pipeline decides whether those moments should
become memory.

Primary event sources:

- Codex hooks: `UserPromptSubmit`, `PostToolUse`, `Stop`, `PreCompact`,
  `PostCompact`, `SessionStart`
- MCP feedback events from `memory_feedback`
- Memory retrieval events from `memory_search`
- Optional filesystem watcher for `$CODEX_HOME/memories/` to import Codex
  built-in generated memory files as external source material
- Optional session transcript reader for finalized local session files

Pipeline capture loop:

```text
1. Ingest new hook, MCP, filesystem, and transcript events.
2. Normalize events into `events.sqlite`.
3. Group events by project, session, run, and task.
4. Build candidate lessons from explicit remember requests, user corrections,
   failed commands followed by fixes, repeated patterns, and final outcomes.
5. Run redaction once the redaction milestone is implemented.
6. Run quality gates.
7. Search similar memories for dedupe or contradiction.
8. Create, update, reject, or mark pending review.
9. Store provenance and evidence event ids.
10. Emit observability records explaining the decision.
```

Recommended V1 processing pipeline behavior:

- Create memories automatically only for explicit remember requests and clear
  user corrections.
- For failed attempts, repeated patterns, and inferred lessons, create
  `pending_review` candidates first.
- Treat Codex built-in memory files under `$CODEX_HOME/memories/` as imported
  evidence, not as authoritative truth.
- Prefer simple rules and inspectable decisions over opaque learned extraction.

Candidate detection rules for V1:

```text
explicit_remember:
  user prompt contains an explicit remember/save preference request

user_correction:
  user says the agent was wrong, gives a corrected command/path/policy, or
  rejects an approach with a durable reason

failed_attempt:
  a tool command fails, a later command or edit fixes the issue, and the final
  answer names the durable fix

successful_resolution:
  final answer contains a repo-specific convention or workflow that was learned
  during the task

repeated_pattern:
  similar candidate appears across N sessions within a configured window
```

Pipeline outputs:

- `memory_created`
- `memory_updated`
- `memory_rejected`
- `memory_pending_review`
- `memory_contradiction_detected`
- `memory_imported_from_codex`

## MVP Milestones

### Milestone 1: Local Memory Store

- LanceDB setup
- local embedding model
- create/search/get memory APIs in Python
- JSONL export for inspection

### Milestone 2: MCP Server

- expose `memory_search`
- expose `memory_get`
- expose `memory_create`
- expose `memory_feedback`
- verify with a simple MCP client

### Milestone 3A: Event Producers And Hooks

- add `events.sqlite`
- add normalized event model
- add event append API / CLI
- write retrieval and feedback events
- write hook events for user prompts, tool results, and turn stops
- add hook-friendly JSON stdin appender
- add Codex hook example config

### Milestone 3B: Event Processing Pipeline

- processing pipeline consumes unprocessed events
- update score counters from `memory_feedback`
- apply weak score signal from `memory_retrieved`
- add daily decay
- mark events processed or failed
- expose the pipeline through `memory-mcp process` and `memory-mcp status`
- later: processing pipeline proposes memory candidates from explicit remember requests and user corrections

### Milestone 4: Quality Controls

#### Milestone 4A: Memory Status And Feedback Semantics

- formalize status transitions: `active`, `stale`, `superseded`, `invalid`, `rejected`, `archived`
- define status meanings:
  - `active`: normal searchable memory
  - `stale`: likely outdated, but no clear replacement is known yet
  - `superseded`: replaced by a newer memory or rule; keep for audit but exclude from normal search
  - `invalid`: previously active memory was later found to be wrong
  - `rejected`: candidate was reviewed and should not become an active memory
  - `archived`: intentionally hidden or retired for cleanup, policy, or manual organization
- keep feedback signals separate from memory statuses:
  - `not_helpful`: keep `active`, lower score
  - `stale`: set status to `stale`
  - `contradicted`: set status to `superseded` if a replacement memory is known, otherwise `stale`
  - `incorrect`: set status to `invalid`
- make `incorrect`, `stale`, and `contradicted` feedback update status consistently through this mapping
- exclude non-active memories from normal search results
- add CLI/MCP support for fetching archived or stale memories by id for audit
- add tests for each feedback-driven status transition

#### Milestone 4B: Dedupe On Memory Creation

- build candidate retrieval text before memory creation
- search similar existing memories before create
- classify matches as duplicate, possible duplicate, or new memory
- for clear duplicates, return or update the existing memory instead of creating a new one
- for possible duplicates, reject with explanation for V1; later route to `pending_review` after 4C exists
- record dedupe decision metadata for audit
- add tests for duplicate, near-duplicate, and distinct-memory cases

#### Milestone 4C: Sessionization And Candidate Queue

- [x] split processing pipeline responsibilities into workers with different schedules:
  - event worker: fast polling for `memory_feedback` and `memory_retrieved`
  - session worker: slower polling to identify idle sessions
  - candidate worker: delayed candidate extraction after session idle
  - decay worker: daily score decay
- [x] add session tracking derived from events:
  - project
  - session id
  - first event time
  - last event time
  - status: `open`, `idle`, `processed`, `skipped`, `failed`
- [x] only process candidate extraction after an idle threshold, for example no new events for 10 minutes
- [x] add a pending candidate table separate from active memories
- [x] store candidate fields, evidence event ids, confidence, creation reason, status, and rejection reason
- [x] add review service/UI commands to list, inspect, approve, reject, or retry pending candidates
- [x] approving a candidate runs the same dedupe-on-create path
- [x] rejecting a candidate stores the rejection reason
- [x] add tests for session idle detection and candidate lifecycle

Note: the first 4C implementation derives segments by scanning all events. That is
acceptable for the MVP data model. A later scale milestone replaces full-table
session refresh with incremental CDC-style sessionization.

#### Milestone 4D: LLM Memory Candidate Extractor

- [x] add an extractor interface so the processing pipeline can use a fake extractor in tests and a Codex CLI extractor in runtime
- [x] use Codex CLI for the MVP extractor instead of an SDK integration
- [x] implement `CodexCliExtractor` as a narrow adapter around `codex exec`
- [x] use `codex exec` non-interactively:
  - pass the extraction prompt through stdin, or use `-` as the prompt argument
  - run with `--ephemeral` so extraction sessions are not persisted as normal user sessions
  - run with `--sandbox read-only` because extraction should inspect provided logs only
  - use `--cd <project>` when project context is useful
  - use `--output-schema <schema-file>` to request strict structured output
  - use `--output-last-message <file>` so the processing pipeline can parse the final JSON from a file
  - optionally use `--json` only for processing pipeline diagnostics; do not parse human text logs as the contract
- [x] keep the CLI command construction isolated so a later SDK extractor can replace it without changing processing pipeline flow
- [x] add timeout, exit-code, stderr, and invalid-JSON handling around the Codex CLI process
- [x] run extraction only for idle sessions from 4C, never for active sessions
- [x] extract memory candidates directly from session events; do not persist generic summaries unless candidates exist
- [x] define memory categories:
  - `clue_location`: where the useful code/config/document clue was found after search
  - `external_context`: human-provided context that filled a knowledge gap
  - `user_correction`: durable correction to an agent assumption or behavior
  - `durable_workflow`: project-specific command, workflow, or convention
  - `repeated_pitfall`: mistake or trap likely to recur
- [x] require structured output:
  - situation
  - lesson
  - action
  - category
  - confidence
  - evidence event ids
  - evidence summary
  - no-memory reason when no candidate exists
- [x] store extracted lessons as pending candidates, not active memories by default
- [x] MVP rule: pipeline/LLM-generated candidates require explicit human approval before becoming active memories
- [x] skip candidate creation when the session has no reusable lesson
- [x] add fixture-based tests for should-create and should-not-create cases

### Milestone 5A: Candidate Review Service And Basic Local UI

Status: complete.

- add a `CandidateReviewService` so CLI and UI share review logic instead of duplicating store calls
- provide a basic local-only human review surface for pending memory candidates
- bind any HTTP UI to `127.0.0.1` by default
- show candidate fields:
  - situation
  - lesson
  - action
  - category
  - confidence
  - evidence summary
  - evidence event ids
  - source session/segment
  - dedupe match or possible duplicate metadata
- support actions:
  - approve
  - edit then approve
  - reject with reason
  - retry extraction for a failed/skipped session
- approval runs the same dedupe-on-create path before creating active memory
- rejection preserves audit metadata and reason
- show referenced evidence events by default, not the full session segment
- make raw event payloads expandable or opt-in
- skip display redaction for this local-only MVP; redaction remains in the dedicated later milestone
- filter by project, status, category, confidence, and created date
- start with CLI review commands from 4C; build dashboard when candidate volume makes CLI review painful

### Milestone 6: Evals

Status: complete.

- [x] retrieval relevance tests
- [x] score update tests
- [x] dedupe tests
- [x] MCP tool contract tests
- [x] LLM candidate extraction evals for good memory vs no-memory sessions
- [x] candidate approval/rejection workflow tests

### Milestone 7: Operator Workflow CLI

Status: complete.

- [x] add a higher-level operator UX so normal use does not require low-level pipeline/hook commands
- [x] keep existing low-level commands for debugging and tests
- [x] add `memory-mcp status` summary that shows:
  - event backlog
  - failed events
  - open/idle/failed session segments
  - pending/rejected/approved candidates
  - active/stale/invalid memory counts
- [x] add `memory-mcp process` for the normal MVP pipeline:
  - process pending feedback/retrieval events
  - refresh/sessionize captured events
  - run candidate extraction for idle segments
  - print one concise JSON or table summary
- [x] add `memory-mcp review` to start the local review UI using `memory-mcp-review serve`
- [x] expose safe defaults:
  - `--root .memory-mcp`
  - local review bind `127.0.0.1`
  - extraction limit default low enough for manual review
- [x] document the recommended daily workflow:
  - `uv run memory-mcp status`
  - `uv run memory-mcp process`
  - `uv run memory-mcp review`
- [x] add tests for status aggregation and process orchestration with a fake extractor

### Milestone 8: Agent-Agnostic Adapter Layer And Claude Code Support

Status: complete.

- [x] make Memory MCP agent-agnostic by treating each agent/runtime as an adapter (`src/memory_mcp/adapters/`)
- [x] define a normalized event contract that all adapters write into `events.sqlite`:
  - event type
  - source adapter
  - project
  - session id
  - run id
  - payload
  - created time
- [x] keep the existing `memory-mcp-event append` CLI as the stable ingestion boundary
- [x] move Codex-specific assumptions into a Codex adapter package or examples directory (`adapters/codex.py`, `hooks/examples/codex-hooks.json`)
- [x] add Claude Code support as a first non-Codex adapter:
  - [x] document Claude Code MCP server registration for `memory-mcp-server`
  - [x] document Claude Code hook/event collection setup when available
  - [x] map Claude Code lifecycle payloads into the normalized event model (`adapters/claude.py`)
  - [x] preserve project/session identifiers when Claude Code exposes them
  - [x] use explicit fallback identifiers when it does not (project-scoped `<source>:<project-name>`)
- [x] keep operator workflow unchanged across agents:
  - `uv run memory-mcp status`
  - `uv run memory-mcp process`
  - `uv run memory-mcp review`
- [x] add adapter docs:
  - [x] Codex setup
  - [x] Claude Code setup
  - [x] generic MCP client setup
- [x] add fixture tests for adapter payload normalization (`tests/test_adapters.py`):
  - [x] Codex hook payload -> normalized event
  - [x] Claude Code payload -> normalized event
  - [x] missing session id fallback behavior
- [x] avoid embedding agent-specific behavior in core storage, review, or retrieval logic

### Milestone 9: Codex Plugin Packaging

Status: complete.

- [x] create a plugin wrapper so Memory MCP can be installed and used from Codex as a local plugin
- [x] include `.codex-plugin/plugin.json` with validated plugin metadata
- [x] include `.mcp.json` that registers the `memory-mcp` MCP stdio server:
  - command: `./install-commands/scripts/memory-mcp-server.sh`
  - wrapper resolves the plugin/project root and runs `uv --directory <project-root> run memory-mcp-server`
  - env: `MEMORY_MCP_ROOT=.memory-mcp`
- [x] include Codex hook configuration for event collection:
  - `UserPromptSubmit` -> `memory-mcp-event append --quiet --event-type user_prompt`
  - `PostToolUse` -> `memory-mcp-event append --quiet --event-type tool_result`
  - `Stop` -> `memory-mcp-event append --quiet --event-type turn_stop`
- [x] include a Memory MCP skill that teaches Codex:
  - search memory when prior project context may help
  - create explicit memories only for durable reusable lessons
  - call `memory_feedback` only when a memory was actually used, helpful, stale, incorrect, or contradicted
  - use `memory-mcp status`, `memory-mcp process`, and `memory-mcp review` for the operator workflow
- [x] include setup/status helper scripts:
  - install/register the local Codex plugin
  - warm the local embedding model
  - stage Codex hooks for review
  - print current memory status
- [x] validate plugin manifest with plugin tooling
- [x] document install/update flow for local development
- [x] keep core app usable without the plugin wrapper

Note: the plugin packages Codex hook configuration under `hooks/codex-hooks.json`
as opt-in setup material. It is intentionally not referenced from
`.codex-plugin/plugin.json`, because hook activation runs commands automatically
on Codex lifecycle events.

### Milestone 10: Claude Code Plugin Packaging

Status: complete.

- [x] create a Claude Code packaging layer after the agent adapter boundary exists
- [x] document Claude Code MCP installation for `memory-mcp-server`
- [x] provide Claude Code configuration snippets for:
  - [x] MCP server registration
  - [x] memory store root selection
  - [x] project-scoped setup
- [x] provide Claude Code event capture setup when supported by Claude Code:
  - [x] map lifecycle events to `memory-mcp-event append`
  - [x] preserve project/session/run identifiers when available
  - [x] fall back to stable source/project identifiers when session metadata is unavailable
- [x] provide Claude-facing instructions comparable to the Codex Memory MCP skill:
  - [x] when to search memory
  - [x] when to create explicit memory
  - [x] when to send feedback
  - [x] how to use `memory-mcp status/process/review`
- [x] include setup/status helper scripts or docs specific to Claude Code usage
- [x] add fixture tests for Claude Code package/config generation where practical
- [x] keep Claude Code packaging separate from Codex plugin packaging

Note: the Claude Memory MCP skill is shared with Codex at `skills/memory-mcp/SKILL.md`
because the guidance is agent-agnostic. Unlike Codex, which stages a standalone
`.codex/hooks.json`, Claude Code reads hooks from `.claude/settings.json`, so
`setup-claude-plugin.sh` merges the packaged hook events into that file (resolving
`${CLAUDE_PLUGIN_ROOT}` to the repo path) and `uninstall-claude-plugin.sh` removes
only the Memory MCP entries. Hooks are intentionally not referenced from
`.claude-plugin/plugin.json`, so installing the plugin never auto-runs commands.

### Milestone 11: Redaction And Secret Safety (done)

Shipped a simplified, best-effort version focused on two chokepoints that cover
every write path into the store. Done:

- added core redaction module (`core/redaction.py`) for text and nested
  JSON-like payloads, combining key-based redaction (sensitive dict keys) with
  pattern-based redaction (PEM private key blocks, bearer tokens,
  OpenAI/GitHub/AWS/Slack token shapes, inline `key=value` secrets); redacted
  spans become `[REDACTED]`
- redact event payloads in `EventStore.append_event` before writing
  `events.sqlite` (covers hook/event ingestion and everything the pipeline
  derives from those events, including candidates)
- redact `memory_create` fields in `LocalMemoryStore.create_memory` before
  embedding or storage (covers the direct MCP/CLI path and approved candidates,
  since approval routes through `create_memory`)
- added tests for API keys, bearer tokens, private keys, password fields, and
  nested payloads (`tests/test_redaction.py`)

Deliberately deferred to keep the milestone simple:

- rejecting high-risk candidates (private key blocks, empty-after-redaction)
- storing redaction metadata in memory source / event payload metadata
- deleting memories by id (moved to Milestone 15)

### Milestone 12: Incremental Event CDC For Sessionization (done)

Make `SessionWorker.run_once` cost O(new events since last run) instead of
O(all events ever), while keeping `session_segments` as the single source of
truth and staying crash-safe across short-lived `memory-mcp process` runs.

Design decisions settled during planning:

- **Reuse `session_segments`, do not add a separate aggregate table.** The
  segment rows already persist the aggregate the plan wants maintained
  (`event_count`, `first_event_at`, `last_event_at`, `segment_index`, `status`).
  The "current open segment" is found with an indexed lookup
  (`ORDER BY segment_index DESC LIMIT 1`) and cached in a per-run dict; the only
  new persistent state is the checkpoint cursor.
- **Cursor, not per-row flags.** Track a single high-water mark
  `(last_event_created_at, last_event_id)` for the `session_worker`; read only
  events strictly after it. The event id is the tie-breaker for equal
  timestamps. This is separate from the `events.processed_at` flags the
  EventWorker uses.
- **Checkpoint lives on `events.sqlite`.** Add a `checkpoints` table to the
  EventStore so the cursor advance commits in the same transaction as the
  `session_segments` writes (cross-database transactions are not available).
  Leave the existing `memory.sqlite` decay checkpoint where it is; factor the
  get/set logic into one shared helper.
- **Crash-safety via atomic batch.** Segment writes plus the cursor advance must
  commit in one transaction so an interrupted run replays cleanly with no
  double-counting. This requires a run-scoped connection/transaction in
  `EventStore` (currently one auto-committing connection per method).

Two ratified behavior changes versus the full-scan version:

- late events arriving after a segment is already terminal open a *new* segment
  instead of being silently dropped (improvement)
- incremental refresh assumes a session's events arrive time-ordered; anomalies
  are repaired with the explicit rebuild command (the full scan was naturally
  order-independent)

#### Milestone 12A: Schema And Checkpoint Plumbing (done)

- add a `checkpoints` table to `EventStore` (`name`, `value`, `updated_at`) plus
  `get_checkpoint` / `set_checkpoint(name, value, *, conn=None)`
- factor checkpoint get/set into a shared helper reused by `LocalMemoryStore`
  and `EventStore`
- add index `idx_events_created_id` on `events(created_at, id)` for the cursor
  scan, and `idx_session_segments_session` on
  `session_segments(project, session_id, segment_index)` for the latest-segment
  lookup
- add a run-scoped `EventStore.transaction()` context manager and make the
  touched writers accept an optional `conn=`

#### Milestone 12B: Incremental Read Path (done)

- add `list_events_after(created_at, event_id, *, limit)` using
  `WHERE created_at > ? OR (created_at = ? AND id > ?) ORDER BY created_at, id`
- add `get_latest_segment_for_session(project, session_id)`
- keep the existing full-scan `_build_segments` / `_make_segment` as the backfill
  implementation and as the parity-test oracle
- add a parity test: incremental output equals the full rebuild for a stream

#### Milestone 12C: Incremental `run_once` (done)

- rewrite `run_once` to walk events after the cursor in batches, seeding a
  per-run `dict[(project, session_id) -> open segment]` lazily from the DB,
  extending or splitting in memory, and flushing segments plus the cursor in one
  transaction per sub-batch
- split when the new-event gap exceeds `max_segment_gap`, when the predecessor
  segment is terminal, or when the session is new
- on first run with no checkpoint, auto-backfill via the full-scan path and set
  the cursor to the high-water mark (seamless migration for existing stores)

#### Milestone 12D: Idle Marking Without Payload Scan (done)

Folded into 12C because `run_once` cannot be correct without it (quiescent
sessions would never be marked idle, so extraction would never fire).

- added `mark_open_segments_idle(threshold)` as a targeted
  `UPDATE session_segments SET status='idle' WHERE status='open' AND last_event_at <= ?`
  using the existing `(status, last_event_at)` index
- run after the cursor walk; the flipped count feeds `SessionWorkerResult`

#### Milestone 12E: Explicit Rebuild/Repair Command (done)

- added a `rebuild-sessions` operator/CLI verb (`OperatorWorkflow.rebuild_sessions`
  / `SessionWorker.rebuild`) that clears non-terminal segments via
  `EventStore.delete_non_terminal_session_segments`, replays through the
  full-scan builder, and resets the cursor to the high-water mark
- keeps already-extracted terminal segments for audit; it is the escape hatch
  for out-of-order events and the migration/repair path

#### Milestone 12F: Tests (done)

- the second refresh reads only new events (`scanned_events == 0`) —
  `test_session_worker_cdc.py`
- same-timestamp events are handled by the event id tie-breaker —
  `test_event_cdc_read.py`
- long gaps create new segments incrementally — `test_session_worker_cdc.py`
- idle marking depends only on segment rows, not event payloads —
  `test_session_rebuild.py`
- resume idempotency: interrupt before the cursor commit, replay, no
  double-count — `test_session_worker_cdc.py`
- a late event after a terminal segment opens a new segment —
  `test_session_worker_cdc.py`
- parity: incremental output equals the full backfill for a stream —
  `test_session_worker_cdc.py`

### Milestone 13: Candidate Merge Workflow (done)

Scope is the durable merge core: the data model, the human-gated manual merge,
and the LLM-assisted merge agent. The rich dashboard UX (batch review UI, richer
filters/sorting, prominent dedupe surfacing) is intentionally out of scope for
M13.

#### Milestone 13A: Merge Data Model And Manual Merge (done)

- added `CandidateWorker.merge_candidates(source_ids, merged)` (placed in the
  worker, not `EventStore`, to match the existing approve/reject/retry
  state-machine layer the review service already calls) that creates one new
  `pending_review` candidate and marks each source `merged` with
  `merged_into_candidate_id`
- the merged candidate preserves:
  - the union of `evidence_event_ids`
  - the set of source `source_session_segment_id`s (in `metadata.merged_from`)
  - the source candidate ids (in `metadata.merged_from`)
- merged candidates stay editable before approval (a normal `pending_review` row)
- added an `archived` candidate status (extends the `CandidateStatus` literal and
  `operator.CANDIDATE_STATUSES`); `CandidateWorker.archive_candidate` hides a
  candidate from the queue while retaining it for audit
- tests (`tests/test_candidate_merge.py`): provenance preserved, sources become
  `merged`, merged candidate is editable, merge needs two distinct pending
  sources, archive hides without deleting

#### Milestone 13B: Review Service Merge And Archive Actions (done)

- exposed merge and archive through `CandidateReviewService`
  (`merge_candidates`, `archive_candidate`) and the HTTP API
  (`POST /api/candidates/merge`, `POST /api/candidates/{id}/archive`)
- all actions stay human-driven; approving a merged candidate runs the normal
  memory creation path (including dedupe), unchanged
- fixed a latent bug in `review/server.py` `_handle_errors`: it called `_json`
  with a positional status code, so every error response (400/422) would have
  raised `TypeError`; now passes `status_code=` (covered by the merge validation
  test)
- tests (`tests/test_review_merge.py`): merge endpoint creates a `pending_review`
  candidate and drops the sources from the queue, merge validation errors return
  400, archive endpoint hides the candidate and lists it under `archived`

#### Milestone 13C: LLM-Assisted Merge Proposals (done)

- reused the extractor abstraction in `pipeline/extractors.py`: added
  `MergeProposalResult` schema, the `MergeProposer` protocol, `StaticMergeProposer`,
  and `CodexCliMergeProposer` / `ClaudeCliMergeProposer` running the same
  non-interactive CLI + JSON schema pattern; factored shared subprocess plumbing
  (`_run_cli_subprocess`, `_run_codex_exec`, `_run_claude_print`,
  `_parse_structured`) so extraction and merge proposal share one runner
- `MergeProposalWorker` deterministically pre-clusters pending candidates
  (single-linkage by category match + lexical Jaccard overlap), asks the proposer
  per cluster, and on `should_merge` calls the 13A `merge_candidates` primitive to
  produce a new `pending_review` candidate
- safety model resolved: the agent never creates active memories (a human still
  approves the merged candidate, which runs the normal dedupe path); sources are
  marked `merged` (reversible via `retry_candidate`), not approved
- tests (`tests/test_merge_proposal.py`): the agent creates no active memory and
  emits a `pending_review` proposal, declines when the proposer says no (sources
  untouched), dissimilar candidates do not cluster, and clustering respects
  category boundaries

### Milestone 14: Shared Online Memory Server (BE + FE)

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

### Milestone 15: Delete Memory By Id (done)

- added `LocalMemoryStore.delete_memory(memory_id)` removing the SQLite
  `memories` row and the LanceDB vector row; returns `True` on delete, `False`
  for an unknown id
- exposed via the CLI (`memory-mcp delete <id>`, exits non-zero when unknown)
  and a `memory_delete` MCP tool (`mcp_server/service.py` + `server.py`)
- semantics documented: hard delete is permanent and bypasses the `invalid` /
  `stale` / `superseded` audit states, so it is reserved for secret removal and
  explicit user requests; `record_feedback` remains the normal lifecycle path
- `feedback_events` rows are intentionally left for audit
- tests (`tests/test_delete_memory.py`): removal from both SQLite and the vector
  table (verified via search), unknown id is a safe no-op, deleting one memory
  leaves others intact, and the service wrapper reports the outcome

## Non-Goals For V1

- Full agent runtime
- Chat UI
- Multi-user cloud service
- Complex learned ranker
- Automatic memory creation from every message
- Distributed storage
- Heavy workflow orchestration

## Recommended Stack

- Python 3.11+
- MCP Python SDK
- LanceDB
- sentence-transformers
- SQLite
- Pydantic
- Typer CLI
- pytest

## Open Questions

- Should explicit user-created memories bypass processing pipeline approval?
- Should memories be scoped globally, per project, per user, or per agent?
- Should the processing pipeline infer usage from final answers, or only trust explicit `memory_feedback` at first?
- How aggressive should decay be for memories that are rarely used?
- Which processing pipeline candidate types should be auto-created versus marked pending review?
- Should imported Codex built-in memory files be indexed directly or only used as evidence?

## Suggested V1 Defaults

- Scope memories by project.
- Allow explicit `memory_create`, but run redaction before storage and dedupe before create.
- Let the processing pipeline create memories for explicit remember requests and clear user corrections.
- Mark inferred processing pipeline candidates as `pending_review` before they become active.
- Treat `retrieved` as weak signal only.
- Treat explicit `helpful`, `incorrect`, and `contradicted` as strong signals.
- Keep all ranking/scoring rules transparent and inspectable.
