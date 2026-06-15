from __future__ import annotations

import json

from typer.testing import CliRunner

from memory_mcp.hooks.cli import app
from memory_mcp.core.events import EventCreate, EventStore


def test_event_store_appends_and_lists_unprocessed(tmp_path) -> None:
    store = EventStore(tmp_path / "memory")
    event = store.append_event(
        EventCreate(
            event_type="user_prompt",
            source="test",
            project="/repo",
            session_id="session-1",
            run_id="run-1",
            payload={"prompt": "remember this"},
        )
    )

    assert event.id.startswith("evt_")
    assert store.count_unprocessed() == 1

    pending = store.list_unprocessed()
    assert [item.id for item in pending] == [event.id]
    assert pending[0].payload == {"prompt": "remember this"}


def test_event_cli_append_reads_json_payload(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "append",
            "--event-type",
            "turn_stop",
            "--source",
            "codex_hook",
            "--project",
            "/repo",
            "--session-id",
            "session-1",
            "--root",
            str(tmp_path / "memory"),
        ],
        input=json.dumps({"ok": True}),
    )

    assert result.exit_code == 0
    output = json.loads(result.stdout)
    assert output["event_type"] == "turn_stop"
    assert output["payload"] == {"ok": True}

    store = EventStore(tmp_path / "memory")
    assert store.count_unprocessed() == 1


def test_event_cli_append_quiet_suppresses_stdout(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "append",
            "--quiet",
            "--event-type",
            "turn_stop",
            "--source",
            "codex_hook",
            "--root",
            str(tmp_path / "memory"),
        ],
        input=json.dumps({"ok": True}),
    )

    assert result.exit_code == 0
    assert result.stdout == ""

    store = EventStore(tmp_path / "memory")
    assert store.count_unprocessed() == 1
