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


def test_event_cli_delete_removes_event(tmp_path) -> None:
    store = EventStore(tmp_path / "memory")
    event = store.append_event(
        EventCreate(event_type="user_prompt", source="test", payload={"prompt": "junk"})
    )

    result = CliRunner().invoke(
        app, ["delete", event.id, "--root", str(tmp_path / "memory")]
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"deleted": True, "event_id": event.id}
    assert store.get_event(event.id) is None
    assert store.list_events() == []


def test_event_cli_delete_unknown_id_exits_nonzero(tmp_path) -> None:
    EventStore(tmp_path / "memory")
    result = CliRunner().invoke(
        app, ["delete", "evt_nope", "--root", str(tmp_path / "memory")]
    )

    assert result.exit_code == 1
    assert json.loads(result.stdout) == {"deleted": False, "event_id": "evt_nope"}


def test_event_cli_append_noops_when_capture_disabled(tmp_path, monkeypatch) -> None:
    # Guards against the self-ingestion loop: while the extractor runs the agent
    # CLI, its capture hooks must not append the extraction prompt back.
    monkeypatch.setenv("MEMORY_MCP_DISABLE_CAPTURE", "1")
    result = CliRunner().invoke(
        app,
        [
            "append",
            "--event-type",
            "user_prompt",
            "--source",
            "claude_hook",
            "--root",
            str(tmp_path / "memory"),
        ],
        input=json.dumps({"prompt": "huge extraction prompt"}),
    )

    assert result.exit_code == 0
    assert result.stdout == ""
    assert EventStore(tmp_path / "memory").count_unprocessed() == 0


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
