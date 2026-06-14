# Memory MCP Plan

## Goal

Build a small local Memory MCP server that gives agents useful retrieval over past experience, plus a daemon that learns which memories are actually useful over time.

The product should stay narrow:

- Store compact memories about prior work.
- Retrieve relevant memories for the current task.
- Track whether retrieved memories were used or helped.
- Adjust memory scores in the background.
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
  -> Learning Daemon
      -> usage classifier
      -> score updater
      -> dedupe / consolidation worker
      -> pruning / decay worker
```

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
- daemon checkpoints
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

## Feedback Loop Daemon

The daemon runs outside the MCP request path.

Responsibilities:

- Consume hook events and MCP feedback events.
- Infer which retrieved memories were actually used.
- Update memory scores.
- Apply time decay.
- Detect duplicate memories.
- Detect contradicted or stale memories.
- Propose consolidated memories from repeated patterns.

Daemon loop:

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
- creator: user, agent, daemon, import
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
3. If several memories describe the same lesson, consolidate them in the daemon.

### Conflict Handling

A new memory may contradict an old memory.

Track statuses:

```text
active
stale
superseded
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

Required fields for every daemon-created memory:

```json
{
  "what_happened": "...",
  "when_useful": "...",
  "helpful_explanation": "...",
  "tags": ["..."],
  "source": {
    "kind": "daemon_candidate",
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

## How The Daemon Catches Memory Candidates

Hooks catch lifecycle moments. The daemon decides whether those moments should
become memory.

Primary event sources:

- Codex hooks: `UserPromptSubmit`, `PostToolUse`, `Stop`, `PreCompact`,
  `PostCompact`, `SessionStart`
- MCP feedback events from `memory_feedback`
- Memory retrieval events from `memory_search`
- Optional filesystem watcher for `$CODEX_HOME/memories/` to import Codex
  built-in generated memory files as external source material
- Optional session transcript reader for finalized local session files

Daemon capture loop:

```text
1. Ingest new hook, MCP, filesystem, and transcript events.
2. Normalize events into `events.sqlite`.
3. Group events by project, session, run, and task.
4. Build candidate lessons from explicit remember requests, user corrections,
   failed commands followed by fixes, repeated patterns, and final outcomes.
5. Run redaction.
6. Run quality gates.
7. Search similar memories for dedupe or contradiction.
8. Create, update, reject, or mark pending review.
9. Store provenance and evidence event ids.
10. Emit observability records explaining the decision.
```

Recommended V1 daemon behavior:

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

Daemon outputs:

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

### Milestone 3: Event Log And Daemon

- write retrieval and feedback events
- write hook events for user prompts, tool results, and turn stops
- daemon consumes unprocessed events
- daemon proposes memory candidates from explicit remember requests and user corrections
- update score counters
- add daily decay

### Milestone 4: Quality Controls

- dedupe on create
- redaction pass
- memory status field
- basic stale/incorrect handling
- pending review status for inferred daemon candidates
- optional watcher for `$CODEX_HOME/memories/` imports

### Milestone 5: Evals

- retrieval relevance tests
- score update tests
- dedupe tests
- MCP tool contract tests

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

- Should explicit user-created memories bypass daemon approval?
- Should memories be scoped globally, per project, per user, or per agent?
- Should the daemon infer usage from final answers, or only trust explicit `memory_feedback` at first?
- How aggressive should decay be for memories that are rarely used?
- Which daemon candidate types should be auto-created versus marked pending review?
- Should imported Codex built-in memory files be indexed directly or only used as evidence?

## Suggested V1 Defaults

- Scope memories by project.
- Allow explicit `memory_create`, but run redaction and dedupe first.
- Let the daemon create memories for explicit remember requests and clear user corrections.
- Mark inferred daemon candidates as `pending_review` before they become active.
- Treat `retrieved` as weak signal only.
- Treat explicit `helpful`, `incorrect`, and `contradicted` as strong signals.
- Keep all ranking/scoring rules transparent and inspectable.
