from __future__ import annotations

import json
from pathlib import Path

import typer

from memory_mcp.core.embeddings import LangChainHuggingFaceEmbedder
from memory_mcp.core.models import MemoryCreate
from memory_mcp.core.store import LocalMemoryStore
from memory_mcp.pipeline.extractors import ClaudeCliExtractor, CodexCliExtractor
from memory_mcp.operator import OperatorWorkflow
from memory_mcp.review.server import create_app

app = typer.Typer(no_args_is_help=True)


def _store(root: Path) -> LocalMemoryStore:
    return LocalMemoryStore(root=root, embedder=LangChainHuggingFaceEmbedder())


@app.command()
def create(
    situation: str = typer.Option(
        ...,
        help="When this memory should be retrieved.",
    ),
    lesson: str = typer.Option(
        ...,
        help="What was learned.",
    ),
    action: str = typer.Option(
        ...,
        help="What the agent should do next time.",
    ),
    tag: list[str] | None = typer.Option(None),
    root: Path = typer.Option(Path(".memory-mcp")),
) -> None:
    record = _store(root).create_memory(
        MemoryCreate(
            what_happened=lesson,
            when_useful=situation,
            helpful_explanation=action,
            tags=tag or [],
        )
    )
    typer.echo(record.model_dump_json(indent=2))


@app.command()
def get(memory_id: str, root: Path = typer.Option(Path(".memory-mcp"))) -> None:
    record = _store(root).get_memory(memory_id)
    if record is None:
        raise typer.Exit(1)
    typer.echo(record.model_dump_json(indent=2))


@app.command()
def delete(memory_id: str, root: Path = typer.Option(Path(".memory-mcp"))) -> None:
    deleted = _store(root).delete_memory(memory_id)
    typer.echo(json.dumps({"deleted": deleted, "memory_id": memory_id}))
    if not deleted:
        raise typer.Exit(1)


@app.command()
def search(
    query: str,
    limit: int = typer.Option(5),
    tag: list[str] | None = typer.Option(None),
    min_score: float = typer.Option(0.0),
    root: Path = typer.Option(Path(".memory-mcp")),
) -> None:
    results = _store(root).search_memories(
        query,
        limit=limit,
        tags=tag or None,
        min_score=min_score,
    )
    typer.echo(
        "[\n"
        + ",\n".join(result.model_dump_json(indent=2) for result in results)
        + "\n]"
    )


@app.command("export")
def export_jsonl(
    output: Path,
    root: Path = typer.Option(Path(".memory-mcp")),
) -> None:
    count = _store(root).export_jsonl(output)
    typer.echo(f"exported {count} memories to {output}")


@app.command("install-model")
def install_model(
    model_name: str = typer.Option(
        "sentence-transformers/all-MiniLM-L6-v2",
        help="Embedding model to download and warm.",
    ),
) -> None:
    embedder = LangChainHuggingFaceEmbedder(model_name=model_name)
    vector = embedder.embed_text("memory mcp embedding model warmup")
    typer.echo(f"installed {model_name} ({len(vector)} dimensions)")


@app.command("status")
def operator_status(
    root: Path = typer.Option(Path(".memory-mcp")),
) -> None:
    status = OperatorWorkflow(root=root).status()
    typer.echo(json.dumps(status.to_dict(), indent=2))


@app.command("process")
def operator_process(
    root: Path = typer.Option(Path(".memory-mcp")),
    event_limit: int = typer.Option(100, min=1, help="Maximum pending events to process."),
    extraction_limit: int = typer.Option(
        1,
        min=0,
        help="Maximum idle session segments to send to extraction. Use 0 to skip.",
    ),
    idle_after: int = typer.Option(
        600,
        min=0,
        help="Seconds with no events before a segment is considered idle.",
    ),
    max_gap: int = typer.Option(
        7200,
        min=1,
        help="Seconds between events before a new segment starts.",
    ),
    decay: bool = typer.Option(True, "--decay/--no-decay"),
    extractor: str = typer.Option(
        "codex",
        help="LLM CLI extractor to use for candidate extraction: codex or claude.",
    ),
    codex_bin: str = typer.Option("codex", help="Codex CLI executable for extraction."),
    claude_bin: str = typer.Option("claude", help="Claude CLI executable for extraction."),
    model: str | None = typer.Option(None, help="Optional model override for the selected extractor."),
    effort: str | None = typer.Option(
        None,
        help="Optional reasoning effort. Codex uses a config override; Claude uses --effort.",
    ),
    timeout: int = typer.Option(180, min=1, help="LLM CLI timeout in seconds."),
    project_context: bool = typer.Option(
        False,
        help="Allow the selected extractor to access the segment project. Off by default to avoid hooks.",
    ),
) -> None:
    memory_extractor = None
    if extraction_limit > 0:
        if extractor == "codex":
            memory_extractor = CodexCliExtractor(
                codex_bin=codex_bin,
                model=model,
                effort=effort,
                timeout_seconds=timeout,
                use_project_context=project_context,
            )
        elif extractor == "claude":
            memory_extractor = ClaudeCliExtractor(
                claude_bin=claude_bin,
                model=model,
                effort=effort,
                timeout_seconds=timeout,
                use_project_context=project_context,
            )
        else:
            raise typer.BadParameter("extractor must be one of: codex, claude")
    result = OperatorWorkflow(root=root).process(
        extractor=memory_extractor,
        event_limit=event_limit,
        extraction_limit=extraction_limit,
        idle_after_seconds=idle_after,
        max_segment_gap_seconds=max_gap,
        apply_decay=decay,
    )
    typer.echo(json.dumps(result.to_dict(), indent=2))


@app.command("rebuild-sessions")
def operator_rebuild_sessions(
    root: Path = typer.Option(Path(".memory-mcp")),
    idle_after: int = typer.Option(
        600,
        min=0,
        help="Seconds with no events before a segment is considered idle.",
    ),
    max_gap: int = typer.Option(
        7200,
        min=1,
        help="Seconds between events before a new segment starts.",
    ),
) -> None:
    """Clear non-terminal session segments and rebuild them from a full scan."""

    result = OperatorWorkflow(root=root).rebuild_sessions(
        idle_after_seconds=idle_after,
        max_segment_gap_seconds=max_gap,
    )
    typer.echo(json.dumps(result, indent=2))


@app.command("review")
def operator_review(
    root: Path = typer.Option(Path(".memory-mcp")),
    host: str = typer.Option("127.0.0.1", help="Bind address for the local review UI."),
    port: int = typer.Option(8765, help="Port for the local review UI."),
) -> None:
    import uvicorn

    uvicorn.run(create_app(root), host=host, port=port)
