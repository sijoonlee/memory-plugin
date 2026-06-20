from __future__ import annotations

import subprocess
from pathlib import Path

# A memory's ``project`` is its *project boundary*, not the literal working
# directory. We find that boundary by walking up from cwd to the nearest
# directory holding a project manifest, bounded by the git repo root. This keeps
# the same project together when entered from any subfolder, while keeping
# monorepo packages distinct (each has its own manifest). See plan-2.md.
_PROJECT_MARKERS: tuple[str, ...] = (
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "Gemfile",
)


def resolve_project(path: str | None) -> str | None:
    """Resolve ``path`` to its project boundary.

    Walking up from ``path`` (bounded by the git repo root), return the nearest
    directory containing a project manifest; if none is found before the git
    root, return the git root. When ``path`` is not inside a git repo, doesn't
    exist, or git is unavailable, return it unchanged (cwd fallback). ``None``
    passes through (a global, project-less memory).
    """

    if not path:
        return path

    git_root = _git_root(path)
    if git_root is None:
        # Not a git repo (or path/git unavailable): keep the cwd as-is.
        return path

    start = Path(path).resolve()
    root = Path(git_root).resolve()
    for directory in _walk_up_to_root(start, root):
        if _has_marker(directory):
            return str(directory)
    # No manifest anywhere up to the repo root: the repo root is the project.
    return str(root)


def _walk_up_to_root(start: Path, root: Path) -> list[Path]:
    """Directories from ``start`` up to and including ``root`` (or the
    filesystem root if ``root`` isn't an ancestor, as a safety stop)."""

    chain: list[Path] = []
    directory = start
    while True:
        chain.append(directory)
        if directory == root or directory.parent == directory:
            break
        directory = directory.parent
    return chain


def _has_marker(directory: Path) -> bool:
    return any((directory / marker).exists() for marker in _PROJECT_MARKERS)


def _git_root(path: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", path, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode == 0:
        root = result.stdout.strip()
        return root or None
    return None
