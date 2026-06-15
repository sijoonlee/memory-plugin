from __future__ import annotations

import json
import time
from pathlib import Path

import typer

from memory_mcp.core.embeddings import LangChainHuggingFaceEmbedder
from memory_mcp.core.events import EventStore, MemoryCandidateCreate
from memory_mcp.core.store import LocalMemoryStore
from memory_mcp.daemon.extractors import CodexCliExtractor, ExtractionResult
from memory_mcp.daemon.processor import MemoryDaemon
from memory_mcp.daemon.workers.candidate_worker import CandidateWorker
from memory_mcp.daemon.workers.extraction_worker import ExtractionWorker
from memory_mcp.daemon.workers.session_worker import SessionWorker

app = typer.Typer(no_args_is_help=True)
sessions_app = typer.Typer(no_args_is_help=True)
candidates_app = typer.Typer(no_args_is_help=True)
extract_app = typer.Typer(no_args_is_help=True)
app.add_typer(sessions_app, name="sessions")
app.add_typer(candidates_app, name="candidates")
app.add_typer(extract_app, name="extract")


def _daemon(root: Path) -> MemoryDaemon:
    return MemoryDaemon(
        memory_store=LocalMemoryStore(
            root=root,
            embedder=DaemonEmbedder(),
        ),
        event_store=EventStore(root),
    )


def _candidate_worker(root: Path) -> CandidateWorker:
    return CandidateWorker(
        event_store=EventStore(root),
        memory_store=LocalMemoryStore(
            root=root,
            embedder=LangChainHuggingFaceEmbedder(),
        ),
    )


class DaemonEmbedder:
    def embed_text(self, text: str) -> list[float]:
        raise RuntimeError("daemon event processing does not embed text")

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("daemon event processing does not embed text")


@app.command()
def once(
    root: Path = typer.Option(Path(".memory-mcp")),
    limit: int = typer.Option(100),
    decay: bool = typer.Option(True, help="Apply daily decay after event processing."),
) -> None:
    result = _daemon(root).run_once(limit=limit, apply_decay=decay)
    typer.echo(json.dumps(result.__dict__, indent=2))


@app.command()
def run(
    root: Path = typer.Option(Path(".memory-mcp")),
    interval: float = typer.Option(5.0, help="Polling interval in seconds."),
    limit: int = typer.Option(100),
    decay: bool = typer.Option(True, help="Apply daily decay after each poll."),
) -> None:
    daemon = _daemon(root)
    while True:
        result = daemon.run_once(limit=limit, apply_decay=decay)
        typer.echo(json.dumps(result.__dict__))
        time.sleep(interval)


@app.command()
def status(root: Path = typer.Option(Path(".memory-mcp"))) -> None:
    events = EventStore(root)
    typer.echo(
        json.dumps(
            {
                "events_db": str(events.sqlite_path),
                "unprocessed": events.count_unprocessed(),
                "failed": events.count_failed(),
                "open_sessions": len(events.list_session_segments(status="open")),
                "idle_sessions": len(events.list_session_segments(status="idle")),
                "failed_sessions": len(events.list_session_segments(status="failed")),
                "pending_candidates": len(
                    events.list_memory_candidates(status="pending_review")
                ),
            },
            indent=2,
        )
    )


@sessions_app.command("refresh")
def refresh_sessions(
    root: Path = typer.Option(Path(".memory-mcp")),
    idle_after: int = typer.Option(600, help="Seconds with no events before a segment is idle."),
    max_gap: int = typer.Option(7200, help="Seconds between events before a new segment starts."),
) -> None:
    result = SessionWorker(
        event_store=EventStore(root),
        idle_after_seconds=idle_after,
        max_segment_gap_seconds=max_gap,
    ).run_once()
    typer.echo(json.dumps(result.__dict__, indent=2))


@sessions_app.command("list")
def list_sessions(
    root: Path = typer.Option(Path(".memory-mcp")),
    status: str | None = typer.Option(None),
) -> None:
    segments = EventStore(root).list_session_segments(status=status)
    typer.echo(_json_list([segment.model_dump(mode="json") for segment in segments]))


@sessions_app.command("show")
def show_session(
    segment_id: str,
    root: Path = typer.Option(Path(".memory-mcp")),
    events: bool = typer.Option(False, help="Include raw events for this segment."),
) -> None:
    store = EventStore(root)
    segment = store.get_session_segment(segment_id)
    if segment is None:
        raise typer.Exit(1)
    payload: dict[str, object] = {"segment": segment.model_dump(mode="json")}
    if events:
        payload["events"] = [
            event.model_dump(mode="json")
            for event in store.list_events_for_session_segment(segment)
        ]
    typer.echo(json.dumps(payload, indent=2))


