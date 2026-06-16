from __future__ import annotations

import anyio

from memory_mcp.mcp_server import build_mcp
from memory_mcp.mcp_server.service import (
    candidate_list,
    memory_create,
    memory_list,
    memory_status,
)
from memory_mcp.core.events import EventStore
from memory_mcp.core.store import LocalMemoryStore

from conftest import FakeEmbedder


def test_mcp_server_exposes_tools(tmp_path) -> None:
    async def run() -> None:
        mcp = build_mcp(
            LocalMemoryStore(tmp_path / "memory", FakeEmbedder()),
            EventStore(tmp_path / "memory"),
        )
        tools = await mcp.list_tools()
        assert [tool.name for tool in tools] == [
            "memory_search",
            "memory_get",
            "memory_create",
            "memory_feedback",
            "memory_status",
            "memory_list",
            "candidate_list",
        ]

    anyio.run(run)


def test_read_only_inspection_tools(tmp_path) -> None:
    store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())
    events = EventStore(tmp_path / "memory")

    created = memory_create(
        store,
        what_happened="ran pytest with uv",
        when_useful="when running this project's tests",
        helpful_explanation="use `uv run pytest`",
    )
    memory_id = created["memory"]["id"]

    status = memory_status(store, events)
    assert status["memories"]["active"] == 1
    assert set(status) == {"root", "events", "sessions", "candidates", "memories"}

    listed = memory_list(store, status="active", limit=10)
    assert listed["total"] == 1
    assert listed["returned"] == 1
    assert listed["memories"][0]["id"] == memory_id
    # summary stays compact: no embedding text leaks into the listing
    assert "content_for_embedding" not in listed["memories"][0]

    # status filter that matches nothing returns an empty, well-formed payload
    assert memory_list(store, status="archived")["memories"] == []

    candidates = candidate_list(events, status="pending_review")
    assert candidates == {
        "status": "pending_review",
        "total": 0,
        "returned": 0,
        "candidates": [],
    }


def test_memory_list_respects_limit(tmp_path) -> None:
    store = LocalMemoryStore(tmp_path / "memory", FakeEmbedder())
    for index in range(3):
        memory_create(
            store,
            what_happened=f"event {index}",
            when_useful="later",
            helpful_explanation="do the thing",
        )

    listed = memory_list(store, status="active", limit=2)
    assert listed["total"] == 3
    assert listed["returned"] == 2
    assert len(listed["memories"]) == 2
