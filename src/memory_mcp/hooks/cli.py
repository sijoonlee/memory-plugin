from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer

from memory_mcp.adapters import ADAPTER_NAMES, GenericAdapter, get_adapter
from memory_mcp.core.events import EventStore

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
