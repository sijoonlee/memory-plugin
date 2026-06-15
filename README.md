# Memory MCP

Milestone 1 implements a local memory store:

- SQLite metadata store
- LanceDB vector store
- local Hugging Face embedding wrapper
- create/search/get APIs
- JSONL export for inspection

## Package Layout

```text
src/memory_mcp/
  core/        shared models, storage, embeddings, and event log
  mcp_server/  MCP stdio server and tool service layer
  hooks/       hook/event ingestion CLI and examples
  pipeline/    reusable event, session, candidate, and extraction workers
  review/      local human review service and dashboard
```

## Install

Install dependencies and warm the local embedding model:

```bash
make install
```

This runs:

```bash
uv sync --extra dev
uv run memory-mcp install-model
```

The model warmup downloads `sentence-transformers/all-MiniLM-L6-v2` on first
use so later `create` and `search` commands do not pay that startup cost.

## Available CLI

Show all commands:

```bash
uv run memory-mcp --help
uv run memory-mcp-event --help
uv run memory-mcp-review --help
uv run memory-mcp-server
```

Command surfaces:

- `memory-mcp`: normal operator workflow plus memory create/search/get/export
- `memory-mcp-server`: run the MCP stdio server
- `memory-mcp-event`: append hook/event log rows and inspect event backlog
- `memory-mcp-review`: run the local human review UI/API for pending candidates

## Daily Workflow

For normal local use, start with the higher-level workflow commands:

```bash
uv run memory-mcp status
uv run memory-mcp process
uv run memory-mcp review
```

`status` prints one JSON summary of event backlog, session segments, memory
candidates, and memory status counts.

`process` runs the MVP pipeline:

1. process pending retrieval/feedback events
2. refresh session segments from captured events
3. extract pending memory candidates from idle segments
4. apply daily score decay unless disabled

Use Codex as the extraction agent:

```bash
uv run memory-mcp process \
  --extractor codex \
  --event-limit 100 \
  --extraction-limit 1 \
  --model gpt-5 \
  --effort high \
  --idle-after 600 \
  --max-gap 7200
```

Use Claude Code as the extraction agent:

```bash
uv run memory-mcp process \
  --extractor claude \
  --model sonnet \
  --effort medium
```

`--model` is passed to the selected CLI extractor. `--effort` maps to Codex's
reasoning effort config when `--extractor codex` is used, and to Claude Code's
`--effort` flag when `--extractor claude` is used.

Use `--extraction-limit 0` to process events and session segments without
calling the extractor:

```bash
uv run memory-mcp process --extraction-limit 0
```

`review` starts the local candidate review UI:

```bash
uv run memory-mcp review
```

Open:

```text
http://127.0.0.1:8765
```

The lower-level `memory-mcp-event` and `memory-mcp-review` commands remain
available for hooks, debugging, tests, and manual repair.

### `install-model`

Download and warm the local embedding model:

```bash
uv run memory-mcp install-model
```

Use a different embedding model:

```bash
uv run memory-mcp install-model \
  --model-name sentence-transformers/all-mpnet-base-v2
```

### `create`

Create a memory:

```bash
uv run memory-mcp create \
  --situation "When running tests in this repo." \
  --lesson "Direct pytest used the wrong environment." \
  --action "Use uv run pytest so dependencies resolve from the project environment." \
  --tag testing
```

Fields:

- `--situation`: when this memory should be retrieved
- `--lesson`: what was learned
- `--action`: what the agent should do next time
- `--tag`: optional repeated tag filter value
- `--root`: optional memory store root, default `.memory-mcp`

### `search`

Search memories with the current task or question:

```bash
uv run memory-mcp search "how should I run tests?"
```

Search with options:

```bash
uv run memory-mcp search "how should I run Python tests?" \
  --limit 3 \
  --tag testing \
  --min-score 0.2
```

Options:

- `--limit`: maximum memories to return, default `5`
- `--tag`: optional repeated tag filter value
- `--min-score`: minimum stored memory score, default `0.0`
- `--root`: optional memory store root, default `.memory-mcp`

### `get`

Fetch one memory by id:

```bash
uv run memory-mcp get mem_ff16a834f1274d8fb3611cbd5f7dc9b5
```

Options:

- `--root`: optional memory store root, default `.memory-mcp`

### `export`

Export memories as JSONL:

```bash
uv run memory-mcp export memories.jsonl
```

Options:

- `--root`: optional memory store root, default `.memory-mcp`

### `memory-mcp-server`

Run the MCP server over stdio:

```bash
uv run memory-mcp-server
```

The server exposes these MCP tools:

- `memory_search`
- `memory_get`
- `memory_create`
- `memory_feedback`

By default, the server stores data under `.memory-mcp` relative to the current
working directory. Set `MEMORY_MCP_ROOT` to use a specific store path:

