from __future__ import annotations

from datetime import datetime, timezone
import json

from memory_mcp.core.events import EventCreate, EventRecord, EventStore, SessionSegmentRecord
from memory_mcp.pipeline.extractors import (
    ClaudeCliExtractor,
    CodexCliExtractor,
    ExtractedMemoryCandidate,
    ExtractionResult,
    StaticMemoryExtractor,
)
from memory_mcp.pipeline.workers.extraction_worker import ExtractionWorker
from memory_mcp.pipeline.workers.session_worker import SessionWorker


def test_extraction_schema_forbids_additional_properties() -> None:
    schema = ExtractionResult.model_json_schema()

    assert schema["additionalProperties"] is False
    candidate_schema = schema["$defs"]["ExtractedMemoryCandidate"]
    assert candidate_schema["additionalProperties"] is False


def test_codex_cli_extractor_passes_model_and_effort(monkeypatch, tmp_path) -> None:
    captured = {}
    segment = _segment()
    event = _event()

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        output_path = cmd[cmd.index("--output-last-message") + 1]
        result = ExtractionResult(
            candidates=[],
            no_memory_reason="No reusable lesson.",
        )
        tmp_output = tmp_path / "output.json"
        tmp_output.write_text(result.model_dump_json(), encoding="utf-8")
        Path(output_path).write_text(tmp_output.read_text(), encoding="utf-8")

        class Completed:
            returncode = 0
            stderr = ""

        return Completed()

    from pathlib import Path

    monkeypatch.setattr("subprocess.run", fake_run)

    CodexCliExtractor(
        codex_bin="codex-test",
        model="gpt-5",
        effort="high",
    ).extract(segment=segment, events=[event])

    assert captured["cmd"][:2] == ["codex-test", "exec"]
    assert ["--model", "gpt-5"] == captured["cmd"][
        captured["cmd"].index("--model") : captured["cmd"].index("--model") + 2
    ]
    assert [
        "--config",
        'model_reasoning_effort="high"',
    ] == captured["cmd"][
        captured["cmd"].index("--config") : captured["cmd"].index("--config") + 2
    ]
    assert captured["cmd"][-1] == "-"
    assert "session_events_json" in captured["kwargs"]["input"]


def test_claude_cli_extractor_passes_model_effort_and_schema(monkeypatch) -> None:
    captured = {}
    result = ExtractionResult(
        candidates=[],
        no_memory_reason="No reusable lesson.",
    )

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs

        class Completed:
            returncode = 0
            stderr = ""
            stdout = json.dumps({"result": result.model_dump_json()})

        return Completed()

    monkeypatch.setattr("subprocess.run", fake_run)

    extracted = ClaudeCliExtractor(
        claude_bin="claude-test",
        model="sonnet",
        effort="xhigh",
    ).extract(segment=_segment(), events=[_event()])

    assert extracted.no_memory_reason == "No reusable lesson."
    assert captured["cmd"][:4] == [
        "claude-test",
        "--print",
        "--output-format",
        "json",
    ]
    # --bare must NOT be passed: it disables keychain reads and breaks auth
    # on machines whose OAuth token lives only in the macOS Keychain.
    assert "--bare" not in captured["cmd"]
    assert "--json-schema" in captured["cmd"]
    assert ["--model", "sonnet"] == captured["cmd"][
        captured["cmd"].index("--model") : captured["cmd"].index("--model") + 2
    ]
    assert ["--effort", "xhigh"] == captured["cmd"][
        captured["cmd"].index("--effort") : captured["cmd"].index("--effort") + 2
    ]
    assert "session_events_json" in captured["cmd"][-1]


def test_extraction_worker_creates_pending_candidate_from_idle_segment(tmp_path) -> None:
    event_store = EventStore(tmp_path / "memory")
    event = event_store.append_event(
        EventCreate(
            event_type="user_prompt",
            source="test",
            project="/repo",
            session_id="session-1",
            payload={"prompt": "Use uv run pytest in this repo."},
        ),
        created_at=datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc),
    )
    SessionWorker(event_store=event_store, idle_after_seconds=1).run_once(
        now=datetime(2026, 6, 14, 12, 1, tzinfo=timezone.utc)
    )
    worker = ExtractionWorker(
        event_store=event_store,
        extractor=StaticMemoryExtractor(
            ExtractionResult(
                candidates=[
                    ExtractedMemoryCandidate(
                        situation="When running tests in this repo.",
                        lesson="Direct pytest uses the wrong environment.",
                        action="Use uv run pytest.",
                        category="durable_workflow",
                        confidence=0.8,
                        evidence_event_ids=[event.id],
                        evidence_summary="The user gave the durable test command.",
                    )
                ],
                no_memory_reason=None,
            )
        ),
    )

    result = worker.run_once()

    assert result.processed_segments == 1
    assert result.created_candidates == 1
    assert result.remaining_idle_segments == 0
    segment = event_store.list_session_segments()[0]
    assert segment.status == "processed"
    candidate = event_store.list_memory_candidates()[0]
    assert candidate.status == "pending_review"
    assert candidate.source_session_segment_id == segment.id
    assert candidate.evidence_event_ids == [event.id]


