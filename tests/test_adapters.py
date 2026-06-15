from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from memory_mcp.adapters import (
    ClaudeAdapter,
    CodexAdapter,
    GenericAdapter,
    fallback_session_id,
    get_adapter,
)
from memory_mcp.core.events import EventStore
from memory_mcp.hooks.cli import app


def test_codex_payload_normalizes_to_event() -> None:
    adapter = CodexAdapter()
    event = adapter.normalize(
        event_type="user_prompt",
        payload={
            "cwd": "/repo/project-a",
            "session_id": "codex-session-1",
            "turn_id": "turn-7",
            "prompt": "remember to use uv run pytest",
        },
    )

    assert event.source == "codex_hook"
    assert event.event_type == "user_prompt"
    assert event.project == "/repo/project-a"
    assert event.session_id == "codex-session-1"
    assert event.run_id == "turn-7"
    assert event.payload["prompt"] == "remember to use uv run pytest"


def test_claude_payload_normalizes_to_event() -> None:
    adapter = ClaudeAdapter()
    event = adapter.normalize(
        event_type="tool_result",
        payload={
            "cwd": "/repo/project-b",
            "session_id": "claude-session-9",
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
        },
    )

    assert event.source == "claude_hook"
    assert event.event_type == "tool_result"
    assert event.project == "/repo/project-b"
    assert event.session_id == "claude-session-9"
    assert event.run_id is None
    assert event.payload["tool_name"] == "Bash"


def test_missing_session_id_uses_project_scoped_fallback() -> None:
    adapter = ClaudeAdapter()
    event = adapter.normalize(
        event_type="turn_stop",
        payload={"cwd": "/repo/project-c"},
    )

    assert event.session_id == fallback_session_id("claude_hook", "/repo/project-c")
    assert event.session_id == "claude_hook:project-c"


def test_explicit_overrides_take_precedence_over_payload() -> None:
    adapter = CodexAdapter()
    event = adapter.normalize(
        event_type="user_prompt",
        payload={"cwd": "/payload/path", "session_id": "from-payload"},
        project="/override/path",
        session_id="from-flag",
        run_id="run-flag",
    )

    assert event.project == "/override/path"
    assert event.session_id == "from-flag"
    assert event.run_id == "run-flag"


def test_get_adapter_resolves_known_names() -> None:
    assert isinstance(get_adapter("codex"), CodexAdapter)
    assert isinstance(get_adapter("claude"), ClaudeAdapter)
    generic = get_adapter("generic", source="my_client")
    assert isinstance(generic, GenericAdapter)
    assert generic.source == "my_client"


def test_get_adapter_rejects_unknown_and_sourceless_generic() -> None:
    with pytest.raises(ValueError):
        get_adapter("unknown")
    with pytest.raises(ValueError):
        get_adapter("generic")


def test_event_cli_append_with_adapter_extracts_identifiers(tmp_path) -> None:
    root = tmp_path / "memory"
    result = CliRunner().invoke(
        app,
        [
            "append",
            "--adapter",
            "claude",
            "--event-type",
            "user_prompt",
            "--root",
            str(root),
        ],
        input=json.dumps({"cwd": "/repo/proj", "session_id": "s-42", "prompt": "hi"}),
    )

    assert result.exit_code == 0, result.output
    output = json.loads(result.stdout)
    assert output["source"] == "claude_hook"
    assert output["project"] == "/repo/proj"
    assert output["session_id"] == "s-42"

    events = EventStore(root).list_events()
    assert len(events) == 1
    assert events[0].session_id == "s-42"


def test_event_cli_append_requires_adapter_or_source(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "append",
            "--event-type",
            "user_prompt",
            "--root",
            str(tmp_path / "memory"),
        ],
        input="{}",
    )

    assert result.exit_code != 0
    assert "provide --adapter or --source" in result.output