```bash
MEMORY_MCP_ROOT=/absolute/path/to/.memory-mcp uv run memory-mcp-server
```

Example MCP client configuration:

```json
{
  "mcpServers": {
    "memory-mcp": {
      "command": "uv",
      "args": ["run", "memory-mcp-server"],
      "env": {
        "MEMORY_MCP_ROOT": "/absolute/path/to/.memory-mcp"
      }
    }
  }
}
```

### `memory-mcp-event`

Append normalized events for `memory-mcp process` to consume:

```bash
uv run memory-mcp-event append \
  --event-type user_prompt \
  --source codex_hook \
  --project /path/to/repo \
  --session-id session-123 \
  --payload '{"prompt":"remember to use uv run pytest"}'
```

Hooks can also pipe JSON to stdin:

```bash
printf '{"prompt":"remember this"}' | uv run memory-mcp-event append \
  --event-type user_prompt \
  --source codex_hook
```

Hook commands should use `--quiet` so Codex does not receive unexpected hook
stdout:

```bash
uv run memory-mcp-event append \
  --quiet \
  --event-type tool_result \
  --source codex_hook \
  --project /path/to/repo \
  --session-id session-123 \
  --run-id run-456
```

Check pending event count:

```bash
uv run memory-mcp-event status
```

Useful event append options:

- `--event-type`: normalized event type, for example `user_prompt`, `tool_result`, `turn_stop`, `memory_feedback`, or `memory_retrieved`
- `--source`: event source adapter, for example `codex_hook`, `mcp_tool`, or `synthetic_test`
- `--payload`: JSON payload object; stdin is used when omitted
- `--project`: project identifier or root path
- `--session-id`: session/thread identifier
- `--run-id`: run/turn identifier
- `--root`: event store root, default `.memory-mcp`
- `--quiet`: suppress stdout for hook execution

Events are stored in:

```text
.memory-mcp/events.sqlite
```

The current repo includes Codex hook examples at:

```text
.codex/hooks.json
src/memory_mcp/hooks/examples/codex-hooks.json
```

It records these Codex lifecycle events:

- `UserPromptSubmit` -> `user_prompt`
- `PostToolUse` -> `tool_result`
- `Stop` -> `turn_stop`

After starting a new Codex session, run `/hooks` to review and trust the
project-local hooks before they execute.

### Pipeline Processing

`memory-mcp process` is the supported way to consume captured events and produce
reviewable memory candidates.

Session statuses are `open`, `idle`, `processed`, `skipped`, and `failed`.
Candidate statuses are `pending_review`, `approved`, `rejected`, and `merged`.
Approving a candidate runs the normal memory creation path, including dedupe.

The extractor can use Codex CLI or Claude Code CLI. Both run non-interactively
with a JSON schema and write only `pending_review` candidates. They do not
create active memories directly. By default, extraction receives session events
as input and avoids running inside the project directory so project hooks are not
triggered recursively. Use `memory-mcp process --project-context` only when
extraction needs repository file access.

### `memory-mcp-review`

Run the local candidate review UI:

```bash
uv run memory-mcp-review serve
```

Useful options:

```bash
uv run memory-mcp-review serve \
  --host 127.0.0.1 \
  --port 8765 \
  --root .memory-mcp
```

Open:

```text
http://127.0.0.1:8765
```

The review UI lets a human filter pending candidates, inspect referenced
evidence events, edit fields, approve into memory, reject with a reason, and
mark failed or skipped source segments as ready for extraction retry. It binds
to `127.0.0.1` by default.

`memory-mcp process` currently handles:

- `memory_feedback`: applies feedback score rules and counter updates
- `memory_retrieved`: applies the weak retrieval score signal
- session segments: derives `open` and `idle` segments from captured events
- extraction: turns idle session segments into pending memory candidates when
  extraction is enabled
- memory candidates: keeps human-reviewed pending candidates separate from active memories
- daily decay: applies `score = score * 0.995` once per elapsed day

Processed events are marked in `events.sqlite`; invalid events are marked
failed with an error for inspection.

Memory feedback can also change memory status:

- `not_helpful`: keep `active`, lower score
- `stale`: mark `stale`
- `contradicted`: mark `superseded` if `replacement_memory_id` is present, otherwise `stale`
- `incorrect`: mark `invalid`

Normal search returns only `active` memories. `memory_get` and `memory-mcp get`
can still fetch non-active memories by id for audit.

Memory creation runs a dedupe check before inserting:

- clear duplicate: merge metadata into the existing active memory and return it
- possible duplicate: store the candidate as `rejected` with dedupe metadata for audit
- distinct memory: create a new active memory

Dedupe uses vector similarity plus field overlap across lesson, situation,
action, and tags. Hooks and event appenders do not dedupe events.

## Tests

Run tests:

```bash
make test
```

Or directly:

```bash
uv run --extra dev pytest
```
