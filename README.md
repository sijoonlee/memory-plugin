# Memory MCP

Milestone 1 implements a local memory store:

- SQLite metadata store
- LanceDB vector store
- local Hugging Face embedding wrapper
- create/search/get APIs
- JSONL export for inspection

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
```

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

## Tests

Run tests:

```bash
make test
```

Or directly:

```bash
uv run --extra dev pytest
```
