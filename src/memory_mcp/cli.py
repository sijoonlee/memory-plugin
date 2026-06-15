from __future__ import annotations

from pathlib import Path

import typer

from memory_mcp.core.embeddings import LangChainHuggingFaceEmbedder
from memory_mcp.core.models import MemoryCreate
from memory_mcp.core.store import LocalMemoryStore

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
