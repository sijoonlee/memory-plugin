from __future__ import annotations

import anyio

from memory_mcp.mcp_server import build_mcp
from memory_mcp.store import LocalMemoryStore

from conftest import FakeEmbedder


def test_mcp_server_exposes_milestone_2_tools(tmp_path) -> None:
    async def run() -> None:
        mcp = build_mcp(LocalMemoryStore(tmp_path / "memory", FakeEmbedder()))
        tools = await mcp.list_tools()
        assert [tool.name for tool in tools] == [
            "memory_search",
            "memory_get",
            "memory_create",
            "memory_feedback",
        ]

    anyio.run(run)