def test_extraction_worker_skips_segment_when_no_memory_found(tmp_path) -> None:
    event_store = EventStore(tmp_path / "memory")
    event_store.append_event(
        EventCreate(
            event_type="turn_stop",
            source="test",
            project="/repo",
            session_id="session-1",
            payload={"status": "done"},
        ),
        created_at=datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc),
    )
    SessionWorker(event_store=event_store, idle_after_seconds=1).run_once(
        now=datetime(2026, 6, 14, 12, 1, tzinfo=timezone.utc)
    )
    worker = ExtractionWorker(
        event_store=event_store,
        extractor=StaticMemoryExtractor(
            ExtractionResult(candidates=[], no_memory_reason="No reusable lesson.")
        ),
    )

    result = worker.run_once()

    assert result.skipped_segments == 1
    segment = event_store.list_session_segments()[0]
    assert segment.status == "skipped"
    assert segment.error == "No reusable lesson."
    assert event_store.list_memory_candidates() == []

    # The LLM's no_memory_reason is surfaced in the worker result, not just the DB.
    assert len(result.skipped) == 1
    assert result.skipped[0].segment_id == segment.id
    assert result.skipped[0].session_id == "session-1"
    assert result.skipped[0].reason == "No reusable lesson."


def test_extraction_worker_can_target_one_idle_segment(tmp_path) -> None:
    event_store = EventStore(tmp_path / "memory")
    first_event = event_store.append_event(
        EventCreate(
            event_type="user_prompt",
            source="test",
            project="/repo",
            session_id="session-1",
            payload={"prompt": "No memory here."},
        ),
        created_at=datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc),
    )
    target_event = event_store.append_event(
        EventCreate(
            event_type="user_prompt",
            source="test",
            project="/repo",
            session_id="session-2",
            payload={"prompt": "Use uv run pytest."},
        ),
        created_at=datetime(2026, 6, 14, 12, 1, tzinfo=timezone.utc),
    )
    SessionWorker(event_store=event_store, idle_after_seconds=1).run_once(
        now=datetime(2026, 6, 14, 12, 2, tzinfo=timezone.utc)
    )
    target_segment = [
        segment
        for segment in event_store.list_session_segments(status="idle")
        if segment.session_id == "session-2"
    ][0]
    worker = ExtractionWorker(
        event_store=event_store,
        extractor=StaticMemoryExtractor(
            ExtractionResult(
                candidates=[
                    ExtractedMemoryCandidate(
                        situation="When running tests in this repo.",
                        lesson="Direct pytest uses the wrong environment.",
                        action="Use uv run pytest.",
                        category="durable_workflow",
                        confidence=0.8,
                        evidence_event_ids=[target_event.id],
                        evidence_summary="The user gave the durable test command.",
                    )
                ],
                no_memory_reason=None,
            )
        ),
    )

    result = worker.run_once(segment_id=target_segment.id)

    assert result.processed_segments == 1
    assert event_store.get_session_segment(target_segment.id).status == "processed"  # type: ignore[union-attr]
    assert event_store.list_memory_candidates()[0].evidence_event_ids == [target_event.id]
    untouched_segments = [
        segment
        for segment in event_store.list_session_segments()
        if segment.session_id == "session-1"
    ]
    assert untouched_segments[0].status == "idle"
    assert first_event.id != target_event.id


def test_extraction_worker_fails_segment_on_unknown_evidence_event(tmp_path) -> None:
    event_store = EventStore(tmp_path / "memory")
    event_store.append_event(
        EventCreate(
            event_type="user_prompt",
            source="test",
            project="/repo",
            session_id="session-1",
            payload={"prompt": "Use uv run pytest."},
        ),
        created_at=datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc),
    )
    SessionWorker(event_store=event_store, idle_after_seconds=1).run_once(
        now=datetime(2026, 6, 14, 12, 1, tzinfo=timezone.utc)
    )
    worker = ExtractionWorker(
        event_store=event_store,
        extractor=StaticMemoryExtractor(
            ExtractionResult(
                candidates=[
                    ExtractedMemoryCandidate(
                        situation="When running tests in this repo.",
                        lesson="Direct pytest uses the wrong environment.",
                        action="Use uv run pytest.",
                        category="durable_workflow",
                        confidence=0.8,
                        evidence_event_ids=["evt_missing"],
                        evidence_summary="Bad evidence id.",
                    )
                ],
                no_memory_reason=None,
            )
        ),
    )

    result = worker.run_once()

    assert result.failed_segments == 1
    segment = event_store.list_session_segments()[0]
    assert segment.status == "failed"
    assert "evt_missing" in (segment.error or "")
    assert event_store.list_memory_candidates() == []


def _segment() -> SessionSegmentRecord:
    created_at = datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc)
    return SessionSegmentRecord(
        id="seg_test",
        project="/repo",
        session_id="session-1",
        segment_index=0,
        first_event_at=created_at,
        last_event_at=created_at,
        event_count=1,
        status="idle",
    )


def _event() -> EventRecord:
    created_at = datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc)
    return EventRecord(
        id="evt_test",
        event_type="user_prompt",
        source="test",
        project="/repo",
        session_id="session-1",
        payload={"prompt": "Use uv run pytest."},
        created_at=created_at,
    )
