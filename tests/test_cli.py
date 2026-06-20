import json
from datetime import datetime, timezone

from typer.testing import CliRunner

from memory_mcp import cli
from memory_mcp.core.events import EventCreate, EventStore, SessionSegmentRecord


class FakeStore:
    def __init__(self, root):
        self.root = root

    def create_memory(self, memory):
        return memory.model_copy(
            update={
                "id": "mem_test",
                "content_for_embedding": "test",
            }
        )


def test_create_uses_when_useful_details_flags(monkeypatch, tmp_path) -> None:
    captured = {}

    def fake_store(root):
        captured["root"] = root
        store = FakeStore(root)
        original_create = store.create_memory

        def create_memory(memory):
            captured["memory"] = memory
            return original_create(memory)

        store.create_memory = create_memory
        return store

    monkeypatch.setattr(cli, "_store", fake_store)

    result = CliRunner().invoke(
        cli.app,
        [
            "create",
            "--when-useful",
            "When running tests in this repo.",
            "--details",
            "Direct pytest used the wrong environment. Use uv run pytest.",
            "--tag",
            "testing",
            "--root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0
    assert captured["memory"].when_useful == "When running tests in this repo."
    assert captured["memory"].details == (
        "Direct pytest used the wrong environment. Use uv run pytest."
    )
    assert captured["memory"].tags == ["testing"]


def test_process_can_use_claude_extractor_options(monkeypatch, tmp_path) -> None:
    captured = {}

    class FakeExtractor:
        def __init__(self, **kwargs):
            captured["extractor_kwargs"] = kwargs

    class FakeResult:
        def to_dict(self):
            return {"ok": True}

    class FakeWorkflow:
        def __init__(self, *, root):
            captured["root"] = root

        def process(self, **kwargs):
            captured["process_kwargs"] = kwargs
            return FakeResult()

    monkeypatch.setattr(cli, "ClaudeCliExtractor", FakeExtractor)
    monkeypatch.setattr(cli, "OperatorWorkflow", FakeWorkflow)

    result = CliRunner().invoke(
        cli.app,
        [
            "process",
            "--root",
            str(tmp_path),
            "--extractor",
            "claude",
            "--claude-bin",
            "claude-test",
            "--model",
            "sonnet",
            "--effort",
            "high",
            "--timeout",
            "30",
            "--extraction-limit",
            "2",
        ],
    )

    assert result.exit_code == 0
    assert '"ok": true' in result.output
    assert captured["root"] == tmp_path
    assert captured["extractor_kwargs"] == {
        "claude_bin": "claude-test",
        "model": "sonnet",
        "effort": "high",
        "timeout_seconds": 30,
        "use_project_context": False,
    }
    assert captured["process_kwargs"]["extractor"] is not None
    assert captured["process_kwargs"]["extraction_limit"] == 2


def _seed_segments(root) -> EventStore:
    store = EventStore(root)
    store.upsert_session_segment(
        SessionSegmentRecord(
            id="seg_skipped",
            project="/repo",
            session_id="s1",
            segment_index=0,
            first_event_at=datetime(2026, 6, 14, 0, 0, tzinfo=timezone.utc),
            last_event_at=datetime(2026, 6, 14, 1, 0, tzinfo=timezone.utc),
            event_count=1,
            status="skipped",
            error="No durable memory candidate found.",
        )
    )
    store.upsert_session_segment(
        SessionSegmentRecord(
            id="seg_failed",
            project="/repo",
            session_id="s2",
            segment_index=0,
            first_event_at=datetime(2026, 6, 15, 0, 0, tzinfo=timezone.utc),
            last_event_at=datetime(2026, 6, 15, 1, 0, tzinfo=timezone.utc),
            event_count=1,
            status="failed",
            error="extractor crashed",
        )
    )
    store.append_event(
        EventCreate(
            event_type="tool_result",
            source="test",
            project="/repo",
            session_id="s1",
            payload={"message": "ran pytest"},
        ),
        created_at=datetime(2026, 6, 14, 0, 30, tzinfo=timezone.utc),
    )
    return store


def test_segments_lists_status_and_reason(tmp_path) -> None:
    _seed_segments(tmp_path)

    result = CliRunner().invoke(
        cli.app,
        ["segments", "--status", "skipped", "--root", str(tmp_path)],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "skipped"
    assert payload["total"] == 1
    assert payload["returned"] == 1
    segment = payload["segments"][0]
    assert segment["id"] == "seg_skipped"
    assert segment["status"] == "skipped"
    assert segment["error"] == "No durable memory candidate found."


def test_segments_without_status_returns_all(tmp_path) -> None:
    _seed_segments(tmp_path)

    result = CliRunner().invoke(cli.app, ["segments", "--root", str(tmp_path)])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] is None
    assert payload["total"] == 2
    ids = {segment["id"] for segment in payload["segments"]}
    assert ids == {"seg_skipped", "seg_failed"}


def test_segment_events_prints_event_log(tmp_path) -> None:
    _seed_segments(tmp_path)

    result = CliRunner().invoke(
        cli.app,
        ["segment-events", "seg_skipped", "--root", str(tmp_path)],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["segment"]["id"] == "seg_skipped"
    assert len(payload["events"]) == 1
    assert payload["events"][0]["payload"]["message"] == "ran pytest"


def test_segment_events_missing_segment_exits_nonzero(tmp_path) -> None:
    EventStore(tmp_path)

    result = CliRunner().invoke(
        cli.app,
        ["segment-events", "seg_nope", "--root", str(tmp_path)],
    )

    assert result.exit_code == 1
    assert "session segment not found" in result.output
