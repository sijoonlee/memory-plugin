from __future__ import annotations

import subprocess

from memory_mcp.core.projects import resolve_project


def _git(*args, cwd) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_resolve_project_single_repo_subdir_collapses_to_root(tmp_path) -> None:
    repo = tmp_path / "repo"
    sub = repo / "src" / "pkg"
    sub.mkdir(parents=True)
    _git("init", cwd=repo)
    (repo / "pyproject.toml").write_text("")

    # Any subfolder of a single-project repo resolves to the manifest at the
    # repo root, not the literal cwd.
    assert resolve_project(str(sub)) == str(repo.resolve())
    assert resolve_project(str(repo)) == str(repo.resolve())


def test_resolve_project_monorepo_picks_nearest_package(tmp_path) -> None:
    repo = tmp_path / "hello-web"
    repo.mkdir()
    _git("init", cwd=repo)
    (repo / "package.json").write_text("{}")  # monorepo root manifest

    backend = repo / "backend"
    (backend / "app").mkdir(parents=True)
    (backend / "pyproject.toml").write_text("")

    frontend = repo / "frontend"
    (frontend / "src").mkdir(parents=True)
    (frontend / "package.json").write_text("{}")

    # Each package resolves to itself — distinct keys, not the shared git root.
    assert resolve_project(str(backend / "app")) == str(backend.resolve())
    assert resolve_project(str(frontend / "src")) == str(frontend.resolve())
    # Root-level work keys to the root manifest.
    assert resolve_project(str(repo)) == str(repo.resolve())


def test_resolve_project_monorepo_without_root_manifest(tmp_path) -> None:
    repo = tmp_path / "ws"
    repo.mkdir()
    _git("init", cwd=repo)  # no manifest at the repo root

    svc = repo / "services" / "api"
    svc.mkdir(parents=True)
    (svc / "pyproject.toml").write_text("")

    assert resolve_project(str(svc)) == str(svc.resolve())
    # No manifest up to the git root -> the git root is the project.
    assert resolve_project(str(repo)) == str(repo.resolve())


def test_resolve_project_falls_back_for_non_git_path(tmp_path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert resolve_project(str(plain)) == str(plain)


def test_resolve_project_falls_back_for_missing_path() -> None:
    assert resolve_project("/no/such/dir/anywhere") == "/no/such/dir/anywhere"


def test_resolve_project_passes_through_none() -> None:
    assert resolve_project(None) is None
