from __future__ import annotations

from pathlib import Path

import typer
import uvicorn

from memory_mcp.review.server import create_app

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """Run the local memory candidate review surface."""


@app.command("serve")
def serve(
    root: Path = typer.Option(Path(".memory-mcp")),
    host: str = typer.Option("127.0.0.1", help="Bind address for the local review UI."),
    port: int = typer.Option(8765, help="Port for the local review UI."),
) -> None:
    uvicorn.run(create_app(root), host=host, port=port)
