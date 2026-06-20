from __future__ import annotations

from typer.testing import CliRunner

from memory_mcp import cli
from memory_mcp.catalog import render_catalog, select_catalog_memories
from memory_mcp.core.models import MemoryCreate
from memory_mcp.core.store import LocalMemoryStore
from memory_mcp.mcp_server.service import memory_catalog

from conftest import FakeEmbedder


def _store(tmp_path) -> LocalMemoryStore:
    return LocalMemoryStore(tmp_path / "memory", FakeEmbedder())


def _add(store, *, when_useful, details, memory_type, score=0.5, project=None):
    return store.create_memory(
        MemoryCreate(
            when_useful=when_useful,
            details=details,
            memory_type=memory_type,
            score=score,
            project=project,
        )
    )


def test_catalog_groups_by_type_in_taxonomy_order(tmp_path) -> None:
    store = _store(tmp_path)
    _add(store, when_useful="When deciding how to run tests", details="d1",
         memory_type="feedback", score=0.8)
    _add(store, when_useful="When choosing the storage backend", details="d2",
         memory_type="project", score=0.6)

    block = render_catalog(store)

    assert block.startswith("<memory-catalog>")
    assert block.endswith(
        "Call memory_get(<id>) to read the full memory when a line looks relevant."
    )
    # feedback precedes project (taxonomy order), each under its own header.
    assert block.index("[feedback]") < block.index("[project]")
    assert "When deciding how to run tests" in block
    assert "When choosing the storage backend" in block


def test_catalog_orders_by_score_and_respects_limit(tmp_path) -> None:
    store = _store(tmp_path)
    for i in range(7):
        _add(store, when_useful=f"cue number {i}", details=f"body {i}",
             memory_type="project", score=i / 10.0)

    selected = select_catalog_memories(store, limit=5)

    assert len(selected) == 5
    scores = [m.score for m in selected]
    assert scores == sorted(scores, reverse=True)
    # The two lowest-scored memories (0.0, 0.1) are dropped.
    assert all(m.score >= 0.2 for m in selected)


def test_catalog_scopes_to_repo_plus_globals(tmp_path) -> None:
    store = _store(tmp_path)
    repo = _add(store, when_useful="repo cue", details="d", memory_type="project",
                project="/repos/a")
    glob = _add(store, when_useful="global cue", details="d", memory_type="feedback",
                project=None)
    other = _add(store, when_useful="other repo cue", details="d",
                 memory_type="project", project="/repos/b")

    block = render_catalog(store, project="/repos/a")

    assert 'project="/repos/a"' in block
    assert repo.id in block
    assert glob.id in block
    assert other.id not in block


def test_catalog_excludes_untyped(tmp_path) -> None:
    store = _store(tmp_path)
    typed = _add(store, when_useful="typed cue", details="d", memory_type="reference")
    # Untyped is no longer creatable via the public paths, but legacy rows can
    # exist; the catalog must skip them.
    untyped = store.create_memory(
        MemoryCreate(when_useful="untyped cue", details="d", memory_type=None)
    )

    block = render_catalog(store)

    assert typed.id in block
    assert untyped.id not in block


def test_catalog_empty_store_returns_empty_string(tmp_path) -> None:
    assert render_catalog(_store(tmp_path)) == ""


def test_catalog_word_budget_caps_entries(tmp_path) -> None:
    store = _store(tmp_path)
    long_cue = " ".join(f"word{i}" for i in range(40))  # ~40 words per line
    for i in range(5):
        _add(store, when_useful=f"{i} {long_cue}", details="d",
             memory_type="project", score=(5 - i) / 10.0)

    # Budget only fits the first line (~41 words); limit is generous.
    selected = select_catalog_memories(store, limit=5, max_words=45)

    assert len(selected) == 1


def test_catalog_ids_resolve_via_get_memory(tmp_path) -> None:
    store = _store(tmp_path)
    _add(store, when_useful="resolvable cue", details="d", memory_type="user")

    selected = select_catalog_memories(store)
    assert selected
    for memory in selected:
        assert store.get_memory(memory.id) is not None


def test_memory_catalog_tool_returns_scoped_structured_list(tmp_path) -> None:
    store = _store(tmp_path)
    repo = _add(store, when_useful="repo cue", details="d", memory_type="project",
                project="/repos/a", score=0.9)
    glob = _add(store, when_useful="global cue", details="d", memory_type="user",
                project=None, score=0.5)
    _add(store, when_useful="other repo cue", details="d", memory_type="project",
         project="/repos/b")

    result = memory_catalog(store, project="/repos/a")

    assert result["project"] == "/repos/a"
    ids = [m["id"] for m in result["memories"]]
    assert ids == [repo.id, glob.id]  # score desc, other repo excluded
    assert result["memories"][0] == {
        "id": repo.id,
        "when_useful": "repo cue",
        "memory_type": "project",
    }
    assert "memory_get" in result["guidance"]


def test_memory_catalog_tool_defaults_project_from_context(tmp_path) -> None:
    store = _store(tmp_path)
    _add(store, when_useful="a cue", details="d", memory_type="project",
         project="/repos/a")
    _add(store, when_useful="b cue", details="d", memory_type="project",
         project="/repos/b")

    result = memory_catalog(store, event_context={"project": "/repos/a"})

    assert result["project"] == "/repos/a"
    assert [m["when_useful"] for m in result["memories"]] == ["a cue"]


def test_catalog_cli_command_prints_block(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    _add(store, when_useful="cli cue", details="d", memory_type="feedback")
    monkeypatch.setattr(cli, "_store", lambda root: store)

    result = CliRunner().invoke(cli.app, ["catalog", "--root", str(tmp_path)])

    assert result.exit_code == 0
    assert "<memory-catalog>" in result.stdout
    assert "cli cue" in result.stdout


def test_catalog_cli_empty_store_prints_nothing(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    monkeypatch.setattr(cli, "_store", lambda root: store)

    result = CliRunner().invoke(cli.app, ["catalog", "--root", str(tmp_path)])

    assert result.exit_code == 0
    assert result.stdout.strip() == ""
