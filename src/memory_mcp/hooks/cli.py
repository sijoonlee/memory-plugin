from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import typer

from memory_mcp.adapters import ADAPTER_NAMES, GenericAdapter, get_adapter
from memory_mcp.catalog import (
    CATALOG_DEFAULT_LIMIT,
    CATALOG_DEFAULT_MAX_WORDS,
    render_catalog,
)
from memory_mcp.core.embeddings import NoopEmbedder
from memory_mcp.core.events import EventStore
from memory_mcp.core.projects import resolve_project
from memory_mcp.core.store import LocalMemoryStore

app = typer.Typer(no_args_is_help=True)


@app.command()
def append(
    event_type: str = typer.Option(..., help="Normalized event type."),
    source: str | None = typer.Option(
        None,
        help="Event source adapter name. Required unless --adapter is codex/claude.",
    ),
    adapter: str | None = typer.Option(
        None,
        help=f"Agent adapter to normalize the payload: one of {ADAPTER_NAMES}.",
    ),
    payload: str | None = typer.Option(None, help="JSON payload object."),
    project: str | None = typer.Option(None, help="Project identifier or root path."),
    session_id: str | None = typer.Option(None, help="Session/thread identifier."),
    run_id: str | None = typer.Option(None, help="Run/turn identifier."),
    root: Path = typer.Option(Path(".memory-mcp"), help="Memory MCP store root."),
    quiet: bool = typer.Option(False, help="Suppress stdout for hook execution."),
) -> None:
    # Break the self-ingestion loop: when the LLM extractor runs the agent CLI,
    # that agent's own UserPromptSubmit/PostToolUse hooks fire and would append
    # the (huge) extraction prompt back as events. The extractor sets this flag in
    # the subprocess env so the capture hooks no-op.
    if os.environ.get("MEMORY_MCP_DISABLE_CAPTURE") == "1":
        return

    payload_dict = _read_payload(payload)
    if adapter is not None:
        try:
            event_adapter = get_adapter(adapter, source=source)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
    elif source is not None:
        event_adapter = GenericAdapter(source)
    else:
        raise typer.BadParameter("provide --adapter or --source")

    event = EventStore(root).append_event(
        event_adapter.normalize(
            event_type=event_type,
            payload=payload_dict,
            project=project,
            session_id=session_id,
            run_id=run_id,
        )
    )
    if not quiet:
        typer.echo(event.model_dump_json(indent=2))


@app.command()
def catalog(
    adapter: str | None = typer.Option(
        None,
        help=f"Agent adapter to read the project from the payload: one of {ADAPTER_NAMES}.",
    ),
    project: str | None = typer.Option(
        None, help="Override the project; otherwise derived from the payload cwd."
    ),
    limit: int = typer.Option(CATALOG_DEFAULT_LIMIT, help="Max memories (top by score)."),
    max_words: int = typer.Option(
        CATALOG_DEFAULT_MAX_WORDS, "--max-words", help="Soft word budget."
    ),
    root: Path = typer.Option(Path(".memory-mcp"), help="Memory MCP store root."),
) -> None:
    """Print the scoped memory catalog for a SessionStart hook to inject.

    Reads the hook payload on stdin to learn the session's ``cwd``, normalizes it
    to the project boundary, and writes the catalog block to stdout (stdout *is*
    the injected context, so this is never ``--quiet``). Prints nothing for an
    empty store. Reads only SQLite — no embedding model is loaded, so it stays
    fast enough for session startup.
    """

    # Mirror ``append``: no-op inside the extractor's own agent run so its
    # SessionStart doesn't get (and re-ingest) the catalog.
    if os.environ.get("MEMORY_MCP_DISABLE_CAPTURE") == "1":
        return

    payload = _read_payload(None)
    raw_project = project
    if raw_project is None and adapter is not None:
        try:
            raw_project = get_adapter(adapter).extract_project(payload)
        except ValueError:
            raw_project = None
    if raw_project is None:
        cwd = payload.get("cwd")
        raw_project = cwd if isinstance(cwd, str) and cwd.strip() else os.getcwd()

    store = LocalMemoryStore(root, NoopEmbedder())
    block = render_catalog(
        store,
        project=resolve_project(raw_project),
        limit=limit,
        max_words=max_words,
    )
    if block:
        typer.echo(block)


@app.command()
def delete(
    event_id: str = typer.Argument(..., help="Event id to remove."),
    root: Path = typer.Option(Path(".memory-mcp"), help="Memory MCP store root."),
) -> None:
    """Permanently delete one raw event by id.

    For pruning junk/oversized captures. Session segments are derived, so run
    ``memory-mcp rebuild-sessions`` afterwards to refresh segment counts. Exits
    non-zero for an unknown id.
    """

    deleted = EventStore(root).delete_event(event_id)
    typer.echo(json.dumps({"deleted": deleted, "event_id": event_id}))
    if not deleted:
        raise typer.Exit(1)


@app.command()
def status(root: Path = typer.Option(Path(".memory-mcp"))) -> None:
    store = EventStore(root)
    typer.echo(
        json.dumps(
            {
                "events_db": str(store.sqlite_path),
                "unprocessed": store.count_unprocessed(),
            },
            indent=2,
        )
    )


def _read_payload(payload: str | None) -> dict[str, Any]:
    if payload is not None:
        return _parse_payload_text(payload)
    if sys.stdin.isatty():
        return {}
    text = sys.stdin.read()
    if not text.strip():
        return {}
    return _parse_payload_text(text)


def _parse_payload_text(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}
    if isinstance(value, dict):
        return value
    return {"value": value}