@candidates_app.command("create")
def create_candidate(
    situation: str = typer.Option(..., help="When this memory should be retrieved."),
    lesson: str = typer.Option(..., help="What was learned."),
    action: str = typer.Option(..., help="What the agent should do next time."),
    category: str = typer.Option("manual_review"),
    confidence: float = typer.Option(0.5, min=0.0, max=1.0),
    creation_reason: str = typer.Option("Manual candidate created from daemon CLI."),
    evidence_event_id: list[str] | None = typer.Option(None),
    evidence_summary: str = typer.Option("Manual candidate; no extracted evidence summary."),
    source_session_segment_id: str | None = typer.Option(None),
    root: Path = typer.Option(Path(".memory-mcp")),
) -> None:
    record = EventStore(root).create_memory_candidate(
        MemoryCandidateCreate(
            situation=situation,
            lesson=lesson,
            action=action,
            category=category,
            confidence=confidence,
            creation_reason=creation_reason,
            evidence_event_ids=evidence_event_id or [],
            evidence_summary=evidence_summary,
            source_session_segment_id=source_session_segment_id,
        )
    )
    typer.echo(record.model_dump_json(indent=2))


@candidates_app.command("list")
def list_candidates(
    root: Path = typer.Option(Path(".memory-mcp")),
    status: str | None = typer.Option("pending_review"),
) -> None:
    candidates = EventStore(root).list_memory_candidates(status=status)
    typer.echo(_json_list([candidate.model_dump(mode="json") for candidate in candidates]))


@candidates_app.command("show")
def show_candidate(
    candidate_id: str,
    root: Path = typer.Option(Path(".memory-mcp")),
) -> None:
    candidate = EventStore(root).get_memory_candidate(candidate_id)
    if candidate is None:
        raise typer.Exit(1)
    typer.echo(candidate.model_dump_json(indent=2))


@candidates_app.command("approve")
def approve_candidate(
    candidate_id: str,
    root: Path = typer.Option(Path(".memory-mcp")),
) -> None:
    candidate, memory = _candidate_worker(root).approve_candidate(candidate_id)
    typer.echo(
        json.dumps(
            {
                "candidate": candidate.model_dump(mode="json"),
                "memory": memory.model_dump(mode="json"),
            },
            indent=2,
        )
    )


@candidates_app.command("reject")
def reject_candidate(
    candidate_id: str,
    reason: str = typer.Option(...),
    root: Path = typer.Option(Path(".memory-mcp")),
) -> None:
    candidate = _candidate_worker(root).reject_candidate(candidate_id, reason=reason)
    typer.echo(candidate.model_dump_json(indent=2))


@candidates_app.command("retry")
def retry_candidate(
    candidate_id: str,
    root: Path = typer.Option(Path(".memory-mcp")),
) -> None:
    candidate = _candidate_worker(root).retry_candidate(candidate_id)
    typer.echo(candidate.model_dump_json(indent=2))


@extract_app.command("once")
def extract_once(
    root: Path = typer.Option(Path(".memory-mcp")),
    limit: int = typer.Option(1, min=1),
    segment_id: str | None = typer.Option(None, help="Extract only this idle session segment."),
    codex_bin: str = typer.Option("codex", help="Codex CLI executable."),
    model: str | None = typer.Option(None, help="Optional Codex model override."),
    timeout: int = typer.Option(180, min=1, help="Codex CLI timeout in seconds."),
    project_context: bool = typer.Option(
        False,
        help="Run Codex with --cd set to the segment project. Off by default to avoid hooks.",
    ),
) -> None:
    worker = ExtractionWorker(
        event_store=EventStore(root),
        extractor=CodexCliExtractor(
            codex_bin=codex_bin,
            model=model,
            timeout_seconds=timeout,
            use_project_context=project_context,
        ),
    )
    result = worker.run_once(limit=limit, segment_id=segment_id)
    typer.echo(json.dumps(result.__dict__, indent=2))


@extract_app.command("schema")
def extract_schema() -> None:
    typer.echo(json.dumps(ExtractionResult.model_json_schema(), indent=2))


def _json_list(items: list[dict[str, object]]) -> str:
    return json.dumps(items, indent=2)
